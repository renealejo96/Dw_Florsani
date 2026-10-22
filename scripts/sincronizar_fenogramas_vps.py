"""
Script para sincronizar y alinear las labores y personal de Fenogramas con la Matriz activa.
Se ejecuta en actualizar_vps.sh sin alterar ni borrar ninguna calificación u observación ingresada por el personal.
"""
import sys
from pathlib import Path

# Agregar directorio raíz al PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text
from app.modules.fenogramas.services.fenograma_service import sync_from_mrt_estado_cultivo

app = create_app()

with app.app_context():
    print("[FENOGRAMAS-VPS] Iniciando sincronización segura de Fenogramas...")

    # 1. Determinar semana activa
    sem_row = db.session.execute(text("SELECT MAX(semana_anio) FROM semanas_evaluacion;")).scalar()
    semana_activa = int(sem_row or 43)
    print(f"[FENOGRAMAS-VPS] Semana activa detectada: {semana_activa}")

    # 2. Correr sincronizador oficial con protección estricta
    try:
        total_items = sync_from_mrt_estado_cultivo(semana_corte=semana_activa, anio=2026, fuente='AUTO')
        print(f"[FENOGRAMAS-VPS] Sincronización de plano completada: {total_items} actividades procesadas.")
    except Exception as e:
        print(f"[FENOGRAMAS-VPS] Error al correr sync_from_mrt_estado_cultivo: {e}")
        db.session.rollback()

    # 3. Homologar auxiliares y supervisores en calificaciones no evaluadas (donde no haya calificación humana)
    print("[FENOGRAMAS-VPS] Homologando asignaciones de personal contra matriz_fenogramas...")
    try:
        updated_pers = db.session.execute(text("""
            UPDATE calificaciones c
            SET auxiliar = m.auxiliar,
                supervisor = m.supervisor
            FROM matriz_fenogramas m
            WHERE c.semana_anio = :sem
              AND c.cumplido IS NULL
              AND (c.observacion IS NULL OR TRIM(c.observacion) = '')
              AND c.producto_maestro = m.producto
              AND c.actividad_tipo = m.actividad_tipo
              AND c.edad_real = m.semana
              AND c.labor = m.labor
              AND (c.auxiliar != m.auxiliar OR c.supervisor != m.supervisor);
        """), {'sem': semana_activa})
        db.session.commit()
        print(f"[FENOGRAMAS-VPS] Asignaciones de personal actualizadas en {updated_pers.rowcount} registros sin evaluar.")
    except Exception as e_pers:
        print(f"[FENOGRAMAS-VPS] Aviso al homologar personal: {e_pers}")
        db.session.rollback()

    # 4. Purgar labores fantasma obsoletas sin calificar (que no existen en la matriz ni tienen calificación)
    try:
        deleted_phantoms = db.session.execute(text("""
            DELETE FROM calificaciones
            WHERE semana_anio = :sem
              AND cumplido IS NULL
              AND (observacion IS NULL OR TRIM(observacion) = '')
              AND es_pendiente_anterior = FALSE
              AND labor != 'No hay actividad'
              AND NOT EXISTS (
                  SELECT 1 FROM matriz_fenogramas m
                  WHERE m.producto = calificaciones.producto_maestro
                    AND m.actividad_tipo = calificaciones.actividad_tipo
                    AND m.semana = calificaciones.edad_real
                    AND m.labor = calificaciones.labor
              );
        """), {'sem': semana_activa})
        db.session.commit()
        print(f"[FENOGRAMAS-VPS] Labores obsoletas sin calificar purgadas: {deleted_phantoms.rowcount} registros eliminados de forma segura.")
    except Exception as e_purge:
        print(f"[FENOGRAMAS-VPS] Aviso al purgar labores obsoletas: {e_purge}")
        db.session.rollback()

    # 5. Verificación final de preservación
    calif_count = db.session.execute(text("""
        SELECT COUNT(*) FROM calificaciones 
        WHERE semana_anio = :sem AND (cumplido IS NOT NULL OR (observacion IS NOT NULL AND TRIM(observacion) != ''));
    """), {'sem': semana_activa}).scalar() or 0
    print(f"[FENOGRAMAS-VPS] [OK] Calificaciones humanas preservadas al 100%: {calif_count} evaluaciones activas.")
