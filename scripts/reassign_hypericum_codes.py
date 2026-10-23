import sys
from app import create_app
from app.extensions import db
from sqlalchemy import text
from app.modules.cosechas.utils import (
    cargar_relaciones_maestro, guardar_relaciones_maestro,
    cargar_variedades, guardar_variedades
)

def run():
    app = create_app()
    with app.app_context():
        print("Iniciando reasignación de códigos a HYPERICUM...")
        
        # 1. Actualizar fact_eventos_cultivo
        r1 = db.session.execute(text("UPDATE fact_eventos_cultivo SET producto_maestro = 'HYPERICUM', variedad = 'H21-117-1' WHERE UPPER(TRIM(variedad)) = 'H21-117-1'"))
        r2 = db.session.execute(text("UPDATE fact_eventos_cultivo SET producto_maestro = 'HYPERICUM', variedad = 'H21-68-7' WHERE UPPER(TRIM(variedad)) = 'H21-68-7'"))
        r3 = db.session.execute(text("UPDATE fact_eventos_cultivo SET producto_maestro = 'HYPERICUM', variedad = 'H21-67-3' WHERE UPPER(TRIM(variedad)) IN ('H21-67-3', 'H-21-67-3')"))
        print(f"fact_eventos_cultivo actualizados: {r1.rowcount}, {r2.rowcount}, {r3.rowcount}")

        # 2. Actualizar cat_variedades_maestro
        target_codes = ['H21-117-1', 'H21-68-7', 'H21-67-3']
        for code in target_codes:
            db.session.execute(text("DELETE FROM cat_variedades_maestro WHERE UPPER(TRIM(variedad)) = :c"), {'c': code})
            db.session.execute(text("INSERT INTO cat_variedades_maestro (variedad, producto_maestro, updated_at) VALUES (:v, 'HYPERICUM', NOW())"), {'v': code})
        db.session.execute(text("DELETE FROM cat_variedades_maestro WHERE UPPER(TRIM(variedad)) = 'H-21-67-3'"))
        db.session.commit()
        print("cat_variedades_maestro actualizado exitosamente.")

        # 3. Actualizar dim_variedades y dim_productos
        for code in target_codes:
            ex_v = db.session.execute(text("SELECT 1 FROM dim_variedades WHERE UPPER(TRIM(variedad)) = :v"), {'v': code}).scalar()
            if not ex_v:
                max_vid = db.session.execute(text("SELECT COALESCE(MAX(id_producto), 0) + 1 FROM dim_variedades")).scalar()
                db.session.execute(text("INSERT INTO dim_variedades (id_producto, variedad) VALUES (:id, :v)"), {'id': max_vid, 'v': code})
            
            ex_dp = db.session.execute(text("SELECT 1 FROM dim_productos WHERE UPPER(TRIM(variedad)) = :v"), {'v': code}).scalar()
            if not ex_dp:
                max_pid = db.session.execute(text("SELECT COALESCE(MAX(id_producto), 0) + 1 FROM dim_productos")).scalar()
                db.session.execute(text("INSERT INTO dim_productos (id_producto, variedad) VALUES (:id, :v)"), {'id': max_pid, 'v': code})
        db.session.commit()

        # 4. Actualizar MAESTRO_PROS.csv
        relaciones = cargar_relaciones_maestro()
        # Filtrar códigos de cualquier producto previo
        relaciones_limpias = [
            (v, p) for v, p in relaciones
            if v.upper() not in [c.upper() for c in target_codes] and v.upper() != 'H-21-67-3'
        ]
        # Agregar bajo HYPERICUM
        for code in target_codes:
            relaciones_limpias.append((code, 'HYPERICUM'))
        
        guardar_relaciones_maestro(relaciones_limpias)
        print("MAESTRO_PROS.csv y relaciones sincronizadas exitosamente.")

        # 5. Asegurar variedades.csv
        vars_set = set(cargar_variedades())
        for code in target_codes:
            vars_set.add(code)
        guardar_variedades(sorted(list(vars_set)))
        print("variedades.csv actualizado exitosamente.")

if __name__ == '__main__':
    run()
