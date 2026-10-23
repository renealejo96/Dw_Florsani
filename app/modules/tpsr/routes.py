from flask import Blueprint, render_template, request, jsonify
from datetime import datetime, date
from sqlalchemy import text
from app.extensions import db
from app.modules.auth.routes import login_required, permission_required, admin_required

tpsr_bp = Blueprint('tpsr', __name__, url_prefix='/tpsr')


@tpsr_bp.route('/')
@login_required
@permission_required('tpsr')
def index():
    """Dashboard de Avance Semanal de TPSR por Producto."""
    semana_filtro = request.args.get('semana', '')

    with db.engine.connect() as conn:
        # Obtener semanas disponibles con eventos
        query_semanas = """
            SELECT DISTINCT semana_campo 
            FROM fact_eventos_cultivo 
            WHERE semana_campo IS NOT NULL 
            ORDER BY semana_campo DESC 
            LIMIT 15;
        """
        semanas = [r[0] for r in conn.execute(text(query_semanas)).fetchall()]

        if not semana_filtro and semanas:
            semana_filtro = semanas[0]

        # Resumen de avance por producto y tipo de evento
        query_avance = """
            SELECT 
                COALESCE(producto_maestro, 'SIN ESPECIFICAR') AS producto,
                tipo_evento,
                COUNT(DISTINCT id_cama) AS total_camas,
                COUNT(DISTINCT codigo_bloque) AS total_bloques,
                COALESCE(SUM(num_plantas), 0) AS total_plantas
            FROM fact_eventos_cultivo
            WHERE semana_campo = :semana AND estado_evento = 'ACTIVO'
            GROUP BY producto_maestro, tipo_evento
            ORDER BY producto_maestro, tipo_evento;
        """
        res_avance = conn.execute(text(query_avance), {'semana': semana_filtro}).fetchall()

        # Conteo exacto de bloques únicos por producto y total de la semana
        query_bloques = """
            SELECT 
                COALESCE(producto_maestro, 'SIN ESPECIFICAR') AS producto,
                COUNT(DISTINCT codigo_bloque) AS total_bloques
            FROM fact_eventos_cultivo
            WHERE semana_campo = :semana AND estado_evento = 'ACTIVO'
            GROUP BY producto_maestro;
        """
        bloques_map = {r[0]: r[1] for r in conn.execute(text(query_bloques), {'semana': semana_filtro}).fetchall()}

        query_total_bloques = """
            SELECT COUNT(DISTINCT codigo_bloque)
            FROM fact_eventos_cultivo
            WHERE semana_campo = :semana AND estado_evento = 'ACTIVO';
        """
        total_bloques_semana = conn.execute(text(query_total_bloques), {'semana': semana_filtro}).scalar() or 0

        # Catálogo maestro de cultivos presentes en la finca
        query_all_prods = """
            SELECT DISTINCT producto_maestro 
            FROM fact_eventos_cultivo 
            WHERE producto_maestro IS NOT NULL 
              AND producto_maestro NOT IN ('VACIO', 'ERRADICADO', 'NAN', 'TOTAL')
            ORDER BY producto_maestro;
        """
        all_prods = [r[0] for r in conn.execute(text(query_all_prods)).fetchall()]

    # Organizar datos por producto (inicializando todos los cultivos para que no desaparezca ninguno)
    productos_resumen = {
        p: {
            'SIEMBRA': 0, 'PODA': 0, 'TUMBA': 0, 
            'plantas_siembra': 0, 'plantas_poda': 0,
            'num_bloques': bloques_map.get(p, 0)
        } for p in all_prods
    }
    for r in res_avance:
        prod = r[0]
        if prod not in productos_resumen:
            productos_resumen[prod] = {
                'SIEMBRA': 0, 'PODA': 0, 'TUMBA': 0, 
                'plantas_siembra': 0, 'plantas_poda': 0,
                'num_bloques': bloques_map.get(prod, 0)
            }
        ev_type = r[1]
        if ev_type in ('SIEMBRA', 'PODA', 'TUMBA'):
            productos_resumen[prod][ev_type] = r[2]
            if ev_type == 'SIEMBRA':
                productos_resumen[prod]['plantas_siembra'] = int(r[4])
            elif ev_type == 'PODA':
                productos_resumen[prod]['plantas_poda'] = int(r[4])

    totales_globales = {
        'siembra_camas': sum(s['SIEMBRA'] for s in productos_resumen.values()),
        'siembra_plantas': sum(s['plantas_siembra'] for s in productos_resumen.values()),
        'poda_camas': sum(s['PODA'] for s in productos_resumen.values()),
        'poda_plantas': sum(s['plantas_poda'] for s in productos_resumen.values()),
        'tumba_camas': sum(s['TUMBA'] for s in productos_resumen.values()),
        'total_bloques': total_bloques_semana,
        'total_camas': sum(s['SIEMBRA'] + s['PODA'] + s['TUMBA'] for s in productos_resumen.values())
    }

    return render_template(
        'tpsr/index.html',
        semanas=semanas,
        semana_actual=semana_filtro,
        productos_resumen=productos_resumen,
        totales_globales=totales_globales
    )


@tpsr_bp.route('/registro')
@login_required
@permission_required('tpsr')
def registro():
    """Pantalla interactiva visual de registro de campo (Zonas -> Bloques -> Rectángulos de Camas -> Formulario)."""
    with db.engine.connect() as conn:
        # 1. Catálogo de Productos Maestros
        query_prods = """
            SELECT DISTINCT producto_maestro 
            FROM fact_eventos_cultivo 
            WHERE producto_maestro IS NOT NULL 
              AND producto_maestro NOT IN ('VACIO', 'ERRADICADO', 'NAN', 'TOTAL')
            ORDER BY producto_maestro;
        """
        productos = [r[0] for r in conn.execute(text(query_prods)).fetchall()]

        # 2. Catálogo de Bloques Físicos Base (blq) con estado vivo + % Vegetativo y % Productivo + Área
        query_blqs = """
            WITH blq_dim AS (
                SELECT 
                    b.blq,
                    COALESCE(MAX(b.zona), 'SIN ZONA') AS zona,
                    SUM(b.total_camas_fisicas) AS total_camas,
                    ROUND(SUM(b.total_camas_estandar)::numeric, 2) AS total_std,
                    ROUND(SUM(b.area_neta_m2)::numeric, 1) AS area_neta,
                    ROUND(SUM(b.area_bruta_m2)::numeric, 1) AS area_bruta,
                    ROUND(SUM(b.ha)::numeric, 4) AS ha,
                    COUNT(DISTINCT b.codigo_bloque) AS total_segmentos,
                    STRING_AGG(DISTINCT b.sufijo, ', ' ORDER BY b.sufijo) AS segmentos_str,
                    STRING_AGG(DISTINCT b.tecnico_de_riego, ', ') AS tecnico
                FROM dim_bloques b
                GROUP BY b.blq
            ),
            mrt_agg AS (
                SELECT 
                    m.blq,
                    COUNT(*) AS camas_mrt,
                    ROUND(SUM(m.metros_netos)::numeric, 1) AS mrt_area_neta,
                    ROUND(SUM(m.metros_brutos)::numeric, 1) AS mrt_area_bruta,
                    ROUND(SUM(m.cama_estandar)::numeric, 2) AS mrt_std,
                    SUM(CASE WHEN m.ultimo_evento = 'SIEMBRA' AND COALESCE(m.producto_actual, 'VACIO') NOT IN ('VACIO', 'ERRADICADO') AND COALESCE(m.variedad_actual, 'ERRADICADO') != 'ERRADICADO' THEN 1 ELSE 0 END) AS camas_siembra,
                    SUM(CASE WHEN m.ultimo_evento = 'PODA' AND COALESCE(m.producto_actual, 'VACIO') NOT IN ('VACIO', 'ERRADICADO') AND COALESCE(m.variedad_actual, 'ERRADICADO') != 'ERRADICADO' THEN 1 ELSE 0 END) AS camas_poda,
                    SUM(CASE WHEN m.ultimo_evento = 'TUMBA' OR m.ultimo_evento IS NULL OR COALESCE(m.producto_actual, 'VACIO') IN ('VACIO', 'ERRADICADO') OR COALESCE(m.variedad_actual, 'ERRADICADO') = 'ERRADICADO' THEN 1 ELSE 0 END) AS camas_vacias,
                    SUM(CASE
                        WHEN m.ultimo_evento IN ('SIEMBRA', 'PODA')
                             AND COALESCE(m.producto_actual, 'VACIO') NOT IN ('VACIO', 'ERRADICADO')
                             AND COALESCE(m.variedad_actual, 'ERRADICADO') != 'ERRADICADO'
                             AND COALESCE(m.edad_semanas_actual, 0) <= COALESCE(cr.veg_max_age, CASE WHEN UPPER(m.producto_actual) LIKE '%HYPER%' THEN 12 ELSE 9 END)
                        THEN 1 ELSE 0 END) AS camas_veg,
                    SUM(CASE
                        WHEN m.ultimo_evento IN ('SIEMBRA', 'PODA')
                             AND COALESCE(m.producto_actual, 'VACIO') NOT IN ('VACIO', 'ERRADICADO')
                             AND COALESCE(m.variedad_actual, 'ERRADICADO') != 'ERRADICADO'
                             AND COALESCE(m.edad_semanas_actual, 0) > COALESCE(cr.veg_max_age, CASE WHEN UPPER(m.producto_actual) LIKE '%HYPER%' THEN 12 ELSE 9 END)
                        THEN 1 ELSE 0 END) AS camas_prod,
                    ROUND(SUM(CASE
                        WHEN m.ultimo_evento IN ('SIEMBRA', 'PODA')
                             AND COALESCE(m.producto_actual, 'VACIO') NOT IN ('VACIO', 'ERRADICADO')
                             AND COALESCE(m.variedad_actual, 'ERRADICADO') != 'ERRADICADO'
                             AND COALESCE(m.edad_semanas_actual, 0) <= COALESCE(cr.veg_max_age, CASE WHEN UPPER(m.producto_actual) LIKE '%HYPER%' THEN 12 ELSE 9 END)
                        THEN m.metros_netos ELSE 0 END)::numeric, 1) AS area_veg,
                    ROUND(SUM(CASE
                        WHEN m.ultimo_evento IN ('SIEMBRA', 'PODA')
                             AND COALESCE(m.producto_actual, 'VACIO') NOT IN ('VACIO', 'ERRADICADO')
                             AND COALESCE(m.variedad_actual, 'ERRADICADO') != 'ERRADICADO'
                             AND COALESCE(m.edad_semanas_actual, 0) > COALESCE(cr.veg_max_age, CASE WHEN UPPER(m.producto_actual) LIKE '%HYPER%' THEN 12 ELSE 9 END)
                        THEN m.metros_netos ELSE 0 END)::numeric, 1) AS area_prod,
                    STRING_AGG(DISTINCT CASE WHEN COALESCE(m.producto_actual, 'VACIO') NOT IN ('VACIO', 'ERRADICADO') THEN m.producto_actual END, ', ') AS productos
                FROM mrt_estado_cultivo_actual m
                LEFT JOIN crops cr ON UPPER(cr.name) = UPPER(m.producto_actual)
                GROUP BY m.blq
            )
            SELECT 
                d.blq,
                d.zona,
                COALESCE(m.camas_mrt, d.total_camas) AS total_camas,
                COALESCE(m.mrt_std, d.total_std) AS total_std,
                COALESCE(m.mrt_area_neta, d.area_neta) AS area_neta,
                COALESCE(m.mrt_area_bruta, d.area_bruta) AS area_bruta,
                d.ha,
                d.total_segmentos,
                d.segmentos_str,
                d.tecnico,
                COALESCE(m.camas_siembra, 0) AS camas_siembra,
                COALESCE(m.camas_poda, 0) AS camas_poda,
                COALESCE(m.camas_vacias, d.total_camas) AS camas_vacias,
                COALESCE(m.productos, '') AS productos,
                COALESCE(m.camas_veg, 0) AS camas_veg,
                COALESCE(m.camas_prod, 0) AS camas_prod,
                COALESCE(m.area_veg, 0) AS area_veg,
                COALESCE(m.area_prod, 0) AS area_prod
            FROM blq_dim d
            LEFT JOIN mrt_agg m ON d.blq = m.blq
            ORDER BY d.zona,
                     COALESCE(NULLIF(regexp_replace(d.blq, '[^0-9]', '', 'g'), '')::int, 999999),
                     d.blq;
        """
        bloques = []
        zonas_map = {}

        for r in conn.execute(text(query_blqs)).fetchall():
            blq_code = r[0]
            zona_name = (r[1] or 'SIN ZONA').strip()
            tot_c = int(r[2]) if r[2] else 0
            c_siembra = int(r[10]) if r[10] else 0
            c_poda = int(r[11]) if r[11] else 0
            c_vacias = int(r[12]) if r[12] else 0
            c_veg = int(r[14]) if r[14] else 0
            c_prod = int(r[15]) if r[15] else 0
            a_veg = float(r[16]) if r[16] else 0.0
            a_prod = float(r[17]) if r[17] else 0.0

            pct_veg = round((c_veg / tot_c) * 100, 1) if tot_c > 0 else 0.0
            pct_prod = round((c_prod / tot_c) * 100, 1) if tot_c > 0 else 0.0
            pct_tumb = max(0.0, round(100.0 - pct_veg - pct_prod, 1)) if tot_c > 0 else 0.0

            # Estado visual del bloque:
            if (c_siembra + c_poda) == 0:
                estado_blq = 'VACIO'
            elif c_poda > c_siembra:
                estado_blq = 'PODA'
            else:
                estado_blq = 'SIEMBRA'

            blq_obj = {
                'blq': blq_code,
                'zona': zona_name,
                'camas': tot_c,
                'std': float(r[3]) if r[3] else 0.0,
                'area_neta': float(r[4]) if r[4] else 0.0,
                'area_bruta': float(r[5]) if r[5] else 0.0,
                'ha': float(r[6]) if r[6] else 0.0,
                'segmentos': int(r[7]) if r[7] else 1,
                'segs_str': r[8] or 'A',
                'tecnico': r[9] or 'N/A',
                'camas_siembra': c_siembra,
                'camas_poda': c_poda,
                'camas_vacias': c_vacias,
                'productos': r[13] or 'VACÍO / TUMBADO',
                'estado_bloque': estado_blq,
                'camas_veg': c_veg,
                'camas_prod': c_prod,
                'pct_veg': pct_veg,
                'pct_prod': pct_prod,
                'pct_tumb': pct_tumb,
                'area_veg': a_veg,
                'area_prod': a_prod
            }
            bloques.append(blq_obj)

            if zona_name not in zonas_map:
                zonas_map[zona_name] = {'zona': zona_name, 'total_bloques': 0, 'siembra': 0, 'poda': 0, 'vacio': 0}
            zonas_map[zona_name]['total_bloques'] += 1
            if estado_blq == 'SIEMBRA':
                zonas_map[zona_name]['siembra'] += 1
            elif estado_blq == 'PODA':
                zonas_map[zona_name]['poda'] += 1
            else:
                zonas_map[zona_name]['vacio'] += 1

        zonas_list = [zonas_map[z] for z in sorted(zonas_map.keys())]

        # 3. Semanas agrícolas habilitadas en el sistema (gobernadas por el corte de cultivo)
        query_cal = """
            WITH semanas_habilitadas AS (
                SELECT DISTINCT semana_campo 
                FROM fact_eventos_cultivo 
                WHERE estado_evento = 'ACTIVO'
                UNION
                SELECT DISTINCT 
                    CASE 
                        WHEN week ~ '^20[0-9]{2}-[0-9]{1,2}$' THEN (SUBSTRING(week, 3, 2)::int * 100 + SUBSTRING(week, 6, 2)::int)
                        WHEN week ~ '^[0-9]{4}$' THEN week::int
                        ELSE 2640
                    END AS semana_campo
                FROM crop_state_records
            )
            SELECT 
                c.semana_campo,
                c.semana_abs,
                TO_CHAR(MIN(c.fecha), 'YYYY-MM-DD') AS fecha_inicio,
                TO_CHAR(MAX(c.fecha), 'YYYY-MM-DD') AS fecha_fin
            FROM dim_calendario_agricola c
            INNER JOIN semanas_habilitadas s ON c.semana_campo = s.semana_campo
            GROUP BY c.semana_campo, c.semana_abs
            ORDER BY c.semana_campo DESC;
        """
        semanas_cal = [
            {
                'semana_campo': r[0],
                'semana_abs': r[1],
                'fecha_inicio': r[2],
                'fecha_fin': r[3]
            } for r in conn.execute(text(query_cal)).fetchall()
        ]

        # 4. Semana de corte maestro (la semana habilitada más reciente)
        semana_hoy = semanas_cal[0]['semana_campo'] if semanas_cal else 2640
        semana_abs_hoy = semanas_cal[0]['semana_abs'] if semanas_cal else 1040

        # 5. Catálogo de densidades ideales por cama (dim_densidad_plantas)
        query_dens = """
            SELECT producto_maestro, COALESCE(variedad, '__DEFAULT__'), plantas_por_cama 
            FROM dim_densidad_plantas;
        """
        densidades_plantas = {}
        for r in conn.execute(text(query_dens)).fetchall():
            prod_name = r[0].upper().strip()
            var_name = r[1].upper().strip()
            if prod_name not in densidades_plantas:
                densidades_plantas[prod_name] = {}
            densidades_plantas[prod_name][var_name] = int(r[2])

        # 6. Catálogo de variedades por producto maestro (fact_eventos_cultivo + crop_state_records)
        query_vars = """
            SELECT DISTINCT UPPER(TRIM(producto_maestro)) AS prod, TRIM(variedad) AS var
            FROM fact_eventos_cultivo
            WHERE producto_maestro IS NOT NULL
              AND variedad IS NOT NULL
              AND UPPER(TRIM(variedad)) NOT IN ('', 'VACIO', 'ERRADICADO', 'DESCONOCIDA', 'NAN', 'DISPONIBLE')
            UNION
            SELECT DISTINCT UPPER(TRIM(crop_master)) AS prod, TRIM(variety) AS var
            FROM crop_state_records
            WHERE crop_master IS NOT NULL
              AND variety IS NOT NULL
              AND UPPER(TRIM(variety)) NOT IN ('', 'VACIO', 'ERRADICADO', 'DESCONOCIDA', 'NAN', 'DISPONIBLE')
            ORDER BY prod, var;
        """
        variedades_por_producto = {}
        for r in conn.execute(text(query_vars)).fetchall():
            p_key = (r[0] or '').strip().upper()
            v_val = (r[1] or '').strip()
            if p_key and v_val:
                if p_key not in variedades_por_producto:
                    variedades_por_producto[p_key] = []
                if v_val not in variedades_por_producto[p_key]:
                    variedades_por_producto[p_key].append(v_val)

    return render_template(
        'tpsr/registro.html',
        productos=productos,
        bloques=bloques,
        zonas_list=zonas_list,
        semanas_cal=semanas_cal,
        semana_hoy=semana_hoy,
        semana_abs_hoy=semana_abs_hoy,
        densidades_plantas=densidades_plantas,
        variedades_por_producto=variedades_por_producto,
        hoy=date.today().strftime('%Y-%m-%d')
    )


@tpsr_bp.route('/api/variedades/<producto>')
@login_required
def api_variedades(producto):
    """Devuelve las variedades asociadas a un producto maestro."""
    with db.engine.connect() as conn:
        query = """
            SELECT DISTINCT variedad 
            FROM fact_eventos_cultivo 
            WHERE producto_maestro = :prod 
              AND variedad IS NOT NULL 
              AND variedad NOT IN ('VACIO', 'ERRADICADO', 'DESCONOCIDA', 'NAN')
            ORDER BY variedad;
        """
        variedades = [r[0] for r in conn.execute(text(query), {'prod': producto}).fetchall()]
    return jsonify(variedades)


@tpsr_bp.route('/api/bloque-camas/<blq>')
@login_required
def api_bloque_camas(blq):
    """
    Retorna el estado vivo de todas las camas del bloque base (blq) en orden físico real:
    - Orden estricto: num_cama ASC, sufijo ASC (nunca orden alfabético por texto).
    - Incluye área neta (metros_netos), área bruta (metros_brutos), cama estándar, largo, ha y veg_max_age.
    """
    with db.engine.connect() as conn:
        query = """
            SELECT 
                m.id_cama,
                m.codigo_bloque,
                m.blq,
                m.sufijo,
                m.num_cama,
                m.cama_estandar,
                m.metros_netos,
                m.metros_brutos,
                m.ha,
                COALESCE(dc.largo_de_cama, 0) AS largo_de_cama,
                m.producto_actual,
                m.variedad_actual,
                m.ultimo_evento,
                m.num_poda_actual,
                to_char(m.fecha_ultimo_evento, 'YYYY-MM-DD') AS fecha_ultimo_evento,
                m.semana_campo_ultimo_evento,
                m.semana_abs_ultimo_evento,
                m.edad_semanas_actual,
                m.num_plantas_actual,
                m.estado_cultivo,
                COALESCE(cr.veg_max_age, CASE WHEN UPPER(m.producto_actual) LIKE '%HYPER%' THEN 12 ELSE 9 END) AS veg_max_age
            FROM mrt_estado_cultivo_actual m
            LEFT JOIN dim_camas dc ON dc.id_cama = m.id_cama
            LEFT JOIN crops cr ON UPPER(cr.name) = UPPER(m.producto_actual)
            WHERE m.blq = :blq
            ORDER BY m.num_cama ASC, m.sufijo ASC;
        """
        rows = conn.execute(text(query), {'blq': blq}).fetchall()
        dens_rows = conn.execute(text("SELECT producto_maestro, plantas_por_cama FROM dim_densidad_plantas;")).fetchall()
        dens_map = {str(dr[0]).strip().upper(): int(dr[1]) for dr in dens_rows if dr[0] and dr[1]}

    camas = []
    for r in rows:
        esta_vacia = (r[12] == 'TUMBA' or r[12] is None or r[10] == 'VACIO' or r[11] == 'ERRADICADO')
        edad_val = r[17] if r[17] is not None else 0
        veg_max = int(r[20]) if r[20] is not None else 9
        if esta_vacia:
            etapa = 'VACIO'
        elif edad_val <= veg_max:
            etapa = 'VEGETATIVO'
        else:
            etapa = 'PRODUCTIVO'

        cama_std = float(r[5]) if r[5] is not None else 1.0
        plantas_val = int(r[18]) if r[18] is not None else 0
        if plantas_val <= 0 and not esta_vacia:
            prod_nom = str(r[10] or '').strip().upper()
            d_val = dens_map.get(prod_nom)
            if not d_val:
                for dk, dv in dens_map.items():
                    if dk in prod_nom or prod_nom in dk:
                        d_val = dv
                        break
            if not d_val:
                d_val = 1350 if 'HYPER' in prod_nom else (400 if 'GYP' in prod_nom else 600)
            plantas_val = int(round(cama_std * d_val))

        camas.append({
            'id_cama': r[0],
            'codigo_bloque': r[1],
            'blq': r[2],
            'sufijo': r[3] or 'A',
            'num_cama': int(r[4]) if r[4] is not None else 0,
            'cama_estandar': cama_std,
            'metros_netos': float(r[6]) if r[6] is not None else 0.0,
            'metros_brutos': float(r[7]) if r[7] is not None else 0.0,
            'ha': float(r[8]) if r[8] is not None else 0.0,
            'largo_de_cama': float(r[9]) if r[9] is not None else 0.0,
            'producto': r[10] or 'VACIO',
            'variedad': r[11] or 'DISPONIBLE',
            'ultimo_evento': r[12] or 'SIN EVENTO',
            'num_poda': r[13] if r[13] is not None else 0,
            'fecha_ultimo_evento': r[14] or '-',
            'semana_campo_ultimo_evento': r[15],
            'semana_abs_ultimo_evento': r[16],
            'edad_semanas': r[17] if r[17] is not None else None,
            'num_plantas': plantas_val,
            'estado_cultivo': r[19] or 'VACIO',
            'veg_max_age': veg_max,
            'etapa_fenologica': etapa,
            'esta_vacia': esta_vacia
        })

    return jsonify(camas)


@tpsr_bp.route('/api/guardar-evento', methods=['POST'])
@login_required
@permission_required('tpsr')
def api_guardar_evento():
    """
    Guarda eventos de campo (SIEMBRA, PODA, TUMBA) y refresca la base de datos en tiempo real.
    Soporta llenado rápido por rangos/particiones (producto, variedad, sufijo/partición, fecha y plantas por cama).
    """
    data = request.get_json() or {}
    tipo_evento_global = data.get('tipo_evento', '').strip().upper()
    blq = data.get('blq', '').strip()
    producto_global = data.get('producto_maestro', '').strip()
    variedad_global = data.get('variedad', '').strip()
    fecha_global_str = data.get('fecha_evento', '').strip()
    semana_campo_param = data.get('semana_campo')
    camas_data = data.get('camas', [])

    # Retrocompatibilidad
    if not camas_data and 'camas_seleccionadas' in data:
        camas_data = [{'id_cama': cid} for cid in data.get('camas_seleccionadas', [])]

    if not tipo_evento_global or not blq or not camas_data:
        return jsonify({'success': False, 'error': 'Faltan campos obligatorios o no seleccionó ninguna cama.'}), 400

    with db.engine.begin() as conn:
        total_guardadas = 0
        total_plantas_guardadas = 0

        for item in camas_data:
            id_cama = item.get('id_cama')
            if not id_cama:
                continue

            tipo_evento = (item.get('tipo_evento') or tipo_evento_global).strip().upper()

            # Obtener fecha de ejecución por cama (o fallback global / hoy)
            fecha_str = item.get('fecha_evento') or fecha_global_str or str(date.today())
            try:
                fecha_evento = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            except ValueError:
                fecha_evento = date.today()

            # Determinar semana agrícola para esta cama
            if semana_campo_param:
                semana_campo_val = int(semana_campo_param)
            else:
                q_sem = text("SELECT semana_campo FROM dim_calendario_agricola WHERE fecha = :f LIMIT 1;")
                res_sem = conn.execute(q_sem, {'f': fecha_evento}).scalar()
                if res_sem:
                    semana_campo_val = int(res_sem)
                else:
                    semana_campo_val = (fecha_evento.year % 100) * 100 + int(fecha_evento.strftime("%W"))

            # Obtener datos físicos y de cultivo actuales de la cama
            q_cama_info = text("""
                SELECT codigo_bloque, sufijo, producto_actual, variedad_actual, num_poda_actual, num_plantas_actual, zona, tecnico_de_riego
                FROM mrt_estado_cultivo_actual
                WHERE id_cama = :id_cama
                LIMIT 1;
            """)
            cama_row = conn.execute(q_cama_info, {'id_cama': id_cama}).fetchone()
            
            if cama_row:
                codigo_bloque = cama_row[0]
                sufijo_actual_cama = cama_row[1] or 'A'
                prod_actual_cama = cama_row[2]
                var_actual_cama = cama_row[3]
                num_poda_actual_cama = cama_row[4] or 0
                plantas_actuales_cama = cama_row[5] or 0
                zona_cama = cama_row[6] or 'SIN ZONA'
                tecnico_cama = cama_row[7] or 'C1'
            else:
                q_dim = text("SELECT codigo_bloque, sufijo, num_plantas, zona, tecnico_de_riego FROM dim_camas WHERE id_cama = :id_cama;")
                dim_row = conn.execute(q_dim, {'id_cama': id_cama}).fetchone()
                codigo_bloque = dim_row[0] if dim_row else f"{blq}A"
                sufijo_actual_cama = dim_row[1] if dim_row and dim_row[1] else 'A'
                prod_actual_cama = 'VACIO'
                var_actual_cama = 'ERRADICADO'
                num_poda_actual_cama = 0
                plantas_actuales_cama = dim_row[2] if dim_row and dim_row[2] else 0
                zona_cama = dim_row[3] if dim_row and dim_row[3] else 'SIN ZONA'
                tecnico_cama = dim_row[4] if dim_row and dim_row[4] else 'C1'

            # Si el usuario especificó una partición/sufijo nuevo (ej. A, B, C...) para esta cama/rango
            nuevo_sufijo = (item.get('sufijo') or '').strip().upper()
            if nuevo_sufijo and nuevo_sufijo != sufijo_actual_cama:
                nuevo_cb = f"{blq}{nuevo_sufijo}"
                conn.execute(text("""
                    INSERT INTO dim_bloques (codigo_bloque, id_finca, blq, sufijo, zona, tecnico_de_riego)
                    VALUES (:cb, 1, :blq, :suf, :z, :t)
                    ON CONFLICT (codigo_bloque) DO NOTHING;
                """), {'cb': nuevo_cb, 'blq': blq, 'suf': nuevo_sufijo, 'z': zona_cama, 't': tecnico_cama})
                conn.execute(text("""
                    UPDATE dim_camas
                    SET sufijo = :suf, codigo_bloque = :cb
                    WHERE id_cama = :id_cama;
                """), {'suf': nuevo_sufijo, 'cb': nuevo_cb, 'id_cama': id_cama})
                codigo_bloque = nuevo_cb

            # Lógica por tipo de evento (prioriza el producto/variedad individual de la partición de la cama)
            if tipo_evento == 'PODA':
                nuevo_num_poda = num_poda_actual_cama + 1
                prod_final = item.get('producto') or prod_actual_cama or producto_global or 'HYPERICUM'
                var_final = item.get('variedad') or var_actual_cama or variedad_global or 'MAGICAL TRIUMPH'
                plantas_final = int(item.get('num_plantas', plantas_actuales_cama))
            elif tipo_evento == 'SIEMBRA':
                nuevo_num_poda = 0
                prod_final = item.get('producto') or producto_global or 'HYPERICUM'
                var_final = item.get('variedad') or variedad_global or 'DESCONOCIDA'
                plantas_final = int(item.get('num_plantas', 0))
            elif tipo_evento == 'TUMBA':
                nuevo_num_poda = None
                prod_final = 'VACIO'
                var_final = 'ERRADICADO'
                plantas_final = 0
            else:
                continue

            # Insertar en fact_eventos_cultivo
            insert_q = text("""
                INSERT INTO fact_eventos_cultivo (
                    id_cama, codigo_bloque, fecha_evento, semana_campo,
                    tipo_evento, num_poda, producto_maestro, variedad,
                    num_plantas, fecha_hora_registro, origen_evento, estado_evento
                ) VALUES (
                    :id_cama, :codigo_bloque, :fecha_evento, :semana_campo,
                    :tipo_evento, :num_poda, :producto, :variedad,
                    :num_plantas, NOW(), 'Registro App TPSR Web', 'ACTIVO'
                );
            """)
            conn.execute(insert_q, {
                'id_cama': id_cama,
                'codigo_bloque': codigo_bloque,
                'fecha_evento': fecha_evento,
                'semana_campo': semana_campo_val,
                'tipo_evento': tipo_evento,
                'num_poda': nuevo_num_poda,
                'producto': prod_final,
                'variedad': var_final,
                'num_plantas': plantas_final
            })

            # Actualizar dimensión física dim_camas con las plantas vivas más recientes
            conn.execute(text("""
                UPDATE dim_camas 
                SET num_plantas = :plantas 
                WHERE id_cama = :id_cama;
            """), {'plantas': plantas_final, 'id_cama': id_cama})

            total_guardadas += 1
            total_plantas_guardadas += plantas_final

        # Sincronizar en tiempo real crop_state_records para fumigación y analítica en la semana del evento
        sync_crop_state_records(conn, semana_target=semana_campo_val)

    return jsonify({
        'success': True,
        'message': f'¡Se registraron con éxito {total_guardadas} eventos en el bloque {blq}! ({total_plantas_guardadas:,} plantas registradas).'
    })


def sync_crop_state_records(conn, semana_target=None):
    """Sincroniza la semana activa en crop_state_records desde la vista viva mrt_estado_cultivo_actual sin borrar semanas históricas."""
    if semana_target:
        sem_actual = str(semana_target).strip()
    else:
        sem_actual = conn.execute(text("SELECT MAX(semana_agricola_actual)::text FROM mrt_estado_cultivo_actual;")).scalar() or '2640'
        sem_actual = str(sem_actual).strip()

    if len(sem_actual) == 4 and sem_actual.isdigit():
        sem_iso = f"20{sem_actual[:2]}-{int(sem_actual[2:]):02d}"
    else:
        sem_iso = sem_actual

    conn.execute(text("DELETE FROM crop_state_records WHERE week IN (:sem_raw, :sem_iso);"), {
        'sem_raw': sem_actual,
        'sem_iso': sem_iso
    })
    conn.execute(text("""
        INSERT INTO crop_state_records (
            week, block_full, block_num, bed_num, suffix, crop_master, 
            product_name, variety, standard_bed, zone, real_age, status_raw, created_at
        )
        SELECT 
            :sem_iso,
            codigo_bloque,
            blq,
            num_cama,
            COALESCE(sufijo, 'A'),
            producto_actual,
            producto_actual,
            variedad_actual,
            cama_estandar,
            zona,
            edad_semanas_actual,
            estado_cultivo,
            NOW()
        FROM mrt_estado_cultivo_actual;
    """), {'sem_iso': sem_iso})


@tpsr_bp.route('/api/habilitar-semana', methods=['POST'])
@login_required
@permission_required('tpsr')
def api_habilitar_semana():
    """
    Habilita una nueva semana en el ERP (Mapping de Campo & Estado de Cultivo).
    Proyecta las camas del corte anterior con edad_real + 1, desbloqueando la semana
    para que los operarios registren siembras, podas y tumbas.
    """
    data = request.get_json() or {}
    raw_semana = str(data.get('semana', '')).strip()
    if not raw_semana:
        return jsonify({'success': False, 'message': 'Semana requerida.'}), 400

    import re
    m = re.search(r'(\d{2,4})[-_]?(\d{1,2})', raw_semana)
    if m:
        p1, p2 = int(m.group(1)), int(m.group(2))
        if p1 >= 2000:
            anio = p1
            sem_num = p2
        elif p1 < 100:
            anio = 2000 + p1
            sem_num = p2
        else:
            anio = 2026
            sem_num = p2
    elif raw_semana.isdigit() and len(raw_semana) == 4:
        anio = 2000 + int(raw_semana[:2])
        sem_num = int(raw_semana[2:])
    elif raw_semana.isdigit() and len(raw_semana) <= 2:
        anio = 2026
        sem_num = int(raw_semana)
    else:
        return jsonify({'success': False, 'message': f'Formato de semana no reconocido: {raw_semana}'}), 400

    semana_campo_val = (anio % 100) * 100 + sem_num
    semana_iso_str = f"{anio}-{sem_num:02d}"

    with db.engine.begin() as conn:
        ya_existe = conn.execute(
            text("SELECT 1 FROM crop_state_records WHERE week IN (:w_iso, :w_raw) LIMIT 1;"),
            {'w_iso': semana_iso_str, 'w_raw': str(semana_campo_val)}
        ).scalar()
        if ya_existe:
            return jsonify({
                'success': True,
                'already_exists': True,
                'semana_campo': semana_campo_val,
                'semana_iso': semana_iso_str,
                'message': f'La semana {semana_campo_val} ({semana_iso_str}) ya se encuentra habilitada.'
            })

        prev_week = conn.execute(text("""
            SELECT week FROM crop_state_records 
            WHERE week IS NOT NULL 
            ORDER BY 
                CASE 
                    WHEN week ~ '^20[0-9]{2}-[0-9]{1,2}$' THEN (SUBSTRING(week, 3, 2)::int * 100 + SUBSTRING(week, 6, 2)::int)
                    WHEN week ~ '^[0-9]{4}$' THEN week::int
                    ELSE 0
                END DESC
            LIMIT 1;
        """)).scalar()

        if not prev_week:
            return jsonify({'success': False, 'message': 'No se encontró un corte previo base para proyectar.'}), 400

        # Calcular delta de semanas entre el corte previo y la semana objetivo
        prev_m = re.search(r'(\d{2,4})[-_]?(\d{1,2})', str(prev_week))
        if prev_m:
            pp1, pp2 = int(prev_m.group(1)), int(prev_m.group(2))
            prev_year = pp1 if pp1 >= 2000 else (2000 + pp1 if pp1 < 100 else 2026)
            prev_sem = pp2
        elif str(prev_week).isdigit() and len(str(prev_week)) == 4:
            prev_year = 2000 + int(str(prev_week)[:2])
            prev_sem = int(str(prev_week)[2:])
        else:
            prev_year = anio
            prev_sem = sem_num - 1

        delta_semanas = ((anio - prev_year) * 52) + (sem_num - prev_sem)
        if delta_semanas < 1:
            delta_semanas = 1

        insert_q = text("""
            INSERT INTO crop_state_records (
                week, block_full, block_num, bed_num, suffix, crop_master,
                product_name, variety, standard_bed, zone, real_age, status_raw, created_at
            )
            SELECT 
                :w_iso,
                block_full,
                block_num,
                bed_num,
                suffix,
                crop_master,
                product_name,
                variety,
                standard_bed,
                zone,
                CASE 
                    WHEN status_raw LIKE '%ERRADICADO%' OR status_raw LIKE '%VAC%' OR crop_master = 'VACIO' THEN NULL
                    ELSE COALESCE(real_age, 0) + :delta_semanas
                END,
                status_raw,
                NOW()
            FROM crop_state_records
            WHERE week = :prev_w;
        """)
        res = conn.execute(insert_q, {'w_iso': semana_iso_str, 'prev_w': prev_week, 'delta_semanas': delta_semanas})
        count_projected = res.rowcount

        record_audit('TPSR', 'HABILITAR_SEMANA', 'CropStateRecord', semana_campo_val,
                     details=f"Semana {semana_campo_val} ({semana_iso_str}) habilitada con {count_projected} camas proyectadas desde {prev_week} (Delta +{delta_semanas} sem).")

    return jsonify({
        'success': True,
        'semana_campo': semana_campo_val,
        'semana_iso': semana_iso_str,
        'count': count_projected,
        'delta_semanas': delta_semanas,
        'message': f'¡Semana {semana_campo_val} ({semana_iso_str}) habilitada con éxito! Se proyectaron {count_projected} camas desde la semana {prev_week} (+{delta_semanas} sem de edad).'
    })


@tpsr_bp.route('/api/historial-eventos', methods=['GET'])
@login_required
@permission_required('tpsr')
def api_historial_eventos():
    """Retorna los últimos 60 eventos registrados para consulta y corrección por Admin."""
    blq = request.args.get('blq', '').strip()
    with db.engine.connect() as conn:
        q = """
            SELECT 
                e.id_evento,
                e.id_cama,
                e.codigo_bloque,
                to_char(e.fecha_evento, 'YYYY-MM-DD') AS fecha_evento,
                e.semana_campo,
                e.tipo_evento,
                e.num_poda,
                e.producto_maestro,
                e.variedad,
                e.num_plantas,
                to_char(e.fecha_hora_registro, 'YYYY-MM-DD HH24:MI') AS fecha_hora_registro,
                e.estado_evento,
                COALESCE(e.motivo_anulacion, '') AS motivo_anulacion
            FROM fact_eventos_cultivo e
        """
        params = {}
        if blq:
            q += " WHERE e.codigo_bloque LIKE :blq"
            params['blq'] = f"{blq}%"
        q += " ORDER BY e.id_evento DESC LIMIT 60;"
        rows = conn.execute(text(q), params).fetchall()

    eventos = [
        {
            'id_evento': r[0],
            'id_cama': r[1],
            'codigo_bloque': r[2],
            'fecha_evento': r[3],
            'semana_campo': r[4],
            'tipo_evento': r[5],
            'num_poda': r[6],
            'producto_maestro': r[7],
            'variedad': r[8],
            'num_plantas': r[9],
            'fecha_registro': r[10],
            'estado_evento': r[11],
            'motivo': r[12]
        }
        for r in rows
    ]
    return jsonify(eventos)


@tpsr_bp.route('/api/anular-evento', methods=['POST'])
@login_required
@admin_required
def api_anular_evento():
    """Anula un evento erróneo de campo y restaura el estado previo de la cama en mrt y crop_state_records."""
    data = request.get_json() or {}
    id_evento = data.get('id_evento')
    motivo = data.get('motivo', 'Anulación manual por Administrador').strip()

    if not id_evento:
        return jsonify({'success': False, 'error': 'ID de evento obligatorio.'}), 400

    with db.engine.begin() as conn:
        q_find = text("SELECT id_cama, tipo_evento, estado_evento FROM fact_eventos_cultivo WHERE id_evento = :id;")
        ev = conn.execute(q_find, {'id': id_evento}).fetchone()
        if not ev:
            return jsonify({'success': False, 'error': 'Evento no encontrado.'}), 404
        if ev[2] == 'ANULADO':
            return jsonify({'success': False, 'error': 'Este evento ya se encuentra anulado.'}), 400

        id_cama = ev[0]
        tipo_ev = ev[1]

        # Anular evento
        q_update = text("""
            UPDATE fact_eventos_cultivo 
            SET estado_evento = 'ANULADO', 
                fecha_anulacion = NOW(), 
                motivo_anulacion = :motivo 
            WHERE id_evento = :id;
        """)
        conn.execute(q_update, {'id': id_evento, 'motivo': motivo})

        # Restaurar plantas en dim_camas si era siembra o tumba
        q_prev_plants = text("""
            SELECT num_plantas 
            FROM fact_eventos_cultivo 
            WHERE id_cama = :id_cama AND estado_evento = 'ACTIVO' 
            ORDER BY fecha_evento DESC, id_evento DESC 
            LIMIT 1;
        """)
        prev_p = conn.execute(q_prev_plants, {'id_cama': id_cama}).scalar() or 0
        conn.execute(text("UPDATE dim_camas SET num_plantas = :p WHERE id_cama = :id_cama;"), {'p': prev_p, 'id_cama': id_cama})

        # Sincronizar analítica viva
        sync_crop_state_records(conn)

    return jsonify({
        'success': True,
        'message': f'Evento #{id_evento} ({tipo_ev} en {id_cama}) anulado exitosamente. La cama regresó a su estado agronómico anterior.'
    })


@tpsr_bp.route('/api/crear-cama', methods=['POST'])
@login_required
@admin_required
def api_crear_cama():
    """Crea una nueva cama física en un bloque existente para aumentar camas durante el año."""
    data = request.get_json() or {}
    blq = str(data.get('blq', '')).strip()
    sufijo = str(data.get('sufijo', 'A')).strip().upper()
    try:
        num_cama = int(data.get('num_cama'))
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'El número de cama debe ser un entero válido.'}), 400

    cama_std = float(data.get('cama_estandar', 1.0) or 1.0)
    metros_netos = float(data.get('metros_netos', 30.0) or 30.0)
    
    if not blq or not num_cama:
        return jsonify({'success': False, 'error': 'Bloque y número de cama son obligatorios.'}), 400

    codigo_bloque = f"{blq}{sufijo}"
    id_cama = f"{codigo_bloque}-{sufijo}-C{num_cama}"

    with db.engine.begin() as conn:
        # Verificar si bloque existe
        q_b = text("SELECT zona, tecnico_de_riego FROM dim_bloques WHERE codigo_bloque = :cb LIMIT 1;")
        brow = conn.execute(q_b, {'cb': codigo_bloque}).fetchone()
        if not brow:
            # Intentar con blq base
            q_b2 = text("SELECT zona, tecnico_de_riego FROM dim_bloques WHERE blq = :b LIMIT 1;")
            brow = conn.execute(q_b2, {'b': blq}).fetchone()
            zona = brow[0] if brow else 'ZONA UNIFICADA'
            tecnico = brow[1] if brow else 'C1'
            # Insertar dim_bloques si no existe
            conn.execute(text("""
                INSERT INTO dim_bloques (codigo_bloque, id_finca, blq, sufijo, zona, tecnico_de_riego)
                VALUES (:cb, 1, :blq, :suf, :z, :t)
                ON CONFLICT (codigo_bloque) DO NOTHING;
            """), {'cb': codigo_bloque, 'blq': blq, 'suf': sufijo, 'z': zona, 't': tecnico})
        else:
            zona = brow[0]
            tecnico = brow[1]

        # Verificar si ya existe id_cama
        q_exists = text("SELECT 1 FROM dim_camas WHERE id_cama = :id;")
        if conn.execute(q_exists, {'id': id_cama}).fetchone():
            return jsonify({'success': False, 'error': f'La cama {id_cama} ya existe en el sistema.'}), 400

        # Insertar en dim_camas
        insert_cama = text("""
            INSERT INTO dim_camas (
                id_cama, codigo_bloque, id_finca, blq, sufijo, num_cama,
                cama_estandar, metros_netos, metros_brutos, zona, tecnico_de_riego
            ) VALUES (
                :id_cama, :codigo_bloque, 1, :blq, :sufijo, :num_cama,
                :cama_std, :metros_netos, :metros_netos, :zona, :tecnico
            );
        """)
        conn.execute(insert_cama, {
            'id_cama': id_cama,
            'codigo_bloque': codigo_bloque,
            'blq': blq,
            'sufijo': sufijo,
            'num_cama': num_cama,
            'cama_std': cama_std,
            'metros_netos': metros_netos,
            'zona': zona,
            'tecnico': tecnico
        })

        # Actualizar agregados de dim_bloques
        conn.execute(text("""
            UPDATE dim_bloques 
            SET total_camas_fisicas = (SELECT COUNT(*) FROM dim_camas WHERE codigo_bloque = :cb),
                total_camas_estandar = (SELECT COALESCE(SUM(cama_estandar), 0) FROM dim_camas WHERE codigo_bloque = :cb),
                area_neta_m2 = (SELECT COALESCE(SUM(metros_netos), 0) FROM dim_camas WHERE codigo_bloque = :cb)
            WHERE codigo_bloque = :cb;
        """), {'cb': codigo_bloque})

        # Sincronizar analítica viva
        sync_crop_state_records(conn)

    return jsonify({
        'success': True,
        'message': f'Cama {id_cama} (Bloque {codigo_bloque}, Cama #{num_cama}) agregada exitosamente al catálogo.'
    })


@tpsr_bp.route('/api/eventos-semana', methods=['GET'])
@login_required
@permission_required('tpsr')
def api_eventos_semana():
    """Retorna los eventos detallados para la matriz interactiva tipo Excel."""
    semana = request.args.get('semana', type=int)
    producto = request.args.get('producto', '').strip()
    tipo_evento = request.args.get('tipo_evento', '').strip()
    estado = request.args.get('estado', 'ACTIVO').strip()

    if not semana:
        return jsonify({'error': 'Parámetro semana requerido'}), 400

    with db.engine.connect() as conn:
        q = """
            SELECT 
                e.id_evento,
                e.id_cama,
                e.codigo_bloque,
                to_char(e.fecha_evento, 'YYYY-MM-DD') AS fecha_evento,
                e.semana_campo,
                e.tipo_evento,
                COALESCE(e.num_poda, 1) AS num_poda,
                COALESCE(e.producto_maestro, '') AS producto_maestro,
                COALESCE(e.variedad, '') AS variedad,
                COALESCE(e.num_plantas, 0) AS num_plantas,
                e.estado_evento,
                to_char(e.fecha_hora_registro, 'YYYY-MM-DD HH24:MI') AS fecha_hora_registro
            FROM fact_eventos_cultivo e
            WHERE e.semana_campo = :semana
        """
        params = {'semana': semana}
        if producto and producto != 'ALL':
            q += " AND e.producto_maestro = :producto"
            params['producto'] = producto
        if tipo_evento and tipo_evento != 'ALL':
            q += " AND e.tipo_evento = :tipo_evento"
            params['tipo_evento'] = tipo_evento
        if estado and estado != 'ALL':
            q += " AND e.estado_evento = :estado"
            params['estado'] = estado

        q += " ORDER BY e.codigo_bloque, e.id_cama, e.id_evento DESC;"
        rows = conn.execute(text(q), params).fetchall()

    eventos = [
        {
            'id_evento': r[0],
            'id_cama': r[1],
            'codigo_bloque': r[2],
            'fecha_evento': r[3],
            'semana_campo': r[4],
            'tipo_evento': r[5],
            'num_poda': r[6],
            'producto_maestro': r[7],
            'variedad': r[8],
            'num_plantas': r[9],
            'estado_evento': r[10],
            'fecha_registro': r[11]
        }
        for r in rows
    ]
    return jsonify({
        'success': True,
        'total': len(eventos),
        'eventos': eventos
    })


@tpsr_bp.route('/api/editar-evento', methods=['POST'])
@login_required
@admin_required
def api_editar_evento():
    """Permite al Administrador corregir directamente un evento (plantas, variedad, poda, fecha)."""
    data = request.get_json() or {}
    id_evento = data.get('id_evento')
    num_plantas = data.get('num_plantas')
    variedad = data.get('variedad', '').strip()
    num_poda = data.get('num_poda')
    fecha_evento = data.get('fecha_evento', '').strip()

    if not id_evento:
        return jsonify({'success': False, 'error': 'ID de evento obligatorio.'}), 400

    with db.engine.begin() as conn:
        q_find = text("SELECT id_cama, tipo_evento, estado_evento FROM fact_eventos_cultivo WHERE id_evento = :id;")
        ev = conn.execute(q_find, {'id': id_evento}).fetchone()
        if not ev:
            return jsonify({'success': False, 'error': 'Evento no encontrado.'}), 404
        if ev[2] == 'ANULADO':
            return jsonify({'success': False, 'error': 'No se puede editar un evento anulado.'}), 400

        id_cama = ev[0]
        tipo_ev = ev[1]

        updates = []
        params = {'id': id_evento}
        if num_plantas is not None:
            updates.append("num_plantas = :num_plantas")
            params['num_plantas'] = int(num_plantas)
        if variedad:
            updates.append("variedad = :variedad")
            params['variedad'] = variedad
        if num_poda is not None:
            updates.append("num_poda = :num_poda")
            params['num_poda'] = int(num_poda)
        if fecha_evento:
            updates.append("fecha_evento = :fecha_evento")
            params['fecha_evento'] = fecha_evento

        if updates:
            q_up = text(f"UPDATE fact_eventos_cultivo SET {', '.join(updates)} WHERE id_evento = :id;")
            conn.execute(q_up, params)

            if num_plantas is not None and tipo_ev in ('SIEMBRA', 'PODA'):
                conn.execute(text("UPDATE dim_camas SET num_plantas = :p WHERE id_cama = :id_cama;"), {'p': int(num_plantas), 'id_cama': id_cama})

            sync_crop_state_records(conn)

    return jsonify({
        'success': True,
        'message': f'Evento #{id_evento} actualizado correctamente por Administrador.'
    })
