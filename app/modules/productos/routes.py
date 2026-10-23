from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app.extensions import db
from app.shared.models import Product
from app.shared.audit import record_audit
from app.modules.auth.routes import login_required, permission_required

productos_bp = Blueprint('productos', __name__)

@productos_bp.route('/')
@login_required
@permission_required('catalogos')
def index():
    search = request.args.get('q', '').strip()
    status_filter = request.args.get('status', 'all')
    
    query = Product.query
    if search:
        query = query.filter(
            db.or_(
                Product.code.ilike(f"%{search}%"),
                Product.commercial_name.ilike(f"%{search}%"),
                Product.pest.ilike(f"%{search}%"),
                Product.active_ingredient.ilike(f"%{search}%")
            )
        )
    
    if status_filter == 'active':
        query = query.filter_by(is_active=True)
    elif status_filter == 'inactive':
        query = query.filter_by(is_active=False)

    products = query.order_by(Product.code.asc()).all()
    return render_template('productos/index.html', products=products, search=search, status_filter=status_filter)


@productos_bp.route('/api/search')
@login_required
def api_search():
    """
    Autocomplete API for rotation planning.
    Returns list of products matching search term.
    """
    term = request.args.get('q', '').strip()
    query = Product.query.filter_by(is_active=True)
    if term:
        query = query.filter(
            db.or_(
                Product.code.ilike(f"%{term}%"),
                Product.commercial_name.ilike(f"%{term}%"),
                Product.active_ingredient.ilike(f"%{term}%")
            )
        )
    products = query.order_by(Product.code.asc()).limit(30).all()
    return jsonify([p.to_dict() for p in products])


@productos_bp.route('/crear', methods=['GET', 'POST'])
@login_required
@permission_required('catalogos')
def create():
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        comm_name = request.form.get('commercial_name', '').strip() or code
        unit = request.form.get('unit', 'CC').strip().upper()
        dose_fumi = request.form.get('dose_fumigation')
        dose_drench = request.form.get('dose_drench')
        dose_trichos = request.form.get('dose_trichos')
        dose_solidos = request.form.get('dose_solidos')
        dose_desinfecciones = request.form.get('dose_desinfecciones')
        pest = request.form.get('pest', '').strip()
        ia = request.form.get('active_ingredient', '').strip()
        cat_tox = request.form.get('toxicological_category', '').strip()
        notes = request.form.get('notes', '').strip()

        if not code:
            flash("El código o nombre corto del producto es obligatorio.", "danger")
            return render_template('productos/form.html', product=None)

        existing = Product.query.filter_by(code=code).first()
        if existing:
            flash(f"Ya existe un producto con el código '{code}'.", "warning")
            return render_template('productos/form.html', product=None)

        product = Product(
            code=code,
            commercial_name=comm_name,
            unit=unit,
            dose_fumigation=float(dose_fumi) if dose_fumi else None,
            dose_drench=float(dose_drench) if dose_drench else None,
            dose_trichos=float(dose_trichos) if dose_trichos else None,
            dose_solidos=float(dose_solidos) if dose_solidos else None,
            dose_desinfecciones=float(dose_desinfecciones) if dose_desinfecciones else None,
            pest=pest,
            active_ingredient=ia,
            toxicological_category=cat_tox,
            notes=notes,
            is_active=True
        )
        db.session.add(product)
        db.session.commit()

        record_audit('PRODUCTOS', 'CREATE', 'Product', product.id, details={'code': code, 'comm_name': comm_name})
        flash(f"Producto '{code}' creado exitosamente.", "success")
        return redirect(url_for('productos.index'))

    return render_template('productos/form.html', product=None)


@productos_bp.route('/<int:product_id>/editar', methods=['GET', 'POST'])
@login_required
@permission_required('catalogos')
def edit(product_id):
    product = Product.query.get_or_404(product_id)
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        comm_name = request.form.get('commercial_name', '').strip() or code
        unit = request.form.get('unit', 'CC').strip().upper()
        dose_fumi = request.form.get('dose_fumigation')
        dose_drench = request.form.get('dose_drench')
        dose_trichos = request.form.get('dose_trichos')
        dose_solidos = request.form.get('dose_solidos')
        dose_desinfecciones = request.form.get('dose_desinfecciones')
        pest = request.form.get('pest', '').strip()
        ia = request.form.get('active_ingredient', '').strip()
        cat_tox = request.form.get('toxicological_category', '').strip()
        is_active = True if request.form.get('is_active') == 'on' else False
        notes = request.form.get('notes', '').strip()

        if not code:
            flash("El código del producto es obligatorio.", "danger")
            return render_template('productos/form.html', product=product)

        # Check unique code
        existing = Product.query.filter(Product.code == code, Product.id != product.id).first()
        if existing:
            flash(f"Ya existe otro producto con el código '{code}'.", "warning")
            return render_template('productos/form.html', product=product)

        product.code = code
        product.commercial_name = comm_name
        product.unit = unit
        product.dose_fumigation = float(dose_fumi) if dose_fumi else None
        product.dose_drench = float(dose_drench) if dose_drench else None
        product.dose_trichos = float(dose_trichos) if dose_trichos else None
        product.dose_solidos = float(dose_solidos) if dose_solidos else None
        product.dose_desinfecciones = float(dose_desinfecciones) if dose_desinfecciones else None
        product.pest = pest
        product.active_ingredient = ia
        product.toxicological_category = cat_tox
        product.is_active = is_active
        product.notes = notes

        db.session.commit()
        record_audit('PRODUCTOS', 'UPDATE', 'Product', product.id, details={'code': code, 'is_active': is_active})
        flash(f"Producto '{product.code}' actualizado exitosamente.", "success")
        return redirect(url_for('productos.index'))

    return render_template('productos/form.html', product=product)


@productos_bp.route('/<int:product_id>/toggle-status', methods=['POST'])
@login_required
@permission_required('catalogos')
def toggle_status(product_id):
    product = Product.query.get_or_404(product_id)
    product.is_active = not product.is_active
    db.session.commit()
    record_audit('PRODUCTOS', 'UPDATE_STATUS', 'Product', product.id, details={'code': product.code, 'is_active': product.is_active})
    status_str = "activado" if product.is_active else "desactivado"
    flash(f"Producto '{product.code}' {status_str}.", "info")
    return redirect(url_for('productos.index'))


# ==================== COSTOS Y BODEGAS DE INSUMOS ====================

@productos_bp.route('/costos')
@login_required
@permission_required('catalogos')
def costos():
    """
    Catálogo Maestro de Costos y Precios de Insumos:
    Permite gestionar precios de compra, unidades, factor de conversión ('tras')
    y asignación de bodegas para costear las aplicaciones de campo.
    """
    search = request.args.get('q', '').strip()
    bodega_filter = request.args.get('bodega', 'all').strip()
    price_filter = request.args.get('price_status', 'all').strip()

    # Query distinct bodegas
    bodegas_raw = db.session.query(Product.bodega).filter(Product.bodega.isnot(None)).distinct().all()
    bodegas_list = sorted(list(set([b[0].strip().upper() for b in bodegas_raw if b[0] and b[0].strip()])))

    query = Product.query
    if search:
        query = query.filter(
            db.or_(
                Product.code.ilike(f"%{search}%"),
                Product.commercial_name.ilike(f"%{search}%"),
                Product.articulo_requisicion.ilike(f"%{search}%"),
                Product.codigo_almacen.ilike(f"%{search}%")
            )
        )

    if bodega_filter and bodega_filter != 'all':
        query = query.filter(Product.bodega.ilike(f"%{bodega_filter}%"))

    if price_filter == 'con_precio':
        query = query.filter(Product.precio_compra.isnot(None), Product.precio_compra > 0)
    elif price_filter == 'sin_precio':
        query = query.filter(db.or_(Product.precio_compra.is_(None), Product.precio_compra == 0))

    products = query.order_by(Product.bodega.asc(), Product.code.asc()).all()

    # Métricas generales de insumos
    total_count = Product.query.count()
    with_price_count = Product.query.filter(Product.precio_compra.isnot(None), Product.precio_compra > 0).count()
    without_price_count = total_count - with_price_count

    return render_template(
        'productos/costos.html',
        products=products,
        search=search,
        bodega_filter=bodega_filter,
        price_filter=price_filter,
        bodegas_list=bodegas_list,
        total_count=total_count,
        with_price_count=with_price_count,
        without_price_count=without_price_count
    )


@productos_bp.route('/costos/<int:product_id>/editar', methods=['POST'])
@login_required
@permission_required('catalogos')
def costo_editar(product_id):
    product = Product.query.get_or_404(product_id)
    
    if request.is_json:
        data = request.get_json() or {}
        precio_raw = data.get('precio_compra')
        u_compra = data.get('unidad_compra')
        u_consumo = data.get('unidad_consumo')
        tras_raw = data.get('factor_conversion')
        bodega_val = data.get('bodega')
        art_req = data.get('articulo_requisicion')
        cod_alm = data.get('codigo_almacen')
    else:
        precio_raw = request.form.get('precio_compra')
        u_compra = request.form.get('unidad_compra')
        u_consumo = request.form.get('unidad_consumo')
        tras_raw = request.form.get('factor_conversion')
        bodega_val = request.form.get('bodega')
        art_req = request.form.get('articulo_requisicion')
        cod_alm = request.form.get('codigo_almacen')

    try:
        precio = float(precio_raw) if precio_raw is not None and str(precio_raw).strip() != '' else None
    except (ValueError, TypeError):
        precio = None

    try:
        tras = float(tras_raw) if tras_raw is not None and str(tras_raw).strip() != '' and float(tras_raw) > 0 else 1.0
    except (ValueError, TypeError):
        tras = 1.0

    costo_u = round(precio / tras, 5) if (precio is not None and tras > 0) else None

    product.precio_compra = precio
    if u_compra: product.unidad_compra = u_compra.strip()
    if u_consumo: product.unidad_consumo = u_consumo.strip()
    product.factor_conversion = tras
    product.costo_unitario = costo_u
    if bodega_val: product.bodega = bodega_val.strip().upper()
    if art_req is not None: product.articulo_requisicion = art_req.strip()
    if cod_alm is not None:
        product.codigo_almacen = cod_alm.strip()

    db.session.commit()
    record_audit('PRODUCTOS', 'UPDATE_COST', 'Product', product.id, details={'precio': precio, 'tras': tras, 'costo_unitario': costo_u, 'bodega': product.bodega})

    if request.is_json:
        return jsonify({
            'success': True,
            'message': f"Costos de '{product.code}' actualizados exitosamente.",
            'product': product.to_dict()
        })
    
    flash(f"Costos del insumo '{product.code}' actualizados exitosamente.", "success")
    return redirect(url_for('productos.costos'))


@productos_bp.route('/costos/quick-update', methods=['POST'])
@login_required
@permission_required('catalogos')
def costo_quick_update():
    data = request.get_json() or {}
    product_id = data.get('product_id')
    if not product_id:
        return jsonify({'success': False, 'error': 'ID de producto no proporcionado.'}), 400

    product = Product.query.get(product_id)
    if not product:
        return jsonify({'success': False, 'error': 'Producto no encontrado.'}), 404

    precio_raw = data.get('precio_compra')
    tras_raw = data.get('factor_conversion')

    try:
        precio = float(precio_raw) if precio_raw is not None and str(precio_raw).strip() != '' else None
    except (ValueError, TypeError):
        precio = None

    try:
        tras = float(tras_raw) if tras_raw is not None and str(tras_raw).strip() != '' and float(tras_raw) > 0 else float(product.factor_conversion or 1.0)
    except (ValueError, TypeError):
        tras = float(product.factor_conversion or 1.0)

    costo_u = round(precio / tras, 5) if (precio is not None and tras > 0) else None

    product.precio_compra = precio
    product.factor_conversion = tras
    product.costo_unitario = costo_u
    db.session.commit()

    record_audit('PRODUCTOS', 'QUICK_UPDATE_COST', 'Product', product.id, details={'precio': precio, 'tras': tras, 'costo_unitario': costo_u})

    return jsonify({
        'success': True,
        'message': f"Costo de {product.code} actualizado a ${costo_u} /{product.unidad_consumo or 'CC'}",
        'product': product.to_dict()
    })
