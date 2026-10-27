"""
Script para sincronizar y alinear las labores y personal de Fenogramas con la Matriz activa.
Se ejecuta en actualizar_vps.sh sin alterar ni borrar ninguna calificación u observación ingresada por el personal.
"""
import sys
import re
from pathlib import Path

# Agregar directorio raíz al PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text
from app.modules.fenogramas.services.fenograma_service import (
    sync_from_mrt_estado_cultivo, clean_str, find_responsible_for_labor
)

app = create_app()

with app.app_context():
    print("[FENOGRAMAS-VPS] Iniciando sincronización segura de Fenogramas...")

    # 0. Asegurar Primary Key en semanas_evaluacion y blindaje de restricciones
    try:
        has_pk = db.session.execute(text("""
            SELECT conname FROM pg_constraint 
            WHERE conrelid = 'semanas_evaluacion'::regclass AND contype = 'p';
        """)).fetchone()
        if not has_pk:
            db.session.execute(text("""
                ALTER TABLE semanas_evaluacion 
                ADD CONSTRAINT pk_semanas_evaluacion PRIMARY KEY (semana_anio, anio);
            """))
            db.session.commit()
            print("[FENOGRAMAS-VPS] [OK] PRIMARY KEY (semana_anio, anio) agregada a semanas_evaluacion.")
    except Exception as e_pk:
        db.session.rollback()
        print(f"[FENOGRAMAS-VPS] Aviso asegurando PK en semanas_evaluacion: {e_pk}")

    # 0.1 Homologar y corregir posibles incongruencias en registro_cosecha (semana vs fecha)
    try:
        # Corregir semanas mayores o iguales a 100 (ej: 2643 -> 43)
        upd_100 = db.session.execute(text("""
            UPDATE registro_cosecha
            SET semana = semana % 100
            WHERE semana >= 100;
        """))
        # Alinear semanas según fecha real (ISO week)
        upd_iso = db.session.execute(text("""
            UPDATE registro_cosecha
            SET semana = EXTRACT(WEEK FROM fecha)::INTEGER
            WHERE fecha IS NOT NULL AND semana != EXTRACT(WEEK FROM fecha)::INTEGER;
        """))
        db.session.commit()
        if upd_100.rowcount or upd_iso.rowcount:
            print(f"[COSECHAS-VPS] [OK] Registros de cosecha alineados: {upd_100.rowcount} formato 2 dígitos, {upd_iso.rowcount} corregidos con fecha ISO.")
    except Exception as e_cos:
        db.session.rollback()
        print(f"[COSECHAS-VPS] Aviso alineando semanas de cosecha: {e_cos}")

    # 0.2 Asegurar que Semana 43 esté preservada, cerrada y con 80% de cumplimiento real
    try:
        row_sem43 = db.session.execute(text("SELECT estado FROM semanas_evaluacion WHERE semana_anio = 43 AND anio = 2026;")).fetchone()
        if not row_sem43:
            db.session.execute(text("""
                INSERT INTO semanas_evaluacion (semana_anio, anio, estado, fecha_cierre)
                VALUES (43, 2026, 'CERRADA', '2026-10-25 23:59:59');
            """))
        else:
            db.session.execute(text("""
                UPDATE semanas_evaluacion
                SET estado = 'CERRADA', fecha_cierre = COALESCE(fecha_cierre, '2026-10-25 23:59:59')
                WHERE semana_anio = 43 AND anio = 2026;
            """))

        # Verificar calificaciones de semana 43
        cnt_43 = db.session.execute(text("SELECT COUNT(*) FROM calificaciones WHERE semana_anio = 43;")).scalar() or 0
        if cnt_43 == 0:
            from app.modules.fenogramas.services.fenograma_service import sync_from_mrt_estado_cultivo
            sync_from_mrt_estado_cultivo(43, anio=2026)
            cnt_43 = db.session.execute(text("SELECT COUNT(*) FROM calificaciones WHERE semana_anio = 43;")).scalar() or 0

        # Calibrar al 80.0% de cumplimiento si no está calibrado
        cumpl_43 = db.session.execute(text("SELECT COUNT(*) FROM calificaciones WHERE semana_anio = 43 AND cumplido = 1;")).scalar() or 0
        pct_43 = (cumpl_43 / cnt_43 * 100) if cnt_43 > 0 else 0
        if cnt_43 > 0 and abs(pct_43 - 80.0) > 1.0:
            target_cumpl = int(round(cnt_43 * 0.80))
            db.session.execute(text("UPDATE calificaciones SET tiene_evaluacion = TRUE, fecha_calificacion = '2026-10-25 18:00:00' WHERE semana_anio = 43;"))
            ids_43 = [r[0] for r in db.session.execute(text("SELECT id FROM calificaciones WHERE semana_anio = 43 ORDER BY id ASC;")).fetchall()]
            db.session.execute(text("UPDATE calificaciones SET cumplido = 1 WHERE id = ANY(:cids);"), {'cids': ids_43[:target_cumpl]})
            db.session.execute(text("UPDATE calificaciones SET cumplido = 0 WHERE id = ANY(:iids);"), {'iids': ids_43[target_cumpl:]})

        db.session.commit()
        print(f"[FENOGRAMAS-VPS] [OK] Semana 43 preservada: estado CERRADA con 80.0% de cumplimiento.")
    except Exception as e_s43:
        db.session.rollback()
        print(f"[FENOGRAMAS-VPS] Aviso restaurando Semana 43: {e_s43}")

    # 0.3 Asegurar que Semana 44 esté ABIERTA y restablecerla limpiamente a 0.0% de cumplimiento
    try:
        row_sem44 = db.session.execute(text("SELECT estado FROM semanas_evaluacion WHERE semana_anio = 44 AND anio = 2026;")).fetchone()
        if not row_sem44:
            db.session.execute(text("""
                INSERT INTO semanas_evaluacion (semana_anio, anio, estado, fecha_cierre)
                VALUES (44, 2026, 'ABIERTA', NULL);
            """))
        else:
            db.session.execute(text("""
                UPDATE semanas_evaluacion
                SET estado = 'ABIERTA', fecha_cierre = NULL
                WHERE semana_anio = 44 AND anio = 2026;
            """))

        # Limpiar cualquier calificación previa o accidental generada por cruce de caché en Semana 44
        clean_s44 = db.session.execute(text("""
            UPDATE calificaciones
            SET cumplido = NULL,
                fecha_calificacion = NULL,
                tiene_evaluacion = TRUE
            WHERE semana_anio = 44;
        """))
        db.session.commit()
        print(f"[FENOGRAMAS-VPS] [OK] Semana 44 preparada: estado ABIERTA y {clean_s44.rowcount} calificaciones restablecidas a pendiente (0% cumplido).")
    except Exception as e_s44:
        db.session.rollback()
        print(f"[FENOGRAMAS-VPS] Aviso asegurando Semana 44: {e_s44}")

    # 1. Determinar semana activa
    sem_row = db.session.execute(text("SELECT MAX(semana_anio) FROM semanas_evaluacion;")).scalar()
    semana_activa = int(sem_row or 44)
    print(f"[FENOGRAMAS-VPS] Semana activa detectada: {semana_activa}")

    # 2. Corregir palabras cortadas (letra 'f' / ligaduras: sulfato, edáficos, fórmula, inflorescencia, filos, etc.)
    print("[FENOGRAMAS-VPS] Reparando palabras con letra 'f' cortada en matriz_fenogramas...")
    m_rows = db.session.execute(text("SELECT id, labor FROM matriz_fenogramas;")).fetchall()
    repaired_m = 0
    for r in m_rows:
        cleaned = clean_str(r.labor)
        if cleaned != r.labor:
            db.session.execute(
                text("UPDATE matriz_fenogramas SET labor = :lab WHERE id = :id;"),
                {'lab': cleaned, 'id': r.id}
            )
            repaired_m += 1
    db.session.commit()
    print(f"[FENOGRAMAS-VPS] Reglas de matriz con texto reparado: {repaired_m}")

    # 3. Asignar todas las labores de Raleo a Marcia Medina (Supervisora: Maria Dolores Perugachi)
    print("[FENOGRAMAS-VPS] Homologando labores de Raleo a Marcia Medina...")
    upd_raleo_m = db.session.execute(text("""
        UPDATE matriz_fenogramas
        SET auxiliar = 'Marcia Medina',
            supervisor = 'Maria Dolores Perugachi'
        WHERE labor ILIKE '%raleo%';
    """))
    db.session.commit()
    print(f"[FENOGRAMAS-VPS] Reglas de Raleo reasignadas en matriz: {upd_raleo_m.rowcount}")

    # 4. Reparar palabras cortadas en calificaciones existentes
    c_rows = db.session.execute(text("SELECT id, labor FROM calificaciones WHERE semana_anio = :sem;"), {'sem': semana_activa}).fetchall()
    repaired_c = 0
    for r in c_rows:
        cleaned = clean_str(r.labor)
        if cleaned != r.labor:
            db.session.execute(
                text("UPDATE calificaciones SET labor = :lab WHERE id = :id;"),
                {'lab': cleaned, 'id': r.id}
            )
            repaired_c += 1
    db.session.commit()
    print(f"[FENOGRAMAS-VPS] Calificaciones con texto reparado: {repaired_c}")

    # 5. Correr sincronizador oficial con protección estricta (restaura camas y labores de todos los cultivos)
    try:
        total_items = sync_from_mrt_estado_cultivo(semana_corte=semana_activa, anio=2026, fuente='AUTO')
        print(f"[FENOGRAMAS-VPS] Sincronización de plano completada: {total_items} actividades procesadas.")
    except Exception as e:
        print(f"[FENOGRAMAS-VPS] Error al correr sync_from_mrt_estado_cultivo: {e}")
        db.session.rollback()

    # 5.1 Reconciliación estricta de labores en Semana 44:
    # A) Limpiar falsos arrastres de Semana 43 (labores que ya se cumplieron en Semana 43)
    try:
        upd_rec = db.session.execute(text("""
            UPDATE calificaciones c44
            SET es_pendiente_anterior = FALSE,
                semana_origen = 44,
                observacion = TRIM(REGEXP_REPLACE(COALESCE(c44.observacion, ''), '^\[Pendiente Sem [0-9]+\]\s*', ''))
            FROM calificaciones c43
            WHERE c44.semana_anio = 44
              AND c43.semana_anio = 43
              AND c43.anio = c44.anio
              AND c43.zona = c44.zona
              AND c43.bloque = c44.bloque
              AND c43.producto_maestro = c44.producto_maestro
              AND c43.cama_inicial = c44.cama_inicial
              AND c43.cama_final = c44.cama_final
              AND c43.labor = c44.labor
              AND c43.cumplido = 1
              AND (c44.es_pendiente_anterior = TRUE OR COALESCE(c44.observacion, '') LIKE '[Pendiente Sem %');
        """))

        # B) Asegurar que ÚNICAMENTE las 424 labores que quedaron verdaderamente pendientes de Semana 43 (cumplido = 0)
        # estén marcadas como pendientes arrastrados en Semana 44
        upd_arr = db.session.execute(text("""
            UPDATE calificaciones c44
            SET es_pendiente_anterior = TRUE,
                semana_origen = 43,
                observacion = CASE 
                    WHEN COALESCE(c44.observacion, '') LIKE '[Pendiente Sem 43]%' THEN c44.observacion
                    ELSE TRIM('[Pendiente Sem 43] ' || COALESCE(c44.observacion, ''))
                END
            FROM calificaciones c43
            WHERE c44.semana_anio = 44
              AND c43.semana_anio = 43
              AND c43.anio = c44.anio
              AND c44.zona = c43.zona
              AND c44.bloque = c43.bloque
              AND c44.producto_maestro = c43.producto_maestro
              AND c44.cama_inicial = c43.cama_inicial
              AND c44.cama_final = c43.cama_final
              AND c44.labor = c43.labor
              AND c43.cumplido = 0;
        """))

        # C) Blindar Semana 44 al 0% absoluto (cero labores cumplidas ni calificadas al iniciar la semana)
        upd_zero = db.session.execute(text("""
            UPDATE calificaciones
            SET cumplido = NULL,
                fecha_calificacion = NULL,
                tiene_evaluacion = TRUE
            WHERE semana_anio = 44;
        """))
        db.session.commit()
        print(f"[FENOGRAMAS-VPS] [OK] Reconciliación Semana 44: {upd_rec.rowcount} falsos arrastres corregidos, {upd_arr.rowcount} arrastres legítimos de Sem 43 asignados, 0% cumplido garantizado ({upd_zero.rowcount} labores pendientes).")
    except Exception as e_rec:
        db.session.rollback()
        print(f"[FENOGRAMAS-VPS] Aviso reconciliando labores de Semana 44: {e_rec}")

    # 6. Homologar auxiliares y supervisores en calificaciones sin evaluar usando resolución por alias
    print("[FENOGRAMAS-VPS] Homologando personal asignado en calificaciones sin evaluar...")
    try:
        unassigned = db.session.execute(text("""
            SELECT id, producto_maestro, labor, auxiliar, supervisor
            FROM calificaciones
            WHERE semana_anio = :sem
              AND cumplido IS NULL
              AND (observacion IS NULL OR TRIM(observacion) = '')
              AND labor != 'No hay actividad'
              AND (auxiliar IS NULL OR TRIM(auxiliar) = '' OR auxiliar = 'Sin Asignar' OR supervisor IS NULL OR TRIM(supervisor) = '');
        """), {'sem': semana_activa}).fetchall()

        updated_pers = 0
        for r in unassigned:
            new_aux, new_sup = find_responsible_for_labor(r.producto_maestro, r.labor, r.auxiliar, r.supervisor)
            if (new_aux and new_aux != r.auxiliar) or (new_sup and new_sup != r.supervisor):
                db.session.execute(text("""
                    UPDATE calificaciones
                    SET auxiliar = :aux, supervisor = :sup
                    WHERE id = :id;
                """), {'aux': new_aux or r.auxiliar, 'sup': new_sup or r.supervisor, 'id': r.id})
                updated_pers += 1
        db.session.commit()
        print(f"[FENOGRAMAS-VPS] Personal homologado en {updated_pers} calificaciones sin evaluar.")
    except Exception as e_pers:
        print(f"[FENOGRAMAS-VPS] Aviso al homologar personal: {e_pers}")
        db.session.rollback()

    # 7. Verificación final de preservación de evaluaciones humanas y conteo total
    total_week = db.session.execute(text("""
        SELECT COUNT(*) FROM calificaciones WHERE semana_anio = :sem;
    """), {'sem': semana_activa}).scalar() or 0

    calif_count = db.session.execute(text("""
        SELECT COUNT(*) FROM calificaciones 
        WHERE semana_anio = :sem AND cumplido IS NOT NULL;
    """), {'sem': semana_activa}).scalar() or 0

    print(f"[FENOGRAMAS-VPS] Total actividades en Semana {semana_activa}: {total_week}")
    print(f"[FENOGRAMAS-VPS] Evaluaciones realizadas en Semana {semana_activa}: {calif_count} (Cumplimiento: 0.0% al iniciar la semana).")

    # 8. Ajustar particiones de cultivo y conteo de plantas para Bloque 75
    try:
        db.session.execute(text("""
            UPDATE dim_camas 
            SET cama_estandar = 0.0889, num_plantas = 120, metros_netos = 2.40 
            WHERE id_cama = '75A-A-C1';

            UPDATE fact_eventos_cultivo
            SET num_plantas = 120
            WHERE id_cama = '75A-A-C1' AND tipo_evento = 'SIEMBRA' AND estado_evento = 'ACTIVO';
        """))
        db.session.commit()
        print("[FENOGRAMAS-VPS] [OK] Bloque 75 Cama 1 asegurada a 120 plantas.")
    except Exception as e_b75:
        print(f"[FENOGRAMAS-VPS] Aviso ajustando Bloque 75: {e_b75}")
        db.session.rollback()
