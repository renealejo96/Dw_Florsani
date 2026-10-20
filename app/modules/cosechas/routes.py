from math import ceil
from datetime import datetime, timedelta
from io import BytesIO
from collections import defaultdict
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify

from app.extensions import db
from app.shared.models import RegistroCosecha, Estimado
from app.shared.audit import record_audit
from app.modules.auth.routes import login_required, permission_required
from .utils import (
    ZONA_HORARIA, formato_semana, cargar_variedades, guardar_variedades,
    cargar_responsables, obtener_monitores_sistema, cargar_modulos, cargar_maestro_productos,
    cargar_relaciones_maestro, guardar_relaciones_maestro,
    cargar_productos_maestros, guardar_productos_maestros,
    construir_productos_con_variedades, sincronizar_variedad_a_bd, sincronizar_producto_maestro_a_bd
)

cosechas_bp = Blueprint('cosechas', __name__)
bp_cosechas = cosechas_bp


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
            semana = int(request.form.get('semana', 0))
            if semana > 0 and semana < 100:
                semana = (fecha.year % 100) * 100 + semana
            tallos = int(request.form.get('tallos', 0))
            mallas = int(request.form.get('mallas', 0))
            modulo = limpiar_nombre_modulo(request.form.get('modulo', ''))
            producto_maestro = request.form.get('producto_maestro', '').strip()
            variedad = request.form.get('variedad', '').strip()
            responsable = request.form.get('responsable', '').strip() or responsable_sesion or 'Responsable'

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
            semana_val = int(request.form['semana'])
            if semana_val > 0 and semana_val < 100:
                semana_val = (registro.fecha.year % 100) * 100 + semana_val
            registro.semana = semana_val
            registro.tallos = int(request.form['tallos'])
            registro.mallas = int(request.form['mallas'])
            registro.modulo = limpiar_nombre_modulo(request.form['modulo'].strip())
            registro.variedad = request.form['variedad'].strip()
            registro.responsable = request.form['responsable'].strip()
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
                    productos = set(cargar_productos_maestros())
                    if producto in productos:
                        flash('El producto maestro ya existe.', 'warning')
                    else:
                        productos.add(producto)
                        guardar_productos_maestros(productos)
                        flash(f'Producto maestro "{producto}" creado exitosamente.', 'success')

            elif accion == 'crear_variedad':
                producto = request.form.get('producto_maestro', '').strip().upper()
                variedad = request.form.get('variedad', '').strip().upper()

                if not producto or not variedad:
                    flash('Debe seleccionar producto maestro e ingresar variedad.', 'danger')
                else:
                    relaciones = cargar_relaciones_maestro()
                    mapa_variedad = {var.upper(): prod.upper() for var, prod in relaciones}
                    clave_variedad = variedad.upper()

                    if clave_variedad in mapa_variedad and mapa_variedad[clave_variedad] == producto:
                        flash('La variedad ya existe asignada a ese producto maestro.', 'warning')
                    else:
                        relaciones_actualizadas = [(var.upper(), prod.upper()) for var, prod in relaciones if var.upper() != clave_variedad]
                        relaciones_actualizadas.append((variedad, producto))
                        guardar_relaciones_maestro(relaciones_actualizadas)

                        productos = set(cargar_productos_maestros())
                        productos.add(producto)
                        guardar_productos_maestros(productos)

                        variedades = set(cargar_variedades())
                        variedades.add(variedad)
                        guardar_variedades(variedades)

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
                    # 1. Actualizar archivo MAESTRO_PROS.csv
                    relaciones = cargar_relaciones_maestro()
                    relaciones_actualizadas = []
                    encontrado = False
                    for var, prod in relaciones:
                        if var.upper() == var_original:
                            relaciones_actualizadas.append((var_nueva, prod_nuevo))
                            encontrado = True
                        else:
                            relaciones_actualizadas.append((var.upper(), prod.upper()))
                    
                    if not encontrado:
                        relaciones_actualizadas.append((var_nueva, prod_nuevo))
                    
                    guardar_relaciones_maestro(relaciones_actualizadas)

                    # 2. Actualizar archivo variedades.csv
                    variedades_set = {v.upper() for v in cargar_variedades() if v.upper() != var_original}
                    variedades_set.add(var_nueva)
                    guardar_variedades(sorted(list(variedades_set)))

                    # 3. Asegurar que prod_nuevo esté en productos maestros
                    productos_set = {p.upper() for p in cargar_productos_maestros()}
                    productos_set.add(prod_nuevo)
                    guardar_productos_maestros(sorted(list(productos_set)))

                    # 4. Sincronización opcional en Base de Datos (Registros históricos y Estimados)
                    regs_actualizados = 0
                    ests_actualizados = 0
                    if actualizar_historico:
                        # Actualizar en RegistroCosecha
                        regs = RegistroCosecha.query.filter(RegistroCosecha.variedad.ilike(var_original)).all()
                        for r in regs:
                            r.variedad = var_nueva
                            regs_actualizados += 1

                        # Actualizar en Estimado
                        ests = Estimado.query.filter(Estimado.variedad.ilike(var_original)).all()
                        for e in ests:
                            e.variedad = var_nueva
                            if prod_nuevo:
                                e.producto_maestro = prod_nuevo
                            ests_actualizados += 1

                        db.session.commit()

                    record_audit(
                        'COSECHAS', 'VARIEDAD_EDIT', 'Catalogo', None,
                        details=f"Variedad editada '{var_original}' -> '{var_nueva}' (Prod: '{prod_original}' -> '{prod_nuevo}'). Registros BD: {regs_actualizados}, Estimados BD: {ests_actualizados}"
                    )

                    msg = f'Variedad "{var_original}" modificada exitosamente a "{var_nueva}" (Producto Maestro: "{prod_nuevo}").'
                    if actualizar_historico:
                        msg += f' Se actualizaron {regs_actualizados} registros históricos de cosecha y {ests_actualizados} estimados.'
                    flash(msg, 'success')

            elif accion == 'eliminar_relacion':
                variedad = request.form.get('variedad', '').strip().upper()
                producto = request.form.get('producto_maestro', '').strip().upper()

                relaciones = cargar_relaciones_maestro()
                antes = len(relaciones)
                relaciones_nuevas = [
                    (var, prod)
                    for var, prod in relaciones
                    if not (var.upper() == variedad and prod.upper() == producto)
                ]

                if len(relaciones_nuevas) == antes:
                    flash('No se encontró la relación para eliminar.', 'warning')
                else:
                    guardar_relaciones_maestro(relaciones_nuevas)
                    try:
                        from sqlalchemy import text
                        db.session.execute(text("DELETE FROM cat_variedades_maestro WHERE UPPER(variedad) = :v AND UPPER(producto_maestro) = :p"), {'v': variedad, 'p': producto})
                        db.session.commit()
                    except Exception:
                        pass

                    # Si la variedad ya no pertenece a ningún otro producto, remover de variedades.csv
                    otras_rel = [var for var, _ in relaciones_nuevas if var.upper() == variedad]
                    if not otras_rel:
                        variedades_set = {v for v in cargar_variedades() if v.upper() != variedad}
                        guardar_variedades(sorted(list(variedades_set)))

                    record_audit('COSECHAS', 'VARIEDAD_DELETE', 'Catalogo', None,
                                 details=f"Variedad '{variedad}' desvinculada de '{producto}'")
                    flash(f'Variedad "{variedad}" eliminada de "{producto}".', 'success')

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
                        db.session.commit()
                    except Exception:
                        pass
                    record_audit('COSECHAS', 'PRODUCTO_DELETE', 'Catalogo', None, details=f"Producto '{producto}' eliminado")
                    flash(f'Producto maestro "{producto}" eliminado exitosamente.', 'success')

            else:
                flash('Acción no válida solicitada.', 'danger')

        except Exception as e:
            flash(f'Error al procesar catálogo: {str(e)}', 'danger')

        return redirect(url_for('cosechas.catalogos'))

    productos_con_variedades = construir_productos_con_variedades()
    total_variedades = sum(len(variedades) for variedades in productos_con_variedades.values())

    return render_template(
        'cosechas/catalogos.html',
        productos_con_variedades=productos_con_variedades,
        productos_maestros=sorted(productos_con_variedades.keys(), key=lambda value: value.upper()),
        total_productos=len(productos_con_variedades),
        total_variedades=total_variedades
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
    
    todos_registros = RegistroCosecha.query.all()
    semanas_disponibles = {formato_semana(r.fecha) for r in todos_registros}
    semanas_ordenadas = sorted(list(semanas_disponibles), reverse=True)
    
    if not semana_filtro:
        if semanas_ordenadas:
            semana_filtro = semanas_ordenadas[0]
        else:
            semana_filtro = formato_semana(datetime.now().date())
    
    registros = [r for r in todos_registros if formato_semana(r.fecha) == semana_filtro]
    
    datos_por_semana = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))
    desglose_modulos = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int)))))
    totales_diarios = defaultdict(lambda: defaultdict(int))

    for registro in registros:
        semana = formato_semana(registro.fecha)
        prod_maestro = maestro_productos.get(registro.variedad, 'SIN CLASIFICAR')
        variedad = registro.variedad
        dia_semana = registro.fecha.weekday()  # 0=Lunes, 6=Domingo
        modulo = registro.modulo
        
        datos_por_semana[semana][prod_maestro][variedad][dia_semana] += registro.total_tallos
        desglose_modulos[semana][prod_maestro][variedad][dia_semana][modulo] += registro.total_tallos
        totales_diarios[semana][dia_semana] += registro.total_tallos
    
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
    estimados_db = Estimado.query.filter_by(semana=semana_filtro).all()
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
    """Datos para tablero analítico semanal con filtros dinámicos"""
    registros = RegistroCosecha.query.order_by(RegistroCosecha.fecha.asc()).all()
    productos_con_variedades = construir_productos_con_variedades()
    maestro_productos = cargar_maestro_productos()

    semanas_disponibles = sorted({formato_semana(r.fecha) for r in registros})
    productos_disponibles = sorted(
        set(productos_con_variedades.keys()) |
        {maestro_productos.get(r.variedad, 'SIN CLASIFICAR') for r in registros},
        key=lambda v: v.upper()
    )
    variedades_disponibles = sorted({r.variedad for r in registros}, key=lambda v: v.upper())

    if not semanas_disponibles:
        return jsonify({
            'semanas_disponibles': [],
            'productos_disponibles': productos_disponibles,
            'variedades_disponibles': variedades_disponibles,
            'productos_con_variedades': productos_con_variedades,
            'filtros_activos': {'productos': [], 'variedades': [], 'semanas': []},
            'curva_semanal': []
        })

    productos_filtro = [p.strip() for p in request.args.getlist('producto_maestro') if p.strip()]
    variedades_filtro = [v.strip() for v in request.args.getlist('variedad') if v.strip()]
    semanas_filtro_param = [s.strip() for s in request.args.getlist('semana') if s.strip()]
    
    if semanas_filtro_param:
        semanas_filtro = [s for s in semanas_disponibles if s in set(semanas_filtro_param)]
    else:
        semanas_filtro = list(semanas_disponibles)

    semanas_set = set(semanas_filtro)
    acumulado = defaultdict(int)

    for registro in registros:
        semana = formato_semana(registro.fecha)
        if semana not in semanas_set:
            continue
        producto = maestro_productos.get(registro.variedad, 'SIN CLASIFICAR')

        if productos_filtro and producto not in productos_filtro:
            continue
        if variedades_filtro and registro.variedad not in variedades_filtro:
            continue

        acumulado[semana] += registro.total_tallos

    curva_semanal = [
        {'semana': sem, 'total_tallos': acumulado.get(sem, 0)}
        for sem in semanas_filtro
    ]

    return jsonify({
        'semanas_disponibles': semanas_disponibles,
        'productos_disponibles': productos_disponibles,
        'variedades_disponibles': variedades_disponibles,
        'productos_con_variedades': productos_con_variedades,
        'filtros_activos': {
            'productos': productos_filtro,
            'variedades': variedades_filtro,
            'semanas': semanas_filtro
        },
        'curva_semanal': curva_semanal
    })


@cosechas_bp.route('/api/resumen', methods=['GET'])
def api_resumen():
    """Resumen de cosecha para integración externa (formato plano o jerárquico)"""
    semana_filtro = request.args.get('semana', '').strip()
    formato = request.args.get('formato', 'plano')
    maestro_productos = cargar_maestro_productos()
    
    if semana_filtro:
        todos = RegistroCosecha.query.all()
        registros = [r for r in todos if formato_semana(r.fecha) == semana_filtro]
    else:
        fecha_hoy = datetime.now().date()
        fecha_lunes = fecha_hoy - timedelta(days=fecha_hoy.weekday())
        registros = RegistroCosecha.query.filter(RegistroCosecha.fecha >= fecha_lunes).all()
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
        todos = RegistroCosecha.query.all()
        registros = [r for r in todos if formato_semana(r.fecha) == semana_filtro]
    else:
        fecha_hoy = datetime.now().date()
        fecha_lunes = fecha_hoy - timedelta(days=fecha_hoy.weekday())
        registros = RegistroCosecha.query.filter(RegistroCosecha.fecha >= fecha_lunes).all()
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
        todos = query.all()
        registros = [r for r in todos if formato_semana(r.fecha) == semana_filtro]
    else:
        registros = query.all()

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
