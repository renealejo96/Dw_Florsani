from flask import Blueprint, render_template, request, jsonify
from app.extensions import db
from app.shared.models import CropStateRecord, Crop
from app.modules.fumigacion.services.calculation_engine import CalculationEngine
from app.modules.auth.routes import login_required, permission_required

estado_cultivo_bp = Blueprint('estado_cultivo', __name__)

@estado_cultivo_bp.route('/')
@login_required
@permission_required('catalogos', 'estado_cultivo')
def index():
    crop_filter = request.args.get('crop', '').strip()
    zone_filter = request.args.get('zone', '').strip()
    import re

    def parse_week_tuple(w_raw):
        s = str(w_raw or '').strip()
        m_dash = re.match(r'^(\d{4})\s*[-/]\s*(\d{1,2})$', s)
        if m_dash:
            return (int(m_dash.group(1)), int(m_dash.group(2)))
        if s.isdigit() and len(s) == 4:
            return (2000 + int(s[:2]), int(s[2:]))
        if s.isdigit() and len(s) <= 2:
            return (2026, int(s))
        nums = re.findall(r'\d+', s)
        if len(nums) >= 2:
            y = int(nums[0])
            if y < 100:
                y += 2000
            return (y, int(nums[1]))
        elif len(nums) == 1:
            v = int(nums[0])
            if v > 2000:
                return (2000 + (v // 100), v % 100)
            return (2026, v)
        return (0, 0)

    def format_sc(sc):
        yr, wk = parse_week_tuple(sc)
        if yr > 0 and wk > 0:
            return f"{yr}-{wk:02d}"
        return str(sc or '')

    # Semanas cargadas en crop_state_records con conteo de camas
    week_counts_rows = db.session.query(
        CropStateRecord.week,
        db.func.count(CropStateRecord.id)
    ).filter(CropStateRecord.week.isnot(None)).group_by(CropStateRecord.week).all()

    semanas_cargadas_info = []
    for w_val, cnt in week_counts_rows:
        if not w_val:
            continue
        yr, wk = parse_week_tuple(w_val)
        semanas_cargadas_info.append({
            'raw': w_val,
            'label': format_sc(w_val),
            'week_num': wk,
            'year': yr,
            'count': int(cnt or 0),
            'sort_key': (yr, wk)
        })
    semanas_cargadas_info.sort(key=lambda x: x['sort_key'], reverse=True)
    unique_weeks = [x['raw'] for x in semanas_cargadas_info]

    # Si no se pasó ?week= en la URL, seleccionar por defecto la última semana cargada
    raw_week_arg = request.args.get('week')
    if raw_week_arg is None:
        week_filter = unique_weeks[0] if unique_weeks else ''
    elif raw_week_arg.strip().upper() == 'ALL':
        week_filter = 'ALL'
    else:
        week_filter = raw_week_arg.strip()

    base_week_query = CropStateRecord.query
    if week_filter and week_filter != 'ALL':
        base_week_query = base_week_query.filter(CropStateRecord.week == week_filter)

    if crop_filter:
        base_week_query = base_week_query.filter(
            db.or_(
                CropStateRecord.crop_master.ilike(f"%{crop_filter}%"),
                CropStateRecord.product_name.ilike(f"%{crop_filter}%")
            )
        )
    if zone_filter:
        base_week_query = base_week_query.filter(CropStateRecord.zone.ilike(f"%{zone_filter}%"))

    all_records = base_week_query.all()

    total_records = len(all_records)
    active_records = 0
    empty_records = 0
    total_std_beds = 0.0

    zone_map = {}
    crop_map = {}
    crop_config_cache = {}

    for r in all_records:
        z_name = (r.zone or 'SIN ZONA').strip()
        c_master = (r.crop_master or 'VACIO').strip().upper()
        is_active = bool(c_master and c_master != 'VACIO')
        b_clean = (r.block_num or r.block_full or '').strip()
        std_val = float(r.standard_bed or 0.0)

        if z_name not in zone_map:
            zone_map[z_name] = {
                'zone': z_name,
                'blocks': set(),
                'active_beds': 0,
                'empty_beds': 0,
                'veg_beds': 0,
                'prod_beds': 0,
                'std_beds': 0.0
            }

        if is_active:
            active_records += 1
            total_std_beds += std_val
            zone_map[z_name]['active_beds'] += 1
            zone_map[z_name]['std_beds'] += std_val
            if b_clean:
                zone_map[z_name]['blocks'].add(b_clean)

            ckey = c_master
            if ckey not in crop_config_cache:
                crop_config_cache[ckey] = CalculationEngine.get_crop_config(c_master or r.product_name)
            crop_obj = crop_config_cache[ckey]
            stage = crop_obj.classify_age(r.real_age) if crop_obj else 'VEGETATIVO'

            if stage == 'PRODUCTIVO':
                zone_map[z_name]['prod_beds'] += 1
            else:
                zone_map[z_name]['veg_beds'] += 1

            if c_master not in crop_map:
                crop_map[c_master] = {
                    'crop': c_master,
                    'blocks': set(),
                    'varieties': set(),
                    'active_beds': 0,
                    'veg_beds': 0,
                    'prod_beds': 0,
                    'std_beds': 0.0
                }
            crop_map[c_master]['active_beds'] += 1
            crop_map[c_master]['std_beds'] += std_val
            if b_clean:
                crop_map[c_master]['blocks'].add(b_clean)
            if r.variety:
                crop_map[c_master]['varieties'].add(r.variety.strip())
            if stage == 'PRODUCTIVO':
                crop_map[c_master]['prod_beds'] += 1
            else:
                crop_map[c_master]['veg_beds'] += 1
        else:
            empty_records += 1
            zone_map[z_name]['empty_beds'] += 1

    summary_by_zone = []
    for z_name in sorted(zone_map.keys()):
        zd = zone_map[z_name]
        act = zd['active_beds']
        summary_by_zone.append({
            'zone': zd['zone'],
            'blocks_count': len(zd['blocks']),
            'active_beds': act,
            'empty_beds': zd['empty_beds'],
            'total_beds': act + zd['empty_beds'],
            'veg_beds': zd['veg_beds'],
            'prod_beds': zd['prod_beds'],
            'pct_veg': round((zd['veg_beds'] / act) * 100, 1) if act > 0 else 0.0,
            'pct_prod': round((zd['prod_beds'] / act) * 100, 1) if act > 0 else 0.0,
            'std_beds': round(zd['std_beds'], 2)
        })

    summary_by_crop = []
    for c_name in sorted(crop_map.keys(), key=lambda k: crop_map[k]['active_beds'], reverse=True):
        cd = crop_map[c_name]
        act = cd['active_beds']
        summary_by_crop.append({
            'crop': cd['crop'],
            'blocks_count': len(cd['blocks']),
            'varieties_count': len(cd['varieties']),
            'active_beds': act,
            'veg_beds': cd['veg_beds'],
            'prod_beds': cd['prod_beds'],
            'pct_veg': round((cd['veg_beds'] / act) * 100, 1) if act > 0 else 0.0,
            'pct_prod': round((cd['prod_beds'] / act) * 100, 1) if act > 0 else 0.0,
            'std_beds': round(cd['std_beds'], 2)
        })

    unique_crops = [r[0] for r in db.session.query(CropStateRecord.crop_master).filter(CropStateRecord.crop_master.isnot(None), CropStateRecord.crop_master != 'VACIO').distinct().order_by(CropStateRecord.crop_master).all() if r[0]]
    unique_zones = [r[0] for r in db.session.query(CropStateRecord.zone).distinct().order_by(CropStateRecord.zone).all() if r[0]]

    curr_cal_week = db.session.execute(db.text("SELECT semana_campo FROM dim_calendario_agricola WHERE fecha = CURRENT_DATE LIMIT 1;")).scalar() or 2642

    ultima_semana_excel = semanas_cargadas_info[0]['label'] if semanas_cargadas_info else format_sc(curr_cal_week)
    if week_filter and week_filter != 'ALL':
        corte_semana = format_sc(week_filter)
    else:
        corte_semana = ultima_semana_excel

    semana_calendario = format_sc(curr_cal_week)

    return render_template(
        'estado_cultivo/index.html',
        summary_by_zone=summary_by_zone,
        summary_by_crop=summary_by_crop,
        total_records=total_records,
        active_records=active_records,
        empty_records=empty_records,
        total_std_beds=round(total_std_beds, 2),
        unique_crops=unique_crops,
        unique_zones=unique_zones,
        unique_weeks=unique_weeks,
        semanas_cargadas_info=semanas_cargadas_info,
        selected_crop=crop_filter,
        selected_zone=zone_filter,
        selected_week=week_filter,
        corte_semana=corte_semana,
        ultima_semana_excel=ultima_semana_excel,
        semana_calendario=semana_calendario
    )
