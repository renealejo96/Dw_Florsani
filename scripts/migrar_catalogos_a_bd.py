"""
Script para crear y sincronizar la tabla cat_variedades_maestro en PostgreSQL
y sincronizar dim_prod_maestros, dim_variedades, dim_productos y variedades.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text
from app.modules.cosechas.utils import cargar_relaciones_maestro, cargar_productos_maestros, cargar_variedades

app = create_app()
with app.app_context():
    print("[1] Creando tabla cat_variedades_maestro si no existe...")
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS cat_variedades_maestro (
            id SERIAL PRIMARY KEY,
            variedad VARCHAR(150) NOT NULL UNIQUE,
            producto_maestro VARCHAR(150) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """))
    db.session.commit()

    print("[2] Sincronizando relaciones de catálogos hacia cat_variedades_maestro...")
    relaciones = cargar_relaciones_maestro()
    count = 0
    for var, prod in relaciones:
        if var and prod:
            db.session.execute(text("""
                INSERT INTO cat_variedades_maestro (variedad, producto_maestro)
                VALUES (:var, :prod)
                ON CONFLICT (variedad) DO UPDATE 
                SET producto_maestro = EXCLUDED.producto_maestro, updated_at = CURRENT_TIMESTAMP;
            """), {'var': var.strip(), 'prod': prod.strip()})
            count += 1
    db.session.commit()
    print(f"    -> {count} relaciones maestro sincronizadas en BD.")

    print("[3] Sincronizando productos maestros a dim_prod_maestros...")
    prods = cargar_productos_maestros()
    p_count = 0
    for p in prods:
        p_clean = p.strip().upper()
        if not p_clean:
            continue
        exists = db.session.execute(text("SELECT 1 FROM dim_prod_maestros WHERE UPPER(producto_maestro) = :p"), {'p': p_clean}).scalar()
        if not exists:
            max_id = db.session.execute(text("SELECT COALESCE(MAX(id_productos), 0) + 1 FROM dim_prod_maestros")).scalar()
            db.session.execute(text("INSERT INTO dim_prod_maestros (id_productos, producto_maestro) VALUES (:id, :p)"), {'id': max_id, 'p': p_clean})
            p_count += 1
    db.session.commit()
    print(f"    -> {p_count} nuevos productos maestros agregados a dim_prod_maestros.")

    print("[4] Sincronizando variedades a dim_variedades y dim_productos...")
    vars_list = cargar_variedades()
    v_count = 0
    for v in vars_list:
        v_clean = v.strip()
        if not v_clean:
            continue
        exists = db.session.execute(text("SELECT 1 FROM dim_variedades WHERE UPPER(variedad) = :v"), {'v': v_clean.upper()}).scalar()
        if not exists:
            max_id = db.session.execute(text("SELECT COALESCE(MAX(id_producto), 0) + 1 FROM dim_variedades")).scalar()
            db.session.execute(text("INSERT INTO dim_variedades (id_producto, variedad) VALUES (:id, :v)"), {'id': max_id, 'v': v_clean})
            
            # dim_productos
            max_p_id = db.session.execute(text("SELECT COALESCE(MAX(id_producto), 0) + 1 FROM dim_productos")).scalar()
            db.session.execute(text("INSERT INTO dim_productos (id_producto, variedad) VALUES (:id, :v)"), {'id': max_p_id, 'v': v_clean})
            
            # variedades
            db.session.execute(text("INSERT INTO variedades (nombre, activo, created_at) VALUES (:v, true, NOW())"), {'v': v_clean.upper()})
            v_count += 1
    db.session.commit()
    print(f"    -> {v_count} nuevas variedades agregadas a dim_variedades/dim_productos.")
    print("Sincronizacion completa y exitosa.")
