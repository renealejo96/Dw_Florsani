"""
PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
ARCHIVO: scripts/migrar_ajustes_cultivo.py
DESCRIPCIÓN: Ajuste de particiones y conteo de plantas para Bloque 75 y camas fraccionadas.
             Garantiza que la Cama 1 de Bloque 75 (75A-A-C1) tenga 120 plantas (0.0889 std)
             y el segmento B (75B-B-C1) tenga 776 plantas (0.5748 std).
"""
import sys
from pathlib import Path

# Agregar directorio raíz al PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text

app = create_app()

with app.app_context():
    print("[MIGRACION-CULTIVO] Iniciando ajuste de particiones para Bloque 75...")
    try:
        # 1. Ajustar dim_camas para Bloque 75 (segmento A fraccionado según censo oficial)
        db.session.execute(text("""
            UPDATE dim_camas 
            SET cama_estandar = 0.0889, num_plantas = 120, metros_netos = 2.40 
            WHERE id_cama = '75A-A-C1';

            UPDATE dim_camas 
            SET cama_estandar = 0.6430, num_plantas = 868, metros_netos = 21.22 
            WHERE id_cama = '75A-A-C2';

            UPDATE dim_camas 
            SET cama_estandar = 0.6844, num_plantas = 924, metros_netos = 22.59 
            WHERE id_cama = '75A-A-C3';

            UPDATE dim_camas 
            SET cama_estandar = 0.0356, num_plantas = 48, metros_netos = 1.17 
            WHERE id_cama = '75A-A-C4';

            UPDATE dim_camas 
            SET cama_estandar = 0.0593, num_plantas = 80, metros_netos = 1.96 
            WHERE id_cama = '75A-A-C5';
        """))

        # 2. Ajustar fact_eventos_cultivo para eventos activos de siembra
        db.session.execute(text("""
            UPDATE fact_eventos_cultivo
            SET num_plantas = 120
            WHERE id_cama = '75A-A-C1' AND tipo_evento = 'SIEMBRA' AND estado_evento = 'ACTIVO';

            UPDATE fact_eventos_cultivo
            SET num_plantas = 868
            WHERE id_cama = '75A-A-C2' AND tipo_evento = 'SIEMBRA' AND estado_evento = 'ACTIVO';

            UPDATE fact_eventos_cultivo
            SET num_plantas = 924
            WHERE id_cama = '75A-A-C3' AND tipo_evento = 'SIEMBRA' AND estado_evento = 'ACTIVO';

            UPDATE fact_eventos_cultivo
            SET num_plantas = 48
            WHERE id_cama = '75A-A-C4' AND tipo_evento = 'SIEMBRA' AND estado_evento = 'ACTIVO';

            UPDATE fact_eventos_cultivo
            SET num_plantas = 80
            WHERE id_cama = '75A-A-C5' AND tipo_evento = 'SIEMBRA' AND estado_evento = 'ACTIVO';
        """))

        db.session.commit()
        print("[MIGRACION-CULTIVO] [OK] Bloque 75 Cama 1 ajustado a 120 plantas (0.0889 std) correctamente.")
    except Exception as e:
        db.session.rollback()
        print(f"[MIGRACION-CULTIVO] Error ajustando Bloque 75: {e}")
