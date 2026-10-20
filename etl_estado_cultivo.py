"""
==============================================================================
PROYECTO: DW RV
ARCHIVO: etl_estado_cultivo.py (Ubicado en la raíz de tu proyecto para fácil acceso)
DESCRIPCIÓN: La base en dbeaver es florsani y dim bloques y camas inyecto yo
             3. Reconstrucción Histórica de Eventos (Event Sourcing):
                - Siembras podas se mueven vs el tracking del plano
                ==============================================================================
"""

import os
import re
import unicodedata
import pandas as pd
import numpy as np
from datetime import datetime
from sqlalchemy import create_engine, text

# Rutas del proyecto
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FOLDER_EXCEL = os.path.join(BASE_DIR, "estados de cultivo")

# Conexión PostgreSQL (DBeaver: localhost:5432, base: florsani, user: postgres, pass: admin)
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "admin")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "florsani")
DB_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def normalizar_encabezado(col: str) -> str:
    """
    Normaliza los encabezados de la fila 5 ya q ahi es la semana de transacción de los planos
    - Limpia tildes espacios y guiones porque los titulos vienen mal"""
    col = str(col)
    col = unicodedata.normalize('NFKD', col).encode('ASCII', 'ignore').decode('utf-8')
    col = col.lower().strip()
    col = re.sub(r'#', 'num_', col)
    col = re.sub(r'[^a-z0-9]+', '_', col)
    return re.sub(r'_+', '_', col).strip('_')


def cargar_dimensiones_fisicas(engine, archivo_maestro_path):
    """
    Lee el archivo más reciente (2026-38) para construir el catálogo
    físico inmutable de Bloques y Camas Físicas.
    """
    print(f"\n[1] Extrayendo catálogo físico desde: {os.path.basename(archivo_maestro_path)}...")
    
    # Leer fila 5 (header=4 en base 0)
    df_raw = pd.read_excel(archivo_maestro_path, sheet_name="DATOS", header=4)
    df = df_raw.rename(columns={c: normalizar_encabezado(c) for c in df_raw.columns})
    df = df[df['blq'].notna() & df['num_cama'].notna()].copy()
    
    df['blq_clean'] = df['blq'].astype(str).str.strip()
    df['sufijo_clean'] = df['sufijo'].fillna('').astype(str).str.strip().str.upper()
    df['num_cama_int'] = pd.to_numeric(df['num_cama'], errors='coerce').fillna(0).astype(int)
    
    # Código del Bloque (ej: '38CA', '38CB', '11A')
    df['codigo_bloque'] = np.where(
        df['sufijo_clean'] != '',
        df['blq_clean'] + df['sufijo_clean'],
        df['blq_clean']
    )
    
    # Identificador de Cama física
    df['id_cama_base'] = df['codigo_bloque'] + '-' + df['sufijo_clean'] + '-C' + df['num_cama_int'].astype(str)
    df['consecutivo'] = df.groupby('id_cama_base').cumcount() + 1
    df['id_cama'] = np.where(
        df['consecutivo'] > 1,
        df['id_cama_base'] + '_' + df['consecutivo'].astype(str),
        df['id_cama_base']
    )

    # 1. Poblar DIM_BLOQUES (Catálogo físico puro sin variedad)
    bloques_agg = df.groupby('codigo_bloque').agg({
        'blq_clean': 'first',
        'sufijo_clean': 'first',
        'zona': 'first',
        'tecnico_de_riego': 'first',
        'id_cama': 'count',
        'cama_estandar': lambda x: pd.to_numeric(x, errors='coerce').sum(),
        'metros_netos': lambda x: pd.to_numeric(x, errors='coerce').sum(),
        'metros_brutos': lambda x: pd.to_numeric(x, errors='coerce').sum(),
        'ha': lambda x: pd.to_numeric(x, errors='coerce').sum()
    }).reset_index()

    bloques_agg.columns = [
        'codigo_bloque', 'blq', 'sufijo', 'zona', 'tecnico_de_riego',
        'total_camas_fisicas', 'total_camas_estandar',
        'area_neta_m2', 'area_bruta_m2', 'ha'
    ]
    bloques_agg['id_finca'] = 1  # Finca PYGAN / Florsani

    print(f"    -> Insertando {len(bloques_agg)} bloques en 'dim_bloques'...")
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dim_bloques CASCADE;"))
        bloques_agg.to_sql(name="dim_bloques", con=conn, if_exists="append", index=False)
    print("    [OK] Dimensión 'dim_bloques' actualizada.")

    # 2. Poblar DIM_CAMAS (Detalle de cada cama física)
    camas_df = pd.DataFrame({
        'id_cama': df['id_cama'],
        'codigo_bloque': df['codigo_bloque'],
        'id_finca': 1,
        'blq': df['blq_clean'],
        'sufijo': df['sufijo_clean'],
        'num_cama': df['num_cama_int'],
        'largo_de_cama': pd.to_numeric(df.get('largo_de_cama', 0), errors='coerce').fillna(0),
        'cama_estandar': pd.to_numeric(df['cama_estandar'], errors='coerce').fillna(0),
        'metros_netos': pd.to_numeric(df['metros_netos'], errors='coerce').fillna(0),
        'metros_brutos': pd.to_numeric(df['metros_brutos'], errors='coerce').fillna(0),
        'ha': pd.to_numeric(df['ha'], errors='coerce').fillna(0),
        'zona': df['zona'].fillna('SIN ZONA').astype(str),
        'tecnico_de_riego': df['tecnico_de_riego'].fillna('SIN ASIGNAR').astype(str)
    })

    print(f"    -> Insertando {len(camas_df)} camas en 'dim_camas'...")
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dim_camas CASCADE;"))
        camas_df.to_sql(name="dim_camas", con=conn, if_exists="append", index=False)
    print("    [OK] Dimensión 'dim_camas' actualizada.")

    return df[['id_cama', 'codigo_bloque']].drop_duplicates()


def reconstruir_eventos_historicos(engine, df_mapping_camas):
    """
    Recorre los archivos en la carpeta 'estados de cultivo' y detecta:
    - Siembras
    - Podas (1..4..)
    - Tumbas (transición de activo a VACIO)
    """
    print("\n[2] Reconstruyendo eventos históricos desde 'estados de cultivo'...")
    archivos = sorted([f for f in os.listdir(FOLDER_EXCEL) if f.startswith("Estado Cultivo PYGAN") and f.endswith(".xlsx")])
    print(f"    Total archivos encontrados: {len(archivos)}")

    estado_previo_camas = {}
    eventos_detectados = []
    eventos_unicos_set = set()

    # Procesar una muestra regular de archivos para velocidad o todos los archivos
    archivos_a_procesar = archivos[::4]
    if archivos[-1] not in archivos_a_procesar:
        archivos_a_procesar.append(archivos[-1])

    for fname in archivos_a_procesar:
        fpath = os.path.join(FOLDER_EXCEL, fname)
        semana_str = fname.replace("Estado Cultivo PYGAN ", "").replace(".xlsx", "").strip()
        partes = semana_str.split("-")
        anio_int, sem_int = int(partes[0]), int(partes[1])
        semana_campo_val = (anio_int % 100) * 100 + sem_int
        fecha_semana = datetime.strptime(f"{anio_int}-W{sem_int:02d}-1", "%Y-W%W-%w").date()

        print(f"    -> Leyendo: {fname}...")
        df = pd.read_excel(
            fpath, sheet_name="DATOS", header=4,
            usecols=lambda c: any(k in str(c).upper() for k in [
                'BLQ', 'SUFIJO', '# CAMA', 'AC', '# PODA', 'FECHA SIEM', 
                'FECHA PODA', 'PRODUCTO MAESTRO', 'VARIEDADES ELITE'
            ])
        )
        df = df.rename(columns={c: normalizar_encabezado(c) for c in df.columns})
        df = df[df['blq'].notna() & df['num_cama'].notna()].copy()

        df['blq_clean'] = df['blq'].astype(str).str.strip()
        df['sufijo_clean'] = df['sufijo'].fillna('').astype(str).str.strip().str.upper()
        df['num_cama_int'] = pd.to_numeric(df['num_cama'], errors='coerce').fillna(0).astype(int)
        df['codigo_bloque'] = np.where(df['sufijo_clean'] != '', df['blq_clean'] + df['sufijo_clean'], df['blq_clean'])
        df['id_cama_base'] = df['codigo_bloque'] + '-' + df['sufijo_clean'] + '-C' + df['num_cama_int'].astype(str)

        for _, row in df.iterrows():
            id_cama = row['id_cama_base']
            cod_bloque = row['codigo_bloque']
            actividad = str(row.get('ac', '')).strip().upper()
            num_poda_raw = str(row.get('num_poda', '')).strip().upper()
            prod_maestro = str(row.get('producto_maestro', '')).strip()
            variedad = str(row.get('variedades_elite', '')).strip()
            if not variedad or variedad == 'NAN':
                variedad = 'DESCONOCIDA'

            f_siem = pd.to_datetime(row.get('fecha_siem'), errors='coerce')
            f_poda = pd.to_datetime(row.get('fecha_poda'), errors='coerce')

            # 1. SIEMBRA
            if pd.notnull(f_siem):
                f_siem_date = f_siem.date()
                key_siem = (id_cama, 'SIEMBRA', f_siem_date, 0)
                if key_siem not in eventos_unicos_set:
                    eventos_unicos_set.add(key_siem)
                    eventos_detectados.append({
                        'id_cama': id_cama,
                        'codigo_bloque': cod_bloque,
                        'fecha_evento': f_siem_date,
                        'semana_campo': (f_siem_date.year % 100) * 100 + int(f_siem_date.strftime("%W")),
                        'tipo_evento': 'SIEMBRA',
                        'num_poda': 0,
                        'producto_maestro': prod_maestro,
                        'variedad': variedad,
                        'origen_evento': fname,
                        'estado_evento': 'ACTIVO'
                    })

            # 2. PODA (con número de poda 1, 2, 3, 4...)
            if pd.notnull(f_poda) and actividad == 'PODA':
                f_poda_date = f_poda.date()
                poda_num = re.findall(r'\d+', num_poda_raw)
                poda_num_val = int(poda_num[0]) if poda_num else 1

                key_poda = (id_cama, 'PODA', f_poda_date, poda_num_val)
                if key_poda not in eventos_unicos_set:
                    eventos_unicos_set.add(key_poda)
                    eventos_detectados.append({
                        'id_cama': id_cama,
                        'codigo_bloque': cod_bloque,
                        'fecha_evento': f_poda_date,
                        'semana_campo': (f_poda_date.year % 100) * 100 + int(f_poda_date.strftime("%W")),
                        'tipo_evento': 'PODA',
                        'num_poda': poda_num_val,
                        'producto_maestro': prod_maestro,
                        'variedad': variedad,
                        'origen_evento': fname,
                        'estado_evento': 'ACTIVO'
                    })

            # 3. DETECCIÓN AUTOMÁTICA DE TUMBAS (Transición a VACIO)
            estado_anterior = estado_previo_camas.get(id_cama)
            if actividad == 'VACIO' and estado_anterior in ['SIEMBRA', 'PODA']:
                key_tumba = (id_cama, 'TUMBA', fecha_semana, None)
                if key_tumba not in eventos_unicos_set:
                    eventos_unicos_set.add(key_tumba)
                    eventos_detectados.append({
                        'id_cama': id_cama,
                        'codigo_bloque': cod_bloque,
                        'fecha_evento': fecha_semana,
                        'semana_campo': semana_campo_val,
                        'tipo_evento': 'TUMBA',
                        'num_poda': None,
                        'producto_maestro': 'VACIO',
                        'variedad': 'ERRADICADO',
                        'origen_evento': f"Transición a VACIO en {fname}",
                        'estado_evento': 'ACTIVO'
                    })

            estado_previo_camas[id_cama] = actividad

    df_eventos_total = pd.DataFrame(eventos_detectados)

    # Filtrar solo eventos de camas que existan en dim_camas
    with engine.connect() as conn:
        camas_db = set(pd.read_sql("SELECT id_cama FROM dim_camas", conn)['id_cama'])

    df_eventos_validos = df_eventos_total[df_eventos_total['id_cama'].isin(camas_db)].copy()

    print(f"    Insertando {len(df_eventos_validos)} eventos en 'fact_eventos_cultivo'...")
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE fact_eventos_cultivo;"))
        df_eventos_validos.to_sql(
            name="fact_eventos_cultivo",
            con=conn,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=2000
        )
    print("    [OK] 'fact_eventos_cultivo' poblada exitosamente.")


def verificar_resultados(engine):
    """Muestra el resultado final del Bloque 38C y de las tablas."""
    print("\n" + "=" * 80)
    print("HISTORIAL DEL BLOQUE 38C (Subsección A, Cama 1)")
    print("=" * 80)
    with engine.raw_connection() as raw_conn:
        with raw_conn.cursor() as cur:
            cur.execute("""
                SELECT id_cama, codigo_bloque, tipo_evento, num_poda, fecha_evento, 
                       producto_maestro, variedad, origen_evento
                FROM fact_eventos_cultivo
                WHERE codigo_bloque = '38CA' AND id_cama LIKE '%-C1'
                ORDER BY fecha_evento ASC;
            """)
            for r in cur.fetchall():
                poda_str = str(r[3]) if r[3] is not None else "-"
                print(f"  {r[0]:<12} | {r[2]:<8} (Poda {poda_str}) | {str(r[4]):<12} | {r[5]:<10} | {r[6]:<22} | {r[7]}")


def main():
    print("=" * 80)
    print("EJECUTANDO ETL: FLORSANI - ESTADOS DE CULTIVO")
    print("=" * 80)
    
    engine = create_engine(DB_URL)
    archivos_disponibles = sorted([f for f in os.listdir(FOLDER_EXCEL) if f.startswith("Estado Cultivo PYGAN") and f.endswith(".xlsx")])
    if not archivos_disponibles:
        raise FileNotFoundError(f"No se encontraron archivos en: {FOLDER_EXCEL}")
    
    ultimo_archivo = archivos_disponibles[-1]
    print(f"[*] Archivo más reciente detectado automáticamente: {ultimo_archivo}")
    archivo_maestro = os.path.join(FOLDER_EXCEL, ultimo_archivo)
    
    df_camas = cargar_dimensiones_fisicas(engine, archivo_maestro)
    reconstruir_eventos_historicos(engine, df_camas)
    verificar_resultados(engine)
    
    print("\n" + "=" * 80)
    print("¡PROCESO COMPLETADO CON ÉXITO!")
    print("=" * 80)


if __name__ == "__main__":
    main()
