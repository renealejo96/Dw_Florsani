"""
Script de migración y actualización:
1. Actualiza nombres de fincas oficiales en dim_fincas (Pyganflor, Malchingui, Urcuquí).
2. Agrega columnas finca_id y fincas_permitidas_json a la tabla users.
3. Mapea al usuario admin a todas las fincas (finca_id = 0, [1, 2, 3]).
4. Crea usuario evaluador_feno para el rol EVALUADOR_FENOGRAMA si no existe.
"""
import sys
import os

# Asegurar path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.shared.models import User
from sqlalchemy import text

def ejecutar_migracion():
    app = create_app()
    with app.app_context():
        print("[MIGRACION] Iniciando migracion de Fincas y Roles de Usuario...")
        
        # 1. Actualizar dim_fincas
        db.session.execute(text("""
            UPDATE dim_fincas SET codigo_finca = 'PYGAN', nombre_finca = 'Pyganflor' WHERE id_finca = 1;
            UPDATE dim_fincas SET codigo_finca = 'MALCH', nombre_finca = 'Malchingui' WHERE id_finca = 2;
            UPDATE dim_fincas SET codigo_finca = 'URCUQ', nombre_finca = 'Urcuquí' WHERE id_finca = 3;
        """))
        print("[OK] Fincas actualizadas en dim_fincas: 1: Pyganflor, 2: Malchingui, 3: Urcuqui.")

        # 2. Agregar columnas a users si no existen
        db.session.execute(text("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS finca_id INTEGER DEFAULT 1 NOT NULL;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS fincas_permitidas_json TEXT DEFAULT '[1]';
        """))
        print("[OK] Columnas 'finca_id' y 'fincas_permitidas_json' aseguradas en tabla 'users'.")

        # 3. Asignar Admin a Multi-Finca
        db.session.execute(text("""
            UPDATE users SET finca_id = 0, fincas_permitidas_json = '[1, 2, 3]' 
            WHERE username = 'admin' OR role = 'ADMIN';
        """))
        print("[OK] Usuario Admin configurado como Multi-Finca (Pyganflor, Malchingui, Urcuqui).")

        # 4. Asegurar usuario de prueba para Evaluador de Fenogramas
        evaluador = User.query.filter_by(username='evaluador_feno').first()
        if not evaluador:
            evaluador = User(
                username='evaluador_feno',
                full_name='Evaluador de Fenogramas y Calidad',
                role='EVALUADOR_FENOGRAMA',
                finca_id=1,
                fincas_permitidas_json='[1]',
                permissions=['fenogramas']
            )
            evaluador.set_password('feno123')
            db.session.add(evaluador)
            print("[OK] Usuario '@evaluador_feno' (pass: feno123) creado con rol EVALUADOR_FENOGRAMA.")
        else:
            evaluador.role = 'EVALUADOR_FENOGRAMA'
            evaluador.finca_id = 1
            evaluador.permissions = ['fenogramas']
            print("[OK] Usuario '@evaluador_feno' actualizado con rol EVALUADOR_FENOGRAMA.")

        db.session.commit()
        print("[EXITO] Migracion completada con exito.")

if __name__ == '__main__':
    ejecutar_migracion()
