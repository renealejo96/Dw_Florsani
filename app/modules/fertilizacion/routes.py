import os
import io
import csv
from datetime import date, datetime
import pandas as pd
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, session
from werkzeug.utils import secure_filename
from app.extensions import db
from app.shared.audit import record_audit
from app.shared.utils import normalizar_producto_maestro
from app.shared.models import Product
from collections import defaultdict
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
# VISTA PRINCIPAL: PROGRAMAS TRICHOS Y SÓLIDOS (MESA DE CALIBRACIÓN & PLANNING)
# ==============================================================================
@fertilizacion_bp.route('/')
@fertilizacion_bp.route('/programas')
@login_required
@permission_required('fertilizacion')
def index():
    hoy = date.today()
    semana_calendario = calcular_semana_formato(hoy)

    semanas_db = [
        r[0] for r in db.session.query(DrenchItem.semana)
        .filter(DrenchItem.semana.isnot(None))
        .distinct()
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

    # Detectar modo actual de Moshe (Verano vs Invierno) para la semana
    moshe_items_week = [it for it in drench_items if 'MOSHE' in (it.clasificacion or '').upper() or 'MOSHE' in (it.bomba or '').upper()]
    tiene_invierno = any('MOSHE-AD' in (it.bomba or '').upper() or (it.edad or 0) > 7 for it in moshe_items_week)
    modo_moshe_actual = 'INVIERNO' if tiene_invierno else 'VERANO'

    # Mesa de Calibración: Procesar grupos por Edad, por Técnico y por Bomba
    calibracion_ordenada, calibracion_tecnicos, calibracion_bombas = procesar_calibracion_fertilizacion(drench_items)

    aplicadores = UsuarioAplicador.query.filter_by(activo=True).order_by(UsuarioAplicador.nombre).all()

    # Catálogo de bloques con Zona y Técnico de Riego para auto-completado en modales
    from sqlalchemy import text
    import json, re
    catalogo_bloques = {}
    catalogo_bloques_list = []
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
            candidate = b or cod
            if candidate and candidate not in catalogo_bloques_list:
                catalogo_bloques_list.append(candidate)
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
        calibracion_ordenada=calibracion_ordenada,
        calibracion_tecnicos=calibracion_tecnicos,
        calibracion_bombas=calibracion_bombas,
        resumen_clasif=resumen_clasif,
        bloques_drench=bloques_drench,
        aplicadores=aplicadores,
        modo_moshe_actual=modo_moshe_actual,
        catalogo_bloques_json=json.dumps(catalogo_bloques),
        catalogo_bloques_list=catalogo_bloques_list
    )


# ==============================================================================
# VISTA SEPARADA: CHECK LIST & APLICACIONES (PERSONAL DE CAMPO / OPERATIVO)
# ==============================================================================
@fertilizacion_bp.route('/checklist')
@login_required
@permission_required('fertilizacion')
def checklist():
    hoy = date.today()
    semana_calendario = calcular_semana_formato(hoy)

    semanas_db = [
        r[0] for r in db.session.query(DrenchItem.semana)
        .filter(DrenchItem.semana.isnot(None))
        .distinct()
        .order_by(DrenchItem.semana.desc())
        .all()
    ]
    semana_solicitada = request.args.get('semana', '').strip()
    semana_activa = semana_solicitada if semana_solicitada else (semanas_db[0] if semanas_db else semana_calendario)

    semanas_disponibles = list(semanas_db)
    if semana_calendario not in semanas_disponibles:
        semanas_disponibles.insert(0, semana_calendario)

    drench_items = obtener_items_drench(semana=semana_activa)

    # Separar Trichos (biológicos/enraizantes) vs Sólidos (Moshe, Alcaplant, Sulfato)
    trichos_items = [
        item for item in drench_items 
        if not any(k in (item.bomba or '').upper() or k in (item.clasificacion or '').upper() for k in ['MOSHE', 'ALCAPLANT', 'SOLID', 'SÓLID', 'SULFATO'])
    ]
    solidos_items = [
        item for item in drench_items 
        if any(k in (item.bomba or '').upper() or k in (item.clasificacion or '').upper() for k in ['MOSHE', 'ALCAPLANT', 'SOLID', 'SÓLID', 'SULFATO'])
    ]

    tecnicos = Tecnico.query.filter_by(activo=True).order_by(Tecnico.nombre).all()
    variedades = VariedadFerti.query.filter_by(activo=True).order_by(VariedadFerti.nombre).all()
    aplicadores = UsuarioAplicador.query.filter_by(activo=True).order_by(UsuarioAplicador.nombre).all()

    riego_ducha_items = RiegoDuchaRegistro.query.filter_by(fecha=hoy).order_by(RiegoDuchaRegistro.id.desc()).all()
    valvula_items = ValvulaRegistro.query.filter_by(fecha=hoy).order_by(ValvulaRegistro.id.desc()).all()

    return render_template(
        'fertilizacion/checklist.html',
        hoy=hoy.strftime("%Y-%m-%d"),
        semana_activa=semana_activa,
        semanas_disponibles=semanas_disponibles,
        trichos_items=trichos_items,
        solidos_items=solidos_items,
        riego_ducha_items=riego_ducha_items,
        valvula_items=valvula_items,
        tecnicos=tecnicos,
        variedades=variedades,
        aplicadores=aplicadores
    )


def get_bomba_rules_fertilizacion(apartado='TODOS', modo_moshe='INVIERNO'):
    """
    Retorna el diccionario de reglas agronómicas:
    (edad, cultivo_norm, actividad_norm, apartado) -> list of (bomba, clasificacion)
    """
    rules = {}

    def add_rule(edad, cultivo, actividad, bomba, clasificacion, apar):
        key = (int(edad), str(cultivo).upper().strip(), str(actividad).upper().strip(), str(apar).upper().strip())
        if key not in rules:
            rules[key] = []
        if (bomba, clasificacion) not in rules[key]:
            rules[key].append((bomba, clasificacion))

    # =========================================================================
    # 1. TRICHOS, BIOLÓGICOS, ENRAIZANTES, DRENCH
    # =========================================================================
    # EDAD 1:
    # - Sulfato de calcio, sulfato de magnesio agricola y sulfato de potasio suelos solo a las podas de solidago de edad 1
    add_rule(1, 'SOLIDAGO', 'PODA', 'SULFATO DE CALCIO, SULFATO DE MAGNESIO AGRICOLA, SULFATO DE POTASIO SUELOS', 'SOLIDOS', 'TRICHOS')
    
    # - Turbo root humistar trichoeb glioceb a siembras y podas de semana 1 solo gypso (igual a sem 5)
    add_rule(1, 'GYPSOPHILA', 'SIEMBRA', 'TURBO ROOT, HUMISTAR, TRICHOEB, GLIOCEB', 'DRENCH', 'TRICHOS')
    add_rule(1, 'GYPSOPHILA', 'PODA', 'TURBO ROOT, HUMISTAR, TRICHOEB, GLIOCEB', 'DRENCH', 'TRICHOS')
    
    # - Turbo root a veronica, solidago e hypericum sem 1 siembras y podas
    for c in ['VERONICA', 'SOLIDAGO', 'HYPERICUM']:
        add_rule(1, c, 'SIEMBRA', 'TURBO ROOT', 'ENRAIZANTE', 'TRICHOS')
        add_rule(1, c, 'PODA', 'TURBO ROOT', 'ENRAIZANTE', 'TRICHOS')
    
    # - Solidago sem 1 siembras y podas raizal, ktionic y turbo root
    add_rule(1, 'SOLIDAGO', 'SIEMBRA', 'RAIZAL, K-TIONIC, TURBO ROOT', 'ENRAIZANTE', 'TRICHOS')
    add_rule(1, 'SOLIDAGO', 'PODA', 'RAIZAL, K-TIONIC, TURBO ROOT', 'ENRAIZANTE', 'TRICHOS')

    # EDAD 2:
    for act in ['SIEMBRA', 'PODA']:
        add_rule(2, 'HYPERICUM', act, 'RAIZAL, TRICHOEB, K-TIONIC, GLIOCEB', 'TRICHOS', 'TRICHOS')
        add_rule(2, 'VERONICA', act, 'PILATUS, RAIZAL, TURBO ROOT, K-TIONIC', 'ENRAIZANTE', 'TRICHOS')
        add_rule(2, 'GYPSOPHILA', act, 'PILATUS, RAIZAL, TURBO ROOT, K-TIONIC', 'ENRAIZANTE', 'TRICHOS')
        add_rule(2, 'SOLIDAGO', act, 'TRADECORP', 'DRENCH HIERRO', 'TRICHOS')

    # EDAD 3:
    for act in ['SIEMBRA', 'PODA']:
        add_rule(3, 'GYPSOPHILA', act, 'NEMATEB FULMINANTE, PILATUS, TURBO ROOT, ARTHROEB', 'DRENCH', 'TRICHOS')
        add_rule(3, 'HYPERICUM', act, 'RAIZAL, TRICHOEB, K-TIONIC, GLIOCEB', 'TRICHOS', 'TRICHOS')
        add_rule(3, 'SOLIDAGO', act, 'TRADECORP HIERRO, NITRATO DE CALCIO IMP', 'DRENCH HIERRO', 'TRICHOS')
        add_rule(3, 'VERONICA', act, 'PILATUS, RAIZAL, TURBO ROOT, K-TIONIC', 'ENRAIZANTE', 'TRICHOS')

    # EDAD 4:
    for act in ['SIEMBRA', 'PODA']:
        add_rule(4, 'HYPERICUM', act, 'TRICHOEB, ARTHROEB, K-TIONIC', 'TRICHOS', 'TRICHOS')
        add_rule(4, 'GYPSOPHILA', act, 'TRICHOEB, ARTHROEB, K-TIONIC', 'TRICHOS', 'TRICHOS')
        add_rule(4, 'VERONICA', act, 'PILATUS, RAIZAL, TURBO ROOT, K-TIONIC', 'ENRAIZANTE', 'TRICHOS')
        add_rule(4, 'SOLIDAGO', act, 'TRADECORP HIERRO, NITRATO DE CALCIO IMP', 'DRENCH HIERRO', 'TRICHOS')

    # EDAD 5:
    for act in ['SIEMBRA', 'PODA']:
        add_rule(5, 'GYPSOPHILA', act, 'TURBO ROOT, HUMISTAR, TRICHOEB, GLIOCEB', 'DRENCH', 'TRICHOS')
        add_rule(5, 'VERONICA', act, 'TRICHOEB, ARTHROEB, K-TIONIC', 'TRICHOS', 'TRICHOS')
        add_rule(5, 'HYPERICUM', act, 'TRADECORP HIERRO', 'DRENCH HIERRO', 'TRICHOS')
        add_rule(5, 'SOLIDAGO', act, 'TRADECORP HIERRO', 'DRENCH HIERRO', 'TRICHOS')

    # EDAD 6:
    for act in ['SIEMBRA', 'PODA']:
        add_rule(6, 'GYPSOPHILA', act, 'GLIOCEB, TRICHOEB, FULMINANTE', 'BIOLOGICOS', 'TRICHOS')
        add_rule(6, 'HYPERICUM', act, 'GLIOCEB, TRICHOEB, FULMINANTE', 'BIOLOGICOS', 'TRICHOS')
        add_rule(6, 'VERONICA', act, 'TRADECORP HIERRO', 'DRENCH HIERRO', 'TRICHOS')
        add_rule(6, 'SOLIDAGO', act, 'SULFATO MAGNESIO TECNICO F', 'SOLIDOS', 'TRICHOS')

    # EDAD 7:
    for act in ['SIEMBRA', 'PODA']:
        add_rule(7, 'GYPSOPHILA', act, 'GLIOCEB, NEMATEB, PILATUS', 'TRICHOS', 'TRICHOS')
        add_rule(7, 'HYPERICUM', act, 'GLIOCEB, NEMATEB, PILATUS', 'TRICHOS', 'TRICHOS')
        add_rule(7, 'VERONICA', act, 'GLIOCEB, NEMATEB, PILATUS', 'TRICHOS', 'TRICHOS')
        add_rule(7, 'SOLIDAGO', act, 'TRADECORP HIERRO', 'DRENCH HIERRO', 'TRICHOS')

    # EDAD 8:
    for act in ['SIEMBRA', 'PODA']:
        add_rule(8, 'GYPSOPHILA', act, 'TRICHOEB, ARTHROEB, K-TIONIC', 'TRICHOS', 'TRICHOS')
        add_rule(8, 'HYPERICUM', act, 'TRICHOEB, ARTHROEB, K-TIONIC', 'TRICHOS', 'TRICHOS')
        add_rule(8, 'VERONICA', act, 'TRICHOEB, ARTHROEB, K-TIONIC', 'TRICHOS', 'TRICHOS')
        add_rule(8, 'SOLIDAGO', act, 'TRADECORP HIERRO, NITRATO DE CALCIO IMP', 'DRENCH HIERRO', 'TRICHOS')

    # EDAD 9:
    for act in ['SIEMBRA', 'PODA']:
        add_rule(9, 'GYPSOPHILA', act, 'PILATUS, RAIZAL, TURBO ROOT, K-TIONIC', 'ENRAIZANTE', 'TRICHOS')
        add_rule(9, 'HYPERICUM', act, 'PILATUS, RAIZAL, TURBO ROOT, K-TIONIC', 'ENRAIZANTE', 'TRICHOS')
        add_rule(9, 'VERONICA', act, 'PILATUS, RAIZAL, TURBO ROOT, K-TIONIC', 'ENRAIZANTE', 'TRICHOS')
        add_rule(9, 'SOLIDAGO', act, 'TRADECORP HIERRO, NITRATO DE CALCIO IMP', 'DRENCH HIERRO', 'TRICHOS')

    # EDAD 10:
    for act in ['SIEMBRA', 'PODA']:
        add_rule(10, 'GYPSOPHILA', act, 'GLIOCEB, TRICHOEB, FULMINANTE', 'BIOLOGICOS', 'TRICHOS')
        add_rule(10, 'HYPERICUM', act, 'GLIOCEB, TRICHOEB, FULMINANTE', 'BIOLOGICOS', 'TRICHOS')
        add_rule(10, 'VERONICA', act, 'GLIOCEB, TRICHOEB, FULMINANTE', 'BIOLOGICOS', 'TRICHOS')
        add_rule(10, 'SOLIDAGO', act, 'TRADECORP HIERRO', 'DRENCH HIERRO', 'TRICHOS')

    # EDAD 11:
    for act in ['SIEMBRA', 'PODA']:
        add_rule(11, 'GYPSOPHILA', act, 'GLIOCEB, NEMATEB, PILATUS', 'TRICHOS', 'TRICHOS')
        add_rule(11, 'HYPERICUM', act, 'GLIOCEB, NEMATEB, PILATUS', 'TRICHOS', 'TRICHOS')
        add_rule(11, 'VERONICA', act, 'GLIOCEB, NEMATEB, PILATUS', 'TRICHOS', 'TRICHOS')
        add_rule(11, 'SOLIDAGO', act, 'TRADECORP HIERRO', 'DRENCH HIERRO', 'TRICHOS')

    # EDAD 12:
    for act in ['SIEMBRA', 'PODA']:
        for c in ['GYPSOPHILA', 'HYPERICUM', 'VERONICA', 'SOLIDAGO']:
            add_rule(12, c, act, 'SULFATO MAGNESIO TECNICO F', 'SOLIDOS', 'TRICHOS')

    # =========================================================================
    # 2. MOSHE (VERANO vs INVIERNO)
    # =========================================================================
    # VERONICA: Siembra [2, 4], Poda [1, 3]
    for ed in [2, 4]:
        add_rule(ed, 'VERONICA', 'SIEMBRA', 'MOSHE', 'MOSHE', 'MOSHE')
    for ed in [1, 3]:
        add_rule(ed, 'VERONICA', 'PODA', 'MOSHE', 'MOSHE', 'MOSHE')

    # HYPERICUM: Siembra [2, 4], Poda [2, 4]
    for ed in [2, 4]:
        add_rule(ed, 'HYPERICUM', 'SIEMBRA', 'MOSHE', 'MOSHE', 'MOSHE')
        add_rule(ed, 'HYPERICUM', 'PODA', 'MOSHE', 'MOSHE', 'MOSHE')

    # SOLIDAGO: Siembra [2, 4], Poda [2, 4]
    for ed in [2, 4]:
        add_rule(ed, 'SOLIDAGO', 'SIEMBRA', 'MOSHE', 'MOSHE', 'MOSHE')
        add_rule(ed, 'SOLIDAGO', 'PODA', 'MOSHE', 'MOSHE', 'MOSHE')

    # BILLION LIGHTS / GYPSOPHILA: Siembra [2, 4, 5, 6, 7], Poda [2, 4, 5, 6, 7]
    for ed in [2, 4, 5, 6, 7]:
        add_rule(ed, 'GYPSOPHILA', 'SIEMBRA', 'MOSHE', 'MOSHE', 'MOSHE')
        add_rule(ed, 'GYPSOPHILA', 'PODA', 'MOSHE', 'MOSHE', 'MOSHE')

    # Reglas Invierno Adicionales (MOSHE-AD):
    if modo_moshe.upper() == 'INVIERNO':
        for ed in [5, 6, 7, 8, 9, 10, 11, 12, 13, 14]:
            add_rule(ed, 'VERONICA', 'SIEMBRA', 'MOSHE-AD', 'MOSHE', 'MOSHE')
        for ed in [4, 5, 6, 7, 8, 9, 10]:
            add_rule(ed, 'VERONICA', 'PODA', 'MOSHE-AD', 'MOSHE', 'MOSHE')

        for ed in [5, 6, 7, 8, 9, 10, 11, 12]:
            add_rule(ed, 'SOLIDAGO', 'SIEMBRA', 'MOSHE-AD', 'MOSHE', 'MOSHE')
        for ed in [5, 6, 7, 8, 9, 10, 11]:
            add_rule(ed, 'SOLIDAGO', 'PODA', 'MOSHE-AD', 'MOSHE', 'MOSHE')

        for ed in [8, 9, 10, 11, 12]:
            add_rule(ed, 'GYPSOPHILA', 'SIEMBRA', 'MOSHE-AD', 'MOSHE', 'MOSHE')
        for ed in [8, 9, 10, 11]:
            add_rule(ed, 'GYPSOPHILA', 'PODA', 'MOSHE-AD', 'MOSHE', 'MOSHE')

    # =========================================================================
    # 3. ALCAPLANT
    # =========================================================================
    for ed in [2, 4, 6, 8, 10]:
        add_rule(ed, 'VERONICA', 'SIEMBRA', 'ALCAPLANT', 'ALCAPLANT', 'ALCAPLANT')
        add_rule(ed, 'VERONICA', 'PODA', 'ALCAPLANT', 'ALCAPLANT', 'ALCAPLANT')

    for ed in [1, 3, 5]:
        add_rule(ed, 'HYPERICUM', 'SIEMBRA', 'ALCAPLANT', 'ALCAPLANT', 'ALCAPLANT')
        add_rule(ed, 'HYPERICUM', 'PODA', 'ALCAPLANT', 'ALCAPLANT', 'ALCAPLANT')

    add_rule(2, 'GYPSOPHILA', 'SIEMBRA', 'ALCAPLANT', 'ALCAPLANT', 'ALCAPLANT')
    add_rule(2, 'GYPSOPHILA', 'PODA', 'ALCAPLANT', 'ALCAPLANT', 'ALCAPLANT')

    # =========================================================================
    # 4. PRESIEMBRAS (EDAD 0 / PLANNING)
    # =========================================================================
    for c in ['VERONICA', 'SOLIDAGO', 'GYPSOPHILA']:
        add_rule(0, c, 'SIEMBRA', 'ABONO TRIPLE 15, MURIATO DE POTASIO, SULFATO DE CALCIO', 'PRESIEMBRA', 'PRESIEMBRAS')
        add_rule(0, c, 'PODA', 'ABONO TRIPLE 15, MURIATO DE POTASIO, SULFATO DE CALCIO', 'PRESIEMBRA', 'PRESIEMBRAS')

    add_rule(0, 'HYPERICUM', 'SIEMBRA', 'ABONO TRIPLE 15, MURIATO DE POTASIO', 'PRESIEMBRA', 'PRESIEMBRAS')
    add_rule(0, 'HYPERICUM', 'PODA', 'ABONO TRIPLE 15, MURIATO DE POTASIO', 'PRESIEMBRA', 'PRESIEMBRAS')

    return rules


def _parse_semana_destino(semana_str):
    raw = str(semana_str or '').strip().replace('-', '').replace('_', '')
    now = datetime.now()
    cur_year = now.year

    if len(raw) == 2 and raw.isdigit():
        return cur_year, int(raw), f"{str(cur_year)[-2:]}{int(raw):02d}"
    elif len(raw) == 4 and raw.isdigit():
        y = 2000 + int(raw[:2])
        w = int(raw[2:])
        return y, w, raw
    elif len(raw) == 6 and raw.isdigit():
        y = int(raw[:4])
        w = int(raw[4:])
        return y, w, f"{raw[2:]}"
    else:
        m = re.search(r'(\d{1,2})', raw)
        w = int(m.group(1)) if m else now.isocalendar()[1]
        return cur_year, w, f"{str(cur_year)[-2:]}{w:02d}"


def proyectar_semana_fertilizacion(semana_destino, semana_origen=None, apartado='TODOS', modo_moshe='INVIERNO'):
    """
    Genera automáticamente el programa de fertilización, trichos, moshe, alcaplant y presiembras para la semana
    destino a partir del último estado de cultivo (mrt_estado_cultivo_actual),
    proyectando las siembras y podas vivas a la edad que tendrán en la semana objetivo.
    Aplica litraje fijo oficial (41.7 L/cama Trichos/Biológicos, 30.0 L/cama Sólidos/Moshe/Alcaplant).
    """
    from sqlalchemy import text
    import re

    target_year, target_week, semana_dest_clean = _parse_semana_destino(semana_destino)
    rules = get_bomba_rules_fertilizacion(apartado=apartado, modo_moshe=modo_moshe)

    # 1. Cargar catálogo de bloques para técnicos y zonas
    dim_map = {}
    try:
        rows_b = db.session.execute(text("SELECT codigo_bloque, blq, zona, tecnico_de_riego FROM dim_bloques")).fetchall()
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
                    dim_map[k] = inf
    except Exception as e_dim:
        print(f"[WARN] Error cargando dim_bloques en proyeccion: {e_dim}")

    def resolver_tecnico(blq):
        clean_b = str(blq or '').strip().upper()
        info = dim_map.get(clean_b) or dim_map.get(clean_b.replace('BL', '').strip()) or dim_map.get(clean_b.replace(' ', ''))
        return info['tecnico'] if info and info.get('tecnico') else 'Técnico 1 (C1)'

    # 2. Cargar camas vivas desde mrt_estado_cultivo_actual
    rows = db.session.execute(text("""
        SELECT 
            COALESCE(blq, codigo_bloque) AS blq,
            COALESCE(sufijo, 'A') AS sufijo,
            num_cama,
            producto_actual,
            variedad_actual,
            ultimo_evento,
            semana_campo_ultimo_evento,
            fecha_ultimo_evento,
            COALESCE(cama_estandar, 1.0) as cama_estandar,
            zona
        FROM mrt_estado_cultivo_actual
        WHERE num_cama IS NOT NULL
          AND ultimo_evento IN ('SIEMBRA', 'PODA')
          AND producto_actual NOT IN ('VACIO', 'ERRADICADO', 'TOTAL', 'NAN', '')
        ORDER BY blq, num_cama;
    """)).fetchall()

    if not rows:
        return 0

    bed_objects = []
    crop_alias = {
        'BILLION LIGHTS': 'GYPSOPHILA',
        'G49': 'GYPSOPHILA',
        'G5': 'GYPSOPHILA',
        'SMALL BLOOM': 'GYPSOPHILA',
        'GYPSOPHILA': 'GYPSOPHILA',
        'HYPERICUM': 'HYPERICUM',
        'VERONICA': 'VERONICA',
        'SOLIDAGO': 'SOLIDAGO',
        'LIMONIUM': 'LIMONIUM'
    }

    max_ev_week = 0
    parsed_rows = []
    for r in rows:
        sc = str(r.semana_campo_ultimo_evento or '').strip()
        ev_year = target_year
        ev_week = target_week - 2  # default razonable
        if len(sc) == 4 and sc.isdigit():
            ev_year = 2000 + int(sc[:2])
            ev_week = int(sc[2:])
        elif sc.isdigit():
            ev_week = int(sc)
        elif r.fecha_ultimo_evento:
            dt = r.fecha_ultimo_evento
            ev_year, ev_week = dt.isocalendar()[0], dt.isocalendar()[1]

        if ev_year == target_year and ev_week > max_ev_week:
            max_ev_week = ev_week

        parsed_rows.append((r, ev_year, ev_week))

    # Guardar camas de la cohorte más reciente para proyección de gap weeks (planning / supuestos de semanas 0 y 1)
    latest_cohort_by_crop = defaultdict(list)
    for r, ev_year, ev_week in parsed_rows:
        c_prod = str(r.producto_actual or '').strip().upper()
        c_var = str(r.variedad_actual or '').strip().upper()
        crop_norm = crop_alias.get(c_prod, crop_alias.get(c_var, c_prod))
        act_norm = 'PODA' if 'PODA' in str(r.ultimo_evento or '').upper() else 'SIEMBRA'

        if max_ev_week > 0 and ev_week >= max(1, max_ev_week - 1):
            latest_cohort_by_crop[(crop_norm, act_norm)].append({
                'bloque': str(r.blq).strip(),
                'num_cama': int(r.num_cama),
                'cama_estandar': float(r.cama_estandar or 1.0),
                'cultivo': crop_norm,
                'variedad': str(r.variedad_actual or crop_norm).strip(),
                'actividad': act_norm,
                'zona': str(r.zona or 'ZONA GENERAL').strip()
            })

        edad = (target_year - ev_year) * 52 + (target_week - ev_week)
        # Edades 0 y 1 corresponden a presiembras y planning manual; no se proyectan desde censos anteriores
        if edad <= 1 or edad > 15:
            continue

        bed_objects.append({
            'bloque': str(r.blq).strip(),
            'num_cama': int(r.num_cama),
            'cama_estandar': float(r.cama_estandar or 1.0),
            'cultivo': crop_norm,
            'variedad': str(r.variedad_actual or crop_norm).strip(),
            'actividad': act_norm,
            'edad': edad,
            'zona': str(r.zona or 'ZONA GENERAL').strip()
        })

    if not bed_objects:
        return 0

    # 3. Mapear reglas por apartado
    target_aparts = ['TRICHOS', 'MOSHE', 'ALCAPLANT', 'PRESIEMBRAS'] if apartado.upper() == 'TODOS' else [apartado.upper()]

    assigned_beds = []
    for b in bed_objects:
        for apar in target_aparts:
            key = (b['edad'], b['cultivo'], b['actividad'], apar)
            if key in rules:
                for b_name, b_clasif in rules[key]:
                    assigned_beds.append({
                        **b,
                        'bomba': b_name,
                        'clasificacion': b_clasif,
                        'apartado': apar
                    })

    if not assigned_beds:
        return 0

    # 4. Consolidar camas contiguas por (bloque, cultivo, actividad, edad, bomba, clasificacion)
    by_group = defaultdict(list)
    for b in assigned_beds:
        by_group[(b['bloque'], b['cultivo'], b['actividad'], b['edad'], b['bomba'], b['clasificacion'])].append(b)

    segments = []
    for (blq, crop, act, age, b_name, b_clasif), beds in by_group.items():
        beds.sort(key=lambda x: x['num_cama'])
        cur_seg = [beds[0]]
        for i in range(1, len(beds)):
            prev = beds[i-1]
            curr = beds[i]
            if curr['num_cama'] == prev['num_cama'] + 1:
                cur_seg.append(curr)
            else:
                c_ini = cur_seg[0]['num_cama']
                c_fin = cur_seg[-1]['num_cama']
                segments.append({
                    'bloque': blq,
                    'cultivo': crop,
                    'actividad': act,
                    'edad': age,
                    'bomba': b_name,
                    'clasificacion': b_clasif,
                    'cama_inicial': c_ini,
                    'cama_final': c_fin,
                    'cantidad_camas': len(cur_seg),
                    'cama_estandar': sum(x['cama_estandar'] for x in cur_seg),
                    'zona': cur_seg[0]['zona']
                })
                cur_seg = [curr]
        if cur_seg:
            c_ini = cur_seg[0]['num_cama']
            c_fin = cur_seg[-1]['num_cama']
            segments.append({
                'bloque': blq,
                'cultivo': crop,
                'actividad': act,
                'edad': age,
                'bomba': b_name,
                'clasificacion': b_clasif,
                'cama_inicial': c_ini,
                'cama_final': c_fin,
                'cantidad_camas': len(cur_seg),
                'cama_estandar': sum(x['cama_estandar'] for x in cur_seg),
                'zona': cur_seg[0]['zona']
            })

    # 5. Guardar en base de datos: eliminar registros previos para esta semana y apartados
    w_clean = semana_dest_clean
    del_query = DrenchItem.query.filter(
        db.or_(
            DrenchItem.semana == semana_destino,
            DrenchItem.semana == w_clean,
            DrenchItem.semana == f"20{w_clean}" if len(w_clean) == 4 else False
        )
    )
    if apartado.upper() != 'TODOS':
        # Filtrar solo la clasificacion correspondiente
        clasif_map = {
            'MOSHE': ['MOSHE', 'MOSHE-AD'],
            'ALCAPLANT': ['ALCAPLANT'],
            'PRESIEMBRAS': ['PRESIEMBRA'],
            'TRICHOS': ['TRICHOS', 'BIOLOGICOS', 'ENRAIZANTE', 'DRENCH', 'DRENCH HIERRO', 'SOLIDOS']
        }
        if apartado.upper() in clasif_map:
            del_query = del_query.filter(DrenchItem.clasificacion.in_(clasif_map[apartado.upper()]))

    del_query.delete(synchronize_session=False)

    created_items = []
    for s in segments:
        tec_name = resolver_tecnico(s['bloque'])
        item = DrenchItem(
            semana=w_clean,
            bloque=s['bloque'],
            cultivo=s['cultivo'],
            actividad=s['actividad'],
            edad=s['edad'],
            bomba=s['bomba'],
            clasificacion=s['clasificacion'],
            cama_inicial=s['cama_inicial'],
            cama_final=s['cama_final'],
            cantidad_camas=s['cantidad_camas'],
            cama_estandar=round(s['cama_estandar'], 2),
            tecnico=tec_name,
            grupo_id=1,
            segmento_edades=f"Semana {s['edad']}",
            completado=False
        )
        created_items.append(item)

    db.session.bulk_save_objects(created_items)
    db.session.commit()
    print(f"[OK] Proyección completada para semana {w_clean} ({apartado}): {len(created_items)} drench_items creados.")
    return len(created_items)


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
        clasif_upper = (item.clasificacion or '').upper()
        b_upper = b_name.upper()

        es_presiembra = ('PRESIEMBRA' in b_upper or 'PRESIEMBRA' in clasif_upper or 'PRESIEMBRAS' in clasif_upper)
        es_sol = any(k in b_upper or k in clasif_upper for k in ['MOSHE', 'ALCAPLANT', 'SULFATO', 'POTASIO', 'MAGNESIO', 'CALCIO', 'SOLID', 'SÓLID'])

        if es_presiembra:
            vol_default = 0.0
        elif es_sol:
            vol_default = 30.0
        else:
            vol_default = 41.7

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
                'tipo_grupo': 'PRESIEMBRAS' if es_presiembra else ('SOLIDOS' if es_sol else 'TRICHOS'),
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
        es_moshe_item = ('MOSHE' in clasif_upper or 'MOSHE' in b_upper)
        es_alcaplant_item = ('ALCAPLANT' in clasif_upper or 'ALCAPLANT' in b_upper)

        if es_moshe_item:
            b_key = ('MOSHE', 0)
            b_card_name = 'MOSHE'
            desc_label = 'MOSHE (Nutrición y Fondo)'
            t_key = 'MOSHE'
            apar_key = 'MOSHE'
        elif es_alcaplant_item:
            b_key = ('ALCAPLANT', 0)
            b_card_name = 'ALCAPLANT'
            desc_label = 'ALCAPLANT (Encalado y Calcio)'
            t_key = 'ALCAPLANT'
            apar_key = 'ALCAPLANT'
        else:
            b_key = (b_name.strip().upper(), ed)
            b_card_name = b_name
            if es_presiembra:
                desc_label = 'PRESIEMBRAS (Planning y Fondo)'
                t_key = 'PRESIEMBRAS'
                apar_key = 'PRESIEMBRAS'
            elif any(p in b_upper for p in ['PILATUS', 'RAIZAL', 'TURBO ROOT', 'ENRAIZ']):
                desc_label = 'ENRAIZANTES'
                t_key = 'ENRAIZANTES'
                apar_key = 'TRICHOS'
            elif any(k in b_upper for k in ['SULFATO', 'POTASIO', 'MAGNESIO', 'CALCIO', 'SOLID', 'SÓLID']):
                desc_label = 'SÓLIDOS EDÁFICOS'
                t_key = 'SOLIDOS'
                apar_key = 'TRICHOS'
            elif 'TRICHO' in b_upper or 'GLIO' in b_upper or 'BIOLOG' in b_upper:
                desc_label = 'TRICHOS & BIOLÓGICOS'
                t_key = 'TRICHOS'
                apar_key = 'TRICHOS'
            else:
                desc_label = item.clasificacion or 'DRENCH'
                t_key = 'SOLIDOS' if es_sol else 'TRICHOS'
                apar_key = 'TRICHOS'

        if b_key not in calibracion_por_bomba:
            calibracion_por_bomba[b_key] = {
                'bomba': b_card_name,
                'variedades': set(),
                'cultivo': item.cultivo or 'GENERAL',
                'edad': ed,
                'edades': set(),
                'es_programa_completo': (es_moshe_item or es_alcaplant_item),
                'descripcion': desc_label,
                'tratamiento_key': t_key,
                'apartado': apar_key,
                'es_solidos': es_sol,
                'es_presiembra': es_presiembra,
                'vol_default': vol_default,
                'camas_totales': 0,
                'litros_totales': 0.0,
                'bloques': [],
                'tecnicos': set(),
                'items': [],
                'bloque_items': [],
                'productos_calculados': []
            }

        calibracion_por_bomba[b_key]['edades'].add(ed)
        calibracion_por_bomba[b_key]['variedades'].add(item.cultivo or 'GENERAL')

        c_camas_it = float(item.cantidad_camas or 0)
        lits_it = 0.0 if es_presiembra else round(c_camas_it * vol_default, 1)
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
            base_dose = None
            if p_match:
                if es_presiembra or t_key == 'PRESIEMBRAS':
                    base_dose = p_match.dose_presiembra or p_match.dose_solidos or p_match.dose_drench
                elif t_key == 'MOSHE':
                    base_dose = p_match.dose_moshe or p_match.dose_solidos or p_match.dose_drench
                elif t_key == 'ALCAPLANT':
                    base_dose = p_match.dose_alcaplant or p_match.dose_solidos or p_match.dose_drench
                elif es_sol:
                    base_dose = p_match.dose_solidos or p_match.dose_drench
                else:
                    base_dose = p_match.dose_trichos or p_match.dose_drench
            if base_dose is None:
                base_dose = 100.0 if es_presiembra else 0.05

            diff_dose = item.dosis_diferenciada or (p_match.dose_diferenciada if p_match else None)
            eff_dose = diff_dose if diff_dose is not None else base_dose
            if es_presiembra or t_key == 'PRESIEMBRAS':
                dose_str = f"{eff_dose} {unit}/cama"
                tot_prod = round(float(eff_dose) * float(c_camas_it), 2)
            else:
                dose_str = f"{eff_dose} {unit}/L" if unit in ['GR', 'CC', 'ML', 'KG', 'L'] else f"{eff_dose} {unit}"
                tot_prod = round(float(eff_dose) * float(lits_it), 1)

            b_item_prods.append({
                'name': p_match.commercial_name if p_match else p_name,
                'unit': unit,
                'dose': base_dose,
                'dosis_diferenciada': diff_dose,
                'dose_str': dose_str,
                'total_producto': tot_prod
            })

        c_inic_it = item.cama_inicial or 1
        c_fin_it = item.cama_final or (c_inic_it + int(c_camas_it) - 1 if c_camas_it > 0 else c_inic_it)
        dia_item = (getattr(item, 'dia_aplicacion', None) or 'LUNES').strip().upper()
        estado_item = (getattr(item, 'estado_calibracion', None) or 'SIN_TOCAR').strip().upper()
        if 'dias' not in calibracion_por_bomba[b_key]:
            calibracion_por_bomba[b_key]['dias'] = set()
        calibracion_por_bomba[b_key]['dias'].add(dia_item)
        if 'estados' not in calibracion_por_bomba[b_key]:
            calibracion_por_bomba[b_key]['estados'] = set()
        calibracion_por_bomba[b_key]['estados'].add(estado_item)

        item_detail_obj = {
            'id': item.id,
            'bloque': item.bloque,
            'zona': zona_name,
            'cultivo': item.cultivo or 'GENERAL',
            'variedad': item.cultivo or 'GENERAL',
            'edad': ed,
            'dia_aplicacion': dia_item,
            'estado_calibracion': estado_item,
            'cama_inicial': c_inic_it,
            'cama_final': c_fin_it,
            'cantidad_camas': int(c_camas_it),
            'cama_estandar': float(item.cama_estandar or 0.0),
            'litros_cama': '-' if es_presiembra else vol_default,
            'total_litros': 0.0 if es_presiembra else lits_it,
            'es_presiembra': es_presiembra,
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
        es_pre_edad = (grp['tipo_grupo'] == 'PRESIEMBRAS' or any('PRESIEMBRA' in (b or '').upper() for b in grp['bombas']))
        es_solidos = any(
            any(k in (b or '').upper() for k in ['MOSHE', 'ALCAPLANT', 'SULFATO', 'POTASIO', 'MAGNESIO', 'CALCIO', 'SOLID', 'SÓLID'])
            for b in grp['bombas']
        )
        if es_pre_edad:
            grp['tipo_grupo'] = 'PRESIEMBRAS'
            grp['vol_default'] = 0.0
            grp['litros_totales'] = 0.0
        elif es_solidos:
            grp['tipo_grupo'] = 'SOLIDOS'
            grp['vol_default'] = 30.0
            grp['litros_totales'] = round(grp['camas_totales'] * grp['vol_default'], 1)
        else:
            grp['tipo_grupo'] = 'TRICHOS'
            grp['vol_default'] = 41.7
            grp['litros_totales'] = round(grp['camas_totales'] * grp['vol_default'], 1)

        grp['bloques'] = sorted(list(grp['bloques']))
        grp['bombas'] = sorted(list(grp['bombas']))
        grp['tecnicos'] = sorted(list(grp['tecnicos']))

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
                    if es_pre_edad:
                        dose = p_match.dose_presiembra or p_match.dose_solidos or p_match.dose_drench
                    elif grp['tipo_grupo'] == 'SOLIDOS':
                        dose = p_match.dose_solidos or p_match.dose_moshe or p_match.dose_alcaplant or p_match.dose_drench
                    else:
                        dose = p_match.dose_trichos or p_match.dose_drench
                if dose is None:
                    dose = 100.0 if es_pre_edad else 0.05
                
                if es_pre_edad:
                    total_qty = round(float(dose) * float(grp['camas_totales']), 2)
                else:
                    total_qty = round(float(dose) * float(grp['vol_default']) * float(grp['camas_totales']))

                if comm_name not in prods_dict:
                    prods_dict[comm_name] = {
                        'name': comm_name,
                        'unit': unit,
                        'dose': dose,
                        'dose_str': f"{dose} {unit}/cama" if es_pre_edad else f"{dose} {unit}/L",
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
            it_is_pre = (es_pre_edad or 'PRESIEMBRA' in (it.bomba or '').upper() or 'PRESIEMBRA' in (it.clasificacion or '').upper())

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
                if it_is_pre:
                    dose = (p_match.dose_presiembra if p_match else None) or (p_match.dose_solidos if p_match else None) or (p_match.dose_drench if p_match else None)
                elif grp['tipo_grupo'] == 'SOLIDOS':
                    dose = (p_match.dose_solidos if p_match else None) or (p_match.dose_moshe if p_match else None) or (p_match.dose_alcaplant if p_match else None) or (p_match.dose_drench if p_match else None)
                else:
                    dose = (p_match.dose_trichos if p_match else None) or (p_match.dose_drench if p_match else None)
                if dose is None:
                    dose = 100.0 if it_is_pre else 0.05
                dose_str = f"{dose} {unit}/cama" if it_is_pre else (f"{dose} {unit}/L" if unit in ['GR', 'CC', 'ML', 'KG', 'L'] else f"{dose} {unit}")
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
                'litros_cama': '-' if it_is_pre else grp['vol_default'],
                'total_litros': 0.0 if it_is_pre else round(c_cam * grp['vol_default'], 1),
                'bomba': it.bomba,
                'tecnico': it_tec,
                'clasificacion': 'PRESIEMBRAS' if it_is_pre else grp['tipo_grupo'],
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
            is_pre = ('PRESIEMBRA' in (item.bomba or '').upper() or 'PRESIEMBRA' in (item.clasificacion or '').upper())
            es_sol = any(k in (item.bomba or '').upper() for k in ['MOSHE', 'ALCAPLANT', 'SULFATO', 'POTASIO', 'MAGNESIO', 'CALCIO', 'SOLID', 'SÓLID'])
            if is_pre:
                v_cama = 0.0
            elif es_sol:
                v_cama = 30.0
            else:
                v_cama = 41.7
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
                    if is_pre:
                        dose = p_match.dose_presiembra or p_match.dose_solidos or p_match.dose_drench
                    elif es_sol:
                        dose = p_match.dose_solidos or p_match.dose_moshe or p_match.dose_alcaplant or p_match.dose_drench
                    else:
                        dose = p_match.dose_trichos or p_match.dose_drench
                if dose is None:
                    dose = 100.0 if is_pre else 0.05

                if is_pre:
                    tot_p = round(float(dose) * c_camas, 2)
                    desc = f"Presiembra / Fondo Seco - Bloque {item.bloque}"
                else:
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

    # Asegurar que las bombas estándar de Semana 0 (Presiembras) y Semana 1 siempre existan
    # para permitir planificar e insertar bloques manuales aunque no haya eventos de campo registrados aún.
    bombas_fijas_requeridas = [
        # Semana 0 (Presiembras / Fondo Seco)
        {
            'bomba': 'ABONO TRIPLE 15, MURIATO DE POTASIO, SULFATO DE CALCIO',
            'edad': 0,
            'cultivo': 'GYPSOPHILA, SOLIDAGO, VERONICA',
            'variedades': {'GYPSOPHILA', 'SOLIDAGO', 'VERONICA'},
            'descripcion': 'PRESIEMBRAS (Planning y Fondo)',
            'tratamiento_key': 'PRESIEMBRAS',
            'apartado': 'PRESIEMBRAS',
            'es_solidos': False,
            'es_presiembra': True,
            'vol_default': 0.0
        },
        {
            'bomba': 'ABONO TRIPLE 15, MURIATO DE POTASIO',
            'edad': 0,
            'cultivo': 'HYPERICUM',
            'variedades': {'HYPERICUM'},
            'descripcion': 'PRESIEMBRAS (Planning y Fondo)',
            'tratamiento_key': 'PRESIEMBRAS',
            'apartado': 'PRESIEMBRAS',
            'es_solidos': False,
            'es_presiembra': True,
            'vol_default': 0.0
        },
        # Semana 1 (Trichos, Enraizantes, Sólidos, Moshe, Alcaplant)
        {
            'bomba': 'TURBO ROOT',
            'edad': 1,
            'cultivo': 'HYPERICUM, SOLIDAGO, VERONICA',
            'variedades': {'HYPERICUM', 'SOLIDAGO', 'VERONICA'},
            'descripcion': 'ENRAIZANTES',
            'tratamiento_key': 'ENRAIZANTES',
            'apartado': 'TRICHOS',
            'es_solidos': False,
            'es_presiembra': False,
            'vol_default': 41.7
        },
        {
            'bomba': 'TURBO ROOT, HUMISTAR, TRICHOEB, GLIOCEB',
            'edad': 1,
            'cultivo': 'GYPSOPHILA',
            'variedades': {'GYPSOPHILA'},
            'descripcion': 'DRENCH & BIOLÓGICOS',
            'tratamiento_key': 'TRICHOS',
            'apartado': 'TRICHOS',
            'es_solidos': False,
            'es_presiembra': False,
            'vol_default': 41.7
        },
        {
            'bomba': 'RAIZAL, K-TIONIC, TURBO ROOT',
            'edad': 1,
            'cultivo': 'SOLIDAGO',
            'variedades': {'SOLIDAGO'},
            'descripcion': 'ENRAIZANTES',
            'tratamiento_key': 'ENRAIZANTES',
            'apartado': 'TRICHOS',
            'es_solidos': False,
            'es_presiembra': False,
            'vol_default': 41.7
        },
        {
            'bomba': 'SULFATO DE CALCIO, SULFATO DE MAGNESIO AGRICOLA, SULFATO DE POTASIO SUELOS',
            'edad': 1,
            'cultivo': 'SOLIDAGO',
            'variedades': {'SOLIDAGO'},
            'descripcion': 'SÓLIDOS EDÁFICOS',
            'tratamiento_key': 'SOLIDOS',
            'apartado': 'TRICHOS',
            'es_solidos': True,
            'es_presiembra': False,
            'vol_default': 30.0
        },
        {
            'b_key': ('MOSHE', 0),
            'bomba': 'MOSHE',
            'edad': 0,
            'cultivo': 'VERONICA, SOLIDAGO, GYPSOPHILA, HYPERICUM',
            'variedades': {'VERONICA', 'SOLIDAGO', 'GYPSOPHILA', 'HYPERICUM'},
            'descripcion': 'MOSHE (Nutrición y Fondo)',
            'tratamiento_key': 'MOSHE',
            'apartado': 'MOSHE',
            'es_solidos': True,
            'es_presiembra': False,
            'es_programa_completo': True,
            'vol_default': 30.0
        },
        {
            'b_key': ('ALCAPLANT', 0),
            'bomba': 'ALCAPLANT',
            'edad': 0,
            'cultivo': 'HYPERICUM, VERONICA, GYPSOPHILA',
            'variedades': {'HYPERICUM', 'VERONICA', 'GYPSOPHILA'},
            'descripcion': 'ALCAPLANT (Encalado y Calcio)',
            'tratamiento_key': 'ALCAPLANT',
            'apartado': 'ALCAPLANT',
            'es_solidos': True,
            'es_presiembra': False,
            'es_programa_completo': True,
            'vol_default': 30.0
        }
    ]

    for req in bombas_fijas_requeridas:
        b_k = req.get('b_key') or (req['bomba'].strip().upper(), req['edad'])
        if b_k not in calibracion_por_bomba:
            calibracion_por_bomba[b_k] = {
                'bomba': req['bomba'],
                'variedades': set(req['variedades']),
                'cultivo': req['cultivo'],
                'edad': req['edad'],
                'edades': {req['edad']} if req['edad'] > 0 else set(),
                'es_programa_completo': req.get('es_programa_completo', False),
                'descripcion': req['descripcion'],
                'tratamiento_key': req['tratamiento_key'],
                'apartado': req['apartado'],
                'es_solidos': req['es_solidos'],
                'es_presiembra': req['es_presiembra'],
                'vol_default': req['vol_default'],
                'camas_totales': 0,
                'litros_totales': 0.0,
                'bloques': [],
                'tecnicos': set(),
                'items': [],
                'bloque_items': [],
                'productos_calculados': [],
                'estado_calibracion': 'SIN_TOCAR'
            }

    # Ordenar y enriquecer grupos por Bomba
    calibracion_bombas_ordenada = []
    for b_key, b_grp in calibracion_por_bomba.items():
        ed_list = sorted(list(b_grp.get('edades', [])))
        if b_grp.get('es_programa_completo') or b_grp.get('apartado') in ['MOSHE', 'ALCAPLANT']:
            b_grp['es_programa_completo'] = True
            if ed_list:
                b_grp['edad'] = f"{min(ed_list)} - {max(ed_list)}"
                b_grp['edad_sort'] = min(ed_list)
            else:
                b_grp['edad'] = "1 - 14" if b_grp.get('apartado') == 'MOSHE' else "1 - 10"
                b_grp['edad_sort'] = 1
        else:
            b_grp['es_programa_completo'] = False
            b_grp['edad_sort'] = int(b_grp.get('edad', 0)) if str(b_grp.get('edad', 0)).isdigit() else 0

        # Ordenar camas de menor a mayor edad, luego bloque, luego cama_inicial
        b_grp['items'].sort(key=lambda x: (x.get('edad', 0), str(x.get('bloque', '')), x.get('cama_inicial', 0)))
        b_grp['bloque_items'].sort(key=lambda x: (x.get('edad', 0), str(x.get('bloque', '')), x.get('cama_inicial', 0)))

        b_grp['cultivo'] = ", ".join(sorted(list(b_grp.get('variedades', [])))) if b_grp.get('variedades') else b_grp.get('cultivo', 'GENERAL')
        b_grp['variedades'] = sorted(list(b_grp.get('variedades', [])))
        b_grp['tecnicos'] = sorted(list(b_grp.get('tecnicos', [])))
        b_grp['edades'] = sorted(list(b_grp.get('edades', [])))
        dias_b = sorted(list(b_grp.get('dias', [])))
        b_grp['dias'] = dias_b
        b_grp['dia_aplicacion'] = ', '.join(dias_b) if dias_b else 'LUNES'
        estados_b = b_grp.get('estados', set())
        if 'ASIGNADO' in estados_b:
            b_grp['estado_calibracion'] = 'ASIGNADO'
        elif 'EDITADO' in estados_b:
            b_grp['estado_calibracion'] = 'EDITADO'
        else:
            b_grp['estado_calibracion'] = 'SIN_TOCAR'
        b_grp['estados'] = sorted(list(estados_b))

        # Garantizar que ningún campo de tipo set() persista para evitar error de serialización JSON en template
        for k_s, v_s in list(b_grp.items()):
            if isinstance(v_s, set):
                b_grp[k_s] = sorted(list(v_s))
        is_presiembra_grp = (b_grp.get('es_presiembra') or b_grp.get('tratamiento_key') == 'PRESIEMBRAS' or b_grp.get('apartado') == 'PRESIEMBRAS' or b_grp.get('vol_default') == 0)
        if is_presiembra_grp:
            b_grp['vol_default'] = 0.0
            b_grp['litros_totales'] = 0.0
            b_grp['es_presiembra'] = True
        else:
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
            if is_presiembra_grp:
                dose = (p_match.dose_presiembra if p_match else None) or (p_match.dose_solidos if p_match else None) or (p_match.dose_drench if p_match else None)
                if dose is None:
                    dose = 100.0
                tot_q = round(float(dose) * float(b_grp['camas_totales']), 2)
                dose_str = f"{dose} {unit}/cama"
            elif b_grp['tratamiento_key'] == 'MOSHE':
                dose = (p_match.dose_moshe if p_match else None) or (p_match.dose_solidos if p_match else None) or (p_match.dose_drench if p_match else None)
                if dose is None:
                    dose = 1.0
                tot_q = round(float(dose) * float(b_grp['litros_totales']))
                dose_str = f"{dose} {unit}/L"
            elif b_grp['tratamiento_key'] == 'ALCAPLANT':
                dose = (p_match.dose_alcaplant if p_match else None) or (p_match.dose_solidos if p_match else None) or (p_match.dose_drench if p_match else None)
                if dose is None:
                    dose = 2.5
                tot_q = round(float(dose) * float(b_grp['litros_totales']))
                dose_str = f"{dose} {unit}/L"
            else:
                dose = (p_match.dose_trichos if p_match else None) or (p_match.dose_drench if p_match else None)
                if dose is None:
                    dose = 0.05
                tot_q = round(float(dose) * float(b_grp['litros_totales']))
                dose_str = f"{dose} {unit}/L"

            b_prods.append({
                'name': p_match.commercial_name if p_match else p_name,
                'unit': unit,
                'dose': dose,
                'dose_str': dose_str,
                'total_cantidad': tot_q
            })
        b_grp['productos_calculados'] = b_prods
        calibracion_bombas_ordenada.append(b_grp)

    calibracion_bombas_ordenada.sort(key=lambda x: (x['edad_sort'], str(x['bomba'])))

    return calibracion_ordenada, calibracion_tecnico_ordenada, calibracion_bombas_ordenada


def obtener_programas_oficiales_fertilizacion(calibracion_bombas):
    """
    Consolida las bombas calibradas en los 4 programas oficiales de campo:
    1. TRICHOS & BIOLÓGICOS (Drench y Enraizantes) - 41.7 L/cama
    2. MOSHE (Nutrición y Fondo Edáfico) - 30.0 L/cama
    3. ALCAPLANT (Encalado y Calcio) - 30.0 L/cama
    4. PRESIEMBRAS (Planning y Fondo Seco) - Espolvoreo Seco (0 L)
    """
    programas = {
        'TRICHOS': {
            'codigo': 'PROG-TRICHOS',
            'nombre': 'TRICHOS & BIOLÓGICOS',
            'subtitulo': 'Drench Radicular y Enraizantes',
            'apartado': 'TRICHOS',
            'icono': 'fa-shield-virus',
            'color': 'success',
            'vol_cama': '41.7 L/cama',
            'volumen_litros': 0.0,
            'camas_totales': 0,
            'bloques_set': set(),
            'tecnicos_set': set(),
            'dias_set': set(),
            'productos_dict': {},
            'bombas_list': []
        },
        'MOSHE': {
            'codigo': 'PROG-MOSHE',
            'nombre': 'MOSHE (FONDO EDÁFICO)',
            'subtitulo': 'Nutrición y Fondo de Crecimiento',
            'apartado': 'MOSHE',
            'icono': 'fa-vial',
            'color': 'warning',
            'vol_cama': '30.0 L/cama',
            'volumen_litros': 0.0,
            'camas_totales': 0,
            'bloques_set': set(),
            'tecnicos_set': set(),
            'dias_set': set(),
            'productos_dict': {},
            'bombas_list': []
        },
        'ALCAPLANT': {
            'codigo': 'PROG-ALCAPLANT',
            'nombre': 'ALCAPLANT (ENCALADO)',
            'subtitulo': 'Encalado y Corrección de Calcio',
            'apartado': 'ALCAPLANT',
            'icono': 'fa-cubes',
            'color': 'primary',
            'vol_cama': '30.0 L/cama',
            'volumen_litros': 0.0,
            'camas_totales': 0,
            'bloques_set': set(),
            'tecnicos_set': set(),
            'dias_set': set(),
            'productos_dict': {},
            'bombas_list': []
        },
        'PRESIEMBRAS': {
            'codigo': 'PROG-PRESIEMBRAS',
            'nombre': 'PRESIEMBRAS (PLANNING)',
            'subtitulo': 'Espolvoreo Seco y Fondo de Siembra',
            'apartado': 'PRESIEMBRAS',
            'icono': 'fa-seedling',
            'color': 'danger',
            'vol_cama': 'Espolvoreo (0 L)',
            'volumen_litros': 0.0,
            'camas_totales': 0,
            'bloques_set': set(),
            'tecnicos_set': set(),
            'dias_set': set(),
            'productos_dict': {},
            'bombas_list': []
        }
    }

    for b in calibracion_bombas:
        apar = (b.get('apartado') or 'TRICHOS').upper()
        if apar not in programas:
            apar = 'TRICHOS'
        p = programas[apar]
        p['camas_totales'] += int(b.get('camas_totales', 0))
        p['volumen_litros'] += float(b.get('litros_totales', 0.0))
        for blq in b.get('bloques', []):
            p['bloques_set'].add(blq)
        for tec in b.get('tecnicos', []):
            p['tecnicos_set'].add(tec)
        for d in b.get('dias', []):
            p['dias_set'].add(d)
        p['bombas_list'].append(b)

        for prod in b.get('productos_calculados', []):
            p_name = prod.get('name')
            p_unit = prod.get('unit', 'GR')
            p_qty = float(prod.get('total_cantidad', 0.0))
            if p_name not in p['productos_dict']:
                p['productos_dict'][p_name] = {
                    'name': p_name,
                    'unit': p_unit,
                    'dose_str': prod.get('dose_str', ''),
                    'total_cantidad': 0.0
                }
            p['productos_dict'][p_name]['total_cantidad'] += p_qty

    result = []
    for apar_key in ['TRICHOS', 'MOSHE', 'ALCAPLANT', 'PRESIEMBRAS']:
        p = programas[apar_key]
        # REGLA ESTRICTA: Un programa oficial SOLO pasa a Estadística si ha sido guardado / asignado por Agronomía
        has_assigned_bombas = any(
            b.get('estado_calibracion') == 'ASIGNADO' for b in p['bombas_list']
        )
        if not has_assigned_bombas or p['camas_totales'] <= 0:
            continue

        dias_list = sorted(list(p['dias_set']))
        dia_display = ', '.join(dias_list) if dias_list else 'LUNES'
        prods_list = [
            {**v, 'total_cantidad': round(v['total_cantidad'], 2)}
            for v in p['productos_dict'].values()
        ]
        result.append({
            'codigo': p['codigo'],
            'nombre': p['nombre'],
            'subtitulo': p['subtitulo'],
            'apartado': p['apartado'],
            'icono': p['icono'],
            'color': p['color'],
            'vol_cama': p['vol_cama'],
            'volumen_litros': round(p['volumen_litros'], 1),
            'camas_totales': p['camas_totales'],
            'bloques_count': len(p['bloques_set']),
            'bloques': sorted(list(p['bloques_set'])),
            'tecnicos': sorted(list(p['tecnicos_set'])),
            'tecnicos_count': len(p['tecnicos_set']),
            'dia_programado': dia_display,
            'dias': dias_list or ['LUNES'],
            'productos': prods_list,
            'bombas_count': len(p['bombas_list'])
        })
    return result


@fertilizacion_bp.route('/programa-impresion')
@login_required
@permission_required('fertilizacion', 'salidas_imprimir', 'bodega')
def programa_impresion():
    """
    Programa Oficial Imprimible de Trichos y Sólidos con Bloques listados verticalmente
    agrupados por Bomba y Cuarto de Bombeo / Técnico, con columnas de Edad y Descripción.
    Permite filtrar por Apartado (TRICHOS, MOSHE, ALCAPLANT, PRESIEMBRAS), Técnico o Día.
    """
    semana = request.args.get('semana', '').strip()
    if not semana:
        semana = calcular_semana_formato(date.today())

    filtro_tecnico = request.args.get('tecnico', '').strip()
    filtro_bomba = request.args.get('bomba', '').strip()
    filtro_edad = request.args.get('edad', '').strip()
    filtro_apartado = request.args.get('apartado', '').strip().upper()
    filtro_dia = request.args.get('dia', '').strip().upper()

    drench_items = obtener_items_drench(semana=semana)
    calibracion_ordenada, calibracion_tecnicos, calibracion_bombas = procesar_calibracion_fertilizacion(drench_items)

    # REGLA ESTRICTA: Solo las bombas que han sido guardadas o asignadas por Agronomía cargan en los filtros y en la planilla
    calibracion_bombas_asignadas = [
        b for b in calibracion_bombas 
        if b.get('estado_calibracion') == 'ASIGNADO' and b.get('camas_totales', 0) > 0
    ]

    # Catálogos dinámicos basados estrictamente en lo que fue asignado/guardado
    apartados_disponibles = sorted(list(set(b.get('apartado') for b in calibracion_bombas_asignadas if b.get('apartado'))))
    todos_tecnicos = sorted(list(set(
        t for b in calibracion_bombas_asignadas for t in b.get('tecnicos', []) if t
    )))
    dias_disponibles = sorted(list(set(
        d for b in calibracion_bombas_asignadas for d in b.get('dias', []) if d
    )))

    calibracion_bombas = calibracion_bombas_asignadas

    # 1. Filtro por Apartado / Tratamiento (TRICHOS, MOSHE, ALCAPLANT, PRESIEMBRAS)
    if filtro_apartado and filtro_apartado != 'TODOS':
        calibracion_bombas = [
            b for b in calibracion_bombas 
            if b.get('apartado', '').upper() == filtro_apartado or b.get('tratamiento_key', '').upper() == filtro_apartado
        ]

    # 2. Filtro por Día Programado
    if filtro_dia and filtro_dia != 'TODOS':
        calibracion_bombas = [
            b for b in calibracion_bombas 
            if any(filtro_dia in str(d).upper() for d in b.get('dias', [])) or (b.get('dia_aplicacion', '').upper() == filtro_dia)
        ]

    # 3. Filtro por Técnico (Segmentación a nivel de bomba y bloques)
    if filtro_tecnico and filtro_tecnico.upper() != 'TODOS':
        nuevas_bombas = []
        for b in calibracion_bombas:
            matching_items = [
                it for it in b.get('items', [])
                if filtro_tecnico.upper() in (it.get('tecnico') or '').upper()
            ]
            if matching_items:
                b_copy = dict(b)
                b_copy['items'] = matching_items
                b_copy['bloque_items'] = matching_items
                b_camas = sum(float(it.get('cantidad_camas') or 0) for it in matching_items)
                b_copy['camas_totales'] = int(round(b_camas))
                b_copy['litros_totales'] = sum(float(it.get('total_litros') or 0) for it in matching_items)
                b_copy['bloques'] = list(dict.fromkeys([it.get('bloque') for it in matching_items if it.get('bloque')]))
                b_copy['tecnicos'] = list(dict.fromkeys([it.get('tecnico') for it in matching_items if it.get('tecnico')]))
                
                # Escalar los productos proporcionales a las camas de este técnico
                if b.get('productos_calculados') and b.get('camas_totales', 0) > 0:
                    factor = b_camas / b['camas_totales']
                    nuevos_prods = []
                    for prod in b['productos_calculados']:
                        p_c = dict(prod)
                        if isinstance(p_c.get('total_cantidad'), (int, float)):
                            val = p_c['total_cantidad'] * factor
                            p_c['total_cantidad'] = int(round(val)) if val >= 10 else round(val, 2)
                        nuevos_prods.append(p_c)
                    b_copy['productos_calculados'] = nuevos_prods
                nuevas_bombas.append(b_copy)
        calibracion_bombas = nuevas_bombas
        calibracion_tecnicos = [
            t for t in calibracion_tecnicos 
            if filtro_tecnico.upper() in t['tecnico'].upper()
        ]
    else:
        # Mantener solo los técnicos que tengan camas en las bombas activas
        tecs_activos = set()
        for b in calibracion_bombas:
            for t_nom in b.get('tecnicos', []):
                tecs_activos.add(t_nom)
        calibracion_tecnicos = [t for t in calibracion_tecnicos if t['tecnico'] in tecs_activos]

    if filtro_bomba and filtro_bomba.upper() != 'TODAS':
        calibracion_bombas = [
            b for b in calibracion_bombas 
            if filtro_bomba.upper() in b['bomba'].upper()
        ]

    if filtro_edad and filtro_edad.isdigit():
        ed_int = int(filtro_edad)
        calibracion_bombas = [
            b for b in calibracion_bombas 
            if b['edad'] == ed_int
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
        filtro_edad=filtro_edad,
        filtro_apartado=filtro_apartado,
        filtro_dia=filtro_dia,
        todos_tecnicos=todos_tecnicos,
        apartados_disponibles=apartados_disponibles,
        dias_disponibles=dias_disponibles,
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
    """Guarda modificaciones de camas, camas estándar, dosis diferenciada o inserta nuevos bloques"""
    data = request.get_json() or {}
    items_data = data.get('items', [])
    deleted_ids = data.get('deleted_ids', [])
    semana_activa = data.get('semana', '').strip() or '2647'

    if not items_data and not deleted_ids:
        return jsonify({"success": False, "message": "No se enviaron datos para guardar"}), 400

    target_dia = str(data.get('dia_aplicacion') or '').strip().upper()

    try:
        import re
        deleted_count = 0
        if deleted_ids and isinstance(deleted_ids, list):
            deleted_count = DrenchItem.query.filter(
                DrenchItem.id.in_([int(i) for i in deleted_ids if str(i).isdigit()])
            ).delete(synchronize_session=False)

        updated_count = 0
        inserted_count = 0
        for it_info in items_data:
            it_id = it_info.get('id')
            is_new = not it_id or str(it_id).startswith('new_') or it_id == 0

            if is_new:
                w_target = it_info.get('semana') or semana_activa
                c_ini = int(float(it_info.get('cama_inicial', 1)))
                c_fin = int(float(it_info.get('cama_final', 1)))
                c_cam = int(float(it_info.get('cantidad_camas', max(1, c_fin - c_ini + 1))))
                d_diff = float(it_info['dosis_diferenciada']) if it_info.get('dosis_diferenciada') else None
                dia_val = str(it_info.get('dia_aplicacion') or target_dia or 'LUNES').strip().upper()

                nuevo = DrenchItem(
                    semana=str(w_target).strip(),
                    dia_aplicacion=dia_val,
                    bloque=str(it_info.get('bloque', 'S/N')).strip(),
                    cultivo=str(it_info.get('cultivo') or it_info.get('variedad') or 'GYPSOPHILA').strip(),
                    bomba=str(it_info.get('bomba', 'BOMBA MANUAL')).strip(),
                    clasificacion=str(it_info.get('clasificacion', 'CALIBRACION')).strip(),
                    actividad=str(it_info.get('actividad', 'SIEMBRA')).strip(),
                    cama_inicial=c_ini,
                    cama_final=c_fin,
                    cantidad_camas=c_cam,
                    cama_estandar=round(float(it_info.get('cama_estandar', 0.0)), 2),
                    edad=int(float(it_info.get('edad', 0))),
                    tecnico=str(it_info.get('tecnico', 'Técnico 1 (C1)')).strip(),
                    dosis_diferenciada=d_diff,
                    estado_calibracion='ASIGNADO',
                    observaciones=str(it_info.get('observaciones', '')).strip()
                )
                db.session.add(nuevo)
                inserted_count += 1
            else:
                item = DrenchItem.query.get(int(it_id))
                if item:
                    item.estado_calibracion = 'ASIGNADO'
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
                    if 'dosis_diferenciada' in it_info:
                        item.dosis_diferenciada = float(it_info['dosis_diferenciada']) if it_info['dosis_diferenciada'] else None
                    dia_val = str(it_info.get('dia_aplicacion') or target_dia or '').strip().upper()
                    if dia_val:
                        item.dia_aplicacion = dia_val
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
            msg_parts.append(f"Se actualizaron {updated_count} bloques")
        if inserted_count:
            msg_parts.append(f"Se insertaron {inserted_count} nuevos bloques a la calibración")
        if deleted_count:
            msg_parts.append(f"Se eliminaron {deleted_count} bloques")
        return jsonify({
            "success": True,
            "message": ". ".join(msg_parts) if msg_parts else "Cambios guardados correctamente.",
            "updated_count": updated_count,
            "inserted_count": inserted_count,
            "deleted_count": deleted_count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@fertilizacion_bp.route('/api/calibracion/asignar-dia', methods=['POST'])
@login_required
@permission_required('fertilizacion')
def api_calibracion_asignar_dia():
    """Asigna el día programado de aplicación a una bomba, apartado o bloque"""
    data = request.get_json() or {}
    dia = str(data.get('dia') or 'LUNES').strip().upper()
    semana = str(data.get('semana') or '').strip()
    bomba = data.get('bomba')
    apartado = data.get('apartado')
    edad = data.get('edad')
    item_ids = data.get('item_ids', [])

    if not semana:
        return jsonify({"success": False, "message": "Semana requerida"}), 400

    w_clean = semana.replace('-', '').replace('20', '', 1) if semana.startswith('20') else semana
    q = DrenchItem.query.filter(
        db.or_(
            DrenchItem.semana == semana,
            DrenchItem.semana == w_clean,
            DrenchItem.semana == f"20{w_clean}" if len(w_clean) == 4 else False
        )
    )
    if item_ids and isinstance(item_ids, list):
        q = q.filter(DrenchItem.id.in_([int(i) for i in item_ids if str(i).isdigit()]))
    elif apartado and apartado != 'TODOS':
        clasif_map = {
            'MOSHE': ['MOSHE', 'MOSHE-AD'],
            'ALCAPLANT': ['ALCAPLANT'],
            'PRESIEMBRAS': ['PRESIEMBRA'],
            'TRICHOS': ['TRICHOS', 'BIOLOGICOS', 'ENRAIZANTE', 'DRENCH', 'DRENCH HIERRO', 'SOLIDOS']
        }
        if apartado in clasif_map:
            q = q.filter(DrenchItem.clasificacion.in_(clasif_map[apartado]))
    elif bomba:
        b_clean = str(bomba).strip()
        q = q.filter(DrenchItem.bomba.ilike(f"%{b_clean}%"))
        if edad is not None and str(edad).isdigit():
            q = q.filter(DrenchItem.edad == int(edad))

    updated = q.update({
        DrenchItem.dia_aplicacion: dia,
        DrenchItem.estado_calibracion: 'ASIGNADO'
    }, synchronize_session=False)
    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Día programado asignado a {dia} ({updated} registros actualizados como ASIGNADOS).",
        "dia": dia,
        "estado_calibracion": "ASIGNADO",
        "updated": updated
    })


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
    apartado = data.get('apartado', 'TODOS').strip().upper()
    modo_moshe = data.get('modo_moshe', 'INVIERNO').strip().upper()

    if not semana_destino:
        return jsonify({"success": False, "message": "Semana destino requerida"}), 400

    try:
        count = proyectar_semana_fertilizacion(
            semana_destino,
            semana_origen or None,
            apartado=apartado,
            modo_moshe=modo_moshe
        )
        return jsonify({
            "success": True,
            "message": f"Se proyectaron exitosamente {count} registros para la Semana {semana_destino} ({apartado}).",
            "count": count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@fertilizacion_bp.route('/api/moshe/cambiar-modo', methods=['POST'])
@login_required
@permission_required('fertilizacion')
def api_moshe_cambiar_modo():
    """Cambia y persiste inmediatamente el régimen de MOSHE (Verano vs Invierno) en la BD"""
    data = request.get_json() or {}
    semana = data.get('semana', '').strip() or '2647'
    modo = data.get('modo', 'INVIERNO').strip().upper()
    if modo not in ['VERANO', 'INVIERNO']:
        return jsonify({"success": False, "message": "Modo inválido. Debe ser VERANO o INVIERNO"}), 400
    try:
        cnt = proyectar_semana_fertilizacion(semana, apartado='MOSHE', modo_moshe=modo)
        return jsonify({
            "success": True,
            "message": f"Programa MOSHE actualizado exitosamente a Modo {modo} ({cnt} bloques guardados).",
            "modo": modo,
            "registros": cnt
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@fertilizacion_bp.route('/reportes')
@login_required
@permission_required('fertilizacion')
def reportes():
    tecnicos = Tecnico.query.filter_by(activo=True).order_by(Tecnico.nombre).all()
    hoy_str = date.today().strftime("%Y-%m-%d")
    semana_actual = calcular_semana_formato()

    semanas_db = [
        r[0] for r in db.session.query(DrenchItem.semana)
        .filter(DrenchItem.semana.isnot(None))
        .distinct()
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
    ferti_prods = Product.query.filter_by(is_active=True).order_by(Product.commercial_name.asc()).all()

    return render_template(
        'fertilizacion/config.html',
        tecnicos=tecnicos,
        tipos=tipos,
        variedades=variedades,
        total_bloques=total_bloques,
        bloques_list=bloques_list,
        aplicadores=aplicadores,
        ferti_prods=ferti_prods
    )


@fertilizacion_bp.route('/api/config/producto-dosis', methods=['POST'])
@login_required
@permission_required('fertilizacion', 'catalogos')
def api_config_producto_dosis():
    data = request.get_json() or {}
    product_id = data.get('product_id')
    if not product_id:
        return jsonify({'success': False, 'message': 'ID de producto requerido'}), 400
    product = Product.query.get(product_id)
    if not product:
        return jsonify({'success': False, 'message': 'Producto no encontrado'}), 404

    def parse_f(val):
        if val is None or str(val).strip() == '':
            return None
        try:
            return float(val)
        except ValueError:
            return None

    if 'dose_trichos' in data:
        product.dose_trichos = parse_f(data.get('dose_trichos'))
    if 'dose_moshe' in data:
        product.dose_moshe = parse_f(data.get('dose_moshe'))
    if 'dose_alcaplant' in data:
        product.dose_alcaplant = parse_f(data.get('dose_alcaplant'))
    if 'dose_presiembra' in data:
        product.dose_presiembra = parse_f(data.get('dose_presiembra'))
    if 'dose_diferenciada' in data:
        product.dose_diferenciada = parse_f(data.get('dose_diferenciada'))
    if 'unit' in data and data.get('unit'):
        product.unit = data.get('unit').strip().upper()

    db.session.commit()
    record_audit(
        module='FERTILIZACION',
        action='UPDATE_DOSIS_PRODUCTO',
        entity_type='Product',
        entity_id=product.id,
        user=session.get('username', 'Admin'),
        details={'code': product.code, 'dose_trichos': product.dose_trichos, 'dose_moshe': product.dose_moshe, 'dose_alcaplant': product.dose_alcaplant, 'dose_presiembra': product.dose_presiembra}
    )
    return jsonify({
        'success': True,
        'message': f"Dosis de '{product.commercial_name or product.code}' actualizadas exitosamente."
    })

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
