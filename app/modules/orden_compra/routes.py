import datetime
import io
import json
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from app.extensions import db
from app.shared.models import Crop, Product, Litraje, CropStateRecord, Rotation, Requisition, RequisitionItem, FumigationOrder
from app.modules.fumigacion.services.calculation_engine import CalculationEngine
from app.modules.fumigacion.services.requisition_service import RequisitionService
from app.shared.utils import get_local_now, format_product_amount, format_age, get_week_difference
from app.shared.audit import record_audit
from app.modules.auth.routes import login_required, permission_required

orden_compra_bp = Blueprint('orden_compra', __name__)

@orden_compra_bp.route('/', methods=['GET', 'POST'])
@login_required
@permission_required('orden_compra')
def index():
    now = datetime.datetime.now()
    cur_year = now.year
    cur_week = now.isocalendar()[1]
    adv_week = cur_week + 2
    default_week = f"{cur_year}-{adv_week:02d}"

    # Get selected week from form POST or query string
    if request.method == 'POST':
        selected_week = request.form.get('week', default_week).strip()
    else:
        selected_week = request.args.get('week', default_week).strip()

    # Find active rotation for this week or latest rotation (excluding ELIMINADA)
    rotation = Rotation.query.filter(
        Rotation.week == selected_week,
        Rotation.status != 'ELIMINADA'
    ).order_by(
        db.case((Rotation.status == 'APROBADA', 1), else_=2),
        Rotation.version.desc()
    ).first()

    # Calculate Cut-Off Week (Selected Week - 2 weeks) for 15-day advance purchase
    try:
        parts = selected_week.split('-')
        sel_y = int(parts[0])
        sel_w = int(parts[1])
        cut_w = sel_w - 2
        cut_y = sel_y
        if cut_w <= 0:
            cut_w += 52
            cut_y -= 1
        cutoff_week = f"{cut_y}-{cut_w:02d}"
    except Exception:
        cutoff_week = selected_week

    # Find latest available crop state week in system (from highest field event / uploaded Excel)
    max_ev = db.session.execute(db.text("SELECT MAX(semana_campo) FROM fact_eventos_cultivo WHERE estado_evento = 'ACTIVO';")).scalar()
    if max_ev:
        s_ev = str(max_ev)
        if s_ev.isdigit() and len(s_ev) == 4:
            latest_available_week = f"20{s_ev[:2]}-{s_ev[2:]}"
        else:
            latest_available_week = s_ev
    else:
        latest_available_week = '2026-39'

    # Get bed configuration from rotation review data or calculate
    bed_configuration = []
    if rotation and rotation.review_data_by_round_json:
        try:
            r_data = json.loads(rotation.review_data_by_round_json)
            if r_data:
                # Use the first round's reviewed data or latest
                first_key = list(r_data.keys())[0]
                bed_configuration = r_data[first_key]
        except Exception:
            bed_configuration = []
    elif rotation and rotation.review_data_json:
        try:
            bed_configuration = json.loads(rotation.review_data_json)
        except Exception:
            bed_configuration = []

    if not bed_configuration and rotation and rotation.rounds:
        try:
            first_round = rotation.rounds[0]
            calc = CalculationEngine.calculate_round(first_round)
            bed_configuration = calc.get('segments', [])
        except Exception:
            bed_configuration = []

    # If no rotation exists for this week yet, find latest or create draft
    requisition = None
    if rotation:
        requisition = Requisition.query.filter_by(rotation_id=rotation.id).first()
        if not requisition:
            requisition = RequisitionService.generate_or_update_forecast(rotation.id)

    # Calculate summary per variety / crop (Base from bed configuration)
    variety_summary = {}
    base_standard_beds_sum = 0.0
    base_liters_sum = 0.0
    if bed_configuration:
        for seg in bed_configuration:
            if isinstance(seg, dict):
                crop = (seg.get('crop_name') or 'SIN CULTIVO').strip()
                std_beds = float(seg.get('standard_beds', 0) or 0)
                liters_bed = float(seg.get('liters_per_bed', 0) or 0)
                stage = (seg.get('phenological_stage') or 'VEGETATIVO').strip().upper()
                base_standard_beds_sum += std_beds
                base_liters_sum += (std_beds * liters_bed)

                if crop not in variety_summary:
                    variety_summary[crop] = {
                        'crop_name': crop,
                        'base_std_beds': 0.0,
                        'budget_std_beds': 0.0,
                        'total_std_beds': 0.0,
                        'veg_std_beds': 0.0,
                        'prod_std_beds': 0.0,
                        'base_liters': 0.0,
                        'budget_liters': 0.0,
                        'total_liters': 0.0,
                        'segments_count': 0
                    }
                variety_summary[crop]['base_std_beds'] += std_beds
                variety_summary[crop]['total_std_beds'] += std_beds
                if stage == 'VEGETATIVO':
                    variety_summary[crop]['veg_std_beds'] += std_beds
                else:
                    variety_summary[crop]['prod_std_beds'] += std_beds
                variety_summary[crop]['base_liters'] += (std_beds * liters_bed)
                variety_summary[crop]['total_liters'] += (std_beds * liters_bed)
                variety_summary[crop]['segments_count'] += 1

    # Include budget adjustments into variety summary
    budget_standard_beds_sum = 0.0
    budget_liters_sum = 0.0
    if requisition and requisition.budget_adjustments_json:
        try:
            budget_adjs = json.loads(requisition.budget_adjustments_json)
            for adj in budget_adjs:
                crop = (adj.get('crop_name') or 'SIN CULTIVO').strip()
                try:
                    beds = float(adj.get('beds', 0) or 0)
                except (ValueError, TypeError):
                    beds = 0.0
                age = int(adj.get('age', 2) or 2)
                stage = (adj.get('stage') or 'VEGETATIVO').strip().upper()
                
                # Liters per bed: custom if given, otherwise 3.0 L for VEG, 7.0 L for PROD, 5.0 L fallback
                liters_bed = None
                if adj.get('liters_per_bed'):
                    try:
                        liters_bed = float(adj.get('liters_per_bed'))
                    except (ValueError, TypeError):
                        liters_bed = None
                
                if not liters_bed or liters_bed <= 0:
                    lit_calc, found = CalculationEngine.get_liters_per_bed(crop, age)
                    if found and lit_calc > 0:
                        liters_bed = lit_calc
                    else:
                        if 'VEG' in stage or (age is not None and float(age) < 10):
                            liters_bed = 3.0
                        elif 'PROD' in stage:
                            liters_bed = 7.0
                        else:
                            liters_bed = 5.0
                
                adj['liters_per_bed'] = liters_bed

                # Count matching rounds for this crop
                rounds_count = 0
                if rotation:
                    for r in rotation.rounds:
                        if any(it.crop_name.strip().upper() == crop.upper() for it in r.items):
                            rounds_count += 1
                if rounds_count == 0:
                    rounds_count = 1

                add_liters = beds * liters_bed * rounds_count
                adj['total_liters'] = add_liters

                budget_standard_beds_sum += beds
                budget_liters_sum += add_liters

                if crop not in variety_summary:
                    variety_summary[crop] = {
                        'crop_name': crop,
                        'base_std_beds': 0.0,
                        'budget_std_beds': 0.0,
                        'total_std_beds': 0.0,
                        'veg_std_beds': 0.0,
                        'prod_std_beds': 0.0,
                        'base_liters': 0.0,
                        'budget_liters': 0.0,
                        'total_liters': 0.0,
                        'segments_count': 0
                    }
                variety_summary[crop]['budget_std_beds'] += beds
                variety_summary[crop]['total_std_beds'] += beds
                if stage == 'VEGETATIVO':
                    variety_summary[crop]['veg_std_beds'] += beds
                else:
                    variety_summary[crop]['prod_std_beds'] += beds
                variety_summary[crop]['budget_liters'] += add_liters
                variety_summary[crop]['total_liters'] += add_liters
        except Exception:
            budget_adjs = []
    else:
        budget_adjs = []

    total_standard_beds_sum = base_standard_beds_sum + budget_standard_beds_sum
    total_liters_sum = base_liters_sum + budget_liters_sum
    variety_summary_list = sorted(variety_summary.values(), key=lambda x: x['crop_name'])

    # Calculate real executed beds summary per crop / variety from official Fumigation Orders of this week
    variety_real_summary = {}
    real_std_beds_sum = 0.0
    real_liters_sum = 0.0
    real_segments_count = 0
    all_rot_ids = [r.id for r in Rotation.query.filter(Rotation.week == selected_week, Rotation.status != 'ELIMINADA').all()]
    if all_rot_ids:
        orders_for_week = FumigationOrder.query.filter(FumigationOrder.rotation_id.in_(all_rot_ids)).all()
        for ord in orders_for_week:
            seen_blocks_for_order = set()
            for d in ord.details:
                crop = (d.crop_name or 'SIN ESPECIFICAR').strip()
                # Deduplicate block segment in the same order so multi-products don't duplicate bed counts
                blk_key = (ord.id, d.block_name, d.suffix, d.bed_range)
                if blk_key not in seen_blocks_for_order:
                    seen_blocks_for_order.add(blk_key)
                    s_beds = float(d.standard_beds or 0.0)
                    l_mix = float(d.total_liters or 0.0)
                    real_std_beds_sum += s_beds
                    real_liters_sum += l_mix
                    real_segments_count += 1

                    if crop not in variety_real_summary:
                        variety_real_summary[crop] = {
                            'crop_name': crop,
                            'total_std_beds': 0.0,
                            'veg_std_beds': 0.0,
                            'prod_std_beds': 0.0,
                            'total_liters': 0.0,
                            'segments_count': 0,
                            'orders_set': set()
                        }
                    variety_real_summary[crop]['total_std_beds'] += s_beds
                    variety_real_summary[crop]['total_liters'] += l_mix
                    variety_real_summary[crop]['segments_count'] += 1
                    variety_real_summary[crop]['orders_set'].add(ord.order_number)

                    stg = (d.phenological_stage or 'VEGETATIVO').strip().upper()
                    if 'VEG' in stg:
                        variety_real_summary[crop]['veg_std_beds'] += s_beds
                    else:
                        variety_real_summary[crop]['prod_std_beds'] += s_beds

    for v in variety_real_summary.values():
        v['orders_count'] = len(v.pop('orders_set', []))

    variety_real_summary_list = sorted(variety_real_summary.values(), key=lambda x: x['crop_name'])

    # Convert to dicts for JSON serialization
    active_crops = [{'id': c.id, 'name': c.name} for c in Crop.query.filter_by(is_active=True).order_by(Crop.name.asc()).all()]
    active_products = [{'id': p.id, 'code': p.code, 'name': p.commercial_name} for p in Product.query.filter_by(is_active=True).order_by(Product.code.asc()).all()]

    # Block map - using matched or latest week (supporting both YYWW e.g. 2640 and YYYY-WW e.g. 2026-40)
    def _normalizar_sem(w_val):
        if not w_val:
            return ""
        s = str(w_val).strip().replace('-', '')
        if s.startswith('20') and len(s) == 6:
            return s[2:]
        return s

    available_cr_weeks = [w[0] for w in db.session.query(CropStateRecord.week).distinct().all() if w[0]]
    matched_cr_week = None
    cut_norm = _normalizar_sem(cutoff_week)
    sel_norm = _normalizar_sem(selected_week)

    for aw in available_cr_weeks:
        aw_norm = _normalizar_sem(aw)
        if aw_norm == cut_norm or aw_norm == sel_norm or aw == selected_week or aw == cutoff_week or get_week_difference(selected_week, aw) == 0:
            matched_cr_week = aw
            break
    if not matched_cr_week and available_cr_weeks:
        matched_cr_week = available_cr_weeks[0]

    block_records_query = db.session.query(
        CropStateRecord.block_full, 
        CropStateRecord.zone, 
        CropStateRecord.crop_master
    )
    if matched_cr_week:
        block_records_query = block_records_query.filter(CropStateRecord.week == matched_cr_week)
    block_records = block_records_query.distinct().all()
    block_zone_map = {b[0].strip(): {'zone': (b[1] or '').strip(), 'crop': (b[2] or '').strip()} for b in block_records if b[0]}

    # Count beds configured
    total_beds_count = len(bed_configuration) if bed_configuration else 0

    # Available weeks list for dropdown
    week_options = []
    for i in range(-1, 9):
        w_num = cur_week + i
        y_num = cur_year
        if w_num > 52:
            w_num -= 52
            y_num += 1
        label = f"Semana {w_num:02d} ({y_num})"
        val = f"{y_num}-{w_num:02d}"
        is_target = (i == 2)
        week_options.append({'value': val, 'label': label, 'is_target': is_target})

    # Consolidated Dynamic Pivot Data (15-Day Purchase vs Foliar + Drench + Trichos)
    pivot_data = RequisitionService.get_consolidated_pivot_data(selected_week)

    return render_template(
        'orden_compra/index_new.html',
        selected_week=selected_week,
        cutoff_week=cutoff_week,
        latest_available_week=latest_available_week,
        rotation=rotation,
        requisition=requisition,
        pivot_data=pivot_data,
        active_crops=active_crops,
        active_products=active_products,
        block_zone_map=block_zone_map,
        week_options=week_options,
        total_beds_count=total_beds_count,
        total_standard_beds_sum=total_standard_beds_sum,
        base_standard_beds_sum=base_standard_beds_sum,
        budget_standard_beds_sum=budget_standard_beds_sum,
        base_liters_sum=base_liters_sum,
        budget_liters_sum=budget_liters_sum,
        total_liters_sum=total_liters_sum,
        variety_summary_list=variety_summary_list,
        variety_real_summary_list=variety_real_summary_list,
        real_std_beds_sum=real_std_beds_sum,
        real_liters_sum=real_liters_sum,
        real_segments_count=real_segments_count,
        saved_budget_adjustments=budget_adjs,
        bed_configuration=bed_configuration
    )


@orden_compra_bp.route('/exportar-excel')
@login_required
@permission_required('orden_compra')
def exportar_excel():
    week = request.args.get('week')
    if not week:
        flash("Debe especificar una semana.", "danger")
        return redirect(url_for('orden_compra.index'))

    rot = Rotation.query.filter(
        Rotation.week == week,
        Rotation.status != 'ELIMINADA'
    ).order_by(
        db.case((Rotation.status == 'APROBADA', 1), else_=2),
        Rotation.version.desc()
    ).first()
    if not rot:
        flash(f"No se encontró rotación para la semana {week}.", "warning")
        return redirect(url_for('orden_compra.index'))

    req = Requisition.query.filter_by(rotation_id=rot.id).first()
    if not req:
        req = RequisitionService.generate_or_update_forecast(rot.id)

    excel_stream = RequisitionService.export_requisition_to_excel(req)
    filename = f"Orden_Compra_Agroquimicos_15Dias_{req.week}.xlsx"
    return send_file(
        excel_stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )


@orden_compra_bp.route('/recalculate-with-budget', methods=['POST'])
@login_required
@permission_required('orden_compra')
def recalculate_with_budget():
    """Recalcula la requisición sumando camas de presupuesto"""
    try:
        data = request.get_json()
        week = data.get('week')
        rotation_id = data.get('rotation_id')
        adjustments = data.get('adjustments', [])
        
        if not rotation_id:
            return jsonify({'success': False, 'error': 'No se especificó la rotación'}), 400
        
        rotation = Rotation.query.get(rotation_id)
        if not rotation:
            return jsonify({'success': False, 'error': 'Rotación no encontrada'}), 404
        
        # Get or create requisition
        requisition = Requisition.query.filter_by(rotation_id=rotation.id).first()
        if not requisition:
            requisition = RequisitionService.generate_or_update_forecast(rotation.id)
        
        # Save budget adjustments
        requisition.budget_adjustments_json = json.dumps(adjustments)
        db.session.commit()
        
        # Regenerate requisition with budget adjustments
        RequisitionService.recalculate_with_budget(requisition.id, adjustments)
        
        return jsonify({'success': True, 'message': 'Requisición recalculada con presupuesto'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@orden_compra_bp.route('/approve-order', methods=['POST'])
@login_required
@permission_required('orden_compra')
def approve_order():
    """Aprueba y congela la orden de compra"""
    try:
        data = request.get_json() if request.is_json else request.form
        week = data.get('week')
        rotation_id = data.get('rotation_id')
        
        if not rotation_id:
            return jsonify({'success': False, 'error': 'No se especificó la rotación'}), 400
        
        rotation = Rotation.query.get(rotation_id)
        if not rotation:
            return jsonify({'success': False, 'error': 'Rotación no encontrada'}), 404
        
        requisition = Requisition.query.filter_by(rotation_id=rotation.id).first()
        if not requisition:
            return jsonify({'success': False, 'error': 'No existe requisición para aprobar'}), 404
        
        # Approve requisition (frozen state)
        user_name = session.get('username') or session.get('name') or 'Ing. Agrónomo'
        requisition.status = 'APROBADO'
        requisition.approved_by = user_name
        requisition.approved_at = get_local_now()
        
        db.session.commit()
        record_audit('ORDEN_COMPRA', 'APPROVE', 'Requisition', requisition.id, details={'week': rotation.week, 'user': user_name, 'total_liters': requisition.total_liters})
        
        return jsonify({'success': True, 'message': f'Orden de compra aprobada exitosamente para la Semana {rotation.week}.'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@orden_compra_bp.route('/eliminar-orden', methods=['POST'])
@login_required
@permission_required('orden_compra')
def eliminar_orden():
    """Solo el Administrador puede eliminar/reiniciar una Orden de Compra aprobada"""
    try:
        if session.get('role') != 'ADMIN':
            return jsonify({
                'success': False, 
                'error': 'Permiso denegado: Solo los usuarios con rol Administrador pueden eliminar o reiniciar una Orden de Compra.'
            }), 403

        data = request.get_json() if request.is_json else request.form
        rotation_id = data.get('rotation_id')
        week = data.get('week')

        if not rotation_id:
            return jsonify({'success': False, 'error': 'No se especificó la rotación'}), 400

        rotation = Rotation.query.get(rotation_id)
        if not rotation:
            return jsonify({'success': False, 'error': 'Rotación no encontrada'}), 404

        requisition = Requisition.query.filter_by(rotation_id=rotation.id).first()
        if not requisition:
            return jsonify({'success': False, 'error': 'No existe orden de compra para eliminar'}), 404

        req_id = requisition.id
        req_week = requisition.week
        RequisitionItem.query.filter_by(requisition_id=req_id).delete()
        db.session.delete(requisition)
        db.session.commit()

        record_audit('ORDEN_COMPRA', 'DELETE_REQUISITION', 'Requisition', req_id, details={'week': req_week, 'admin': session.get('username')})

        return jsonify({'success': True, 'message': f'Orden de compra de la Semana {req_week} eliminada correctamente. Ya puedes recalcular nuevamente.'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
