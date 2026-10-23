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

        # Verificar crop_state_records (camas de cultivo)
        from sqlalchemy import text
        pn_beds = db.session.execute(text("""
            SELECT count(*) FROM crop_state_records 
            WHERE UPPER(TRIM(variety)) IN ('H21-117-1', 'H21-68-7', 'H21-67-3')
              AND UPPER(TRIM(crop_master)) = 'PROD.NUEVOS'
        """)).scalar()
        assert pn_beds == 0, f"No debe haber camas en PROD.NUEVOS para estos códigos. Encontradas: {pn_beds}"
        hyp_beds = db.session.execute(text("""
            SELECT count(*) FROM crop_state_records 
            WHERE UPPER(TRIM(variety)) IN ('H21-117-1', 'H21-68-7', 'H21-67-3')
              AND UPPER(TRIM(crop_master)) = 'HYPERICUM'
        """)).scalar()
        assert hyp_beds > 0, "Debe haber camas asignadas a HYPERICUM en crop_state_records"
        print(f"Beds en crop_state_records: HYPERICUM={hyp_beds}, PROD.NUEVOS={pn_beds}")
        print(">> TEST 1 APROBADO: Códigos correctamente asignados a HYPERICUM en catálogos y crop_state_records.")

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
        from app.modules.cosechas.utils import cargar_variedades
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
        
        # Verificar que se eliminó de construir_productos_con_variedades, cargar_variedades y dim_variedades
        prods_del = construir_productos_con_variedades()
        assert test_code not in prods_del.get('HYPERICUM', []), "No debe estar en productos con variedades"
        assert test_code not in cargar_variedades(), "No debe estar en cargar_variedades()"
        
        in_dim = db.session.execute(text("SELECT count(*) FROM dim_variedades WHERE UPPER(TRIM(variedad)) = :v"), {'v': test_code}).scalar()
        assert in_dim == 0, f"No debe quedar en dim_variedades. Encontrado: {in_dim}"
        print(f">> TEST 4 APROBADO: Variedad '{test_code}' eliminada completamente sin efecto zombi en CSV ni BD.")

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
        assert test_reasign not in cargar_variedades()
        print(">> TEST 5 APROBADO: Reasignación y limpieza exitosas.")

        print("\n--- TEST 6: Caso Borde - Case insensitive & Whitespace Duplication ---")
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

        print("\n--- TEST 9: Caso Borde - Detección de duplicados con guiones/espacios (ej. H-21-67-3 vs H21-67-3) ---")
        resp_hyphen = client.post('/cosechas/catalogos', data={
            'accion': 'crear_variedad',
            'variedad': 'H-21-67-3',
            'producto_maestro': 'PROD.NUEVOS'
        }, follow_redirects=True)
        content_hyphen = resp_hyphen.data.decode('utf-8', errors='ignore')
        assert 'No se puede agregar la variedad' in content_hyphen and 'HYPERICUM' in content_hyphen
        print(">> TEST 9 APROBADO: Detección inteligente de duplicado normalizado con guiones (H-21-67-3).")

        print("\n--- TEST 10: Validación de serialización de productos maestros para UI ---")
        resp_cat_get = client.get('/cosechas/catalogos')
        assert resp_cat_get.status_code == 200
        get_html = resp_cat_get.data.decode('utf-8', errors='ignore')
        assert 'alertaDuplicadoProducto' in get_html
        assert 'btnGuardarProducto' in get_html
        assert 'listaProductos' in get_html
        print(">> TEST 10 APROBADO: Elementos de validación en vivo presentes en el HTML de catálogos.")

        print("\n--- TEST 11: Eliminación definitiva en PROD.NUEVOS (sin resurrección zombie ni en recarga ni en VPS) ---")
        zombie_var = 'TEST-PROD-NUEVO-ZOMBIE-1'
        # 1. Crear en PROD.NUEVOS
        client.post('/cosechas/catalogos', data={
            'accion': 'crear_variedad',
            'variedad': zombie_var,
            'producto_maestro': 'PROD.NUEVOS'
        }, follow_redirects=True)
        assert zombie_var in construir_productos_con_variedades().get('PROD.NUEVOS', []), "Debe estar creada en PROD.NUEVOS"

        # Simular inserción en crop_state_records y fact_eventos_cultivo
        from app.shared.utils import get_local_now
        cama_row = db.session.execute(text("SELECT id_cama, codigo_bloque, num_cama FROM dim_camas LIMIT 1")).fetchone()
        cama_id = cama_row.id_cama if cama_row else 'B_TEST-1'
        blk_code = cama_row.codigo_bloque if cama_row else 'B_TEST'
        bed_num = cama_row.num_cama if cama_row else 1

        db.session.execute(text("""
            INSERT INTO crop_state_records (block_full, bed_num, suffix, variety, crop_master, week, standard_bed)
            VALUES (:b, :num, 'A', :v, 'PROD.NUEVOS', '2026-43', 1.0)
        """), {'b': blk_code, 'num': bed_num, 'v': zombie_var})
        db.session.execute(text("""
            INSERT INTO fact_eventos_cultivo (id_cama, codigo_bloque, variedad, producto_maestro, fecha_evento, tipo_evento, estado_evento)
            VALUES (:cama, :b, :v, 'PROD.NUEVOS', :now, 'SIEMBRA', 'ACTIVO')
        """), {'cama': cama_id, 'b': blk_code, 'v': zombie_var, 'now': get_local_now()})
        db.session.commit()

        # 2. Eliminar enviando casing diferente ('prod nuevos')
        resp_del_zombie = client.post('/cosechas/catalogos', data={
            'accion': 'eliminar_relacion',
            'variedad': zombie_var,
            'producto_maestro': 'prod nuevos'
        }, follow_redirects=True)
        assert resp_del_zombie.status_code == 200

        # 3. Comprobar que desapareció de la BD y CSV
        prods_after_del = construir_productos_con_variedades()
        assert zombie_var not in prods_after_del.get('PROD.NUEVOS', []), "No debe estar en PROD.NUEVOS"
        assert zombie_var not in cargar_relaciones_maestro(), "No debe estar en cargar_relaciones_maestro"
        assert zombie_var not in cargar_variedades(), "No debe estar en cargar_variedades"

        # Comprobar eliminación en cascada en BD
        cnt_cat = db.session.execute(text("SELECT count(*) FROM cat_variedades_maestro WHERE UPPER(TRIM(variedad)) = :v"), {'v': zombie_var}).scalar()
        cnt_dim = db.session.execute(text("SELECT count(*) FROM dim_variedades WHERE UPPER(TRIM(variedad)) = :v"), {'v': zombie_var}).scalar()
        cnt_csr = db.session.execute(text("SELECT count(*) FROM crop_state_records WHERE UPPER(TRIM(variety)) = :v"), {'v': zombie_var}).scalar()
        cnt_evt = db.session.execute(text("SELECT count(*) FROM fact_eventos_cultivo WHERE UPPER(TRIM(variedad)) = :v"), {'v': zombie_var}).scalar()
        assert cnt_cat == 0, f"No debe quedar en cat_variedades_maestro. Encontrados: {cnt_cat}"
        assert cnt_dim == 0, f"No debe quedar en dim_variedades. Encontrados: {cnt_dim}"
        assert cnt_csr == 0, f"No debe quedar en crop_state_records. Encontrados: {cnt_csr}"
        assert cnt_evt == 0, f"No debe quedar en fact_eventos_cultivo. Encontrados: {cnt_evt}"

        # 4. Simular recarga fresca (evitar efecto zombie)
        from app.modules.cosechas.utils import cargar_maestro_productos
        maestro_fresh = cargar_maestro_productos()
        assert zombie_var not in maestro_fresh, "No debe resucitar al recargar el maestro de productos"
        print(">> TEST 11 APROBADO: Variedad en PROD.NUEVOS eliminada permanentemente y sin resurrección zombie.")

        print("\n--- TEST 12: Dashboard Gerencial - 5 Bento KPIs dinámicos, Chip sutil y eliminación de banner AI ---")
        resp_dash = client.get('/')
        assert resp_dash.status_code == 200, f"Dashboard debe responder 200, obtenido {resp_dash.status_code}"
        dash_html = resp_dash.data.decode('utf-8', errors='ignore')

        # Verificar que el banner estridente NO existe
        assert 'id="bannerFiltroPowerBI"' not in dash_html, "bannerFiltroPowerBI NO debe estar en el HTML"
        assert 'Segmentación Power BI:' not in dash_html, "Texto de banner Power BI no debe estar"

        # Verificar presencia de chip y botón sutil en la cabecera del gráfico
        assert 'id="chipFiltroCrop"' in dash_html, "chipFiltroCrop DEBE estar en el HTML"
        assert 'id="chipNombreCrop"' in dash_html, "chipNombreCrop DEBE estar en el HTML"
        assert 'id="btnRestablecerFiltroCrop"' in dash_html, "btnRestablecerFiltroCrop DEBE estar en el HTML"

        # Verificar IDs de las 5 tarjetas Bento KPI
        assert 'id="kpiTopCosechaVal"' in dash_html, "kpiTopCosechaVal debe existir"
        assert 'id="kpiTopCosechaLabel"' in dash_html, "kpiTopCosechaLabel debe existir"
        assert 'id="kpiTopFenoVal"' in dash_html, "kpiTopFenoVal debe existir"
        assert 'id="kpiTopFenoLabel"' in dash_html, "kpiTopFenoLabel debe existir"
        assert 'id="kpiTopAreaVal"' in dash_html, "kpiTopAreaVal debe existir"
        assert 'id="kpiTopAreaLabel"' in dash_html, "kpiTopAreaLabel debe existir"
        assert 'id="kpiTopCamasVal"' in dash_html, "kpiTopCamasVal debe existir"
        assert 'id="kpiTopCamasLabel"' in dash_html, "kpiTopCamasLabel debe existir"
        assert 'id="kpiTopCostosVal"' in dash_html, "kpiTopCostosVal debe existir"
        assert 'id="kpiTopCostosLabel"' in dash_html, "kpiTopCostosLabel debe existir"

        # Verificar serialización de datos de segmentación por producto
        assert 'kpiCosechaByCrop' in dash_html, "kpiCosechaByCrop debe ser inyectado en script"
        assert 'kpiFenoByCrop' in dash_html, "kpiFenoByCrop debe ser inyectado en script"
        assert 'kpiAreaCamasByCrop' in dash_html, "kpiAreaCamasByCrop debe ser inyectado en script"
        assert 'kpiCostosByCrop' in dash_html, "kpiCostosByCrop debe ser inyectado en script"
        assert 'kpiGlobal' in dash_html, "kpiGlobal debe ser inyectado en script"
        assert 'aplicarSegmentacionPowerBI' in dash_html, "aplicarSegmentacionPowerBI debe existir en script"
        assert 'restablecerSegmentacionPowerBI' in dash_html, "restablecerSegmentacionPowerBI debe existir en script"
        print(">> TEST 12 APROBADO: Dashboard Gerencial cuenta con todos los KPIs, chip sutil y datos dinámicos.")

        print("\n==========================================")
        print("¡TODOS LOS 12 TESTS DE CATÁLOGOS Y DASHBOARD PASARON CON ÉXITO!")
        print("==========================================\n")

if __name__ == '__main__':
    test_cosechas_catalogos_suite()
