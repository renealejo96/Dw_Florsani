import os
import io
import csv
from datetime import date, datetime
import pandas as pd
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, session
from werkzeug.utils import secure_filename
from app.extensions import db
from app.shared.audit import record_audit
from app.modules.auth.routes import login_required, permission_required
from app.modules.fertilizacion.models import (
    Tecnico, TipoAplicacion, VariedadFerti, TecnicoBloque,
    DrenchItem, RiegoDuchaRegistro, ValvulaRegistro,
    UsuarioAplicador, calcular_semana_formato
)
from app.modules.fertilizacion.services.drench_service import (
    obtener_items_drench, toggle_completado_drench,
    obtener_resumen_clasificaciones, obtener_bloques_disponibles,
    obtener_bombas_por_clasificacion
)
from app.modules.fertilizacion.services.report_service import generar_datos_reporte_powerbi

fertilizacion_bp = Blueprint('fertilizacion', __name__, url_prefix='/fertilizacion')

# ==============================================================================
# VISTA PRINCIPAL: CHECKLIST & APLICACIONES DIARIAS
# ==============================================================================
@fertilizacion_bp.route('/')
@login_required
@permission_required('fertilizacion')
def index():
    hoy = date.today()
    semana_calendario = calcular_semana_formato(hoy)

    semanas_db = [
        r[0] for r in db.session.query(DrenchItem.semana.distinct())
        .filter(DrenchItem.semana.isnot(None))
        .order_by(DrenchItem.semana.desc())
        .all()
    ]
    
    semana_solicitada = request.args.get('semana', '').strip()
    semana_activa = semana_solicitada if semana_solicitada else (semanas_db[0] if semanas_db else semana_calendario)

    semanas_disponibles = list(semanas_db)
    if semana_calendario not in semanas_disponibles:
        semanas_disponibles.insert(0, semana_calendario)

    total_items_semana = DrenchItem.query.filter_by(semana=semana_activa).count()
    semana_sin_datos = (total_items_semana == 0)

    tecnicos = Tecnico.query.filter_by(activo=True).order_by(Tecnico.nombre).all()
    tipos = TipoAplicacion.query.filter_by(activo=True).order_by(TipoAplicacion.id).all()
    variedades = VariedadFerti.query.filter_by(activo=True).order_by(VariedadFerti.nombre).all()

    drench_items = obtener_items_drench(semana=semana_activa)
    resumen_clasif = obtener_resumen_clasificaciones(semana=semana_activa)
    bloques_drench = obtener_bloques_disponibles(semana=semana_activa)

    # 1. Separar Trichos (biológicos/enraizantes) vs Sólidos (Moshe, Alcaplant, Sulfato)
    trichos_items = [
        item for item in drench_items 
        if not any(k in (item.bomba or '').upper() or k in (item.clasificacion or '').upper() for k in ['MOSHE', 'ALCAPLANT', 'SOLID', 'SÓLID', 'SULFATO'])
    ]
    solidos_items = [
        item for item in drench_items 
        if any(k in (item.bomba or '').upper() or k in (item.clasificacion or '').upper() for k in ['MOSHE', 'ALCAPLANT', 'SOLID', 'SÓLID', 'SULFATO'])
    ]

    # 2. Mesa de Calibración: Procesar grupos por Edad, por Técnico y por Bomba
    calibracion_ordenada, calibracion_tecnicos, calibracion_bombas = procesar_calibracion_fertilizacion(drench_items)

    riego_ducha_items = RiegoDuchaRegistro.query.filter_by(fecha=hoy).order_by(RiegoDuchaRegistro.id.desc()).all()
    valvula_items = ValvulaRegistro.query.filter_by(fecha=hoy).order_by(ValvulaRegistro.id.desc()).all()
    aplicadores = UsuarioAplicador.query.filter_by(activo=True).order_by(UsuarioAplicador.nombre).all()

    # Catálogo de bloques con Zona y Técnico de Riego para auto-completado en modales
    from sqlalchemy import text
    import json, re
    catalogo_bloques = {}
    try:
        rows_b = db.session.execute(text("SELECT codigo_bloque, blq, zona, tecnico_de_riego FROM dim_bloques ORDER BY blq")).fetchall()
        for r in rows_b:
            cod = (r[0] or '').strip().upper()
            b = (r[1] or '').strip().upper()
            z = (r[2] or 'ZONA GENERAL').strip()
            t = (r[3] or 'C1').strip().upper()
            m = re.search(r'([1-5])', t)
            t_norm = f"Técnico {m.group(1)} (C{m.group(1)})" if m else f"Técnico {t}"
            inf = {'zona': z, 'tecnico': t_norm}
            for k in [cod, b, f"BL {cod}", f"BL {b}", f"BL  {b}", cod.replace(' ', ''), b.replace(' ', '')]:
                if k:
                    catalogo_bloques[k] = inf
    except Exception:
        pass

    return render_template(
        'fertilizacion/index.html',
        hoy=hoy.strftime("%Y-%m-%d"),
        semana_activa=semana_activa,
        semana_calendario=semana_calendario,
        semanas_disponibles=semanas_disponibles,
        semana_sin_datos=semana_sin_datos,
        total_items_semana=total_items_semana,
        tecnicos=tecnicos,
        tipos=tipos,
        variedades=variedades,
        drench_items=drench_items,
        trichos_items=trichos_items,
        solidos_items=solidos_items,
        calibracion_ordenada=calibracion_ordenada,
        calibracion_tecnicos=calibracion_tecnicos,
        calibracion_bombas=calibracion_bombas,
        resumen_clasif=resumen_clasif,
        bloques_drench=bloques_drench,
        riego_ducha_items=riego_ducha_items,
        valvula_items=valvula_items,
        aplicadores=aplicadores,
        catalogo_bloques_json=json.dumps(catalogo_bloques)
    )


def proyectar_semana_fertilizacion(semana_destino, semana_origen=None):
    """
    Proyecta automáticamente las camas, bloques, edades y bombas para una semana futura
    (ej. Semana 2641, 2643, etc.) sumando el diferencial de semanas a la edad.
    """
    semana_dest_clean = str(semana_destino).replace('-', '').strip()
    if len(semana_dest_clean) == 6:  # 202643
        semana_dest_clean = semana_dest_clean[2:]  # 2643

    if not semana_origen:
        max_sem = db.session.query(DrenchItem.semana).filter(
            DrenchItem.semana.isnot(None),
            DrenchItem.semana != semana_dest_clean
        ).order_by(DrenchItem.semana.desc()).first()
        semana_origen = max_sem[0] if max_sem else '2639'

    def parse_sem(s):
        s_clean = str(s).replace('-', '').strip()
        if len(s_clean) == 6:  # 202641
            return int(s_clean[:4]), int(s_clean[4:])
        elif len(s_clean) == 4:  # 2641
            return 2000 + int(s_clean[:2]), int(s_clean[2:])
        return 2026, 40

    y_dest, w_dest = parse_sem(semana_dest_clean)
    y_orig, w_orig = parse_sem(semana_origen)
    offset = ((y_dest - y_orig) * 52) + (w_dest - w_orig)
    if offset < 0:
        offset = 0

    base_items = DrenchItem.query.filter_by(semana=semana_origen).all()
    if not base_items:
        return 0

    # Evitar duplicados si se re-proyecta la semana destino
    DrenchItem.query.filter_by(semana=semana_dest_clean).delete()

    nuevos = []
    for bi in base_items:
        nueva_edad = max(1, (bi.edad or 0) + offset)
        item_nuevo = DrenchItem(
            fecha=date.today(),
            semana=semana_dest_clean,
            clasificacion=bi.clasificacion,
            actividad=bi.actividad,
            bomba=bi.bomba,
            bloque=bi.bloque,
            cultivo=bi.cultivo,
            cama_inicial=bi.cama_inicial,
            cama_final=bi.cama_final,
            cama_estandar=bi.cama_estandar,
            edad=nueva_edad,
            cantidad_camas=bi.cantidad_camas,
            grupo_id=bi.grupo_id,
            segmento_edades=bi.segmento_edades,
            completado=False,
            aplicador='',
            tecnico=bi.tecnico,
            fecha_completado=None,
            observaciones=f"Proyectado desde Sem {semana_origen} (+{offset} sem)"
        )
        nuevos.append(item_nuevo)

    db.session.bulk_save_objects(nuevos)
    db.session.commit()

    record_audit('FERTILIZACION', 'PROYECTAR_SEMANA', 'DrenchItem', int(semana_dest_clean) if semana_dest_clean.isdigit() else 0,
                 details=f"Semana {semana_dest_clean} proyectada con {len(nuevos)} ítems desde Sem {semana_origen} (+{offset} sem).")

    return len(nuevos)


def procesar_calibracion_fertilizacion(drench_items):
    """
    Agrupa ítems de drench/trichos/sólidos por edad, por técnico y por bomba.
    Aplica la regla de negocio:
    - Trichos default: 41.7 L/cama
    - Sólidos default: 30.0 L/cama (Sulfato de potasio, Moshe, Alcaplant)
    - Dosis requerida = round(dose * vol_cama * num_camas) a enteros.
    """
    from app.shared.models import Product
    products = Product.query.filter_by(is_active=True).all()
    prod_map = {}
    for p in products:
        c_name = (p.commercial_name or '').strip().upper()
        code = (p.code or '').strip().upper()
        if c_name:
            prod_map[c_name] = p
        if code:
            prod_map[code] = p

    import re
    from sqlalchemy import text
    dim_map = {}
    try:
        rows_dim = db.session.execute(
            text("SELECT codigo_bloque, blq, zona, tecnico_de_riego FROM dim_bloques")
        ).fetchall()
        for r in rows_dim:
            cod = (r[0] or '').strip().upper()
            blq_b = (r[1] or '').strip().upper()
            z = (r[2] or 'ZONA GENERAL').strip()
            tec_code = (r[3] or 'C1').strip().upper()
            m = re.search(r'([1-5])', tec_code)
            tec_norm = f"Técnico {m.group(1)} (C{m.group(1)})" if m else f"Técnico {tec_code}"
            info = {'zona': z, 'tecnico': tec_norm}
            for k in [cod, blq_b, f"BL {cod}", f"BL {blq_b}", f"BL  {blq_b}", f"BL{blq_b}", cod.replace(' ', ''), blq_b.replace(' ', '')]:
                if k:
                    dim_map[k] = info
    except Exception as e:
        print(f"Error cargando dim_bloques: {e}")

    def resolver_tecnico_y_zona(raw_tec, blq):
        clean_b = str(blq or '').strip().upper()
        b_info = dim_map.get(clean_b)
        if not b_info:
            b_stripped = clean_b.replace('BL', '').strip()
            b_info = dim_map.get(b_stripped) or dim_map.get(clean_b.replace(' ', ''))

        zona_res = b_info['zona'] if b_info else 'ZONA GENERAL'

        raw = (raw_tec or '').strip()
        if raw and any(char.isdigit() for char in raw):
            m = re.search(r'([1-5])', raw)
            if m:
                return f"Técnico {m.group(1)} (C{m.group(1)})", zona_res

        if b_info and b_info.get('tecnico'):
            return b_info['tecnico'], zona_res

        return 'Técnico 1 (C1)', zona_res

    calibracion_por_edad = {}
    calibracion_por_tecnico = {}
    calibracion_por_bomba = {}

    for item in drench_items:
        ed = item.edad or 0
        b_name = (item.bomba or 'SIN BOMBA').strip()
        es_sol = any(k in b_name.upper() for k in ['MOSHE', 'ALCAPLANT', 'SULFATO', 'POTASIO', 'MAGNESIO', 'CALCIO', 'SOLID', 'SÓLID'])
        vol_default = 30.0 if es_sol else 41.7
        tec_name, zona_name = resolver_tecnico_y_zona(item.tecnico, item.bloque)
        item.tecnico = tec_name
        item.zona = zona_name

        if ed not in calibracion_por_edad:
            calibracion_por_edad[ed] = {
                'edad': ed,
                'items': [],
                'bloques': set(),
                'camas_totales': 0,
                'bombas': set(),
                'es_siembra': False,
                'es_poda': False,
                'tecnicos': set(),
                'tipo_grupo': 'SOLIDOS' if es_sol else 'TRICHOS',
                'vol_default': vol_default,
                'productos_calculados': [],
                'items_detail': []
            }
        calibracion_por_edad[ed]['items'].append(item)
        calibracion_por_edad[ed]['bloques'].add(item.bloque)
        calibracion_por_edad[ed]['camas_totales'] += (item.cantidad_camas or 0)
        calibracion_por_edad[ed]['bombas'].add(item.bomba)
        calibracion_por_edad[ed]['tecnicos'].add(tec_name)
        if 'SIEMBRA' in (item.actividad or '').upper():
            calibracion_por_edad[ed]['es_siembra'] = True
        if 'PODA' in (item.actividad or '').upper():
            calibracion_por_edad[ed]['es_poda'] = True

        # Agrupar por técnico / cuarto de bombeo normalizado
        if tec_name not in calibracion_por_tecnico:
            calibracion_por_tecnico[tec_name] = {
                'tecnico': tec_name,
                'bloques': set(),
                'camas_totales': 0,
                'items': [],
                'edades': set(),
                'bombas': set()
            }
        calibracion_por_tecnico[tec_name]['bloques'].add(item.bloque)
        calibracion_por_tecnico[tec_name]['camas_totales'] += (item.cantidad_camas or 0)
        calibracion_por_tecnico[tec_name]['items'].append(item)
        calibracion_por_tecnico[tec_name]['edades'].add(item.edad or 0)
        calibracion_por_tecnico[tec_name]['bombas'].add(item.bomba)

        # Agrupar por Bomba directamente (para impresión vertical de bloques agrupados)
        b_key = (b_name, ed)
        if b_key not in calibracion_por_bomba:
            b_upper = b_name.upper()
            if 'MOSHE' in b_upper:
                desc_label = 'MOSHE (Nutrición / Enraizamiento)'
                t_key = 'MOSHE'
            elif 'ALCAPLANT' in b_upper:
                desc_label = 'ALCAPLANT (Encalado y Calcio)'
                t_key = 'ALCAPLANT'
            elif any(p in b_upper for p in ['PILATUS', 'RAIZAL', 'TURBO ROOT', 'ENRAIZ']):
                desc_label = 'ENRAIZANTES'
                t_key = 'ENRAIZANTES'
            elif any(k in b_upper for k in ['SULFATO', 'POTASIO', 'MAGNESIO', 'CALCIO', 'SOLID', 'SÓLID']):
                desc_label = 'SÓLIDOS EDÁFICOS'
                t_key = 'SOLIDOS'
            elif 'TRICHO' in b_upper or 'GLIO' in b_upper or 'BIOLOG' in b_upper:
                desc_label = 'TRICHOS & BIOLÓGICOS'
                t_key = 'TRICHOS'
            else:
                desc_label = item.clasificacion or 'DRENCH'
                t_key = 'SOLIDOS' if es_sol else 'TRICHOS'

            calibracion_por_bomba[b_key] = {
                'bomba': b_name,
                'cultivo': item.cultivo or 'GENERAL',
                'edad': ed,
                'descripcion': desc_label,
                'tratamiento_key': t_key,
                'es_solidos': es_sol,
                'vol_default': vol_default,
                'camas_totales': 0,
                'litros_totales': 0.0,
                'bloques': [],
                'tecnicos': set(),
                'items': [],
                'bloque_items': [],
                'productos_calculados': []
            }

        c_camas_it = float(item.cantidad_camas or 0)
        lits_it = round(c_camas_it * vol_default, 1)
        calibracion_por_bomba[b_key]['camas_totales'] += int(c_camas_it)
        calibracion_por_bomba[b_key]['litros_totales'] += lits_it
        if item.bloque not in calibracion_por_bomba[b_key]['bloques']:
            calibracion_por_bomba[b_key]['bloques'].append(item.bloque)
        calibracion_por_bomba[b_key]['tecnicos'].add(tec_name)

        # Insumos y dosis específicos de la bomba para este bloque
        parts_b = [p.strip().upper() for p in (b_name or '').split(',') if p.strip()]
        b_item_prods = []
        for p_name in parts_b:
            p_match = None
            for k, v in prod_map.items():
                if k == p_name or p_name in k or k in p_name:
                    p_match = v
                    break
            unit = p_match.unit if p_match else 'GR'
            dose = (p_match.dose_solidos if es_sol else p_match.dose_trichos) if p_match else None
            if dose is None and p_match:
                dose = p_match.dose_drench
            if dose is None:
                dose = 0.05
            dose_str = f"{dose} {unit}/L" if unit in ['GR', 'CC', 'ML', 'KG', 'L'] else f"{dose} {unit}"
            b_item_prods.append({
                'name': p_match.commercial_name if p_match else p_name,
                'unit': unit,
                'dose': dose,
                'dose_str': dose_str
            })

        c_inic_it = item.cama_inicial or 1
        c_fin_it = item.cama_final or (c_inic_it + int(c_camas_it) - 1 if c_camas_it > 0 else c_inic_it)
        item_detail_obj = {
            'id': item.id,
            'bloque': item.bloque,
            'zona': zona_name,
            'cultivo': item.cultivo or 'GENERAL',
            'variedad': item.cultivo or 'GENERAL',
            'edad': ed,
            'cama_inicial': c_inic_it,
            'cama_final': c_fin_it,
            'cantidad_camas': int(c_camas_it),
            'cama_estandar': float(item.cama_estandar or 0.0),
            'litros_cama': vol_default,
            'total_litros': lits_it,
            'tecnico': tec_name,
            'bomba': b_name,
            'descripcion': calibracion_por_bomba[b_key]['descripcion'],
            'productos': b_item_prods
        }
        calibracion_por_bomba[b_key]['items'].append(item_detail_obj)
        calibracion_por_bomba[b_key]['bloque_items'].append(item_detail_obj)

    # Ordenar y enriquecer grupos de edad
    calibracion_ordenada = []
    for ed in sorted(calibracion_por_edad.keys()):
        grp = calibracion_por_edad[ed]
        es_solidos = any(
            any(k in (b or '').upper() for k in ['MOSHE', 'ALCAPLANT', 'SULFATO', 'POTASIO', 'MAGNESIO', 'CALCIO', 'SOLID', 'SÓLID'])
            for b in grp['bombas']
        )
        grp['tipo_grupo'] = 'SOLIDOS' if es_solidos else 'TRICHOS'
        grp['vol_default'] = 30.0 if es_solidos else 41.7
        grp['bloques'] = sorted(list(grp['bloques']))
        grp['bombas'] = sorted(list(grp['bombas']))
        grp['tecnicos'] = sorted(list(grp['tecnicos']))
        grp['litros_totales'] = round(grp['camas_totales'] * grp['vol_default'], 1)

        prods_dict = {}
        for b_str in grp['bombas']:
            parts = [p.strip().upper() for p in (b_str or '').split(',') if p.strip()]
            for p_name in parts:
                p_match = None
                for k, v in prod_map.items():
                    if k == p_name or p_name in k or k in p_name:
                        p_match = v
                        break
                dose = None
                unit = 'GR'
                comm_name = p_name
                if p_match:
                    comm_name = p_match.commercial_name or p_name
                    unit = p_match.unit or 'GR'
                    if grp['tipo_grupo'] == 'SOLIDOS':
                        dose = p_match.dose_solidos or p_match.dose_drench
                    else:
                        dose = p_match.dose_trichos or p_match.dose_drench
                if dose is None:
                    dose = 0.05
                
                total_qty = round(float(dose) * float(grp['vol_default']) * float(grp['camas_totales']))
                if comm_name not in prods_dict:
                    prods_dict[comm_name] = {
                        'name': comm_name,
                        'unit': unit,
                        'dose': dose,
                        'total_cantidad': total_qty
                    }
                else:
                    prods_dict[comm_name]['total_cantidad'] += total_qty

        grp['productos_calculados'] = list(prods_dict.values())

        # Detalle de camas y bloques para la mesa de calibración interactiva
        items_detail = []
        for it in sorted(grp['items'], key=lambda x: str(x.bloque)):
            c_cam = float(it.cantidad_camas or 0)
            c_inic = it.cama_inicial or 1
            c_fin = it.cama_final or (c_inic + int(c_cam) - 1 if c_cam > 0 else c_inic)
            it_tec, it_zona = resolver_tecnico_y_zona(it.tecnico, it.bloque)

            # Insumos y dosis específicos de la bomba para este bloque
            parts_it = [p.strip().upper() for p in (it.bomba or '').split(',') if p.strip()]
            it_prods = []
            for p_name in parts_it:
                p_match = None
                for k, v in prod_map.items():
                    if k == p_name or p_name in k or k in p_name:
                        p_match = v
                        break
                unit = p_match.unit if p_match else 'GR'
                dose = (p_match.dose_solidos if grp['tipo_grupo'] == 'SOLIDOS' else p_match.dose_trichos) if p_match else None
                if dose is None and p_match:
                    dose = p_match.dose_drench
                if dose is None:
                    dose = 0.05
                dose_str = f"{dose} {unit}/L" if unit in ['GR', 'CC', 'ML', 'KG', 'L'] else f"{dose} {unit}"
                it_prods.append({
                    'name': p_match.commercial_name if p_match else p_name,
                    'unit': unit,
                    'dose': dose,
                    'dose_str': dose_str
                })

            items_detail.append({
                'id': it.id,
                'bloque': it.bloque,
                'zona': it_zona,
                'cultivo': it.cultivo or 'GENERAL',
                'variedad': it.cultivo or 'GENERAL',
                'actividad': it.actividad or 'PODA',
                'cama_inicial': c_inic,
                'cama_final': c_fin,
                'cantidad_camas': int(c_cam),
                'cama_estandar': float(it.cama_estandar or 0.0),
                'edad': it.edad or ed,
                'litros_cama': grp['vol_default'],
                'total_litros': round(c_cam * grp['vol_default'], 1),
                'bomba': it.bomba,
                'tecnico': it_tec,
                'clasificacion': grp['tipo_grupo'],
                'productos': it_prods
            })
        grp['items_detail'] = items_detail
        calibracion_ordenada.append(grp)

    # Ordenar y enriquecer grupos por técnico
    calibracion_tecnico_ordenada = []
    for tec in sorted(calibracion_por_tecnico.keys()):
        t_data = calibracion_por_tecnico[tec]
        t_data['bloques'] = sorted(list(t_data['bloques']))
        t_data['edades'] = sorted(list(t_data['edades']))
        t_data['bombas'] = sorted(list(t_data['bombas']))
        
        t_prods = {}
        t_litros = 0.0
        for item in t_data['items']:
            es_sol = any(k in (item.bomba or '').upper() for k in ['MOSHE', 'ALCAPLANT', 'SULFATO', 'POTASIO', 'MAGNESIO', 'CALCIO', 'SOLID', 'SÓLID'])
            v_cama = 30.0 if es_sol else 41.7
            c_camas = float(item.cantidad_camas or 0)
            t_litros += (v_cama * c_camas)
            parts = [p.strip().upper() for p in (item.bomba or '').split(',') if p.strip()]
            for p_name in parts:
                p_match = None
                for k, v in prod_map.items():
                    if k == p_name or p_name in k or k in p_name:
                        p_match = v
                        break
                dose = None
                unit = 'GR'
                comm_name = p_name
                if p_match:
                    comm_name = p_match.commercial_name or p_name
                    unit = p_match.unit or 'GR'
                    dose = (p_match.dose_solidos if es_sol else p_match.dose_trichos) or p_match.dose_drench
                if dose is None:
                    dose = 0.05
                tot_p = round(float(dose) * v_cama * c_camas)
                desc = f"{'Nutrición Sólidos (Moshe/Alcaplant/Sulfato)' if es_sol else 'Inoculación Biológica Trichos'} - Bloque {item.bloque}"
                if comm_name not in t_prods:
                    t_prods[comm_name] = {
                        'name': comm_name,
                        'unit': unit,
                        'dose': dose,
                        'total_cantidad': tot_p,
                        'descripcion': desc
                    }
                else:
                    t_prods[comm_name]['total_cantidad'] += tot_p
        t_data['litros_totales'] = round(t_litros, 1)
        t_data['productos_totales'] = list(t_prods.values())
        calibracion_tecnico_ordenada.append(t_data)

    # Ordenar y enriquecer grupos por Bomba
    calibracion_bombas_ordenada = []
    for b_key, b_grp in sorted(calibracion_por_bomba.items(), key=lambda x: (x[1]['edad'], x[1]['cultivo'], x[1]['bomba'])):
        b_grp['tecnicos'] = sorted(list(b_grp['tecnicos']))
        b_grp['litros_totales'] = round(b_grp['litros_totales'], 1)
        parts = [p.strip().upper() for p in b_grp['bomba'].split(',') if p.strip()]
        b_prods = []
        for p_name in parts:
            p_match = None
            for k, v in prod_map.items():
                if k == p_name or p_name in k or k in p_name:
                    p_match = v
                    break
            unit = p_match.unit if p_match else 'GR'
            dose = (p_match.dose_solidos if b_grp['es_solidos'] else p_match.dose_trichos) if p_match else None
            if dose is None and p_match:
                dose = p_match.dose_drench
            if dose is None:
                dose = 0.05
            tot_q = round(float(dose) * float(b_grp['litros_totales']))
            b_prods.append({
                'name': p_match.commercial_name if p_match else p_name,
                'unit': unit,
                'dose': dose,
                'total_cantidad': tot_q
            })
        b_grp['productos_calculados'] = b_prods
        calibracion_bombas_ordenada.append(b_grp)

    return calibracion_ordenada, calibracion_tecnico_ordenada, calibracion_bombas_ordenada


@fertilizacion_bp.route('/programa-impresion')
@login_required
@permission_required('fertilizacion', 'salidas_imprimir', 'bodega')
def programa_impresion():
    """
    Programa Oficial Imprimible de Trichos y Sólidos con Bloques listados verticalmente
    agrupados por Bomba y Cuarto de Bombeo / Técnico, con columnas de Edad y Descripción.
    """
    semana = request.args.get('semana', '').strip()
    if not semana:
        semana = calcular_semana_formato(date.today())

    filtro_tecnico = request.args.get('tecnico', '').strip()
    filtro_bomba = request.args.get('bomba', '').strip()
    drench_items = obtener_items_drench(semana=semana)
    calibracion_ordenada, calibracion_tecnicos, calibracion_bombas = procesar_calibracion_fertilizacion(drench_items)

    if filtro_tecnico and filtro_tecnico.upper() != 'TODOS':
        calibracion_tecnicos = [
            t for t in calibracion_tecnicos 
            if filtro_tecnico.upper() in t['tecnico'].upper()
        ]
        calibracion_bombas = [
            b for b in calibracion_bombas 
            if any(filtro_tecnico.upper() in tec.upper() for tec in b['tecnicos'])
        ]

    if filtro_bomba and filtro_bomba.upper() != 'TODAS':
        calibracion_bombas = [
            b for b in calibracion_bombas 
            if filtro_bomba.upper() in b['bomba'].upper()
        ]

    total_camas = sum(b['camas_totales'] for b in calibracion_bombas)
    total_litros = sum(b['litros_totales'] for b in calibracion_bombas)

    # Identificar categorías de tratamientos presentes
    cats_presentes = list(dict.fromkeys([b.get('tratamiento_key', 'TRICHOS') for b in calibracion_bombas]))

    return render_template(
        'fertilizacion/programa_impresion.html',
        semana=semana,
        filtro_tecnico=filtro_tecnico,
        filtro_bomba=filtro_bomba,
        calibracion_tecnicos=calibracion_tecnicos,
        calibracion_ordenada=calibracion_ordenada,
        calibracion_bombas=calibracion_bombas,
        categorias_tratamientos=cats_presentes,
        total_camas=total_camas,
        total_litros=total_litros,
        hoy=datetime.now().strftime("%d/%m/%Y %H:%M")
    )


# ==============================================================================
# API: CALIBRACIÓN INTERACTIVA & REASIGNACIÓN DE BOMBA
# ==============================================================================
@fertilizacion_bp.route('/api/calibracion/guardar', methods=['POST'])
@login_required
@permission_required('fertilizacion')
def api_calibracion_guardar():
    """Guarda modificaciones de camas, camas estándar o litros desde la mesa de calibración"""
    data = request.get_json() or {}
    items_data = data.get('items', [])
    deleted_ids = data.get('deleted_ids', [])
    if not items_data and not deleted_ids:
        return jsonify({"success": False, "message": "No se enviaron datos para guardar"}), 400

    try:
        import re
        deleted_count = 0
        if deleted_ids and isinstance(deleted_ids, list):
            deleted_count = DrenchItem.query.filter(
                DrenchItem.id.in_([int(i) for i in deleted_ids])
            ).delete(synchronize_session=False)

        updated_count = 0
        for it_info in items_data:
            it_id = it_info.get('id')
            if not it_id:
                continue
            item = DrenchItem.query.get(it_id)
            if item:
                if 'cantidad_camas' in it_info:
                    item.cantidad_camas = int(float(it_info['cantidad_camas']))
                if 'cama_estandar' in it_info:
                    item.cama_estandar = round(float(it_info['cama_estandar']), 2)
                if 'cama_inicial' in it_info:
                    item.cama_inicial = int(float(it_info['cama_inicial']))
                if 'cama_final' in it_info:
                    item.cama_final = int(float(it_info['cama_final']))
                if 'edad' in it_info:
                    item.edad = int(float(it_info['edad']))
                if 'tecnico' in it_info:
                    raw_t = str(it_info['tecnico']).strip()
                    m = re.search(r'([1-5])', raw_t)
                    if m:
                        item.tecnico = f"Técnico {m.group(1)} (C{m.group(1)})"
                    else:
                        item.tecnico = raw_t
                if 'bomba' in it_info:
                    item.bomba = str(it_info['bomba']).strip()
                updated_count += 1
        db.session.commit()
        msg_parts = []
        if updated_count:
            msg_parts.append(f"Se actualizaron {updated_count} bloques/camas calibradas")
        if deleted_count:
            msg_parts.append(f"Se eliminaron {deleted_count} bloques")
        return jsonify({
            "success": True,
            "message": ". ".join(msg_parts) if msg_parts else "Cambios guardados correctamente.",
            "updated_count": updated_count,
            "deleted_count": deleted_count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@fertilizacion_bp.route('/api/calibracion/eliminar-bloque', methods=['POST'])
@login_required
@permission_required('fertilizacion')
def api_calibracion_eliminar_bloque():
    """Elimina un bloque específico de la calibración/drench"""
    data = request.get_json() or {}
    item_id = data.get('id')
    if not item_id:
        return jsonify({"success": False, "message": "ID de bloque requerido"}), 400
    try:
        item = DrenchItem.query.get(int(item_id))
        if not item:
            return jsonify({"success": False, "message": "Registro no encontrado"}), 404
        blq = item.bloque
        db.session.delete(item)
        db.session.commit()
        return jsonify({"success": True, "message": f"Bloque {blq} eliminado correctamente."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@fertilizacion_bp.route('/api/bomba/cambiar-edad', methods=['POST'])
@login_required
@permission_required('fertilizacion')
def api_bomba_cambiar_edad():
    """Reasigna dinámicamente la edad de aplicación para una bomba específica (Exclusivo Administrador)"""
    if session.get('role') != 'ADMIN':
        return jsonify({
            "success": False,
            "message": "Acceso restringido: Solo un usuario Administrador puede reasignar o mover edades de bombas."
        }), 403

    data = request.get_json() or {}
    semana = data.get('semana', '').strip()
    bomba = data.get('bomba', '').strip()
    nueva_edad = data.get('nueva_edad')
    edad_origen = data.get('edad_origen')
    item_ids = data.get('item_ids')

    if not semana or not bomba or nueva_edad is None:
        return jsonify({"success": False, "message": "Semana, bomba y nueva edad son requeridos"}), 400

    try:
        nueva_edad_int = int(nueva_edad)
        w_target = semana
        w_clean = w_target.replace('-', '').replace('20', '', 1) if w_target.startswith('20') else w_target

        if item_ids and isinstance(item_ids, list) and len(item_ids) > 0:
            affected = DrenchItem.query.filter(
                DrenchItem.id.in_([int(i) for i in item_ids])
            ).update({'edad': nueva_edad_int}, synchronize_session=False)
        else:
            filt = [
                db.or_(
                    DrenchItem.semana == w_target,
                    DrenchItem.semana == w_clean,
                    DrenchItem.semana == f"20{w_clean}" if len(w_clean) == 4 else False
                ),
                DrenchItem.bomba == bomba
            ]
            if edad_origen is not None:
                try:
                    filt.append(DrenchItem.edad == int(edad_origen))
                except (ValueError, TypeError):
                    pass
            affected = DrenchItem.query.filter(*filt).update({'edad': nueva_edad_int}, synchronize_session=False)

        db.session.commit()
        record_audit(
            module='FERTILIZACION',
            action='REASIGNAR_BOMBA_EDAD',
            entity_type='DrenchItem',
            user=session.get('username', 'Admin'),
            details={'bomba': bomba, 'edad_origen': edad_origen, 'nueva_edad': nueva_edad_int, 'affected': affected, 'semana': semana}
        )
        return jsonify({
            "success": True,
            "message": f"Bomba '{bomba}' reasignada exitosamente a Semana {nueva_edad_int} ({affected} bloques actualizados).",
            "affected": affected,
            "nueva_edad": nueva_edad_int
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@fertilizacion_bp.route('/api/proyectar-semana', methods=['POST'])
@login_required
@permission_required('fertilizacion')
def api_proyectar_semana():
    """Proyecta automáticamente el programa de fertilización sumando diferencial de edad a siembras y podas"""
    data = request.get_json() or {}
    semana_destino = data.get('semana_destino', '').strip()
    semana_origen = data.get('semana_origen', '').strip()

    if not semana_destino:
        return jsonify({"success": False, "message": "Semana destino requerida"}), 400

    try:
        count = proyectar_semana_fertilizacion(semana_destino, semana_origen or None)
        return jsonify({
            "success": True,
            "message": f"Se proyectaron exitosamente {count} registros para la Semana {semana_destino}.",
            "count": count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500

# ==============================================================================
# VISTA DE REPORTES Y RENDIMIENTOS
# ==============================================================================
@fertilizacion_bp.route('/reportes')
@login_required
@permission_required('fertilizacion')
def reportes():
    tecnicos = Tecnico.query.filter_by(activo=True).order_by(Tecnico.nombre).all()
    hoy_str = date.today().strftime("%Y-%m-%d")
    semana_actual = calcular_semana_formato()

    semanas_db = [
        r[0] for r in db.session.query(DrenchItem.semana.distinct())
        .filter(DrenchItem.semana.isnot(None))
        .order_by(DrenchItem.semana.desc())
        .all()
    ]
    if not semanas_db:
        semanas_db = [semana_actual]

    return render_template(
        'fertilizacion/reports.html',
        tecnicos=tecnicos,
        hoy=hoy_str,
        semana_actual=semana_actual,
        semanas=semanas_db
    )

# ==============================================================================
# VISTA DE CONFIGURACIÓN Y CATÁLOGOS
# ==============================================================================
@fertilizacion_bp.route('/configuracion')
@login_required
@permission_required('fertilizacion')
def configuracion():
    import re
    from sqlalchemy import text
    tecnicos = Tecnico.query.order_by(Tecnico.nombre).all()
    tipos = TipoAplicacion.query.order_by(TipoAplicacion.id).all()
    variedades = VariedadFerti.query.order_by(VariedadFerti.nombre).all()
    aplicadores = UsuarioAplicador.query.order_by(UsuarioAplicador.nombre).all()

    bloques_list = []
    try:
        rows_dim = db.session.execute(
            text("""
                SELECT codigo_bloque, zona, tecnico_de_riego, 
                       COALESCE(total_camas_fisicas, 0) as camas_fisicas, 
                       COALESCE(total_camas_estandar, 0.0) as camas_estandar
                FROM dim_bloques 
                ORDER BY codigo_bloque
            """)
        ).fetchall()
        for r in rows_dim:
            tec_code = (r[2] or 'C1').strip().upper()
            m = re.search(r'([1-5])', tec_code)
            tec_norm = f"Técnico {m.group(1)} (C{m.group(1)})" if m else f"Técnico {tec_code}"
            bloques_list.append({
                'bloque': r[0],
                'zona': r[1] or 'ZONA GENERAL',
                'tecnico_nombre': tec_norm,
                'camas_fisicas': r[3],
                'cama_estandar': float(r[4] or 0.0)
            })
    except Exception as e:
        print(f"Error cargando bloques en configuracion: {e}")

    total_bloques = len(bloques_list)

    return render_template(
        'fertilizacion/config.html',
        tecnicos=tecnicos,
        tipos=tipos,
        variedades=variedades,
        total_bloques=total_bloques,
        bloques_list=bloques_list,
        aplicadores=aplicadores
    )

# ==============================================================================
# API: DRENCH ITEMS & TOGGLE
# ==============================================================================
@fertilizacion_bp.route('/api/drench/items', methods=['GET'])
@login_required
def api_drench_items():
    fecha_str = request.args.get('fecha')
    semana = request.args.get('semana')
    tecnico = request.args.get('tecnico')
    clasificacion = request.args.get('clasificacion')
    bomba = request.args.get('bomba')
    bloque = request.args.get('bloque')
    search = request.args.get('search')
    completado_str = request.args.get('completado')

    fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date() if fecha_str else None
    if not semana and not fecha:
        semana = calcular_semana_formato()

    completado = None
    if completado_str == 'true':
        completado = True
    elif completado_str == 'false':
        completado = False

    items = obtener_items_drench(
        fecha=fecha,
        semana=semana,
        clasificacion=clasificacion,
        bomba=bomba,
        bloque=bloque,
        completado=completado,
        search=search,
        tecnico=tecnico
    )
    resumen = obtener_resumen_clasificaciones(fecha=fecha, semana=semana, tecnico=tecnico)
    bombas = obtener_bombas_por_clasificacion(fecha=fecha, semana=semana, clasificacion=clasificacion, tecnico=tecnico)

    return jsonify({
        "success": True,
        "total": len(items),
        "items": [it.to_dict() for it in items],
        "resumen": resumen,
        "bombas": bombas
    })

@fertilizacion_bp.route('/api/drench/toggle', methods=['POST'])
@login_required
def api_drench_toggle():
    data = request.get_json() or {}
    item_id = data.get('id')
    aplicador = data.get('aplicador') or session.get('username', 'Sistema')
    tecnico = data.get('tecnico') or 'Técnico'
    nuevo_estado = data.get('completado')

    if not item_id:
        return jsonify({"success": False, "message": "ID requerido"}), 400

    item, msg = toggle_completado_drench(item_id, aplicador, tecnico, nuevo_estado)
    if not item:
        return jsonify({"success": False, "message": msg}), 404

    return jsonify({
        "success": True,
        "message": msg,
        "item": item.to_dict()
    })

# ==============================================================================
# API: RIEGO DUCHA (REGISTROS Y RENDIMIENTOS)
# ==============================================================================
@fertilizacion_bp.route('/api/riego-ducha', methods=['GET', 'POST'])
@login_required
def api_riego_ducha():
    if request.method == 'POST':
        data = request.get_json() or {}
        try:
            aplicador = data.get('aplicador', '').strip()
            tecnico = data.get('tecnico', '').strip()
            bloque = data.get('bloque', '').strip()
            pases = int(data.get('pases', 1))
            tipo_cobertura = data.get('tipo_cobertura', 'bloque_completo')
            camas_fisicas = int(data.get('camas_fisicas', 0))
            duracion_min = float(data.get('duracion_minutos', 0.0))

            if not aplicador or not tecnico or not bloque or camas_fisicas <= 0:
                return jsonify({"success": False, "message": "Datos incompletos"}), 400

            rendimiento = round(duracion_min / camas_fisicas, 2) if camas_fisicas > 0 else 0.0

            reg = RiegoDuchaRegistro(
                fecha=date.today(),
                semana=calcular_semana_formato(),
                aplicador=aplicador,
                tecnico=tecnico,
                bloque=bloque,
                pases=pases,
                tipo_cobertura=tipo_cobertura,
                cama_inicial=data.get('cama_inicial'),
                cama_final=data.get('cama_final'),
                camas_fisicas=camas_fisicas,
                duracion_minutos=duracion_min,
                rendimiento_min_cama=rendimiento,
                observaciones=data.get('observaciones', '')
            )
            db.session.add(reg)
            db.session.commit()
            return jsonify({"success": True, "registro": reg.to_dict()})
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "message": str(e)}), 500

    regs = RiegoDuchaRegistro.query.order_by(RiegoDuchaRegistro.id.desc()).limit(50).all()
    return jsonify({"success": True, "registros": [r.to_dict() for r in regs]})

@fertilizacion_bp.route('/api/riego-ducha/<int:reg_id>', methods=['DELETE'])
@login_required
def api_riego_ducha_delete(reg_id):
    reg = RiegoDuchaRegistro.query.get_or_404(reg_id)
    db.session.delete(reg)
    db.session.commit()
    return jsonify({"success": True, "message": "Registro eliminado"})

# ==============================================================================
# API: VÁLVULAS
# ==============================================================================
@fertilizacion_bp.route('/api/valvulas', methods=['GET', 'POST'])
@login_required
def api_valvulas():
    if request.method == 'POST':
        data = request.get_json() or {}
        try:
            aplicador = data.get('aplicador', '').strip()
            tecnico = data.get('tecnico', '').strip()
            variedad = data.get('variedad', '').strip()
            cantidad_valvulas = int(data.get('cantidad_valvulas', 0))
            minutos_riego = int(data.get('minutos_riego', 0))

            if not aplicador or not tecnico or not variedad or cantidad_valvulas <= 0:
                return jsonify({"success": False, "message": "Datos incompletos"}), 400

            reg = ValvulaRegistro(
                fecha=date.today(),
                semana=calcular_semana_formato(),
                aplicador=aplicador,
                tecnico=tecnico,
                variedad=variedad,
                cantidad_valvulas=cantidad_valvulas,
                minutos_riego=minutos_riego,
                observaciones=data.get('observaciones', '')
            )
            db.session.add(reg)
            db.session.commit()
            return jsonify({"success": True, "registro": reg.to_dict()})
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "message": str(e)}), 500

    regs = ValvulaRegistro.query.order_by(ValvulaRegistro.id.desc()).limit(50).all()
    return jsonify({"success": True, "registros": [r.to_dict() for r in regs]})

@fertilizacion_bp.route('/api/valvulas/<int:reg_id>', methods=['DELETE'])
@login_required
def api_valvulas_delete(reg_id):
    reg = ValvulaRegistro.query.get_or_404(reg_id)
    db.session.delete(reg)
    db.session.commit()
    return jsonify({"success": True, "message": "Registro eliminado"})

# ==============================================================================
# API: REPORTES DE RENDIMIENTO (POWERBI STYLE)
# ==============================================================================
@fertilizacion_bp.route('/api/reportes/datos', methods=['GET'])
@login_required
def api_reportes_datos():
    fecha_str = request.args.get('fecha')
    tecnico = request.args.get('tecnico')
    semana = request.args.get('semana')

    fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date() if fecha_str and fecha_str != "TODAS" else None
    datos = generar_datos_reporte_powerbi(fecha=fecha, tecnico=tecnico, semana=semana)
    return jsonify({"success": True, "data": datos})

# ==============================================================================
# API: CONFIGURACIÓN (TÉCNICOS, VARIEDADES, BLOQUES)
# ==============================================================================
@fertilizacion_bp.route('/api/config/tecnicos', methods=['GET', 'POST'])
@login_required
def api_config_tecnicos():
    if request.method == 'POST':
        data = request.get_json() or {}
        nombre = data.get('nombre', '').strip()
        if not nombre:
            return jsonify({"success": False, "message": "Nombre obligatorio"}), 400
        existente = Tecnico.query.filter(Tecnico.nombre.ilike(nombre)).first()
        if existente:
            return jsonify({"success": False, "message": "Técnico ya existe"}), 400
        tec = Tecnico(nombre=nombre, activo=True)
        db.session.add(tec)
        db.session.commit()
        return jsonify({"success": True, "tecnico": tec.to_dict()})

    tecs = Tecnico.query.order_by(Tecnico.nombre).all()
    return jsonify({"success": True, "tecnicos": [t.to_dict() for t in tecs]})

@fertilizacion_bp.route('/api/config/tecnicos/<int:tec_id>/toggle', methods=['POST'])
@login_required
def api_config_tecnico_toggle(tec_id):
    tec = Tecnico.query.get_or_404(tec_id)
    tec.activo = not tec.activo
    db.session.commit()
    return jsonify({"success": True, "tecnico": tec.to_dict()})

@fertilizacion_bp.route('/api/config/tecnicos/<int:tec_id>', methods=['DELETE'])
@login_required
def api_config_tecnico_delete(tec_id):
    tec = Tecnico.query.get_or_404(tec_id)
    db.session.delete(tec)
    db.session.commit()
    return jsonify({"success": True, "message": "Técnico eliminado"})

@fertilizacion_bp.route('/api/config/variedades', methods=['GET', 'POST'])
@login_required
def api_config_variedades():
    if request.method == 'POST':
        data = request.get_json() or {}
        nombre = data.get('nombre', '').strip()
        if not nombre:
            return jsonify({"success": False, "message": "Nombre obligatorio"}), 400
        existente = VariedadFerti.query.filter(VariedadFerti.nombre.ilike(nombre)).first()
        if existente:
            return jsonify({"success": False, "message": "Variedad ya existe"}), 400
        var = VariedadFerti(nombre=nombre, activo=True)
        db.session.add(var)
        db.session.commit()
        return jsonify({"success": True, "variedad": var.to_dict()})

    vars = VariedadFerti.query.order_by(VariedadFerti.nombre).all()
    return jsonify({"success": True, "variedades": [v.to_dict() for v in vars]})

@fertilizacion_bp.route('/api/config/variedades/<int:var_id>', methods=['DELETE'])
@login_required
def api_config_variedad_delete(var_id):
    var = VariedadFerti.query.get_or_404(var_id)
    db.session.delete(var)
    db.session.commit()
    return jsonify({"success": True, "message": "Variedad eliminada"})
