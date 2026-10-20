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
