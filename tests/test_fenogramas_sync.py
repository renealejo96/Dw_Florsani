import os
import sys
import unittest
import json

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.extensions import db
from app.shared.models import User
from app.modules.fenogramas.models import Calificacion


class TestFenogramasSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True

    def setUp(self):
        self.client = self.app.test_client()

    def test_01_api_ping_unauthenticated(self):
        """El endpoint /fenogramas/api/ping debe responder 200 sin redirigir, informando authenticated=False."""
        res = self.client.get('/fenogramas/api/ping')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIsNotNone(data)
        self.assertEqual(data.get('status'), 'ok')
        self.assertFalse(data.get('authenticated'))
        self.assertIsNone(data.get('username'))

    def test_02_api_calificar_lote_unauthenticated_returns_401_json(self):
        """Peticiones API a /fenogramas/api/calificar-lote sin sesión deben retornar 401 JSON, NUNCA 302 HTML."""
        payload = {'items': [{'id': 17582, 'cumplido': 1, 'observacion': 'Offline test'}]}
        
        # Test con header application/json
        res = self.client.post('/fenogramas/api/calificar-lote',
                               json=payload,
                               headers={'Accept': 'application/json'})
        self.assertEqual(res.status_code, 401)
        self.assertTrue(res.is_json)
        data = res.get_json()
        self.assertFalse(data.get('success'))
        self.assertEqual(data.get('code'), 'SESSION_EXPIRED')
        self.assertTrue(data.get('auth_required'))

    def test_03_quick_login_endpoint(self):
        """/auth/api/quick-login permite reautenticarse en vivo y establecer sesión permanente."""
        # 1. Credenciales inválidas
        bad_res = self.client.post('/auth/api/quick-login', json={'username': 'admin', 'password': 'wrong_password'})
        self.assertEqual(bad_res.status_code, 401)
        bad_data = bad_res.get_json()
        self.assertFalse(bad_data.get('success'))

        # 2. Credenciales válidas de admin
        good_res = self.client.post('/auth/api/quick-login', json={'username': 'admin', 'password': 'admin123'})
        self.assertEqual(good_res.status_code, 200)
        good_data = good_res.get_json()
        self.assertTrue(good_data.get('success'))
        self.assertEqual(good_data.get('user', {}).get('username'), 'admin')

        # 3. Verificar que ping ahora reporta autenticado
        ping_res = self.client.get('/fenogramas/api/ping')
        self.assertEqual(ping_res.status_code, 200)
        ping_data = ping_res.get_json()
        self.assertTrue(ping_data.get('authenticated'))
        self.assertEqual(ping_data.get('username'), 'admin')

    def test_04_calificar_lote_authenticated_batch(self):
        """Guardar lote de calificaciones en Palmeras 3 debe ejecutarse exitosamente en una sola transacción."""
        with self.app.app_context():
            admin_user = User.query.filter_by(role='ADMIN').first()
            user_id = admin_user.id
            username = admin_user.username
            role = admin_user.role
            perms = admin_user.permissions
            finca_id = admin_user.finca_id

            # Obtener 5 registros de Palmeras 3 para la prueba
            sample_califs = Calificacion.query.filter(Calificacion.zona == 'PALMERAS3').limit(5).all()
            self.assertTrue(len(sample_califs) > 0, "Debe haber registros en PALMERAS3")
            sample_ids = [c.id for c in sample_califs]

        with self.client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = username
            sess['role'] = role
            sess['permissions'] = perms
            sess['finca_id'] = finca_id
            sess['finca_nombre'] = 'Pyganflor'

        # Preparar payload mixto: cumplido=1, cumplido=0, cumplido=None (desmarcar)
        payload_items = [
            {'id': sample_ids[0], 'cumplido': 1, 'observacion': 'Test automatizado cumplido'},
            {'id': str(sample_ids[1]), 'cumplido': 0, 'observacion': 'Test automatizado no cumplido'}, # ID como string
            {'id': sample_ids[2], 'cumplido': '1', 'observacion': 'Test cumplido string'},
            {'id': sample_ids[3], 'cumplido': None, 'observacion': 'Test desmarcado'},
        ]
        if len(sample_ids) > 4:
            payload_items.append({'id': sample_ids[4], 'cumplido': 'null', 'observacion': ''})

        res = self.client.post('/fenogramas/api/calificar-lote', json={'items': payload_items})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('updated'), len(payload_items))

        # Verificar en base de datos que se guardaron correctamente
        with self.app.app_context():
            c0 = db.session.get(Calificacion, sample_ids[0])
            self.assertEqual(c0.cumplido, 1)
            self.assertEqual(c0.observacion, 'Test automatizado cumplido')
            self.assertTrue(c0.tiene_evaluacion)

            c1 = db.session.get(Calificacion, sample_ids[1])
            self.assertEqual(c1.cumplido, 0)
            self.assertEqual(c1.observacion, 'Test automatizado no cumplido')

            # Revertir cambios de prueba para dejar la DB limpia
            for s_id in sample_ids:
                c = db.session.get(Calificacion, s_id)
                if c:
                    c.cumplido = None
                    c.observacion = ''
            db.session.commit()

    def test_05_calificar_lote_edge_cases_resilience(self):
        """Robustez ante items inválidos, IDs no numéricos o inexistentes sin romper la transacción."""
        with self.app.app_context():
            admin_user = User.query.filter_by(role='ADMIN').first()
            user_id = admin_user.id
            username = admin_user.username
            role = admin_user.role
            perms = admin_user.permissions
            finca_id = admin_user.finca_id
            real_calif = Calificacion.query.filter(Calificacion.zona == 'PALMERAS3').first()
            real_id = real_calif.id

        with self.client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = username
            sess['role'] = role
            sess['permissions'] = perms
            sess['finca_id'] = finca_id
            sess['finca_nombre'] = 'Pyganflor'

        # Payload con casos borde: lista vacía
        res_empty = self.client.post('/fenogramas/api/calificar-lote', json={'items': []})
        self.assertEqual(res_empty.status_code, 200)
        self.assertEqual(res_empty.get_json().get('updated'), 0)

        # Payload con IDs inexistentes y no numéricos intercalados con un ID válido
        res_edge = self.client.post('/fenogramas/api/calificar-lote', json={'items': [
            {'id': 'non_numeric_id_abc', 'cumplido': 1, 'observacion': 'Invalid ID'},
            {'id': 99999999, 'cumplido': 1, 'observacion': 'Inexistent ID'},
            {'id': None, 'cumplido': 1, 'observacion': 'None ID'},
            {'id': real_id, 'cumplido': 1, 'observacion': 'Valid edge test'}
        ]})
        self.assertEqual(res_edge.status_code, 200)
        edge_data = res_edge.get_json()
        self.assertTrue(edge_data.get('success'))
        # Solo el ID válido real debe haber sido actualizado
        self.assertEqual(edge_data.get('updated'), 1)

        # Limpiar registro
        with self.app.app_context():
            c = db.session.get(Calificacion, real_id)
            c.cumplido = None
            c.observacion = ''
            db.session.commit()


if __name__ == '__main__':
    unittest.main()
