"""
PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
ARCHIVO: scripts/limpiar_eventos_fantasma.py
DESCRIPCIÓN:
1. Purga eventos artificiales/fantasma inyectados por error ('Corrección Siembra%')
   en fact_eventos_cultivo que distorsionaban las edades reales de campo (ej. Bloque 36C a 4 semanas
   en vez de 14-16 semanas).
2. Restaura los cultivos oficiales en dim_camas para 38C y 39E (Solidago).
3. Sincroniza plano_cultivo y calificaciones con la vista viva mrt_estado_cultivo_actual.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text
from app.modules.fenogramas.services.fenograma_service import sync_from_mrt_estado_cultivo

app = create_app()

with app.app_context():
    print("=" * 80)
    print("🧹 PURGANDO EVENTOS FANTASMA Y RESTAURANDO EDADES REALES DE CULTIVO")
    print("=" * 80)

    # 1. Purgar eventos artificiales
    cnt_del = db.session.execute(text("""
        DELETE FROM fact_eventos_cultivo 
        WHERE origen_evento LIKE 'Corrección Siembra%';
    """)).rowcount
    print(f"  • Eventos fantasma eliminados de fact_eventos_cultivo: {cnt_del}")

    # 2. Restaurar cultivos comerciales en dim_camas
    cnt_38 = db.session.execute(text("""
        UPDATE dim_camas
        SET area_producto_comercial = 'SOLIDAGO',
            variedad_comercial = 'GOLDEN GLORY YELLOW'
        WHERE (blq = '38C' OR codigo_bloque LIKE '38C%') AND area_producto_comercial = 'HYPERICUM';
    """)).rowcount

    cnt_39 = db.session.execute(text("""
        UPDATE dim_camas
        SET area_producto_comercial = 'SOLIDAGO',
            variedad_comercial = 'GOLDEN GLORY YELLOW'
        WHERE (blq = '39E' OR codigo_bloque LIKE '39E%') AND area_producto_comercial = 'VERONICA';
    """)).rowcount
    print(f"  • dim_camas restauradas: 38C ({cnt_38} camas), 39E ({cnt_39} camas)")

    # 3. Eliminar segmentos obsoletos en plano_cultivo para 36C con edad 4
    db.session.execute(text("""
        DELETE FROM plano_cultivo
        WHERE bloque = '36C' AND edad_real = 4;
    """))

    db.session.commit()

    # 4. Sincronizar plano y calificaciones de la semana 48
    print("  • Sincronizando semana 48 con mrt_estado_cultivo_actual...")
    try:
        res = sync_from_mrt_estado_cultivo(48, anio=2026, fuente='AUTO', forzar_limpieza=False)
        print(f"  • Semana 48 sincronizada con éxito: {res} actividades registradas.")
    except Exception as e:
        print(f"  • Aviso al sincronizar semana 48: {e}")

    print("=" * 80)
    print("✅ RESTAURACIÓN COMPLETADA CON ÉXITO.")
    print("=" * 80)
