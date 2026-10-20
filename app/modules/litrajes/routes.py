from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app.extensions import db
from app.shared.models import Litraje
from app.shared.audit import record_audit
from app.modules.auth.routes import login_required, permission_required

litrajes_bp = Blueprint('litrajes', __name__)

@litrajes_bp.route('/')
@login_required
@permission_required('catalogos')
def index():
    crop_filter = request.args.get('crop', '').strip()
    
    unique_crops = [r[0] for r in db.session.query(Litraje.crop_name).distinct().order_by(Litraje.crop_name).all()]
    
    query = Litraje.query
    if crop_filter:
        query = query.filter_by(crop_name=crop_filter)
    
    litrajes = query.order_by(Litraje.crop_name.asc(), Litraje.age.asc()).all()
    return render_template('litrajes/index.html', litrajes=litrajes, unique_crops=unique_crops, selected_crop=crop_filter)


@litrajes_bp.route('/crear', methods=['GET', 'POST'])
@login_required
@permission_required('catalogos')
def create():
    if request.method == 'POST':
        crop_name = request.form.get('crop_name', '').strip().upper()
        age = request.form.get('age')
        liters = request.form.get('liters_per_bed')

        if not crop_name or not age or liters is None:
            flash("Todos los campos son obligatorios.", "danger")
            return redirect(url_for('litrajes.index'))

        try:
            age_int = int(age)
            liters_float = float(liters)
        except ValueError:
            flash("Edad y Litros deben ser valores numéricos.", "danger")
            return redirect(url_for('litrajes.index'))

        existing = Litraje.query.filter_by(crop_name=crop_name, age=age_int).first()
        if existing:
            existing.liters_per_bed = liters_float
            db.session.commit()
            flash(f"Litraje para {crop_name} edad {age_int} actualizado a {liters_float} L/cama.", "success")
        else:
            lit = Litraje(crop_name=crop_name, age=age_int, liters_per_bed=liters_float)
            db.session.add(lit)
            db.session.commit()
            flash(f"Litraje para {crop_name} edad {age_int} registrado con {liters_float} L/cama.", "success")

        record_audit('LITRAJES', 'SAVE', 'Litraje', details={'crop': crop_name, 'age': age_int, 'liters': liters_float})
        return redirect(url_for('litrajes.index', crop=crop_name))

    return redirect(url_for('litrajes.index'))


@litrajes_bp.route('/<int:litraje_id>/editar', methods=['POST'])
@login_required
@permission_required('catalogos')
def edit(litraje_id):
    lit = Litraje.query.get_or_404(litraje_id)
    liters = request.form.get('liters_per_bed')
    try:
        lit.liters_per_bed = float(liters)
        db.session.commit()
        record_audit('LITRAJES', 'UPDATE', 'Litraje', lit.id, details={'crop': lit.crop_name, 'age': lit.age, 'liters': lit.liters_per_bed})
        flash(f"Litraje para {lit.crop_name} edad {lit.age} actualizado a {lit.liters_per_bed} L/cama.", "success")
    except (ValueError, TypeError):
        flash("El valor de litros por cama debe ser numérico.", "danger")
    
    return redirect(url_for('litrajes.index', crop=lit.crop_name))


@litrajes_bp.route('/<int:litraje_id>/eliminar', methods=['POST'])
@login_required
@permission_required('catalogos')
def delete(litraje_id):
    lit = Litraje.query.get_or_404(litraje_id)
    crop_name = lit.crop_name
    db.session.delete(lit)
    db.session.commit()
    record_audit('LITRAJES', 'DELETE', 'Litraje', litraje_id, details={'crop': crop_name, 'age': lit.age})
    flash("Registro de litraje eliminado.", "info")
    return redirect(url_for('litrajes.index', crop=crop_name))
