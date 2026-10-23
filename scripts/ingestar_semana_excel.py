"""
==============================================================================
PROYECTO: DATA WAREHOUSE & BACKEND TRANSACCIONAL - GRUPO FLORICULTOR FLORSANI
ARCHIVO: scripts/ingestar_semana_excel.py
DESCRIPCIÓN: Ingestión incremental segura de eventos de campo desde Excel de Estado
             de Cultivo (ej. PYGAN 2026-39.xlsx).
             - Conexión directa a PostgreSQL en la nube (Neon) vía .env
             - NO trunca ni borra el historial histórico.
             - Detecta y agrega únicamente NUEVOS eventos (Siembras, Podas, Tumbas).
             - Sincroniza la vista viva mrt_estado_cultivo_actual y crop_state_records.
USO:
    python scripts/ingestar_semana_excel.py "estados de cultivo/Estado Cultivo PYGAN 2026-39.xlsx"
==============================================================================
"""

import os
import sys
import re
import unicodedata
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Cargar variables de entorno del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

if not DATABASE_URL:
    raise ValueError("ERROR: DATABASE_URL no encontrada en el archivo .env")


def normalizar_encabezado(col: str) -> str:
    col = str(col)
    col = unicodedata.normalize('NFKD', col).encode('ASCII', 'ignore').decode('utf-8')
    col = col.lower().strip()
    col = re.sub(r'#', 'num_', col)
    col = re.sub(r'[^a-z0-9]+', '_', col)
    return re.sub(r'_+', '_', col).strip('_')


def ingestar_archivo(excel_path: str, target_week: str = None, sync_crop_state: bool = True, engine=None):
    if not os.path.exists(excel_path):
        # Intentar en subcarpeta 'estados de cultivo'
        alt_path = os.path.join(BASE_DIR, "estados de cultivo", os.path.basename(excel_path))
        if os.path.exists(alt_path):
            excel_path = alt_path
        else:
            raise FileNotFoundError(f"Archivo no encontrado: {excel_path}")

    filename = os.path.basename(excel_path)
    print("=" * 80)
    print(f"INGESTIÓN INCREMENTAL: {filename}")
    print("=" * 80)

    # Extraer semana del parámetro target_week o del nombre de archivo
    source_str = f"{target_week or ''} {filename}"
    match_semana = re.search(r'(\d{4})[-_](\d{1,2})', source_str)
    if match_semana:
        anio_int = int(match_semana.group(1))
        sem_int = int(match_semana.group(2))
    else:
        now = datetime.now()
        anio_int, sem_int = now.year, now.isocalendar()[1]

    semana_campo_val = (anio_int % 100) * 100 + sem_int
    semana_iso_str = f"{anio_int}-{sem_int:02d}"
    try:
        fecha_semana = datetime.strptime(f"{anio_int}-W{sem_int:02d}-1", "%Y-W%W-%w").date()
    except Exception:
        fecha_semana = datetime.now().date()

    print(f"  • Semana detectada: {semana_iso_str} (semana_campo: {semana_campo_val})")

    if engine is None:
        engine = create_engine(DATABASE_URL)

    # 1. Leer Excel (Hoja DATOS, encabezado fila 5)
    print("  • Leyendo hoja DATOS del archivo Excel...")
    df_raw = pd.read_excel(excel_path, sheet_name="DATOS", header=4)
    df = df_raw.rename(columns={c: normalizar_encabezado(c) for c in df_raw.columns})
    df = df[df['blq'].notna() & df['num_cama'].notna()].copy()

    df['blq_clean'] = df['blq'].astype(str).str.strip()
    df['sufijo_clean'] = df['sufijo'].fillna('').astype(str).str.strip().str.upper()
    df['num_cama_int'] = pd.to_numeric(df['num_cama'], errors='coerce').fillna(0).astype(int)

    # Código bloque y cama
    df['codigo_bloque'] = np.where(
        df['sufijo_clean'] != '',
        df['blq_clean'] + df['sufijo_clean'],
        df['blq_clean']
    )
    df['id_cama_base'] = df['codigo_bloque'] + '-' + df['sufijo_clean'] + '-C' + df['num_cama_int'].astype(str)
    df['consecutivo'] = df.groupby('id_cama_base').cumcount() + 1
    df['id_cama'] = np.where(
        df['consecutivo'] > 1,
        df['id_cama_base'] + '_' + df['consecutivo'].astype(str),
        df['id_cama_base']
    )

    print(f"  • Filas procesadas en Excel: {len(df):,}")

    # 2. Consultar eventos existentes y estado actual en la base de datos
    with engine.connect() as conn:
        q_exist = text("SELECT id_cama, tipo_evento, fecha_evento, COALESCE(num_poda, 0) FROM fact_eventos_cultivo WHERE estado_evento = 'ACTIVO';")
        existing_rows = conn.execute(q_exist).fetchall()
        existing_set = set((r[0], r[1], r[2], r[3]) for r in existing_rows)

        # Cargar camas existentes en dim_camas
        q_camas = text("SELECT id_cama FROM dim_camas;")
        camas_db = set(r[0] for r in conn.execute(q_camas).fetchall())

        # Estado actual previo por cama para detectar transiciones a VACIO (TUMBA)
        q_prev = text("SELECT id_cama, estado_cultivo FROM mrt_estado_cultivo_actual;")
        estado_previo_camas = {r[0]: str(r[1] or '').strip().upper() for r in conn.execute(q_prev).fetchall()}

    print(f"  • Eventos activos previamente en base de datos: {len(existing_set):,}")

    nuevos_eventos = []
    camas_a_sincronizar = {}

    for _, row in df.iterrows():
        id_cama = row['id_cama']
        cod_bloque = row['codigo_bloque']
        actividad = str(row.get('ac', '')).strip().upper()
        num_poda_raw = str(row.get('num_poda', '')).strip().upper()
        prod_maestro = str(row.get('producto_maestro', '')).strip()
        variedad = str(row.get('variedades_elite', '')).strip() or str(row.get('variedad', '')).strip()
        if not variedad or variedad.upper() == 'NAN':
            variedad = 'DESCONOCIDA'

        f_siem = pd.to_datetime(row.get('fecha_siem'), errors='coerce')
        f_poda = pd.to_datetime(row.get('fecha_poda'), errors='coerce')
        sem_siem_excel = pd.to_numeric(row.get('sem_siem'), errors='coerce')
        sem_poda_excel = pd.to_numeric(row.get('sema_poda'), errors='coerce')

        # Mantener métricas físicas de la cama sincronizadas con el archivo (particiones, cama estándar, metros)
        camas_a_sincronizar[id_cama] = {
            'id_cama': id_cama,
            'codigo_bloque': cod_bloque,
            'id_finca': 1,
            'blq': row['blq_clean'],
            'sufijo': row['sufijo_clean'],
            'num_cama': row['num_cama_int'],
            'cama_estandar': float(pd.to_numeric(row.get('cama_estandar', 1.0), errors='coerce') or 1.0),
            'metros_netos': float(pd.to_numeric(row.get('metros_netos', 30.0), errors='coerce') or 30.0),
            'metros_brutos': float(pd.to_numeric(row.get('metros_brutos', 30.0), errors='coerce') or 30.0),
            'ha': float(pd.to_numeric(row.get('ha', 0.0), errors='coerce') or 0.0),
            'zona': str(row.get('zona', 'SIN ZONA')).strip(),
            'tecnico_de_riego': str(row.get('tecnico_de_riego', 'C1')).strip()
        }

        # 1. SIEMBRA
        if pd.notnull(f_siem):
            f_siem_date = f_siem.date()
            key_s = (id_cama, 'SIEMBRA', f_siem_date, 0)
            if key_s not in existing_set:
                existing_set.add(key_s)
                sc_siem = int(sem_siem_excel) if pd.notnull(sem_siem_excel) and 2000 <= int(sem_siem_excel) <= 3000 else ((f_siem_date.year % 100) * 100 + int(f_siem_date.strftime("%W")))
                nuevos_eventos.append({
                    'id_cama': id_cama,
                    'codigo_bloque': cod_bloque,
                    'fecha_evento': f_siem_date,
                    'semana_campo': sc_siem,
                    'tipo_evento': 'SIEMBRA',
                    'num_poda': 0,
                    'producto_maestro': prod_maestro,
                    'variedad': variedad,
                    'origen_evento': filename,
                    'estado_evento': 'ACTIVO'
                })

        # 2. PODA
        if pd.notnull(f_poda) and actividad == 'PODA':
            f_poda_date = f_poda.date()
            poda_digits = re.findall(r'\d+', num_poda_raw)
            poda_num_val = int(poda_digits[0]) if poda_digits else 1

            key_p = (id_cama, 'PODA', f_poda_date, poda_num_val)
            if key_p not in existing_set:
                existing_set.add(key_p)
                sc_poda = int(sem_poda_excel) if pd.notnull(sem_poda_excel) and 2000 <= int(sem_poda_excel) <= 3000 else ((f_poda_date.year % 100) * 100 + int(f_poda_date.strftime("%W")))
                nuevos_eventos.append({
                    'id_cama': id_cama,
                    'codigo_bloque': cod_bloque,
                    'fecha_evento': f_poda_date,
                    'semana_campo': sc_poda,
                    'tipo_evento': 'PODA',
                    'num_poda': poda_num_val,
                    'producto_maestro': prod_maestro,
                    'variedad': variedad,
                    'origen_evento': filename,
                    'estado_evento': 'ACTIVO'
                })

        # 3. TUMBA (Transición a VACIO respecto al estado previo en TPSR)
        estado_anterior = estado_previo_camas.get(id_cama)
        if actividad == 'VACIO' and estado_anterior in ('SIEMBRA', 'PODA'):
            key_t = (id_cama, 'TUMBA', fecha_semana, 0)
            if key_t not in existing_set:
                existing_set.add(key_t)
                nuevos_eventos.append({
                    'id_cama': id_cama,
                    'codigo_bloque': cod_bloque,
                    'fecha_evento': fecha_semana,
                    'semana_campo': semana_campo_val,
                    'tipo_evento': 'TUMBA',
                    'num_poda': None,
                    'producto_maestro': 'VACIO',
                    'variedad': 'ERRADICADO',
                    'origen_evento': f"Transición a VACIO en {filename}",
                    'estado_evento': 'ACTIVO'
                })

    # Guardar en base de datos
    with engine.begin() as conn:
        # Sincronizar o insertar camas físicas
        if camas_a_sincronizar:
            print(f"  • Sincronizando {len(camas_a_sincronizar)} camas físicas en 'dim_camas'...")
            for c in camas_a_sincronizar.values():
                conn.execute(text("""
                    INSERT INTO dim_bloques (codigo_bloque, id_finca, blq, sufijo, zona, tecnico_de_riego)
                    VALUES (:cb, 1, :blq, :suf, :z, :t)
                    ON CONFLICT (codigo_bloque) DO NOTHING;
                """), {'cb': c['codigo_bloque'], 'blq': c['blq'], 'suf': c['sufijo'], 'z': c['zona'], 't': c['tecnico_de_riego']})

                conn.execute(text("""
                    INSERT INTO dim_camas (
                        id_cama, codigo_bloque, id_finca, blq, sufijo, num_cama,
                        cama_estandar, metros_netos, metros_brutos, ha, zona, tecnico_de_riego
                    ) VALUES (
                        :id_cama, :codigo_bloque, :id_finca, :blq, :sufijo, :num_cama,
                        :cama_estandar, :metros_netos, :metros_brutos, :ha, :zona, :tecnico_de_riego
                    ) ON CONFLICT (id_cama) DO UPDATE SET
                        cama_estandar = EXCLUDED.cama_estandar,
                        metros_netos = EXCLUDED.metros_netos,
                        metros_brutos = EXCLUDED.metros_brutos,
                        ha = EXCLUDED.ha,
                        zona = COALESCE(EXCLUDED.zona, dim_camas.zona);
                """), c)

        # Insertar nuevos eventos
        if nuevos_eventos:
            print(f"  • Insertando {len(nuevos_eventos)} nuevos eventos en 'fact_eventos_cultivo'...")
            df_new = pd.DataFrame(nuevos_eventos)
            df_new.to_sql(
                name="fact_eventos_cultivo",
                con=conn,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=1000
            )
            print("    [OK] Nuevos eventos guardados exitosamente.")
        else:
            print("  • No se detectaron eventos nuevos (todos ya constan en la base).")

        if sync_crop_state:
            print(f"  • Sincronizando semana {semana_iso_str} en 'crop_state_records' desde 'mrt_estado_cultivo_actual'...")
            conn.execute(text("DELETE FROM crop_state_records WHERE week IN (:w_iso, :w_raw);"), {
                'w_iso': semana_iso_str,
                'w_raw': str(semana_campo_val)
            })
            conn.execute(text("""
                INSERT INTO crop_state_records (
                    week, block_full, block_num, bed_num, suffix, crop_master, 
                    product_name, variety, standard_bed, zone, real_age, status_raw, created_at
                )
                SELECT 
                    :w_iso,
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
            """), {'w_iso': semana_iso_str})

    return {
        'nuevos_eventos': len(nuevos_eventos),
        'nuevas_camas': len(camas_a_crear),
        'semana_iso': semana_iso_str,
        'semana_campo': semana_campo_val
    }


if __name__ == '__main__':
    archivo = sys.argv[1] if len(sys.argv) > 1 else "estados de cultivo/Estado Cultivo PYGAN 2026-39.xlsx"
    ingestar_archivo(archivo)
