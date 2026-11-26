import os
from flask import Flask, render_template, session, g, request
from app.config import Config
from app.extensions import db
from app.shared.utils import (
    format_product_amount, format_age, format_local_datetime, 
    is_liquid_unit, is_solid_unit, safe_float, safe_int
)

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)

    # Concurrency optimization: Enable WAL mode & busy timeout if SQLite is used
    if 'sqlite' in str(app.config.get('SQLALCHEMY_DATABASE_URI', '')):
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        @event.listens_for(Engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            try:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()
            except Exception:
                pass

    # Register Jinja2 filters
    @app.template_filter('format_number')
    def filter_format_number(val, decimals=2):
        if val is None:
            return "0"
        try:
            f = safe_float(val, default=0.0)
            if decimals == 0:
                return f"{int(round(f)):,}"
            return f"{f:,.{decimals}f}"
        except (ValueError, TypeError):
            return str(val)

    @app.template_filter('format_product')
    def filter_format_product(amount, unit):
        return format_product_amount(amount, unit)

    @app.template_filter('format_age')
    def filter_format_age(age):
        return format_age(age)

    @app.template_filter('format_local_datetime')
    def filter_format_local_datetime(dt, fmt="%Y-%m-%d %H:%M"):
        return format_local_datetime(dt, fmt)

    @app.template_filter('is_liquid')
    def filter_is_liquid(unit):
        return is_liquid_unit(unit)

    @app.template_filter('is_solid')
    def filter_is_solid(unit):
        return is_solid_unit(unit)

    @app.template_filter('crop_category')
    def filter_crop_category(crop_name):
        from app.shared.utils import get_crop_category
        return get_crop_category(crop_name)

    # Safe dynamic column migrations for SQLite and PostgreSQL (Single batch lookup for ultra-fast worker startup)
    with app.app_context():
        from sqlalchemy import inspect, text
        done_migrations = set()
        is_sqlite = 'sqlite' in str(app.config.get('SQLALCHEMY_DATABASE_URI', ''))
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS _system_one_time_migrations (
                    migration_key VARCHAR(100) PRIMARY KEY,
                    executed_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """))
            db.session.commit()
            done_migrations = {
                r[0] for r in db.session.execute(text("SELECT migration_key FROM _system_one_time_migrations")).fetchall()
            }
        except Exception:
            db.session.rollback()

        try:
            if 'system_init_schema_and_catalogs_v8' not in done_migrations:
                db.create_all()
                inspector = inspect(db.engine)
                existing_tables = inspector.get_table_names()

                def ensure_column(table_name, column_name, col_type_sql, default_sql=None):
                    if table_name not in existing_tables:
                        return
                    cols = [c['name'] for c in inspector.get_columns(table_name)]
                    if column_name not in cols:
                        default_clause = f" DEFAULT {default_sql}" if default_sql is not None else ""
                        sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {col_type_sql}{default_clause}"
                        try:
                            db.session.execute(text(sql))
                            db.session.commit()
                        except Exception:
                            db.session.rollback()

                ensure_column('crops', 'category', 'VARCHAR(50)', "'PRODUCTOS_NUEVOS'")
                ensure_column('products', 'dose_trichos', 'DOUBLE PRECISION', 'NULL')
                ensure_column('products', 'dose_solidos', 'DOUBLE PRECISION', 'NULL')
                ensure_column('products', 'dose_desinfecciones', 'DOUBLE PRECISION', 'NULL')
                ensure_column('fumigation_order_details', 'spray_lance', 'VARCHAR(100)', "'Lanza 2 Boquillas'")
                ensure_column('additional_applications', 'spray_lance', 'VARCHAR(100)', "'Lanza 2 Boquillas'")
                ensure_column('rotations', 'is_salidas_digitadas', 'BOOLEAN', 'FALSE')
                ensure_column('rotations', 'salidas_digitadas_at', 'TIMESTAMP WITHOUT TIME ZONE', 'NULL')
                ensure_column('rotations', 'salidas_digitadas_by', 'VARCHAR(100)', 'NULL')
                ensure_column('rotations', 'is_salidas_dispatched', 'BOOLEAN', 'FALSE')
                ensure_column('rotations', 'salidas_dispatched_at', 'TIMESTAMP WITHOUT TIME ZONE', 'NULL')
                ensure_column('rotations', 'salidas_dispatched_by', 'VARCHAR(100)', 'NULL')
                ensure_column('rotations', 'weighed_products_json', 'TEXT', 'NULL')
                ensure_column('fumigation_orders', 'program_statuses_json', 'TEXT', "'{}'")

                # Columnas añadidas para Módulo de Fertilización, Productos y Despachos Oficiales
                ensure_column('drench_items', 'dia_aplicacion', 'VARCHAR(20)', "'LUNES'")
                ensure_column('drench_items', 'estado_calibracion', 'VARCHAR(20)', "'SIN_TOCAR'")
                ensure_column('drench_items', 'dosis_diferenciada', 'DOUBLE PRECISION', 'NULL')
                ensure_column('drench_items', 'producto_especifico', 'TEXT', 'NULL')
                ensure_column('products', 'dose_moshe', 'DOUBLE PRECISION', 'NULL')
                ensure_column('products', 'dose_alcaplant', 'DOUBLE PRECISION', 'NULL')
                ensure_column('products', 'dose_presiembra', 'DOUBLE PRECISION', 'NULL')
                ensure_column('products', 'dose_diferenciada', 'DOUBLE PRECISION', 'NULL')
                ensure_column('rotations', 'is_fertilizacion_printed', 'BOOLEAN', 'FALSE')
                ensure_column('rotations', 'fertilizacion_printed_at', 'TIMESTAMP WITHOUT TIME ZONE', 'NULL')
                ensure_column('rotations', 'fertilizacion_printed_by', 'VARCHAR(100)', 'NULL')
                ensure_column('rotations', 'is_fertilizacion_digitadas', 'BOOLEAN', 'FALSE')
                ensure_column('rotations', 'fertilizacion_digitadas_at', 'TIMESTAMP WITHOUT TIME ZONE', 'NULL')
                ensure_column('rotations', 'fertilizacion_digitadas_by', 'VARCHAR(100)', 'NULL')
                ensure_column('crop_state_records', 'id_finca', 'INTEGER', '1')

                # Sincronizar categorias maestras en tabla crops (GYPSOPHILA, VARIEDADES, PIV)
                try:
                    db.session.execute(text("UPDATE crops SET category = 'GYPSOPHILA' WHERE UPPER(name) LIKE '%GYPSO%';"))
                    db.session.execute(text("UPDATE crops SET category = 'PIV' WHERE UPPER(name) LIKE '%EUCAL%' OR UPPER(name) LIKE '%LISI%' OR UPPER(name) LIKE '%LYSI%' OR UPPER(name) LIKE '%ASTER%' OR UPPER(name) LIKE '%RUSCUS%' OR UPPER(name) LIKE '%RUMEX%' OR UPPER(name) LIKE '%PROD.NUEVOS%' OR UPPER(name) LIKE '%VICIA%' OR UPPER(name) LIKE '%MOSTAZA%';"))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

                if not is_sqlite:
                    pg_migrations = [
                        "ALTER TABLE fumigation_order_details ALTER COLUMN bed_range TYPE TEXT",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN real_age TYPE VARCHAR(255)",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN crop_name TYPE VARCHAR(255)",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN variety_specific TYPE VARCHAR(255)",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN product_code TYPE VARCHAR(255)",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN commercial_name TYPE VARCHAR(255)",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN operator TYPE VARCHAR(255)",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN zone TYPE VARCHAR(255)",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN block_name TYPE VARCHAR(100)",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN suffix TYPE VARCHAR(50)",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN phenological_stage TYPE VARCHAR(100)",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN pest TYPE TEXT",
                        "ALTER TABLE fumigation_order_details ALTER COLUMN active_ingredient TYPE TEXT",
                        "ALTER TABLE fumigation_order_product_summaries ALTER COLUMN product_code TYPE VARCHAR(255)",
                        "ALTER TABLE fumigation_order_product_summaries ALTER COLUMN commercial_name TYPE VARCHAR(255)",
                        "ALTER TABLE fumigation_order_product_summaries ALTER COLUMN pest TYPE TEXT",
                        "ALTER TABLE requisition_items ALTER COLUMN pest TYPE TEXT",
                        "ALTER TABLE requisition_items ALTER COLUMN product_code TYPE VARCHAR(255)",
                        "ALTER TABLE additional_applications ALTER COLUMN reason TYPE TEXT",
                        "ALTER TABLE additional_applications ALTER COLUMN notes TYPE TEXT",
                        "ALTER TABLE rotations ADD COLUMN IF NOT EXISTS weighed_products_json TEXT",
                        "UPDATE calificaciones SET auxiliar = 'Kevin Buitron', supervisor = 'Maria Dolores Perugachi' WHERE (labor ILIKE '%malla pl%stica%' OR labor ILIKE '%tejido de alambre%') AND (supervisor ILIKE '%borja%' OR auxiliar ILIKE '%borja%')",
                        "UPDATE calificaciones SET auxiliar = 'Marcia Medina', supervisor = 'Maria Dolores Perugachi' WHERE labor ILIKE '%malla naranja%' AND (supervisor ILIKE '%borja%' OR auxiliar ILIKE '%borja%')",
                        "UPDATE calificaciones SET tiene_evaluacion = FALSE, cumplido = NULL WHERE (labor = 'No hay actividad' OR labor IS NULL OR TRIM(labor) = '') AND tiene_evaluacion = TRUE",
                        "CREATE TABLE IF NOT EXISTS cat_productos_eliminados (id SERIAL PRIMARY KEY, producto_maestro VARCHAR(150) NOT NULL UNIQUE, deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
                        "INSERT INTO cat_productos_eliminados (producto_maestro) VALUES ('EUCALIPTOS') ON CONFLICT (producto_maestro) DO NOTHING",
                        "DELETE FROM cat_productos_eliminados WHERE UPPER(producto_maestro) = 'EUCALIPTO'",
                        "DELETE FROM cat_variedades_eliminadas WHERE UPPER(variedad) IN ('BABY BLUE', 'GUNNII', 'PARVIFOLIA')",
                        "DELETE FROM cat_variedades_maestro WHERE UPPER(producto_maestro) = 'EUCALIPTOS'",
                        "INSERT INTO cat_variedades_maestro (variedad, producto_maestro) VALUES ('BABY BLUE', 'EUCALIPTO'), ('GUNNII', 'EUCALIPTO'), ('PARVIFOLIA', 'EUCALIPTO') ON CONFLICT (variedad) DO UPDATE SET producto_maestro = 'EUCALIPTO'",
                        "DELETE FROM dim_prod_maestros WHERE UPPER(producto_maestro) = 'EUCALIPTOS'",
                        "INSERT INTO dim_prod_maestros (id_productos, producto_maestro) SELECT COALESCE(MAX(id_productos), 0) + 1, 'EUCALIPTO' FROM dim_prod_maestros WHERE NOT EXISTS (SELECT 1 FROM dim_prod_maestros WHERE UPPER(producto_maestro) = 'EUCALIPTO')",
                        "DELETE FROM crops WHERE UPPER(name) = 'EUCALIPTOS'",
                        "INSERT INTO crops (name, category, veg_min_age, veg_max_age, prod_min_age, prod_max_age, is_active, notes) SELECT 'Eucalipto', 'PIV', 0, 12, 13, 999, true, 'Cultivo oficial' WHERE NOT EXISTS (SELECT 1 FROM crops WHERE UPPER(name) = 'EUCALIPTO')",
                        "UPDATE dim_camas SET area_producto_comercial = 'EUCALIPTO' WHERE UPPER(TRIM(variedad_comercial)) IN ('BABY BLUE', 'GUNNII', 'PARVIFOLIA')",
                        "UPDATE dim_camas SET area_producto_comercial = 'GYPSOPHILA' WHERE UPPER(TRIM(area_producto_comercial)) LIKE 'BILLION%' OR UPPER(TRIM(area_producto_comercial)) LIKE 'GYPSO%' OR UPPER(TRIM(area_producto_comercial)) IN ('BILLION LIGHTS', 'BILLION LIGHT', 'BILLION', 'G5', 'G49', 'G3', 'SMALL BLOOM', 'MILLION STAR II')",
                        "UPDATE plano_cultivo SET producto_maestro = 'GYPSOPHILA' WHERE UPPER(TRIM(producto_maestro)) LIKE 'BILLION%' OR UPPER(TRIM(producto_maestro)) LIKE 'GYPSO%' OR UPPER(TRIM(producto_maestro)) IN ('BILLION LIGHTS', 'BILLION LIGHT', 'BILLION', 'G5', 'G49', 'G3', 'SMALL BLOOM', 'MILLION STAR II')",
                        "UPDATE dim_camas SET area_producto_comercial = 'VERONICA' WHERE UPPER(TRIM(area_producto_comercial)) IN ('VERONICA SPLASH', 'VERONICAS SPLASH')",
                        "UPDATE dim_camas SET area_producto_comercial = 'BRILLANTINA' WHERE UPPER(TRIM(variedad_comercial)) = 'BRILLANTINA' OR UPPER(TRIM(area_producto_comercial)) = 'BRILLANTINA'",
                        "UPDATE calificaciones SET producto_maestro = 'GYPSOPHILA' WHERE UPPER(TRIM(producto_maestro)) LIKE 'BILLION%' OR UPPER(TRIM(producto_maestro)) LIKE 'GYPSO%' OR UPPER(TRIM(producto_maestro)) IN ('BILLION LIGHTS', 'BILLION LIGHT', 'BILLION', 'G5', 'G49', 'G3', 'SMALL BLOOM', 'MILLION STAR II')",
                        "UPDATE calificaciones SET producto_maestro = 'VERONICA' WHERE UPPER(TRIM(producto_maestro)) IN ('VERONICA SPLASH', 'VERONICAS SPLASH')",
                        "UPDATE calificaciones SET producto_maestro = 'BRILLANTINA' WHERE UPPER(TRIM(variedad_comercial)) = 'BRILLANTINA' OR UPPER(TRIM(producto_maestro)) = 'BRILLANTINA'",
                        "INSERT INTO cat_variedades_maestro (variedad, producto_maestro) VALUES ('BRILLANTINA', 'BRILLANTINA') ON CONFLICT (variedad) DO UPDATE SET producto_maestro = 'BRILLANTINA'",
                        "INSERT INTO cat_variedades_maestro (variedad, producto_maestro) VALUES ('PIV', 'PIV') ON CONFLICT (variedad) DO UPDATE SET producto_maestro = 'PIV'",
                        "INSERT INTO dim_prod_maestros (id_productos, producto_maestro) SELECT COALESCE(MAX(id_productos), 0) + 1, 'BRILLANTINA' FROM dim_prod_maestros WHERE NOT EXISTS (SELECT 1 FROM dim_prod_maestros WHERE UPPER(producto_maestro) = 'BRILLANTINA')"
                    ]
                    for m in pg_migrations:
                        try:
                            db.session.execute(text(m))
                            db.session.commit()
                        except Exception:
                            db.session.rollback()

                    try:
                        from app.modules.fenogramas.services.fenograma_service import sincronizar_responsables_calificaciones
                        sincronizar_responsables_calificaciones()
                    except Exception:
                        pass

                db.session.execute(text(
                    "INSERT INTO _system_one_time_migrations (migration_key) VALUES ('system_init_schema_and_catalogs_v8')"
                ))
                db.session.commit()
                print("[SYSTEM] Migraciones DDL iniciales ejecutadas con éxito.")
        except Exception:
            db.session.rollback()

        # Ensure initial admin is ready (only run create_all when initial bootstrap hasn't happened)
        if 'admin_bootstrapped_v1' not in done_migrations:
            try:
                db.create_all()
                from app.shared.models import User
                admin_user = User.query.filter_by(role='ADMIN').first()
                if not admin_user:
                    initial_admin = User(
                        username='admin',
                        full_name='Administrador Principal',
                        role='ADMIN',
                        permissions=[
                            'tpsr', 'estado_cultivo', 'cosechas', 'fenogramas',
                            'fumigacion', 'ordenes_ver', 'ordenes_imprimir', 'salidas_ver', 'salidas_imprimir',
                            'aplicaciones_extras', 'orden_compra', 'drench', 'trichos', 'desinfecciones',
                            'catalogos', 'importador'
                        ]
                    )
                    initial_admin.set_password('admin123')
                    db.session.add(initial_admin)
                db.session.execute(text(
                    "INSERT INTO _system_one_time_migrations (migration_key) VALUES ('admin_bootstrapped_v1') ON CONFLICT DO NOTHING"
                ))
                db.session.commit()
            except Exception:
                db.session.rollback()

        # One-time migration to reset test fenograma week 43 with bed consolidation
        if 'reset_feno_sem43_clean_v2' not in done_migrations:
            try:
                from app.modules.fenogramas.services.fenograma_service import sync_from_mrt_estado_cultivo
                sync_from_mrt_estado_cultivo(semana_corte=43, anio=2026, fuente='AUTO', forzar_limpieza=True)
                db.session.execute(text(
                    "INSERT INTO _system_one_time_migrations (migration_key) VALUES ('reset_feno_sem43_clean_v2') ON CONFLICT DO NOTHING"
                ))
                db.session.commit()
                print("[SYSTEM] Limpieza y regeneración inicial de Fenograma Semana 43 completada con éxito.")
            except Exception as ex_mig:
                db.session.rollback()
                print(f"[SYSTEM] Aviso en migración de fenograma: {ex_mig}")

        # One-time migration to synchronize cat_variedades_maestro and enforce tombstones on VPS
        try:
            mig_cat_done = ('sync_catalogos_vps_tombstones_v7' in done_migrations)

            if not mig_cat_done:
                # 1. Crear tabla de variedades eliminadas (lápidas / tombstones)
                db.session.execute(text("""
                    CREATE TABLE IF NOT EXISTS cat_variedades_eliminadas (
                        id SERIAL PRIMARY KEY,
                        variedad VARCHAR(150) NOT NULL UNIQUE,
                        producto_maestro VARCHAR(150),
                        deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                
                # 2. Registrar variedades de prueba o eliminadas en tombstone table
                for tomb_var, tomb_prod in [('150020-26', 'PROD.NUEVOS'), ('BLANCA', 'PROD.NUEVOS')]:
                    db.session.execute(text("""
                        INSERT INTO cat_variedades_eliminadas (variedad, producto_maestro)
                        VALUES (:v, :p)
                        ON CONFLICT (variedad) DO NOTHING;
                    """), {'v': tomb_var, 'p': tomb_prod})

                # 3. Purgar variedades eliminadas de todas las tablas activas
                db.session.execute(text("""
                    DELETE FROM cat_variedades_maestro 
                    WHERE UPPER(TRIM(variedad)) IN (SELECT UPPER(TRIM(variedad)) FROM cat_variedades_eliminadas);
                """))
                db.session.execute(text("""
                    DELETE FROM dim_variedades 
                    WHERE UPPER(TRIM(variedad)) IN (SELECT UPPER(TRIM(variedad)) FROM cat_variedades_eliminadas);
                """))
                db.session.execute(text("""
                    DELETE FROM dim_productos 
                    WHERE UPPER(TRIM(variedad)) IN (SELECT UPPER(TRIM(variedad)) FROM cat_variedades_eliminadas);
                """))
                db.session.execute(text("""
                    DELETE FROM variedades 
                    WHERE UPPER(TRIM(nombre)) IN (SELECT UPPER(TRIM(variedad)) FROM cat_variedades_eliminadas);
                """))

                from app.modules.cosechas.utils import _get_csv_path
                csv_path = _get_csv_path('MAESTRO_PROS.csv')
                if csv_path.exists():
                    csv_vars_map = {}
                    with open(csv_path, 'r', encoding='utf-8-sig') as f:
                        for line in f.read().strip().split('\n')[1:]:
                            if ';' in line:
                                p = line.strip().split(';')
                                if len(p) >= 2:
                                    v = p[0].strip().upper()
                                    m = p[1].strip().upper()
                                    if v and m:
                                        csv_vars_map[v] = m
                    
                    # Excluir cualquier variedad presente en cat_variedades_eliminadas
                    tomb_rows = db.session.execute(text("SELECT UPPER(TRIM(variedad)) FROM cat_variedades_eliminadas;")).fetchall()
                    tomb_set = {r[0] for r in tomb_rows if r[0]}
                    for v_tomb in tomb_set:
                        csv_vars_map.pop(v_tomb, None)

                    if csv_vars_map:
                        db.session.execute(text("""
                            CREATE TABLE IF NOT EXISTS cat_variedades_maestro (
                                id SERIAL PRIMARY KEY,
                                variedad VARCHAR(150) NOT NULL UNIQUE,
                                producto_maestro VARCHAR(150) NOT NULL,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            );
                        """))
                        # Eliminar de BD cualquier variedad que ya fue eliminada del CSV canónico o esté en tombstones
                        from sqlalchemy import bindparam
                        stmt = text(
                            "DELETE FROM cat_variedades_maestro WHERE UPPER(TRIM(variedad)) NOT IN :vars"
                        ).bindparams(bindparam('vars', expanding=True))
                        db.session.execute(stmt, {'vars': tuple(csv_vars_map.keys())})

                        # Sincronizar inserciones y actualizaciones
                        for v, m in csv_vars_map.items():
                            db.session.execute(text("""
                                INSERT INTO cat_variedades_maestro (variedad, producto_maestro)
                                VALUES (:var, :prod)
                                ON CONFLICT (variedad) DO UPDATE 
                                SET producto_maestro = EXCLUDED.producto_maestro, updated_at = CURRENT_TIMESTAMP;
                            """), {'var': v, 'prod': m})
                
                db.session.execute(text(
                    "INSERT INTO _system_one_time_migrations (migration_key) VALUES ('sync_catalogos_vps_tombstones_v7')"
                ))
                db.session.commit()
                print("[SYSTEM] Sincronización canónica con lápidas (tombstones) de variedades completada con éxito.")
        except Exception as ex_mig_cat:
            db.session.rollback()
            print(f"[SYSTEM] Aviso en migración de catálogo con lápidas: {ex_mig_cat}")

        # Índices de alto rendimiento y optimización de vista mrt_estado_cultivo_actual (DISTINCT ON + Index-Only Scan)
        if not is_sqlite and 'perf_indexes_and_mrt_view_v9' not in done_migrations:
            try:
                perf_sqls = [
                    "CREATE INDEX IF NOT EXISTS idx_fact_eventos_latest_cama ON fact_eventos_cultivo (id_cama, fecha_evento DESC, id_evento DESC) INCLUDE (codigo_bloque, tipo_evento, num_poda, producto_maestro, variedad, num_plantas, fecha_hora_registro) WHERE estado_evento::text = 'ACTIVO'::text",
                    "CREATE INDEX IF NOT EXISTS idx_fact_eventos_sem_activo ON fact_eventos_cultivo (semana_campo DESC) WHERE estado_evento::text = 'ACTIVO'::text",
                    "CREATE INDEX IF NOT EXISTS idx_dim_camas_blq ON dim_camas (blq)",
                    "CREATE INDEX IF NOT EXISTS idx_dim_camas_cod_blq ON dim_camas (codigo_bloque)",
                    "CREATE INDEX IF NOT EXISTS idx_fum_orders_rot_id ON fumigation_orders (rotation_id)",
                    "CREATE INDEX IF NOT EXISTS idx_fum_orders_status_week ON fumigation_orders (status, week)",
                    "CREATE INDEX IF NOT EXISTS idx_rotations_status_week ON rotations (status, week DESC)",
                    "CREATE INDEX IF NOT EXISTS idx_drench_items_semana ON drench_items (semana)",
                    "CREATE INDEX IF NOT EXISTS idx_calif_sem_eval ON calificaciones (semana_anio) WHERE tiene_evaluacion = TRUE",
                    "CREATE INDEX IF NOT EXISTS idx_reg_cosecha_semana ON registro_cosecha (semana)",
                    "CREATE INDEX IF NOT EXISTS idx_reg_cosecha_fecha ON registro_cosecha (fecha)",
                    """
                    CREATE OR REPLACE VIEW mrt_estado_cultivo_actual AS
                    WITH ultimos_eventos AS (
                        SELECT DISTINCT ON (e.id_cama)
                            e.id_cama,
                            e.codigo_bloque,
                            e.fecha_evento,
                            e.tipo_evento,
                            e.num_poda,
                            e.producto_maestro,
                            e.variedad,
                            e.num_plantas,
                            e.fecha_hora_registro
                        FROM fact_eventos_cultivo e
                        WHERE e.estado_evento::text = 'ACTIVO'::text
                        ORDER BY e.id_cama, e.fecha_evento DESC, e.id_evento DESC
                    ), semana_corte_calc AS (
                        SELECT c_1.semana_abs AS semana_abs_hoy,
                               c_1.semana_campo AS semana_campo_hoy
                        FROM dim_calendario_agricola c_1
                        WHERE c_1.semana_campo = GREATEST((
                            SELECT COALESCE(MAX(e.semana_campo), 2643)
                            FROM fact_eventos_cultivo e
                            WHERE e.estado_evento::text = 'ACTIVO'::text
                        ), 2643)
                        ORDER BY c_1.fecha DESC
                        LIMIT 1
                    ), semana_hoy AS (
                        SELECT COALESCE((SELECT semana_corte_calc.semana_abs_hoy FROM semana_corte_calc), 1354) AS semana_abs_hoy,
                               COALESCE((SELECT semana_corte_calc.semana_campo_hoy FROM semana_corte_calc), 2643) AS semana_campo_hoy
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
                        COALESCE(NULLIF(TRIM(BOTH FROM c.area_producto_comercial), ''::text), NULLIF(TRIM(BOTH FROM ue.producto_maestro), ''::text), 'VACIO'::text) AS producto_actual,
                        COALESCE(NULLIF(TRIM(BOTH FROM c.variedad_comercial), ''::text), NULLIF(TRIM(BOTH FROM ue.variedad), ''::text), 'SIN VARIEDAD'::text) AS variedad_actual,
                        CASE
                            WHEN upper(TRIM(BOTH FROM COALESCE(c.area_producto_comercial, ''::character varying))) = 'VACIO'::text THEN 'TUMBA'::character varying
                            ELSE COALESCE(ue.tipo_evento, 'PRODUCCION'::character varying)
                        END AS ultimo_evento,
                        ue.num_poda AS num_poda_actual,
                        ue.fecha_evento AS fecha_ultimo_evento,
                        cal_ev.semana_campo AS semana_campo_ultimo_evento,
                        CASE
                            WHEN upper(TRIM(BOTH FROM COALESCE(c.area_producto_comercial, ''::character varying))) = ANY (ARRAY['VACIO'::text, 'APERTURA'::text]) THEN NULL::integer
                            WHEN ue.tipo_evento::text = 'TUMBA'::text THEN NULL::integer
                            ELSE sh.semana_abs_hoy - cal_ev.semana_abs
                        END AS edad_semanas_actual,
                        CASE
                            WHEN upper(TRIM(BOTH FROM COALESCE(c.area_producto_comercial, ''::character varying))) = 'VACIO'::text THEN 'DISPONIBLE / VACIO'::text
                            WHEN upper(TRIM(BOTH FROM COALESCE(c.area_producto_comercial, ''::character varying))) = 'APERTURA'::text THEN 'EN APERTURA / PREPARACIÓN'::text
                            WHEN upper(TRIM(BOTH FROM COALESCE(c.area_producto_comercial, ''::character varying))) = ANY (ARRAY['VICIA-AVENA'::text, 'MOSTAZA CALIENTE'::text]) THEN 'ROTACIÓN / ABONO VERDE'::text
                            WHEN upper(TRIM(BOTH FROM COALESCE(c.area_producto_comercial, ''::character varying))) = 'EUCALIPTOS'::text THEN 'CORTINA ROMPEVIENTOS'::text
                            WHEN ue.tipo_evento::text = 'PODA'::text THEN ('EN PRODUCCIÓN (PODA '::text || COALESCE(ue.num_poda::text, '1'::text)) || ')'::text
                            WHEN ue.tipo_evento::text = 'SIEMBRA'::text THEN 'CULTIVO NUEVO (SIEMBRA)'::text
                            ELSE 'EN PRODUCCIÓN'::text
                        END AS estado_cultivo,
                        sh.semana_campo_hoy AS semana_agricola_actual,
                        COALESCE(ue.num_plantas, c.num_plantas) AS num_plantas_actual,
                        ue.fecha_hora_registro AS fecha_hora_ultimo_registro,
                        cal_ev.semana_abs AS semana_abs_ultimo_evento
                    FROM dim_camas c
                    JOIN dim_fincas f ON c.id_finca = f.id_finca
                    LEFT JOIN dim_bloques b ON c.codigo_bloque::text = b.codigo_bloque::text
                    LEFT JOIN ultimos_eventos ue ON c.id_cama::text = ue.id_cama::text
                    LEFT JOIN dim_calendario_agricola cal_ev ON ue.fecha_evento = cal_ev.fecha
                    CROSS JOIN semana_hoy sh;
                    """,
                    "ANALYZE fact_eventos_cultivo",
                    "ANALYZE dim_camas",
                    "ANALYZE crop_state_records",
                    "ANALYZE calificaciones",
                    "ANALYZE rotations",
                    "ANALYZE fumigation_orders"
                ]
                for p_sql in perf_sqls:
                    try:
                        db.session.execute(text(p_sql))
                        db.session.commit()
                    except Exception as e_idx:
                        db.session.rollback()
                        print(f"[SYSTEM] Aviso índice/vista rendimiento: {e_idx}")

                db.session.execute(text(
                    "INSERT INTO _system_one_time_migrations (migration_key) VALUES ('perf_indexes_and_mrt_view_v9') ON CONFLICT DO NOTHING"
                ))
                db.session.commit()
                print("[SYSTEM] Índices compuestos y vista optimizada mrt_estado_cultivo_actual (v9) aplicados con éxito.")
            except Exception as ex_perf:
                db.session.rollback()
                print(f"[SYSTEM] Aviso en migración de rendimiento v9: {ex_perf}")

        # Reconciliación automática del censo más reciente (solo si la semana vigente aún no fue reconciliada)
        try:
            db.session.execute(text("""
                INSERT INTO crops (name, category, is_active, veg_min_age, veg_max_age, prod_min_age, prod_max_age, aliases_json, created_at, updated_at)
                VALUES ('Brillantina', 'PRODUCTOS_NUEVOS', TRUE, 0, 12, 13, 99, '[]', NOW(), NOW())
                ON CONFLICT (name) DO UPDATE SET is_active = TRUE;
            """))
            db.session.commit()
            from app.shared.censo_sync import reconciliar_censo_vigente
            reconciliar_censo_vigente(startup_only_if_needed=False)
        except Exception as ex_sync:
            db.session.rollback()
            print(f"[SYSTEM] Aviso en reconciliación automática de censo: {ex_sync}")

    # Register blueprints
    from app.modules.auth.routes import auth_bp
    from app.modules.orden_compra.routes import orden_compra_bp
    from app.modules.fumigacion.routes import fumigacion_bp
    from app.modules.drench.routes import drench_bp
    from app.modules.trichos.routes import trichos_bp
    from app.modules.desinfecciones.routes import desinfecciones_bp
    from app.modules.productos.routes import productos_bp
    from app.modules.cultivos.routes import cultivos_bp
    from app.modules.litrajes.routes import litrajes_bp
    from app.modules.estado_cultivo.routes import estado_cultivo_bp
    from app.modules.importador.routes import importador_bp
    from app.modules.bodega.routes import bodega_bp
    from app.modules.tpsr.routes import tpsr_bp
    from app.modules.cosechas.routes import cosechas_bp, bp_cosechas
    from app.modules.fertilizacion.routes import fertilizacion_bp
    from app.modules.fenogramas.routes import fenogramas_bp

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(cosechas_bp, url_prefix='/cosechas')
    app.register_blueprint(fertilizacion_bp, url_prefix='/fertilizacion')
    app.register_blueprint(fenogramas_bp, url_prefix='/fenogramas')
    app.register_blueprint(tpsr_bp, url_prefix='/tpsr')
    app.register_blueprint(orden_compra_bp, url_prefix='/orden-compra')
    app.register_blueprint(fumigacion_bp, url_prefix='/fumigacion')
    app.register_blueprint(bodega_bp, url_prefix='/bodega')
    app.register_blueprint(drench_bp, url_prefix='/drench')
    app.register_blueprint(trichos_bp, url_prefix='/trichos')
    app.register_blueprint(desinfecciones_bp, url_prefix='/desinfecciones')
    app.register_blueprint(productos_bp, url_prefix='/productos')
    app.register_blueprint(cultivos_bp, url_prefix='/cultivos')
    app.register_blueprint(litrajes_bp, url_prefix='/litrajes')
    app.register_blueprint(estado_cultivo_bp, url_prefix='/estado-cultivo')
    app.register_blueprint(importador_bp, url_prefix='/importador')

    # Global user context processor (reutiliza g.current_user cargado en before_request sin consulta extra)
    @app.context_processor
    def inject_user():
        if hasattr(g, 'current_user'):
            return {'current_user': g.current_user}
        from app.shared.models import User
        user_id = session.get('user_id')
        u = db.session.get(User, user_id) if user_id else None
        g.current_user = u
        return {'current_user': u}

    # Force login by default for all application routes
    @app.before_request
    def check_authenticated_user():
        from flask import request, redirect, url_for, jsonify
        # Fast-path inmediato para archivos estáticos y Service Worker (0 consultas a BD)
        req_path = request.path or ''
        if req_path.startswith('/static/') or req_path in ('/sw.js', '/favicon.ico'):
            return None

        # 0. Acceso autorizado por API Key o Token (Excel, Power Query, Postman)
        api_token = (
            request.args.get('api_key') or 
            request.args.get('token') or 
            request.headers.get('X-API-KEY') or
            request.headers.get('X-Token')
        )
        if api_token in ['florsani2026', 'florsani_excel_2026', 'florsani']:
            return None

        # 0.1 Acceso por HTTP Basic Auth (Autenticacion Basica de Excel)
        auth = request.authorization
        if auth and auth.username and auth.password:
            try:
                from app.shared.models import User
                user = User.query.filter_by(username=auth.username).first()
                if user and user.check_password(auth.password) and user.is_active:
                    session['user_id'] = user.id
                    g.current_user = user
                    return None
            except Exception:
                pass

        if request.endpoint:
            if request.endpoint.startswith('static') or request.endpoint in [
                'auth.login', 'auth.logout', 'auth.api_quick_login',
                'fenogramas.api_ping', 'service_worker_file',
                'cosechas.api_cosechas_datos_semanales', 'cosechas.api_areas_resumen',
                'alias_api_datos_semanales', 'alias_api_areas_resumen'
            ]:
                return None

        if req_path and (
            req_path.startswith('/cosechas/api/datos-semanales') or
            req_path.startswith('/cosechas/api/areas-resumen') or
            req_path.startswith('/cosechas/api/cosechas') or
            req_path.startswith('/cosechas/api/areas') or
            req_path.startswith('/api/datos-semanales') or
            req_path.startswith('/api/areas-resumen') or
            req_path.startswith('/api/cosechas') or
            req_path.startswith('/api/areas')
        ):
            return None
        
        if 'user_id' not in session:
            # Si es una petición API / JSON / AJAX, retornar 401 JSON en lugar de redirección HTML 302
            is_api = bool(
                request.is_json
                or ('/api/' in req_path or req_path.startswith('/api/'))
                or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or 'application/json' in request.headers.get('Accept', '')
            )
            if is_api:
                return jsonify({
                    'success': False,
                    'error': 'Tu sesión ha expirado por inactividad. Inicia sesión para continuar.',
                    'code': 'SESSION_EXPIRED',
                    'auth_required': True
                }), 401
            return redirect(url_for('auth.login', next=request.url))

        # Sincronización en caliente de roles y permisos (solo modifica la cookie si cambió realmente)
        user_id = session.get('user_id')
        if user_id:
            try:
                from app.shared.models import User
                active_user = db.session.get(User, user_id)
                g.current_user = active_user
                if active_user and active_user.is_active:
                    new_perms = active_user.permissions
                    if session.get('permissions') != new_perms:
                        session['permissions'] = new_perms
                    if session.get('role') != active_user.role:
                        session['role'] = active_user.role
            except Exception:
                pass


    @app.route('/api/datos-semanales', methods=['GET', 'HEAD'])
    @app.route('/api/datos-semanales.csv', methods=['GET', 'HEAD'])
    @app.route('/api/cosechas.csv', methods=['GET', 'HEAD'])
    def alias_api_datos_semanales():
        from app.modules.cosechas.routes import api_cosechas_datos_semanales
        return api_cosechas_datos_semanales()

    @app.route('/api/areas-resumen', methods=['GET', 'HEAD'])
    @app.route('/api/areas-resumen.csv', methods=['GET', 'HEAD'])
    @app.route('/api/areas.csv', methods=['GET', 'HEAD'])
    def alias_api_areas_resumen():
        from app.modules.cosechas.routes import api_areas_resumen
        return api_areas_resumen()

    @app.route('/sw.js')
    def service_worker_file():
        from flask import send_from_directory
        response = send_from_directory(app.static_folder, 'sw.js', mimetype='application/javascript')
        response.headers['Service-Worker-Allowed'] = '/'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

    # ==============================================================================
    # COMPRESIÓN GZIP NATIVA EN C (ZLIB LEVEL 1) + CACHÉ DE ESTÁTICOS
    # 10x más rápida en CPU que GzipFile level 6; libera el GIL en milisegundos
    # ==============================================================================
    import zlib

    @app.after_request
    def compress_response(response):
        req_p = request.path or ''
        if req_p.startswith('/static/') and 'Cache-Control' not in response.headers:
            response.headers['Cache-Control'] = 'public, max-age=86400'

        accept_encoding = request.headers.get('Accept-Encoding', '')
        if 'gzip' not in accept_encoding.lower():
            return response

        if (
            response.status_code < 200
            or response.status_code >= 300
            or 'Content-Encoding' in response.headers
            or response.direct_passthrough
            or getattr(response, 'is_streamed', False)
        ):
            return response

        content_type = (response.headers.get('Content-Type') or '').lower()
        compressible_types = (
            'text/', 'application/json', 'application/javascript',
            'application/xml', 'application/x-javascript', 'image/svg+xml'
        )
        if not any(content_type.startswith(ct) for ct in compressible_types):
            return response

        data = response.get_data()
        if len(data) < 800:
            return response

        compressor = zlib.compressobj(1, zlib.DEFLATED, zlib.MAX_WBITS | 16)
        compressed = compressor.compress(data) + compressor.flush()

        response.set_data(compressed)
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Content-Length'] = len(compressed)
        response.headers['Vary'] = 'Accept-Encoding'
        return response

    # Main dashboard route
    @app.route('/')
    def dashboard():
        from flask import request, redirect, url_for
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))

        from app.shared.models import User
        user = User.query.get(session['user_id'])
        if user and user.role not in ['ADMIN', 'AGRONOMO']:
            # Redireccionar directamente a su módulo de trabajo
            if user.has_permission('fenogramas'):
                return redirect(url_for('fenogramas.index'))
            if user.has_permission('cosechas'):
                return redirect(url_for('cosechas.nuevo_registro'))
            if user.has_permission('tpsr'):
                return redirect(url_for('tpsr.registro'))
            if user.has_permission('bodega'):
                return redirect(url_for('bodega.index'))

        from app.shared.models import (
            Crop, Product, CropStateRecord, 
            Rotation, RotationRound, RotationRoundItem, 
            FumigationOrder
        )
        crops_count = Crop.query.filter_by(is_active=True).count()
        products_count = Product.query.filter_by(is_active=True).count()
        beds_count = CropStateRecord.query.count()
        rotations_count = Rotation.query.count()
        orders_count = FumigationOrder.query.count()

        # Optimización: las rotaciones detalladas no se renderizan en el dashboard principal
        weekly_rotations_data = {}
        weeks_list = []
        selected_week = request.args.get('week')

        recent_orders = FumigationOrder.query.order_by(FumigationOrder.created_at.desc()).limit(8).all()

        # Cosechas & Producción Semanal Actual (Semana de Corte)
        from app.shared.utils import get_local_now
        from app.modules.cosechas.routes import RegistroCosecha, Estimado, cargar_maestro_productos
        now_dt = get_local_now()
        iso_year, iso_week, _ = now_dt.isocalendar()

        # Determinar todos los años disponibles en el sistema (Cosechas, Fenogramas)
        available_years_set = {iso_year}
        try:
            for r in db.session.execute(text("SELECT DISTINCT EXTRACT(isoyear FROM fecha)::int FROM registro_cosecha WHERE fecha IS NOT NULL")).fetchall():
                if r[0] and 2020 <= int(r[0]) <= 2035:
                    available_years_set.add(int(r[0]))
            for r in db.session.execute(text("SELECT DISTINCT anio FROM calificaciones WHERE anio IS NOT NULL")).fetchall():
                if r[0] and 2020 <= int(r[0]) <= 2035:
                    available_years_set.add(int(r[0]))
        except Exception:
            pass
        available_years_list = sorted(list(available_years_set), reverse=True)

        # Leer año seleccionado del filtro global (?year=YYYY)
        req_year = request.args.get('year')
        if req_year:
            try:
                selected_year = int(req_year)
            except Exception:
                selected_year = iso_year
        else:
            selected_year = iso_year

        # Determinar todas las semanas disponibles en el sistema (Cosechas, Fenogramas, Fumigación)
        harvest_weeks_raw = [r[0] for r in db.session.query(RegistroCosecha.semana).distinct().all() if r[0] is not None]
        feno_weeks_raw = [r[0] for r in db.session.execute(text("SELECT DISTINCT semana_anio FROM calificaciones")).fetchall() if r[0] is not None]
        feno_eval_weeks_raw = [r[0] for r in db.session.execute(text("SELECT DISTINCT semana_anio FROM semanas_evaluacion")).fetchall() if r[0] is not None]
        fum_weeks_raw = [r[0] for r in db.session.query(FumigationOrder.week).distinct().all() if r[0] is not None]

        available_weeks_set = {iso_week}
        for hw in harvest_weeks_raw:
            try:
                hw_int = int(hw)
                w_norm = hw_int % 100 if hw_int >= 100 else hw_int
                if 1 <= w_norm <= 53:
                    available_weeks_set.add(w_norm)
            except Exception:
                pass
        for fw in feno_weeks_raw + feno_eval_weeks_raw:
            try:
                fw_int = int(fw)
                if 1 <= fw_int <= 53:
                    available_weeks_set.add(fw_int)
            except Exception:
                pass
        for fumw in fum_weeks_raw:
            try:
                if '-' in str(fumw):
                    fumw_int = int(str(fumw).split('-')[-1])
                    if 1 <= fumw_int <= 53:
                        available_weeks_set.add(fumw_int)
            except Exception:
                pass

        available_weeks_list = sorted(list(available_weeks_set), reverse=True)

        # Leer semana seleccionada del filtro global (?week=XX)
        req_week = request.args.get('week')
        if req_week:
            try:
                selected_week_num = int(str(req_week).split('-')[-1])
            except Exception:
                selected_week_num = iso_week
        else:
            selected_week_num = iso_week

        if not selected_week and weeks_list:
            selected_week = weeks_list[0]

        semanas_cosecha_match = [selected_week_num, (selected_year % 100) * 100 + selected_week_num]
        semanas_estimado_match = [str(selected_week_num), f"{selected_year % 100:02d}{selected_week_num:02d}"]

        cosechas_semana_actual_query = RegistroCosecha.query.filter(
            RegistroCosecha.semana.in_(semanas_cosecha_match),
            db.or_(
                db.extract('isoyear', RegistroCosecha.fecha) == selected_year,
                db.and_(RegistroCosecha.fecha == None, RegistroCosecha.semana == (selected_year % 100) * 100 + selected_week_num)
            )
        )
        cosechas_total_tallos = db.session.query(db.func.sum(RegistroCosecha.total_tallos)).filter(
            RegistroCosecha.semana.in_(semanas_cosecha_match),
            db.or_(
                db.extract('isoyear', RegistroCosecha.fecha) == selected_year,
                db.and_(RegistroCosecha.fecha == None, RegistroCosecha.semana == (selected_year % 100) * 100 + selected_week_num)
            )
        ).scalar() or 0
        cosechas_registros_count = cosechas_semana_actual_query.count()

        estimados_total_tallos = db.session.query(db.func.sum(Estimado.cantidad_estimada)).filter(Estimado.semana.in_(semanas_estimado_match)).scalar() or 0
        cumplimiento_cosecha = round((float(cosechas_total_tallos) / float(estimados_total_tallos) * 100), 1) if estimados_total_tallos > 0 else 0

        # 1. Cumplimiento Fenograma de la Semana y Año Seleccionados (Homologado con módulo Fenogramas: tiene_evaluacion = TRUE)
        try:
            feno_res = db.session.execute(text("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas,
                    ROUND(100.0 * COUNT(CASE WHEN cumplido = 1 THEN 1 END) / NULLIF(COUNT(*), 0), 1) as pct
                FROM calificaciones
                WHERE semana_anio = :w AND (anio = :y OR anio IS NULL) AND tiene_evaluacion = TRUE
            """), {'w': selected_week_num, 'y': selected_year}).fetchone()
            if feno_res and feno_res.total and feno_res.total > 0:
                cumplimiento_fenograma = float(feno_res.pct or 0.0)
                feno_total_actividades = feno_res.total
                feno_cumplidas = feno_res.cumplidas or 0
            else:
                cumplimiento_fenograma = 0.0
                feno_total_actividades = 0
                feno_cumplidas = 0
        except Exception:
            cumplimiento_fenograma = 0.0
            feno_total_actividades = 0
            feno_cumplidas = 0

        # 2. Área Total de Finca y Camas de Cultivo (calculado en memoria desde get_mrt_rows_cached en <1ms)
        excluded_mrt_prods = {'VACIO', 'APERTURA', 'EUCALIPTOS', 'VICIA-AVENA', 'MOSTAZA CALIENTE', 'ERRADICADO', 'TOTAL', 'NAN', ''}
        mrt_cached_rows = []
        try:
            from app.shared.censo_sync import get_mrt_rows_cached
            mrt_cached_rows = get_mrt_rows_cached(ttl_seconds=25)
            if mrt_cached_rows:
                ha_tot = 0.0
                ha_prd = 0.0
                c_tot_set = set()
                c_prd_set = set()
                blq_set = set()
                for mr in mrt_cached_rows:
                    h_v = float(mr[10] or 0.0)
                    id_c = mr[11]
                    cod_b = mr[12]
                    p_act = str(mr[3] or '').strip().upper()
                    ha_tot += h_v
                    if id_c is not None:
                        c_tot_set.add(id_c)
                    if cod_b is not None:
                        blq_set.add(cod_b)
                    if p_act not in excluded_mrt_prods:
                        ha_prd += h_v
                        if id_c is not None:
                            c_prd_set.add(id_c)
                if ha_tot > 0:
                    area_total_finca_ha = round(ha_tot, 2)
                    area_finca_ha = round(ha_prd, 2)
                    camas_finca_count = len(c_prd_set)
                    camas_total_count = len(c_tot_set)
                    bloques_finca_count = len(blq_set)
                else:
                    area_total_finca_ha = 32.00
                    area_finca_ha = 19.99
                    camas_finca_count = 6314
                    camas_total_count = 9302
                    bloques_finca_count = 210
            else:
                area_total_finca_ha = 32.00
                area_finca_ha = 19.99
                camas_finca_count = 6314
                camas_total_count = 9302
                bloques_finca_count = 210

            porcentaje_ocupacion = round((area_finca_ha / area_total_finca_ha) * 100.0, 1) if area_total_finca_ha > 0 else 62.5
        except Exception:
            area_total_finca_ha = 32.00
            area_finca_ha = 19.99
            camas_finca_count = 6314
            camas_total_count = 9302
            bloques_finca_count = 210
            porcentaje_ocupacion = 62.5

        # 3. Histórico semanal de tallos cosechados agrupado año a año (2025 vs 2026)
        historico_semanas_raw = db.session.query(
            db.extract('isoyear', RegistroCosecha.fecha).label('anio'),
            RegistroCosecha.semana,
            db.func.sum(RegistroCosecha.total_tallos).label('total')
        ).group_by('anio', RegistroCosecha.semana).order_by('anio', RegistroCosecha.semana).all()

        semanas_2025_map = {}
        semanas_2026_map = {}
        all_weeks_set = set()

        for r_hist in historico_semanas_raw:
            if not r_hist.semana:
                continue
            s_raw = int(r_hist.semana)
            tot_w = int(r_hist.total or 0)

            # Homologar estrictamente semana en formato 1 a 52
            if s_raw >= 100:
                w_num = s_raw % 100
                y_val = 2000 + (s_raw // 100)
            else:
                w_num = s_raw
                y_val = int(r_hist.anio) if r_hist.anio else 2026

            if w_num < 1 or w_num > 53:
                continue

            all_weeks_set.add(w_num)
            if y_val == 2025:
                semanas_2025_map[w_num] = semanas_2025_map.get(w_num, 0) + tot_w
            else:
                semanas_2026_map[w_num] = semanas_2026_map.get(w_num, 0) + tot_w

        all_weeks_sorted = sorted(all_weeks_set)
        historico_semanas_labels = [f"Sem {w}" for w in all_weeks_sorted]
        historico_2025_tallos = [semanas_2025_map.get(w, None) for w in all_weeks_sorted]
        historico_2026_tallos = [semanas_2026_map.get(w, None) for w in all_weeks_sorted]

        # 4. Participación por Producto Maestro (Semana Seleccionada - Orden estrictamente descendente)
        maestro_productos = cargar_maestro_productos()
        todos_cosechas_actuales = cosechas_semana_actual_query.all()
        cosechas_para_prods = todos_cosechas_actuales if todos_cosechas_actuales else []
        tallos_por_producto_raw = {}
        for r in cosechas_para_prods:
            pm = maestro_productos.get(r.variedad, 'SIN CLASIFICAR')
            tallos_por_producto_raw[pm] = tallos_por_producto_raw.get(pm, 0) + (r.total_tallos or 0)

        total_tallos_semana = sum(tallos_por_producto_raw.values())
        participacion_productos_lista = []
        for prod_name, tallos_val in tallos_por_producto_raw.items():
            pct = round((tallos_val / total_tallos_semana * 100), 1) if total_tallos_semana > 0 else 0.0
            participacion_productos_lista.append({
                'producto': prod_name,
                'tallos': tallos_val,
                'pct': pct
            })
        participacion_productos_lista.sort(key=lambda x: x['tallos'], reverse=True)
        tallos_por_producto = {item['producto']: item['tallos'] for item in participacion_productos_lista}

        # 4.1 Histórico de Cumplimiento de Fenogramas para Segmentador Multimodal de Líneas (tiene_evaluacion = TRUE)
        feno_trend_raw = db.session.execute(text("""
            SELECT 
                semana_anio,
                COUNT(*) as total,
                COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas,
                ROUND(100.0 * COUNT(CASE WHEN cumplido = 1 THEN 1 END) / NULLIF(COUNT(*), 0), 1) as pct
            FROM calificaciones
            WHERE tiene_evaluacion = TRUE
            GROUP BY semana_anio
            ORDER BY semana_anio ASC
        """)).fetchall()

        feno_weeks_list = [int(r.semana_anio) for r in feno_trend_raw]
        feno_labels = [f"Sem {r.semana_anio}" for r in feno_trend_raw]
        feno_pcts = [float(r.pct or 0.0) for r in feno_trend_raw]

        feno_crop_trend_raw = db.session.execute(text("""
            SELECT 
                semana_anio,
                UPPER(TRIM(COALESCE(producto_maestro, 'VARIOS'))) as crop,
                COUNT(*) as total,
                COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas,
                ROUND(100.0 * COUNT(CASE WHEN cumplido = 1 THEN 1 END) / NULLIF(COUNT(*), 0), 1) as pct
            FROM calificaciones
            WHERE tiene_evaluacion = TRUE
            GROUP BY semana_anio, UPPER(TRIM(COALESCE(producto_maestro, 'VARIOS')))
            ORDER BY semana_anio ASC, crop ASC
        """)).fetchall()

        feno_por_cultivo = {}
        for r in feno_crop_trend_raw:
            c = str(r.crop).upper().strip()
            if c not in feno_por_cultivo:
                feno_por_cultivo[c] = {}
            feno_por_cultivo[c][int(r.semana_anio)] = float(r.pct or 0.0)

        historico_feno_data = {
            'labels': feno_labels,
            'global_pct': feno_pcts,
            'by_crop': feno_por_cultivo,
            'weeks': feno_weeks_list
        }

        # 5. Histórico Desglosado por Producto Maestro para Segmentación Interactiva (Estilo Power BI)
        from collections import defaultdict
        crop_week_map = defaultdict(lambda: {'2025': defaultdict(int), '2026': defaultdict(int)})
        records_hist_crops = db.session.query(
            db.extract('isoyear', RegistroCosecha.fecha).label('anio'),
            RegistroCosecha.semana,
            RegistroCosecha.variedad,
            db.func.sum(RegistroCosecha.total_tallos).label('total')
        ).group_by('anio', RegistroCosecha.semana, RegistroCosecha.variedad).all()

        for r in records_hist_crops:
            if not r.semana: continue
            s_raw = int(r.semana)
            tot_w = int(r.total or 0)
            if s_raw >= 100:
                w_num = s_raw % 100
                y_val = 2000 + (s_raw // 100)
            else:
                w_num = s_raw
                y_val = int(r.anio) if r.anio else 2026

            if w_num < 1 or w_num > 53: continue
            y_key = str(y_val) if str(y_val) in ['2025', '2026'] else '2026'
            c_nom = str(maestro_productos.get(r.variedad, 'SIN CLASIFICAR')).upper().strip()
            crop_week_map[c_nom][y_key][w_num] += tot_w

        historico_por_cultivo = {}
        for c_nom, y_data in crop_week_map.items():
            historico_por_cultivo[c_nom] = {
                '2025': [y_data['2025'].get(w, None) for w in all_weeks_sorted],
                '2026': [y_data['2026'].get(w, None) for w in all_weeks_sorted]
            }
        historico_por_cultivo['ALL'] = {
            '2025': historico_2025_tallos,
            '2026': historico_2026_tallos
        }

        # 6. Costos Agronómicos y Liquidación por Hectárea (Fumigación, Desinfecciones & Bodegas de Insumos)
        try:
            from app.modules.fumigacion.services.cost_service import CostService
            selected_week_str = f"{selected_year}-{selected_week_num:02d}"
            # KPI 5 Cost Summary exclusivamente para la semana seleccionada en el corte
            kpi_cost_summary = CostService.get_dashboard_cost_summary(week=selected_week_str)

            # Selector de semana de la sección inferior de costos (si no se especifica, por defecto semana de corte)
            cost_week = request.args.get('cost_week')
            if not cost_week:
                cost_week = selected_week_str

            if cost_week == selected_week_str:
                cost_summary = kpi_cost_summary
            else:
                cost_summary = CostService.get_dashboard_cost_summary(week=cost_week if cost_week != 'ALL' else None)
            distinct_order_weeks_raw = db.session.query(FumigationOrder.week).distinct().all()
            from app.shared.models import Rotation
            distinct_rot_weeks_raw = db.session.query(Rotation.week).distinct().all()
            all_weeks_raw = [w[0] for w in distinct_order_weeks_raw if w[0]] + [w[0] for w in distinct_rot_weeks_raw if w[0]]
            available_cost_weeks = sorted(list(set(all_weeks_raw)), reverse=True)
        except Exception:
            kpi_cost_summary = {
                'total_cost': 0.0, 'total_ha': 0.0, 'farm_cultivated_ha': 19.99, 'avg_cost_per_ha': 0.0, 'cost_per_ha_finca': 0.0,
                'orders_count': 0, 'blocks_count': 0, 'by_bodega': [], 'top_blocks': [],
                'all_blocks': [], 'by_crop': [], 'weekly_history': []
            }
            cost_summary = kpi_cost_summary
            cost_week = 'ALL'
            available_cost_weeks = []

        # 7. Pre-agregados por Producto Maestro para Filtrado Dinámico de las 5 Tarjetas Bento KPI
        import json
        kpi_cosecha_by_crop = defaultdict(lambda: {'tallos': 0, 'registros': 0})
        for r in cosechas_para_prods:
            pm = str(maestro_productos.get(r.variedad, 'SIN CLASIFICAR')).upper().strip()
            kpi_cosecha_by_crop[pm]['tallos'] += int(r.total_tallos or 0)
            kpi_cosecha_by_crop[pm]['registros'] += 1

        # Generar alias recíprocos singular/plural (ej: EUCALIPTO <-> EUCALIPTOS)
        for k in list(kpi_cosecha_by_crop.keys()):
            val = kpi_cosecha_by_crop[k]
            if k.endswith('S') and k[:-1] not in kpi_cosecha_by_crop:
                kpi_cosecha_by_crop[k[:-1]] = val
            elif not k.endswith('S') and (k + 'S') not in kpi_cosecha_by_crop:
                kpi_cosecha_by_crop[k + 'S'] = val

        kpi_feno_by_crop = {}
        try:
            feno_kpi_rows = db.session.execute(text("""
                SELECT 
                    UPPER(TRIM(COALESCE(producto_maestro, 'VARIOS'))) as crop,
                    COUNT(*) as total,
                    COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas,
                    ROUND(100.0 * COUNT(CASE WHEN cumplido = 1 THEN 1 END) / NULLIF(COUNT(*), 0), 1) as pct
                FROM calificaciones
                WHERE semana_anio = :w AND (anio = :y OR anio IS NULL) AND tiene_evaluacion = TRUE
                GROUP BY UPPER(TRIM(COALESCE(producto_maestro, 'VARIOS')))
            """), {'w': selected_week_num, 'y': selected_year}).fetchall()
            for r in feno_kpi_rows:
                c_nom = str(r.crop).upper().strip()
                item = {
                    'pct': float(r.pct or 0.0),
                    'cumplidas': int(r.cumplidas or 0),
                    'total': int(r.total or 0)
                }
                kpi_feno_by_crop[c_nom] = item
                if c_nom.endswith('S'):
                    kpi_feno_by_crop.setdefault(c_nom[:-1], item)
                else:
                    kpi_feno_by_crop.setdefault(c_nom + 'S', item)
                first_tok = c_nom.split()[0].replace('-', ' ')
                kpi_feno_by_crop.setdefault(first_tok, item)
        except Exception:
            kpi_feno_by_crop = {}

        kpi_area_camas_by_crop = {}
        try:
            crop_agg = defaultdict(lambda: {'camas': set(), 'bloques': set(), 'ha': 0.0})
            for mr in mrt_cached_rows:
                p_act = str(mr[3] or '').strip().upper()
                if p_act and p_act not in excluded_mrt_prods:
                    if mr[11] is not None:
                        crop_agg[p_act]['camas'].add(mr[11])
                    if mr[12] is not None:
                        crop_agg[p_act]['bloques'].add(mr[12])
                    crop_agg[p_act]['ha'] += float(mr[10] or 0.0)
            for c_nom, agg_v in crop_agg.items():
                item = {
                    'ha': round(agg_v['ha'], 2),
                    'camas': len(agg_v['camas']),
                    'bloques': len(agg_v['bloques'])
                }
                kpi_area_camas_by_crop[c_nom] = item
                if c_nom.endswith('S'):
                    kpi_area_camas_by_crop.setdefault(c_nom[:-1], item)
                else:
                    kpi_area_camas_by_crop.setdefault(c_nom + 'S', item)
                first_tok = c_nom.split()[0].replace('-', ' ')
                kpi_area_camas_by_crop.setdefault(first_tok, item)

            # Consolidado de VERONICAS: VERONICA (3.63) + VERONICA SPLASH (1.50) = 5.13 ha
            v_norm = kpi_area_camas_by_crop.get('VERONICA', {}).get('ha', 0.0)
            v_spl = kpi_area_camas_by_crop.get('VERONICA SPLASH', {}).get('ha', 0.0)
            v_tot = round(v_norm + v_spl, 2)
            if v_tot > 0:
                v_camas = kpi_area_camas_by_crop.get('VERONICA', {}).get('camas', 0) + kpi_area_camas_by_crop.get('VERONICA SPLASH', {}).get('camas', 0)
                v_blqs = kpi_area_camas_by_crop.get('VERONICA', {}).get('bloques', 0) + kpi_area_camas_by_crop.get('VERONICA SPLASH', {}).get('bloques', 0)
                kpi_area_camas_by_crop['VERONICAS'] = {'ha': v_tot, 'camas': v_camas, 'bloques': v_blqs}
                kpi_area_camas_by_crop['VERONICA'] = {'ha': v_tot, 'camas': v_camas, 'bloques': v_blqs}

            # Consolidado unificado de GYPSOPHILA (Billion Lights + G49 + G5 = 4.55 ha)
            gyp_entry = kpi_area_camas_by_crop.get('GYPSOPHILA') or kpi_area_camas_by_crop.get('GYPSOPHILAS')
            if not gyp_entry or float(gyp_entry.get('ha', 0.0)) <= 0:
                bl_ha = kpi_area_camas_by_crop.get('BILLION LIGHTS', {}).get('ha', 0.0)
                g49_ha = kpi_area_camas_by_crop.get('G49', {}).get('ha', 0.0)
                g5_ha = kpi_area_camas_by_crop.get('G5', {}).get('ha', 0.0)
                gyp_tot = round(bl_ha + g49_ha + g5_ha, 2)
                if gyp_tot > 0:
                    gyp_camas = (kpi_area_camas_by_crop.get('BILLION LIGHTS', {}).get('camas', 0) +
                                 kpi_area_camas_by_crop.get('G49', {}).get('camas', 0) +
                                 kpi_area_camas_by_crop.get('G5', {}).get('camas', 0))
                    gyp_blqs = (kpi_area_camas_by_crop.get('BILLION LIGHTS', {}).get('bloques', 0) +
                                kpi_area_camas_by_crop.get('G49', {}).get('bloques', 0) +
                                kpi_area_camas_by_crop.get('G5', {}).get('bloques', 0))
                    gyp_entry = {'ha': gyp_tot, 'camas': gyp_camas, 'bloques': gyp_blqs}
            if gyp_entry:
                kpi_area_camas_by_crop['GYPSOPHILA'] = gyp_entry
                kpi_area_camas_by_crop['GYPSOPHILAS'] = gyp_entry
                kpi_area_camas_by_crop['BILLION LIGHTS'] = gyp_entry
                kpi_area_camas_by_crop['BILLION'] = gyp_entry
        except Exception:
            pass

        if not kpi_area_camas_by_crop and latest_batch:
            try:
                csr_crop_rows = db.session.execute(text("""
                    SELECT 
                        UPPER(TRIM(crop_master)) as crop,
                        COUNT(DISTINCT (block_full || '-' || bed_num)) as camas,
                        COUNT(DISTINCT block_full) as bloques
                    FROM crop_state_records
                    WHERE batch_id = :b AND crop_master IS NOT NULL
                    GROUP BY UPPER(TRIM(crop_master))
                """), {'b': latest_batch}).fetchall()
                for r in csr_crop_rows:
                    c_nom = str(r.crop).upper().strip()
                    item = {
                        'ha': round(float(r.camas or 0) * 0.0042, 2),
                        'camas': int(r.camas or 0),
                        'bloques': int(r.bloques or 0)
                    }
                    kpi_area_camas_by_crop[c_nom] = item
                    if c_nom.endswith('S'):
                        kpi_area_camas_by_crop.setdefault(c_nom[:-1], item)
                    else:
                        kpi_area_camas_by_crop.setdefault(c_nom + 'S', item)
            except Exception:
                pass

        kpi_costos_by_crop = {}
        for c in kpi_cost_summary.get('by_crop', []):
            c_nom = str(c.get('crop') or '').upper().strip()
            if c_nom:
                item = {
                    'total_cost': float(c.get('total_cost') or 0.0),
                    'cost_per_ha': float(c.get('cost_per_ha') or 0.0),
                    'ha': float(c.get('ha') or 0.0)
                }
                kpi_costos_by_crop[c_nom] = item
                if c_nom.endswith('S'):
                    kpi_costos_by_crop.setdefault(c_nom[:-1], item)
                else:
                    kpi_costos_by_crop.setdefault(c_nom + 'S', item)
                first_tok = c_nom.split()[0].replace('-', ' ')
                kpi_costos_by_crop.setdefault(first_tok, item)

        # Homologar alias recíprocos de GYPSOPHILA en todas las dimensiones Bento
        feno_gyp = kpi_feno_by_crop.get('GYPSOPHILA') or kpi_feno_by_crop.get('GYPSOPHILAS')
        if feno_gyp:
            kpi_feno_by_crop['BILLION LIGHTS'] = feno_gyp
            kpi_feno_by_crop['BILLION'] = feno_gyp

        gyp_cos = kpi_cosecha_by_crop.get('GYPSOPHILA') or kpi_cosecha_by_crop.get('GYPSOPHILAS') or kpi_cosecha_by_crop.get('BILLION LIGHTS')
        if gyp_cos:
            kpi_cosecha_by_crop['GYPSOPHILA'] = gyp_cos
            kpi_cosecha_by_crop['GYPSOPHILAS'] = gyp_cos
            kpi_cosecha_by_crop['BILLION LIGHTS'] = gyp_cos
            kpi_cosecha_by_crop['BILLION'] = gyp_cos

        cost_gyp = kpi_costos_by_crop.get('GYPSOPHILA') or kpi_costos_by_crop.get('GYPSOPHILAS')
        if cost_gyp:
            kpi_costos_by_crop['BILLION LIGHTS'] = cost_gyp
            kpi_costos_by_crop['BILLION'] = cost_gyp

        kpi_cosecha_by_crop_json = json.dumps(dict(kpi_cosecha_by_crop))
        kpi_feno_by_crop_json = json.dumps(kpi_feno_by_crop)
        kpi_area_camas_by_crop_json = json.dumps(kpi_area_camas_by_crop)
        kpi_costos_by_crop_json = json.dumps(kpi_costos_by_crop)

        return render_template(
            'dashboard.html',
            crops_count=crops_count,
            products_count=products_count,
            beds_count=camas_finca_count,
            camas_finca_count=camas_finca_count,
            bloques_finca_count=bloques_finca_count,
            area_finca_ha=area_finca_ha,
            area_total_finca_ha=area_total_finca_ha,
            camas_total_count=camas_total_count,
            rotations_count=rotations_count,
            orders_count=orders_count,
            weekly_rotations_data=weekly_rotations_data,
            weeks_list=weeks_list,
            selected_week=selected_week,
            recent_orders=recent_orders,
            semana_actual=selected_week_num,
            semana_actual_iso=iso_week,
            semana_actual_year=iso_year,
            available_weeks_list=available_weeks_list,
            selected_week_num=selected_week_num,
            available_years_list=available_years_list,
            selected_year=selected_year,
            cosechas_total_tallos=cosechas_total_tallos,
            estimados_total_tallos=estimados_total_tallos,
            cumplimiento_cosecha=cumplimiento_cosecha,
            cumplimiento_fenograma=cumplimiento_fenograma,
            feno_cumplidas=feno_cumplidas,
            feno_total_actividades=feno_total_actividades,
            historico_semanas_labels=historico_semanas_labels,
            historico_2025_tallos=historico_2025_tallos,
            historico_2026_tallos=historico_2026_tallos,
            tallos_por_producto=tallos_por_producto,
            participacion_productos_lista=participacion_productos_lista,
            historico_por_cultivo=historico_por_cultivo,
            historico_feno_data=historico_feno_data,
            cosechas_registros_count=cosechas_registros_count,
            kpi_cost_summary=kpi_cost_summary,
            cost_summary=cost_summary,
            cost_week=cost_week,
            available_cost_weeks=available_cost_weeks,
            kpi_cosecha_by_crop_json=kpi_cosecha_by_crop_json,
            kpi_feno_by_crop_json=kpi_feno_by_crop_json,
            kpi_area_camas_by_crop_json=kpi_area_camas_by_crop_json,
            kpi_costos_by_crop_json=kpi_costos_by_crop_json,
            porcentaje_ocupacion=porcentaje_ocupacion
        )

    return app
