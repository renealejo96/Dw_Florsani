import os
import sys
import re
import unicodedata
from datetime import date, datetime
from app.extensions import db
from app.modules.fertilizacion.models import DrenchItem, TecnicoBloque, calcular_semana_formato

def normalizar_clasificacion(texto):
    if not texto:
        return ""
    return ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn').upper().strip()

def obtener_bloques_de_tecnico(tecnico):
    if not tecnico or str(tecnico).strip().upper() in ('TODOS', 'TODOS LOS TECNICOS', 'TODOS LOS TÉCNICOS', 'ALL'):
        return None
    
    m = re.search(r'([1-5])', str(tecnico))
    if m:
        num = m.group(1)
        filtro = db.or_(
            TecnicoBloque.tecnico_nombre.ilike(f'%{num}%'),
            TecnicoBloque.tecnico_nombre.ilike(f'%C{num}%')
        )
    else:
        filtro = TecnicoBloque.tecnico_nombre.ilike(f'%{str(tecnico).strip()}%')

    rows = TecnicoBloque.query.filter(filtro).all()
    return [r.bloque.strip() for r in rows if r.bloque]

def obtener_items_drench(fecha=None, semana=None, clasificacion=None, bomba=None, bloque=None, completado=None, search=None, tecnico=None):
    query = DrenchItem.query
    if semana:
        query = query.filter(DrenchItem.semana == str(semana).strip())
    elif fecha:
        query = query.filter(DrenchItem.fecha == fecha)
    
    if tecnico:
        bloques_tec = obtener_bloques_de_tecnico(tecnico)
        if bloques_tec is not None:
            query = query.filter(DrenchItem.bloque.in_(bloques_tec))

    if clasificacion and clasificacion != "TODAS":
        c_norm = normalizar_clasificacion(clasificacion)
        query = query.filter(
            db.or_(
                DrenchItem.clasificacion == c_norm,
                DrenchItem.clasificacion == clasificacion.upper(),
                DrenchItem.clasificacion.ilike(f"%{c_norm}%")
            )
        )

    if bomba and bomba != "TODAS":
        query = query.filter(DrenchItem.bomba == bomba)
        
    if bloque and bloque != "TODOS":
        query = query.filter(DrenchItem.bloque == bloque)
        
    if completado is not None:
        query = query.filter(DrenchItem.completado == completado)

    if search:
        s = f"%{search}%"
        query = query.filter(
            db.or_(
                DrenchItem.bomba.ilike(s),
                DrenchItem.cultivo.ilike(s),
                DrenchItem.bloque.ilike(s),
                DrenchItem.clasificacion.ilike(s),
                DrenchItem.actividad.ilike(s)
            )
        )

    query = query.order_by(
        DrenchItem.clasificacion,
        DrenchItem.bomba,
        DrenchItem.bloque,
        DrenchItem.cama_inicial
    )
    return query.all()

def toggle_completado_drench(item_id, aplicador="Sistema", tecnico="Técnico", nuevo_estado=None):
    item = DrenchItem.query.get(item_id)
    if not item:
        return None, "Item no encontrado"

    if nuevo_estado is not None:
        item.completado = bool(nuevo_estado)
    else:
        item.completado = not item.completado

    if item.completado:
        item.aplicador = aplicador
        item.tecnico = tecnico
        item.fecha_completado = datetime.now()
    else:
        item.fecha_completado = None

    db.session.commit()
    return item, "Estado actualizado con éxito"

def obtener_bombas_por_clasificacion(fecha=None, semana=None, clasificacion=None, tecnico=None):
    query = DrenchItem.query
    if semana:
        query = query.filter(DrenchItem.semana == str(semana).strip())
    elif fecha:
        query = query.filter(DrenchItem.fecha == fecha)
    if tecnico:
        bloques_tec = obtener_bloques_de_tecnico(tecnico)
        if bloques_tec is not None:
            query = query.filter(DrenchItem.bloque.in_(bloques_tec))
    if clasificacion and clasificacion != "TODAS":
        c_norm = normalizar_clasificacion(clasificacion)
        query = query.filter(
            db.or_(
                DrenchItem.clasificacion == c_norm,
                DrenchItem.clasificacion == clasificacion.upper(),
                DrenchItem.clasificacion.ilike(f"%{c_norm}%")
            )
        )

    items = query.all()
    bombas_map = {}

    for it in items:
        b = it.bomba
        if b not in bombas_map:
            bombas_map[b] = {
                "bomba": b,
                "clasificacion": it.clasificacion,
                "total": 0,
                "completados": 0,
                "pendientes": 0,
                "camas_estandar_total": 0.0,
                "camas_estandar_completadas": 0.0,
                "porcentaje": 0.0,
                "cultivos": set(),
                "edades": set(),
                "bloques": set()
            }
        bombas_map[b]["total"] += 1
        bombas_map[b]["camas_estandar_total"] += it.cama_estandar or 0.0
        bombas_map[b]["cultivos"].add(it.cultivo)
        bombas_map[b]["edades"].add(it.edad)
        bombas_map[b]["bloques"].add(it.bloque)
        if it.completado:
            bombas_map[b]["completados"] += 1
            bombas_map[b]["camas_estandar_completadas"] += it.cama_estandar or 0.0

    resultado = []
    for b, data in bombas_map.items():
        data["pendientes"] = data["total"] - data["completados"]
        data["porcentaje"] = round((data["completados"] / data["total"] * 100), 1) if data["total"] > 0 else 0.0
        data["camas_estandar_total"] = round(data["camas_estandar_total"], 2)
        data["camas_estandar_completadas"] = round(data["camas_estandar_completadas"], 2)
        data["cultivos"] = sorted(list(data["cultivos"]))
        data["edades"] = sorted(list(data["edades"]))
        data["total_bloques"] = len(data["bloques"])
        data["bloques"] = sorted(list(data["bloques"]))
        resultado.append(data)

    resultado.sort(key=lambda x: (-x["total"], x["bomba"]))
    return resultado

def obtener_resumen_clasificaciones(fecha=None, semana=None, tecnico=None):
    query = DrenchItem.query
    if semana:
        query = query.filter(DrenchItem.semana == str(semana).strip())
    elif fecha:
        query = query.filter(DrenchItem.fecha == fecha)
    if tecnico:
        bloques_tec = obtener_bloques_de_tecnico(tecnico)
        if bloques_tec is not None:
            query = query.filter(DrenchItem.bloque.in_(bloques_tec))
        
    items = query.all()
    resumen = {}
    for it in items:
        c = it.clasificacion
        if c not in resumen:
            resumen[c] = {
                "total": 0,
                "completados": 0,
                "pendientes": 0,
                "camas_estandar_total": 0.0,
                "camas_estandar_completadas": 0.0,
                "porcentaje": 0.0,
                "total_bombas": set()
            }
        resumen[c]["total"] += 1
        resumen[c]["camas_estandar_total"] += it.cama_estandar or 0.0
        resumen[c]["total_bombas"].add(it.bomba)
        if it.completado:
            resumen[c]["completados"] += 1
            resumen[c]["camas_estandar_completadas"] += it.cama_estandar or 0.0

    for c, d in resumen.items():
        d["pendientes"] = d["total"] - d["completados"]
        d["porcentaje"] = round((d["completados"] / d["total"] * 100), 1) if d["total"] > 0 else 0.0
        d["camas_estandar_total"] = round(d["camas_estandar_total"], 2)
        d["camas_estandar_completadas"] = round(d["camas_estandar_completadas"], 2)
        d["total_bombas"] = len(d["total_bombas"])

    return resumen

def obtener_bloques_disponibles(fecha=None, semana=None, clasificacion=None, bomba=None, tecnico=None):
    query = db.session.query(DrenchItem.bloque).distinct()
    if semana:
        query = query.filter(DrenchItem.semana == str(semana).strip())
    elif fecha:
        query = query.filter(DrenchItem.fecha == fecha)
    if tecnico:
        bloques_tec = obtener_bloques_de_tecnico(tecnico)
        if bloques_tec is not None:
            query = query.filter(DrenchItem.bloque.in_(bloques_tec))
    if clasificacion and clasificacion != "TODAS":
        c_norm = normalizar_clasificacion(clasificacion)
        query = query.filter(
            db.or_(
                DrenchItem.clasificacion == c_norm,
                DrenchItem.clasificacion == clasificacion.upper(),
                DrenchItem.clasificacion.ilike(f"%{c_norm}%")
            )
        )
    if bomba and bomba != "TODAS":
        query = query.filter(DrenchItem.bomba == bomba)
    rows = query.order_by(DrenchItem.bloque).all()
    return [r[0] for r in rows if r[0]]
