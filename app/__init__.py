import os
from flask import Flask, render_template, session, g
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

    # Safe dynamic column migrations for SQLite and PostgreSQL
    with app.app_context():
        from sqlalchemy import inspect, text
        try:
            inspector = inspect(db.engine)
            existing_tables = inspector.get_table_names()
            is_sqlite = 'sqlite' in str(app.config.get('SQLALCHEMY_DATABASE_URI', ''))

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
                    "ALTER TABLE additional_applications ALTER COLUMN notes TYPE TEXT"
                ]
                for m in pg_migrations:
                    try:
                        db.session.execute(text(m))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
        except Exception:
            pass

        # Ensure database tables exist and initial admin is ready
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
                db.session.commit()
        except Exception:
            pass

        # One-time migration to reset test fenograma week 43 with bed consolidation
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS _system_one_time_migrations (
                    migration_key VARCHAR(100) PRIMARY KEY,
                    executed_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """))
            db.session.commit()

            mig_done = db.session.execute(text(
                "SELECT 1 FROM _system_one_time_migrations WHERE migration_key = 'reset_feno_sem43_clean_v2'"
            )).scalar()

            if not mig_done:
                from app.modules.fenogramas.services.fenograma_service import sync_from_mrt_estado_cultivo
                sync_from_mrt_estado_cultivo(semana_corte=43, anio=2026, fuente='AUTO', forzar_limpieza=True)
                db.session.execute(text(
                    "INSERT INTO _system_one_time_migrations (migration_key) VALUES ('reset_feno_sem43_clean_v2')"
                ))
                db.session.commit()
                print("[SYSTEM] Limpieza y regeneración inicial de Fenograma Semana 43 completada con éxito.")
        except Exception as ex_mig:
            db.session.rollback()
            print(f"[SYSTEM] Aviso en migración de fenograma: {ex_mig}")

        # One-time migration to synchronize cat_variedades_maestro and enforce tombstones on VPS
        try:
            mig_cat_done = db.session.execute(text(
                "SELECT 1 FROM _system_one_time_migrations WHERE migration_key = 'sync_catalogos_vps_tombstones_v7'"
            )).scalar()

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

    # Global user context processor
    @app.context_processor
    def inject_user():
        from app.shared.models import User
        user_id = session.get('user_id')
        current_user = User.query.get(user_id) if user_id else None
        return {'current_user': current_user}

    # Force login by default for all application routes
    @app.before_request
    def check_authenticated_user():
        from flask import request, redirect, url_for, jsonify
        if request.endpoint:
            if request.endpoint.startswith('static') or request.endpoint in [
                'auth.login', 'auth.logout', 'auth.api_quick_login',
                'fenogramas.api_ping', 'service_worker_file'
            ]:
                return None
        
        if 'user_id' not in session:
            # Si es una petición API / JSON / AJAX, retornar 401 JSON en lugar de redirección HTML 302
            is_api = bool(
                request.is_json
                or (request.path and ('/api/' in request.path or request.path.startswith('/api/')))
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


    @app.route('/sw.js')
    def service_worker_file():
        from flask import send_from_directory
        response = send_from_directory(app.static_folder, 'sw.js', mimetype='application/javascript')
        response.headers['Service-Worker-Allowed'] = '/'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
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

        all_rotations = Rotation.query.options(
            db.joinedload(Rotation.rounds).joinedload(RotationRound.items).joinedload(RotationRoundItem.product)
        ).order_by(Rotation.week.desc(), Rotation.version.desc()).all()
        
        # Group rotations by week with full details of rounds and chemicals
        weekly_rotations_data = {}
        for rot in all_rotations:
            w = rot.week
            if w not in weekly_rotations_data:
                weekly_rotations_data[w] = []
            
            rounds_info = []
            for rd in sorted(rot.rounds, key=lambda x: x.round_number):
                formula_groups = {}
                for it in sorted(rd.items, key=lambda x: x.order_index):
                    crop_k = it.crop_name or 'Cultivo'
                    stage_k = it.phenological_stage or 'GENERAL'
                    grp_k = f"{crop_k} ({stage_k})"
                    if grp_k not in formula_groups:
                        formula_groups[grp_k] = []
                    
                    p_code = it.product.code if it.product else f"Prod #{it.product_id}"
                    p_name = it.product.commercial_name if (it.product and it.product.commercial_name) else p_code
                    formula_groups[grp_k].append({
                        'code': p_code,
                        'name': p_name,
                        'dose': it.dose_applied,
                        'unit': it.dose_unit or (it.product.unit if it.product else 'CC')
                    })
                
                distinct_formulas = []
                for grp_k, p_list in formula_groups.items():
                    distinct_formulas.append({
                        'label': grp_k,
                        'products': p_list
                    })

                rounds_info.append({
                    'round_number': rd.round_number,
                    'name': rd.name,
                    'day': rd.scheduled_day,
                    'formulas': distinct_formulas,
                    'total_items': len(rd.items)
                })

            weekly_rotations_data[w].append({
                'id': rot.id,
                'week': rot.week,
                'version': rot.version,
                'title': rot.title or f"Rotación Semana {rot.week}",
                'status': rot.status,
                'created_by': rot.created_by,
                'approved_by': rot.approved_by,
                'created_at': rot.created_at,
                'approved_at': rot.approved_at,
                'notes': rot.notes,
                'rounds': rounds_info
            })

        weeks_list = list(weekly_rotations_data.keys())
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
        for fw in feno_weeks_raw:
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

        # 1. Cumplimiento Fenograma de la Semana y Año Seleccionados
        try:
            feno_res = db.session.execute(text("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN cumplido = 1.0 THEN 1 ELSE 0 END) as cumplidas,
                    ROUND(100.0 * SUM(CASE WHEN cumplido = 1.0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as pct
                FROM calificaciones
                WHERE semana_anio = :w AND (anio = :y OR anio IS NULL)
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

        # 2. Área Total de Finca y Camas de Cultivo (Último Estado de Cultivo y dim_camas)
        try:
            area_finca_ha = db.session.execute(text("""
                SELECT ROUND(COALESCE(SUM(ha), 0)::numeric, 2)
                FROM dim_camas
                WHERE activa IS TRUE OR activa IS NULL
            """)).scalar() or 32.09

            latest_batch = db.session.execute(text("SELECT MAX(batch_id) FROM crop_state_records")).scalar()
            if latest_batch:
                camas_finca_count = db.session.execute(text("""
                    SELECT COUNT(DISTINCT (block_full || '-' || bed_num))
                    FROM crop_state_records
                    WHERE batch_id = :b
                """), {'b': latest_batch}).scalar() or 7565
                bloques_finca_count = db.session.execute(text("""
                    SELECT COUNT(DISTINCT block_full)
                    FROM crop_state_records
                    WHERE batch_id = :b
                """), {'b': latest_batch}).scalar() or 231
            else:
                camas_finca_count = 7565
                bloques_finca_count = 231
        except Exception:
            area_finca_ha = 32.09
            camas_finca_count = 7565
            bloques_finca_count = 231

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

        # 4.1 Histórico de Cumplimiento de Fenogramas para Segmentador Multimodal de Líneas
        feno_trend_raw = db.session.execute(text("""
            SELECT 
                semana_anio,
                COUNT(*) as total,
                SUM(CASE WHEN cumplido = 1.0 THEN 1 ELSE 0 END) as cumplidas,
                ROUND(100.0 * SUM(CASE WHEN cumplido = 1.0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as pct
            FROM calificaciones
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
                SUM(CASE WHEN cumplido = 1.0 THEN 1 ELSE 0 END) as cumplidas,
                ROUND(100.0 * SUM(CASE WHEN cumplido = 1.0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as pct
            FROM calificaciones
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
            cost_week = request.args.get('cost_week', 'ALL')
            cost_summary = CostService.get_dashboard_cost_summary(week=cost_week if cost_week != 'ALL' else None)
            distinct_order_weeks_raw = db.session.query(FumigationOrder.week).distinct().all()
            available_cost_weeks = sorted(list(set([w[0] for w in distinct_order_weeks_raw if w[0]])), reverse=True)
        except Exception:
            cost_week = 'ALL'
            cost_summary = {
                'total_cost': 0.0, 'total_ha': 0.0, 'avg_cost_per_ha': 0.0,
                'orders_count': 0, 'blocks_count': 0, 'by_bodega': [], 'top_blocks': [],
                'all_blocks': [], 'by_crop': [], 'weekly_history': []
            }
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
                    SUM(CASE WHEN cumplido = 1 THEN 1 ELSE 0 END) as cumplidas,
                    ROUND(100.0 * SUM(CASE WHEN cumplido = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as pct
                FROM calificaciones
                WHERE semana_anio = :w AND (anio = :y OR anio IS NULL)
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
            mrt_crop_rows = db.session.execute(text("""
                SELECT 
                    UPPER(TRIM(producto_actual)) as crop,
                    COUNT(DISTINCT id_cama) as camas,
                    COUNT(DISTINCT codigo_bloque) as bloques,
                    ROUND(COALESCE(SUM(ha), 0)::numeric, 2) as ha
                FROM mrt_estado_cultivo_actual
                WHERE producto_actual IS NOT NULL AND TRIM(producto_actual) != ''
                GROUP BY UPPER(TRIM(producto_actual))
            """)).fetchall()
            for r in mrt_crop_rows:
                c_nom = str(r.crop).upper().strip()
                item = {
                    'ha': float(r.ha or 0.0),
                    'camas': int(r.camas or 0),
                    'bloques': int(r.bloques or 0)
                }
                kpi_area_camas_by_crop[c_nom] = item
                if c_nom.endswith('S'):
                    kpi_area_camas_by_crop.setdefault(c_nom[:-1], item)
                else:
                    kpi_area_camas_by_crop.setdefault(c_nom + 'S', item)
                first_tok = c_nom.split()[0].replace('-', ' ')
                kpi_area_camas_by_crop.setdefault(first_tok, item)
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
        for c in cost_summary.get('by_crop', []):
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
            cost_summary=cost_summary,
            cost_week=cost_week,
            available_cost_weeks=available_cost_weeks,
            kpi_cosecha_by_crop_json=kpi_cosecha_by_crop_json,
            kpi_feno_by_crop_json=kpi_feno_by_crop_json,
            kpi_area_camas_by_crop_json=kpi_area_camas_by_crop_json,
            kpi_costos_by_crop_json=kpi_costos_by_crop_json
        )

    return app
