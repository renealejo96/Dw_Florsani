from datetime import date
from app.extensions import db
from app.modules.fertilizacion.models import DrenchItem, RiegoDuchaRegistro, ValvulaRegistro, Tecnico, TecnicoBloque
from app.modules.fertilizacion.services.drench_service import obtener_bloques_de_tecnico

def generar_datos_reporte_powerbi(fecha=None, tecnico=None, semana=None):
    """
    Calcula métricas agregadas al estilo de Power BI para el tablero de control,
    con soporte para modelo de negocio semanal (formato YYWW, ej. 2638).
    """
    drench_query = DrenchItem.query
    riego_query = RiegoDuchaRegistro.query
    valvula_query = ValvulaRegistro.query

    if fecha:
        drench_query = drench_query.filter(DrenchItem.fecha == fecha)
        riego_query = riego_query.filter(RiegoDuchaRegistro.fecha == fecha)
        valvula_query = valvula_query.filter(ValvulaRegistro.fecha == fecha)

    if semana and semana != "TODAS":
        drench_query = drench_query.filter(DrenchItem.semana == semana)
        riego_query = riego_query.filter(RiegoDuchaRegistro.semana == semana)
        valvula_query = valvula_query.filter(ValvulaRegistro.semana == semana)

    if tecnico and tecnico != "TODOS":
        bloques_tec = obtener_bloques_de_tecnico(tecnico)
        if bloques_tec is not None:
            drench_query = drench_query.filter(
                db.or_(
                    DrenchItem.bloque.in_(bloques_tec),
                    DrenchItem.tecnico == tecnico
                )
            )
        else:
            drench_query = drench_query.filter(DrenchItem.tecnico == tecnico)
        riego_query = riego_query.filter(RiegoDuchaRegistro.tecnico == tecnico)
        valvula_query = valvula_query.filter(ValvulaRegistro.tecnico == tecnico)

    drench_items = drench_query.all()
    riego_items = riego_query.all()
    valvula_items = valvula_query.all()

    # KPIs Globales de Drench
    total_drench = len(drench_items)
    completados_drench = sum(1 for it in drench_items if it.completado)
    pendientes_drench = total_drench - completados_drench
    pct_global_drench = round((completados_drench / total_drench * 100), 1) if total_drench > 0 else 0.0

    camas_estandar_total = round(sum(it.cama_estandar or 0.0 for it in drench_items), 2)
    camas_estandar_completadas = round(sum(it.cama_estandar or 0.0 for it in drench_items if it.completado), 2)
    pct_camas_estandar = round((camas_estandar_completadas / camas_estandar_total * 100), 1) if camas_estandar_total > 0 else 0.0

    # % Cumplimiento por Clasificación
    clasificaciones_data = {}
    for it in drench_items:
        c = it.clasificacion
        if c not in clasificaciones_data:
            clasificaciones_data[c] = {
                "clasificacion": c,
                "total": 0,
                "completados": 0,
                "pendientes": 0,
                "porcentaje": 0.0,
                "camas_estandar_total": 0.0,
                "camas_estandar_completadas": 0.0
            }
        clasificaciones_data[c]["total"] += 1
        clasificaciones_data[c]["camas_estandar_total"] += it.cama_estandar or 0.0
        if it.completado:
            clasificaciones_data[c]["completados"] += 1
            clasificaciones_data[c]["camas_estandar_completadas"] += it.cama_estandar or 0.0

    for c, d in clasificaciones_data.items():
        d["pendientes"] = d["total"] - d["completados"]
        d["porcentaje"] = round((d["completados"] / d["total"] * 100), 1) if d["total"] > 0 else 0.0
        d["camas_estandar_total"] = round(d["camas_estandar_total"], 2)
        d["camas_estandar_completadas"] = round(d["camas_estandar_completadas"], 2)

    clasificaciones_list = sorted(clasificaciones_data.values(), key=lambda x: x["clasificacion"])

    # Rendimientos de Riego Ducha
    total_riego = len(riego_items)
    camas_riego_total = sum(r.camas_fisicas or 0 for r in riego_items)
    minutos_riego_total = sum(r.duracion_minutos or 0.0 for r in riego_items)
    prom_min_cama = round((minutos_riego_total / camas_riego_total), 2) if camas_riego_total > 0 else 0.0
    camas_por_hora = round((camas_riego_total / (minutos_riego_total / 60)), 1) if minutos_riego_total > 0 else 0.0

    # Detalle por Aplicador / Técnico en Riego Ducha
    rendimientos_aplicador = {}
    for r in riego_items:
        ap = r.aplicador or "Sin Especificar"
        if ap not in rendimientos_aplicador:
            rendimientos_aplicador[ap] = {
                "aplicador": ap,
                "registros": 0,
                "camas_totales": 0,
                "minutos_totales": 0.0,
                "promedio_min_cama": 0.0,
                "camas_por_hora": 0.0
            }
        rendimientos_aplicador[ap]["registros"] += 1
        rendimientos_aplicador[ap]["camas_totales"] += r.camas_fisicas or 0
        rendimientos_aplicador[ap]["minutos_totales"] += r.duracion_minutos or 0.0

    for ap, data in rendimientos_aplicador.items():
        if data["camas_totales"] > 0:
            data["promedio_min_cama"] = round(data["minutos_totales"] / data["camas_totales"], 2)
        if data["minutos_totales"] > 0:
            data["camas_por_hora"] = round(data["camas_totales"] / (data["minutos_totales"] / 60), 1)
        data["minutos_totales"] = round(data["minutos_totales"], 1)

    # Válvulas
    total_valvulas = len(valvula_items)
    valvulas_sum = sum(v.cantidad_valvulas or 0 for v in valvula_items)
    minutos_valvulas_sum = sum(v.minutos_riego or 0 for v in valvula_items)

    return {
        "kpis_drench": {
            "total_items": total_drench,
            "completados": completados_drench,
            "pendientes": pendientes_drench,
            "porcentaje_cumplimiento": pct_global_drench,
            "camas_estandar_total": camas_estandar_total,
            "camas_estandar_completadas": camas_estandar_completadas,
            "porcentaje_camas_estandar": pct_camas_estandar
        },
        "clasificaciones": clasificaciones_list,
        "kpis_riego": {
            "total_registros": total_riego,
            "camas_totales": camas_riego_total,
            "minutos_totales": round(minutos_riego_total, 1),
            "promedio_min_cama": prom_min_cama,
            "camas_por_hora": camas_por_hora,
            "por_aplicador": list(rendimientos_aplicador.values())
        },
        "kpis_valvulas": {
            "total_registros": total_valvulas,
            "cantidad_valvulas": valvulas_sum,
            "minutos_totales": minutos_valvulas_sum
        }
    }
