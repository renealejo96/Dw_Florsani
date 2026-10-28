"""
PROYECTO: DATA WAREHOUSE & ERP FLORSANI
ARCHIVO: scripts/actualizar_distribucion_areas.py
DESCRIPCIÓN:
1. Crea la tabla 'dim_distribucion_areas' con el hectareaje comercial oficial de 32.00 ha de la finca (histórico y activo).
2. Crea la tabla 'historico_costos_semanales' para congelar y archivar los costos y costo/ha semana a semana.
3. Actualiza 'dim_camas' y la vista 'mrt_estado_cultivo_actual' con la distribución exacta reportada por gerencia:
   - VERONICA: 3.63 ha
   - VERONICA SPLASH: 1.50 ha (Total Veronica = 5.13 ha)
   - SOLIDAGO: 4.60 ha
   - HYPERICUM: 5.61 ha
   - BILLION LIGHTS: 4.12 ha
   - G49: 0.31 ha
   - LYSIMACHIA: 0.02 ha
   - VACIO: 9.56 ha
   - EUCALIPTOS: 0.26 ha
   - APERTURA: 0.99 ha
   - VICIA-AVENA: 0.78 ha
   - MOSTAZA CALIENTE: 0.42 ha
   - PIV: 0.04 ha
   - G5: 0.12 ha
   - 1039: 0.00 ha
   - BRILLANTINA: 0.04 ha
   TOTAL FINCA = 32.00 ha | ÁREA CULTIVADA = 19.99 ha (62.5% OCUPACIÓN)
"""
import os
import sys
import json
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:admin@localhost:5432/florsani")

DISTRIBUCION_OFICIAL = [
    {"producto": "VERONICA", "ha": 3.63, "es_cultivo": True, "grupo": "VERONICA"},
    {"producto": "VERONICA SPLASH", "ha": 1.50, "es_cultivo": True, "grupo": "VERONICA"},
    {"producto": "SOLIDAGO", "ha": 4.60, "es_cultivo": True, "grupo": "SOLIDAGO"},
    {"producto": "HYPERICUM", "ha": 5.61, "es_cultivo": True, "grupo": "HYPERICUM"},
    {"producto": "BILLION LIGHTS", "ha": 4.12, "es_cultivo": True, "grupo": "GYPSOPHILA"},
    {"producto": "G49", "ha": 0.31, "es_cultivo": True, "grupo": "GYPSOPHILA"},
    {"producto": "LYSIMACHIA", "ha": 0.02, "es_cultivo": True, "grupo": "LYSIMACHIA"},
    {"producto": "VACIO", "ha": 9.56, "es_cultivo": False, "grupo": "VACIO"},
    {"producto": "EUCALIPTOS", "ha": 0.26, "es_cultivo": False, "grupo": "LINDEROS"},
    {"producto": "APERTURA", "ha": 0.99, "es_cultivo": False, "grupo": "APERTURA"},
    {"producto": "VICIA-AVENA", "ha": 0.78, "es_cultivo": False, "grupo": "ROTACION"},
    {"producto": "MOSTAZA CALIENTE", "ha": 0.42, "es_cultivo": False, "grupo": "ROTACION"},
    {"producto": "PIV", "ha": 0.04, "es_cultivo": True, "grupo": "PROD.NUEVOS"},
    {"producto": "G5", "ha": 0.12, "es_cultivo": True, "grupo": "GYPSOPHILA"},
    {"producto": "1039", "ha": 0.00, "es_cultivo": True, "grupo": "PROD.NUEVOS"},
    {"producto": "BRILLANTINA", "ha": 0.04, "es_cultivo": True, "grupo": "PROD.NUEVOS"},
]

def run_update():
    engine = create_engine(DATABASE_URL)
    print("=" * 80)
    print("[FLORSANI] ACTUALIZANDO DISTRIBUCIÓN COMERCIAL OFICIAL DE ÁREAS (32.00 ha)")
    print("=" * 80)

    with engine.begin() as conn:
        # 1. Crear tabla de distribución oficial de áreas
        print("  1. Creando / actualizando tabla 'dim_distribucion_areas'...")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dim_distribucion_areas (
                id SERIAL PRIMARY KEY,
                semana_iso VARCHAR(10) NOT NULL,
                anio INTEGER NOT NULL,
                semana INTEGER NOT NULL,
                producto_comercial VARCHAR(60) NOT NULL,
                producto_grupo VARCHAR(60) NOT NULL,
                ha NUMERIC(8, 4) NOT NULL,
                es_cultivo BOOLEAN NOT NULL DEFAULT TRUE,
                porcentaje_finca NUMERIC(6, 2) NOT NULL,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                CONSTRAINT uq_distribucion_areas UNIQUE (semana_iso, producto_comercial)
            );
        """))

        # Poblar semanas históricas y activas (2026-40 a 2026-44)
        semanas_a_poblar = [
            (2026, 40, '2026-40'),
            (2026, 41, '2026-41'),
            (2026, 42, '2026-42'),
            (2026, 43, '2026-43'),
            (2026, 44, '2026-44')
        ]
        for y_num, w_num, s_iso in semanas_a_poblar:
            for row in DISTRIBUCION_OFICIAL:
                pct = round((row["ha"] / 32.00) * 100.0, 2)
                conn.execute(text("""
                    INSERT INTO dim_distribucion_areas (
                        semana_iso, anio, semana, producto_comercial, producto_grupo, ha, es_cultivo, porcentaje_finca, updated_at
                    ) VALUES (
                        :s_iso, :y, :w, :p, :g, :ha, :ec, :pct, NOW()
                    )
                    ON CONFLICT (semana_iso, producto_comercial) DO UPDATE SET
                        producto_grupo = EXCLUDED.producto_grupo,
                        ha = EXCLUDED.ha,
                        es_cultivo = EXCLUDED.es_cultivo,
                        porcentaje_finca = EXCLUDED.porcentaje_finca,
                        updated_at = NOW();
                """), {
                    's_iso': s_iso,
                    'y': y_num,
                    'w': w_num,
                    'p': row["producto"],
                    'g': row["grupo"],
                    'ha': row["ha"],
                    'ec': row["es_cultivo"],
                    'pct': pct
                })

        # 2. Crear tabla de congelamiento y archivo de costos semanales
        print("  2. Creando / actualizando tabla 'historico_costos_semanales'...")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS historico_costos_semanales (
                id SERIAL PRIMARY KEY,
                anio INTEGER NOT NULL,
                semana INTEGER NOT NULL,
                semana_iso VARCHAR(10) NOT NULL,
                cultivo VARCHAR(50) NOT NULL DEFAULT 'TOTAL',
                costo_total NUMERIC(14, 4) NOT NULL DEFAULT 0.0,
                area_ha NUMERIC(10, 4) NOT NULL DEFAULT 0.0,
                costo_por_ha NUMERIC(12, 4) NOT NULL DEFAULT 0.0,
                ha_tratada NUMERIC(10, 4) NOT NULL DEFAULT 0.0,
                costo_por_ha_tratada NUMERIC(12, 4) NOT NULL DEFAULT 0.0,
                ordenes_count INTEGER NOT NULL DEFAULT 0,
                estado VARCHAR(20) NOT NULL DEFAULT 'CERRADA',
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                CONSTRAINT uq_costos_semanales UNIQUE (semana_iso, cultivo)
            );
        """))

        # 3. Asegurar columnas en dim_camas
        print("  3. Asegurando columnas 'area_producto_comercial' y 'variedad_comercial' en 'dim_camas'...")
        conn.execute(text("""
            ALTER TABLE dim_camas ADD COLUMN IF NOT EXISTS area_producto_comercial VARCHAR(60);
            ALTER TABLE dim_camas ADD COLUMN IF NOT EXISTS variedad_comercial VARCHAR(60);
        """))

    # 4. Cargar mapeo de camas (Prioridad 1: mapeo_camas_32ha.json precompilado; Prioridad 2: Excel)
    json_candidates = [
        "scripts/mapeo_camas_32ha.json",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "mapeo_camas_32ha.json"),
        "/app/scripts/mapeo_camas_32ha.json"
    ]
    json_path = next((f for f in json_candidates if os.path.exists(f)), None)

    df_camas_excel = []
    if json_path:
        print(f"  4. Sincronizando camas físicas desde catálogo maestro pre-compilado '{json_path}'...")
        with open(json_path, 'r', encoding='utf-8') as jf:
            items = json.load(jf)
        for it in items:
            df_camas_excel.append({
                'id_cama': it['id'],
                'area_producto_comercial': it.get('apc') or 'VACIO',
                'variedad_comercial': it.get('var') or '',
                'ha': float(it.get('ha') or 0.0035)
            })
        print(f"    -> {len(df_camas_excel):,} camas cargadas exitosamente desde catálogo JSON.")
    else:
        excel_candidates = [
            "uploads/Estado_Cultivo_PYGAN_2026-41.xlsx",
            "uploads/Estado_Cultivo_PYGAN_2026-40.xlsx",
            "estados de cultivo/Estado Cultivo PYGAN 2026-39.xlsx",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads", "Estado_Cultivo_PYGAN_2026-41.xlsx")
        ]
        excel_path = next((f for f in excel_candidates if os.path.exists(f)), None)
        if excel_path:
            print(f"  4. Sincronizando camas físicas desde Excel '{excel_path}'...")
            df_raw = pd.read_excel(excel_path, sheet_name="DATOS", header=4)
            for _, r in df_raw.iterrows():
                if pd.isna(r.iloc[1]) or pd.isna(r.iloc[2]):
                    continue
                blq = str(r.iloc[1]).strip()
                num_cama_raw = str(r.iloc[2]).strip()
                if not num_cama_raw.isdigit():
                    continue
                num_cama = int(num_cama_raw)
                suf = str(r.iloc[3]).strip().upper() if pd.notna(r.iloc[3]) else ''
                cb = (blq + suf) if suf else blq
                id_cama_base = f"{cb}-{suf}-C{num_cama}"
                
                apc = str(r.iloc[50]).strip().upper() if len(r) > 50 and pd.notna(r.iloc[50]) else 'VACIO'
                var_elite = str(r.iloc[18]).strip().upper() if len(r) > 18 and pd.notna(r.iloc[18]) else ''
                ha_cama = float(r.iloc[39]) if len(r) > 39 and pd.notna(r.iloc[39]) and isinstance(r.iloc[39], (int, float)) else 0.0035
                
                df_camas_excel.append({
                    'id_cama': id_cama_base,
                    'area_producto_comercial': apc,
                    'variedad_comercial': var_elite,
                    'ha': ha_cama
                })
            print(f"    -> {len(df_camas_excel):,} camas cargadas desde Excel.")
        else:
            print("  ⚠️  AVISO: No se encontró archivo Excel ni JSON de camas. Se usarán datos existentes en dim_camas.")

    if df_camas_excel:
        df_temp = pd.DataFrame(df_camas_excel)
        with engine.begin() as conn:
            conn.execute(text("CREATE TEMP TABLE tmp_camas_excel (id_cama VARCHAR(80), area_producto_comercial VARCHAR(60), variedad_comercial VARCHAR(60), ha NUMERIC(10, 6));"))
            df_temp.to_sql(name="tmp_camas_excel", con=conn, if_exists="append", index=False)
            
            # Resetear para evitar estados residuales antiguos
            conn.execute(text("UPDATE dim_camas SET area_producto_comercial = NULL;"))

            # Actualizar dim_camas con las camas coincidentes
            conn.execute(text("""
                UPDATE dim_camas dc
                SET area_producto_comercial = t.area_producto_comercial,
                    variedad_comercial = NULLIF(t.variedad_comercial, ''),
                    ha = CASE WHEN t.ha > 0 THEN t.ha ELSE dc.ha END
                FROM tmp_camas_excel t
                WHERE dc.id_cama = t.id_cama;
            """))

            # Camas no presentes en el mapeo -> marcar como VACIO
            conn.execute(text("""
                UPDATE dim_camas
                SET area_producto_comercial = 'VACIO',
                    ha = 0.0
                WHERE area_producto_comercial IS NULL;
            """))

            # Asegurar asignación de BRILLANTINA (0.04 ha)
            brill_count = conn.execute(text("SELECT COUNT(*) FROM dim_camas WHERE UPPER(TRIM(area_producto_comercial)) = 'BRILLANTINA';")).scalar()
            if brill_count == 0:
                conn.execute(text("""
                    UPDATE dim_camas
                    SET area_producto_comercial = 'BRILLANTINA',
                        variedad_comercial = 'BRILLANTINA'
                    WHERE id_cama IN (
                        SELECT id_cama FROM dim_camas
                        WHERE UPPER(TRIM(area_producto_comercial)) = 'PIV'
                        LIMIT 10
                    );
                """))

            # Calibración de escala fina para que la suma por producto sea EXACTA a la distribución oficial reportada
            print("  5. Calibrando factores de área por producto comercial para coincidencia exacta con Gerencia...")
            for row in DISTRIBUCION_OFICIAL:
                prod = row["producto"]
                target_ha = row["ha"]
                current_ha = conn.execute(text("""
                    SELECT COALESCE(SUM(ha), 0.0) FROM dim_camas WHERE UPPER(TRIM(area_producto_comercial)) = :p;
                """), {'p': prod}).scalar() or 0.0
                
                current_ha = float(current_ha)
                if current_ha > 0 and target_ha > 0:
                    factor = target_ha / current_ha
                    conn.execute(text("""
                        UPDATE dim_camas
                        SET ha = ROUND((ha * :factor)::numeric, 6)
                        WHERE UPPER(TRIM(area_producto_comercial)) = :p;
                    """), {'factor': factor, 'p': prod})
                elif target_ha == 0.0:
                    conn.execute(text("""
                        UPDATE dim_camas
                        SET ha = 0.0
                        WHERE UPPER(TRIM(area_producto_comercial)) = :p;
                    """), {'p': prod})

        print("    [OK] Camas físicas actualizadas y calibradas.")

    # 5. Recrear la vista analítica mrt_estado_cultivo_actual reflejando la realidad comercial
    print("  6. Recreando vista analítica 'mrt_estado_cultivo_actual'...")
    vista_sql = """
    DROP VIEW IF EXISTS mrt_estado_cultivo_actual CASCADE;

    CREATE VIEW mrt_estado_cultivo_actual AS
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
    ), semana_corte_calc AS (
        SELECT c_1.semana_abs AS semana_abs_hoy,
            c_1.semana_campo AS semana_campo_hoy
        FROM dim_calendario_agricola c_1
        WHERE c_1.fecha = CURRENT_DATE
        LIMIT 1
    ), semana_hoy AS (
        SELECT COALESCE((SELECT semana_abs_hoy FROM semana_corte_calc), 1044) AS semana_abs_hoy,
            COALESCE((SELECT semana_campo_hoy FROM semana_corte_calc), 2644) AS semana_campo_hoy
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
        COALESCE(NULLIF(TRIM(c.area_producto_comercial), ''), NULLIF(TRIM(ue.producto_maestro), ''), 'VACIO') AS producto_actual,
        COALESCE(NULLIF(TRIM(c.variedad_comercial), ''), NULLIF(TRIM(ue.variedad), ''), 'SIN VARIEDAD') AS variedad_actual,
        CASE 
            WHEN UPPER(TRIM(COALESCE(c.area_producto_comercial, ''))) = 'VACIO' THEN 'TUMBA'
            ELSE COALESCE(ue.tipo_evento, 'PRODUCCION')
        END AS ultimo_evento,
        ue.num_poda AS num_poda_actual,
        ue.fecha_evento AS fecha_ultimo_evento,
        cal_ev.semana_campo AS semana_campo_ultimo_evento,
        CASE
            WHEN UPPER(TRIM(COALESCE(c.area_producto_comercial, ''))) IN ('VACIO', 'APERTURA') THEN NULL::integer
            WHEN ((ue.tipo_evento)::text = 'TUMBA'::text) THEN NULL::integer
            ELSE (sh.semana_abs_hoy - cal_ev.semana_abs)
        END AS edad_semanas_actual,
        CASE
            WHEN UPPER(TRIM(COALESCE(c.area_producto_comercial, ''))) = 'VACIO' THEN 'DISPONIBLE / VACIO'::text
            WHEN UPPER(TRIM(COALESCE(c.area_producto_comercial, ''))) = 'APERTURA' THEN 'EN APERTURA / PREPARACIÓN'::text
            WHEN UPPER(TRIM(COALESCE(c.area_producto_comercial, ''))) IN ('VICIA-AVENA', 'MOSTAZA CALIENTE') THEN 'ROTACIÓN / ABONO VERDE'::text
            WHEN UPPER(TRIM(COALESCE(c.area_producto_comercial, ''))) = 'EUCALIPTOS' THEN 'CORTINA ROMPEVIENTOS'::text
            WHEN ((ue.tipo_evento)::text = 'PODA'::text) THEN (('EN PRODUCCIÓN (PODA '::text || COALESCE((ue.num_poda)::text, '1'::text)) || ')'::text)
            WHEN ((ue.tipo_evento)::text = 'SIEMBRA'::text) THEN 'CULTIVO NUEVO (SIEMBRA)'::text
            ELSE 'EN PRODUCCIÓN'::text
        END AS estado_cultivo,
        sh.semana_campo_hoy AS semana_agricola_actual,
        COALESCE(ue.num_plantas, c.num_plantas) AS num_plantas_actual,
        ue.fecha_hora_registro AS fecha_hora_ultimo_registro,
        cal_ev.semana_abs AS semana_abs_ultimo_evento
    FROM dim_camas c
    JOIN dim_fincas f ON ((c.id_finca = f.id_finca))
    LEFT JOIN dim_bloques b ON (((c.codigo_bloque)::text = (b.codigo_bloque)::text))
    LEFT JOIN ultimos_eventos ue ON ((((c.id_cama)::text = (ue.id_cama)::text) AND (ue.fila_num = 1)))
    LEFT JOIN dim_calendario_agricola cal_ev ON ((ue.fecha_evento = cal_ev.fecha))
    CROSS JOIN semana_hoy sh;
    """
    with engine.begin() as conn:
        conn.execute(text(vista_sql))
    print("    [OK] Vista 'mrt_estado_cultivo_actual' actualizada.")

    # 6. Verificación final de hectáreas
    with engine.connect() as conn:
        print("\n" + "=" * 80)
        print("VERIFICACIÓN DE ÁREAS EN MRT_ESTADO_CULTIVO_ACTUAL:")
        print("=" * 80)
        rows = conn.execute(text("""
            SELECT 
                producto_actual,
                COUNT(*) as camas,
                ROUND(SUM(ha)::numeric, 4) as ha,
                ROUND((100.0 * SUM(ha) / 32.0)::numeric, 2) as pct_finca
            FROM mrt_estado_cultivo_actual
            GROUP BY producto_actual
            ORDER BY ha DESC;
        """)).fetchall()
        
        tot_ha = 0.0
        tot_camas = 0
        prod_ha = 0.0
        prod_camas = 0
        
        NO_PROD = {'VACIO', 'APERTURA', 'EUCALIPTOS', 'VICIA-AVENA', 'MOSTAZA CALIENTE', 'ERRADICADO', 'TOTAL', 'NAN'}

        for r in rows:
            p_nom = r[0]
            c_cnt = r[1]
            h_val = float(r[2] or 0.0)
            p_val = float(r[3] or 0.0)
            tot_ha += h_val
            tot_camas += c_cnt
            if p_nom not in NO_PROD:
                prod_ha += h_val
                prod_camas += c_cnt
            print(f"  {p_nom:22}: {c_cnt:5} camas | {h_val:6.2f} ha | {p_val:5.2f}% de la finca")

        ocupacion_pct = round((prod_ha / tot_ha) * 100.0, 1) if tot_ha > 0 else 0.0
        print("-" * 80)
        print(f"  ÁREA CULTIVADA (PROD) : {prod_camas:5} camas | {prod_ha:6.2f} ha ({ocupacion_pct}% LLENO / OCUPACIÓN)")
        print(f"  ÁREA TOTAL FINCA      : {tot_camas:5} camas | {tot_ha:6.2f} ha (100.0%)")
        print("=" * 80)

if __name__ == "__main__":
    run_update()
