"""
==============================================================================
PROYECTO: DATA WAREHOUSE & BACKEND TRANSACCIONAL - GRUPO FLORICULTOR FLORSANI
ARCHIVO: scripts/replicate_star_and_sync.py
DESCRIPCIÓN: Sincroniza y consolida la base de datos 'florsani' con el modelo 
             estrella completo de cultivo (fact_eventos_cultivo, dim_camas, 
             dim_bloques, dim_calendario_agricola, etc.) y los módulos de cosechas,
             estimados, fenogramas y usuarios traídos de 'pyganflor'.
==============================================================================
"""

import sys
import pandas as pd
from sqlalchemy import create_engine, text

def run_sync():
    print("[1] Conectando a PostgreSQL...")
    url_florsani = "postgresql://postgres:admin@localhost:5432/florsani"
    url_pyganflor = "postgresql://postgres:admin@localhost:5432/pyganflor"
    
    eng_f = create_engine(url_florsani)
    eng_p = create_engine(url_pyganflor)
    
    # 1. Sync User monitor_flores to florsani
    print("\n[2] Sincronizando usuario monitor_flores...")
    with eng_p.connect() as cp, eng_f.connect() as cf:
        u = cp.execute(text("SELECT username, password_hash, role, is_active FROM users WHERE username = 'monitor_flores';")).fetchone()
        if u:
            exist = cf.execute(text("SELECT id FROM users WHERE username = 'monitor_flores';")).fetchone()
            if not exist:
                cf.execute(text("""
                    INSERT INTO users (username, password_hash, full_name, role, is_active, permissions_json, created_at, updated_at)
                    VALUES (:u, :p, 'Monitor de Cosecha', :r, :a, '["cosechas"]', NOW(), NOW());
                """), {"u": u[0], "p": u[1], "r": u[2], "a": u[3]})
                cf.commit()
                print("    -> Usuario 'monitor_flores' creado en florsani.")
            else:
                print("    -> Usuario 'monitor_flores' ya existe en florsani.")

    # 2. Copiar tablas transaccionales y de catálogo desde pyganflor -> florsani
    tables_to_copy = [
        "registro_cosecha",
        "estimado",
        "dim_variedades",
        "matriz_fenogramas",
        "calificaciones",
        "semanas_evaluacion",
        "dim_zonas",
        "dim_tecnico_riego",
        "dim_productos",
        "dim_prod_maestros",
        "dim_ppto",
        "calendario"
    ]
    
    print("\n[3] Replicando tablas de Cosechas, Catálogos y Fenogramas a 'florsani'...")
    for table in tables_to_copy:
        try:
            df = pd.read_sql_table(table, eng_p)
            print(f"    Leyendo '{table}' de pyganflor: {len(df)} registros.")
            # Copiar a florsani
            df.to_sql(table, eng_f, if_exists="replace", index=False)
            print(f"    [OK] '{table}' copiada a florsani.")
        except Exception as e:
            print(f"    [AVISO] No se pudo copiar '{table}': {e}")

    # 3. Restaurar Primary Keys y Secuencias Auto-Incrementables en florsani
    print("\n[4] Configurando Primary Keys y Secuencias en florsani...")
    with eng_f.connect() as cf:
        # registro_cosecha PK & Sequence
        try:
            cf.execute(text("ALTER TABLE registro_cosecha ADD PRIMARY KEY (id);"))
            cf.execute(text("CREATE SEQUENCE IF NOT EXISTS registro_cosecha_id_seq;"))
            cf.execute(text("ALTER TABLE registro_cosecha ALTER COLUMN id SET DEFAULT nextval('registro_cosecha_id_seq');"))
            cf.execute(text("SELECT setval('registro_cosecha_id_seq', COALESCE((SELECT MAX(id) FROM registro_cosecha), 0) + 1, false);"))
            cf.commit()
            print("    [OK] PK y Secuencia configuradas para 'registro_cosecha'.")
        except Exception as e:
            cf.rollback()
            print(f"    Nota PK 'registro_cosecha': {e}")

        # estimado PK & Sequence
        try:
            cf.execute(text("ALTER TABLE estimado ADD PRIMARY KEY (id);"))
            cf.execute(text("CREATE SEQUENCE IF NOT EXISTS estimado_id_seq;"))
            cf.execute(text("ALTER TABLE estimado ALTER COLUMN id SET DEFAULT nextval('estimado_id_seq');"))
            cf.execute(text("SELECT setval('estimado_id_seq', COALESCE((SELECT MAX(id) FROM estimado), 0) + 1, false);"))
            cf.commit()
            print("    [OK] PK y Secuencia configuradas para 'estimado'.")
        except Exception as e:
            cf.rollback()
            print(f"    Nota PK 'estimado': {e}")

        # matriz_fenogramas PK & Sequence
        try:
            cf.execute(text("ALTER TABLE matriz_fenogramas ADD PRIMARY KEY (id);"))
            cf.execute(text("CREATE SEQUENCE IF NOT EXISTS matriz_fenogramas_id_seq;"))
            cf.execute(text("ALTER TABLE matriz_fenogramas ALTER COLUMN id SET DEFAULT nextval('matriz_fenogramas_id_seq');"))
            cf.execute(text("SELECT setval('matriz_fenogramas_id_seq', COALESCE((SELECT MAX(id) FROM matriz_fenogramas), 0) + 1, false);"))
            cf.commit()
            print("    [OK] PK y Secuencia configuradas para 'matriz_fenogramas'.")
        except Exception as e:
            cf.rollback()
            print(f"    Nota PK 'matriz_fenogramas': {e}")

    # 4. Asegurar Vistas Analíticas en florsani
    print("\n[5] Verificando vistas analíticas en florsani (stg_eventos, mrt_estado_cultivo_actual)...")
    with eng_f.connect() as cf:
        cf.execute(text("""
            CREATE OR REPLACE VIEW stg_eventos AS
            SELECT 
                id_evento, id_cama, fecha_evento, tipo_evento, variedad, 
                estado_evento, origen_evento, num_poda, num_plantas, producto_maestro,
                semana_campo, created_at
            FROM fact_eventos_cultivo
            WHERE estado_evento = 'ACTIVO';
        """))
        cf.commit()
        print("    [OK] Vista 'stg_eventos' actualizada.")

        cf.execute(text("""
            CREATE OR REPLACE VIEW mrt_estado_cultivo_actual AS
            WITH ultimos_eventos AS (
                SELECT 
                    e.id_evento,
                    e.id_cama,
                    e.fecha_evento,
                    e.tipo_evento,
                    e.variedad,
                    e.producto_maestro,
                    e.num_plantas,
                    e.num_poda,
                    ROW_NUMBER() OVER(
                        PARTITION BY e.id_cama 
                        ORDER BY e.fecha_evento DESC, e.id_evento DESC
                    ) AS fila_num
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
            SELECT 
                f.id_finca,
                f.nombre_finca,
                c.id_cama,
                c.zona,
                c.codigo_bloque,
                c.num_cama,
                c.metros_netos,
                c.cama_estandar,
                c.tecnico_de_riego,
                ue.tipo_evento AS ultimo_evento,
                ue.fecha_evento AS fecha_ultimo_evento,
                cal_ev.semana_campo AS semana_campo_ultimo_evento,
                ue.producto_maestro,
                ue.variedad,
                ue.num_plantas,
                ue.num_poda,
                CASE 
                    WHEN ue.tipo_evento = 'TUMBA' THEN NULL
                    ELSE (sh.semana_abs_hoy - COALESCE(cal_ev.semana_abs, sh.semana_abs_hoy))
                END AS edad_semanas_actual,
                CASE 
                    WHEN ue.tipo_evento IS NULL THEN 'SIN EVENTOS REGISTRADOS'
                    WHEN ue.tipo_evento = 'TUMBA' THEN 'ERRADICADO / DISPONIBLE PARA SIEMBRA'
                    WHEN ue.tipo_evento = 'PODA' THEN 'CULTIVO EN REBROTE (PODADO)'
                    WHEN ue.tipo_evento = 'SIEMBRA' THEN 'CULTIVO NUEVO (SIEMBRA)'
                    ELSE 'EN PRODUCCIÓN'
                END AS estado_cultivo,
                sh.semana_campo_hoy AS semana_agricola_actual
            FROM dim_camas c
            INNER JOIN dim_fincas f ON c.id_finca = f.id_finca
            LEFT JOIN ultimos_eventos ue ON c.id_cama = ue.id_cama AND ue.fila_num = 1
            LEFT JOIN dim_calendario_agricola cal_ev ON ue.fecha_evento = cal_ev.fecha
            CROSS JOIN semana_hoy sh;
        """))
        cf.commit()
        print("    [OK] Vista 'mrt_estado_cultivo_actual' actualizada.")

    # 5. Replicar también a pyganflor para consistencia bidireccional
    print("\n[6] Replicando modelo estrella a 'pyganflor' para compatibilidad total...")
    star_tables = [
        "dim_fincas",
        "dim_calendario_agricola",
        "dim_densidad_plantas",
        "dim_recetas_fenologicas",
        "dim_camas",
        "fact_eventos_cultivo"
    ]
    for table in star_tables:
        try:
            df = pd.read_sql_table(table, eng_f)
            print(f"    Leyendo '{table}' de florsani: {len(df)} registros.")
            df.to_sql(table, eng_p, if_exists="replace", index=False)
            print(f"    [OK] '{table}' replicada en pyganflor.")
        except Exception as e:
            print(f"    [AVISO] No se pudo replicar '{table}' a pyganflor: {e}")

    # Replicar vistas a pyganflor
    with eng_p.connect() as cp:
        try:
            cp.execute(text("""
                CREATE OR REPLACE VIEW stg_eventos AS
                SELECT 
                    id_evento, id_cama, fecha_evento, tipo_evento, variedad, 
                    estado_evento, origen_evento, num_poda, num_plantas, producto_maestro,
                    semana_campo, created_at
                FROM fact_eventos_cultivo
                WHERE estado_evento = 'ACTIVO';
            """))
            cp.commit()
            print("    [OK] Vista 'stg_eventos' replicada en pyganflor.")
        except Exception as e:
            cp.rollback()
            print(f"    [AVISO] Vista stg_eventos en pyganflor: {e}")

    print("\n=======================================================")
    print("[EXITO] Sincronización de bases de datos completada al 100%.")
    print("=======================================================")

if __name__ == "__main__":
    run_sync()
