import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import text
from app import create_app, db

def run_fix():
    app = create_app()
    with app.app_context():
        print("1. Eliminando semanas ficticias (2026-41, 2026-42) de crop_state_records...")
        db.session.execute(text("DELETE FROM crop_state_records WHERE week IN ('2026-41', '2026-42', '2641', '2642');"))
        db.session.commit()
        print("   [OK] Semanas eliminadas.")

        print("2. Recreando vista 'mrt_estado_cultivo_actual' manteniendo columnas idénticas pero gobernada por el corte de cultivo...")
        view_sql = """
        CREATE OR REPLACE VIEW mrt_estado_cultivo_actual AS
        WITH ultimos_eventos AS (
            SELECT e.id_cama,
                e.codigo_bloque,
                e.fecha_evento,
                e.tipo_evento,
                e.num_poda,
                e.producto_maestro,
                e.variedad,
                e.num_plantas,
                e.fecha_hora_registro,
                row_number() OVER (PARTITION BY e.id_cama ORDER BY e.fecha_evento DESC, e.id_evento DESC) AS fila_num
            FROM stg_eventos e
        ),
        semana_corte_calc AS (
            SELECT c.semana_abs AS semana_abs_hoy,
                   c.semana_campo AS semana_campo_hoy
            FROM dim_calendario_agricola c
            WHERE c.semana_campo = (
                SELECT MAX(semana_campo) FROM (
                    SELECT MAX(semana_campo) AS semana_campo 
                    FROM fact_eventos_cultivo 
                    WHERE estado_evento = 'ACTIVO'
                    UNION
                    SELECT MAX(
                        CASE 
                            WHEN week ~ '^20[0-9]{2}-[0-9]{1,2}$' THEN (SUBSTRING(week, 3, 2)::int * 100 + SUBSTRING(week, 6, 2)::int)
                            WHEN week ~ '^[0-9]{4}$' THEN week::int
                            ELSE 2640
                        END
                    ) AS semana_campo 
                    FROM crop_state_records
                ) t
            )
            LIMIT 1
        ),
        semana_hoy AS (
            SELECT 
                COALESCE((SELECT semana_abs_hoy FROM semana_corte_calc), 1040) AS semana_abs_hoy,
                COALESCE((SELECT semana_campo_hoy FROM semana_corte_calc), 2640) AS semana_campo_hoy
        )
        SELECT f.id_finca,
            f.nombre_finca,
            b.zona,
            b.tecnico_de_riego,
            c.codigo_bloque,
            c.blq,
            c.sufijo,
            c.id_cama,
            c.num_cama,
            c.cama_estandar,
            c.metros_netos,
            c.metros_brutos,
            c.ha,
            ue.producto_maestro AS producto_actual,
            ue.variedad AS variedad_actual,
            ue.tipo_evento AS ultimo_evento,
            ue.num_poda AS num_poda_actual,
            ue.fecha_evento AS fecha_ultimo_evento,
            cal_ev.semana_campo AS semana_campo_ultimo_evento,
            CASE
                WHEN ue.tipo_evento::text = 'TUMBA'::text THEN NULL::integer
                ELSE sh.semana_abs_hoy - cal_ev.semana_abs
            END AS edad_semanas_actual,
            CASE
                WHEN ue.tipo_evento IS NULL THEN 'SIN REGISTRO DE EVENTOS'::text
                WHEN ue.tipo_evento::text = 'TUMBA'::text THEN 'ERRADICADO / DISPONIBLE PARA SIEMBRA'::text
                WHEN ue.tipo_evento::text = 'PODA'::text THEN ('EN PRODUCCIÓN (PODA '::text || COALESCE(ue.num_poda::text, '1'::text)) || ')'::text
                WHEN ue.tipo_evento::text = 'SIEMBRA'::text THEN 'CULTIVO NUEVO (SIEMBRA)'::text
                ELSE 'EN PRODUCCIÓN'::text
            END AS estado_cultivo,
            sh.semana_campo_hoy AS semana_agricola_actual,
            COALESCE(ue.num_plantas, c.num_plantas) AS num_plantas_actual,
            ue.fecha_hora_registro AS fecha_hora_ultimo_registro,
            cal_ev.semana_abs AS semana_abs_ultimo_evento
        FROM dim_camas c
        JOIN dim_bloques b ON c.codigo_bloque::text = b.codigo_bloque::text
        JOIN dim_fincas f ON c.id_finca = f.id_finca
        LEFT JOIN ultimos_eventos ue ON c.id_cama::text = ue.id_cama::text AND ue.fila_num = 1
        LEFT JOIN dim_calendario_agricola cal_ev ON ue.fecha_evento = cal_ev.fecha
        CROSS JOIN semana_hoy sh
        WHERE c.activa = true;
        """
        db.session.execute(text(view_sql))
        db.session.commit()
        print("   [OK] Vista 'mrt_estado_cultivo_actual' recreada con éxito.")



        # Verificar
        r1 = db.session.execute(text("SELECT semana_agricola_actual, count(*) FROM mrt_estado_cultivo_actual GROUP BY semana_agricola_actual;")).fetchall()
        print("   -> mrt_estado_cultivo_actual semana_agricola_actual:", r1)
        r2 = db.session.execute(text("SELECT week, count(*) FROM crop_state_records GROUP BY week ORDER BY week DESC;")).fetchall()
        print("   -> crop_state_records semanas:", r2)

if __name__ == '__main__':
    run_fix()
