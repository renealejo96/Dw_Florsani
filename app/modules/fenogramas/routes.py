import os
import io
import csv
import re
from datetime import datetime
from collections import defaultdict
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, Response, flash, session
from werkzeug.utils import secure_filename
from sqlalchemy import text
from app.extensions import db
from app.modules.auth.routes import login_required, permission_required, admin_required
from app.modules.fenogramas.models import (
    SemanaEvaluacion, MatrizFenograma, PlanoCultivo, Calificacion
)
from app.modules.fenogramas.services.fenograma_service import (
    load_mapeos_config, save_mapeos_config,
    preview_excel_sync, sync_excel_to_db,
    process_matriz_fenogramas,
    sync_from_mrt_estado_cultivo, cerrar_semana_evaluacion,
    obtener_datos_dashboard, MAX_SEMANAS_ARRASTRE_DEFAULT,
    clean_block_code
)

fenogramas_bp = Blueprint('fenogramas', __name__, url_prefix='/fenogramas')


def _natural_block_key(blq_code):
    """Ordenamiento natural de bloques: 1, 2, 10, 13A, 71, 72, 100."""
    cleaned = clean_block_code(blq_code)
    parts = re.split(r'(\d+)', str(cleaned or '').strip())
    return [int(p) if p.isdigit() else p.upper() for p in parts]


# ==============================================================================
# 1. ENTORNO OPERARIOS: CUADRÍCULA VISUAL DE BLOQUES + MATRIZ DE CALIFICACIÓN
# ==============================================================================
@fenogramas_bp.route('/')
@login_required
@permission_required('fenogramas')
def index():
    # Obtener semanas de evaluación disponibles
    try:
        curr_cal_sc = db.session.execute(text("SELECT semana_campo FROM dim_calendario_agricola WHERE fecha = CURRENT_DATE LIMIT 1;")).scalar() or 2643
        sem_cal_hoy_default = int(curr_cal_sc) % 100
    except Exception:
        db.session.rollback()
        sem_cal_hoy_default = 43

    # Obtener semanas de evaluación disponibles
    semanas_query = db.session.execute(text("SELECT semana_anio, estado FROM semanas_evaluacion ORDER BY semana_anio ASC;")).fetchall()
    semanas = [dict(s._mapping) for s in semanas_query]

    # Si no hay semanas en BD, inicializar de forma limpia con la semana de corte actual (ej. 43)
    if not semanas:
        sem_default = SemanaEvaluacion(semana_anio=sem_cal_hoy_default, anio=2026, estado='ABIERTA')
        db.session.add(sem_default)
        db.session.commit()
        semanas = [{'semana_anio': sem_cal_hoy_default, 'estado': 'ABIERTA'}]

    semanas_list = [s['semana_anio'] for s in semanas]
    semana_arg = request.args.get('semana', type=int)
    if semana_arg and semana_arg in semanas_list:
        semana = semana_arg
        session['fenogramas_semana_activa'] = semana
    elif session.get('fenogramas_semana_activa') in semanas_list:
        semana = session.get('fenogramas_semana_activa')
    else:
        # Por defecto seleccionar la semana actual
        semanas_actuales = [s for s in semanas_list if s <= sem_cal_hoy_default]
        semana = semanas_actuales[-1] if semanas_actuales else (semanas_list[-1] if semanas_list else sem_cal_hoy_default)
        session['fenogramas_semana_activa'] = semana

    zona_sel = request.args.get('zona', '').strip()
    bloque_sel = request.args.get('bloque', '').strip()

    # Resumen por zona para las pestañas superiores (Nivel 1)
    zonas_stats_rows = db.session.execute(text("""
        SELECT 
            zona,
            COUNT(DISTINCT bloque) as total_bloques,
            COUNT(CASE WHEN tiene_evaluacion = TRUE THEN 1 END) as total_labores,
            COUNT(CASE WHEN tiene_evaluacion = TRUE AND cumplido IN (0, 1) THEN 1 END) as calificadas,
            COUNT(CASE WHEN tiene_evaluacion = TRUE AND cumplido = 1 THEN 1 END) as cumplidas,
            COUNT(CASE WHEN tiene_evaluacion = TRUE AND cumplido = 0 THEN 1 END) as incumplidas,
            COUNT(CASE WHEN tiene_evaluacion = TRUE AND cumplido IS NULL THEN 1 END) as pendientes
        FROM calificaciones
        WHERE semana_anio = :semana AND zona IS NOT NULL AND zona != ''
        GROUP BY zona
        ORDER BY zona ASC;
    """), {'semana': semana}).fetchall()

    zonas = []
    zonas_resumen = {}
    for zr in zonas_stats_rows:
        z_name = zr.zona
        zonas.append(z_name)
        t_lab = int(zr.total_labores or 0)
        calif = int(zr.calificadas or 0)
        cumpl = int(zr.cumplidas or 0)
        zonas_resumen[z_name] = {
            'zona': z_name,
            'total_bloques': int(zr.total_bloques or 0),
            'total_labores': t_lab,
            'calificadas': calif,
            'cumplidas': cumpl,
            'incumplidas': int(zr.incumplidas or 0),
            'pendientes': int(zr.pendientes or 0),
            'pct_avance': round((calif / t_lab) * 100, 1) if t_lab > 0 else 0.0,
            'pct_cumplimiento': round((cumpl / t_lab) * 100, 1) if (t_lab > 0 and calif > 0) else None
        }

    if (not zona_sel or zona_sel not in zonas) and zonas:
        zona_sel = zonas[0]

    # Cargar todas las calificaciones de la semana para soporte 100% Offline (campo sin señal)
    calif_rows = db.session.execute(text("""
        SELECT id, semana_anio, anio, zona, bloque, actividad_tipo, producto_maestro,
               cama_inicial, cama_final, sufijo, edad_real, labor, auxiliar, supervisor,
               cumplido, observacion, tiene_evaluacion, es_pendiente_anterior, semana_origen
        FROM calificaciones
        WHERE semana_anio = :semana
        ORDER BY zona ASC, cama_inicial ASC, id ASC;
    """), {'semana': semana}).fetchall()
    calificaciones = [dict(r._mapping) for r in calif_rows]

    raw_bloques = {}
    productos_set = set()
    responsables_set = set()

    for item in calificaciones:
        bl = str(item['bloque'] or '').strip()
        if not bl:
            continue
        if bl not in raw_bloques:
            raw_bloques[bl] = []
        raw_bloques[bl].append(item)
        if item.get('producto_maestro'):
            productos_set.add(item['producto_maestro'])
        if item.get('supervisor') and str(item['supervisor']).strip():
            responsables_set.add(str(item['supervisor']).strip())
        if item.get('auxiliar') and str(item['auxiliar']).strip():
            responsables_set.add(str(item['auxiliar']).strip())

    # Ordenar bloques naturalmente (ej. 71, 72, ..., 100)
    sorted_block_keys = sorted(raw_bloques.keys(), key=_natural_block_key)
    bloques_dict = {bl: raw_bloques[bl] for bl in sorted_block_keys}

    if bloque_sel and bloque_sel not in bloques_dict:
        bloque_sel = ''

    # Consultar área neta y bruta (m²), % Vegetativo, % Productivo y variedades por bloque desde dim_camas + mrt_estado_cultivo_actual + crops
    crop_age_map = {
        'HYPERICUM': 12,
        'RUSCUS': 20,
        'SUNFLOWER': 4,
        'LYSIMACHIA': 8,
        'ASTER': 8
    }
    try:
        crop_rows = db.session.execute(text("SELECT UPPER(name) AS cname, veg_max_age FROM crops")).fetchall()
        for cr in crop_rows:
            if cr.cname and cr.veg_max_age is not None:
                crop_age_map[cr.cname.strip()] = int(cr.veg_max_age)
    except Exception:
        db.session.rollback()

    tpsr_blq_stats = {}
    try:
        tpsr_rows = db.session.execute(text("""
            SELECT
                c.blq,
                COUNT(DISTINCT c.num_cama) AS total_camas,
                SUM(COALESCE(c.metros_netos, 30.0)) AS area_neta,
                SUM(COALESCE(c.metros_brutos, 30.0)) AS area_bruta,
                COUNT(DISTINCT CASE
                    WHEN m.id_cama IS NOT NULL AND COALESCE(m.ultimo_evento, '') IN ('SIEMBRA', 'PODA') AND COALESCE(m.producto_actual, '') NOT IN ('', 'VACIO')
                         AND COALESCE(m.edad_semanas_actual, 0) <= COALESCE(cr.veg_max_age, 9)
                    THEN c.num_cama END) AS camas_veg,
                COUNT(DISTINCT CASE
                    WHEN m.id_cama IS NOT NULL AND COALESCE(m.ultimo_evento, '') IN ('SIEMBRA', 'PODA') AND COALESCE(m.producto_actual, '') NOT IN ('', 'VACIO')
                         AND COALESCE(m.edad_semanas_actual, 0) > COALESCE(cr.veg_max_age, 9)
                    THEN c.num_cama END) AS camas_prod,
                SUM(CASE
                    WHEN m.id_cama IS NOT NULL AND COALESCE(m.ultimo_evento, '') IN ('SIEMBRA', 'PODA') AND COALESCE(m.producto_actual, '') NOT IN ('', 'VACIO')
                         AND COALESCE(m.edad_semanas_actual, 0) <= COALESCE(cr.veg_max_age, 9)
                    THEN COALESCE(c.metros_netos, 30.0) ELSE 0 END) AS area_veg,
                SUM(CASE
                    WHEN m.id_cama IS NOT NULL AND COALESCE(m.ultimo_evento, '') IN ('SIEMBRA', 'PODA') AND COALESCE(m.producto_actual, '') NOT IN ('', 'VACIO')
                         AND COALESCE(m.edad_semanas_actual, 0) > COALESCE(cr.veg_max_age, 9)
                    THEN COALESCE(c.metros_netos, 30.0) ELSE 0 END) AS area_prod,
                STRING_AGG(DISTINCT CASE
                    WHEN m.id_cama IS NOT NULL AND COALESCE(m.ultimo_evento, '') IN ('SIEMBRA', 'PODA')
                         AND COALESCE(m.variedad_actual, '') NOT IN ('', 'DISPONIBLE', 'ERRADICADO', 'DESCONOCIDA')
                    THEN m.variedad_actual END, '|') AS variedades_str
            FROM dim_camas c
            LEFT JOIN mrt_estado_cultivo_actual m ON m.id_cama = c.id_cama
            LEFT JOIN crops cr ON UPPER(cr.name) = UPPER(m.producto_actual)
            GROUP BY c.blq;
        """)).fetchall()
        for tr in tpsr_rows:
            c_code = clean_block_code(tr.blq)
            t_cam = int(tr.total_camas or 0)
            c_veg = int(tr.camas_veg or 0)
            c_prod = int(tr.camas_prod or 0)
            pct_veg = round((c_veg / t_cam) * 100.0, 1) if t_cam > 0 else 0.0
            pct_prod = round((c_prod / t_cam) * 100.0, 1) if t_cam > 0 else 0.0
            if pct_veg + pct_prod > 100.0:
                scale = 100.0 / (pct_veg + pct_prod)
                pct_veg = round(pct_veg * scale, 1)
                pct_prod = round(pct_prod * scale, 1)
            vars_list = [v.strip() for v in (tr.variedades_str or '').split('|') if v and v.strip()]
            tpsr_blq_stats[c_code] = {
                'total_camas': t_cam,
                'area_neta': round(float(tr.area_neta or 0.0), 1),
                'area_bruta': round(float(tr.area_bruta or 0.0), 1),
                'camas_veg': c_veg,
                'camas_prod': c_prod,
                'area_veg': round(float(tr.area_veg or 0.0), 1),
                'area_prod': round(float(tr.area_prod or 0.0), 1),
                'pct_veg': pct_veg,
                'pct_prod': pct_prod,
                'variedades': sorted(set(vars_list))
            }
    except Exception:
        db.session.rollback()

    variedades_set = set()

    # Calcular metadatos por bloque y KPIs de la zona activa
    bloques_meta = {}
    zona_kpi = {
        'total_bloques': sum(1 for it in bloques_dict.values() if it and it[0].get('zona') == zona_sel),
        'bloques_completos': 0,
        'bloques_con_pendientes': 0,
        'bloques_sin_calificar': 0,
        'bloques_alto': 0,
        'bloques_medio': 0,
        'bloques_bajo': 0,
        'bloques_con_arrastre': 0,
        'total_labores': 0,
        'calificadas': 0,
        'cumplidas': 0,
        'incumplidas': 0,
        'pendientes': 0,
        'arrastre_total': 0,
        'pct_avance': 0.0,
        'pct_cumplimiento': None
    }

    for bl, items in bloques_dict.items():
        eval_items = [it for it in items if it.get('tiene_evaluacion')]
        total_lab = len(eval_items)
        cumpl = sum(1 for it in eval_items if it.get('cumplido') == 1)
        incumpl = sum(1 for it in eval_items if it.get('cumplido') == 0)
        calif = cumpl + incumpl
        pend = total_lab - calif
        arrastre_cnt = sum(1 for it in items if it.get('es_pendiente_anterior'))
        sin_match_cnt = len(items) - total_lab

        prods_bl = sorted({it['producto_maestro'] for it in items if it.get('producto_maestro')})
        acts_bl = sorted({it['actividad_tipo'] for it in items if it.get('actividad_tipo')})
        sups_bl = sorted({it['supervisor'] for it in items if it.get('supervisor')})
        auxs_bl = sorted({it['auxiliar'] for it in items if it.get('auxiliar')})

        pct_av = round((calif / total_lab) * 100, 1) if total_lab > 0 else 0.0
        pct_cu = round((cumpl / total_lab) * 100, 1) if (total_lab > 0 and calif > 0) else None

        tiene_notas = any(bool(it.get('observacion') and str(it['observacion']).strip()) for it in items)
        ha_sido_editado = bool(calif > 0 or tiene_notas)

        if total_lab == 0:
            estado_bl = 'SIN_LABOR'
        elif calif == 0 and not tiene_notas:
            estado_bl = 'PENDIENTE'
        elif calif == 0 and tiene_notas:
            # Si tiene notas justificando estado o incumplimiento, computa como 0% evaluado
            pct_cu = 0.0
            estado_bl = 'BAJO'
        elif pct_cu is not None and pct_cu >= 85:
            estado_bl = 'ALTO'
        elif pct_cu is not None and pct_cu >= 70:
            estado_bl = 'MEDIO'
        else:
            estado_bl = 'BAJO'

        eval_completa = bool(total_lab > 0 and pend == 0)

        c_code = clean_block_code(bl)
        st_info = tpsr_blq_stats.get(c_code)

        # Fallback: calcular desde los números físicos de camas en calificaciones si no está en mrt_estado_cultivo_actual
        all_calif_beds = set()
        veg_calif_beds = set()
        prod_calif_beds = set()
        for it in items:
            c_ini = int(it.get('cama_inicial') or 1)
            c_fin = int(it.get('cama_final') or c_ini)
            if c_ini > c_fin:
                c_ini, c_fin = c_fin, c_ini
            p_name = str(it.get('producto_maestro') or '').upper().strip()
            veg_max = crop_age_map.get(p_name, 9)
            edad_r = int(it.get('edad_real') or 0)
            is_veg = (edad_r <= veg_max)
            for b_num in range(c_ini, c_fin + 1):
                all_calif_beds.add(b_num)
                if is_veg:
                    veg_calif_beds.add(b_num)
                else:
                    prod_calif_beds.add(b_num)

        fb_total_beds = len(all_calif_beds) if all_calif_beds else 1
        fb_veg_beds = len(veg_calif_beds)
        fb_prod_beds = len(prod_calif_beds)

        if st_info and (st_info['camas_veg'] + st_info['camas_prod']) > 0:
            area_neta_bl = st_info['area_neta']
            area_bruta_bl = st_info.get('area_bruta', area_neta_bl)
            camas_fisicas_bl = st_info['total_camas']
            camas_veg_bl = st_info['camas_veg']
            camas_prod_bl = st_info['camas_prod']
            pct_veg_bl = st_info['pct_veg']
            pct_prod_bl = st_info['pct_prod']
            area_veg_bl = st_info['area_veg']
            area_prod_bl = st_info['area_prod']
            vars_bl = st_info['variedades'] or prods_bl
        else:
            camas_fisicas_bl = st_info['total_camas'] if (st_info and st_info['total_camas'] > 0) else fb_total_beds
            area_neta_bl = st_info['area_neta'] if (st_info and st_info['area_neta'] > 0) else round(fb_total_beds * 30.0, 1)
            area_bruta_bl = st_info['area_bruta'] if (st_info and st_info.get('area_bruta', 0) > 0) else area_neta_bl
            camas_veg_bl = fb_veg_beds
            camas_prod_bl = fb_prod_beds
            denom = max(1, fb_total_beds)
            pct_veg_bl = round((fb_veg_beds / denom) * 100.0, 1) if fb_total_beds > 0 else 0.0
            pct_prod_bl = round((fb_prod_beds / denom) * 100.0, 1) if fb_total_beds > 0 else 0.0
            area_veg_bl = round(area_neta_bl * (pct_veg_bl / 100.0), 1)
            area_prod_bl = round(area_neta_bl * (pct_prod_bl / 100.0), 1)
            vars_bl = (st_info['variedades'] if st_info else []) or prods_bl

        for v_item in vars_bl:
            if v_item:
                variedades_set.add(v_item)

        bl_zona = items[0].get('zona', '')
        bloques_meta[bl] = {
            'bloque': bl,
            'clean_code': c_code,
            'zona': bl_zona,
            'total_filas': len(items),
            'total_labores': total_lab,
            'calificadas': calif,
            'cumplidas': cumpl,
            'incumplidas': incumpl,
            'pendientes': pend,
            'arrastre_count': arrastre_cnt,
            'sin_match_count': sin_match_cnt,
            'productos': prods_bl,
            'variedades': vars_bl,
            'actividades': acts_bl,
            'supervisores': sups_bl,
            'auxiliares': auxs_bl,
            'pct_avance': pct_av,
            'pct_cumplimiento': pct_cu,
            'estado_bloque': estado_bl,
            'ha_sido_editado': ha_sido_editado,
            'tiene_notas': tiene_notas,
            'evaluacion_completa': eval_completa,
            'camas_fisicas': camas_fisicas_bl,
            'area_neta': area_neta_bl,
            'area_bruta': area_bruta_bl,
            'camas_veg': camas_veg_bl,
            'camas_prod': camas_prod_bl,
            'pct_veg': pct_veg_bl,
            'pct_prod': pct_prod_bl,
            'area_veg': area_veg_bl,
            'area_prod': area_prod_bl
        }

        if bl_zona == zona_sel:
            zona_kpi['total_labores'] += total_lab
            zona_kpi['calificadas'] += calif
            zona_kpi['cumplidas'] += cumpl
            zona_kpi['incumplidas'] += incumpl
            zona_kpi['pendientes'] += pend
            zona_kpi['arrastre_total'] += arrastre_cnt

            if eval_completa:
                zona_kpi['bloques_completos'] += 1
            if pend > 0:
                zona_kpi['bloques_con_pendientes'] += 1
            if estado_bl == 'PENDIENTE':
                zona_kpi['bloques_sin_calificar'] += 1
            elif estado_bl == 'ALTO':
                zona_kpi['bloques_alto'] += 1
            elif estado_bl == 'MEDIO':
                zona_kpi['bloques_medio'] += 1
            elif estado_bl == 'BAJO':
                zona_kpi['bloques_bajo'] += 1
            if arrastre_cnt > 0:
                zona_kpi['bloques_con_arrastre'] += 1

    if zona_kpi['total_labores'] > 0:
        zona_kpi['pct_avance'] = round((zona_kpi['calificadas'] / zona_kpi['total_labores']) * 100, 1)
        if zona_kpi['calificadas'] > 0:
            zona_kpi['pct_cumplimiento'] = round((zona_kpi['cumplidas'] / zona_kpi['total_labores']) * 100, 1)

    sem_info = db.session.execute(text("SELECT estado FROM semanas_evaluacion WHERE semana_anio = :semana;"), {'semana': semana}).fetchone()
    semana_estado = sem_info.estado if sem_info else 'ABIERTA'

    cfg = load_mapeos_config()
    max_semanas_arrastre = int(cfg.get("max_semanas_arrastre", MAX_SEMANAS_ARRASTRE_DEFAULT))

    # Consultar semanas de ingesta disponibles (Excel Integral + TPSR Campo + Calendario)
    semanas_excel_nums = []
    try:
        w_rows = db.session.execute(text("SELECT DISTINCT week FROM crop_state_records WHERE week IS NOT NULL;")).fetchall()
        seen_wks = set()
        for wr in w_rows:
            s_w = str(wr.week or '').strip()
            nums = re.findall(r'\d+', s_w)
            if len(nums) >= 2:
                wk_n = int(nums[1])
            elif len(nums) == 1:
                v_n = int(nums[0])
                wk_n = (v_n % 100) if v_n > 2000 else v_n
            else:
                wk_n = 0
            if 1 <= wk_n <= 53 and wk_n not in seen_wks:
                seen_wks.add(wk_n)
                semanas_excel_nums.append(wk_n)
        semanas_excel_nums.sort(reverse=True)
    except Exception:
        db.session.rollback()

    try:
        max_ev_sc = db.session.execute(text("SELECT MAX(semana_campo) FROM fact_eventos_cultivo;")).scalar() or 2639
        sem_tpsr_num = int(max_ev_sc) % 100
        curr_cal_sc = db.session.execute(text("SELECT semana_campo FROM dim_calendario_agricola WHERE fecha = CURRENT_DATE LIMIT 1;")).scalar() or 2642
        sem_cal_hoy = int(curr_cal_sc) % 100
    except Exception:
        db.session.rollback()
        sem_tpsr_num = 39
        sem_cal_hoy = 42

    ultima_sem_ingesta = semanas_excel_nums[0] if semanas_excel_nums else sem_tpsr_num
    semana_proxima_sugerida = max(sem_cal_hoy + 1, ultima_sem_ingesta + 1)
    if semana_proxima_sugerida > 53:
        semana_proxima_sugerida = 53

    ingesta_info = {
        'ultima_sem_excel': semanas_excel_nums[0] if semanas_excel_nums else None,
        'semanas_excel': semanas_excel_nums,
        'ultima_sem_tpsr': sem_tpsr_num,
        'semana_calendario_hoy': sem_cal_hoy,
        'ultima_sem_ingesta': ultima_sem_ingesta,
        'semana_proxima_sugerida': semana_proxima_sugerida
    }

    # Catálogo de labores disponibles agrupadas por producto y auxiliares asignados
    catalogo_labores_rows = db.session.execute(text("""
        SELECT DISTINCT producto, labor, auxiliar, supervisor
        FROM matriz_fenogramas
        WHERE labor IS NOT NULL AND labor != ''
        ORDER BY producto, labor;
    """)).fetchall()

    catalogo_labores_map = defaultdict(list)
    labores_unicas = set()
    auxiliares_map = {}
    
    for cl in catalogo_labores_rows:
        p_name = (cl.producto or '').strip()
        l_name = (cl.labor or '').strip()
        aux = (cl.auxiliar or '').strip()
        sup = (cl.supervisor or '').strip()
        
        catalogo_labores_map[p_name].append({
            'labor': l_name,
            'auxiliar': aux,
            'supervisor': sup
        })
        labores_unicas.add(l_name)
        auxiliares_map[f"{p_name.upper()}|||{l_name.upper()}"] = {'auxiliar': aux, 'supervisor': sup}
        if l_name.upper() not in auxiliares_map:
            auxiliares_map[l_name.upper()] = {'auxiliar': aux, 'supervisor': sup}

    return render_template(
        'fenogramas/operarios.html',
        semana=semana,
        semanas=semanas,
        zonas=zonas,
        zonas_resumen=zonas_resumen,
        zona_sel=zona_sel,
        bloque_sel=bloque_sel,
        bloques=bloques_dict,
        bloques_meta=bloques_meta,
        zona_kpi=zona_kpi,
        productos_zona=sorted(productos_set),
        variedades_zona=sorted(variedades_set),
        supervisores_zona=sorted(responsables_set),
        semana_estado=semana_estado,
        max_semanas_arrastre=max_semanas_arrastre,
        ingesta_info=ingesta_info,
        catalogo_labores=dict(catalogo_labores_map),
        todas_labores=sorted(list(labores_unicas)),
        auxiliares_map=auxiliares_map
    )


# ==============================================================================
# 2. CALIFICACIÓN: INDIVIDUAL, POR LOTE Y DESCARTE DE ARRASTRE OBSOLETO
# ==============================================================================
@fenogramas_bp.route('/api/calificar', methods=['POST'])
@login_required
@permission_required('fenogramas')
def api_calificar():
    data = request.get_json() or {}
    item_id = data.get('id')
    if not item_id:
        return jsonify({'success': False, 'error': 'ID de calificación requerido'}), 400

    cumplido = data.get('cumplido')
    observacion = data.get('observacion', '')
    nueva_labor = (data.get('labor') or '').strip()
    nuevo_aux = (data.get('auxiliar') or '').strip()
    nuevo_sup = (data.get('supervisor') or '').strip()

    if cumplido is not None:
        try:
            cumplido = int(cumplido)
            if cumplido not in [0, 1]:
                cumplido = None
        except ValueError:
            cumplido = None

    try:
        calif = Calificacion.query.get(item_id)
        if not calif:
            return jsonify({'success': False, 'error': 'Registro no encontrado'}), 404

        if nueva_labor and nueva_labor != calif.labor:
            existing = Calificacion.query.filter_by(
                semana_anio=calif.semana_anio,
                anio=calif.anio,
                zona=calif.zona,
                bloque=calif.bloque,
                producto_maestro=calif.producto_maestro,
                cama_inicial=calif.cama_inicial,
                cama_final=calif.cama_final,
                sufijo=calif.sufijo,
                labor=nueva_labor
            ).first()

            if existing and existing.id != calif.id:
                existing.cumplido = cumplido
                existing.observacion = observacion
                if nuevo_aux:
                    existing.auxiliar = nuevo_aux
                if nuevo_sup:
                    existing.supervisor = nuevo_sup
                existing.tiene_evaluacion = True
                existing.fecha_calificacion = datetime.utcnow()
                db.session.commit()
                return jsonify({
                    'success': True,
                    'id': existing.id,
                    'cumplido': cumplido,
                    'observacion': observacion,
                    'labor': existing.labor,
                    'auxiliar': existing.auxiliar,
                    'supervisor': existing.supervisor
                })
            else:
                calif.labor = nueva_labor
                if nuevo_aux:
                    calif.auxiliar = nuevo_aux
                if nuevo_sup:
                    calif.supervisor = nuevo_sup

        calif.cumplido = cumplido
        calif.observacion = observacion
        calif.tiene_evaluacion = True
        calif.fecha_calificacion = datetime.utcnow()
        db.session.commit()
        return jsonify({
            'success': True,
            'id': item_id,
            'cumplido': cumplido,
            'observacion': observacion,
            'labor': calif.labor,
            'auxiliar': calif.auxiliar,
            'supervisor': calif.supervisor
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@fenogramas_bp.route('/api/calificar-lote', methods=['POST'])
@login_required
@permission_required('fenogramas')
def api_calificar_lote():
    data = request.get_json() or {}
    items = data.get('items', [])
    if not items:
        return jsonify({'success': True, 'updated': 0, 'message': 'No hay cambios para guardar'})

    updated_count = 0
    try:
        for item in items:
            item_id = item.get('id')
            cumplido = item.get('cumplido')
            obs = item.get('observacion', '')
            nueva_labor = (item.get('labor') or '').strip()
            nuevo_aux = (item.get('auxiliar') or '').strip()
            nuevo_sup = (item.get('supervisor') or '').strip()

            if cumplido is not None:
                try:
                    cumplido = int(cumplido)
                    if cumplido not in [0, 1]:
                        cumplido = None
                except ValueError:
                    cumplido = None

            # Soporte offline para labores adicionales agregadas con el botón '+'
            if item.get('is_extra') or str(item_id).startswith('client_extra_') or str(item_id).startswith('temp_'):
                parent_id = item.get('parent_id')
                parent_calif = Calificacion.query.get(parent_id) if parent_id else None
                if not parent_calif:
                    continue
                labor_extra = (item.get('nueva_labor') or item.get('labor') or '').strip()
                if not labor_extra:
                    continue
                existing_extra = Calificacion.query.filter_by(
                    semana_anio=parent_calif.semana_anio,
                    anio=parent_calif.anio,
                    zona=parent_calif.zona,
                    bloque=parent_calif.bloque,
                    producto_maestro=parent_calif.producto_maestro,
                    cama_inicial=parent_calif.cama_inicial,
                    cama_final=parent_calif.cama_final,
                    sufijo=parent_calif.sufijo,
                    labor=labor_extra
                ).first()
                if existing_extra:
                    existing_extra.cumplido = cumplido
                    existing_extra.observacion = obs
                    if nuevo_aux: existing_extra.auxiliar = nuevo_aux
                    if nuevo_sup: existing_extra.supervisor = nuevo_sup
                    existing_extra.tiene_evaluacion = True
                    existing_extra.fecha_calificacion = datetime.utcnow()
                else:
                    new_calif = Calificacion(
                        semana_anio=parent_calif.semana_anio,
                        anio=parent_calif.anio,
                        zona=parent_calif.zona,
                        bloque=parent_calif.bloque,
                        actividad_tipo=parent_calif.actividad_tipo,
                        producto_maestro=parent_calif.producto_maestro,
                        cama_inicial=parent_calif.cama_inicial,
                        cama_final=parent_calif.cama_final,
                        sufijo=parent_calif.sufijo,
                        edad_real=parent_calif.edad_real,
                        labor=labor_extra,
                        auxiliar=nuevo_aux or parent_calif.auxiliar,
                        supervisor=nuevo_sup or parent_calif.supervisor,
                        cumplido=cumplido,
                        observacion=obs,
                        tiene_evaluacion=True,
                        es_pendiente_anterior=False,
                        semana_origen=parent_calif.semana_anio,
                        fecha_calificacion=datetime.utcnow()
                    )
                    db.session.add(new_calif)
                updated_count += 1
                continue

            if item_id:
                calif = Calificacion.query.get(item_id)
                if not calif:
                    continue

                if nueva_labor and nueva_labor != calif.labor:
                    existing = Calificacion.query.filter_by(
                        semana_anio=calif.semana_anio,
                        anio=calif.anio,
                        zona=calif.zona,
                        bloque=calif.bloque,
                        producto_maestro=calif.producto_maestro,
                        cama_inicial=calif.cama_inicial,
                        cama_final=calif.cama_final,
                        sufijo=calif.sufijo,
                        labor=nueva_labor
                    ).first()

                    if existing and existing.id != calif.id:
                        existing.cumplido = cumplido
                        existing.observacion = obs
                        if nuevo_aux:
                            existing.auxiliar = nuevo_aux
                        if nuevo_sup:
                            existing.supervisor = nuevo_sup
                        existing.tiene_evaluacion = True
                        existing.fecha_calificacion = datetime.utcnow()
                    else:
                        calif.labor = nueva_labor
                        if nuevo_aux:
                            calif.auxiliar = nuevo_aux
                        if nuevo_sup:
                            calif.supervisor = nuevo_sup
                        calif.cumplido = cumplido
                        calif.observacion = obs
                        calif.tiene_evaluacion = True
                        calif.fecha_calificacion = datetime.utcnow()
                else:
                    calif.cumplido = cumplido
                    calif.observacion = obs
                    calif.tiene_evaluacion = True
                    calif.fecha_calificacion = datetime.utcnow()

                updated_count += 1

        db.session.commit()
        return jsonify({'success': True, 'updated': updated_count})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@fenogramas_bp.route('/api/duplicar-calificacion', methods=['POST'])
@login_required
@permission_required('fenogramas')
def api_duplicar_calificacion():
    """Crea una fila de calificación adicional para evaluar otra labor en el mismo rango de camas de un bloque."""
    data = request.get_json() or {}
    item_id = data.get('id')
    nueva_labor = (data.get('nueva_labor') or '').strip()

    if not item_id or not nueva_labor:
        return jsonify({'success': False, 'error': 'ID y nueva labor requeridos'}), 400

    try:
        calif_base = Calificacion.query.get(item_id)
        if not calif_base:
            return jsonify({'success': False, 'error': 'Calificación base no encontrada'}), 404

        # Buscar responsable en matriz_fenogramas
        responsable = db.session.execute(text("""
            SELECT auxiliar, supervisor
            FROM matriz_fenogramas
            WHERE UPPER(producto) = UPPER(:prod) AND UPPER(labor) = UPPER(:labor)
            LIMIT 1;
        """), {'prod': calif_base.producto_maestro or '', 'labor': nueva_labor}).fetchone()

        aux = (responsable.auxiliar if responsable and responsable.auxiliar else calif_base.auxiliar) or ''
        sup = (responsable.supervisor if responsable and responsable.supervisor else calif_base.supervisor) or ''

        existente = Calificacion.query.filter_by(
            semana_anio=calif_base.semana_anio,
            anio=calif_base.anio,
            zona=calif_base.zona,
            bloque=calif_base.bloque,
            producto_maestro=calif_base.producto_maestro,
            cama_inicial=calif_base.cama_inicial,
            cama_final=calif_base.cama_final,
            sufijo=calif_base.sufijo,
            labor=nueva_labor
        ).first()

        if existente:
            return jsonify({
                'success': True,
                'item': existente.to_dict(),
                'already_existed': True,
                'message': 'Esta labor ya estaba registrada para este rango de camas'
            })

        # Si la fila base era "No hay actividad" (sin match previo), actualizar esta misma fila en lugar de dejar un renglón vacío
        if calif_base.labor == 'No hay actividad' and not calif_base.tiene_evaluacion:
            calif_base.labor = nueva_labor
            calif_base.auxiliar = aux
            calif_base.supervisor = sup
            calif_base.tiene_evaluacion = True
            calif_base.cumplido = None
            db.session.commit()
            return jsonify({
                'success': True,
                'item': calif_base.to_dict(),
                'already_existed': False,
                'updated_in_place': True,
                'message': f'Labor "{nueva_labor}" asignada para evaluación'
            })

        nueva_calif = Calificacion(
            semana_anio=calif_base.semana_anio,
            anio=calif_base.anio,
            zona=calif_base.zona,
            bloque=calif_base.bloque,
            actividad_tipo=calif_base.actividad_tipo,
            producto_maestro=calif_base.producto_maestro,
            cama_inicial=calif_base.cama_inicial,
            cama_final=calif_base.cama_final,
            sufijo=calif_base.sufijo,
            edad_real=calif_base.edad_real,
            labor=nueva_labor,
            auxiliar=aux,
            supervisor=sup,
            cumplido=None,
            observacion='',
            tiene_evaluacion=True,
            es_pendiente_anterior=False,
            semana_origen=calif_base.semana_anio
        )
        db.session.add(nueva_calif)
        db.session.commit()

        return jsonify({
            'success': True,
            'item': nueva_calif.to_dict(),
            'already_existed': False,
            'message': f'Labor "{nueva_labor}" agregada para evaluación'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@fenogramas_bp.route('/api/descartar-arrastre', methods=['POST'])
@login_required
@permission_required('fenogramas')
def api_descartar_arrastre():
    """Permite dar de baja manualmente una tarea arrastrada de semanas anteriores si ya no aplica en campo."""
    data = request.get_json() or {}
    item_id = data.get('id')
    if not item_id:
        return jsonify({'success': False, 'error': 'ID requerido'}), 400
    try:
        row = db.session.execute(text("""
            SELECT id, es_pendiente_anterior FROM calificaciones WHERE id = :id;
        """), {'id': item_id}).fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Registro no encontrado'}), 404
        if not row.es_pendiente_anterior:
            return jsonify({'success': False, 'error': 'Solo se pueden descartar tareas arrastradas de semanas anteriores'}), 400

        db.session.execute(text("DELETE FROM calificaciones WHERE id = :id;"), {'id': item_id})
        db.session.commit()
        return jsonify({'success': True, 'deleted_id': item_id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==============================================================================
# 3. DASHBOARD EJECUTIVO DE CUMPLIMIENTO
# ==============================================================================
@fenogramas_bp.route('/dashboard')
@login_required
@permission_required('fenogramas')
def dashboard():
    semanas_query = db.session.execute(text("SELECT semana_anio, estado FROM semanas_evaluacion ORDER BY semana_anio ASC;")).fetchall()
    semanas = [dict(s._mapping) for s in semanas_query]
    semanas_list = [s['semana_anio'] for s in semanas]

    semana = request.args.get('semana', type=int)
    if not semana or semana not in semanas_list:
        semana = semanas_list[-1] if semanas_list else 30

    dash_data = obtener_datos_dashboard(semana)

    return render_template(
        'fenogramas/dashboard.html',
        semana=semana,
        semanas=dash_data['semanas'],
        semanas_todas=dash_data['semanas_todas'],
        semana_estado=dash_data['semana_estado'],
        fecha_cierre=dash_data['fecha_cierre'],
        kpi_global=dash_data['kpi_global'],
        pct_cumplimiento=dash_data['pct_cumplimiento'],
        por_zona=dash_data['por_zona'],
        por_producto=dash_data['por_producto'],
        por_personal=dash_data['por_personal'],
        por_supervisor=dash_data['por_supervisor'],
        tendencia_finca=dash_data['tendencia_finca'],
        tendencia_multisemana=dash_data['tendencia_multisemana'],
        cultivos_columnas=dash_data['cultivos_columnas'],
        matriz_auxiliares=dash_data['matriz_auxiliares'],
        sin_match_count=dash_data['sin_match_count'],
        config_mapeos=dash_data['config_mapeos'],
        zonas_por_cultivo=dash_data.get('zonas_por_cultivo', []),
        tendencia_por_cultivo=dash_data.get('tendencia_por_cultivo', {})
    )


# ==============================================================================
# 4. GESTOR Y EDITOR DE MATRIZ DE FENOGRAMAS
# ==============================================================================
@fenogramas_bp.route('/matriz')
@login_required
@permission_required('fenogramas')
def matriz():
    rules_rows = db.session.execute(text("""
        SELECT id, sheet_name, producto, actividad_tipo, semana, labor, auxiliar, supervisor
        FROM matriz_fenogramas
        ORDER BY producto ASC, actividad_tipo ASC, semana ASC, labor ASC;
    """)).fetchall()
    rules = [dict(r._mapping) for r in rules_rows]

    productos_raw = db.session.execute(text("SELECT DISTINCT producto FROM matriz_fenogramas ORDER BY producto ASC;")).fetchall()
    productos_list = [r.producto for r in productos_raw if r.producto]

    supervisores_raw = db.session.execute(text("SELECT DISTINCT supervisor FROM matriz_fenogramas WHERE supervisor != '' AND supervisor IS NOT NULL ORDER BY supervisor ASC;")).fetchall()
    supervisores_list = [r.supervisor for r in supervisores_raw]

    auxiliares_raw = db.session.execute(text("SELECT DISTINCT auxiliar FROM matriz_fenogramas WHERE auxiliar != '' AND auxiliar IS NOT NULL ORDER BY auxiliar ASC;")).fetchall()
    auxiliares_list = [r.auxiliar for r in auxiliares_raw]

    productos_counts = {}
    for r in rules:
        p = r.get('producto') or 'Otro'
        productos_counts[p] = productos_counts.get(p, 0) + 1

    semana_activa = session.get('fenogramas_semana_activa', 43)

    return render_template(
        'fenogramas/matriz.html',
        rules=rules,
        productos_list=productos_list,
        productos_counts=productos_counts,
        supervisores_list=supervisores_list,
        auxiliares_list=auxiliares_list,
        semana_activa=semana_activa
    )


@fenogramas_bp.route('/api/guardar-matriz', methods=['POST'])
@login_required
@permission_required('fenogramas')
def api_guardar_matriz():
    data = request.get_json() or {}
    items = data.get('items', [])
    semana = data.get('semana') or session.get('fenogramas_semana_activa', 43)

    if not items:
        return jsonify({'success': False, 'error': 'No hay items para guardar'}), 400

    try:
        db.session.execute(text("TRUNCATE TABLE matriz_fenogramas RESTART IDENTITY CASCADE;"))
        for it in items:
            db.session.add(MatrizFenograma(
                sheet_name=f"{it['producto']} {it['actividad_tipo']}",
                producto=it['producto'],
                actividad_tipo=it['actividad_tipo'],
                semana=int(it['semana']),
                labor=it['labor'],
                auxiliar=it['auxiliar'],
                supervisor=it['supervisor']
            ))
        db.session.commit()

        # Recalcular la semana con el estado de cultivo actual en base de datos
        try:
            count = sync_from_mrt_estado_cultivo(semana_corte=semana, anio=2026, fuente='AUTO')
        except Exception as e_calc:
            print(f"[AVISO] Error al recalcular semana {semana} tras guardar matriz: {e_calc}")
            count = len(items)
        return jsonify({'success': True, 'recalculated': count, 'message': f'Reglas guardadas y semana {semana} actualizada ({count} actividades)'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==============================================================================
# 5. ACTIVIDADES INCUMPLIDAS (0s)
# ==============================================================================
@fenogramas_bp.route('/incumplidas')
@login_required
@permission_required('fenogramas')
def incumplidas():
    semanas_query = db.session.execute(text("SELECT semana_anio, estado FROM semanas_evaluacion ORDER BY semana_anio ASC;")).fetchall()
    semanas = [dict(s._mapping) for s in semanas_query]
    semanas_list = [s['semana_anio'] for s in semanas]

    semana = request.args.get('semana', type=int)
    if not semana or semana not in semanas_list:
        semana = semanas_list[-1] if semanas_list else 30

    aux_sel = request.args.get('auxiliar', '').strip()

    auxiliares_raw = db.session.execute(text("""
        SELECT DISTINCT COALESCE(NULLIF(auxiliar, ''), 'Sin Auxiliar') as aux
        FROM calificaciones
        WHERE semana_anio = :semana AND COALESCE(cumplido, 0) = 0 AND tiene_evaluacion = TRUE
        ORDER BY aux ASC;
    """), {'semana': semana}).fetchall()
    auxiliares_list = [r.aux for r in auxiliares_raw]

    query = """
        SELECT id, semana_anio, anio, zona, bloque, actividad_tipo, producto_maestro,
               cama_inicial, cama_final, sufijo, edad_real, labor, auxiliar, supervisor,
               cumplido, observacion, es_pendiente_anterior, semana_origen
        FROM calificaciones
        WHERE semana_anio = :semana AND COALESCE(cumplido, 0) = 0 AND tiene_evaluacion = TRUE
    """
    params = {'semana': semana}
    if aux_sel:
        query += " AND COALESCE(NULLIF(auxiliar, ''), 'Sin Auxiliar') = :aux_sel"
        params['aux_sel'] = aux_sel

    query += " ORDER BY zona ASC, bloque ASC, cama_inicial ASC, labor ASC;"
    incump_rows = db.session.execute(text(query), params).fetchall()
    incumplidas_list = [dict(r._mapping) for r in incump_rows]

    total_incumplidas_count = len(incumplidas_list)
    aux_afectados_count = len(set([r['auxiliar'] for r in incumplidas_list if r['auxiliar']]))

    top_labor_row = db.session.execute(text("""
        SELECT labor, COUNT(*) as cnt
        FROM calificaciones
        WHERE semana_anio = :semana AND COALESCE(cumplido, 0) = 0 AND tiene_evaluacion = TRUE
        GROUP BY labor
        ORDER BY cnt DESC
        LIMIT 1;
    """), {'semana': semana}).fetchone()
    top_labor_fallo = top_labor_row.labor if top_labor_row else 'Ninguna'

    return render_template(
        'fenogramas/incumplidas.html',
        semana=semana,
        semanas=semanas,
        aux_sel=aux_sel,
        auxiliares_list=auxiliares_list,
        incumplidas_list=incumplidas_list,
        total_incumplidas_count=total_incumplidas_count,
        aux_afectados_count=aux_afectados_count,
        top_labor_fallo=top_labor_fallo
    )


# ==============================================================================
# 6. AUDITORÍA DE CAMAS SIN MATCH
# ==============================================================================
@fenogramas_bp.route('/sin-match')
@login_required
@permission_required('fenogramas')
def sin_match():
    semanas_query = db.session.execute(text("SELECT semana_anio, estado FROM semanas_evaluacion ORDER BY semana_anio ASC;")).fetchall()
    semanas = [dict(s._mapping) for s in semanas_query]
    semanas_list = [s['semana_anio'] for s in semanas]

    semana = request.args.get('semana', type=int)
    if not semana or semana not in semanas_list:
        semana = semanas_list[-1] if semanas_list else 30

    rows_sm = db.session.execute(text("""
        SELECT 
            producto_maestro,
            actividad_tipo,
            edad_real,
            COUNT(*) as total_segmentos
        FROM calificaciones
        WHERE semana_anio = :semana AND tiene_evaluacion = FALSE
        GROUP BY producto_maestro, actividad_tipo, edad_real
        ORDER BY producto_maestro ASC, actividad_tipo ASC, edad_real ASC;
    """), {'semana': semana}).fetchall()

    sin_match_por_producto = {}
    for r in rows_sm:
        p = r.producto_maestro
        if p not in sin_match_por_producto:
            sin_match_por_producto[p] = {
                'producto': p,
                'total_segmentos': 0,
                'detalles': []
            }
        sin_match_por_producto[p]['total_segmentos'] += r.total_segmentos
        sin_match_por_producto[p]['detalles'].append({
            'actividad_tipo': r.actividad_tipo,
            'edad_real': r.edad_real,
            'segmentos': r.total_segmentos
        })

    sin_match_camas = db.session.execute(text("""
        SELECT zona, bloque, actividad_tipo, producto_maestro, cama_inicial, cama_final, sufijo, edad_real
        FROM calificaciones
        WHERE semana_anio = :semana AND tiene_evaluacion = FALSE
        ORDER BY zona ASC, bloque ASC, cama_inicial ASC;
    """), {'semana': semana}).fetchall()

    return render_template(
        'fenogramas/sin_match.html',
        semana=semana,
        semanas=semanas,
        sin_match_por_producto=sin_match_por_producto,
        sin_match=[dict(r._mapping) for r in sin_match_camas]
    )


# ==============================================================================
# 7. EXPORTACIÓN DE REPORTES (CSV)
# ==============================================================================
@fenogramas_bp.route('/exportar-incumplidas')
@login_required
@permission_required('fenogramas')
def exportar_incumplidas():
    semana = request.args.get('semana', 30, type=int)
    aux_sel = request.args.get('auxiliar', '').strip()

    query = """
        SELECT zona, bloque, actividad_tipo, producto_maestro, cama_inicial, cama_final, sufijo, edad_real, labor, auxiliar, supervisor, observacion
        FROM calificaciones
        WHERE semana_anio = :semana AND COALESCE(cumplido, 0) = 0 AND tiene_evaluacion = TRUE
    """
    params = {'semana': semana}
    if aux_sel:
        query += " AND COALESCE(NULLIF(auxiliar, ''), 'Sin Auxiliar') = :aux_sel"
        params['aux_sel'] = aux_sel

    query += " ORDER BY zona ASC, bloque ASC, cama_inicial ASC;"
    rows = db.session.execute(text(query), params).fetchall()

    csv_data = "ZONA;BLOQUE;ACTIVIDAD;PRODUCTO_MAESTRO;CAMA_INICIAL;CAMA_FINAL;SUFIJO;EDAD_REAL;LABOR;AUXILIAR;SUPERVISOR;OBSERVACIONES\n"
    for r in rows:
        obs = str(r.observacion or '').replace(';', ',').replace('\n', ' ')
        csv_data += f"{r.zona};{r.bloque};{r.actividad_tipo};{r.producto_maestro};{r.cama_inicial};{r.cama_final};{r.sufijo};{r.edad_real};{r.labor};{r.auxiliar};{r.supervisor};{obs}\n"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=actividades_incumplidas_semana_{semana}.csv"}
    )


@fenogramas_bp.route('/exportar-sin-match')
@login_required
@permission_required('fenogramas')
def exportar_sin_match():
    semana = request.args.get('semana', 30, type=int)
    rows = db.session.execute(text("""
        SELECT zona, bloque, actividad_tipo, producto_maestro, cama_inicial, cama_final, sufijo, edad_real
        FROM calificaciones
        WHERE semana_anio = :semana AND tiene_evaluacion = FALSE
        ORDER BY zona ASC, bloque ASC, cama_inicial ASC;
    """), {'semana': semana}).fetchall()

    csv_data = "ZONA;BLOQUE;ACTIVIDAD;PRODUCTO_MAESTRO;CAMA_INICIAL;CAMA_FINAL;SUFIJO;EDAD_REAL\n"
    for r in rows:
        csv_data += f"{r.zona};{r.bloque};{r.actividad_tipo};{r.producto_maestro};{r.cama_inicial};{r.cama_final};{r.sufijo};{r.edad_real}\n"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=camas_sin_match_semana_{semana}.csv"}
    )


# ==============================================================================
# 8. GENERACIÓN DIRECTA DESDE ESTADO DE CULTIVO (FACT / MRT LOCAL)
# ==============================================================================
@fenogramas_bp.route('/api/generar-desde-fact', methods=['POST'])
@login_required
@permission_required('fenogramas')
def api_generar_desde_fact():
    try:
        data = request.get_json() or {}
        semana_corte = int(data.get('semana_corte', 42))
        anio = int(data.get('anio', 2026))
        fuente = data.get('fuente', 'AUTO')
        semana_base = data.get('semana_base')
        forzar_limpieza = bool(data.get('forzar_limpieza', False))

        count = sync_from_mrt_estado_cultivo(
            semana_corte=semana_corte,
            anio=anio,
            fuente=fuente,
            semana_base=semana_base,
            forzar_limpieza=forzar_limpieza
        )
        return jsonify({
            'success': True,
            'semana_corte': semana_corte,
            'count': count,
            'message': f'Fenograma Semana {semana_corte} generado con éxito ({count} actividades proyectadas).'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==============================================================================
# 9. SUBIDA Y PREVIEW DE EXCEL (PLANO CULTIVO & MATRIZ FENOGRAMAS)
# ==============================================================================
@fenogramas_bp.route('/api/preview-plano', methods=['POST'])
@login_required
@permission_required('fenogramas')
def api_preview_plano():
    try:
        ec_file = request.files.get('file_estado_cultivo')
        mf_file = request.files.get('file_matriz_fenogramas')

        if not ec_file or not ec_file.filename:
            return jsonify({'success': False, 'error': 'Debes adjuntar el archivo Estado Cultivo Excel'}), 400

        upload_dir = os.path.join(os.getcwd(), 'uploads')
        os.makedirs(upload_dir, exist_ok=True)

        ec_filename = secure_filename(ec_file.filename)
        ec_path = os.path.join(upload_dir, ec_filename)
        ec_file.save(ec_path)

        mf_path = None
        if mf_file and mf_file.filename:
            mf_filename = secure_filename(mf_file.filename)
            mf_path = os.path.join(upload_dir, mf_filename)
            mf_file.save(mf_path)

        preview_data = preview_excel_sync(ec_path, mf_path)
        preview_data['ec_path'] = ec_path
        preview_data['mf_path'] = mf_path or ''

        return jsonify({'success': True, 'preview': preview_data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@fenogramas_bp.route('/api/confirmar-plano', methods=['POST'])
@login_required
@permission_required('fenogramas')
def api_confirmar_plano():
    try:
        data = request.get_json() or {}
        semana = data.get('semana')
        anio = data.get('anio', 2026)
        ec_path = data.get('ec_path')
        mf_path = data.get('mf_path')

        if not semana or not ec_path:
            return jsonify({'success': False, 'error': 'Parámetros requeridos faltantes'}), 400

        count = sync_excel_to_db(semana_anio=semana, anio=anio, ec_file_path=ec_path, mf_file_path=mf_path if mf_path else None)
        return jsonify({'success': True, 'count': count, 'semana': semana, 'anio': anio})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@fenogramas_bp.route('/api/inyectar-matriz', methods=['POST'])
@login_required
@admin_required
@permission_required('fenogramas')
def api_inyectar_matriz():
    try:
        semana = request.form.get('semana', type=int)
        mf_file = request.files.get('file_matriz_fenogramas')

        if not mf_file or not mf_file.filename:
            return jsonify({'success': False, 'error': 'Debes adjuntar el archivo Matriz Fenogramas Excel (.xlsx)'}), 400

        upload_dir = os.path.join(os.getcwd(), 'uploads')
        os.makedirs(upload_dir, exist_ok=True)

        mf_filename = secure_filename(mf_file.filename)
        mf_path = os.path.join(upload_dir, mf_filename)
        mf_file.save(mf_path)

        # 1. Procesar reglas de la matriz desde el archivo Excel subido
        config = load_mapeos_config()
        df_matrix_rules = process_matriz_fenogramas(mf_path, config)

        if df_matrix_rules is None or df_matrix_rules.empty:
            return jsonify({'success': False, 'error': 'El archivo Excel no contiene hojas o columnas de labores válidas (Semana, Labor).'}), 400

        # 2. Reemplazar tabla matriz_fenogramas en PostgreSQL
        db.session.execute(text("TRUNCATE TABLE matriz_fenogramas RESTART IDENTITY CASCADE;"))
        for _, r in df_matrix_rules.iterrows():
            db.session.add(MatrizFenograma(
                sheet_name=r.get('sheet_name', ''),
                producto=r['producto'],
                actividad_tipo=r['actividad_tipo'],
                semana=int(r['semana']),
                labor=r['labor'],
                auxiliar=r.get('auxiliar') or '',
                supervisor=r.get('supervisor') or ''
            ))
        db.session.commit()

        # 3. Si se pasó semana activa, recalcular o actualizar actividades
        recalculadas = 0
        if semana and semana > 0:
            try:
                recalculadas = sync_from_mrt_estado_cultivo(semana_corte=semana, anio=2026, fuente='AUTO')
            except Exception as e_recalc:
                print(f"[AVISO] Al recalcular calificaciones para semana {semana}: {e_recalc}")

        # Limpiar archivo temporal
        try:
            if os.path.exists(mf_path):
                os.remove(mf_path)
        except Exception:
            pass

        return jsonify({
            'success': True,
            'count': len(df_matrix_rules),
            'recalculadas': recalculadas,
            'semana': semana,
            'message': f'Matriz actualizada exitosamente ({len(df_matrix_rules)} reglas cargadas).'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@fenogramas_bp.route('/api/guardar-mapeos', methods=['POST'])
@login_required
@permission_required('fenogramas')
def api_guardar_mapeos():
    try:
        data = request.get_json() or {}
        save_mapeos_config(data)
        semana = data.get('semana', 30)
        sync_excel_to_db(semana_anio=semana, anio=2026)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==============================================================================
# 10. CIERRE DE SEMANA Y ARRASTRE DE PENDIENTES
# ==============================================================================
@fenogramas_bp.route('/api/cerrar-semana', methods=['POST'])
@login_required
@admin_required
@permission_required('fenogramas')
def api_cerrar_semana():
    data = request.get_json() or {}
    semana_actual = data.get('semana_actual', 30)

    try:
        sem_sig, carried = cerrar_semana_evaluacion(semana_actual=int(semana_actual), anio=2026)
        return jsonify({
            'success': True,
            'semana_cerrada': semana_actual,
            'semana_siguiente': sem_sig,
            'actividades_recalculadas': carried
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@fenogramas_bp.route('/api/reset-semana-limpia', methods=['POST', 'GET'])
@login_required
@admin_required
def api_reset_semana_limpia():
    semana_target = request.args.get('semana', 43, type=int)
    try:
        count = sync_from_mrt_estado_cultivo(
            semana_corte=semana_target,
            anio=2026,
            fuente='AUTO',
            forzar_limpieza=True
        )
        flash(f'Semana {semana_target} limpiada y regenerada desde cero con éxito ({count} labores proyectadas).', 'success')
    except Exception as e:
        flash(f'Error al limpiar y regenerar semana {semana_target}: {e}', 'danger')
    return redirect(url_for('fenogramas.index', semana=semana_target))

