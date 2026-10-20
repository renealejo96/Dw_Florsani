import functools
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g
from app.extensions import db
from app.shared.models import User
from app.shared.audit import record_audit

auth_bp = Blueprint('auth', __name__)

FINCAS_MAP = {
    1: 'Pyganflor',
    2: 'Malchingui',
    3: 'Urcuquí'
}

def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login', next=request.url))
        return view(**kwargs)
    return wrapped_view

def admin_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login', next=request.url))
        if session.get('role') != 'ADMIN':
            flash("Acceso restringido: Se requieren permisos de Administrador para esta sección.", "danger")
            return redirect(url_for('dashboard'))
        return view(**kwargs)
    return wrapped_view

def permission_required(*perm_names):
    def decorator(view):
        @functools.wraps(view)
        def wrapped_view(**kwargs):
            if 'user_id' not in session:
                return redirect(url_for('auth.login', next=request.url))
            user = User.query.get(session['user_id'])
            if not user or not user.is_active:
                flash("Acceso denegado: Cuenta de usuario inactiva.", "danger")
                return redirect(url_for('auth.login'))
            if user.role == 'ADMIN':
                return view(**kwargs)
            
            has_any = any(user.has_permission(p) for p in perm_names)
            if not has_any:
                labels = {
                    'tpsr': 'Gestión de Campo y TPSR',
                    'estado_cultivo': 'Censo / Estado de Cultivo',
                    'fumigacion': 'Planificación de Fumigación y Rotaciones',
                    'bodega': 'Módulo de Bodega y Almacén (Solo Órdenes Oficiales)',
                    'ordenes_ver': 'Consulta de Órdenes de Fumigación',
                    'ordenes_imprimir': 'Impresión / Exportación de Órdenes',
                    'salidas_ver': 'Salidas de Bodega y Requisiciones',
                    'salidas_imprimir': 'Exportación e Impresión de Salidas',
                    'aplicaciones_extras': 'Aplicaciones Adicionales y Manchas',
                    'orden_compra': 'Órdenes de Compra',
                    'drench': 'Módulo de Drench',
                    'trichos': 'Módulo de Trichos',
                    'desinfecciones': 'Módulo de Desinfecciones',
                    'catalogos': 'Catálogos y Datos Maestros',
                    'importador': 'Importador de Excel',
                    'cosechas': 'Módulo de Cosechas y Rendimientos',
                    'fenogramas': 'Módulo de Fenogramas'
                }
                needed = ", ".join([labels.get(p, p) for p in perm_names])
                flash(f"Acceso denegado: Tu perfil no tiene habilitado el acceso a '{needed}'.", "warning")
                return redirect(url_for('dashboard'))
            return view(**kwargs)
        return wrapped_view
    return decorator


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        try:
            finca_id = int(request.form.get('finca_id', 1))
        except (ValueError, TypeError):
            finca_id = 1

        clean_user = username.lower().strip()
        user = User.query.filter(db.func.lower(User.username) == clean_user).first()
        if user and user.check_password(password):
            if not user.is_active:
                flash("Esta cuenta de usuario ha sido desactivada. Comuníquese con el Administrador.", "danger")
                return render_template('auth/login.html')

            # Validar si el usuario tiene permiso para la finca seleccionada
            if user.role != 'ADMIN' and user.finca_id != 0 and finca_id not in user.allowed_finca_ids:
                # Conectar a la finca asignada automáticamente si no tiene acceso a la que seleccionó
                finca_id = user.finca_id
                flash(f"Conectado automáticamente a tu sede de trabajo: {user.finca_nombre}.", "info")

            session['user_id'] = user.id
            session['username'] = user.username
            session['full_name'] = user.full_name
            session['role'] = user.role
            session['permissions'] = user.permissions
            session['finca_id'] = finca_id
            session['finca_nombre'] = FINCAS_MAP.get(finca_id, 'Pyganflor')

            record_audit('AUTH', 'LOGIN', 'User', user.id, user=user.username, details={'finca': session['finca_nombre']})
            flash(f"¡Bienvenido de nuevo, {user.full_name}! Conectado a Finca {session['finca_nombre']}.", "success")
            
            next_url = request.args.get('next')
            if not next_url:
                if user.role in ('MONITOR_COSECHA', 'COSECHA'):
                    next_url = url_for('cosechas.index')
                elif user.role in ('EVALUADOR_FENOGRAMA', 'OPERARIO'):
                    next_url = url_for('fenogramas.index')
                elif user.role in ('BODEGUERO', 'BODEGA'):
                    next_url = url_for('bodega.salidas')
                else:
                    next_url = url_for('dashboard')
            return redirect(next_url)
        else:
            flash("Usuario o contraseña incorrectos. Por favor verifique sus credenciales.", "danger")

    return render_template('auth/login.html')


@auth_bp.route('/cambiar-finca/<int:finca_id>')
@login_required
def cambiar_finca(finca_id):
    if finca_id not in FINCAS_MAP:
        flash("Finca solicitada no válida.", "warning")
        return redirect(request.referrer or url_for('dashboard'))

    user = User.query.get(session.get('user_id'))
    if not user:
        return redirect(url_for('auth.login'))

    if user.role != 'ADMIN' and user.finca_id != 0 and finca_id not in user.allowed_finca_ids:
        flash(f"No tienes autorización para cambiar a la Finca {FINCAS_MAP.get(finca_id)}.", "danger")
        return redirect(request.referrer or url_for('dashboard'))

    session['finca_id'] = finca_id
    session['finca_nombre'] = FINCAS_MAP[finca_id]
    record_audit('AUTH', 'SWITCH_FINCA', 'User', user.id, user=user.username, details={'finca': session['finca_nombre']})
    flash(f"Finca activa cambiada a: {session['finca_nombre']}.", "info")
    return redirect(request.referrer or url_for('dashboard'))


@auth_bp.route('/logout')
def logout():
    username = session.get('username')
    session.clear()
    if username:
        record_audit('AUTH', 'LOGOUT', 'User', None, user=username)
    flash("Has cerrado sesión exitosamente.", "info")
    return redirect(url_for('auth.login'))


@auth_bp.route('/usuarios', methods=['GET', 'POST'])
@login_required
@admin_required
def usuarios():
    # Guarantee initial admin exists
    admin_exists = User.query.filter_by(role='ADMIN').first()
    if not admin_exists:
        initial_admin = User(
            username='admin',
            full_name='Administrador Principal',
            role='ADMIN',
            finca_id=0,
            fincas_permitidas_json='[1, 2, 3]',
            permissions=[
                'tpsr', 'estado_cultivo',
                'fumigacion', 'ordenes_ver', 'ordenes_imprimir', 'salidas_ver', 'salidas_imprimir',
                'aplicaciones_extras', 'orden_compra', 'drench', 'trichos', 'desinfecciones',
                'catalogos', 'importador', 'cosechas', 'fenogramas'
            ]
        )
        initial_admin.set_password('admin123')
        db.session.add(initial_admin)
        db.session.commit()

    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        full_name = request.form.get('full_name', '').strip()
        role = request.form.get('role', 'AGRONOMO')
        finca_id = int(request.form.get('finca_id', 1))
        perms = request.form.getlist('permissions')

        if not username or not password:
            flash("El nombre de usuario y la contraseña inicial son obligatorios.", "danger")
            return redirect(url_for('auth.usuarios'))

        existing = User.query.filter_by(username=username).first()
        if existing:
            flash(f"El usuario '@{username}' ya se encuentra registrado en el sistema.", "danger")
            return redirect(url_for('auth.usuarios'))

        fincas_json = json.dumps([1, 2, 3]) if finca_id == 0 else json.dumps([finca_id])

        user = User(
            username=username,
            full_name=full_name or username,
            role=role,
            finca_id=finca_id,
            fincas_permitidas_json=fincas_json,
            permissions=perms
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        record_audit('AUTH', 'CREATE_USER', 'User', user.id, user=session.get('username', 'admin'), details={'new_user': username, 'role': role, 'finca_id': finca_id, 'permissions': perms})
        flash(f"Usuario '@{username}' ({full_name}) creado exitosamente para Finca {user.finca_nombre}.", "success")
        return redirect(url_for('auth.usuarios'))

    users = User.query.order_by(User.created_at.asc()).all()
    return render_template('auth/usuarios.html', users=users)


@auth_bp.route('/usuarios/<int:user_id>/editar', methods=['POST'])
@login_required
@admin_required
def editar_usuario(user_id):
    user = User.query.get_or_404(user_id)
    full_name = request.form.get('full_name', '').strip()
    role = request.form.get('role', user.role)
    password = request.form.get('password', '').strip()
    perms = request.form.getlist('permissions')
    is_active_val = request.form.get('is_active') == '1'
    finca_id_raw = request.form.get('finca_id')

    if full_name:
        user.full_name = full_name

    if finca_id_raw is not None:
        user.finca_id = int(finca_id_raw)
        user.fincas_permitidas_json = json.dumps([1, 2, 3]) if user.finca_id == 0 else json.dumps([user.finca_id])

    # Only change role if not the main admin
    if user.username != 'admin':
        user.role = role
        user.is_active = is_active_val

    user.permissions = perms

    if password:
        user.set_password(password)

    db.session.commit()

    # If updating self, sync session
    if session.get('user_id') == user.id:
        session['full_name'] = user.full_name
        session['role'] = user.role
        session['permissions'] = user.permissions
        if user.finca_id != 0:
            session['finca_id'] = user.finca_id
            session['finca_nombre'] = user.finca_nombre

    record_audit('AUTH', 'UPDATE_USER', 'User', user.id, user=session.get('username', 'admin'), details={'updated_user': user.username, 'role': user.role, 'finca_id': user.finca_id, 'permissions': perms})
    flash(f"Datos y permisos del usuario '@{user.username}' actualizados correctamente.", "success")
    return redirect(url_for('auth.usuarios'))


@auth_bp.route('/usuarios/<int:user_id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.username == 'admin' and user.is_active:
        flash("No se puede desactivar al administrador principal del sistema.", "danger")
        return redirect(url_for('auth.usuarios'))

    user.is_active = not user.is_active
    db.session.commit()
    
    status_text = "ACTIVADA" if user.is_active else "DESACTIVADA"
    record_audit('AUTH', 'TOGGLE_USER_STATUS', 'User', user.id, user=session.get('username', 'admin'), details={'user': user.username, 'is_active': user.is_active})
    flash(f"La cuenta '@{user.username}' ha sido {status_text}.", "info")
    return redirect(url_for('auth.usuarios'))


@auth_bp.route('/usuarios/<int:user_id>/eliminar', methods=['POST'])
@login_required
@admin_required
def eliminar_usuario(user_id):
    user = User.query.get_or_404(user_id)
    if user.username == 'admin':
        flash("No se puede eliminar al usuario administrador raíz.", "danger")
        return redirect(url_for('auth.usuarios'))

    if session.get('user_id') == user.id:
        flash("No puedes eliminar tu propia cuenta mientras estás conectado.", "danger")
        return redirect(url_for('auth.usuarios'))

    u_name = user.username
    db.session.delete(user)
    db.session.commit()

    record_audit('AUTH', 'DELETE_USER', 'User', user_id, user=session.get('username', 'admin'), details={'deleted_user': u_name})
    flash(f"Usuario '@{u_name}' eliminado permanentemente del sistema.", "warning")
    return redirect(url_for('auth.usuarios'))
