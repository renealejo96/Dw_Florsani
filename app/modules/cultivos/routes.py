from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app.extensions import db
from app.shared.models import Crop
from app.shared.audit import record_audit
from app.modules.auth.routes import login_required, permission_required

cultivos_bp = Blueprint('cultivos', __name__)

@cultivos_bp.route('/')
@login_required
@permission_required('catalogos')
def index():
    crops = Crop.query.order_by(Crop.name.asc()).all()
    return render_template('cultivos/index.html', crops=crops)


@cultivos_bp.route('/api/list')
@login_required
def api_list():
    crops = Crop.query.filter_by(is_active=True).order_by(Crop.name.asc()).all()
    return jsonify([c.to_dict() for c in crops])


@cultivos_bp.route('/crear', methods=['GET', 'POST'])
@login_required
@permission_required('catalogos')
def create():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category = request.form.get('category', 'PRODUCTOS_NUEVOS').strip()
        aliases_str = request.form.get('aliases', '').strip()
        veg_min = request.form.get('veg_min_age', 0)
        veg_max = request.form.get('veg_max_age', 12)
        prod_min = request.form.get('prod_min_age', 13)
        prod_max = request.form.get('prod_max_age', 25)
        notes = request.form.get('notes', '').strip()

        if not name:
            flash("El nombre del cultivo es obligatorio.", "danger")
            return render_template('cultivos/form.html', crop=None)

        existing = Crop.query.filter_by(name=name).first()
        if existing:
            flash(f"Ya existe un cultivo con el nombre '{name}'.", "warning")
            return render_template('cultivos/form.html', crop=None)

        try:
            veg_min_int = int(veg_min)
            veg_max_int = int(veg_max)
            prod_min_int = int(prod_min)
            prod_max_int = int(prod_max)
        except ValueError:
            flash("Los rangos de edad deben ser valores numéricos enteros.", "danger")
            return render_template('cultivos/form.html', crop=None)

        crop = Crop(
            name=name,
            category=category,
            veg_min_age=veg_min_int,
            veg_max_age=veg_max_int,
            prod_min_age=prod_min_int,
            prod_max_age=prod_max_int,
            notes=notes,
            is_active=True
        )
        crop.aliases = aliases_str
        db.session.add(crop)
        db.session.commit()

        record_audit('CULTIVOS', 'CREATE', 'Crop', crop.id, details={'name': name, 'category': category, 'veg_range': f"{veg_min_int}-{veg_max_int}", 'prod_range': f"{prod_min_int}-{prod_max_int}"})
        flash(f"Cultivo '{name}' configurado exitosamente.", "success")
        return redirect(url_for('cultivos.index'))

    return render_template('cultivos/form.html', crop=None)


@cultivos_bp.route('/<int:crop_id>/editar', methods=['GET', 'POST'])
@login_required
@permission_required('catalogos')
def edit(crop_id):
    crop = Crop.query.get_or_404(crop_id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category = request.form.get('category', 'PRODUCTOS_NUEVOS').strip()
        aliases_str = request.form.get('aliases', '').strip()
        veg_min = request.form.get('veg_min_age', 0)
        veg_max = request.form.get('veg_max_age', 12)
        prod_min = request.form.get('prod_min_age', 13)
        prod_max = request.form.get('prod_max_age', 25)
        is_active = True if request.form.get('is_active') == 'on' else False
        notes = request.form.get('notes', '').strip()

        if not name:
            flash("El nombre del cultivo es obligatorio.", "danger")
            return render_template('cultivos/form.html', crop=crop)

        existing = Crop.query.filter(Crop.name == name, Crop.id != crop.id).first()
        if existing:
            flash(f"Ya existe otro cultivo con el nombre '{name}'.", "warning")
            return render_template('cultivos/form.html', crop=crop)

        try:
            crop.veg_min_age = int(veg_min)
            crop.veg_max_age = int(veg_max)
            crop.prod_min_age = int(prod_min)
            crop.prod_max_age = int(prod_max)
        except ValueError:
            flash("Los rangos de edad deben ser números enteros.", "danger")
            return render_template('cultivos/form.html', crop=crop)

        crop.name = name
        crop.category = category
        crop.aliases = aliases_str
        crop.is_active = is_active
        crop.notes = notes

        db.session.commit()
        record_audit('CULTIVOS', 'UPDATE', 'Crop', crop.id, details={'name': name, 'category': category, 'is_active': is_active})
        flash(f"Configuración de cultivo '{crop.name}' actualizada.", "success")
        return redirect(url_for('cultivos.index'))

    return render_template('cultivos/form.html', crop=crop)


@cultivos_bp.route('/<int:crop_id>/toggle-status', methods=['POST'])
@login_required
@permission_required('catalogos')
def toggle_status(crop_id):
    crop = Crop.query.get_or_404(crop_id)
    crop.is_active = not crop.is_active
    db.session.commit()
    record_audit('CULTIVOS', 'UPDATE_STATUS', 'Crop', crop.id, details={'name': crop.name, 'is_active': crop.is_active})
    status_str = "activado" if crop.is_active else "desactivado"
    flash(f"Cultivo '{crop.name}' {status_str}.", "info")
    return redirect(url_for('cultivos.index'))
