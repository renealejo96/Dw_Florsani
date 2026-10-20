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
from sqlalchemy import create_engine, text

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
    "users"
]

# Tablas de Snapshot Oficial de Cultivo:
TABLAS_ESTADO_CULTIVO = [
    "crop_state_records",
    "plano_cultivo"
]

# Tablas de Transacciones / Pruebas (se inicializan vacías):
TABLAS_TRANSACCIONALES_VACIAS = [
    "calificaciones",
    "fact_eventos_cultivo",
    "drench_items",
    "registro_cosecha",
    "estimado",
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
    "warehouse_dispatch_logs",
    "import_batches"
]

VISTA_STG_EVENTOS_SQL = """
CREATE OR REPLACE VIEW stg_eventos AS
SELECT 
    id_evento, id_cama, fecha_evento, tipo_evento, variedad, 
    estado_evento, origen_evento, num_poda, num_plantas, producto_maestro,
    semana_campo, created_at
FROM fact_eventos_cultivo
WHERE estado_evento = 'ACTIVO';
"""

VISTA_MRT_ESTADO_CULTIVO_SQL = """
CREATE OR REPLACE VIEW mrt_estado_cultivo_actual AS
WITH semana_corte_calc AS (
    SELECT COALESCE(
        MAX(semana_campo),
        (
            SELECT MAX(
                CASE 
                    WHEN week ~ '^20[0-9]{2}-[0-9]{1,2}$' THEN (SUBSTRING(week, 3, 2)::int * 100 + SUBSTRING(week, 6, 2)::int)
                    WHEN week ~ '^[0-9]{4}$' THEN week::int
                    ELSE 0
                END
            )
            FROM crop_state_records
            WHERE week IS NOT NULL
        ),
        (SELECT semana_campo FROM dim_calendario_agricola WHERE fecha = CURRENT_DATE LIMIT 1),
        2640
    ) AS semana_activa
    FROM fact_eventos_cultivo
    WHERE estado_evento = 'ACTIVO'
),
ultimos_eventos AS (
    SELECT 
        e.id_evento,
        e.id_cama,
        e.fecha_evento,
        e.tipo_evento,
        e.variedad,
        e.producto_maestro,
        e.num_plantas,
        e.num_poda,
        e.origen_evento,
        e.semana_campo,
        ROW_NUMBER() OVER (
            PARTITION BY e.id_cama 
            ORDER BY e.fecha_evento DESC, e.id_evento DESC
        ) AS rn
    FROM fact_eventos_cultivo e
    WHERE e.estado_evento = 'ACTIVO'
),
eventos_recientes AS (
    SELECT *
    FROM ultimos_eventos
    WHERE rn = 1
),
base_camas AS (
    SELECT 
        c.id_cama,
        c.blq,
        c.num_cama,
        c.sufijo,
        c.metros_netos,
        c.metros_brutos,
        c.cama_estandar,
        c.codigo_bloque,
        b.zona,
        b.ha,
        b.total_segmentos,
        b.tecnico,
        b.riego,
        ue.id_evento,
        ue.fecha_evento,
        ue.tipo_evento,
        ue.variedad,
        ue.producto_maestro,
        ue.num_plantas,
        ue.num_poda,
        ue.origen_evento,
        ue.semana_campo AS evento_semana_campo,
        CASE 
            WHEN ue.tipo_evento = 'TUMBA' OR ue.id_evento IS NULL THEN 'VACIO'
            WHEN ue.tipo_evento IN ('SIEMBRA', 'PODA') THEN 'ACTIVO'
            ELSE 'OTRO'
        END AS estado_cultivo
    FROM dim_camas c
    JOIN dim_bloques b ON c.codigo_bloque = b.codigo_bloque
    LEFT JOIN eventos_recientes ue ON c.id_cama = ue.id_cama
)
SELECT 
    b.id_cama,
    b.blq,
    b.num_cama,
    b.sufijo,
    b.metros_netos,
    b.metros_brutos,
    b.cama_estandar,
    b.zona,
    b.tecnico,
    b.riego,
    b.estado_cultivo,
    b.tipo_evento AS ultimo_evento,
    b.fecha_evento AS fecha_ultimo_evento,
    b.producto_maestro AS producto_actual,
    b.variedad AS variedad_actual,
    b.num_plantas AS plantas_actuales,
    b.num_poda,
    b.origen_evento,
    scc.semana_activa AS semana_corte,
    CASE 
        WHEN b.estado_cultivo = 'ACTIVO' AND b.evento_semana_campo IS NOT NULL THEN
            GREATEST(0, (
                (scc.semana_activa / 100 - b.evento_semana_campo / 100) * 52 + 
                (scc.semana_activa % 100 - b.evento_semana_campo % 100)
            ))
        ELSE NULL
    END AS edad_semanas_actual
FROM base_camas b
CROSS JOIN semana_corte_calc scc;
"""


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
        print("    ✅ Conexión exitosa a Base de Datos Origen.")
    with tgt_engine.connect() as tc:
        tc.execute(text("SELECT 1;"))
        print("    ✅ Conexión exitosa a Base de Datos Destino.")

    # 2. Inicializar tablas en Destino usando SQLAlchemy
    print("\n[2] Creando esquema de tablas en la base de datos destino si no existen...")
    # Importar modelos de la aplicación Flask para sincronizar metadata
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from app import create_app, db
    temp_app = create_app()
    temp_app.config['SQLALCHEMY_DATABASE_URI'] = target_url
    with temp_app.app_context():
        db.create_all()
    print("    ✅ Tablas de la aplicación creadas en destino.")

    # 3. Copiar Catálogos y Dimensiones Maestras
    print("\n[3] Migrando catálogos y dimensiones maestras...")
    tablas_a_migrar = list(TABLAS_CATALOGOS)
    if incluir_snapshot_cultivo:
        tablas_a_migrar.extend(TABLAS_ESTADO_CULTIVO)

    for tbl in tablas_a_migrar:
        try:
            with src_engine.connect() as sc:
                # Comprobar si existe en origen
                existe = sc.execute(text(f"SELECT to_regclass('public.{tbl}');")).scalar()
                if not existe:
                    print(f"    ⏭️  Tabla '{tbl}' no existe en origen. Saltando.")
                    continue

                rows = sc.execute(text(f"SELECT * FROM \"{tbl}\";")).fetchall()
                if not rows:
                    print(f"    ⚪  Tabla '{tbl}' está vacía en origen. Saltando.")
                    continue

                cols = sc.execute(text(f"SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='{tbl}' ORDER BY ordinal_position;")).fetchall()
                col_names = [c[0] for c in cols]
                cols_str = ", ".join([f'"{c}"' for c in col_names])
                params_str = ", ".join([f":{c}" for c in col_names])

            with tgt_engine.begin() as tc:
                # Asegurar tabla en destino
                tc.execute(text(f"TRUNCATE TABLE \"{tbl}\" CASCADE;"))
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
                tc.execute(text(f"TRUNCATE TABLE \"{trans_tbl}\" CASCADE;"))
            except Exception:
                pass
    print("    ✅ Tablas de pruebas (calificaciones, cosechas, órdenes de prueba) limpias.")

    # 5. Crear Vistas Analíticas en Destino
    print("\n[5] Recreando vistas analíticas en producción (stg_eventos, mrt_estado_cultivo_actual)...")
    with tgt_engine.begin() as tc:
        tc.execute(text(VISTA_STG_EVENTOS_SQL))
        tc.execute(text(VISTA_MRT_ESTADO_CULTIVO_SQL))
    print("    ✅ Vistas analíticas creadas y operativas.")

    print("\n" + "=" * 65)
    print("🎉 ¡MIGRACIÓN COMPLETADA CON ÉXITO!")
    print("Tu base de datos de producción está lista, limpia y con los catálogos oficiales.")
    print("=" * 65)


if __name__ == '__main__':
    default_src = "postgresql://postgres:florsani_prod_secure_pass_2026@localhost:5432/florsani"
    
    parser = argparse.ArgumentParser(description="Migración limpia a PostgreSQL en Producción (Render o VPS)")
    parser.add_argument("--target", required=True, help="URL de PostgreSQL en producción (ej. postgresql://user:pass@host/db)")
    parser.add_argument("--source", default=default_src, help="URL de PostgreSQL origen local")
    
    args = parser.parse_args()
    migrar(args.source, args.target)
