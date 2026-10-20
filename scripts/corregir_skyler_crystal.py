"""
Script para homologar y corregir SKYLER DUO CRISTAL -> SKYLER DUO CRYSTAL
en todas las tablas de PostgreSQL y archivos CSV de catálogos.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text
from app.modules.cosechas.utils import DATA_DIR

def corregir_skyler():
    app = create_app()
    with app.app_context():
        print("[1] Actualizando registros históricos de cosecha...")
        r_cosechas = db.session.execute(text("""
            UPDATE registro_cosecha 
            SET variedad = 'SKYLER DUO CRYSTAL' 
            WHERE variedad ILIKE '%SKYLER DUO CRISTAL%'
        """)).rowcount
        print(f"    -> {r_cosechas} registros de cosecha actualizados a 'SKYLER DUO CRYSTAL'.")

        print("[2] Actualizando estimados si existieren...")
        r_est = db.session.execute(text("""
            UPDATE estimado 
            SET variedad = 'SKYLER DUO CRYSTAL' 
            WHERE variedad ILIKE '%SKYLER DUO CRISTAL%'
        """)).rowcount
        print(f"    -> {r_est} estimados actualizados.")

        print("[3] Actualizando fact_eventos_cultivo y crop_state_records si existieren...")
        db.session.execute(text("""
            UPDATE fact_eventos_cultivo 
            SET variedad = 'SKYLER DUO CRYSTAL' 
            WHERE variedad ILIKE '%SKYLER DUO CRISTAL%'
        """))
        db.session.execute(text("""
            UPDATE crop_state_records 
            SET variety = 'SKYLER DUO CRYSTAL' 
            WHERE variety ILIKE '%SKYLER DUO CRISTAL%'
        """))

        print("[4] Limpiando catálogos en BD (cat_variedades_maestro, dim_variedades, variedades)...")
        db.session.execute(text("DELETE FROM cat_variedades_maestro WHERE variedad ILIKE '%SKYLER DUO CRISTAL%'"))
        db.session.execute(text("DELETE FROM dim_variedades WHERE variedad ILIKE '%SKYLER DUO CRISTAL%'"))
        db.session.execute(text("DELETE FROM dim_productos WHERE variedad ILIKE '%SKYLER DUO CRISTAL%'"))
        db.session.execute(text("DELETE FROM variedades WHERE nombre ILIKE '%SKYLER DUO CRISTAL%'"))

        # Asegurar que SKYLER DUO CRYSTAL esté en cat_variedades_maestro con VERONICA
        db.session.execute(text("""
            INSERT INTO cat_variedades_maestro (variedad, producto_maestro)
            VALUES ('SKYLER DUO CRYSTAL', 'VERONICA')
            ON CONFLICT (variedad) DO UPDATE SET producto_maestro = 'VERONICA', updated_at = CURRENT_TIMESTAMP
        """))

        db.session.commit()
        print("    -> BD homologada exitosamente.")

    # [5] Limpiar archivos CSV
    print("[5] Limpiando archivos CSV en data/cosechas/...")
    maestro_csv = DATA_DIR / 'MAESTRO_PROS.csv'
    if maestro_csv.exists():
        lines = maestro_csv.read_text(encoding='utf-8').splitlines()
        new_lines = [l for l in lines if 'SKYLER DUO CRISTAL' not in l.upper()]
        maestro_csv.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
        print("    -> MAESTRO_PROS.csv actualizado.")

    vars_csv = DATA_DIR / 'variedades.csv'
    if vars_csv.exists():
        lines = vars_csv.read_text(encoding='utf-8').splitlines()
        new_lines = [l for l in lines if 'SKYLER DUO CRISTAL' not in l.upper()]
        vars_csv.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
        print("    -> variedades.csv actualizado.")

    print("Correccion de SKYLER DUO CRYSTAL completada al 100%.")

if __name__ == '__main__':
    corregir_skyler()
