from math import ceil
from datetime import datetime, timedelta
import io
import csv
from io import BytesIO
from collections import defaultdict
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify, session, Response

from sqlalchemy import text
from app.extensions import db
from app.shared.models import RegistroCosecha, Estimado, User
from app.shared.audit import record_audit
from app.modules.auth.routes import login_required, permission_required
from .utils import (
    ZONA_HORARIA, formato_semana, cargar_variedades, guardar_variedades,
    cargar_responsables, obtener_monitores_sistema, cargar_modulos, cargar_maestro_productos,
    cargar_relaciones_maestro, guardar_relaciones_maestro,
    cargar_productos_maestros, guardar_productos_maestros,
    construir_productos_con_variedades, sincronizar_variedad_a_bd, sincronizar_producto_maestro_a_bd,
    semanas_en_anio, parse_semana_to_range
)

cosechas_bp = Blueprint('cosechas', __name__)
bp_cosechas = cosechas_bp

def _homologar_sem(reg):
    if hasattr(reg, 'semana') and reg.semana:
        s_val = int(reg.semana)
        return str(s_val % 100 if s_val >= 100 else s_val)
    try:
        f_val = int(formato_semana(reg.fecha))
        return str(f_val % 100)
    except Exception:
        return "1"



@cosechas_bp.route('/')
@login_required
@permission_required('cosechas')
def index():
    """Listado paginado y filtrable de registros de cosecha"""
    page = request.args.get('page', 1, type=int)
    per_page = 100
    
    filtro_fecha = request.args.get('fecha', '').strip()
    filtro_producto = request.args.get('producto_maestro', '').strip()
    filtro_variedad = request.args.get('variedad', '').strip()
    filtro_modulo = request.args.get('modulo', '').strip()
    filtro_responsable = request.args.get('responsable', '').strip()
    filtro_semana = request.args.get('semana', '').strip()
    
    query = RegistroCosecha.query
    
    if filtro_fecha:
        try:
            fecha_filtro = datetime.strptime(filtro_fecha, '%Y-%m-%d').date()
            query = query.filter(RegistroCosecha.fecha == fecha_filtro)
        except Exception:
            pass
            
    maestro_map = cargar_maestro_productos()
    todos_productos = sorted(list(set(maestro_map.values()))) if maestro_map else []
    
    if filtro_producto:
        vars_del_producto = [v for v, p in maestro_map.items() if p.upper() == filtro_producto.upper()]
        if vars_del_producto:
            query = query.filter(RegistroCosecha.variedad.in_(vars_del_producto))
        else:
            query = query.filter(db.false())
    
    if filtro_variedad:
        query = query.filter(RegistroCosecha.variedad.ilike(f'%{filtro_variedad}%'))
    
    if filtro_modulo:
        query = query.filter(RegistroCosecha.modulo.ilike(f'%{filtro_modulo}%'))
    
    if filtro_responsable:
        query = query.filter(RegistroCosecha.responsable.ilike(f'%{filtro_responsable}%'))
    
    if filtro_semana:
        try:
            query = query.filter(RegistroCosecha.semana == int(filtro_semana))
        except Exception:
            pass
    
    query = query.order_by(
        RegistroCosecha.fecha_creacion.desc(),
        RegistroCosecha.fecha.desc(), 
        RegistroCosecha.hora.desc()
    )
    
    total_registros = query.count()
    total_pages = max(1, ceil(total_registros / per_page))
    
    registros = query.offset((page - 1) * per_page).limit(per_page).all()
    
    # Resumen de estadísticas rápidas para la cabecera
    from sqlalchemy import func
    stats_query = query.order_by(None).with_entities(
        func.coalesce(func.sum(RegistroCosecha.total_tallos), 0),
        func.coalesce(func.sum(RegistroCosecha.mallas), 0)
    ).first()
    sum_tallos = stats_query[0] if stats_query else 0
    sum_mallas = stats_query[1] if stats_query else 0

    # Cómputo de tallos agrupados por Producto Maestro
    tallos_por_var = query.order_by(None).with_entities(
        RegistroCosecha.variedad,
        func.coalesce(func.sum(RegistroCosecha.total_tallos), 0)
    ).group_by(RegistroCosecha.variedad).all()

    tallos_por_prod_map = defaultdict(int)
    for var_name, t_count in tallos_por_var:
        p_maestro = maestro_map.get(var_name, 'OTROS').upper().strip()
        tallos_por_prod_map[p_maestro] += int(t_count or 0)

    color_theme_map = {
        'GYPSOPHILA': {'border': 'border-primary', 'icon': 'fa-spa', 'badge_class': 'bg-primary-subtle text-primary', 'text_class': 'text-primary'},
        'HYPERICUM': {'border': 'border-success', 'icon': 'fa-seedling', 'badge_class': 'bg-success-subtle text-success', 'text_class': 'text-success'},
        'LIMONIUM': {'border': 'border-info', 'icon': 'fa-leaf', 'badge_class': 'bg-info-subtle text-info', 'text_class': 'text-info'},
        'EUCALIPTO': {'border': 'border-secondary', 'icon': 'fa-tree', 'badge_class': 'bg-secondary-subtle text-secondary', 'text_class': 'text-secondary'},
        'OTROS': {'border': 'border-warning', 'icon': 'fa-boxes-stacked', 'badge_class': 'bg-warning-subtle text-warning', 'text_class': 'text-warning'}
    }

    resumen_productos = []
    for prod_name, t_tot in sorted(tallos_por_prod_map.items(), key=lambda x: x[1], reverse=True):
        if t_tot > 0 or len(tallos_por_prod_map) <= 3:
            pct_p = round((t_tot / sum_tallos * 100), 1) if sum_tallos > 0 else 0.0
            th = color_theme_map.get(prod_name, {'border': 'border-secondary', 'icon': 'fa-boxes-stacked', 'badge_class': 'bg-light text-dark', 'text_class': 'text-dark'})
            resumen_productos.append({
                'producto': prod_name,
                'tallos': t_tot,
                'pct': pct_p,
                'theme': th
            })

    # Listas para filtros dinámicos
    todas_variedades = sorted(list({r[0] for r in db.session.query(RegistroCosecha.variedad).distinct().all() if r[0]}))
    todos_modulos = sorted(list({r[0] for r in db.session.query(RegistroCosecha.modulo).distinct().all() if r[0]}))
    todos_responsables = sorted(list({r[0] for r in db.session.query(RegistroCosecha.responsable).distinct().all() if r[0]}))
    todas_semanas = sorted(list({r[0] for r in db.session.query(RegistroCosecha.semana).distinct().all() if r[0]}), reverse=True)
    
    return render_template(
        'cosechas/index.html', 
        registros=registros,
        page=page,
        total_pages=total_pages,
        total_registros=total_registros,
        sum_tallos=sum_tallos,
        sum_mallas=sum_mallas,
        resumen_productos=resumen_productos,
        per_page=per_page,
        filtro_fecha=filtro_fecha,
        filtro_producto=filtro_producto,
        todos_productos=todos_productos,
        filtro_variedad=filtro_variedad,
        filtro_modulo=filtro_modulo,
        filtro_responsable=filtro_responsable,
        filtro_semana=filtro_semana,
        todas_variedades=todas_variedades,
        todos_modulos=todos_modulos,
        todos_responsables=todos_responsables,
        todas_semanas=todas_semanas
    )


import re

def limpiar_nombre_modulo(mod_raw):
    """Garantiza que el módulo o bloque se almacene puro sin prefijos como Mod, Modulo, Bloque"""
    if not mod_raw:
        return ''
    m = str(mod_raw).strip()
    m = re.sub(r'^(mod\.?|modulo|bloque|blq\.?)\s*', '', m, flags=re.IGNORECASE)
    return m.strip()

@cosechas_bp.route('/nuevo', methods=['GET', 'POST'])
@cosechas_bp.route('/nuevo-registro', methods=['GET', 'POST'])
@login_required
@permission_required('cosechas')
def nuevo_registro():
    """Formulario interactivo para registrar cosecha"""
    from flask import session
    from app.shared.models import User

    productos_con_variedades = construir_productos_con_variedades()
    monitores_sistema = obtener_monitores_sistema()
    lista_responsables = cargar_responsables()

    # Identificar el responsable / monitor por defecto desde la sesión actual
    u_sesion = User.query.get(session.get('user_id')) if session.get('user_id') else None
    responsable_sesion = (u_sesion.full_name if u_sesion else None) or session.get('full_name') or session.get('username') or ''

    if request.method == 'POST':
        try:
            fecha_str = request.form.get('fecha', '').strip()
            fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            viaje = request.form.get('viaje', '').strip()
            
            # Blindaje estricto: la semana siempre deriva de la fecha real (ISO week 1..53)
            iso_year, iso_week, _ = fecha.isocalendar()
            semana = iso_week
            
            semana_raw = int(request.form.get('semana', 0)) if request.form.get('semana') else 0
            if semana_raw and (semana_raw % 100) != iso_week:
                flash(f'Aviso: La fecha seleccionada ({fecha.strftime("%d/%m/%Y")}) corresponde a la Semana {iso_week}. Se guardó en la semana correspondiente para evitar incongruencias.', 'info')
            tallos = int(request.form.get('tallos', 0))
            mallas = int(request.form.get('mallas', 0))
            modulo = limpiar_nombre_modulo(request.form.get('modulo', ''))
            producto_maestro = request.form.get('producto_maestro', '').strip().upper()
            variedad = request.form.get('variedad', '').strip().upper()

            # Bloqueo estricto del usuario / responsable a la sesión activa para garantizar trazabilidad
            responsable = (u_sesion.full_name if u_sesion else None) or responsable_sesion or request.form.get('responsable', '').strip() or 'Responsable'

            if not producto_maestro:
                flash('Debe seleccionar un producto maestro.', 'danger')
                return render_template('cosechas/nuevo.html',
                    variedades=cargar_variedades(),
                    responsables=lista_responsables,
                    monitores_sistema=monitores_sistema,
                    responsable_sesion=responsable_sesion,
                    modulos=cargar_modulos(),
                    productos_con_variedades=productos_con_variedades,
                    producto_seleccionado=producto_maestro,
                    variedad_seleccionada=variedad
                )

            if not variedad:
                flash('Debe seleccionar una variedad.', 'danger')
                return render_template('cosechas/nuevo.html',
                    variedades=cargar_variedades(),
                    responsables=lista_responsables,
                    monitores_sistema=monitores_sistema,
                    responsable_sesion=responsable_sesion,
                    modulos=cargar_modulos(),
                    productos_con_variedades=productos_con_variedades,
                    producto_seleccionado=producto_maestro,
                    variedad_seleccionada=variedad
                )

            variedades_producto = productos_con_variedades.get(producto_maestro, [])
            if variedades_producto and variedad not in variedades_producto:
                flash('La variedad seleccionada no corresponde al producto maestro elegido.', 'warning')
            
            # Hora oficial en Ecuador
            hora_actual = datetime.now(ZONA_HORARIA).time()
            total_tallos = tallos * mallas
            
            nuevo = RegistroCosecha(
                fecha=fecha,
                viaje=viaje,
                semana=semana,
                tallos=tallos,
                mallas=mallas,
                modulo=modulo,
                total_tallos=total_tallos,
                variedad=variedad,
                hora=hora_actual,
                responsable=responsable
            )
            
            db.session.add(nuevo)
            db.session.commit()
            
            record_audit('COSECHAS', 'CREATE', 'RegistroCosecha', nuevo.id, 
                         details=f"Variedad: {variedad}, Módulo: {modulo}, Total: {total_tallos} tallos, Monitor: {responsable}")
            
            flash(f'¡Registro de {total_tallos:,} tallos de {variedad} guardado exitosamente por {responsable}!', 'success')
            return redirect(url_for('cosechas.nuevo_registro'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al crear el registro: {str(e)}', 'danger')
            return render_template(
                'cosechas/nuevo.html', 
                variedades=cargar_variedades(), 
                responsables=lista_responsables,
                monitores_sistema=monitores_sistema,
                responsable_sesion=responsable_sesion,
                modulos=cargar_modulos(),
                productos_con_variedades=productos_con_variedades,
                producto_seleccionado=request.form.get('producto_maestro', '').strip(),
                variedad_seleccionada=request.form.get('variedad', '').strip()
            )
    
    return render_template(
        'cosechas/nuevo.html', 
        variedades=cargar_variedades(), 
        responsables=lista_responsables,
        monitores_sistema=monitores_sistema,
        responsable_sesion=responsable_sesion,
        modulos=cargar_modulos(),
        productos_con_variedades=productos_con_variedades,
        producto_seleccionado='',
        variedad_seleccionada=''
    )


@cosechas_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@permission_required('cosechas')
def editar_registro(id):
    """Editar un registro de cosecha existente"""
    registro = RegistroCosecha.query.get_or_404(id)
    productos_con_variedades = construir_productos_con_variedades()
    maestro_map = cargar_maestro_productos()
    producto_actual = maestro_map.get(registro.variedad, '')
    monitores_sistema = obtener_monitores_sistema()
    lista_responsables = cargar_responsables()
    
    if request.method == 'POST':
        try:
            registro.fecha = datetime.strptime(request.form['fecha'], '%Y-%m-%d').date()
            registro.viaje = request.form['viaje'].strip()
            # Blindaje: semana en formato 1..53 homologada con la fecha real
            iso_year, iso_week, _ = registro.fecha.isocalendar()
            registro.semana = iso_week
            registro.tallos = int(request.form['tallos'])
            registro.mallas = int(request.form['mallas'])
            registro.modulo = limpiar_nombre_modulo(request.form['modulo'].strip())
            registro.variedad = request.form['variedad'].strip().upper()
            u_sesion = User.query.get(session.get('user_id')) if session.get('user_id') else None
            if u_sesion and u_sesion.role == 'ADMIN':
                registro.responsable = request.form.get('responsable', '').strip() or registro.responsable
            # Si no es ADMIN, se preserva intacto el responsable original del registro
            registro.total_tallos = registro.tallos * registro.mallas
            
            db.session.commit()
            record_audit('COSECHAS', 'UPDATE', 'RegistroCosecha', registro.id, 
                         details=f"Actualizado: {registro.variedad}, {registro.total_tallos} tallos, Monitor: {registro.responsable}")
            
            flash('¡Registro actualizado exitosamente!', 'success')
            return redirect(url_for('cosechas.index'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar el registro: {str(e)}', 'danger')
    
    return render_template(
        'cosechas/editar.html', 
        registro=registro,
        variedades=cargar_variedades(), 
        responsables=lista_responsables,
        monitores_sistema=monitores_sistema,
        modulos=cargar_modulos(),
        productos_con_variedades=productos_con_variedades,
        producto_actual=producto_actual
    )


@cosechas_bp.route('/eliminar/<int:id>')
@login_required
@permission_required('cosechas')
def eliminar_registro(id):
    """Eliminar un registro de cosecha"""
    registro = RegistroCosecha.query.get_or_404(id)
    try:
        reg_id = registro.id
        desc = f"Variedad: {registro.variedad}, {registro.total_tallos} tallos, Módulo: {registro.modulo}"
        db.session.delete(registro)
        db.session.commit()
        record_audit('COSECHAS', 'DELETE', 'RegistroCosecha', reg_id, details=desc)
        flash('¡Registro de cosecha eliminado correctamente!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar el registro: {str(e)}', 'danger')
    
    return redirect(url_for('cosechas.index'))


@cosechas_bp.route('/catalogos', methods=['GET', 'POST'])
@login_required
@permission_required('cosechas')
def catalogos():
    """Administrar productos maestros, variedades y sus relaciones"""
    if request.method == 'POST':
        accion = request.form.get('accion', '').strip()

        try:
            if accion == 'crear_producto':
                producto = request.form.get('producto_maestro', '').strip().upper()
                if not producto:
                    flash('Debe ingresar el nombre del producto maestro.', 'danger')
                else:
                    productos_existentes = {p.strip().upper() for p in cargar_productos_maestros()}
                    if producto in productos_existentes:
                        flash(f'El producto maestro "{producto}" ya existe en el catálogo.', 'warning')
                    else:
                        productos_existentes.add(producto)
                        guardar_productos_maestros(sorted(list(productos_existentes)))
                        record_audit('COSECHAS', 'PRODUCTO_CREATE', 'Catalogo', None, details=f"Producto maestro '{producto}' creado")
                        flash(f'Producto maestro "{producto}" creado exitosamente.', 'success')

            elif accion == 'crear_variedad':
                producto = request.form.get('producto_maestro', '').strip().upper()
                variedad = request.form.get('variedad', '').strip().upper()

                if not producto or not variedad:
                    flash('Debe seleccionar el producto maestro e ingresar el nombre de la variedad.', 'danger')
                else:
                    relaciones = cargar_relaciones_maestro()
                    mapa_variedad = {var.strip().upper(): prod.strip().upper() for var, prod in relaciones}
                    
                    import re
                    def norm_var(s):
                        return re.sub(r'[^A-Z0-9]', '', (s or '').upper())
                    mapa_norm = {norm_var(var): (var, prod) for var, prod in mapa_variedad.items()}

                    clave_variedad = variedad.upper()
                    norm_clave = norm_var(clave_variedad)

                    colision = None
                    if clave_variedad in mapa_variedad:
                        colision = (clave_variedad, mapa_variedad[clave_variedad])
                    elif norm_clave and norm_clave in mapa_norm:
                        colision = mapa_norm[norm_clave]

                    if colision:
                        var_existente, cultivo_actual = colision
                        if cultivo_actual == producto:
                            flash(f'La variedad "{variedad}" ya se encuentra asignada al cultivo "{producto}".', 'warning')
                        else:
                            flash(f'No se puede agregar la variedad "{variedad}" a "{producto}" porque ya está asignada al cultivo "{cultivo_actual}" (registrada como "{var_existente}"). Para cambiarla de cultivo, use la opción de editar en su tarjeta o elimínela primero de "{cultivo_actual}".', 'danger')
                    else:
                        try:
                            from sqlalchemy import text
                            db.session.execute(text("DELETE FROM cat_variedades_eliminadas WHERE UPPER(TRIM(variedad)) = :v"), {'v': variedad})
                            db.session.commit()
                        except Exception:
                            db.session.rollback()

                        relaciones_actualizadas = [(var.strip().upper(), prod.strip().upper()) for var, prod in relaciones]
                        relaciones_actualizadas.append((variedad, producto))
                        guardar_relaciones_maestro(relaciones_actualizadas)

                        productos = set(cargar_productos_maestros())
                        productos.add(producto)
                        guardar_productos_maestros(sorted(list(productos)))

                        variedades = set(cargar_variedades())
                        variedades.add(variedad)
                        guardar_variedades(sorted(list(variedades)))

                        record_audit('COSECHAS', 'VARIEDAD_CREATE', 'Catalogo', None,
                                     details=f"Variedad '{variedad}' vinculada al cultivo '{producto}'")
                        flash(f'Variedad "{variedad}" vinculada exitosamente con "{producto}".', 'success')

            elif accion == 'editar_variedad':
                var_original = request.form.get('variedad_original', '').strip().upper()
                prod_original = request.form.get('producto_original', '').strip().upper()
                var_nueva = request.form.get('variedad_nueva', '').strip().upper()
                prod_nuevo = request.form.get('producto_maestro', '').strip().upper()
                actualizar_historico = (request.form.get('actualizar_historico') == '1')

                if not var_original or not var_nueva or not prod_nuevo:
                    flash('Debe especificar el nombre original, el nuevo nombre y el producto maestro.', 'danger')
                else:
                    relaciones = cargar_relaciones_maestro()
                    mapa_variedad = {var.strip().upper(): prod.strip().upper() for var, prod in relaciones}
                    
                    import re
                    def norm_var(s):
                        return re.sub(r'[^A-Z0-9]', '', (s or '').upper())
                    mapa_norm = {norm_var(var): (var, prod) for var, prod in mapa_variedad.items() if var != var_original}

                    # Si cambió el nombre de la variedad, verificar que no colisione con otra variedad asignada
                    norm_nueva = norm_var(var_nueva)
                    colision = None
                    if var_nueva != var_original:
                        if var_nueva in mapa_variedad:
                            colision = (var_nueva, mapa_variedad[var_nueva])
                        elif norm_nueva and norm_nueva in mapa_norm:
                            colision = mapa_norm[norm_nueva]

                    if colision:
                        var_existente, cultivo_existente = colision
                        flash(f'No se puede renombrar a "{var_nueva}" porque ya existe asignada al cultivo "{cultivo_existente}" (como "{var_existente}").', 'danger')
                    else:
                        try:
                            from sqlalchemy import text
                            db.session.execute(text("DELETE FROM cat_variedades_eliminadas WHERE UPPER(TRIM(variedad)) = :v"), {'v': var_nueva})
                            db.session.commit()
                        except Exception:
                            db.session.rollback()

                        # 1. Actualizar archivo MAESTRO_PROS.csv y BD
                        relaciones_actualizadas = [
                            (var.strip().upper(), prod.strip().upper())
                            for var, prod in relaciones
                            if var.strip().upper() != var_original and var.strip().upper() != var_nueva
                        ]
                        relaciones_actualizadas.append((var_nueva, prod_nuevo))
                        guardar_relaciones_maestro(relaciones_actualizadas)

                        # 2. Actualizar archivo variedades.csv
                        variedades_set = {v.strip().upper() for v in cargar_variedades() if v.strip().upper() != var_original}
                        variedades_set.add(var_nueva)
                        guardar_variedades(sorted(list(variedades_set)))

                        # 3. Asegurar que prod_nuevo esté en productos maestros
                        productos_set = {p.strip().upper() for p in cargar_productos_maestros()}
                        productos_set.add(prod_nuevo)
                        guardar_productos_maestros(sorted(list(productos_set)))

                        # 4. Sincronización en Base de Datos (Registros históricos, Estimados, Eventos y Crop State)
                        regs_actualizados = 0
                        ests_actualizados = 0
                        try:
                            from sqlalchemy import text
                            db.session.execute(
                                text("UPDATE fact_eventos_cultivo SET producto_maestro = :pnew, variedad = :vnew WHERE UPPER(TRIM(variedad)) = :vold"),
                                {'pnew': prod_nuevo, 'vnew': var_nueva, 'vold': var_original}
                            )
                            db.session.execute(
                                text("UPDATE crop_state_records SET crop_master = :pnew, variety = :vnew WHERE UPPER(TRIM(variety)) = :vold"),
                                {'pnew': prod_nuevo, 'vnew': var_nueva, 'vold': var_original}
                            )

                            if actualizar_historico:
                                regs = RegistroCosecha.query.filter(RegistroCosecha.variedad.ilike(var_original)).all()
                                for r in regs:
                                    r.variedad = var_nueva
                                    regs_actualizados += 1

                                ests = Estimado.query.filter(Estimado.variedad.ilike(var_original)).all()
                                for e in ests:
                                    e.variedad = var_nueva
                                    if prod_nuevo:
                                        e.producto_maestro = prod_nuevo
                                    ests_actualizados += 1

                            db.session.execute(text("DELETE FROM cat_variedades_eliminadas WHERE UPPER(TRIM(variedad)) = :v"), {'v': var_nueva})
                            db.session.commit()
                        except Exception as e:
                            print(f"Error sincronizando registros históricos: {e}")

                        record_audit(
                            'COSECHAS', 'VARIEDAD_EDIT', 'Catalogo', None,
                            details=f"Variedad editada '{var_original}' -> '{var_nueva}' (Prod: '{prod_original}' -> '{prod_nuevo}'). Registros BD: {regs_actualizados}, Estimados BD: {ests_actualizados}"
                        )

                        if prod_original != prod_nuevo and var_original == var_nueva:
                            msg = f'Variedad "{var_original}" reasignada exitosamente de "{prod_original}" a "{prod_nuevo}".'
                        else:
                            msg = f'Variedad "{var_original}" modificada exitosamente a "{var_nueva}" (Producto Maestro: "{prod_nuevo}").'
                        
                        if actualizar_historico and (regs_actualizados > 0 or ests_actualizados > 0):
                            msg += f' Se actualizaron {regs_actualizados} registros históricos de cosecha y {ests_actualizados} estimados.'
                        flash(msg, 'success')

            elif accion == 'eliminar_relacion':
                variedad = request.form.get('variedad', '').strip().upper()
                producto = request.form.get('producto_maestro', '').strip().upper()

                if not variedad:
                    flash('Debe especificar la variedad a eliminar.', 'danger')
                else:
                    from sqlalchemy import text

                    # 1. Registrar permanentemente en tabla de lápidas (tombstones) cat_variedades_eliminadas
                    try:
                        db.session.execute(text("""
                            CREATE TABLE IF NOT EXISTS cat_variedades_eliminadas (
                                id SERIAL PRIMARY KEY,
                                variedad VARCHAR(150) NOT NULL UNIQUE,
                                producto_maestro VARCHAR(150),
                                deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            );
                        """))
                        db.session.execute(text("""
                            INSERT INTO cat_variedades_eliminadas (variedad, producto_maestro)
                            VALUES (:v, :p)
                            ON CONFLICT (variedad) DO NOTHING;
                        """), {'v': variedad, 'p': producto})
                        db.session.commit()
                    except Exception as e_tomb:
                        db.session.rollback()
                        print(f"Error registrando tombstone en BD: {e_tomb}")

                    # 2. Forzar eliminación estricta en todas las tablas de base de datos PostgreSQL
                    try:
                        db.session.execute(text("DELETE FROM cat_variedades_maestro WHERE UPPER(TRIM(variedad)) = :v"), {'v': variedad})
                        db.session.execute(text("DELETE FROM dim_variedades WHERE UPPER(TRIM(variedad)) = :v"), {'v': variedad})
                        db.session.execute(text("DELETE FROM dim_productos WHERE UPPER(TRIM(variedad)) = :v"), {'v': variedad})
                        db.session.execute(text("DELETE FROM variedades WHERE UPPER(TRIM(nombre)) = :v"), {'v': variedad})
                        db.session.execute(text("DELETE FROM crop_state_records WHERE UPPER(TRIM(variety)) = :v"), {'v': variedad})
                        db.session.execute(text("DELETE FROM fact_eventos_cultivo WHERE UPPER(TRIM(variedad)) = :v"), {'v': variedad})
                        db.session.commit()
                    except Exception as e_del:
                        db.session.rollback()
                        print(f"Error borrando variedad de base de datos: {e_del}")

                    # 3. Guardar relaciones en CSV canónico MAESTRO_PROS.csv excluyendo la variedad eliminada
                    relaciones = cargar_relaciones_maestro()
                    relaciones_nuevas = [
                        (var.strip().upper(), prod.strip().upper())
                        for var, prod in relaciones
                        if var.strip().upper() != variedad
                    ]
                    guardar_relaciones_maestro(relaciones_nuevas)

                    # 4. Remover de variedades.csv y sincronizar
                    variedades_set = {v.strip().upper() for v in cargar_variedades() if v.strip().upper() != variedad}
                    guardar_variedades(sorted(list(variedades_set)))

                    record_audit('COSECHAS', 'VARIEDAD_DELETE', 'Catalogo', None,
                                 details=f"Variedad '{variedad}' eliminada permanentemente del catálogo")
                    if producto:
                        flash(f'Variedad "{variedad}" eliminada de "{producto}".', 'success')
                    else:
                        flash(f'Variedad "{variedad}" eliminada permanentemente del catálogo.', 'success')

            elif accion == 'editar_producto':
                prod_original = request.form.get('producto_original', '').strip().upper()
                prod_nuevo = request.form.get('producto_nuevo', '').strip().upper()
                actualizar_historico = (request.form.get('actualizar_historico') == '1')

                if not prod_original or not prod_nuevo:
                    flash('Debe especificar el nombre actual y el nuevo nombre del producto maestro.', 'danger')
                else:
                    # Actualizar productos_maestros.csv y BD
                    prods = {p for p in cargar_productos_maestros() if p.upper() != prod_original}
                    prods.add(prod_nuevo)
                    guardar_productos_maestros(sorted(list(prods)))

                    # Actualizar MAESTRO_PROS.csv y BD
                    relaciones = cargar_relaciones_maestro()
                    relaciones_nuevas = [
                        (var, prod_nuevo if prod.upper() == prod_original else prod)
                        for var, prod in relaciones
                    ]
                    guardar_relaciones_maestro(relaciones_nuevas)

                    try:
                        from sqlalchemy import text
                        db.session.execute(text("UPDATE cat_variedades_maestro SET producto_maestro = :pnew WHERE UPPER(producto_maestro) = :pold"), {'pnew': prod_nuevo, 'pold': prod_original})
                        db.session.execute(text("UPDATE dim_prod_maestros SET producto_maestro = :pnew WHERE UPPER(producto_maestro) = :pold"), {'pnew': prod_nuevo, 'pold': prod_original})
                        db.session.commit()
                    except Exception:
                        pass

                    if actualizar_historico:
                        Estimado.query.filter(Estimado.producto_maestro.ilike(prod_original)).update(
                            {Estimado.producto_maestro: prod_nuevo}, synchronize_session=False
                        )
                        db.session.commit()

                    record_audit('COSECHAS', 'PRODUCTO_EDIT', 'Catalogo', None,
                                 details=f"Producto maestro '{prod_original}' renombrado a '{prod_nuevo}'")
                    flash(f'Producto maestro "{prod_original}" renombrado a "{prod_nuevo}".', 'success')

            elif accion == 'eliminar_producto':
                producto = request.form.get('producto_maestro', '').strip().upper()
                relaciones = cargar_relaciones_maestro()
                vars_asoc = [var for var, prod in relaciones if prod.upper() == producto]
                if vars_asoc:
                    flash(f'No se puede eliminar "{producto}" porque tiene {len(vars_asoc)} variedades asociadas. Reasigne o elimine primero sus variedades.', 'warning')
                else:
                    prods = {p for p in cargar_productos_maestros() if p.upper() != producto}
                    guardar_productos_maestros(sorted(list(prods)))
                    try:
                        from sqlalchemy import text
                        db.session.execute(text("DELETE FROM dim_prod_maestros WHERE UPPER(producto_maestro) = :p"), {'p': producto})
                        db.session.execute(text("DELETE FROM cat_variedades_maestro WHERE UPPER(producto_maestro) = :p"), {'p': producto})
                        db.session.commit()
                    except Exception as e_del_prod:
                        db.session.rollback()
                        print(f"Error eliminando producto maestro: {e_del_prod}")
                    record_audit('COSECHAS', 'PRODUCTO_DELETE', 'Catalogo', None, details=f"Producto '{producto}' eliminado")
                    flash(f'Producto maestro "{producto}" eliminado exitosamente.', 'success')

            else:
                flash('Acción no válida solicitada.', 'danger')

        except Exception as e:
            flash(f'Error al procesar catálogo: {str(e)}', 'danger')

        return redirect(url_for('cosechas.catalogos'))

    productos_con_variedades = construir_productos_con_variedades()
    total_variedades = sum(len(variedades) for variedades in productos_con_variedades.values())

    # Mapas serializables para validación instantánea de duplicados en el cliente
    import json
    mapa_variedades_dict = {}
    for prod_k, vars_list in productos_con_variedades.items():
        for v_item in vars_list:
            mapa_variedades_dict[v_item.strip().upper()] = prod_k.strip().upper()
    mapa_variedades_json = json.dumps(mapa_variedades_dict)
    
    lista_prods = sorted(productos_con_variedades.keys(), key=lambda value: value.upper())
    productos_maestros_json = json.dumps(lista_prods)

    return render_template(
        'cosechas/catalogos.html',
        productos_con_variedades=productos_con_variedades,
        productos_maestros=lista_prods,
        total_productos=len(productos_con_variedades),
        total_variedades=total_variedades,
        mapa_variedades_json=mapa_variedades_json,
        productos_maestros_json=productos_maestros_json
    )


@cosechas_bp.route('/reportes')
@login_required
@permission_required('cosechas')
def reportes():
    """Reportes detallados y tablero analítico con gráficos interactivos"""
    from sqlalchemy import func
    
    fecha_desde = request.args.get('fecha_desde', '').strip()
    fecha_hasta = request.args.get('fecha_hasta', '').strip()
    
    mostrar_resultados = bool(fecha_desde and fecha_hasta)
    
    resumen_variedad = []
    total_registros = 0
    total_tallos = 0
    total_mallas = 0
    variedades_unicas = 0
    
    if mostrar_resultados:
        try:
            fecha_desde_dt = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            fecha_hasta_dt = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
            fecha_hasta_inclusiva = fecha_hasta_dt + timedelta(days=1)
            
            query_base = RegistroCosecha.query.filter(
                RegistroCosecha.fecha >= fecha_desde_dt,
                RegistroCosecha.fecha < fecha_hasta_inclusiva
            )
            
            resumen_variedad = db.session.query(
                RegistroCosecha.variedad,
                func.count(RegistroCosecha.id).label('total_registros'),
                func.sum(RegistroCosecha.total_tallos).label('total_tallos'),
                func.sum(RegistroCosecha.mallas).label('total_mallas'),
                func.avg(RegistroCosecha.total_tallos).label('promedio_tallos')
            ).filter(
                RegistroCosecha.fecha >= fecha_desde_dt,
                RegistroCosecha.fecha < fecha_hasta_inclusiva
            ).group_by(RegistroCosecha.variedad).order_by(
                func.sum(RegistroCosecha.total_tallos).desc()
            ).all()
            
            total_registros = query_base.count()
            total_tallos = db.session.query(func.sum(RegistroCosecha.total_tallos)).filter(
                RegistroCosecha.fecha >= fecha_desde_dt,
                RegistroCosecha.fecha < fecha_hasta_inclusiva
            ).scalar() or 0
            
            total_mallas = db.session.query(func.sum(RegistroCosecha.mallas)).filter(
                RegistroCosecha.fecha >= fecha_desde_dt,
                RegistroCosecha.fecha < fecha_hasta_inclusiva
            ).scalar() or 0
            
            variedades_unicas = db.session.query(func.count(func.distinct(RegistroCosecha.variedad))).filter(
                RegistroCosecha.fecha >= fecha_desde_dt,
                RegistroCosecha.fecha < fecha_hasta_inclusiva
            ).scalar() or 0
        except Exception as e:
            flash(f'Error en cálculo de fechas para reporte: {e}', 'danger')
    
    return render_template(
        'cosechas/reportes.html',
        resumen_variedad=resumen_variedad,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        total_registros=total_registros,
        total_tallos=total_tallos,
        total_mallas=total_mallas,
        variedades_unicas=variedades_unicas,
        mostrar_resultados=mostrar_resultados
    )


@cosechas_bp.route('/resumen')
@login_required
@permission_required('cosechas')
def resumen():
    """Resumen matricial por semana: Producto Maestro → Variedad → Día (Lunes-Domingo)"""
    semana_filtro = request.args.get('semana', '').strip()
    maestro_productos = cargar_maestro_productos()
    
    # Obtener semanas disponibles desde fechas únicas en BD de manera optimizada
    fechas_db = db.session.execute(text("SELECT DISTINCT fecha FROM registro_cosecha WHERE fecha IS NOT NULL ORDER BY fecha DESC")).scalars().all()
    semanas_disponibles = sorted(list({formato_semana(f) for f in fechas_db if f}), reverse=True)
    semanas_ordenadas = semanas_disponibles
    
    if not semana_filtro:
        if semanas_ordenadas:
            semana_filtro = semanas_ordenadas[0]
        else:
            semana_filtro = formato_semana(datetime.now().date())
    
    year, week_num, lunes, domingo = parse_semana_to_range(semana_filtro)
    s_filtro_clean = f"{year % 100:02d}{week_num:02d}"
    s_filtro_2d = str(week_num)
    
    # Blindaje contra mezcla de tarjetas: filtrar estrictamente por el rango exacto Lunes-Domingo de la semana
    registros = RegistroCosecha.query.filter(
        RegistroCosecha.fecha >= lunes,
        RegistroCosecha.fecha <= domingo
    ).order_by(RegistroCosecha.fecha.asc(), RegistroCosecha.hora.asc()).all()
    
    datos_por_semana = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))
    desglose_modulos = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int)))))
    totales_diarios = defaultdict(lambda: defaultdict(int))

    # Clave fija de la semana seleccionada para que Jinja NUNCA genere más de una tarjeta en la vista semanal
    semana_key = semana_filtro
    for registro in registros:
        prod_maestro = maestro_productos.get(registro.variedad, 'SIN CLASIFICAR')
        variedad = registro.variedad
        dia_semana = registro.fecha.weekday()  # 0=Lunes, 6=Domingo (el domingo cae estrictamente en día 6 de esta semana)
        modulo = registro.modulo
        
        datos_por_semana[semana_key][prod_maestro][variedad][dia_semana] += registro.total_tallos
        desglose_modulos[semana_key][prod_maestro][variedad][dia_semana][modulo] += registro.total_tallos
        totales_diarios[semana_key][dia_semana] += registro.total_tallos


    
    # Conversión a dicts estándar para Jinja2
    datos_finales = {
        sem: {
            pm: {var: dict(dias) for var, dias in vars_.items()}
            for pm, vars_ in pms.items()
        }
        for sem, pms in datos_por_semana.items()
    }
    
    desglose_finales = {
        sem: {
            pm: {
                var: {dia: dict(mods) for dia, mods in dias.items()}
                for var, dias in vars_.items()
            }
            for pm, vars_ in pms.items()
        }
        for sem, pms in desglose_modulos.items()
    }
    
    totales_diarios_finales = {sem: dict(dias) for sem, dias in totales_diarios.items()}
    dias_semana = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']

    # Cargar estimados de la semana activa para comparar Estimado vs Real y calcular % acierto
    semanas_estimado_match = [s_filtro_clean, s_filtro_2d]
    if s_filtro_2d.isdigit():
        semanas_estimado_match.append(f"26{int(s_filtro_2d):02d}")
    estimados_db = Estimado.query.filter(Estimado.semana.in_(semanas_estimado_match)).all()
    estimados_por_producto = {}
    estimados_por_variedad = {}

    for e in estimados_db:
        pm = (e.producto_maestro or '').strip()
        var = (e.variedad or '').strip()
        cant = e.cantidad_estimada or 0

        estimados_por_producto[pm] = estimados_por_producto.get(pm, 0) + cant
        if pm.upper() != pm:
            estimados_por_producto[pm.upper()] = estimados_por_producto.get(pm.upper(), 0) + cant

        estimados_por_variedad[(pm, var)] = cant
        estimados_por_variedad[(pm.upper(), var.upper())] = cant
        estimados_por_variedad[var] = cant
        estimados_por_variedad[var.upper()] = cant

    total_estimado_global = sum(e.cantidad_estimada or 0 for e in estimados_db)

    return render_template(
        'cosechas/resumen.html',
        datos=datos_finales,
        desglose=desglose_finales,
        totales_diarios=totales_diarios_finales,
        semanas_disponibles=semanas_ordenadas,
        semana_seleccionada=semana_filtro,
        dias_semana=dias_semana,
        estimados_por_producto=estimados_por_producto,
        estimados_por_variedad=estimados_por_variedad,
        total_estimado_global=total_estimado_global
    )


@cosechas_bp.route('/estimados')
@login_required
@permission_required('cosechas')
def estimados():
    """Página de estimados semanales y cumplimiento por variedad"""
    fecha_hoy = datetime.now().date()
    semana_actual = formato_semana(fecha_hoy)
    semana_filtro = request.args.get('semana', semana_actual).strip()
    
    # Catálogo completo de productos maestros con sus variedades
    productos_con_variedades = construir_productos_con_variedades()
    todas_variedades = cargar_variedades()
    mapa_variedad_producto = cargar_maestro_productos()
    
    estimados_db = Estimado.query.filter_by(semana=semana_filtro).all()
    # Diccionario case-insensitive (UPPER(producto), UPPER(variedad)) -> Estimado
    estimados_dict = {(e.producto_maestro.strip().upper(), e.variedad.strip().upper()): e for e in estimados_db}
    
    # Estructura { producto: { variedad: cantidad } } para pre-poblar el frontend
    estimados_existentes = defaultdict(dict)
    for e in estimados_db:
        pm = e.producto_maestro.strip().upper()
        var = e.variedad.strip()
        estimados_existentes[pm][var] = e.cantidad_estimada
        if pm not in productos_con_variedades:
            productos_con_variedades[pm] = []
        if var not in productos_con_variedades[pm]:
            productos_con_variedades[pm].append(var)
            productos_con_variedades[pm].sort()
    
    for producto in productos_con_variedades:
        productos_con_variedades[producto].sort()
    
    productos_maestros = sorted(productos_con_variedades.keys())
    
    todos_registros = RegistroCosecha.query.all()
    registros_semana = [r for r in todos_registros if formato_semana(r.fecha) == semana_filtro]
    
    totales_reales_variedad = defaultdict(int)
    for registro in registros_semana:
        if registro.variedad:
            totales_reales_variedad[registro.variedad.strip().upper()] += (registro.total_tallos or 0)
    
    datos_por_producto = []
    for producto in productos_maestros:
        variedades_data = []
        total_estimado_producto = 0
        total_real_producto = 0
        
        for variedad in productos_con_variedades[producto]:
            estimado_obj = estimados_dict.get((producto.strip().upper(), variedad.strip().upper()))
            if estimado_obj:
                cant_estimada = estimado_obj.cantidad_estimada
                cant_real = totales_reales_variedad.get(variedad.strip().upper(), 0)
                porcentaje = round((cant_real / cant_estimada) * 100, 2) if cant_estimada > 0 else 0
                
                variedades_data.append({
                    'variedad': variedad,
                    'estimado': cant_estimada,
                    'real': cant_real,
                    'porcentaje': porcentaje,
                    'id': estimado_obj.id
                })
                
                total_estimado_producto += cant_estimada
                total_real_producto += cant_real
        
        if variedades_data:
            pct_producto = round((total_real_producto / total_estimado_producto) * 100, 2) if total_estimado_producto > 0 else 0
            datos_por_producto.append({
                'producto': producto,
                'variedades': variedades_data,
                'total_estimado': total_estimado_producto,
                'total_real': total_real_producto,
                'porcentaje': pct_producto
            })
    
    total_estimado_global = sum(d['total_estimado'] for d in datos_por_producto)
    total_real_global = sum(d['total_real'] for d in datos_por_producto)
    cumplimiento_global = round((total_real_global / total_estimado_global * 100), 1) if total_estimado_global > 0 else 0

    semanas_con_estimados = db.session.query(Estimado.semana).distinct().all()
    semanas_disponibles = sorted([s[0] for s in semanas_con_estimados], reverse=True)
    if semana_actual not in semanas_disponibles:
        semanas_disponibles.insert(0, semana_actual)
    
    return render_template(
        'cosechas/estimados.html',
        datos_por_producto=datos_por_producto,
        productos_con_variedades=dict(productos_con_variedades),
        todas_variedades=todas_variedades,
        mapa_variedad_producto=mapa_variedad_producto,
        estimados_existentes=dict(estimados_existentes),
        semana_seleccionada=semana_filtro,
        semanas_disponibles=semanas_disponibles,
        total_estimado_global=total_estimado_global,
        total_real_global=total_real_global,
        cumplimiento_global=cumplimiento_global
    )


@cosechas_bp.route('/estimados/guardar', methods=['POST'])
@login_required
@permission_required('cosechas')
def guardar_estimado():
    """Guardar o actualizar estimados semanales y sincronizar catálogo en BD"""
    try:
        semana = request.form.get('semana', '').strip()
        producto_maestro = request.form.get('producto_maestro', '').strip().upper()
        
        if not semana or not producto_maestro:
            flash('Debe seleccionar la semana y el producto maestro.', 'warning')
            return redirect(url_for('cosechas.estimados', semana=semana))

        variedades = request.form.getlist('variedad[]')
        cantidades = request.form.getlist('cantidad_estimada[]')
        variedades_eliminadas = request.form.getlist('variedades_eliminadas[]')
        
        if not variedades and request.form.get('variedad'):
            variedades = [request.form.get('variedad')]
            cantidades = [request.form.get('cantidad_estimada')]
        
        eliminados = 0
        # 1. Eliminar variedades que fueron quitadas en el formulario
        for v_del in set(variedades_eliminadas):
            v_clean = (v_del or '').strip()
            if v_clean:
                num_del = Estimado.query.filter(
                    Estimado.semana == semana,
                    Estimado.producto_maestro.ilike(producto_maestro),
                    Estimado.variedad.ilike(v_clean)
                ).delete(synchronize_session=False)
                if num_del:
                    eliminados += num_del
        
        guardados = 0
        actualizados = 0
        
        for variedad, cantidad_str in zip(variedades, cantidades):
            if not variedad or not str(variedad).strip():
                continue
            
            v_nombre = variedad.strip()
            if cantidad_str is None or str(cantidad_str).strip() == '':
                continue
                
            try:
                cant_estimada = int(cantidad_str)
            except ValueError:
                continue
            
            # Sincronizar automáticamente en BD para que siempre exista en el catálogo general
            sincronizar_variedad_a_bd(v_nombre, producto_maestro)

            # Si el usuario colocó 0 o negativo, eliminar el estimado
            if cant_estimada <= 0:
                num_del = Estimado.query.filter(
                    Estimado.semana == semana,
                    Estimado.producto_maestro.ilike(producto_maestro),
                    Estimado.variedad.ilike(v_nombre)
                ).delete(synchronize_session=False)
                if num_del:
                    eliminados += num_del
                continue
            
            estimado = Estimado.query.filter(
                Estimado.semana == semana,
                Estimado.producto_maestro.ilike(producto_maestro),
                Estimado.variedad.ilike(v_nombre)
            ).first()
            
            if estimado:
                estimado.cantidad_estimada = cant_estimada
                actualizados += 1
            else:
                nuevo_est = Estimado(
                    semana=semana,
                    producto_maestro=producto_maestro,
                    variedad=v_nombre,
                    cantidad_estimada=cant_estimada
                )
                db.session.add(nuevo_est)
                guardados += 1
        
        db.session.commit()
        record_audit('COSECHAS', 'ESTIMADOS_SAVE', 'Estimado', None,
                     details=f"Semana: {semana}, Prod: {producto_maestro}, Nuevos: {guardados}, Act: {actualizados}, Del: {eliminados}")
        
        msg_parts = []
        if guardados > 0:
            msg_parts.append(f"{guardados} guardados")
        if actualizados > 0:
            msg_parts.append(f"{actualizados} actualizados")
        if eliminados > 0:
            msg_parts.append(f"{eliminados} eliminados")
            
        if msg_parts:
            flash(f"Estimados procesados ({', '.join(msg_parts)}).", 'success')
        else:
            flash('No se realizaron cambios en los estimados.', 'info')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error al guardar estimado: {str(e)}', 'danger')
    
    return redirect(url_for('cosechas.estimados', semana=request.form.get('semana', '')))


@cosechas_bp.route('/estimados/eliminar/<int:id>')
@login_required
@permission_required('cosechas')
def eliminar_estimado(id):
    """Eliminar un estimado individual"""
    estimado = Estimado.query.get_or_404(id)
    semana = estimado.semana
    try:
        db.session.delete(estimado)
        db.session.commit()
        record_audit('COSECHAS', 'ESTIMADOS_DELETE', 'Estimado', id, details=f"Semana: {semana}")
        flash('Estimado eliminado exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar estimado: {str(e)}', 'danger')
    
    return redirect(url_for('cosechas.estimados', semana=semana))


@cosechas_bp.route('/exportar_excel')
@login_required
@permission_required('cosechas')
def exportar_excel():
    """Descargar registros de cosecha en formato Excel"""
    try:
        registros = RegistroCosecha.query.order_by(RegistroCosecha.fecha.desc()).all()
        data = [
            {
                'ID': r.id,
                'Fecha': r.fecha.strftime('%Y-%m-%d'),
                'Hora': r.hora.strftime('%H:%M:%S'),
                'Viaje': r.viaje,
                'Semana': r.semana,
                'Variedad': r.variedad,
                'Módulo': r.modulo,
                'Tallos por Malla': r.tallos,
                'Cantidad Mallas': r.mallas,
                'Total Tallos': r.total_tallos,
                'Responsable': r.responsable
            }
            for r in registros
        ]
        
        df = pd.DataFrame(data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Registros de Cosecha', index=False)
            if not df.empty:
                resumen_var = df.groupby('Variedad').agg({
                    'Total Tallos': 'sum',
                    'Cantidad Mallas': 'sum',
                    'ID': 'count'
                }).rename(columns={'ID': 'Total Registros'})
                resumen_var.to_excel(writer, sheet_name='Resumen por Variedad')
                
                resumen_fec = df.groupby('Fecha').agg({
                    'Total Tallos': 'sum',
                    'Cantidad Mallas': 'sum',
                    'ID': 'count'
                }).rename(columns={'ID': 'Total Registros'})
                resumen_fec.to_excel(writer, sheet_name='Resumen por Fecha')
        
        output.seek(0)
        nombre_archivo = f'cosecha_registros_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=nombre_archivo
        )
    except Exception as e:
        flash(f'Error al exportar: {str(e)}', 'danger')
        return redirect(url_for('cosechas.reportes'))


# ====================================================
# API ENDPOINTS COMPATIBLES (POWER QUERY, EXCEL, APPS)
# ====================================================

@cosechas_bp.route('/api/sincronizar-offline', methods=['POST'])
@login_required
@permission_required('cosechas')
def api_sincronizar_offline():
    """Recibe un lote de registros de cosecha capturados offline en el dispositivo del operario."""
    try:
        data = request.get_json() or {}
        items = data.get('items', [])
        if not items:
            return jsonify({'success': False, 'error': 'No se enviaron registros para sincronizar.'}), 400

        from app.shared.models import User
        u_sesion = User.query.get(session.get('user_id')) if session.get('user_id') else None
        responsable_defecto = (u_sesion.full_name if u_sesion else None) or session.get('full_name') or session.get('username') or 'Responsable'

        guardados = 0
        hora_actual = datetime.now(ZONA_HORARIA).time()

        for it in items:
            fecha_str = it.get('fecha', '').strip()
            if not fecha_str:
                continue
            try:
                fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            except Exception:
                fecha = datetime.now(ZONA_HORARIA).date()

            viaje = str(it.get('viaje', '')).strip()
            # Blindaje: semana siempre sincronizada con la fecha real (1..53)
            iso_year, iso_week, _ = fecha.isocalendar()
            semana = iso_week

            tallos = int(it.get('tallos', 0))
            mallas = int(it.get('mallas', 0))
            modulo = limpiar_nombre_modulo(it.get('modulo', ''))
            variedad = str(it.get('variedad', '')).strip().upper()
            total_tallos = tallos * mallas

            # Enforzar autoría real del operario conectado o autor del ticket capturado
            responsable = str(it.get('responsable') or '').strip() or (u_sesion.full_name if u_sesion else None) or responsable_defecto

            if not variedad:
                continue

            reg = RegistroCosecha(
                fecha=fecha,
                viaje=viaje,
                semana=semana,
                tallos=tallos,
                mallas=mallas,
                modulo=modulo,
                total_tallos=total_tallos,
                variedad=variedad,
                hora=hora_actual,
                responsable=responsable
            )
            db.session.add(reg)
            guardados += 1

        db.session.commit()
        record_audit('COSECHAS', 'SYNC_OFFLINE', 'RegistroCosecha', None, 
                     details=f"Sincronizados {guardados} tickets offline por {responsable_defecto}")
        return jsonify({'success': True, 'count': guardados})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@cosechas_bp.route('/api/maestro-productos', methods=['GET'])
def api_maestro_productos():
    """Catálogo de productos maestros y sus variedades"""
    productos_con_variedades = construir_productos_con_variedades()
    return jsonify({
        'productos': list(productos_con_variedades.keys()),
        'productos_con_variedades': productos_con_variedades
    })


@cosechas_bp.route('/api/maestro-productos/<producto_maestro>/variedades', methods=['GET'])
def api_variedades_por_producto(producto_maestro):
    """Variedades de un producto maestro específico"""
    productos_con_variedades = construir_productos_con_variedades()
    return jsonify({
        'producto_maestro': producto_maestro,
        'variedades': productos_con_variedades.get(producto_maestro, [])
    })


@cosechas_bp.route('/api/reportes/tablero', methods=['GET'])
def api_reportes_tablero():
    """Datos para tablero analítico semanal con filtros dinámicos, segmentación por año y comparativa anual"""
    registros = RegistroCosecha.query.order_by(RegistroCosecha.fecha.asc()).all()
    productos_con_variedades = construir_productos_con_variedades()
    maestro_productos = cargar_maestro_productos()

    parsed_records = []
    anios_set = set()
    semanas_set_total = set()

    for r in registros:
        y = r.fecha.isocalendar()[0] if r.fecha else (2000 + int(str(r.semana)[:2]) if r.semana and int(r.semana) >= 100 else 2026)
        w = int(r.semana) % 100 if r.semana else (r.fecha.isocalendar()[1] if r.fecha else 1)
        w_str = str(w)
        anios_set.add(int(y))
        semanas_set_total.add(w_str)
        parsed_records.append((r, int(y), w_str))

    anios_disponibles = sorted(list(anios_set), reverse=True)
    if not anios_disponibles:
        anios_disponibles = [2026, 2025]

    req_anio = request.args.get('anio')
    try:
        anio_filtro = int(req_anio) if req_anio else anios_disponibles[0]
    except Exception:
        anio_filtro = anios_disponibles[0]

    anio_anterior = anio_filtro - 1

    semanas_disponibles = sorted(
        list(semanas_set_total),
        key=lambda x: int(x) if x.isdigit() else 0
    )

    productos_disponibles = sorted(
        set(productos_con_variedades.keys()) |
        {maestro_productos.get(r.variedad, 'SIN CLASIFICAR') for r in registros},
        key=lambda v: v.upper()
    )
    variedades_disponibles = sorted({r.variedad for r in registros}, key=lambda v: v.upper())

    if not semanas_disponibles:
        return jsonify({
            'anios_disponibles': anios_disponibles,
            'anio_activo': anio_filtro,
            'anio_comparativo': anio_anterior,
            'semanas_disponibles': [],
            'productos_disponibles': productos_disponibles,
            'variedades_disponibles': variedades_disponibles,
            'productos_con_variedades': productos_con_variedades,
            'filtros_activos': {'anio': anio_filtro, 'productos': [], 'variedades': [], 'semanas': []},
            'curva_semanal': [],
            'kpis': {'total_tallos': 0, 'total_semanas': 0, 'promedio_semanal': 0, 'pico_semanal': 0}
        })

    productos_filtro = [p.strip() for p in request.args.getlist('producto_maestro') if p.strip()]
    variedades_filtro = [v.strip() for v in request.args.getlist('variedad') if v.strip()]
    semanas_filtro_param = [s.strip() for s in request.args.getlist('semana') if s.strip()]

    if semanas_filtro_param:
        semanas_filtro = [s for s in semanas_disponibles if s in set(semanas_filtro_param)]
    else:
        max_sem = max([int(s) for s in semanas_disponibles if s.isdigit()] or [52])
        semanas_filtro = [str(w) for w in range(1, max(52, max_sem) + 1)]

    semanas_set = set(semanas_filtro)
    acumulado_actual = defaultdict(int)
    acumulado_anterior = defaultdict(int)

    for registro, y, semana in parsed_records:
        if semana not in semanas_set:
            continue
        producto = maestro_productos.get(registro.variedad, 'SIN CLASIFICAR')

        if productos_filtro and producto not in productos_filtro:
            continue
        if variedades_filtro and registro.variedad not in variedades_filtro:
            continue

        if y == anio_filtro:
            acumulado_actual[semana] += (registro.total_tallos or 0)
        elif y == anio_anterior:
            acumulado_anterior[semana] += (registro.total_tallos or 0)

    curva_semanal = []
    for sem in semanas_filtro:
        val_act = acumulado_actual.get(sem, 0)
        val_ant = acumulado_anterior.get(sem, 0)
        curva_semanal.append({
            'semana': sem,
            'semana_num': int(sem) if sem.isdigit() else 0,
            'total_tallos': val_act,
            'tallos_anterior': val_ant
        })

    total_tallos_actual = sum(acumulado_actual[s] for s in semanas_filtro)
    semanas_con_cosecha = len([s for s in semanas_filtro if acumulado_actual.get(s, 0) > 0])
    promedio_semanal = round(total_tallos_actual / semanas_con_cosecha, 1) if semanas_con_cosecha > 0 else 0
    pico_semanal = max([acumulado_actual.get(s, 0) for s in semanas_filtro] or [0])
    total_tallos_anterior = sum(acumulado_anterior[s] for s in semanas_filtro)

    return jsonify({
        'anios_disponibles': anios_disponibles,
        'anio_activo': anio_filtro,
        'anio_comparativo': anio_anterior,
        'semanas_disponibles': [str(w) for w in range(1, 53)],
        'productos_disponibles': productos_disponibles,
        'variedades_disponibles': variedades_disponibles,
        'productos_con_variedades': productos_con_variedades,
        'filtros_activos': {
            'anio': anio_filtro,
            'productos': productos_filtro,
            'variedades': variedades_filtro,
            'semanas': semanas_filtro_param
        },
        'curva_semanal': curva_semanal,
        'kpis': {
            'total_tallos': total_tallos_actual,
            'total_semanas': semanas_con_cosecha or len(semanas_filtro_param) or len(curva_semanal),
            'promedio_semanal': promedio_semanal,
            'pico_semanal': pico_semanal,
            'total_tallos_anterior': total_tallos_anterior
        }
    })


@cosechas_bp.route('/api/resumen', methods=['GET'])
def api_resumen():
    """Resumen de cosecha para integración externa (formato plano o jerárquico)"""
    semana_filtro = request.args.get('semana', '').strip()
    formato = request.args.get('formato', 'plano')
    maestro_productos = cargar_maestro_productos()
    
    if semana_filtro:
        year, week_num, lunes, domingo = parse_semana_to_range(semana_filtro)
        registros = RegistroCosecha.query.filter(
            RegistroCosecha.fecha >= lunes,
            RegistroCosecha.fecha <= domingo
        ).order_by(RegistroCosecha.fecha.asc(), RegistroCosecha.hora.asc()).all()
    else:
        fecha_hoy = datetime.now().date()
        year, week_num, lunes, domingo = parse_semana_to_range(formato_semana(fecha_hoy))
        registros = RegistroCosecha.query.filter(
            RegistroCosecha.fecha >= lunes,
            RegistroCosecha.fecha <= domingo
        ).order_by(RegistroCosecha.fecha.asc(), RegistroCosecha.hora.asc()).all()
        semana_filtro = formato_semana(fecha_hoy)
    
    dias_semana = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    
    if formato == 'plano':
        datos_planos = [
            {
                'semana': semana_filtro,
                'producto_maestro': maestro_productos.get(r.variedad, 'SIN CLASIFICAR'),
                'variedad': r.variedad,
                'fecha': r.fecha.strftime('%Y-%m-%d'),
                'dia_semana': dias_semana[r.fecha.weekday()],
                'modulo': r.modulo,
                'hora_cosecha': r.hora.strftime('%H:%M:%S'),
                'tallos_por_malla': r.tallos,
                'mallas': r.mallas,
                'total_tallos': r.total_tallos,
                'responsable': r.responsable,
                'viaje': r.viaje
            }
            for r in registros
        ]
        return jsonify({
            'semana': semana_filtro,
            'total_registros': len(datos_planos),
            'datos': datos_planos
        })
    else:
        datos_agrupados = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
        totales_por_variedad = defaultdict(lambda: defaultdict(int))
        for r in registros:
            pm = maestro_productos.get(r.variedad, 'SIN CLASIFICAR')
            dia_nombre = dias_semana[r.fecha.weekday()]
            datos_agrupados[pm][r.variedad][dia_nombre][r.modulo].append({
                'hora_cosecha': r.hora.strftime('%H:%M:%S'),
                'tallos_por_malla': r.tallos,
                'mallas': r.mallas,
                'total_tallos': r.total_tallos,
                'responsable': r.responsable,
                'viaje': r.viaje
            })
            totales_por_variedad[pm][r.variedad] += r.total_tallos
        
        datos_finales = {
            pm: {
                var: {
                    'total_tallos': totales_por_variedad[pm][var],
                    'dias': dict(datos_agrupados[pm][var])
                }
                for var in vars_
            }
            for pm, vars_ in datos_agrupados.items()
        }
        return jsonify({'semana': semana_filtro, 'datos': datos_finales})


@cosechas_bp.route('/api/resumen/excel', methods=['GET'])
def api_resumen_excel():
    """Descargar resumen de cosecha semanal como archivo Excel directo"""
    semana_filtro = request.args.get('semana', '').strip()
    maestro_productos = cargar_maestro_productos()
    
    if semana_filtro:
        year, week_num, lunes, domingo = parse_semana_to_range(semana_filtro)
        registros = RegistroCosecha.query.filter(
            RegistroCosecha.fecha >= lunes,
            RegistroCosecha.fecha <= domingo
        ).order_by(RegistroCosecha.fecha.asc(), RegistroCosecha.hora.asc()).all()
    else:
        fecha_hoy = datetime.now().date()
        year, week_num, lunes, domingo = parse_semana_to_range(formato_semana(fecha_hoy))
        registros = RegistroCosecha.query.filter(
            RegistroCosecha.fecha >= lunes,
            RegistroCosecha.fecha <= domingo
        ).order_by(RegistroCosecha.fecha.asc(), RegistroCosecha.hora.asc()).all()
        semana_filtro = formato_semana(fecha_hoy)
    
    dias_semana = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    datos_excel = [
        {
            'Semana': semana_filtro,
            'Producto Maestro': maestro_productos.get(r.variedad, 'SIN CLASIFICAR'),
            'Variedad': r.variedad,
            'Fecha': r.fecha.strftime('%Y-%m-%d'),
            'Día': dias_semana[r.fecha.weekday()],
            'Bloque/Módulo': r.modulo,
            'Hora Cosecha': r.hora.strftime('%H:%M:%S'),
            'Tallos/Malla': r.tallos,
            'Mallas': r.mallas,
            'Total Tallos': r.total_tallos,
            'Responsable': r.responsable,
            'Viaje': r.viaje
        }
        for r in registros
    ]
    
    df = pd.DataFrame(datos_excel)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Resumen Cosecha', index=False)
        worksheet = writer.sheets['Resumen Cosecha']
        for idx, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).apply(len).max() if not df.empty else 0, len(col)) + 3
            worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 45)
    
    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'resumen_cosecha_semana_{semana_filtro}.xlsx'
    )


@cosechas_bp.route('/api/resumen/bloques/excel', methods=['GET'])
@login_required
@permission_required('cosechas')
def api_resumen_bloques_excel():
    """Descargar desglose de cosecha por bloque/módulo de un producto maestro en formato Excel"""
    semana_filtro = request.args.get('semana', '').strip()
    producto_filtro = request.args.get('producto', '').strip()
    maestro_productos = cargar_maestro_productos()
    
    query = RegistroCosecha.query
    if semana_filtro:
        year, week_num, lunes, domingo = parse_semana_to_range(semana_filtro)
        query = query.filter(RegistroCosecha.fecha >= lunes, RegistroCosecha.fecha <= domingo)
    registros = query.order_by(RegistroCosecha.fecha.asc(), RegistroCosecha.hora.asc()).all()

    if producto_filtro:
        registros = [r for r in registros if maestro_productos.get(r.variedad, 'SIN CLASIFICAR').upper() == producto_filtro.upper()]

    datos_matriz = defaultdict(lambda: [0]*7)
    prod_nombre = producto_filtro or 'TODOS'
    
    for r in registros:
        mod = str(r.modulo or 'S/M').strip()
        var = str(r.variedad or '').strip()
        d_idx = r.fecha.weekday()
        datos_matriz[(mod, var)][d_idx] += int(r.total_tallos or 0)
        
    filas = []
    for (mod, var), tallos_dias in sorted(datos_matriz.items(), key=lambda x: (x[0][0], x[0][1])):
        total_mod_var = sum(tallos_dias)
        fila = {
            'Semana': semana_filtro,
            'Producto Maestro': prod_nombre,
            'Bloque/Módulo': mod,
            'Variedad': var,
            'Lunes': tallos_dias[0] if tallos_dias[0] > 0 else 0,
            'Martes': tallos_dias[1] if tallos_dias[1] > 0 else 0,
            'Miércoles': tallos_dias[2] if tallos_dias[2] > 0 else 0,
            'Jueves': tallos_dias[3] if tallos_dias[3] > 0 else 0,
            'Viernes': tallos_dias[4] if tallos_dias[4] > 0 else 0,
            'Sábado': tallos_dias[5] if tallos_dias[5] > 0 else 0,
            'Domingo': tallos_dias[6] if tallos_dias[6] > 0 else 0,
            'Total Tallos': total_mod_var
        }
        filas.append(fila)
        
    df = pd.DataFrame(filas) if filas else pd.DataFrame(columns=[
        'Semana', 'Producto Maestro', 'Bloque/Módulo', 'Variedad',
        'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo', 'Total Tallos'
    ])
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        clean_sheet = f"Bloques_{prod_nombre[:20]}".replace('/', '_').replace('\\', '_')
        df.to_excel(writer, sheet_name=clean_sheet, index=False)
        worksheet = writer.sheets[clean_sheet]
        for idx, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).apply(len).max() if not df.empty else 0, len(col)) + 3
            worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 35)
            
    output.seek(0)
    safe_prod = prod_nombre.replace(' ', '_').lower()
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'desglose_bloques_{safe_prod}_sem_{semana_filtro}.xlsx'
    )


@cosechas_bp.route('/api/semanas', methods=['GET'])
def api_semanas_disponibles():
    """Listar todas las semanas disponibles en base de datos"""
    todos = RegistroCosecha.query.all()
    semanas = sorted(list({formato_semana(r.fecha) for r in todos}), reverse=True)
    return jsonify({'total': len(semanas), 'semanas': semanas})


@cosechas_bp.route('/api/semanas/tallos-producto-maestro', methods=['GET'])
def api_tallos_producto_maestro_por_semana():
    """Totales de tallos por producto maestro agrupados por semana y día"""
    semana_filtro = request.args.get('semana', '').strip()
    maestro_productos = cargar_maestro_productos()
    dias_semana = ['Lunes', 'Martes', 'Miercoles', 'Jueves', 'Viernes', 'Sabado', 'Domingo']

    acumulado = defaultdict(
        lambda: defaultdict(
            lambda: {
                'total_tallos': 0, 'total_mallas': 0, 'total_registros': 0,
                'dias': defaultdict(lambda: {'dia_semana': '', 'total_tallos': 0, 'total_mallas': 0, 'total_registros': 0})
            }
        )
    )

    semanas_disponibles = set()
    registros = RegistroCosecha.query.all()

    for r in registros:
        sem = formato_semana(r.fecha)
        if semana_filtro and sem != semana_filtro:
            continue
        pm = maestro_productos.get(r.variedad, 'SIN CLASIFICAR')
        fecha_iso = r.fecha.strftime('%Y-%m-%d')
        dia_nom = dias_semana[r.fecha.weekday()]
        semanas_disponibles.add(sem)

        acumulado[sem][pm]['total_tallos'] += r.total_tallos
        acumulado[sem][pm]['total_mallas'] += r.mallas
        acumulado[sem][pm]['total_registros'] += 1

        acumulado[sem][pm]['dias'][fecha_iso]['dia_semana'] = dia_nom
        acumulado[sem][pm]['dias'][fecha_iso]['total_tallos'] += r.total_tallos
        acumulado[sem][pm]['dias'][fecha_iso]['total_mallas'] += r.mallas
        acumulado[sem][pm]['dias'][fecha_iso]['total_registros'] += 1

    semanas_ordenadas = sorted(list(semanas_disponibles), reverse=True)
    resultado = []
    for sem in semanas_ordenadas:
        prods = []
        for pm in sorted(acumulado[sem].keys(), key=lambda v: v.upper()):
            tot_prod = acumulado[sem][pm]
            dias = [
                {
                    'fecha': f,
                    'dia_semana': tot_prod['dias'][f]['dia_semana'],
                    'total_tallos': tot_prod['dias'][f]['total_tallos'],
                    'total_mallas': tot_prod['dias'][f]['total_mallas'],
                    'total_registros': tot_prod['dias'][f]['total_registros']
                }
                for f in sorted(tot_prod['dias'].keys())
            ]
            prods.append({
                'producto_maestro': pm,
                'total_tallos': tot_prod['total_tallos'],
                'total_mallas': tot_prod['total_mallas'],
                'total_registros': tot_prod['total_registros'],
                'dias': dias
            })
        resultado.append({
            'semana': sem,
            'total_productos': len(prods),
            'productos': prods
        })

    return jsonify({
        'filtro_semana': semana_filtro or None,
        'total_semanas': len(resultado),
        'semanas': resultado
    })



# ==============================================================================
# API PARA EXCEL / POWER QUERY: COSECHAS SEMANA A SEMANA
# ==============================================================================
@cosechas_bp.route('/api/datos-semanales', methods=['GET', 'HEAD'])
@cosechas_bp.route('/api/cosechas-resumen', methods=['GET', 'HEAD'])
@cosechas_bp.route('/api/cosechas-historico', methods=['GET', 'HEAD'])
def api_cosechas_datos_semanales():
    """
    API optimizada para conectar Microsoft Excel (Power Query / Obtener datos de la web).
    Devuelve los datos de cosechas semana a semana en formato plano tabular JSON o CSV con BOM UTF-8.
    Parámetros:
      - semana: int opcional (ej: 43)
      - anio: int opcional (ej: 2026)
      - producto: str opcional (ej: 'GYPSOPHILA')
      - resumen: 1 para resumen agregado por semana/producto/variedad, 0 para detalle fila a fila (por defecto 0)
      - formato: 'json' (por defecto) o 'csv'
      - sep: delimitador CSV (';' por defecto, o ',')
    """
    if request.method == 'HEAD':
        resp = Response(status=200, mimetype='application/json')
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp

    try:
        semana_param = request.args.get('semana', type=int)
        anio_raw = request.args.get('anio')
        anio_param = None
        if anio_raw is not None and str(anio_raw).strip().lower() not in ('todos', 'all', 'todas', 'historico', '*', '', 'none'):
            try:
                anio_param = int(anio_raw)
            except ValueError:
                anio_param = None
        producto_param = (request.args.get('producto') or '').strip().upper()
        resumen_param = request.args.get('resumen', '0').strip() in ('1', 'true', 'si')
        formato_param = request.args.get('formato', 'json').strip().lower()
        sep_param = request.args.get('sep', ';')

        dias_semana_map = {1: 'Lunes', 2: 'Martes', 3: 'Miércoles', 4: 'Jueves', 5: 'Viernes', 6: 'Sábado', 7: 'Domingo'}

        where_clauses = []
        params = {}
        if semana_param is not None:
            where_clauses.append("r.semana = :semana")
            params['semana'] = semana_param
        if anio_param is not None:
            where_clauses.append("(r.fecha IS NOT NULL AND EXTRACT(ISOYEAR FROM r.fecha) = :anio)")
            params['anio'] = anio_param
        if producto_param:
            where_clauses.append("UPPER(TRIM(COALESCE(cvm.producto_maestro, ''))) = :producto")
            params['producto'] = producto_param

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        if resumen_param:
            query = f"""
                SELECT 
                    r.semana,
                    CASE 
                        WHEN r.fecha IS NOT NULL THEN EXTRACT(ISOYEAR FROM r.fecha)::INTEGER 
                        ELSE 2026 
                    END as anio,
                    COALESCE(NULLIF(TRIM(cvm.producto_maestro), ''), 'SIN CLASIFICAR') as producto_maestro,
                    UPPER(TRIM(COALESCE(r.variedad, ''))) as variedad,
                    COUNT(DISTINCT r.modulo) as total_bloques,
                    COUNT(r.id) as total_registros,
                    SUM(COALESCE(r.mallas, 0)) as total_mallas,
                    SUM(COALESCE(r.total_tallos, 0)) as total_tallos,
                    MIN(r.fecha)::TEXT as fecha_inicio,
                    MAX(r.fecha)::TEXT as fecha_fin
                FROM registro_cosecha r
                LEFT JOIN (
                    SELECT DISTINCT ON (UPPER(TRIM(variedad))) 
                        UPPER(TRIM(variedad)) as norm_var, 
                        producto_maestro 
                    FROM cat_variedades_maestro
                ) cvm ON cvm.norm_var = UPPER(TRIM(r.variedad))
                {where_sql}
                GROUP BY r.semana, (CASE WHEN r.fecha IS NOT NULL THEN EXTRACT(ISOYEAR FROM r.fecha)::INTEGER ELSE 2026 END), COALESCE(NULLIF(TRIM(cvm.producto_maestro), ''), 'SIN CLASIFICAR'), UPPER(TRIM(COALESCE(r.variedad, '')))
                ORDER BY anio DESC, r.semana DESC, producto_maestro ASC, total_tallos DESC;
            """
        else:
            query = f"""
                SELECT 
                    r.semana,
                    CASE 
                        WHEN r.fecha IS NOT NULL THEN EXTRACT(ISOYEAR FROM r.fecha)::INTEGER 
                        ELSE 2026 
                    END as anio,
                    r.id,
                    COALESCE(r.fecha::TEXT, '') as fecha,
                    CASE 
                        WHEN r.fecha IS NOT NULL THEN EXTRACT(ISODOW FROM r.fecha)::INTEGER 
                        ELSE 1 
                    END as dia_num,
                    COALESCE(r.viaje::TEXT, '') as viaje,
                    COALESCE(r.modulo::TEXT, '') as bloque,
                    UPPER(TRIM(COALESCE(r.variedad, ''))) as variedad,
                    COALESCE(NULLIF(TRIM(cvm.producto_maestro), ''), 'SIN CLASIFICAR') as producto_maestro,
                    COALESCE(r.mallas, 0) as mallas,
                    COALESCE(r.tallos, 0) as tallos_por_malla,
                    COALESCE(r.total_tallos, 0) as total_tallos,
                    COALESCE(SUBSTRING(r.hora::TEXT FROM 1 FOR 5), '') as hora,
                    COALESCE(r.responsable, '') as responsable
                FROM registro_cosecha r
                LEFT JOIN (
                    SELECT DISTINCT ON (UPPER(TRIM(variedad))) 
                        UPPER(TRIM(variedad)) as norm_var, 
                        producto_maestro 
                    FROM cat_variedades_maestro
                ) cvm ON cvm.norm_var = UPPER(TRIM(r.variedad))
                {where_sql}
                ORDER BY r.fecha DESC, r.semana DESC, r.id DESC;
            """

        rows = db.session.execute(text(query), params).fetchall()

        data_list = []
        for r in rows:
            d = dict(r._mapping)
            if 'dia_num' in d:
                d['dia_semana'] = dias_semana_map.get(d.pop('dia_num', 1), 'Desconocido')
            data_list.append(d)

        if formato_param == 'csv':
            if not data_list:
                csv_output = "No se encontraron datos\n"
            else:
                headers = list(data_list[0].keys())
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=headers, delimiter=sep_param)
                writer.writeheader()
                writer.writerows(data_list)
                csv_output = output.getvalue()

            resp = Response(
                '\ufeff' + csv_output,
                mimetype="text/csv; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename=cosechas_semanales_{semana_param or 'todas'}.csv"}
            )
            resp.headers['Access-Control-Allow-Origin'] = '*'
            return resp

        resp = jsonify(data_list)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f"Error al consultar cosechas semanales: {str(e)}"
        }), 500


# ==============================================================================
# API PARA EXCEL / POWER QUERY: RESUMEN DE ÁREAS (HECTÁREAS POR PRODUCTO Y VARIEDAD)
# ==============================================================================
@cosechas_bp.route('/api/areas-resumen', methods=['GET', 'HEAD'])
@cosechas_bp.route('/api/areas-historico', methods=['GET', 'HEAD'])
def api_areas_resumen():
    """
    API para Microsoft Excel que devuelve el resumen oficial de hectáreas y camas
    por producto maestro y variedad desde mrt_estado_cultivo_actual.
    Incluye normalización con Catálogo Maestro y suma completa de 32.09 ha de la finca.
    """
    if request.method == 'HEAD':
        resp = Response(status=200, mimetype="application/json")
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp

    try:
        formato = request.args.get('formato', 'json').strip().lower()
        sep_param = request.args.get('sep', ';')
        semana_req = request.args.get('semana', type=int)
        anio_req = request.args.get('anio', type=int)
        nivel_req = request.args.get('nivel', 'variedad').strip().lower()
        solo_sembradas = request.args.get('solo_sembradas', 'no').strip().lower() in ('si', 'true', '1')
        historico_req = (request.args.get('historico', '0').strip().lower() in ('1', 'true', 'si') 
                         or request.args.get('todas_semanas', '0').strip().lower() in ('1', 'true', 'si')
                         or request.path.endswith('/areas-historico'))

        # Obtener ultima semana cerrada disponible con datos reales en el sistema
        ultima_sem_row = db.session.execute(text("SELECT anio, semana FROM dim_distribucion_areas ORDER BY anio DESC, semana DESC LIMIT 1;")).fetchone()
        if ultima_sem_row:
            default_anio = int(ultima_sem_row[0])
            default_semana = int(ultima_sem_row[1])
        else:
            default_anio = 2026
            default_semana = 41

        semana_val = semana_req if semana_req is not None else default_semana
        anio_val = anio_req or default_anio

        # Modo 1: Histórico completo semana a semana desde dim_distribucion_areas
        if historico_req:
            where_hist = ""
            params_hist = {}
            if anio_req:
                where_hist = "WHERE anio = :anio"
                params_hist['anio'] = anio_req
            if solo_sembradas:
                where_hist = ("WHERE " if not where_hist else where_hist + " AND ") + "es_cultivo = TRUE"

            query = f"""
                SELECT 
                    semana,
                    anio,
                    semana_iso,
                    producto_comercial,
                    producto_grupo as producto_maestro,
                    ha as total_ha,
                    es_cultivo,
                    porcentaje_finca
                FROM dim_distribucion_areas
                {where_hist}
                ORDER BY anio DESC, semana DESC, total_ha DESC;
            """
            rows = db.session.execute(text(query), params_hist).fetchall()
        elif nivel_req == 'comercial':
            # Modo 2: Resumen oficial comercial de 32 ha para la semana seleccionada
            where_clause = ""
            if solo_sembradas:
                where_clause = "WHERE m.producto_actual IS NOT NULL AND TRIM(m.producto_actual) != '' AND UPPER(TRIM(m.producto_actual)) NOT IN ('VACIO', 'APERTURA', 'EUCALIPTOS', 'VICIA-AVENA', 'MOSTAZA CALIENTE', 'ERRADICADO', 'TOTAL', 'NAN')"

            query = f"""
                SELECT 
                    :semana_val as semana,
                    :anio_val as anio,
                    COALESCE(NULLIF(TRIM(m.producto_actual), ''), 'VACIO') as producto_comercial,
                    MAX(COALESCE(NULLIF(TRIM(cvm.producto_maestro), ''), NULLIF(TRIM(m.producto_actual), ''), 'SIN CLASIFICAR')) as producto_maestro,
                    COUNT(DISTINCT m.codigo_bloque) as total_bloques,
                    COUNT(DISTINCT m.id_cama) as total_camas,
                    ROUND(SUM(m.ha)::numeric, 4) as total_ha,
                    ROUND(SUM(m.cama_estandar)::numeric, 2) as total_camas_estandar,
                    ROUND((100.0 * SUM(m.ha) / 32.0)::numeric, 2) as porcentaje_finca
                FROM mrt_estado_cultivo_actual m
                LEFT JOIN (
                    SELECT DISTINCT ON (UPPER(TRIM(variedad))) 
                        UPPER(TRIM(variedad)) as norm_var, 
                        producto_maestro 
                    FROM cat_variedades_maestro
                ) cvm ON cvm.norm_var = UPPER(TRIM(m.variedad_actual))
                {where_clause}
                GROUP BY 1, 2, 3
                ORDER BY total_ha DESC, producto_comercial ASC;
            """
            rows = db.session.execute(text(query), {'semana_val': semana_val, 'anio_val': anio_val}).fetchall()
        else:
            # Modo 3: Resumen por variedad para la semana seleccionada
            where_clause = ""
            if solo_sembradas:
                where_clause = "WHERE m.producto_actual IS NOT NULL AND TRIM(m.producto_actual) != '' AND UPPER(TRIM(m.producto_actual)) NOT IN ('VACIO', 'APERTURA', 'EUCALIPTOS', 'VICIA-AVENA', 'MOSTAZA CALIENTE', 'ERRADICADO', 'TOTAL', 'NAN')"

            query = f"""
                SELECT 
                    :semana_val as semana,
                    :anio_val as anio,
                    COALESCE(NULLIF(TRIM(m.producto_actual), ''), 'VACIO') as producto_comercial,
                    COALESCE(NULLIF(TRIM(cvm.producto_maestro), ''), NULLIF(TRIM(m.producto_actual), ''), 'SIN CLASIFICAR') as producto_maestro,
                    COALESCE(NULLIF(TRIM(m.variedad_actual), ''), 'SIN VARIEDAD') as variedad,
                    COUNT(DISTINCT m.codigo_bloque) as total_bloques,
                    COUNT(DISTINCT m.id_cama) as total_camas,
                    ROUND(SUM(m.ha)::numeric, 4) as total_ha,
                    ROUND(SUM(m.cama_estandar)::numeric, 2) as total_camas_estandar,
                    ROUND((100.0 * SUM(m.ha) / 32.0)::numeric, 2) as porcentaje_finca
                FROM mrt_estado_cultivo_actual m
                LEFT JOIN (
                    SELECT DISTINCT ON (UPPER(TRIM(variedad))) 
                        UPPER(TRIM(variedad)) as norm_var, 
                        producto_maestro 
                    FROM cat_variedades_maestro
                ) cvm ON cvm.norm_var = UPPER(TRIM(m.variedad_actual))
                {where_clause}
                GROUP BY 1, 2, 3, 4, 5
                ORDER BY total_ha DESC, producto_comercial ASC;
            """
            rows = db.session.execute(text(query), {'semana_val': semana_val, 'anio_val': anio_val}).fetchall()
        data_list = [dict(r._mapping) for r in rows]

        for d in data_list:
            if 'total_ha' in d:
                d['total_ha'] = float(d['total_ha'] or 0.0)
            if 'total_camas_estandar' in d:
                d['total_camas_estandar'] = float(d['total_camas_estandar'] or 0.0)
            if 'porcentaje_finca' in d:
                d['porcentaje_finca'] = float(d['porcentaje_finca'] or 0.0)

        if formato == 'csv':
            if not data_list:
                csv_output = "No hay datos\n"
            else:
                headers = list(data_list[0].keys())
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=headers, delimiter=sep_param)
                writer.writeheader()
                writer.writerows(data_list)
                csv_output = output.getvalue()
            resp = Response(
                '\ufeff' + csv_output,
                mimetype="text/csv; charset=utf-8",
                headers={"Content-Disposition": "attachment; filename=areas_finca_florsani.csv"}
            )
            resp.headers['Access-Control-Allow-Origin'] = '*'
            return resp

        resp = jsonify(data_list)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': f"Error al consultar áreas de finca: {str(e)}"
        }), 500
