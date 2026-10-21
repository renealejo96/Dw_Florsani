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
        from flask import request, redirect, url_for
        if request.endpoint:
            if request.endpoint.startswith('static') or request.endpoint in ['auth.login', 'auth.logout', 'service_worker_file']:
                return None
        
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))

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
        if not selected_week and weeks_list:
            selected_week = weeks_list[0]

        recent_orders = FumigationOrder.query.order_by(FumigationOrder.created_at.desc()).limit(8).all()

        # Cosechas & Producción Semanal Actual (Semana de Corte)
        from app.shared.utils import get_local_now
        from app.modules.cosechas.routes import RegistroCosecha, Estimado, cargar_maestro_productos
        now_dt = get_local_now()
        iso_year, iso_week, _ = now_dt.isocalendar()
        semana_actual_int = iso_week
        semanas_cosecha_match = [semana_actual_int, (iso_year % 100) * 100 + semana_actual_int]
        semanas_estimado_match = [str(semana_actual_int), f"{iso_year % 100:02d}{semana_actual_int:02d}"]

        cosechas_semana_actual_query = RegistroCosecha.query.filter(RegistroCosecha.semana.in_(semanas_cosecha_match))
        cosechas_total_tallos = db.session.query(db.func.sum(RegistroCosecha.total_tallos)).filter(RegistroCosecha.semana.in_(semanas_cosecha_match)).scalar() or 0
        cosechas_registros_count = cosechas_semana_actual_query.count()

        estimados_total_tallos = db.session.query(db.func.sum(Estimado.cantidad_estimada)).filter(Estimado.semana.in_(semanas_estimado_match)).scalar() or 0
        cumplimiento_cosecha = round((float(cosechas_total_tallos) / float(estimados_total_tallos) * 100), 1) if estimados_total_tallos > 0 else 0

        # Histórico semanal de tallos cosechados agrupado año a año (2025 vs 2026)
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

            # Homologar estrictamente semana en formato 1 a 52 (ej: 2640 -> 40, 2643 -> 43, 2548 -> 48)
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

        maestro_productos = cargar_maestro_productos()
        todos_cosechas_actuales = cosechas_semana_actual_query.all()
        cosechas_para_prods = todos_cosechas_actuales if todos_cosechas_actuales else RegistroCosecha.query.limit(500).all()
        tallos_por_producto = {}
        for r in cosechas_para_prods:
            pm = maestro_productos.get(r.variedad, 'SIN CLASIFICAR')
            tallos_por_producto[pm] = tallos_por_producto.get(pm, 0) + (r.total_tallos or 0)

        return render_template(
            'dashboard.html',
            crops_count=crops_count,
            products_count=products_count,
            beds_count=beds_count,
            rotations_count=rotations_count,
            orders_count=orders_count,
            weekly_rotations_data=weekly_rotations_data,
            weeks_list=weeks_list,
            selected_week=selected_week,
            recent_orders=recent_orders,
            semana_actual=semana_actual_int,
            cosechas_total_tallos=cosechas_total_tallos,
            estimados_total_tallos=estimados_total_tallos,
            cumplimiento_cosecha=cumplimiento_cosecha,
            historico_semanas_labels=historico_semanas_labels,
            historico_2025_tallos=historico_2025_tallos,
            historico_2026_tallos=historico_2026_tallos,
            tallos_por_producto=tallos_por_producto,
            cosechas_registros_count=cosechas_registros_count
        )

    return app
