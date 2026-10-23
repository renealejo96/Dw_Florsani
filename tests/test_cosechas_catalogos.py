import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.extensions import db
from app.shared.models import User
from app.modules.cosechas.utils import construir_productos_con_variedades, cargar_relaciones_maestro

def test_cosechas_catalogos_suite():
    app = create_app()
    client = app.test_client()
    
    with app.app_context():
        u = User.query.filter_by(role='ADMIN').first()
        if not u:
            u = User.query.first()
        
        with client.session_transaction() as sess:
            sess['user_id'] = u.id
            sess['role'] = u.role

        print("\n--- TEST 1: Verificar códigos de Hypericum asignados y eliminados de Prod. Nuevos ---")
        prods = construir_productos_con_variedades()
        hyp = prods.get('HYPERICUM', [])
        pn = prods.get('PROD.NUEVOS', [])
        for c in ['H21-117-1', 'H21-68-7', 'H21-67-3']:
            print(f"Código {c}: en HYPERICUM={c in hyp}, en PROD.NUEVOS={c in pn}")
            assert c in hyp, f"{c} DEBE estar en HYPERICUM"
            assert c not in pn, f"{c} NO debe estar en PROD.NUEVOS"
        print(">> TEST 1 APROBADO: Códigos correctamente asignados a HYPERICUM.")

        print("\n--- TEST 2: Validación de no permitir duplicados entre cultivos distintos ---")
        resp = client.post('/cosechas/catalogos', data={
            'accion': 'crear_variedad',
            'variedad': 'H21-117-1',
            'producto_maestro': 'GYPSOPHILA'
        }, follow_redirects=True)
        assert resp.status_code == 200
        content = resp.data.decode('utf-8', errors='ignore')
        assert 'No se puede agregar la variedad' in content or 'ya está asignada al cultivo' in content
        assert 'HYPERICUM' in content
        # Asegurar que no se duplicó
        prods_after = construir_productos_con_variedades()
        assert 'H21-117-1' not in prods_after.get('GYPSOPHILA', [])
        print(">> TEST 2 APROBADO: Intento de duplicación en otro cultivo rechazado con alerta clara.")

        print("\n--- TEST 3: Validación de advertencia si la variedad ya existe en el mismo cultivo ---")
        resp_same = client.post('/cosechas/catalogos', data={
            'accion': 'crear_variedad',
            'variedad': 'H21-117-1',
            'producto_maestro': 'HYPERICUM'
        }, follow_redirects=True)
        content_same = resp_same.data.decode('utf-8', errors='ignore')
        assert 'ya se encuentra asignada al cultivo' in content_same or 'ya se encuentra registrada' in content_same
        print(">> TEST 3 APROBADO: Advertencia generada al registrar duplicado en mismo cultivo.")

        print("\n--- TEST 4: Crear variedad y eliminarla usando la acción del tacho (eliminar_relacion) ---")
        test_code = 'TEST-VAR-COSECHA-999'
        resp_add = client.post('/cosechas/catalogos', data={
            'accion': 'crear_variedad',
            'variedad': test_code,
            'producto_maestro': 'HYPERICUM'
        }, follow_redirects=True)
        assert resp_add.status_code == 200
        prods_add = construir_productos_con_variedades()
        assert test_code in prods_add.get('HYPERICUM', [])
        print(f"Variedad de prueba '{test_code}' creada exitosamente en HYPERICUM.")

        # Ahora ejecutar eliminación (tacho)
        resp_del = client.post('/cosechas/catalogos', data={
            'accion': 'eliminar_relacion',
            'variedad': test_code,
            'producto_maestro': 'HYPERICUM'
        }, follow_redirects=True)
        assert resp_del.status_code == 200
        content_del = resp_del.data.decode('utf-8', errors='ignore')
        assert test_code in content_del and 'eliminada de' in content_del and 'HYPERICUM' in content_del
        
        # Verificar que se eliminó y que no resucita
        prods_del = construir_productos_con_variedades()
        assert test_code not in prods_del.get('HYPERICUM', [])
        print(f">> TEST 4 APROBADO: Variedad '{test_code}' eliminada correctamente sin efecto zombi.")

        print("\n--- TEST 5: Reasignación de cultivo mediante edición ---")
        test_reasign = 'TEST-REASIGN-VAR-123'
        client.post('/cosechas/catalogos', data={
            'accion': 'crear_variedad',
            'variedad': test_reasign,
            'producto_maestro': 'PROD.NUEVOS'
        }, follow_redirects=True)
        assert test_reasign in construir_productos_con_variedades().get('PROD.NUEVOS', [])

        # Reasignar a HYPERICUM
        resp_edit = client.post('/cosechas/catalogos', data={
            'accion': 'editar_variedad',
            'variedad_original': test_reasign,
            'producto_original': 'PROD.NUEVOS',
            'variedad_nueva': test_reasign,
            'producto_maestro': 'HYPERICUM',
            'actualizar_historico': '1'
        }, follow_redirects=True)
        prods_edit = construir_productos_con_variedades()
        assert test_reasign not in prods_edit.get('PROD.NUEVOS', [])
        assert test_reasign in prods_edit.get('HYPERICUM', [])
        print(f"Variedad '{test_reasign}' reasignada exitosamente de PROD.NUEVOS a HYPERICUM.")

        # Limpiar variedad de prueba
        client.post('/cosechas/catalogos', data={
            'accion': 'eliminar_relacion',
            'variedad': test_reasign,
            'producto_maestro': 'HYPERICUM'
        }, follow_redirects=True)
        assert test_reasign not in construir_productos_con_variedades().get('HYPERICUM', [])
        print(">> TEST 5 APROBADO: Reasignación y limpieza exitosas.")

        print("\n--- TEST 6: Caso Borde - Case insensitive & Whitespace Duplication ---")
        # Intento de agregar en minúsculas y espacios: '  h21-117-1  ' en GYPSOPHILA
        resp_edge1 = client.post('/cosechas/catalogos', data={
            'accion': 'crear_variedad',
            'variedad': '  h21-117-1  ',
            'producto_maestro': 'GYPSOPHILA'
        }, follow_redirects=True)
        content_edge1 = resp_edge1.data.decode('utf-8', errors='ignore')
        assert 'No se puede agregar la variedad' in content_edge1 and 'HYPERICUM' in content_edge1
        print(">> TEST 6 APROBADO: Bloqueo de duplicado con minúsculas y espacios periféricos.")

        print("\n--- TEST 7: Caso Borde - Producto Maestro Duplicado ---")
        resp_pm_dup = client.post('/cosechas/catalogos', data={
            'accion': 'crear_producto',
            'producto_maestro': '  hypericum  '
        }, follow_redirects=True)
        content_pm_dup = resp_pm_dup.data.decode('utf-8', errors='ignore')
        assert 'ya existe en el catálogo' in content_pm_dup
        print(">> TEST 7 APROBADO: Producto maestro duplicado rechazado.")

        print("\n--- TEST 8: Caso Borde - Eliminar Producto con Variedades Asociadas ---")
        resp_del_prod = client.post('/cosechas/catalogos', data={
            'accion': 'eliminar_producto',
            'producto_maestro': 'HYPERICUM'
        }, follow_redirects=True)
        content_del_prod = resp_del_prod.data.decode('utf-8', errors='ignore')
        assert 'No se puede eliminar' in content_del_prod and 'variedades asociadas' in content_del_prod
        print(">> TEST 8 APROBADO: Protección contra eliminación de producto con variedades activas.")

        print("\n==========================================")
        print("¡TODOS LOS TESTS DE CATÁLOGOS PASARON CON ÉXITO!")
        print("==========================================\n")

if __name__ == '__main__':
    test_cosechas_catalogos_suite()
