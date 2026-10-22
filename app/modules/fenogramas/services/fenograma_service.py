import os
import re
import json
from datetime import datetime
import pandas as pd
from sqlalchemy import text, bindparam
from app.extensions import db
from app.modules.fenogramas.models import (
    SemanaEvaluacion, MatrizFenograma, PlanoCultivo, Calificacion
)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config_mapeos.json")

def load_mapeos_config():
    default_config = {
        "supervisor_aliases": {
            "MARIA DOLORES": "Maria Dolores Perugachi",
            "MARIA DOLORES PERUGACHI": "Maria Dolores Perugachi"
        },
        "auxiliar_aliases": {
            "PATRCIO CEVILLA": "Patricio Sevilla",
            "KEVIN BUTRON": "Kevin Buitron"
        },
        "producto_aliases": {
            "BILLION LIGHT": ["BILLION LIGHT", "BILLION LIGHTS", "GYPSOPHILA", "GYPSO XLENCE", "MILLION STAR II", "SMALL BLOOM"],
            "BILLION LIGHTS": ["BILLION LIGHTS", "BILLION LIGHT", "GYPSOPHILA", "GYPSO XLENCE", "MILLION STAR II", "SMALL BLOOM"],
            "GYPSOPHILA": ["GYPSOPHILA", "BILLION LIGHT", "BILLION LIGHTS", "GYPSO XLENCE", "MILLION STAR II", "SMALL BLOOM"],
            "GYPSO XLENCE": ["GYPSO XLENCE", "GYPSOPHILA", "BILLION LIGHT", "BILLION LIGHTS", "MILLION STAR II", "SMALL BLOOM"],
            "MILLION STAR II": ["MILLION STAR II", "GYPSOPHILA", "BILLION LIGHT", "BILLION LIGHTS", "GYPSO XLENCE", "SMALL BLOOM"],
            "VERONICA": ["VERONICA", "VERONICAS", "VERÓNICA", "VERÓNICAS"],
            "VERONICA SPLASH": ["VERONICA SPLASH", "VERONICAS SPLASH", "VERÓNICA SPLASH", "VERÓNICAS SPLASH"]
        }
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning reading config_mapeos.json: {e}")
    return default_config

def save_mapeos_config(config_data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4, ensure_ascii=False)

def clean_str(s):
    if pd.isna(s) or s is None:
        return ""
    s = str(s).strip()
    # Reemplazo de ligaduras tipográficas Unicode (fi, fl, ff, etc.)
    s = s.replace('\ufb01', 'fi').replace('\ufb02', 'fl').replace('\ufb00', 'ff').replace('\ufb03', 'ffi').replace('\ufb04', 'ffl')
    s = re.sub(r'[\xa0\xfeff\ufeff\u200b]+', ' ', s)
    s = re.sub(r'^[^\w\(\)"\']+', '', s)
    s = re.sub(r'[\s,\.]+$', '', s)
    s = re.sub(r'\s+', ' ', s)

    # Corrección de palabras agrícolas comunes donde la ligadura fi/fl/f se convirtió en espacio o se perdió en Excel
    correcciones = [
        (r'\b[sS]ul\s+at([oa]s?)\b', r'sulfat\1'),
        (r'\b[sS]ul\s*at([oa]s?)\b', r'sulfat\1'),
        (r'\b(?:[oó]rmula|f[oó]rmula)?\s*Mosh[eé]?\b', 'Fórmula de Moshe'),
        (r'\b[oó]rmul([ao]s?)\b', r'fórmul\1'),
        (r'\beda\s*ic([oa]s?)\b', r'edáfic\1'),
        (r'\b[uú]lminante\b', 'fulminante'),
        (r'\bin\s*lorescencia\b', 'inflorescencia'),
        (r'\b([ií]los?)\s+(de\s+camas?|y\s+camas?)\b', r'f\1 \2'),
        (r'\bde\s+ilos\b', 'de filos'),
        (r'\blimpieza\s+de\s+ilos\b', 'limpieza de filos'),
        (r'\bescari\s*icado\b', 'escarificado'),
        (r'\bDesin\s*ecci[oó]n\b', 'Desinfección'),
        (r'\bdesin\s*ecci[oó]n\b', 'desinfección'),
        (r'\b[ií]tosanitari([oa]s?)\b', r'fitosanitari\1'),
        (r'\ba\s+ectad([oa]s?)\b', r'afectad\1'),
        (r'\bcon\s*inamiento\b', 'confinamiento'),
        (r'\b[oó]liar\b', 'foliar'),
        (r'\b[oó]llaje\b', 'follaje'),
        (r'\b[ií]tof[aá]rmaco\b', 'fitofármaco'),
        (r'\b[ií]totoxicidad\b', 'fitotoxicidad'),
        (r'\bde\s*oliaci[oó]n\b', 'defoliación'),
        (r'\b[oó]sforo\b', 'fósforo'),
        (r'\b[oó]sforad([oa]s?)\b', r'fosforad\1'),
        (r'\bFincalizaci[oó]n\b', 'Finalización')
    ]
    for pattern, repl in correcciones:
        s = re.sub(pattern, repl, s, flags=re.IGNORECASE)

    # Normalizaciones directas de mayúsculas en términos recurrentes
    s = s.replace('Sul ato', 'Sulfato').replace('sul ato', 'sulfato')
    s = s.replace('Sulfato de magnesio', 'Sulfato de Magnesio').replace('sulfato de magnesio', 'sulfato de magnesio')

    return s.strip()

def clean_block_code(s):
    """Elimina prefijos repetidos como BL, BLQ, BLOQUE, MOD, MODULO para dejar solo el código limpio del bloque (ej. 71, 13A)."""
    val = clean_str(s)
    while True:
        new_val = re.sub(r'^(?:BLOQUE|BLQ|BL|MODULO|MOD)[\s.\-_]*', '', val, flags=re.IGNORECASE).strip()
        if new_val == val or not new_val:
            break
        val = new_val
    return val or clean_str(s)

def normalize_key(s):
    if not s:
        return ""
    s = clean_str(s).upper()
    replacements = [('Ó', 'O'), ('Ö', 'O'), ('É', 'E'), ('Á', 'A'), ('Í', 'I'), ('Ú', 'U'), ('Ñ', 'N')]
    for old, new in replacements:
        s = s.replace(old, new)
    return s

def normalize_product_name(p):
    norm = normalize_key(p)
    if 'VERONICA SPLASH' in norm or 'VERONICAS SPLASH' in norm:
        return 'VERONICA SPLASH'
    if norm in ['VERONICA', 'VERONICAS', 'VERÓNICA', 'VERÓNICAS']:
        return 'VERONICA'
    if any(k in norm for k in ['BILLION', 'GYPSO', 'XLENCE', 'MILLION', 'SMALL BLOOM', 'GYPSOPHILA']):
        if 'BILLION' in norm: return 'BILLION LIGHTS'
        if 'XLENCE' in norm or 'GYPSO' in norm: return 'GYPSO XLENCE'
        if 'MILLION' in norm: return 'MILLION STAR II'
        if 'SMALL' in norm: return 'SMALL BLOOM'
        return 'GYPSOPHILA'
    return norm

def map_person_name(name, alias_dict):
    cleaned = clean_str(name)
    if not cleaned:
        return ""
    norm = normalize_key(cleaned)
    if norm in alias_dict:
        return alias_dict[norm]
    if ',' in cleaned:
        parts = [clean_str(p) for p in cleaned.split(',')]
        mapped_parts = [alias_dict.get(normalize_key(p), p) for p in parts if p]
        return ", ".join(mapped_parts)
    return cleaned

def get_matching_matrix_products(ec_producto, producto_aliases):
    norm_ec = normalize_product_name(ec_producto)
    if norm_ec == 'VERONICA':
        return ['VERONICA', 'VERONICAS', 'VERÓNICA', 'VERÓNICAS']
    if norm_ec == 'VERONICA SPLASH':
        return ['VERONICA SPLASH', 'VERONICAS SPLASH', 'VERÓNICA SPLASH', 'VERÓNICAS SPLASH']
    if norm_ec in producto_aliases:
        mapped = [normalize_key(p) for p in producto_aliases[norm_ec]]
        if norm_ec in mapped:
            mapped.remove(norm_ec)
        return [norm_ec] + mapped
    if any(k in norm_ec for k in ['BILLION', 'GYPSO', 'XLENCE', 'MILLION', 'SMALL BLOOM', 'GYPSOPHILA']):
        return [norm_ec, 'GYPSOPHILA', 'BILLION LIGHT', 'BILLION LIGHTS', 'GYPSO XLENCE', 'MILLION STAR II', 'SMALL BLOOM']
    return [norm_ec]

def find_responsible_for_labor(producto_maestro, labor, default_aux='', default_sup=''):
    """
    Busca auxiliar y supervisor asignado a una labor en matriz_fenogramas,
    resolviendo alias de productos (Gypsophila -> Billion Light, etc.)
    y con fallback por labor.
    """
    if not labor or str(labor).strip() in ('', 'No hay actividad'):
        return (default_aux or '').strip(), (default_sup or '').strip()
        
    labor_clean = str(labor).strip()
    config = load_mapeos_config()
    prod_aliases = {normalize_key(k): v for k, v in config.get("producto_aliases", {}).items()}
    
    # Obtener lista de nombres/alias posibles para el producto
    target_prods = get_matching_matrix_products(producto_maestro or '', prod_aliases)
    if producto_maestro:
        target_prods.append(str(producto_maestro).strip())
    
    # Expandir con variantes mayúsculas y con/sin S final
    expanded_prods = set()
    for p in target_prods:
        p_up = p.upper().strip()
        expanded_prods.add(p_up)
        if p_up.endswith('S'):
            expanded_prods.add(p_up[:-1])
        else:
            expanded_prods.add(p_up + 'S')
            
    prods_list = list(expanded_prods)
    
    # 1. Match exacto por producto alias + labor
    row = db.session.execute(text("""
        SELECT auxiliar, supervisor
        FROM matriz_fenogramas
        WHERE UPPER(producto) = ANY(:prods) AND UPPER(labor) = UPPER(:labor)
        LIMIT 1;
    """), {'prods': prods_list, 'labor': labor_clean}).fetchone()
    
    if row and (row.auxiliar or row.supervisor):
        return (row.auxiliar or default_aux or '').strip(), (row.supervisor or default_sup or '').strip()
        
    # 2. Match parcial por producto (LIKE) + labor
    row_part = db.session.execute(text("""
        SELECT auxiliar, supervisor
        FROM matriz_fenogramas
        WHERE (UPPER(producto) LIKE :p_like OR UPPER(:p_input) LIKE '%' || UPPER(producto) || '%')
          AND UPPER(labor) = UPPER(:labor)
        LIMIT 1;
    """), {
        'p_like': f"%{prods_list[0]}%" if prods_list else '%',
        'p_input': (producto_maestro or '').upper().strip(),
        'labor': labor_clean
    }).fetchone()
    
    if row_part and (row_part.auxiliar or row_part.supervisor):
        return (row_part.auxiliar or default_aux or '').strip(), (row_part.supervisor or default_sup or '').strip()

    # 3. Fallback: match por nombre de labor en cualquier producto donde haya personal asignado
    row_fb = db.session.execute(text("""
        SELECT auxiliar, supervisor
        FROM matriz_fenogramas
        WHERE UPPER(labor) = UPPER(:labor) 
          AND auxiliar IS NOT NULL AND auxiliar != ''
        LIMIT 1;
    """), {'labor': labor_clean}).fetchone()
    
    if row_fb and (row_fb.auxiliar or row_fb.supervisor):
        return (row_fb.auxiliar or default_aux or '').strip(), (row_fb.supervisor or default_sup or '').strip()

    return (default_aux or '').strip(), (default_sup or '').strip()

def extract_week_from_ec(ec_file_path):
    try:
        df_raw = pd.read_excel(ec_file_path, sheet_name='DATOS', header=None, nrows=6)
        val = df_raw.iloc[3, 4]  # Row 4 (index 3), Col E (index 4)
        val_str = str(val).strip()
        m = re.search(r'(\d{2})(\d{2})', val_str)
        if m:
            anio_short = int(m.group(1))
            semana = int(m.group(2))
            anio = 2000 + anio_short
            return semana, anio, val_str
    except Exception as e:
        print(f"Error reading E4: {e}")
    return 30, 2026, "2630"

def process_matriz_fenogramas(mf_file_path, config):
    xl_mf = pd.ExcelFile(mf_file_path)
    matrix_rules = []
    sup_aliases = {normalize_key(k): v for k, v in config.get("supervisor_aliases", {}).items()}
    aux_aliases = {normalize_key(k): v for k, v in config.get("auxiliar_aliases", {}).items()}

    for sheet_name in xl_mf.sheet_names:
        df_raw = pd.read_excel(mf_file_path, sheet_name=sheet_name, header=None)
        header_idx = None
        for idx, row in df_raw.iterrows():
            row_vals = [str(v).strip().lower() for v in row.values if pd.notna(v)]
            if any('semana' in v for v in row_vals) and any('labor' in v for v in row_vals):
                header_idx = idx
                break

        if header_idx is None:
            continue

        df_sheet = pd.read_excel(mf_file_path, sheet_name=sheet_name, header=header_idx)
        col_map = {}
        for col in df_sheet.columns:
            col_str = str(col).strip()
            col_lower = col_str.lower()
            if 'semana' in col_lower:
                col_map[col] = 'SEMANA'
            elif 'labor' in col_lower:
                col_map[col] = 'LABORES'
            elif 'auxiliar' in col_lower:
                col_map[col] = 'AUXILIAR'
            elif 'supervisor' in col_lower:
                col_map[col] = 'SUPERVISOR'

        df_sheet.rename(columns=col_map, inplace=True)
        if 'SEMANA' not in df_sheet.columns or 'LABORES' not in df_sheet.columns:
            continue

        if 'SEMANA' in df_sheet.columns:
            df_sheet['SEMANA'] = df_sheet['SEMANA'].ffill()
        if 'AUXILIAR' in df_sheet.columns:
            df_sheet['AUXILIAR'] = df_sheet['AUXILIAR'].ffill()
        if 'SUPERVISOR' in df_sheet.columns:
            df_sheet['SUPERVISOR'] = df_sheet['SUPERVISOR'].ffill()

        df_sheet = df_sheet.dropna(subset=['LABORES']).copy()

        parts = sheet_name.strip().split()
        if 'poda' in sheet_name.lower():
            actividad_fen = 'Poda'
            producto_fen = " ".join([p for p in parts if p.lower() != 'poda']).strip()
        elif 'siembra' in sheet_name.lower():
            actividad_fen = 'Siembra'
            producto_fen = " ".join([p for p in parts if p.lower() != 'siembra']).strip()
        else:
            actividad_fen = 'Siembra'
            producto_fen = sheet_name.strip()

        producto_fen_norm = normalize_product_name(producto_fen)
        if producto_fen_norm == 'VERONICA':
            producto_fen = 'VERONICA'

        for _, r in df_sheet.iterrows():
            labor = clean_str(r['LABORES'])
            if not labor:
                continue
            semana = pd.to_numeric(r['SEMANA'], errors='coerce')
            if pd.isna(semana):
                continue

            aux = map_person_name(r.get('AUXILIAR', ''), aux_aliases)
            sup = map_person_name(r.get('SUPERVISOR', ''), sup_aliases)

            matrix_rules.append({
                'sheet_name': sheet_name,
                'producto': producto_fen,
                'producto_norm': normalize_key(producto_fen_norm),
                'actividad_tipo': actividad_fen,
                'actividad_norm': normalize_key(actividad_fen),
                'semana': int(semana),
                'labor': labor,
                'auxiliar': aux,
                'supervisor': sup
            })

    return pd.DataFrame(matrix_rules)

def consolidar_camas_cultivo_df(df_ec):
    """
    Consolida las camas de cultivo a nivel de cama física (NUM_CAMA),
    asigna el cultivo comercial predominante del bloque a los micro-códigos de prueba (PROD.NUEVOS),
    y fusiona camas contiguas que compartan (actividad, producto_maestro, edad_real).
    """
    if df_ec.empty:
        return pd.DataFrame(columns=['ZONA', 'BLOQUE', 'ACTIVIDAD', 'PRODUCTO_MAESTRO', 'CAMA_INICIAL', 'CAMA_FINAL', 'SUFIJO', 'EDAD_REAL'])

    df_ec = df_ec.copy()
    df_ec.dropna(subset=['BLOQUE', 'NUM_CAMA', 'EDAD_REAL'], inplace=True)
    df_ec['EDAD_REAL'] = pd.to_numeric(df_ec['EDAD_REAL'], errors='coerce')
    df_ec['NUM_CAMA'] = pd.to_numeric(df_ec['NUM_CAMA'], errors='coerce')
    df_ec.dropna(subset=['EDAD_REAL', 'NUM_CAMA'], inplace=True)
    df_ec['EDAD_REAL'] = df_ec['EDAD_REAL'].astype(int)
    df_ec['NUM_CAMA'] = df_ec['NUM_CAMA'].astype(int)

    resultados = []
    grupos = df_ec.groupby(['ZONA', 'BLOQUE'], sort=False)

    for (zona, bloque), grupo in grupos:
        grupo = grupo.sort_values(['NUM_CAMA', 'SUFIJO']).reset_index(drop=True)
        grupo['SUFIJO'] = grupo['SUFIJO'].fillna('').astype(str).str.strip()
        grupo['ACTIVIDAD'] = grupo['ACTIVIDAD'].fillna('').astype(str).str.strip()
        grupo['PRODUCTO_MAESTRO'] = grupo['PRODUCTO_MAESTRO'].fillna('').astype(str).str.strip()

        # Determinar cultivo comercial predominante del bloque (ej. HYPERICUM, VERONICA, etc.)
        prods_validos = [p for p in grupo['PRODUCTO_MAESTRO'] if p and p not in ('PROD.NUEVOS', 'VACIO', 'DISPONIBLE', 'ERRADICADO', 'DESCONOCIDA')]
        prod_predominante = pd.Series(prods_validos).mode()[0] if len(prods_validos) > 0 else None

        # Consolidar a nivel de cama física para unificar micro-sufijos y códigos de ensayos
        cama_records = []
        for num_cama, c_group in grupo.groupby('NUM_CAMA', sort=True):
            p_cand = c_group['PRODUCTO_MAESTRO'].tolist()
            # Si la cama tiene el cultivo comercial o códigos PROD.NUEVOS que comparten la cama, hereda el cultivo comercial
            p_val = prod_predominante if (prod_predominante and prod_predominante in p_cand) else (p_cand[0] if p_cand else '')
            act_cand = c_group['ACTIVIDAD'].tolist()
            act_val = act_cand[0] if act_cand else 'Siembra'

            # Edad de la variedad comercial o la moda del segmento
            comercial_rows = c_group[c_group['PRODUCTO_MAESTRO'] == prod_predominante]
            if len(comercial_rows) > 0:
                edad_val = int(comercial_rows['EDAD_REAL'].iloc[0])
            else:
                edad_val = int(c_group['EDAD_REAL'].mode()[0])

            sufs = sorted(set(s for s in c_group['SUFIJO'] if s))
            suf_str = sufs[0] if len(sufs) == 1 else (f"{sufs[0]}-{sufs[-1]}" if sufs else '')

            cama_records.append({
                'num_cama': int(num_cama),
                'actividad': act_val,
                'producto_maestro': p_val,
                'edad_real': edad_val,
                'sufijo': suf_str
            })

        # Fusionar camas contiguas que compartan actividad, producto maestro y edad real
        seg_start = 0
        for i in range(1, len(cama_records) + 1):
            is_end = (i == len(cama_records))
            if not is_end:
                prev_c = cama_records[i - 1]
                curr_c = cama_records[i]
                same = (
                    curr_c['num_cama'] == prev_c['num_cama'] + 1 and
                    curr_c['actividad'] == cama_records[seg_start]['actividad'] and
                    curr_c['producto_maestro'] == cama_records[seg_start]['producto_maestro'] and
                    curr_c['edad_real'] == cama_records[seg_start]['edad_real']
                )
            else:
                same = False

            if not same:
                c_ini = cama_records[seg_start]['num_cama']
                c_fin = cama_records[i - 1]['num_cama']
                s_ini = cama_records[seg_start]['sufijo']
                s_fin = cama_records[i - 1]['sufijo']
                suf_final = s_ini if (c_ini == c_fin or s_ini == s_fin) else ''

                prod_final = cama_records[seg_start]['producto_maestro']
                prod_norm = normalize_product_name(prod_final)
                if prod_norm == 'VERONICA':
                    prod_final = 'VERONICA'

                resultados.append({
                    'ZONA': clean_str(zona),
                    'BLOQUE': clean_block_code(bloque),
                    'ACTIVIDAD': clean_str(cama_records[seg_start]['actividad']),
                    'PRODUCTO_MAESTRO': clean_str(prod_final),
                    'CAMA_INICIAL': c_ini,
                    'CAMA_FINAL': c_fin,
                    'SUFIJO': suf_final,
                    'EDAD_REAL': cama_records[seg_start]['edad_real']
                })
                seg_start = i

    return pd.DataFrame(resultados)

def process_estado_cultivo(ec_file_path):
    df_ec = pd.read_excel(ec_file_path, sheet_name='DATOS', header=4)
    renombrar = {
        'BLOQUES2': 'BLOQUE',
        '# Cama': 'NUM_CAMA',
        'SUFIJO': 'SUFIJO',
        'Ac': 'ACTIVIDAD',
        'VARIEDADES ELITE': 'VARIEDAD_ELITE',
        'ZONA': 'ZONA',
        'AREA PRODUCTO COMERCIAL': 'PRODUCTO_MAESTRO',
        'EDAD REAL': 'EDAD_REAL'
    }
    available_cols = [k for k in renombrar.keys() if k in df_ec.columns]
    df_ec = df_ec[available_cols].rename(columns={k: renombrar[k] for k in available_cols})
    return consolidar_camas_cultivo_df(df_ec)

def preview_excel_sync(ec_file_path, mf_file_path=None):
    if not mf_file_path or not os.path.exists(mf_file_path):
        mf_file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "matriz fenogramas.xlsx")
        if not os.path.exists(mf_file_path):
            mf_file_path = None

    semana, anio, raw_code = extract_week_from_ec(ec_file_path)
    config = load_mapeos_config()
    prod_aliases = {normalize_key(k): v for k, v in config.get("producto_aliases", {}).items()}

    df_matrix_rules = process_matriz_fenogramas(mf_file_path, config)
    df_plano = process_estado_cultivo(ec_file_path)

    all_matrix_prods = set(df_matrix_rules['producto_norm'].unique())
    matched_count = 0
    unmatched_count = 0

    for _, row in df_plano.iterrows():
        target_prods = get_matching_matrix_products(row['PRODUCTO_MAESTRO'], prod_aliases)
        has_matrix = any(tp in all_matrix_prods for tp in target_prods)
        if not has_matrix:
            unmatched_count += 1
            continue

        act_norm = normalize_key(row['ACTIVIDAD'])
        edad = row['EDAD_REAL']

        matches = df_matrix_rules[
            (df_matrix_rules['producto_norm'].isin(target_prods)) &
            (df_matrix_rules['semana'] == edad) &
            (
                (df_matrix_rules['actividad_norm'] == act_norm) |
                (df_matrix_rules['actividad_norm'] == 'TODOS')
            )
        ]

        if len(matches) == 0:
            unmatched_count += 1
        else:
            matched_count += len(matches)

    existing_records = db.session.query(Calificacion).filter_by(semana_anio=semana).count()
    existing_ratings = db.session.query(Calificacion).filter(Calificacion.semana_anio == semana, Calificacion.cumplido.isnot(None)).count()

    return {
        'raw_code': raw_code,
        'semana': semana,
        'anio': anio,
        'total_camas_segmentos': len(df_plano),
        'actividades_macheadas': matched_count,
        'segmentos_sin_match': unmatched_count,
        'existe_semana': existing_records > 0,
        'tiene_calificaciones': existing_ratings > 0,
        'registros_existentes': existing_records,
        'calificaciones_existentes': existing_ratings
    }

def sync_excel_to_db(semana_anio=None, anio=None, ec_file_path=None, mf_file_path=None):
    if not ec_file_path or not os.path.exists(ec_file_path):
        raise FileNotFoundError("Debe proporcionar la ruta de un archivo válido de Estado Cultivo (.xlsx).")
    if not mf_file_path or not os.path.exists(mf_file_path):
        mf_file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "matriz fenogramas.xlsx")

    extracted_sem, extracted_anio, _ = extract_week_from_ec(ec_file_path)
    if semana_anio is None:
        semana_anio = extracted_sem
    if anio is None:
        anio = extracted_anio

    config = load_mapeos_config()
    prod_aliases = {normalize_key(k): v for k, v in config.get("producto_aliases", {}).items()}

    print(f"[FENOGRAMAS] Sincronizando semana {semana_anio} ({anio})...")
    df_matrix_rules = process_matriz_fenogramas(mf_file_path, config)
    df_plano = process_estado_cultivo(ec_file_path)

    # 1. Asegurar registro de semana_evaluacion
    sem_ev = SemanaEvaluacion.query.filter_by(semana_anio=semana_anio).first()
    if not sem_ev:
        sem_ev = SemanaEvaluacion(semana_anio=semana_anio, anio=anio, estado='ABIERTA')
        db.session.add(sem_ev)

    # 2. Reemplazar matriz_fenogramas
    db.session.execute(text("TRUNCATE TABLE matriz_fenogramas RESTART IDENTITY CASCADE;"))
    for _, r in df_matrix_rules.iterrows():
        db.session.add(MatrizFenograma(
            sheet_name=r['sheet_name'],
            producto=r['producto'],
            actividad_tipo=r['actividad_tipo'],
            semana=int(r['semana']),
            labor=r['labor'],
            auxiliar=r['auxiliar'],
            supervisor=r['supervisor']
        ))

    # 3. Reemplazar plano_cultivo
    db.session.execute(text("TRUNCATE TABLE plano_cultivo RESTART IDENTITY CASCADE;"))
    for _, r in df_plano.iterrows():
        db.session.add(PlanoCultivo(
            zona=r['ZONA'],
            bloque=r['BLOQUE'],
            actividad=r['ACTIVIDAD'],
            producto_maestro=r['PRODUCTO_MAESTRO'],
            cama_inicial=r['CAMA_INICIAL'],
            cama_final=r['CAMA_FINAL'],
            sufijo=r['SUFIJO'],
            edad_real=r['EDAD_REAL']
        ))

    db.session.commit()

    # 4. Generar o actualizar calificaciones
    all_matrix_prods = set(df_matrix_rules['producto_norm'].unique())
    final_items_map = {}

    for _, row in df_plano.iterrows():
        target_prods = get_matching_matrix_products(row['PRODUCTO_MAESTRO'], prod_aliases)
        has_matrix = any(tp in all_matrix_prods for tp in target_prods)
        if not has_matrix:
            item_key = (semana_corte, anio, row['ZONA'], row['BLOQUE'], row['ACTIVIDAD'], row['PRODUCTO_MAESTRO'], int(row['CAMA_INICIAL']), int(row['CAMA_FINAL']), row['SUFIJO'], 'No hay actividad')
            if item_key not in final_items_map:
                final_items_map[item_key] = {
                    'semana_anio': semana_corte, 'anio': anio, 'zona': row['ZONA'], 'bloque': row['BLOQUE'],
                    'actividad_tipo': row['ACTIVIDAD'], 'producto_maestro': row['PRODUCTO_MAESTRO'],
                    'cama_inicial': int(row['CAMA_INICIAL']), 'cama_final': int(row['CAMA_FINAL']), 'sufijo': row['SUFIJO'],
                    'edad_real': int(row['EDAD_REAL']), 'labor': 'No hay actividad', 'auxiliar': '', 'supervisor': '',
                    'tiene_evaluacion': False, 'es_pendiente_anterior': False, 'semana_origen': semana_corte
                }
            continue

        act_norm = normalize_key(row['ACTIVIDAD'])
        edad = row['EDAD_REAL']

        matches = df_matrix_rules[
            (df_matrix_rules['producto_norm'].isin(target_prods)) &
            (df_matrix_rules['semana'] == edad) &
            (
                (df_matrix_rules['actividad_norm'] == act_norm) |
                (df_matrix_rules['actividad_norm'] == 'TODOS')
            )
        ]

        if len(matches) == 0:
            item_key = (semana_anio, anio, row['ZONA'], row['BLOQUE'], row['ACTIVIDAD'], row['PRODUCTO_MAESTRO'], row['CAMA_INICIAL'], row['CAMA_FINAL'], row['SUFIJO'], 'No hay actividad')
            if item_key not in final_items_map:
                final_items_map[item_key] = {
                    'semana_anio': semana_anio, 'anio': anio, 'zona': row['ZONA'], 'bloque': row['BLOQUE'],
                    'actividad_tipo': row['ACTIVIDAD'], 'producto_maestro': row['PRODUCTO_MAESTRO'],
                    'cama_inicial': row['CAMA_INICIAL'], 'cama_final': row['CAMA_FINAL'], 'sufijo': row['SUFIJO'],
                    'edad_real': edad, 'labor': 'No hay actividad', 'auxiliar': '', 'supervisor': '',
                    'tiene_evaluacion': False, 'es_pendiente_anterior': False, 'semana_origen': semana_anio
                }
        else:
            for _, m in matches.iterrows():
                item_key = (semana_anio, anio, row['ZONA'], row['BLOQUE'], row['ACTIVIDAD'], row['PRODUCTO_MAESTRO'], row['CAMA_INICIAL'], row['CAMA_FINAL'], row['SUFIJO'], m['labor'])
                new_item = {
                    'semana_anio': semana_anio, 'anio': anio, 'zona': row['ZONA'], 'bloque': row['BLOQUE'],
                    'actividad_tipo': row['ACTIVIDAD'], 'producto_maestro': row['PRODUCTO_MAESTRO'],
                    'cama_inicial': row['CAMA_INICIAL'], 'cama_final': row['CAMA_FINAL'], 'sufijo': row['SUFIJO'],
                    'edad_real': edad, 'labor': m['labor'], 'auxiliar': m['auxiliar'], 'supervisor': m['supervisor'],
                    'tiene_evaluacion': True, 'es_pendiente_anterior': False, 'semana_origen': semana_anio
                }
                if item_key not in final_items_map:
                    final_items_map[item_key] = new_item
                else:
                    if not final_items_map[item_key]['supervisor'] and m['supervisor']:
                        final_items_map[item_key] = new_item

    # Insertar o actualizar usando SQL nativo ON CONFLICT
    for it in final_items_map.values():
        db.session.execute(text("""
            INSERT INTO calificaciones (
                semana_anio, anio, zona, bloque, actividad_tipo, producto_maestro,
                cama_inicial, cama_final, sufijo, edad_real, labor, auxiliar, supervisor,
                tiene_evaluacion, es_pendiente_anterior, semana_origen
            ) VALUES (
                :semana_anio, :anio, :zona, :bloque, :actividad_tipo, :producto_maestro,
                :cama_inicial, :cama_final, :sufijo, :edad_real, :labor, :auxiliar, :supervisor,
                :tiene_evaluacion, :es_pendiente_anterior, :semana_origen
            )
            ON CONFLICT (semana_anio, anio, zona, bloque, producto_maestro, cama_inicial, cama_final, sufijo, labor)
            DO UPDATE SET
                auxiliar = EXCLUDED.auxiliar,
                supervisor = EXCLUDED.supervisor,
                edad_real = EXCLUDED.edad_real,
                tiene_evaluacion = EXCLUDED.tiene_evaluacion;
        """), it)

    db.session.commit()

    # Limpieza de actividades obsoletas no calificadas (filas fantasma generadas por matrices anteriores)
    valid_labors_by_crop = set()
    for it in final_items_map.values():
        valid_labors_by_crop.add((it['zona'], it['bloque'], it['producto_maestro'], it['cama_inicial'], it['cama_final'], it['sufijo'], it['labor']))

    sem_target = semana_corte if 'semana_corte' in locals() else semana_anio
    existing_unrated = db.session.execute(text("""
        SELECT id, zona, bloque, producto_maestro, cama_inicial, cama_final, sufijo, labor
        FROM calificaciones
        WHERE semana_anio = :sem
          AND cumplido IS NULL
          AND (observacion IS NULL OR TRIM(observacion) = '')
          AND es_pendiente_anterior = FALSE;
    """), {'sem': sem_target}).fetchall()

    stale_ids = []
    for er in existing_unrated:
        key = (er.zona, er.bloque, er.producto_maestro, er.cama_inicial, er.cama_final, er.sufijo, er.labor)
        if key not in valid_labors_by_crop:
            stale_ids.append(er.id)

    if stale_ids:
        for i in range(0, len(stale_ids), 500):
            chunk = stale_ids[i:i+500]
            db.session.execute(text("DELETE FROM calificaciones WHERE id IN :ids;").bindparams(bindparam('ids', expanding=True)), {'ids': chunk})
        db.session.commit()
        print(f"[FENOGRAMAS] Se purgaron {len(stale_ids)} labores fantasma obsoletas sin calificar.")
    carried_count, expired_count = arrastrar_pendientes_semana_anterior(
        semana_destino=semana_anio,
        anio=anio,
        exigir_consecutiva=True,
        validar_censo_activo=True
    )
    print(f"[FENOGRAMAS] Sincronizados {len(final_items_map)} ítems (+{carried_count} arrastrados, {expired_count} caducados por límite).")
    return len(final_items_map) + carried_count


MAX_SEMANAS_ARRASTRE_DEFAULT = 3


def arrastrar_pendientes_semana_anterior(
    semana_destino,
    anio=2026,
    max_semanas_arrastre=None,
    exigir_consecutiva=True,
    validar_censo_activo=True,
    solo_calificadas_cero=False,
    cerrar_previa=True
):
    """
    Arrastra las labores pendientes (cumplido IS NULL) o incumplidas (cumplido = 0)
    hacia `semana_destino`, aplicando 3 candados contra bucles infinitos:
      1. Candado de Semana Consecutiva: Si el usuario dejó de usar la app y carga una semana
         no consecutiva (semana_destino - semana_previa > 1), cierra las semanas anteriores
         abiertas y NO arrastra tareas obsoletas a través del salto de semanas.
      2. Candado de Horizonte Máximo (TTL): Ninguna tarea se arrastra más de `max_semanas_arrastre`
         semanas desde su `semana_origen` (por defecto 3 semanas).
      3. Candado de Censo Activo: Si `validar_censo_activo=True` y existe `plano_cultivo`,
         solo arrastra tareas cuyo (zona, bloque, producto_maestro) sigue activo en el campo.
    """
    semana_destino = int(semana_destino)
    if max_semanas_arrastre is None:
        cfg = load_mapeos_config()
        max_semanas_arrastre = int(cfg.get("max_semanas_arrastre", MAX_SEMANAS_ARRASTRE_DEFAULT))

    prev_row = db.session.execute(text("""
        SELECT semana_anio, estado
        FROM semanas_evaluacion
        WHERE semana_anio < :semana_destino
        ORDER BY semana_anio DESC
        LIMIT 1;
    """), {'semana_destino': semana_destino}).fetchone()

    if not prev_row:
        return 0, 0

    semana_previa = int(prev_row.semana_anio)
    gap = semana_destino - semana_previa

    # Candado 1: Si hubo salto de semanas (ej. dejó de usar la app en sem 30 y retoma en sem 35)
    # manejar también cruce de fin de año (ej. sem 52 -> sem 1 del siguiente año)
    es_consecutiva = (gap == 1) or (semana_previa in (52, 53) and semana_destino == 1)
    if exigir_consecutiva and not es_consecutiva:
        if cerrar_previa:
            db.session.execute(text("""
                UPDATE semanas_evaluacion
                SET estado = 'CERRADA', fecha_cierre = COALESCE(fecha_cierre, CURRENT_TIMESTAMP)
                WHERE semana_anio < :semana_destino AND estado = 'ABIERTA';
            """), {'semana_destino': semana_destino})
            db.session.commit()
        print(f"[FENOGRAMAS] Salto no consecutivo detectado (Sem {semana_previa} -> Sem {semana_destino}). Se omite arrastre para evitar bucle infinito.")
        return 0, 0

    # Candado 3: Bloques/productos activos en plano_cultivo actual
    bloques_activos = set()
    if validar_censo_activo:
        plano_rows = db.session.execute(text("""
            SELECT DISTINCT zona, bloque, producto_maestro
            FROM plano_cultivo;
        """)).fetchall()
        if plano_rows:
            bloques_activos = {(clean_str(r.zona), clean_str(r.bloque), clean_str(r.producto_maestro)) for r in plano_rows}

    if solo_calificadas_cero:
        cond_cumplido = "cumplido = 0"
    else:
        cond_cumplido = "(cumplido = 0 OR cumplido IS NULL)"

    incumplidas = db.session.execute(text(f"""
        SELECT zona, bloque, actividad_tipo, producto_maestro, cama_inicial, cama_final, sufijo,
               edad_real, labor, auxiliar, supervisor, observacion, semana_origen
        FROM calificaciones
        WHERE semana_anio = :semana_previa
          AND tiene_evaluacion = TRUE
          AND {cond_cumplido};
    """), {'semana_previa': semana_previa}).fetchall()

    carried_count = 0
    expired_count = 0

    for item in incumplidas:
        orig = int(item.semana_origen or semana_previa)
        # Calcular antigüedad en semanas (soportando cambio de año 52 -> 1)
        if semana_destino >= orig:
            antiguedad = semana_destino - orig
        else:
            antiguedad = (52 - orig) + semana_destino

        # Candado 2: Límite máximo de semanas de arrastre (TTL)
        if antiguedad > max_semanas_arrastre:
            expired_count += 1
            continue

        # Candado 3: Validar que el bloque y cultivo sigan existiendo en el censo actual
        if bloques_activos:
            key_blq = (clean_str(item.zona), clean_str(item.bloque), clean_str(item.producto_maestro))
            if key_blq not in bloques_activos:
                expired_count += 1
                continue

        obs_prev = (item.observacion or '').strip()
        prefix = f"[Pendiente Sem {orig}]"
        if obs_prev.startswith("[Pendiente"):
            obs_final = obs_prev
        else:
            obs_final = f"{prefix} {obs_prev}".strip()

        db.session.execute(text("""
            INSERT INTO calificaciones (
                semana_anio, anio, zona, bloque, actividad_tipo, producto_maestro,
                cama_inicial, cama_final, sufijo, edad_real, labor, auxiliar, supervisor,
                cumplido, observacion, tiene_evaluacion, es_pendiente_anterior, semana_origen
            ) VALUES (
                :semana_destino, :anio, :zona, :bloque, :actividad_tipo, :producto_maestro,
                :cama_inicial, :cama_final, :sufijo, :edad_real, :labor, :auxiliar, :supervisor,
                NULL, :observacion, TRUE, TRUE, :semana_origen
            )
            ON CONFLICT (semana_anio, anio, zona, bloque, producto_maestro, cama_inicial, cama_final, sufijo, labor)
            DO UPDATE SET
                es_pendiente_anterior = TRUE,
                semana_origen = EXCLUDED.semana_origen,
                observacion = CASE
                    WHEN calificaciones.observacion IS NULL OR calificaciones.observacion = ''
                    THEN EXCLUDED.observacion
                    ELSE calificaciones.observacion
                END;
        """), {
            'semana_destino': semana_destino, 'anio': anio,
            'zona': item.zona, 'bloque': item.bloque,
            'actividad_tipo': item.actividad_tipo, 'producto_maestro': item.producto_maestro,
            'cama_inicial': item.cama_inicial, 'cama_final': item.cama_final, 'sufijo': item.sufijo,
            'edad_real': int(item.edad_real or 0) + 1,
            'labor': item.labor, 'auxiliar': item.auxiliar, 'supervisor': item.supervisor,
            'observacion': obs_final, 'semana_origen': orig
        })
        carried_count += 1

    # Marcar la semana previa como cerrada solo cuando corresponde
    if cerrar_previa:
        db.session.execute(text("""
            UPDATE semanas_evaluacion
            SET estado = 'CERRADA', fecha_cierre = COALESCE(fecha_cierre, CURRENT_TIMESTAMP)
            WHERE semana_anio = :semana_previa AND estado = 'ABIERTA';
        """), {'semana_previa': semana_previa})

    db.session.commit()
    return carried_count, expired_count


def _parse_week_code_to_tuple(w_raw, default_year=2026):
    s = str(w_raw or '').strip()
    m_dash = re.match(r'^(\d{4})\s*[-/]\s*(\d{1,2})$', s)
    if m_dash:
        return (int(m_dash.group(1)), int(m_dash.group(2)))
    if s.isdigit() and len(s) == 4:
        return (2000 + int(s[:2]), int(s[2:]))
    if s.isdigit() and len(s) <= 2:
        return (int(default_year), int(s))
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
        return (int(default_year), v)
    return (0, 0)


def _ensure_calificaciones_unique_index():
    """Garantiza que exista la secuencia de IDs y el índice único requerido por ON CONFLICT en calificaciones."""
    try:
        for tbl in ('calificaciones', 'matriz_fenogramas', 'plano_cultivo'):
            seq_name = f"{tbl}_id_seq"
            db.session.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {seq_name};"))
            max_id = db.session.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {tbl};")).scalar() or 0
            db.session.execute(text(f"SELECT setval('{seq_name}', :m, true);"), {'m': max(1, int(max_id))})
            db.session.execute(text(f"UPDATE {tbl} SET id = nextval('{seq_name}') WHERE id IS NULL;"))
            db.session.execute(text(f"ALTER TABLE {tbl} ALTER COLUMN id SET DEFAULT nextval('{seq_name}');"))
            db.session.execute(text(f"ALTER TABLE {tbl} ALTER COLUMN id SET NOT NULL;"))
            db.session.execute(text(f"ALTER SEQUENCE {seq_name} OWNED BY {tbl}.id;"))

        db.session.execute(text("""
            DELETE FROM calificaciones a
            USING calificaciones b
            WHERE a.id < b.id
              AND a.semana_anio = b.semana_anio
              AND COALESCE(a.anio, 2026) = COALESCE(b.anio, 2026)
              AND COALESCE(a.zona, '') = COALESCE(b.zona, '')
              AND a.bloque = b.bloque
              AND COALESCE(a.producto_maestro, '') = COALESCE(b.producto_maestro, '')
              AND a.cama_inicial = b.cama_inicial
              AND a.cama_final = b.cama_final
              AND COALESCE(a.sufijo, '') = COALESCE(b.sufijo, '')
              AND a.labor = b.labor;
        """))
        db.session.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_calificaciones_item_key
            ON calificaciones (semana_anio, anio, zona, bloque, producto_maestro, cama_inicial, cama_final, sufijo, labor);
        """))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[FENOGRAMAS] Aviso asegurando índice único: {e}")


def sync_from_mrt_estado_cultivo(semana_corte, anio=2026, fuente='AUTO', semana_base=None, forzar_limpieza=False):
    """
    Genera el plano de cultivo y las calificaciones de fenograma para cualquier `semana_corte`
    (ej. Semana 41, 42, 43...), proyectando automáticamente la edad recorrida (`edad_recorrida`)
    de cada cama según la diferencia de semanas entre la ingesta base (Excel Integral `crop_state_records`
    y/o Registro de Campo TPSR `mrt_estado_cultivo_actual` / `fact_eventos_cultivo`) y la semana objetivo.
    """
    _ensure_calificaciones_unique_index()
    semana_corte = int(semana_corte)
    anio = int(anio or 2026)
    fuente = str(fuente or 'AUTO').strip().upper()

    config = load_mapeos_config()
    prod_aliases = {normalize_key(k): v for k, v in config.get("producto_aliases", {}).items()}

    camas_map = {}  # key: (bloque_clean, int(num_cama), sufijo_clean) -> dict
    fuente_usada = 'TPSR'
    semana_base_usada = semana_corte
    delta_semanas_aplicado = 0

    # 1. En modo AUTO o TPSR: Usar directamente mrt_estado_cultivo_actual (datos vivos de campo con eventos reales)
    if fuente in ('AUTO', 'TPSR'):
        query_camas = text("""
            SELECT 
                COALESCE(zona, 'ZONA1') as zona,
                COALESCE(blq, codigo_bloque) as bloque,
                COALESCE(sufijo, 'A') as sufijo,
                num_cama,
                CASE 
                    WHEN UPPER(COALESCE(ultimo_evento, '')) LIKE '%PODA%' THEN 'Poda'
                    ELSE 'Siembra'
                END as actividad,
                COALESCE(producto_actual, 'GYPSOPHILA') as producto_maestro,
                COALESCE(edad_semanas_actual, 1) as edad_real,
                semana_campo_ultimo_evento,
                fecha_ultimo_evento
            FROM mrt_estado_cultivo_actual
            WHERE producto_actual IS NOT NULL 
              AND producto_actual NOT IN ('VACIO', 'ERRADICADO', 'TOTAL', 'NAN', '')
              AND COALESCE(ultimo_evento, '') != 'TUMBA'
              AND num_cama IS NOT NULL
            ORDER BY zona, bloque, num_cama, sufijo;
        """)

        camas_rows = db.session.execute(query_camas).fetchall()
        if camas_rows:
            fuente_usada = f"TPSR Campo (Semana {semana_corte})"
            semana_base_usada = semana_corte
            delta_semanas_aplicado = 0

            for r in camas_rows:
                blq_c = clean_block_code(r.bloque)
                suf_c = str(r.sufijo or 'A').strip().upper()
                n_cama = int(r.num_cama)
                if r.semana_campo_ultimo_evento:
                    yr_ev, wk_ev = _parse_week_code_to_tuple(r.semana_campo_ultimo_evento, anio)
                    edad_proy = max(0, (anio - yr_ev) * 52 + (semana_corte - wk_ev))
                elif r.fecha_ultimo_evento:
                    dt = r.fecha_ultimo_evento
                    yr_ev, wk_ev = dt.isocalendar()[0], dt.isocalendar()[1]
                    edad_proy = max(0, (anio - yr_ev) * 52 + (semana_corte - wk_ev))
                else:
                    edad_proy = max(0, int(r.edad_real or 0))

                camas_map[(blq_c, n_cama, suf_c)] = {
                    'ZONA': clean_str(r.zona),
                    'BLOQUE': blq_c,
                    'SUFIJO': suf_c,
                    'NUM_CAMA': n_cama,
                    'ACTIVIDAD': r.actividad,
                    'PRODUCTO_MAESTRO': clean_str(r.producto_maestro),
                    'EDAD_REAL': int(edad_proy)
                }

    # 2. Modo explícito EXCEL o fallback si mrt_estado_cultivo_actual estaba vacío
    if not camas_map and fuente in ('AUTO', 'EXCEL'):
        week_rows = db.session.execute(text("""
            SELECT DISTINCT week
            FROM crop_state_records
            WHERE week IS NOT NULL AND crop_master IS NOT NULL AND crop_master != 'VACIO';
        """)).fetchall()

        available_snapshots = []
        for wr in week_rows:
            yr_w, wk_w = _parse_week_code_to_tuple(wr.week, anio)
            if wk_w > 0:
                available_snapshots.append({
                    'raw': wr.week,
                    'year': yr_w,
                    'week_num': wk_w,
                    'abs_val': yr_w * 52 + wk_w
                })

        if available_snapshots:
            target_abs = anio * 52 + semana_corte
            if semana_base:
                yr_b, wk_b = _parse_week_code_to_tuple(semana_base, anio)
                chosen = next((s for s in available_snapshots if s['raw'] == str(semana_base) or s['week_num'] == wk_b), None)
            else:
                chosen = None

            if not chosen:
                # Preferir la foto cargada más reciente <= semana_corte; si no existe, la más cercana
                le_snaps = [s for s in available_snapshots if s['abs_val'] <= target_abs]
                if le_snaps:
                    le_snaps.sort(key=lambda x: x['abs_val'], reverse=True)
                    chosen = le_snaps[0]
                else:
                    available_snapshots.sort(key=lambda x: abs(x['abs_val'] - target_abs))
                    chosen = available_snapshots[0]

            if chosen:
                semana_base_usada = chosen['week_num']
                delta_semanas_aplicado = (anio - chosen['year']) * 52 + (semana_corte - chosen['week_num'])
                fuente_usada = f"EXCEL Sem {chosen['year']}-{chosen['week_num']:02d}"

                # Cargar eventos reales de campo registrados para cruzar por cama
                tpsr_events_lookup = {}
                try:
                    ev_rows = db.session.execute(text("""
                        SELECT 
                            COALESCE(blq, codigo_bloque) as bloque,
                            COALESCE(sufijo, 'A') as sufijo,
                            num_cama,
                            semana_campo_ultimo_evento,
                            fecha_ultimo_evento,
                            ultimo_evento
                        FROM mrt_estado_cultivo_actual
                        WHERE num_cama IS NOT NULL;
                    """)).fetchall()
                    for er in ev_rows:
                        k_ev = (clean_block_code(er.bloque), int(er.num_cama), str(er.sufijo or 'A').strip().upper())
                        tpsr_events_lookup[k_ev] = er
                except Exception:
                    tpsr_events_lookup = {}

                csr_rows = db.session.execute(text("""
                    SELECT
                        COALESCE(NULLIF(TRIM(zone), ''), 'SIN ZONA') AS zona,
                        COALESCE(NULLIF(TRIM(block_num), ''), TRIM(block_full)) AS bloque,
                        COALESCE(NULLIF(TRIM(suffix), ''), 'A') AS sufijo,
                        bed_num AS num_cama,
                        CASE
                            WHEN UPPER(COALESCE(status_raw, '')) LIKE '%PODA%' THEN 'Poda'
                            ELSE 'Siembra'
                        END AS actividad,
                        COALESCE(NULLIF(TRIM(crop_master), ''), TRIM(product_name)) AS producto_maestro,
                        COALESCE(real_age, 0) AS edad_base
                    FROM crop_state_records
                    WHERE week = :raw_week
                      AND crop_master IS NOT NULL
                      AND UPPER(TRIM(crop_master)) NOT IN ('VACIO', 'ERRADICADO', 'TOTAL', 'NAN', '')
                      AND bed_num IS NOT NULL
                    ORDER BY zone, block_num, bed_num, suffix;
                """), {'raw_week': chosen['raw']}).fetchall()

                for r in csr_rows:
                    blq_c = clean_block_code(r.bloque)
                    suf_c = str(r.sufijo or 'A').strip().upper()
                    n_cama = int(r.num_cama)
                    ev_match = tpsr_events_lookup.get((blq_c, n_cama, suf_c))

                    if ev_match and ev_match.semana_campo_ultimo_evento:
                        yr_ev, wk_ev = _parse_week_code_to_tuple(ev_match.semana_campo_ultimo_evento, anio)
                        edad_proy = max(0, (anio - yr_ev) * 52 + (semana_corte - wk_ev))
                        actividad = 'Poda' if 'PODA' in str(ev_match.ultimo_evento or '').upper() else 'Siembra'
                    elif ev_match and ev_match.fecha_ultimo_evento:
                        dt = ev_match.fecha_ultimo_evento
                        yr_ev, wk_ev = dt.isocalendar()[0], dt.isocalendar()[1]
                        edad_proy = max(0, (anio - yr_ev) * 52 + (semana_corte - wk_ev))
                        actividad = 'Poda' if 'PODA' in str(ev_match.ultimo_evento or '').upper() else 'Siembra'
                    else:
                        edad_proy = int(round(float(r.edad_base or 0))) + delta_semanas_aplicado
                        actividad = r.actividad

                    if edad_proy < 0:
                        continue
                    camas_map[(blq_c, n_cama, suf_c)] = {
                        'ZONA': clean_str(r.zona),
                        'BLOQUE': blq_c,
                        'SUFIJO': suf_c,
                        'NUM_CAMA': n_cama,
                        'ACTIVIDAD': actividad,
                        'PRODUCTO_MAESTRO': clean_str(r.producto_maestro),
                        'EDAD_REAL': edad_proy
                    }

    if not camas_map:
        return 0

    df_plano = consolidar_camas_cultivo_df(pd.DataFrame(list(camas_map.values())))

    # Actualizar tabla plano_cultivo
    db.session.execute(text("TRUNCATE TABLE plano_cultivo RESTART IDENTITY CASCADE;"))
    for _, r in df_plano.iterrows():
        db.session.add(PlanoCultivo(
            zona=r['ZONA'],
            bloque=r['BLOQUE'],
            actividad=r['ACTIVIDAD'],
            producto_maestro=r['PRODUCTO_MAESTRO'],
            cama_inicial=int(r['CAMA_INICIAL']),
            cama_final=int(r['CAMA_FINAL']),
            sufijo=r['SUFIJO'],
            edad_real=int(r['EDAD_REAL'])
        ))

    # Asegurar registro de semana de evaluación (y dejarla ABIERTA al generar/proyectar)
    sem_ev = SemanaEvaluacion.query.filter_by(semana_anio=semana_corte).first()
    if not sem_ev:
        sem_ev = SemanaEvaluacion(semana_anio=semana_corte, anio=anio, estado='ABIERTA')
        db.session.add(sem_ev)
    else:
        sem_ev.estado = 'ABIERTA'
        sem_ev.fecha_cierre = None

    # Limpiar ítems previos de esta semana antes de regenerar
    if forzar_limpieza:
        db.session.execute(text("DELETE FROM calificaciones WHERE semana_anio = :sem;"), {'sem': semana_corte})
    else:
        db.session.execute(text("""
            DELETE FROM calificaciones
            WHERE semana_anio = :sem
              AND cumplido IS NULL
              AND (
                  COALESCE(observacion, '') = ''
                  OR (COALESCE(es_pendiente_anterior, FALSE) = TRUE AND observacion LIKE '[Pendiente Sem %')
              );
        """), {'sem': semana_corte})

        # Si un bloque fue tumbado/erradicado en el nuevo estado de cultivo, limpiar sus filas huerfanas
        active_blocks = [str(b).strip() for b in df_plano['BLOQUE'].unique() if str(b).strip()]
        if active_blocks:
            db.session.execute(text("""
                DELETE FROM calificaciones
                WHERE semana_anio = :sem
                  AND bloque NOT IN :blocks;
            """).bindparams(bindparam('blocks', expanding=True)), {'sem': semana_corte, 'blocks': active_blocks})

    db.session.commit()

    # Cargar reglas de matriz_fenogramas
    matriz_rows = MatrizFenograma.query.all()
    matrix_rules = []
    for m in matriz_rows:
        matrix_rules.append({
            'sheet_name': m.sheet_name,
            'producto': m.producto,
            'producto_norm': normalize_key(normalize_product_name(m.producto)),
            'actividad_tipo': m.actividad_tipo,
            'actividad_norm': normalize_key(m.actividad_tipo),
            'semana': int(m.semana),
            'labor': m.labor,
            'auxiliar': m.auxiliar or '',
            'supervisor': m.supervisor or ''
        })
    df_matrix_rules = pd.DataFrame(matrix_rules)

    all_matrix_prods = set(df_matrix_rules['producto_norm'].unique()) if not df_matrix_rules.empty else set()
    final_items_map = {}

    for _, row in df_plano.iterrows():
        target_prods = get_matching_matrix_products(row['PRODUCTO_MAESTRO'], prod_aliases)
        has_matrix = any(tp in all_matrix_prods for tp in target_prods)
        if not has_matrix:
            item_key = (semana_corte, anio, row['ZONA'], row['BLOQUE'], row['ACTIVIDAD'], row['PRODUCTO_MAESTRO'], int(row['CAMA_INICIAL']), int(row['CAMA_FINAL']), row['SUFIJO'], 'No hay actividad')
            if item_key not in final_items_map:
                final_items_map[item_key] = {
                    'semana_anio': semana_corte, 'anio': anio, 'zona': row['ZONA'], 'bloque': row['BLOQUE'],
                    'actividad_tipo': row['ACTIVIDAD'], 'producto_maestro': row['PRODUCTO_MAESTRO'],
                    'cama_inicial': int(row['CAMA_INICIAL']), 'cama_final': int(row['CAMA_FINAL']), 'sufijo': row['SUFIJO'],
                    'edad_real': int(row['EDAD_REAL']), 'labor': 'No hay actividad', 'auxiliar': '', 'supervisor': '',
                    'tiene_evaluacion': False, 'es_pendiente_anterior': False, 'semana_origen': semana_corte
                }
            continue

        act_norm = normalize_key(row['ACTIVIDAD'])
        edad = int(row['EDAD_REAL'])

        matches = df_matrix_rules[
            (df_matrix_rules['producto_norm'].isin(target_prods)) &
            (df_matrix_rules['semana'] == edad) &
            (
                (df_matrix_rules['actividad_norm'] == act_norm) |
                (df_matrix_rules['actividad_norm'] == 'TODOS')
            )
        ]

        if len(matches) == 0:
            item_key = (semana_corte, anio, row['ZONA'], row['BLOQUE'], row['ACTIVIDAD'], row['PRODUCTO_MAESTRO'], int(row['CAMA_INICIAL']), int(row['CAMA_FINAL']), row['SUFIJO'], 'No hay actividad')
            if item_key not in final_items_map:
                final_items_map[item_key] = {
                    'semana_anio': semana_corte, 'anio': anio, 'zona': row['ZONA'], 'bloque': row['BLOQUE'],
                    'actividad_tipo': row['ACTIVIDAD'], 'producto_maestro': row['PRODUCTO_MAESTRO'],
                    'cama_inicial': int(row['CAMA_INICIAL']), 'cama_final': int(row['CAMA_FINAL']), 'sufijo': row['SUFIJO'],
                    'edad_real': edad, 'labor': 'No hay actividad', 'auxiliar': '', 'supervisor': '',
                    'tiene_evaluacion': False, 'es_pendiente_anterior': False, 'semana_origen': semana_corte
                }
        else:
            for _, m in matches.iterrows():
                item_key = (semana_corte, anio, row['ZONA'], row['BLOQUE'], row['ACTIVIDAD'], row['PRODUCTO_MAESTRO'], int(row['CAMA_INICIAL']), int(row['CAMA_FINAL']), row['SUFIJO'], m['labor'])
                new_item = {
                    'semana_anio': semana_corte, 'anio': anio, 'zona': row['ZONA'], 'bloque': row['BLOQUE'],
                    'actividad_tipo': row['ACTIVIDAD'], 'producto_maestro': row['PRODUCTO_MAESTRO'],
                    'cama_inicial': int(row['CAMA_INICIAL']), 'cama_final': int(row['CAMA_FINAL']), 'sufijo': row['SUFIJO'],
                    'edad_real': edad, 'labor': m['labor'], 'auxiliar': m['auxiliar'], 'supervisor': m['supervisor'],
                    'tiene_evaluacion': True, 'es_pendiente_anterior': False, 'semana_origen': semana_corte
                }
                if item_key not in final_items_map:
                    final_items_map[item_key] = new_item
                else:
                    if not final_items_map[item_key]['supervisor'] and m['supervisor']:
                        final_items_map[item_key] = new_item

    for it in final_items_map.values():
        db.session.execute(text("""
            INSERT INTO calificaciones (
                semana_anio, anio, zona, bloque, actividad_tipo, producto_maestro,
                cama_inicial, cama_final, sufijo, edad_real, labor, auxiliar, supervisor,
                tiene_evaluacion, es_pendiente_anterior, semana_origen
            ) VALUES (
                :semana_anio, :anio, :zona, :bloque, :actividad_tipo, :producto_maestro,
                :cama_inicial, :cama_final, :sufijo, :edad_real, :labor, :auxiliar, :supervisor,
                :tiene_evaluacion, :es_pendiente_anterior, :semana_origen
            )
            ON CONFLICT (semana_anio, anio, zona, bloque, producto_maestro, cama_inicial, cama_final, sufijo, labor)
            DO UPDATE SET
                auxiliar = EXCLUDED.auxiliar,
                supervisor = EXCLUDED.supervisor,
                edad_real = EXCLUDED.edad_real,
                tiene_evaluacion = EXCLUDED.tiene_evaluacion;
        """), it)

    db.session.commit()

    # Limpieza de actividades obsoletas no calificadas (filas fantasma generadas por matrices anteriores)
    valid_labors_by_crop = set()
    for it in final_items_map.values():
        valid_labors_by_crop.add((it['zona'], it['bloque'], it['producto_maestro'], it['cama_inicial'], it['cama_final'], it['sufijo'], it['labor']))

    sem_target = semana_corte if 'semana_corte' in locals() else semana_anio
    existing_unrated = db.session.execute(text("""
        SELECT id, zona, bloque, producto_maestro, cama_inicial, cama_final, sufijo, labor
        FROM calificaciones
        WHERE semana_anio = :sem
          AND cumplido IS NULL
          AND (observacion IS NULL OR TRIM(observacion) = '')
          AND es_pendiente_anterior = FALSE;
    """), {'sem': sem_target}).fetchall()

    stale_ids = []
    for er in existing_unrated:
        key = (er.zona, er.bloque, er.producto_maestro, er.cama_inicial, er.cama_final, er.sufijo, er.labor)
        if key not in valid_labors_by_crop:
            stale_ids.append(er.id)

    if stale_ids:
        for i in range(0, len(stale_ids), 500):
            chunk = stale_ids[i:i+500]
            db.session.execute(text("DELETE FROM calificaciones WHERE id IN :ids;").bindparams(bindparam('ids', expanding=True)), {'ids': chunk})
        db.session.commit()
        print(f"[FENOGRAMAS] Se purgaron {len(stale_ids)} labores fantasma obsoletas sin calificar.")
    carried_count, expired_count = arrastrar_pendientes_semana_anterior(
        semana_destino=semana_corte,
        anio=anio,
        exigir_consecutiva=True,
        validar_censo_activo=True,
        solo_calificadas_cero=False,
        cerrar_previa=False
    )
    print(f"[FENOGRAMAS] Generadas {len(final_items_map)} actividades ({fuente_usada}, delta={delta_semanas_aplicado:+d} sem) para semana {semana_corte} (+{carried_count} arrastradas, {expired_count} caducadas).")
    return len(final_items_map) + carried_count


def cerrar_semana_evaluacion(semana_actual, anio=2026):
    """
    Cierra la semana de calificación actual y arrastra las labores incumplidas (0 o pendientes)
    a la siguiente semana (semana_actual + 1), aplicando los candados de horizonte máximo (TTL)
    y evitando bucles infinitos.
    """
    semana_actual = int(semana_actual)
    semana_siguiente = semana_actual + 1

    # 1. Cerrar semana actual
    db.session.execute(text("""
        UPDATE semanas_evaluacion
        SET estado = 'CERRADA', fecha_cierre = CURRENT_TIMESTAMP
        WHERE semana_anio = :semana_actual;
    """), {'semana_actual': semana_actual})

    # 2. Abrir semana siguiente
    db.session.execute(text("""
        INSERT INTO semanas_evaluacion (semana_anio, anio, estado)
        VALUES (:semana_siguiente, :anio, 'ABIERTA')
        ON CONFLICT (semana_anio) DO NOTHING;
    """), {'semana_siguiente': semana_siguiente, 'anio': anio})
    db.session.commit()

    # 3. Arrastrar pendientes con límite de antigüedad (TTL)
    carried_count, _ = arrastrar_pendientes_semana_anterior(
        semana_destino=semana_siguiente,
        anio=anio,
        exigir_consecutiva=True,
        validar_censo_activo=False
    )
    return semana_siguiente, carried_count

def obtener_datos_dashboard(semana):
    """
    Calcula todos los KPIs de cumplimiento, gráficos y tendencias para el Dashboard Fenogramas.
    """
    semanas = db.session.execute(text("SELECT semana_anio, estado FROM semanas_evaluacion ORDER BY semana_anio ASC;")).fetchall()
    semanas_todas = [s.semana_anio for s in semanas]

    sem_info = db.session.execute(text("SELECT estado, fecha_cierre FROM semanas_evaluacion WHERE semana_anio = :semana;"), {'semana': semana}).fetchone()
    semana_estado = sem_info.estado if sem_info else 'ABIERTA'
    fecha_cierre = sem_info.fecha_cierre if sem_info else None

    kpi_global = db.session.execute(text("""
        SELECT 
            COUNT(*) as total_evaluables,
            COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas,
            COUNT(CASE WHEN cumplido = 0 THEN 1 END) as incumplidas,
            COUNT(CASE WHEN cumplido IS NULL THEN 1 END) as pendientes,
            COUNT(CASE WHEN es_pendiente_anterior = TRUE THEN 1 END) as arrastradas_anterior
        FROM calificaciones
        WHERE semana_anio = :semana AND tiene_evaluacion = TRUE;
    """), {'semana': semana}).fetchone()

    kpi_dict = dict(kpi_global._mapping) if kpi_global else {}
    total = kpi_dict.get('total_evaluables', 0) or 0
    cumplidas = kpi_dict.get('cumplidas', 0) or 0
    kpi_dict['total'] = total
    kpi_dict['no_cumplidas'] = max(0, total - cumplidas)
    pct_cumplimiento = round((cumplidas / total * 100), 1) if total > 0 else 0

    por_zona = db.session.execute(text("""
        SELECT 
            zona,
            COUNT(*) as total,
            COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas,
            COUNT(CASE WHEN cumplido = 0 THEN 1 END) as incumplidas,
            COUNT(CASE WHEN cumplido IS NULL THEN 1 END) as pendientes
        FROM calificaciones
        WHERE semana_anio = :semana AND tiene_evaluacion = TRUE
        GROUP BY zona
        ORDER BY zona ASC;
    """), {'semana': semana}).fetchall()

    zonas_list = []
    for z in por_zona:
        tot = z.total
        zonas_list.append({
            'zona': z.zona,
            'total': tot,
            'cumplidas': z.cumplidas,
            'incumplidas': z.incumplidas,
            'pendientes': z.pendientes,
            'pct': round((z.cumplidas / tot * 100), 1) if tot > 0 else 0
        })

    por_producto = db.session.execute(text("""
        SELECT 
            producto_maestro,
            COUNT(*) as total,
            COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas,
            COUNT(CASE WHEN cumplido = 0 THEN 1 END) as incumplidas,
            COUNT(CASE WHEN cumplido IS NULL THEN 1 END) as pendientes
        FROM calificaciones
        WHERE semana_anio = :semana AND tiene_evaluacion = TRUE
        GROUP BY producto_maestro
        ORDER BY producto_maestro ASC;
    """), {'semana': semana}).fetchall()

    productos_list = []
    for p in por_producto:
        tot = p.total
        productos_list.append({
            'producto_maestro': p.producto_maestro,
            'total': tot,
            'cumplidas': p.cumplidas,
            'incumplidas': p.incumplidas,
            'pendientes': p.pendientes,
            'pct': round((p.cumplidas / tot * 100), 1) if tot > 0 else 0
        })

    por_personal = db.session.execute(text("""
        SELECT 
            COALESCE(NULLIF(supervisor, ''), 'Sin Supervisor') as supervisor,
            COALESCE(NULLIF(auxiliar, ''), 'Sin Auxiliar') as auxiliar,
            COUNT(*) as total_asignadas,
            COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas,
            COUNT(CASE WHEN cumplido = 0 THEN 1 END) as incumplidas,
            COUNT(CASE WHEN cumplido IS NULL THEN 1 END) as pendientes
        FROM calificaciones
        WHERE semana_anio = :semana AND tiene_evaluacion = TRUE
        GROUP BY supervisor, auxiliar
        ORDER BY supervisor ASC, auxiliar ASC;
    """), {'semana': semana}).fetchall()

    personal_list = []
    for pers in por_personal:
        tot = pers.total_asignadas
        personal_list.append({
            'supervisor': pers.supervisor,
            'auxiliar': pers.auxiliar,
            'total_asignadas': tot,
            'cumplidas': pers.cumplidas,
            'incumplidas': pers.incumplidas,
            'pendientes': pers.pendientes,
            'pct': round((pers.cumplidas / tot * 100), 1) if tot > 0 else 0
        })

    # Tendencia multisemana
    rows_mw = db.session.execute(text("""
        SELECT 
            COALESCE(NULLIF(supervisor, ''), 'Sin Supervisor') as supervisor,
            COALESCE(NULLIF(auxiliar, ''), 'Sin Auxiliar') as auxiliar,
            semana_anio,
            COUNT(*) as total,
            COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas
        FROM calificaciones
        WHERE tiene_evaluacion = TRUE
        GROUP BY supervisor, auxiliar, semana_anio
        ORDER BY supervisor ASC, auxiliar ASC, semana_anio ASC;
    """)).fetchall()

    personal_trend_dict = {}
    for r in rows_mw:
        key = f"{r.supervisor} | {r.auxiliar}"
        if key not in personal_trend_dict:
            personal_trend_dict[key] = {
                'supervisor': r.supervisor,
                'auxiliar': r.auxiliar,
                'semanas': {}
            }
        tot = r.total
        cumpl = r.cumplidas
        pct = round((cumpl / tot * 100), 1) if tot > 0 else 0
        personal_trend_dict[key]['semanas'][r.semana_anio] = pct

    tendencia_multisemana = []
    for key, data in personal_trend_dict.items():
        sems_data = []
        prev_pct = None
        tendencia = 'Igual'
        for sem in semanas_todas:
            p = data['semanas'].get(sem, None)
            sems_data.append({'semana': sem, 'pct': p})
            if p is not None:
                if prev_pct is not None:
                    if p > prev_pct:
                        tendencia = 'Sube'
                    elif p < prev_pct:
                        tendencia = 'Baja'
                    else:
                        tendencia = 'Igual'
                prev_pct = p

        tendencia_multisemana.append({
            'supervisor': data['supervisor'],
            'auxiliar': data['auxiliar'],
            'semanas_pct': sems_data,
            'tendencia': tendencia,
            'ultimo_pct': prev_pct if prev_pct is not None else 0
        })

    # Desempeño consolidado por Supervisor (actividades programadas, cumplidas, incumplidas, pendientes y %)
    por_sup_rows = db.session.execute(text("""
        SELECT 
            COALESCE(NULLIF(supervisor, ''), 'Sin Supervisor') as supervisor,
            COUNT(*) as total,
            COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas,
            COUNT(CASE WHEN cumplido = 0 THEN 1 END) as incumplidas,
            COUNT(CASE WHEN cumplido IS NULL THEN 1 END) as pendientes
        FROM calificaciones
        WHERE semana_anio = :semana AND tiene_evaluacion = TRUE
        GROUP BY COALESCE(NULLIF(supervisor, ''), 'Sin Supervisor')
        ORDER BY total DESC, supervisor ASC;
    """), {'semana': semana}).fetchall()

    supervisores_list = []
    for s_row in por_sup_rows:
        t_sup = int(s_row.total or 0)
        c_sup = int(s_row.cumplidas or 0)
        i_sup = int(s_row.incumplidas or 0)
        p_sup = int(s_row.pendientes or 0)
        supervisores_list.append({
            'supervisor': s_row.supervisor,
            'total': t_sup,
            'cumplidas': c_sup,
            'incumplidas': i_sup,
            'pendientes': p_sup,
            'quedaron': i_sup + p_sup,
            'pct': round((c_sup / t_sup * 100), 1) if t_sup > 0 else 0.0
        })

    # Tendencia General de Finca (semana a semana)
    finca_trend_rows = db.session.execute(text("""
        SELECT 
            semana_anio,
            COUNT(*) as total,
            COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas,
            COUNT(CASE WHEN cumplido = 0 THEN 1 END) as incumplidas,
            COUNT(CASE WHEN cumplido IS NULL THEN 1 END) as pendientes
        FROM calificaciones
        WHERE tiene_evaluacion = TRUE
        GROUP BY semana_anio
        ORDER BY semana_anio ASC;
    """)).fetchall()

    tendencia_finca = []
    for ft in finca_trend_rows:
        t_f = int(ft.total or 0)
        c_f = int(ft.cumplidas or 0)
        i_f = int(ft.incumplidas or 0)
        p_f = int(ft.pendientes or 0)
        tendencia_finca.append({
            'semana': int(ft.semana_anio),
            'total': t_f,
            'cumplidas': c_f,
            'incumplidas': i_f,
            'pendientes': p_f,
            'pct': round((c_f / t_f * 100), 1) if t_f > 0 else 0.0
        })

    # Desglose de cumplimiento por Zona cruzado con Cultivo (para filtrado interactivo estilo Power BI)
    zona_prod_rows = db.session.execute(text("""
        SELECT 
            zona,
            COALESCE(NULLIF(producto_maestro, ''), 'SIN PRODUCTO') as producto_maestro,
            COUNT(*) as total,
            COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas,
            COUNT(CASE WHEN cumplido = 0 THEN 1 END) as incumplidas,
            COUNT(CASE WHEN cumplido IS NULL THEN 1 END) as pendientes
        FROM calificaciones
        WHERE semana_anio = :semana AND tiene_evaluacion = TRUE
        GROUP BY zona, COALESCE(NULLIF(producto_maestro, ''), 'SIN PRODUCTO')
        ORDER BY zona ASC, producto_maestro ASC;
    """), {'semana': semana}).fetchall()

    zonas_por_cultivo = []
    for zp in zona_prod_rows:
        tot_zp = int(zp.total or 0)
        zonas_por_cultivo.append({
            'zona': zp.zona,
            'producto_maestro': zp.producto_maestro,
            'total': tot_zp,
            'cumplidas': int(zp.cumplidas or 0),
            'incumplidas': int(zp.incumplidas or 0),
            'pendientes': int(zp.pendientes or 0),
            'pct': round((zp.cumplidas / tot_zp * 100), 1) if tot_zp > 0 else 0.0
        })

    # Tendencia semanal individual por Cultivo
    trend_prod_rows = db.session.execute(text("""
        SELECT 
            semana_anio,
            COALESCE(NULLIF(producto_maestro, ''), 'SIN PRODUCTO') as producto_maestro,
            COUNT(*) as total,
            COUNT(CASE WHEN cumplido = 1 THEN 1 END) as cumplidas
        FROM calificaciones
        WHERE tiene_evaluacion = TRUE
        GROUP BY semana_anio, COALESCE(NULLIF(producto_maestro, ''), 'SIN PRODUCTO')
        ORDER BY semana_anio ASC, producto_maestro ASC;
    """)).fetchall()

    tendencia_por_cultivo = {}
    for tp in trend_prod_rows:
        pm = tp.producto_maestro
        if pm not in tendencia_por_cultivo:
            tendencia_por_cultivo[pm] = []
        tot_tp = int(tp.total or 0)
        c_tp = int(tp.cumplidas or 0)
        tendencia_por_cultivo[pm].append({
            'semana': int(tp.semana_anio),
            'total': tot_tp,
            'cumplidas': c_tp,
            'pct': round((c_tp / tot_tp * 100), 1) if tot_tp > 0 else 0.0
        })

    # Matriz Dinámica Estilo Power BI: Auxiliar × Cultivo + Drill-down de tareas que se quedaron (0 o Pendientes)
    detalle_aux_rows = db.session.execute(text("""
        SELECT 
            id,
            COALESCE(NULLIF(auxiliar, ''), 'Sin Auxiliar') as auxiliar,
            COALESCE(NULLIF(supervisor, ''), 'Sin Supervisor') as supervisor,
            COALESCE(NULLIF(producto_maestro, ''), 'SIN PRODUCTO') as producto_maestro,
            zona,
            bloque,
            actividad_tipo,
            cama_inicial,
            cama_final,
            sufijo,
            edad_real,
            labor,
            cumplido,
            observacion,
            es_pendiente_anterior,
            semana_origen
        FROM calificaciones
        WHERE semana_anio = :semana AND tiene_evaluacion = TRUE
        ORDER BY auxiliar ASC, producto_maestro ASC, zona ASC, bloque ASC, cama_inicial ASC;
    """), {'semana': semana}).fetchall()

    cultivos_set = set()
    aux_matrix_map = {}

    for r in detalle_aux_rows:
        aux_name = r.auxiliar
        prod_name = r.producto_maestro
        cultivos_set.add(prod_name)

        if aux_name not in aux_matrix_map:
            aux_matrix_map[aux_name] = {
                'auxiliar': aux_name,
                'supervisores_set': set(),
                'total': 0,
                'cumplidas': 0,
                'incumplidas': 0,
                'pendientes': 0,
                'quedaron': 0,
                'pct_global': 0.0,
                'cultivos': {}
            }

        entry = aux_matrix_map[aux_name]
        if r.supervisor and r.supervisor != 'Sin Supervisor':
            entry['supervisores_set'].add(r.supervisor)

        if prod_name not in entry['cultivos']:
            entry['cultivos'][prod_name] = {
                'producto': prod_name,
                'total': 0,
                'cumplidas': 0,
                'incumplidas': 0,
                'pendientes': 0,
                'quedaron': 0,
                'pct': 0.0,
                'tareas_quedaron': []
            }

        c_entry = entry['cultivos'][prod_name]
        entry['total'] += 1
        c_entry['total'] += 1

        if r.cumplido == 1:
            entry['cumplidas'] += 1
            c_entry['cumplidas'] += 1
        else:
            if r.cumplido == 0:
                entry['incumplidas'] += 1
                c_entry['incumplidas'] += 1
                estado_txt = 'Incumplida (0)'
            else:
                entry['pendientes'] += 1
                c_entry['pendientes'] += 1
                estado_txt = 'Pendiente'

            entry['quedaron'] += 1
            c_entry['quedaron'] += 1

            suf = f" {r.sufijo}" if r.sufijo else ""
            c_entry['tareas_quedaron'].append({
                'id': r.id,
                'zona': r.zona,
                'bloque': clean_block_code(r.bloque),
                'bloque_raw': r.bloque,
                'camas': f"{r.cama_inicial}-{r.cama_final}{suf}",
                'actividad_tipo': r.actividad_tipo,
                'edad_real': r.edad_real,
                'labor': r.labor,
                'estado': estado_txt,
                'es_incumplida': bool(r.cumplido == 0),
                'es_arrastre': bool(r.es_pendiente_anterior),
                'semana_origen': r.semana_origen,
                'observacion': r.observacion or ''
            })

    cultivos_columnas = sorted(cultivos_set)
    matriz_auxiliares = []
    for aux_name in sorted(aux_matrix_map.keys()):
        item = aux_matrix_map[aux_name]
        item['supervisor'] = ", ".join(sorted(item['supervisores_set'])) if item['supervisores_set'] else 'Sin Supervisor'
        del item['supervisores_set']
        item['pct_global'] = round((item['cumplidas'] / item['total'] * 100), 1) if item['total'] > 0 else 0.0
        for p_name, c_data in item['cultivos'].items():
            c_data['pct'] = round((c_data['cumplidas'] / c_data['total'] * 100), 1) if c_data['total'] > 0 else 0.0
        matriz_auxiliares.append(item)

    sin_match_count = db.session.execute(text("SELECT COUNT(*) FROM calificaciones WHERE semana_anio = :semana AND tiene_evaluacion = FALSE;"), {'semana': semana}).scalar() or 0
    config_mapeos = load_mapeos_config()

    return {
        'semanas': [dict(s._mapping) for s in semanas],
        'semanas_todas': semanas_todas,
        'semana_estado': semana_estado,
        'fecha_cierre': fecha_cierre,
        'kpi_global': kpi_dict,
        'pct_cumplimiento': pct_cumplimiento,
        'por_zona': zonas_list,
        'por_producto': productos_list,
        'por_personal': personal_list,
        'por_supervisor': supervisores_list,
        'tendencia_finca': tendencia_finca,
        'tendencia_multisemana': tendencia_multisemana,
        'cultivos_columnas': cultivos_columnas,
        'matriz_auxiliares': matriz_auxiliares,
        'sin_match_count': sin_match_count,
        'config_mapeos': config_mapeos,
        'zonas_por_cultivo': zonas_por_cultivo,
        'tendencia_por_cultivo': tendencia_por_cultivo
    }
