import io
import json
import datetime
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from app.extensions import db
from app.shared.models import (
    Rotation, FumigationOrder, FumigationOrderDetail, FumigationOrderProductSummary,
    WarehouseDispatchLog, Product, Crop, User
)
from app.modules.fumigacion.services.order_service import OrderService
from app.shared.utils import (
    get_operator_for_zone, get_toxicological_color_info, 
    get_local_now, format_local_datetime, is_integer_unit, round_product_amount,
    is_liquid_unit, is_solid_unit
)
from app.shared.audit import record_audit
from app.modules.auth.routes import login_required, permission_required

bodega_bp = Blueprint('bodega', __name__)


@bodega_bp.route('/')
@bodega_bp.route('/salidas')
@login_required
@permission_required('salidas_ver', 'bodega')
def salidas():
    """
    Centro de Salidas de Bodega:
    Muestra únicamente las salidas y requerimientos de insumos de las rotaciones
    y órdenes OFICIALES APROBADAS por los agrónomos / asistentes.
    """
    # Query only approved rotations
    approved_rotations = Rotation.query.filter(Rotation.status == 'APROBADA').order_by(Rotation.week.desc(), Rotation.version.desc()).all()
    
    if not approved_rotations:
        flash("Aún no hay planificaciones oficiales aprobadas en el sistema para despacho de bodega.", "info")
        return render_template(
            'bodega/salidas.html',
            rotation=None,
            orders=[],
            scheduled_days=[],
            data_by_area={},
            general_summary={},
            product_info={},
            all_rotations=[],
            prev_rotation=None,
            next_rotation=None,
            dispatch_logs=[],
            total_products_count=0,
            total_volume_liters=0.0,
            total_beds=0.0
        )

    # Determine which rotation to display
    target_id = request.args.get('rotation_id', type=int)
    target_week = request.args.get('week', '').strip()

    current_rot = None
    if target_id:
        current_rot = Rotation.query.filter_by(id=target_id, status='APROBADA').first()
    elif target_week:
        current_rot = Rotation.query.filter_by(week=target_week, status='APROBADA').order_by(Rotation.version.desc()).first()

    if not current_rot:
        current_rot = approved_rotations[0]

    # Calculate previous and next rotation in chronological sequence
    prev_rotation = None
    next_rotation = None
    for idx, r in enumerate(approved_rotations):
        if r.id == current_rot.id:
            if idx > 0:
                next_rotation = approved_rotations[idx - 1]
            if idx < len(approved_rotations) - 1:
                prev_rotation = approved_rotations[idx + 1]
            break

    # Get ONLY official approved orders for this rotation AND any additional extra application orders for this week
    orders = FumigationOrder.query.filter(
        db.or_(
            FumigationOrder.rotation_id == current_rot.id,
            FumigationOrder.week == current_rot.week
        ),
        FumigationOrder.status.in_(['APROBADA', 'EJECUTADA'])
    ).order_by(FumigationOrder.round_number.asc()).all()

    # Filter by specific program if requested
    target_order_id = request.args.get('order_id', type=int)
    selected_order = None
    if target_order_id:
        selected_order = next((o for o in orders if o.id == target_order_id), None)

    display_orders = [selected_order] if selected_order else orders

    # Get dispatch & print logs for this week
    dispatch_logs = WarehouseDispatchLog.query.filter_by(week=current_rot.week).order_by(WarehouseDispatchLog.created_at.desc()).limit(20).all()

    # Get unique scheduled days in execution order
    scheduled_days = []
    for ord in display_orders:
        if ord.scheduled_day and ord.scheduled_day not in scheduled_days:
            scheduled_days.append(ord.scheduled_day)

    # Build data structure grouped by area and category
    data_by_area = {}
    data_by_category = {
        'GYPSOPHILA': {},
        'PRODUCTOS_NUEVOS': {},
        'PIV': {}
    }
    product_info = {}
    from app.shared.utils import get_crop_category

    for order in display_orders:
        for detail in order.details:
            area = (detail.crop_name or 'GENERAL').strip()
            cat = get_crop_category(area)
            product_code = (detail.product_code or '').strip()
            day = order.scheduled_day
            amount = float(detail.product_amount or 0.0)

            if not product_code or product_code == 'SIN PRODUCTO':
                continue

            # Group by Area
            if area not in data_by_area:
                data_by_area[area] = {
                    'category': cat,
                    'products': {}
                }
            if product_code not in data_by_area[area]['products']:
                data_by_area[area]['products'][product_code] = {d: 0.0 for d in scheduled_days}

            # Group by Operational Category (Gypsophila, Productos Nuevos, PIV)
            if cat not in data_by_category:
                data_by_category[cat] = {}
            if product_code not in data_by_category[cat]:
                data_by_category[cat][product_code] = {d: 0.0 for d in scheduled_days}

            if product_code not in product_info:
                product_info[product_code] = {
                    'commercial_name': detail.commercial_name or product_code,
                    'unit': detail.unit or 'CC',
                    'category': cat,
                    'pest': detail.pest or ''
                }

            if day in data_by_area[area]['products'][product_code]:
                data_by_area[area]['products'][product_code][day] += amount

            if day in data_by_category[cat][product_code]:
                data_by_category[cat][product_code][day] += amount

    # Build general summary (without area grouping)
    general_summary = {}
    for area_info in data_by_area.values():
        for product_code, day_quantities in area_info['products'].items():
            if product_code not in general_summary:
                general_summary[product_code] = {d: 0.0 for d in scheduled_days}
            for day, qty in day_quantities.items():
                if day in general_summary[product_code]:
                    general_summary[product_code][day] += qty

    consolidated_products = []
    for product_code, day_quantities in sorted(general_summary.items(), key=lambda x: (product_info.get(x[0], {}).get('commercial_name', x[0]))):
        tot_q = sum(day_quantities.values())
        if tot_q > 0:
            consolidated_products.append({
                'product_code': product_code,
                'commercial_name': product_info.get(product_code, {}).get('commercial_name', product_code),
                'unit': product_info.get(product_code, {}).get('unit', 'CC'),
                'category': product_info.get(product_code, {}).get('category', ''),
                'pest': product_info.get(product_code, {}).get('pest', ''),
                'days': day_quantities,
                'total_quantity': round(tot_q, 2)
            })

    # Segmented consolidated by operational category (Gypsophila, Productos Nuevos/Variedades, PIV)
    consolidated_by_category = {
        'ALL': consolidated_products,
        'GYPSOPHILA': [],
        'PRODUCTOS_NUEVOS': [],
        'PIV': []
    }
    for cat_key in ['GYPSOPHILA', 'PRODUCTOS_NUEVOS', 'PIV']:
        cat_summary = {}
        for area_name, area_info in data_by_area.items():
            if area_info.get('category') == cat_key:
                for p_code, day_qtys in area_info['products'].items():
                    if p_code not in cat_summary:
                        cat_summary[p_code] = {d: 0.0 for d in scheduled_days}
                    for d, q in day_qtys.items():
                        if d in cat_summary[p_code]:
                            cat_summary[p_code][d] += q

        for p_code, day_qtys in sorted(cat_summary.items(), key=lambda x: (product_info.get(x[0], {}).get('commercial_name', x[0]))):
            tot_q = sum(day_qtys.values())
            if tot_q > 0:
                consolidated_by_category[cat_key].append({
                    'product_code': p_code,
                    'commercial_name': product_info.get(p_code, {}).get('commercial_name', p_code),
                    'unit': product_info.get(p_code, {}).get('unit', 'CC'),
                    'category': cat_key,
                    'pest': product_info.get(p_code, {}).get('pest', ''),
                    'days': day_qtys,
                    'total_quantity': round(tot_q, 2)
                })

    weighed_products = []
    if current_rot.weighed_products_json:
        try:
            weighed_products = json.loads(current_rot.weighed_products_json)
        except Exception:
            weighed_products = []

    total_products_count = len(product_info)
    total_volume_liters = sum(ord.total_liters for ord in display_orders)
    total_beds = sum(ord.total_standard_beds for ord in display_orders)

    # Obtener calibración de Trichos y Sólidos para esta semana (para Kelly / Almacén & Estadística)
    from app.modules.fertilizacion.models import DrenchItem
    from app.modules.fertilizacion.routes import procesar_calibracion_fertilizacion
    w_target = str(current_rot.week).strip()
    w_clean = w_target.replace('-', '').replace('20', '', 1) if w_target.startswith('20') else w_target
    drench_records = DrenchItem.query.filter(
        db.or_(
            DrenchItem.semana == w_target,
            DrenchItem.semana == w_clean,
            DrenchItem.semana == f"20{w_clean}" if len(w_clean) == 4 else False
        )
    ).all()
    trichos_calibracion_ordenada, trichos_calibracion_tecnicos, trichos_calibracion_bombas = procesar_calibracion_fertilizacion(drench_records)

    return render_template(
        'bodega/salidas.html',
        rotation=current_rot,
        orders=orders,
        display_orders=display_orders,
        selected_order=selected_order,
        scheduled_days=scheduled_days,
        data_by_area=data_by_area,
        data_by_category=data_by_category,
        general_summary=general_summary,
        consolidated_products=consolidated_products,
        consolidated_by_category=consolidated_by_category,
        weighed_products=weighed_products,
        product_info=product_info,
        all_rotations=approved_rotations,
        prev_rotation=prev_rotation,
        next_rotation=next_rotation,
        dispatch_logs=dispatch_logs,
        total_products_count=total_products_count,
        total_volume_liters=total_volume_liters,
        total_beds=total_beds,
        trichos_calibracion_ordenada=trichos_calibracion_ordenada,
        trichos_calibracion_tecnicos=trichos_calibracion_tecnicos,
        trichos_calibracion_bombas=trichos_calibracion_bombas
    )


@bodega_bp.route('/ordenes')
@login_required
@permission_required('bodega')
def ordenes():
    """
    Listado oficial de órdenes de fumigación aprobadas listas para despacho en bodega.
    """
    week_filter = request.args.get('week', '').strip()
    
    query = FumigationOrder.query.outerjoin(Rotation).filter(
        db.or_(
            db.and_(Rotation.status == 'APROBADA', FumigationOrder.status.in_(['APROBADA', 'EJECUTADA'])),
            db.and_(FumigationOrder.round_number == 99, FumigationOrder.status.in_(['APROBADA', 'EJECUTADA']))
        )
    )

    if week_filter and week_filter != 'all':
        query = query.filter(FumigationOrder.week == week_filter)

    official_orders = query.order_by(FumigationOrder.week.desc(), FumigationOrder.round_number.asc()).all()

    # Get distinct weeks of approved orders for filter
    approved_weeks = [r.week for r in Rotation.query.filter(Rotation.status == 'APROBADA').order_by(Rotation.week.desc()).all()]
    extra_weeks = [o.week for o in FumigationOrder.query.filter_by(round_number=99).all()]
    approved_weeks = list(dict.fromkeys(approved_weeks + extra_weeks))

    return render_template(
        'bodega/ordenes.html',
        orders=official_orders,
        approved_weeks=approved_weeks,
        selected_week=week_filter
    )


@bodega_bp.route('/ordenes/<int:order_id>')
@login_required
@permission_required('bodega')
def orden_detalle(order_id):
    """
    Vista detallada de la orden oficial en el sistema para el personal de bodega.
    """
    order = FumigationOrder.query.get_or_404(order_id)
    
    # Structure details grouped by Zone / Operator
    zones_map = {}
    distinct_products_map = {}

    for d in order.details:
        z_name = d.zone or 'ZONA GENERAL'
        if z_name not in zones_map:
            zones_map[z_name] = {
                'zone_name': z_name,
                'operator': d.operator,
                'details': [],
                'total_beds': 0.0,
                'total_liters': 0.0,
                'products_by_code': {}
            }
        zones_map[z_name]['details'].append(d)
        zones_map[z_name]['total_beds'] += (d.standard_beds or 0.0)
        zones_map[z_name]['total_liters'] += (d.total_liters or 0.0)

        if d.product_code and d.product_code != 'SIN PRODUCTO':
            u = d.unit or 'CC'
            is_liq = is_liquid_unit(u)
            is_sol = is_solid_unit(u)

            # Accumulate in zone
            if d.product_code not in zones_map[z_name]['products_by_code']:
                zones_map[z_name]['products_by_code'][d.product_code] = {
                    'product_code': d.product_code,
                    'commercial_name': d.commercial_name or d.product_code,
                    'unit': u,
                    'dose': d.dose,
                    'is_liquid': is_liq,
                    'is_solid': is_sol,
                    'total_amount': 0.0,
                    'pest': d.pest
                }
            zones_map[z_name]['products_by_code'][d.product_code]['total_amount'] += (d.product_amount or 0.0)

            # Accumulate in overall
            if d.product_code not in distinct_products_map:
                distinct_products_map[d.product_code] = {
                    'code': d.product_code,
                    'name': d.commercial_name or d.product_code,
                    'unit': u,
                    'dose': d.dose,
                    'is_liquid': is_liq,
                    'is_solid': is_sol,
                    'pest': d.pest,
                    'color': d.toxicological_color,
                    'category': d.toxicological_category,
                    'total_amount': 0.0
                }
            distinct_products_map[d.product_code]['total_amount'] += (d.product_amount or 0.0)

    # Process liquids and solids per zone
    zones_list = list(zones_map.values())
    zones_list.sort(key=lambda z: z['zone_name'])
    for z in zones_list:
        prods = list(z['products_by_code'].values())
        z['liquid_products'] = [p for p in prods if p['is_liquid']]
        z['solid_products'] = [p for p in prods if not p['is_liquid']]

    all_products = list(distinct_products_map.values())
    overall_liquids = [p for p in all_products if p['is_liquid']]
    overall_solids = [p for p in all_products if not p['is_liquid']]

    weighed_products = []
    if order.rotation and order.rotation.weighed_products_json:
        try:
            weighed_products = json.loads(order.rotation.weighed_products_json)
        except Exception:
            weighed_products = []

    return render_template(
        'bodega/orden_detalle.html',
        order=order,
        rotation=order.rotation,
        zones_list=zones_list,
        products_summary=order.products_summary,
        distinct_products=all_products,
        overall_liquids=overall_liquids,
        overall_solids=overall_solids,
        weighed_products=weighed_products
    )


@bodega_bp.route('/ordenes/<int:order_id>/editar-titulo', methods=['POST'])
@login_required
@permission_required('bodega')
def orden_editar_titulo(order_id):
    """Actualiza el nombre / título del programa de fumigación"""
    order = FumigationOrder.query.get_or_404(order_id)
    if request.is_json:
        data = request.get_json() or {}
        new_title = data.get('title', '').strip()
    else:
        new_title = request.form.get('title', '').strip()
    
    if new_title:
        order.title = new_title
        db.session.commit()
        record_audit('BODEGA', 'UPDATE_PROGRAM_TITLE', 'FumigationOrder', order.id, details={'order_number': order.order_number, 'title': new_title})
    
    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': True, 'title': order.title or order.display_title})
    
    flash("Nombre del programa actualizado correctamente.", "success")
    return redirect(url_for('bodega.orden_detalle', order_id=order.id))



@bodega_bp.route('/rotacion/<int:rotation_id>/exportar-excel')
@login_required
@permission_required('salidas_ver', 'bodega')
def exportar_salidas_excel(rotation_id):
    """Exporta las salidas oficiales de bodega a Excel"""
    rotation = Rotation.query.filter_by(id=rotation_id, status='APROBADA').first_or_404()
    target_order_id = request.args.get('order_id', type=int)

    orders_query = FumigationOrder.query.filter(
        FumigationOrder.rotation_id == rotation.id,
        FumigationOrder.status.in_(['APROBADA', 'EJECUTADA'])
    ).order_by(FumigationOrder.round_number.asc())

    if target_order_id:
        orders_query = orders_query.filter(FumigationOrder.id == target_order_id)

    orders = orders_query.all()

    if not orders:
        flash("No hay órdenes oficiales aprobadas para esta consulta.", "warning")
        return redirect(url_for('bodega.salidas', rotation_id=rotation_id))

    # 1. Build flat detailed database rows (exact reflection of program view across all orders/vueltas)
    all_details = [d for o in orders for d in o.details]
    flat_rows = OrderService.get_order_details_rows(all_details)
    df_flat = pd.DataFrame(flat_rows)

    # 2. Build summary matrix by area and scheduled day
    scheduled_days = []
    for ord in orders:
        if ord.scheduled_day and ord.scheduled_day not in scheduled_days:
            scheduled_days.append(ord.scheduled_day)

    data_by_area = {}
    product_info = {}

    for order in orders:
        for detail in order.details:
            area = (detail.crop_name or 'GENERAL').strip()
            product_code = (detail.product_code or '').strip()
            day = order.scheduled_day
            amount = float(detail.product_amount or 0.0)

            if not product_code or product_code == 'SIN PRODUCTO':
                continue

            if area not in data_by_area:
                data_by_area[area] = {}
            if product_code not in data_by_area[area]:
                data_by_area[area][product_code] = {d: 0.0 for d in scheduled_days}

            if product_code not in product_info:
                product_info[product_code] = {
                    'commercial_name': detail.commercial_name or product_code,
                    'unit': detail.unit or 'CC'
                }

            if day in data_by_area[area][product_code]:
                data_by_area[area][product_code][day] += amount

    rows_matrix = []
    for area in sorted(data_by_area.keys()):
        for product_code in sorted(data_by_area[area].keys()):
            u = product_info[product_code]['unit']
            is_int = is_integer_unit(u)

            row = {
                'ÁREA / VARIEDAD': area,
                'PRODUCTO AGROQUÍMICO': product_info[product_code]['commercial_name'],
                'U.M': u
            }
            total = 0.0
            for day in scheduled_days:
                qty = data_by_area[area][product_code][day]
                row[day] = int(round(qty)) if is_int else round(qty, 1)
                total += qty
            row['Total General Despacho'] = int(round(total)) if is_int else round(total, 1)
            rows_matrix.append(row)

    df_matrix = pd.DataFrame(rows_matrix)

    # 3. Build Pivot-style dynamic sheet (Tabla Dinámica de Salidas por Vuelta y Día)
    dynamic_rows = []
    for order in orders:
        v_name = order.round_name or f"Vuelta {order.round_number}"
        v_day = order.scheduled_day or ''
        v_label = f"{v_name} ({v_day})" if v_day else v_name

        order_crop_prods = {}
        for detail in order.details:
            area = (detail.crop_name or 'GENERAL').strip()
            p_code = (detail.product_code or '').strip()
            amount = float(detail.product_amount or 0.0)
            if not p_code or p_code == 'SIN PRODUCTO':
                continue

            key = (area, p_code)
            if key not in order_crop_prods:
                order_crop_prods[key] = 0.0
            order_crop_prods[key] += amount

        for (area, p_code), qty in sorted(order_crop_prods.items(), key=lambda x: (x[0][0], product_info.get(x[0][1], {}).get('commercial_name', ''))):
            p_meta = product_info.get(p_code, {'commercial_name': p_code, 'unit': 'CC'})
            u = p_meta['unit']
            is_int = is_integer_unit(u)

            d_row = {
                'VUELTA': v_label,
                'CULTIVO': area,
                'PRODUCTO AGROQUÍMICO': p_meta['commercial_name'],
                'U.M': u
            }
            for day in scheduled_days:
                if day == v_day:
                    d_row[day] = int(round(qty)) if is_int else round(qty, 1)
                else:
                    d_row[day] = 0

            d_row['TOTAL VUELTA'] = int(round(qty)) if is_int else round(qty, 1)
            dynamic_rows.append(d_row)

    df_dynamic = pd.DataFrame(dynamic_rows)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        sheet_flat_name = f"Programa_Sem_{rotation.week}"[:31]
        sheet_matrix_name = f"Matriz_Salidas_{rotation.week}"[:31]
        sheet_dynamic_name = "Tabla_Dinamica_Salidas"

        df_flat.to_excel(writer, sheet_name=sheet_flat_name, index=False)
        if not df_matrix.empty:
            df_matrix.to_excel(writer, sheet_name=sheet_matrix_name, index=False)
        if not df_dynamic.empty:
            df_dynamic.to_excel(writer, sheet_name=sheet_dynamic_name, index=False)

        # Style sheet_flat_name
        ws_flat = writer.sheets[sheet_flat_name]
        ws_flat.freeze_panes = 'A2'
        for col in ws_flat.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = col[0].column_letter
            ws_flat.column_dimensions[col_letter].width = max(max_len + 3, 11)

        # Style sheet_matrix_name
        if sheet_matrix_name in writer.sheets:
            ws_matrix = writer.sheets[sheet_matrix_name]
            ws_matrix.freeze_panes = 'A2'
            for col in ws_matrix.columns:
                max_len = max(len(str(cell.value or '')) for cell in col)
                col_letter = col[0].column_letter
                ws_matrix.column_dimensions[col_letter].width = max(max_len + 4, 12)

        # Style sheet_dynamic_name (Tabla Dinámica para Bodega)
        if sheet_dynamic_name in writer.sheets:
            ws_dyn = writer.sheets[sheet_dynamic_name]
            ws_dyn.freeze_panes = 'A2'
            for col in ws_dyn.columns:
                max_len = max(len(str(cell.value or '')) for cell in col)
                col_letter = col[0].column_letter
                ws_dyn.column_dimensions[col_letter].width = max(max_len + 4, 13)

    output.seek(0)
    filename = f"Salidas_Bodega_Semana_{rotation.week}.xlsx"

    # Log action
    user_name = session.get('full_name') or session.get('username') or 'Usuario Bodega'
    log_entry = WarehouseDispatchLog(
        week=rotation.week,
        rotation_id=rotation.id,
        action='EXPORTAR_EXCEL',
        performed_by=user_name,
        role=session.get('role', 'BODEGA'),
        notes=f"Exportación de planilla de salidas de bodega Semana {rotation.week} a Excel."
    )
    db.session.add(log_entry)
    db.session.commit()

    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )


@bodega_bp.route('/ordenes/<int:order_id>/exportar-excel')
@login_required
@permission_required('bodega')
def orden_exportar_excel(order_id):
    """Exporta la orden oficial a Excel para el personal de bodega"""
    order = FumigationOrder.query.get_or_404(order_id)
    excel_stream = OrderService.export_order_to_excel(order)
    filename = f"Orden_Oficial_{order.order_number}.xlsx"
    return send_file(
        excel_stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )


@bodega_bp.route('/rotaciones/<int:rotation_id>/marcar-impresa', methods=['POST'])
@login_required
@permission_required('salidas_ver', 'bodega')
def marcar_impresa(rotation_id):
    """Marca la planilla de salidas como impresa y registra log"""
    rotation = Rotation.query.filter_by(id=rotation_id, status='APROBADA').first_or_404()
    user_name = session.get('full_name') or session.get('username') or 'Usuario Bodega'
    user_role = session.get('role') or 'BODEGA'

    rotation.is_salidas_printed = True
    rotation.salidas_printed_at = get_local_now()
    rotation.salidas_printed_by = user_name

    for ord in rotation.orders:
        if ord.status in ['APROBADA', 'EJECUTADA']:
            ord.is_printed = True
            ord.printed_at = get_local_now()
            ord.printed_by = user_name

    log_entry = WarehouseDispatchLog(
        week=rotation.week,
        rotation_id=rotation.id,
        action='IMPRESION_SALIDAS',
        performed_by=user_name,
        role=user_role,
        notes=f"Planilla de salidas de la Semana {rotation.week} impresa por Bodega."
    )
    db.session.add(log_entry)
    db.session.commit()

    record_audit('BODEGA', 'PRINT_SALIDAS', 'Rotation', rotation.id, details={'week': rotation.week, 'user': user_name})

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'success': True,
            'is_printed': True,
            'printed_at': format_local_datetime(rotation.salidas_printed_at),
            'printed_by': rotation.salidas_printed_by
        })

    flash(f"Planilla de salidas de la Semana {rotation.week} marcada como IMPRESA.", "success")
    return redirect(url_for('bodega.salidas', rotation_id=rotation.id))


@bodega_bp.route('/rotaciones/<int:rotation_id>/marcar-despachada', methods=['POST'])
@login_required
@permission_required('bodega')
def marcar_despachada(rotation_id):
    """Alterna el estado de entrega física de insumos en bodega"""
    rotation = Rotation.query.filter_by(id=rotation_id, status='APROBADA').first_or_404()
    user_name = session.get('full_name') or session.get('username') or 'Usuario Bodega'
    user_role = session.get('role') or 'BODEGA'

    rotation.is_salidas_dispatched = not rotation.is_salidas_dispatched
    if rotation.is_salidas_dispatched:
        rotation.salidas_dispatched_at = get_local_now()
        rotation.salidas_dispatched_by = user_name
        status_action = "DESPACHO_BODEGA"
        msg_text = f"Insumos de la Semana {rotation.week} marcados como ENTREGADOS A OPERARIOS."
    else:
        rotation.salidas_dispatched_at = None
        rotation.salidas_dispatched_by = None
        status_action = "REVERTIR_DESPACHO"
        msg_text = f"Despacho de la Semana {rotation.week} devuelto a estado PENDIENTE."

    log_entry = WarehouseDispatchLog(
        week=rotation.week,
        rotation_id=rotation.id,
        action=status_action,
        performed_by=user_name,
        role=user_role,
        notes=msg_text
    )
    db.session.add(log_entry)
    db.session.commit()

    record_audit('BODEGA', status_action, 'Rotation', rotation.id, details={'week': rotation.week, 'dispatched': rotation.is_salidas_dispatched})

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'success': True,
            'is_dispatched': rotation.is_salidas_dispatched,
            'dispatched_at': format_local_datetime(rotation.salidas_dispatched_at) if rotation.salidas_dispatched_at else None,
            'dispatched_by': rotation.salidas_dispatched_by or ''
        })

    flash(msg_text, "success" if rotation.is_salidas_dispatched else "info")
    return redirect(url_for('bodega.salidas', rotation_id=rotation.id))


@bodega_bp.route('/rotaciones/<int:rotation_id>/marcar-salidas-digitadas', methods=['POST'])
@login_required
@permission_required('salidas_ver', 'bodega')
def marcar_salidas_digitadas(rotation_id):
    """Marca o desmarca si las salidas de insumos ya fueron digitadas en el sistema contable / administrativo"""
    rotation = Rotation.query.filter_by(id=rotation_id, status='APROBADA').first_or_404()
    user_name = session.get('full_name') or session.get('username') or 'Estadística'
    user_role = session.get('role') or 'ESTADISTICA'

    rotation.is_salidas_digitadas = not rotation.is_salidas_digitadas
    if rotation.is_salidas_digitadas:
        rotation.salidas_digitadas_at = get_local_now()
        rotation.salidas_digitadas_by = user_name
        status_action = "SALIDAS_DIGITADAS"
        msg_text = f"Salidas de la Semana {rotation.week} marcadas como DIGITADAS EN EL SISTEMA por {user_name}."
    else:
        rotation.salidas_digitadas_at = None
        rotation.salidas_digitadas_by = None
        status_action = "REVERTIR_SALIDAS_DIGITADAS"
        msg_text = f"Estado de digitación de salidas de la Semana {rotation.week} devuelto a PENDIENTE."

    log_entry = WarehouseDispatchLog(
        week=rotation.week,
        rotation_id=rotation.id,
        action=status_action,
        performed_by=user_name,
        role=user_role,
        notes=msg_text
    )
    db.session.add(log_entry)
    db.session.commit()

    record_audit('ESTADISTICA', status_action, 'Rotation', rotation.id, details={'week': rotation.week, 'digitadas': rotation.is_salidas_digitadas})

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'success': True,
            'is_digitadas': rotation.is_salidas_digitadas,
            'digitadas_at': format_local_datetime(rotation.salidas_digitadas_at) if rotation.salidas_digitadas_at else None,
            'digitadas_by': rotation.salidas_digitadas_by or ''
        })

    flash(msg_text, "success" if rotation.is_salidas_digitadas else "info")
    return redirect(url_for('bodega.salidas', rotation_id=rotation.id))


@bodega_bp.route('/rotaciones/<int:rotation_id>/toggle-pesado', methods=['POST'])
@login_required
def toggle_pesado(rotation_id):
    """
    Permite registrar o desmarcar el estado de pesado en balanza de un producto químico.
    Exclusivo para personal de Bodega/Almacén y Administradores.
    """
    user_id = session.get('user_id')
    user = User.query.get(user_id) if user_id else None
    user_role = (user.role if user else session.get('role')) or ''

    if user_role not in ['BODEGA', 'ADMIN']:
        return jsonify({
            'success': False,
            'error': 'Permiso denegado. Solo el personal de Bodega / Almacén puede registrar el pesaje de insumos.'
        }), 403

    rotation = Rotation.query.filter_by(id=rotation_id, status='APROBADA').first_or_404()
    data = request.get_json() or {}
    product_code = (data.get('product_code') or '').strip()
    is_weighed = bool(data.get('weighed', False))

    if not product_code:
        return jsonify({'success': False, 'error': 'Código de producto requerido.'}), 400

    weighed_list = []
    if rotation.weighed_products_json:
        try:
            weighed_list = json.loads(rotation.weighed_products_json)
        except Exception:
            weighed_list = []

    if is_weighed:
        if product_code not in weighed_list:
            weighed_list.append(product_code)
    else:
        if product_code in weighed_list:
            weighed_list.remove(product_code)

    rotation.weighed_products_json = json.dumps(weighed_list)
    db.session.commit()

    return jsonify({
        'success': True,
        'product_code': product_code,
        'is_weighed': is_weighed,
        'weighed_products': weighed_list
    })


