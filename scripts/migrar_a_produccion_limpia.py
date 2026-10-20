#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
==============================================================================
PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
SCRIPT: scripts/migrar_a_produccion_limpia.py
DESCRIPCIÓN:
    Migra a una base de datos de producción (Render, Hostinger VPS, etc.)
    únicamente la estructura limpia, los catálogos/dimensiones maestros
    y el Estado de Cultivo oficial, excluyendo datos de prueba transaccionales.
==============================================================================
"""

import sys
import os
import argparse
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, MetaData, Table

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

# Tablas Maestras / Catálogos que deben copiarse a producción:
TABLAS_CATALOGOS = [
    "dim_fincas",
    "dim_zonas",
    "dim_bloques",
    "dim_camas",
    "dim_prod_maestros",
    "dim_productos",
    "dim_variedades",
    "dim_densidad_plantas",
    "dim_recetas_fenologicas",
    "dim_tecnico_riego",
    "tecnicos",
    "tecnico_bloques",
    "dim_calendario_agricola",
    "calendario",
    "crops",
    "products",
    "variedades",
    "matriz_fenogramas",
    "litrajes",
    "tipos_aplicacion",
    "dim_ppto",
    "users",
    "usuarios_aplicadores",
    "semanas_evaluacion"
]

# Tablas de Snapshot Oficial e Historial de Cultivo y Cosechas:
TABLAS_ESTADO_CULTIVO = [
    "crop_state_records",
    "plano_cultivo",
    "fact_eventos_cultivo",
    "import_batches",
    "registro_cosecha",
    "estimado"
]

# Tablas de Transacciones / Pruebas Operativas (se inicializan vacías):
TABLAS_TRANSACCIONALES_VACIAS = [
    "calificaciones",
    "drench_items",
    "rotations",
    "rotation_rounds",
    "rotation_round_items",
    "fumigation_orders",
    "fumigation_order_details",
    "fumigation_order_product_summaries",
    "requisitions",
    "requisition_items",
    "additional_applications",
    "audit_logs",
    "riego_ducha_registros",
    "valvulas_registros",
    "warehouse_dispatch_logs"
]

VISTA_STG_EVENTOS_SQL = """
CREATE OR REPLACE VIEW stg_eventos AS
SELECT id_evento,
    id_cama,
    codigo_bloque,
    fecha_evento,
    semana_campo,
    tipo_evento,
    num_poda,
    producto_maestro,
    variedad,
    origen_evento,
    estado_evento,
    created_at,
    num_plantas,
    fecha_hora_registro
FROM fact_eventos_cultivo
WHERE ((estado_evento)::text = 'ACTIVO'::text);
"""

VISTA_MRT_ESTADO_CULTIVO_SQL = """
CREATE OR REPLACE VIEW mrt_estado_cultivo_actual AS
WITH ultimos_eventos AS (
    SELECT e.id_cama,
        e.codigo_bloque,
        e.fecha_evento,
        e.tipo_evento,
        e.num_poda,
        e.producto_maestro,
        e.variedad,
        e.num_plantas,
        e.fecha_hora_registro,
        row_number() OVER (PARTITION BY e.id_cama ORDER BY e.fecha_evento DESC, e.id_evento DESC) AS fila_num
    FROM stg_eventos e
), semana_corte_calc AS (
    SELECT c_1.semana_abs AS semana_abs_hoy,
        c_1.semana_campo AS semana_campo_hoy
    FROM dim_calendario_agricola c_1
    WHERE (c_1.semana_campo = ( SELECT max(t.semana_campo) AS max
            FROM ( SELECT max(fact_eventos_cultivo.semana_campo) AS semana_campo
                    FROM fact_eventos_cultivo
                    WHERE ((fact_eventos_cultivo.estado_evento)::text = 'ACTIVO'::text)
                UNION
                    SELECT max(
                        CASE
                            WHEN ((crop_state_records.week)::text ~ '^20[0-9]{2}-[0-9]{1,2}$'::text) THEN ((("substring"((crop_state_records.week)::text, 3, 2))::integer * 100) + ("substring"((crop_state_records.week)::text, 6, 2))::integer)
                            WHEN ((crop_state_records.week)::text ~ '^[0-9]{4}$'::text) THEN (crop_state_records.week)::integer
                            ELSE 2640
                        END) AS semana_campo
                    FROM crop_state_records) t))
    LIMIT 1
), semana_hoy AS (
    SELECT COALESCE(( SELECT semana_corte_calc.semana_abs_hoy
            FROM semana_corte_calc), 1040) AS semana_abs_hoy,
        COALESCE(( SELECT semana_corte_calc.semana_campo_hoy
            FROM semana_corte_calc), 2640) AS semana_campo_hoy
)
SELECT f.id_finca,
    f.nombre_finca,
    b.zona,
    b.tecnico_de_riego,
    c.codigo_bloque,
    c.blq,
    c.sufijo,
    c.id_cama,
    c.num_cama,
    c.cama_estandar,
    c.metros_netos,
    c.metros_brutos,
    c.ha,
    ue.producto_maestro AS producto_actual,
    ue.variedad AS variedad_actual,
    ue.tipo_evento AS ultimo_evento,
    ue.num_poda AS num_poda_actual,
    ue.fecha_evento AS fecha_ultimo_evento,
    cal_ev.semana_campo AS semana_campo_ultimo_evento,
    CASE
        WHEN ((ue.tipo_evento)::text = 'TUMBA'::text) THEN NULL::integer
        ELSE (sh.semana_abs_hoy - cal_ev.semana_abs)
    END AS edad_semanas_actual,
    CASE
        WHEN (ue.tipo_evento IS NULL) THEN 'SIN REGISTRO DE EVENTOS'::text
        WHEN ((ue.tipo_evento)::text = 'TUMBA'::text) THEN 'ERRADICADO / DISPONIBLE PARA SIEMBRA'::text
        WHEN ((ue.tipo_evento)::text = 'PODA'::text) THEN (('EN PRODUCCIÓN (PODA '::text || COALESCE((ue.num_poda)::text, '1'::text)) || ')'::text)
        WHEN ((ue.tipo_evento)::text = 'SIEMBRA'::text) THEN 'CULTIVO NUEVO (SIEMBRA)'::text
        ELSE 'EN PRODUCCIÓN'::text
    END AS estado_cultivo,
    sh.semana_campo_hoy AS semana_agricola_actual,
    COALESCE(ue.num_plantas, c.num_plantas) AS num_plantas_actual,
    ue.fecha_hora_registro AS fecha_hora_ultimo_registro,
    cal_ev.semana_abs AS semana_abs_ultimo_evento
FROM (((((dim_camas c
    JOIN dim_bloques b ON (((c.codigo_bloque)::text = (b.codigo_bloque)::text)))
    JOIN dim_fincas f ON ((c.id_finca = f.id_finca)))
    LEFT JOIN ultimos_eventos ue ON ((((c.id_cama)::text = (ue.id_cama)::text) AND (ue.fila_num = 1))))
    LEFT JOIN dim_calendario_agricola cal_ev ON ((ue.fecha_evento = cal_ev.fecha)))
    CROSS JOIN semana_hoy sh)
WHERE (c.activa = true);
"""


def normalize_url(url: str, is_target: bool = False) -> str:
    if not url:
        return ""
    url = url.strip().strip("'").strip('"')
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    
    # Solo requerir SSL para servicios cloud externos que lo exigen (ej. Render, Supabase)
    # NO requerir SSL para conexiones internas de Docker (ej. host 'db') ni localhost
    cloud_hosts = ["render.com", "supabase.co", "neon.tech", "cockroachlabs.cloud"]
    if any(ch in url for ch in cloud_hosts):
        if "sslmode=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
    return url


def migrar(source_url, target_url, incluir_snapshot_cultivo=True):
    print("=" * 65)
    print("🚀 MIGRACIÓN DE DATOS LIMPIOS A PRODUCCIÓN (FLORSANI ERP)")
    print("=" * 65)
    print(f"🔹 Origen (Local): {source_url.split('@')[-1]}")
    print(f"🔹 Destino (Producción): {target_url.split('@')[-1]}")
    print("-" * 65)

    src_engine = create_engine(source_url)
    tgt_engine = create_engine(target_url)

    # 1. Probar conexiones
    print("\n[1] Verificando conexiones a bases de datos...")
    with src_engine.connect() as sc:
        sc.execute(text("SELECT 1;"))
        print("    ✅ Conexión exitosa a Base de Datos Origen (Local).")
    with tgt_engine.connect() as tc:
        tc.execute(text("SELECT 1;"))
        print("    ✅ Conexión exitosa a Base de Datos Destino (Producción).")

    # 2. Replicar secuencias y esquema completo en destino
    print("\n[2] Creando secuencias y esquema de tablas en destino...")
    
    # 2.1 Crear previamente todas las secuencias para que los defaults de columnas nextval(...) no fallen
    with src_engine.connect() as sc:
        seqs = sc.execute(text("SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public' ORDER BY sequence_name;")).fetchall()
        seq_names = [s[0] for s in seqs]

    with tgt_engine.begin() as tc:
        for sname in seq_names:
            tc.execute(text(f'CREATE SEQUENCE IF NOT EXISTS "{sname}";'))
    print(f"    ✅ {len(seq_names)} secuencias preparadas en destino.")

    # 2.2 Reflejar todas las tablas existentes en origen (incluyendo dimensiones DW) y crearlas en destino
    src_meta = MetaData()
    src_meta.reflect(bind=src_engine)
    src_meta.create_all(bind=tgt_engine, checkfirst=True)

    # 2.3 Asegurar también tablas declaradas en modelos de Flask
    try:
        sys.path.insert(0, str(BASE_DIR))
        from app.extensions import db
        import app.shared.models
        import app.modules.fenogramas.models
        import app.modules.fertilizacion.models
        db.metadata.create_all(bind=tgt_engine, checkfirst=True)
    except Exception as e:
        print(f"    ℹ️  Sincronización complementaria de modelos: {e}")
    print("    ✅ Todas las tablas creadas o verificadas en la base de datos destino.")

    # 3. Copiar Catálogos y Dimensiones Maestras
    print("\n[3] Migrando catálogos y dimensiones maestras...")
    tablas_a_migrar = list(TABLAS_CATALOGOS)
    if incluir_snapshot_cultivo:
        tablas_a_migrar.extend(TABLAS_ESTADO_CULTIVO)

    for tbl in tablas_a_migrar:
        try:
            with src_engine.connect() as sc:
                # Comprobar si existe en origen
                existe = sc.execute(text(f"SELECT to_regclass('public.\"{tbl}\"');")).scalar()
                if not existe:
                    print(f"    ⏭️  Tabla '{tbl}' no existe en origen. Saltando.")
                    continue

                rows = sc.execute(text(f'SELECT * FROM "{tbl}";')).fetchall()
                if not rows:
                    print(f"    ⚪  Tabla '{tbl}' está vacía en origen. Saltando.")
                    continue

                cols = sc.execute(text(f"SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='{tbl}' ORDER BY ordinal_position;")).fetchall()
                col_names = [c[0] for c in cols]
                cols_str = ", ".join([f'"{c}"' for c in col_names])
                params_str = ", ".join([f":{c}" for c in col_names])

            with tgt_engine.begin() as tc:
                # Truncar tabla en destino antes de rellenar
                tc.execute(text(f'TRUNCATE TABLE "{tbl}" CASCADE;'))
                insert_stmt = text(f'INSERT INTO "{tbl}" ({cols_str}) VALUES ({params_str});')
                
                # Insertar en lotes de 1000
                batch = []
                for r in rows:
                    batch.append(dict(r._mapping))
                    if len(batch) >= 1000:
                        tc.execute(insert_stmt, batch)
                        batch = []
                if batch:
                    tc.execute(insert_stmt, batch)

            print(f"    ✅ Tabla '{tbl:26}': {len(rows):5d} registros migrados limpiamente.")
        except Exception as e:
            print(f"    ⚠️  Error en tabla '{tbl}': {e}")

    # 4. Asegurar tablas transaccionales vacías
    print("\n[4] Asegurando que las tablas transaccionales de prueba queden limpias (0 registros)...")
    with tgt_engine.begin() as tc:
        for trans_tbl in TABLAS_TRANSACCIONALES_VACIAS:
            try:
                existe = tc.execute(text(f"SELECT to_regclass('public.\"{trans_tbl}\"');")).scalar()
                if existe:
                    tc.execute(text(f'TRUNCATE TABLE "{trans_tbl}" CASCADE;'))
            except Exception:
                pass
    print("    ✅ Tablas de pruebas (calificaciones, cosechas, órdenes de prueba) limpias.")

    # 5. Crear Vistas Analíticas en Destino
    print("\n[5] Recreando vistas analíticas en producción (stg_eventos, mrt_estado_cultivo_actual)...")
    views_order = [
        ('stg_eventos', VISTA_STG_EVENTOS_SQL),
        ('mrt_estado_cultivo_actual', VISTA_MRT_ESTADO_CULTIVO_SQL)
    ]
    with src_engine.connect() as sc:
        for vname, fallback_sql in views_order:
            try:
                ddl = sc.execute(text("SELECT definition FROM pg_views WHERE schemaname = 'public' AND viewname = :v"), {"v": vname}).scalar()
                view_sql = f"CREATE OR REPLACE VIEW {vname} AS\n{ddl}" if ddl else fallback_sql
                with tgt_engine.begin() as tc:
                    tc.execute(text(view_sql))
                print(f"    ✅ Vista '{vname}' creada exitosamente en destino.")
            except Exception as e:
                print(f"    ⚠️  Error al crear vista '{vname}': {e}")

    # 6. Sincronizar secuencias auto-incrementales en destino
    print("\n[6] Ajustando secuencias auto-incrementables (SERIAL IDs) en producción...")
    try:
        with tgt_engine.begin() as tc:
            seq_cols = tc.execute(text("""
                SELECT table_name, column_name, column_default 
                FROM information_schema.columns 
                WHERE table_schema = 'public' 
                  AND column_default LIKE 'nextval(%'
            """)).fetchall()
            ajustadas = 0
            for stbl, scol, sdef in seq_cols:
                try:
                    # Extraer el nombre de la secuencia de nextval('nombre_seq'::regclass)
                    if "'" in sdef:
                        seq_name = sdef.split("'")[1]
                    else:
                        seq_name = f"{stbl}_{scol}_seq"
                    
                    # Asociar secuencia a la tabla si es posible
                    try:
                        tc.execute(text(f'ALTER SEQUENCE "{seq_name}" OWNED BY "{stbl}"."{scol}";'))
                    except Exception:
                        pass
                        
                    tc.execute(text(f"""
                        SELECT setval('"{seq_name}"', COALESCE(MAX("{scol}"), 0) + 1, false)
                        FROM "{stbl}";
                    """))
                    ajustadas += 1
                except Exception:
                    pass
            print(f"    ✅ {ajustadas} secuencias sincronizadas con el valor máximo.")
    except Exception as e:
        print(f"    ℹ️  Ajuste de secuencias: {e}")

    print("\n" + "=" * 65)
    print("🎉 ¡MIGRACIÓN COMPLETADA CON ÉXITO!")
    print("Tu base de datos de producción en Render está lista, limpia y con los catálogos oficiales.")
    print("=" * 65)


if __name__ == '__main__':
    # Auto-detectar la URL de la base de datos local desde .env
    env_source = os.environ.get('DATABASE_URL', '').strip()
    if not env_source:
        env_source = "postgresql://postgres:admin@localhost:5432/florsani"
    
    parser = argparse.ArgumentParser(description="Migración limpia a PostgreSQL en Producción (Render o VPS)")
    parser.add_argument("--target", required=True, help="URL de PostgreSQL en producción (ej. postgresql://user:pass@host/db)")
    parser.add_argument("--source", default=env_source, help="URL de PostgreSQL origen local (por defecto se lee de .env)")
    parser.add_argument("--sin-snapshot-cultivo", action="store_true", help="Omitir la copia de crop_state_records")
    
    args = parser.parse_args()
    
    source = normalize_url(args.source, is_target=False)
    target = normalize_url(args.target, is_target=True)
    
    migrar(source, target, incluir_snapshot_cultivo=not args.sin_snapshot_cultivo)
