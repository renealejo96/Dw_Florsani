"""
PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
ARCHIVO: scripts/migrar_siembras_zona3_y_blindaje.py
DESCRIPCIÓN:
1. Registra las siembras reales de Zona 3 en fact_eventos_cultivo y dim_camas:
   - Bloque 36: Sembrado en Semana 44 (2644) -> Edad 1 en Semana 45 (SOLIDAGO)
   - Bloque 38C: Sembrado en Semana 43 (2643) -> Edad 2 en Semana 45 (HYPERICUM)
   - Bloque 39E: Sembrado en Semana 42 (2642) -> Edad 3 en Semana 45 (VERONICA)
2. Actualiza dim_camas para que area_producto_comercial no quede bloqueado en 'VACIO'.
3. Regenera y calibra las actividades de Semana 45 en calificaciones respetando el 100%
   de las calificaciones ya realizadas (cumplido IS NOT NULL).
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text
from app.modules.fenogramas.services.fenograma_service import sync_from_mrt_estado_cultivo

app = create_app()

with app.app_context():
    print("=" * 80)
    print("[MIGRACION] REGISTRANDO SIEMBRAS REALES ZONA 3 (BLOQUES 36, 38C, 39E)")
    print("=" * 80)

    # 1. Obtener todas las camas físicas de 36, 38C, 39E en dim_camas
    camas_36 = db.session.execute(text("SELECT id_cama, codigo_bloque FROM dim_camas WHERE blq = '36' OR codigo_bloque LIKE '36%';")).fetchall()
    camas_38c = db.session.execute(text("SELECT id_cama, codigo_bloque FROM dim_camas WHERE blq = '38C' OR codigo_bloque LIKE '38C%';")).fetchall()
    camas_39e = db.session.execute(text("SELECT id_cama, codigo_bloque FROM dim_camas WHERE blq = '39E' OR codigo_bloque LIKE '39E%';")).fetchall()

    print(f"  • Camas encontradas: Bloque 36 ({len(camas_36)} camas), Bloque 38C ({len(camas_38c)} camas), Bloque 39E ({len(camas_39e)} camas)")

    # 2. Registrar eventos de siembra en fact_eventos_cultivo
    # Bloque 36: Sembrado en Semana 44 (Lunes 2026-10-26)
    f_36 = date.fromisocalendar(2026, 44, 1)
    for c_id, cb in camas_36:
        db.session.execute(text("""
            INSERT INTO fact_eventos_cultivo (
                id_cama, codigo_bloque, fecha_evento, semana_campo, tipo_evento,
                num_poda, producto_maestro, variedad, origen_evento, estado_evento, created_at
            ) VALUES (
                :c_id, :cb, :f_ev, 2644, 'SIEMBRA',
                0, 'SOLIDAGO', 'GOLDEN GLORY YELLOW', 'Corrección Siembra Semana 44', 'ACTIVO', NOW()
            ) ON CONFLICT DO NOTHING;
        """), {'c_id': c_id, 'cb': cb, 'f_ev': f_36})

    # Bloque 38C: Sembrado en Semana 43 (Lunes 2026-10-19)
    f_38c = date.fromisocalendar(2026, 43, 1)
    for c_id, cb in camas_38c:
        db.session.execute(text("""
            INSERT INTO fact_eventos_cultivo (
                id_cama, codigo_bloque, fecha_evento, semana_campo, tipo_evento,
                num_poda, producto_maestro, variedad, origen_evento, estado_evento, created_at
            ) VALUES (
                :c_id, :cb, :f_ev, 2643, 'SIEMBRA',
                0, 'HYPERICUM', 'MAGICAL TRIUMPH', 'Corrección Siembra Semana 43', 'ACTIVO', NOW()
            ) ON CONFLICT DO NOTHING;
        """), {'c_id': c_id, 'cb': cb, 'f_ev': f_38c})

    # Bloque 39E: Sembrado en Semana 42 (Lunes 2026-10-12)
    f_39e = date.fromisocalendar(2026, 42, 1)
    for c_id, cb in camas_39e:
        db.session.execute(text("""
            INSERT INTO fact_eventos_cultivo (
                id_cama, codigo_bloque, fecha_evento, semana_campo, tipo_evento,
                num_poda, producto_maestro, variedad, origen_evento, estado_evento, created_at
            ) VALUES (
                :c_id, :cb, :f_ev, 2642, 'SIEMBRA',
                0, 'VERONICA', 'SKYLER PINK', 'Corrección Siembra Semana 42', 'ACTIVO', NOW()
            ) ON CONFLICT DO NOTHING;
        """), {'c_id': c_id, 'cb': cb, 'f_ev': f_39e})

    # 3. Actualizar dim_camas para reflejar el cultivo comercial activo
    db.session.execute(text("""
        UPDATE dim_camas
        SET area_producto_comercial = 'SOLIDAGO',
            variedad_comercial = 'GOLDEN GLORY YELLOW'
        WHERE blq = '36' OR codigo_bloque LIKE '36%';
    """))

    db.session.execute(text("""
        UPDATE dim_camas
        SET area_producto_comercial = 'HYPERICUM',
            variedad_comercial = 'MAGICAL TRIUMPH'
        WHERE blq = '38C' OR codigo_bloque LIKE '38C%';
    """))

    db.session.execute(text("""
        UPDATE dim_camas
        SET area_producto_comercial = 'VERONICA',
            variedad_comercial = 'SKYLER PINK'
        WHERE blq = '39E' OR codigo_bloque LIKE '39E%';
    """))

    db.session.commit()
    print("  [OK] Eventos de siembra y dim_camas actualizados exitosamente.")

    # 4. Sincronizar fenograma para Semana 45 (y 44 si aplica)
    print("\n[SYNC] Sincronizando fenograma Semana 45...")
    # Limpiar solo las filas no calificadas (cumplido IS NULL) de los bloques corregidos
    db.session.execute(text("""
        DELETE FROM calificaciones
        WHERE semana_anio = 45
          AND bloque IN ('36', '38C', '39E', '36A', '38CA', '38CB', '39EA', '39EB')
          AND cumplido IS NULL;
    """))
    db.session.commit()

    count_45 = sync_from_mrt_estado_cultivo(semana_corte=45, anio=2026, fuente='AUTO', forzar_limpieza=False)
    print(f"  [OK] Fenograma Semana 45 sincronizado: {count_45} actividades en total.")
