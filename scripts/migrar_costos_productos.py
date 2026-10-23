"""
PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
ARCHIVO: scripts/migrar_costos_productos.py
DESCRIPCIÓN: Ingesta y homologación de precios, unidades, factores de conversión ('tras'),
             artículo de requisición (Venture) y bodega para el catálogo de productos agroquímicos.
"""
import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np

# Agregar directorio raíz al PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text
from app.shared.models import Product

app = create_app()

EXCEL_FILE = "costos productos.xlsx"

with app.app_context():
    print("[MIGRACION-COSTOS] 1. Agregando columnas de costos y bodega a 'products' si no existen...")
    alter_sql = """
        ALTER TABLE products ADD COLUMN IF NOT EXISTS precio_compra double precision;
        ALTER TABLE products ADD COLUMN IF NOT EXISTS unidad_compra character varying(30);
        ALTER TABLE products ADD COLUMN IF NOT EXISTS unidad_consumo character varying(30);
        ALTER TABLE products ADD COLUMN IF NOT EXISTS factor_conversion double precision DEFAULT 1.0;
        ALTER TABLE products ADD COLUMN IF NOT EXISTS costo_unitario double precision;
        ALTER TABLE products ADD COLUMN IF NOT EXISTS bodega character varying(100);
        ALTER TABLE products ADD COLUMN IF NOT EXISTS articulo_requisicion character varying(255);
        ALTER TABLE products ADD COLUMN IF NOT EXISTS codigo_venture character varying(100);
    """
    db.session.execute(text(alter_sql))
    db.session.commit()
    print("[MIGRACION-COSTOS] [OK] Estructura de tabla 'products' actualizada.")

    excel_path = os.path.join(str(Path(__file__).resolve().parent.parent), EXCEL_FILE)
    if not os.path.exists(excel_path):
        print(f"[MIGRACION-COSTOS] [ERROR] No se encontró el archivo: {excel_path}")
        sys.exit(1)

    print(f"[MIGRACION-COSTOS] 2. Leyendo {EXCEL_FILE} (hoja 'bd')...")
    df = pd.read_excel(excel_path, sheet_name="bd")
    df.columns = [str(c).strip() for c in df.columns]

    print(f"  • Filas en Excel: {len(df)}")

    # Cargar productos existentes
    existing_prods = Product.query.all()
    prod_map_code = {p.code.strip().upper(): p for p in existing_prods if p.code}
    prod_map_comm = {p.commercial_name.strip().upper(): p for p in existing_prods if p.commercial_name}
    prod_map_req = {p.articulo_requisicion.strip().upper(): p for p in existing_prods if p.articulo_requisicion}

    updated_count = 0
    created_count = 0

    for idx, row in df.iterrows():
        p_name = str(row.get('PRODUCTO', '')).strip()
        if not p_name or p_name.upper() == 'NAN':
            continue

        p_name_u = p_name.upper()
        p_req = str(row.get('ARTICULO REQUISICION', '')).strip() if pd.notnull(row.get('ARTICULO REQUISICION')) else ''
        p_req_u = p_req.upper() if p_req else ''
        
        precio_val = float(row.get('precio')) if pd.notnull(row.get('precio')) else None
        u_compra = str(row.get('U. Compra', '')).strip() if pd.notnull(row.get('U. Compra')) else None
        u_consumo = str(row.get('U. Consumo', '')).strip() if pd.notnull(row.get('U. Consumo')) else None
        bodega_val = str(row.get('Bodega', '')).strip().upper() if pd.notnull(row.get('Bodega')) else 'FUMIGANTES Y PESTICIDAS'
        
        tras_val = float(row.get('tras')) if pd.notnull(row.get('tras')) and float(row.get('tras')) > 0 else 1.0
        codigo2_val = str(row.get('Código2', '')).strip() if pd.notnull(row.get('Código2')) else None
        if codigo2_val and (codigo2_val.upper() == 'NAN' or codigo2_val == 'nan'):
            codigo2_val = None

        # Costo por unidad de consumo = precio / tras
        costo_unit = round(precio_val / tras_val, 5) if (precio_val is not None and tras_val > 0) else None

        # Intentar match con producto existente
        target = prod_map_code.get(p_name_u) or prod_map_comm.get(p_name_u)
        if not target and p_req_u:
            target = prod_map_code.get(p_req_u) or prod_map_comm.get(p_req_u) or prod_map_req.get(p_req_u)

        if target:
            # Actualizar producto existente
            target.precio_compra = precio_val
            target.unidad_compra = u_compra
            target.unidad_consumo = u_consumo or target.unit or 'CC'
            target.factor_conversion = tras_val
            target.costo_unitario = costo_unit
            target.bodega = bodega_val
            target.articulo_requisicion = p_req or target.articulo_requisicion
            if codigo2_val:
                target.codigo_venture = codigo2_val
            updated_count += 1
        else:
            # Crear nuevo producto para el ERP
            new_code = p_name
            # Asegurar código único
            base_code = new_code
            counter = 1
            while new_code.upper() in prod_map_code:
                new_code = f"{base_code}_{counter}"
                counter += 1

            new_prod = Product(
                code=new_code,
                commercial_name=p_name,
                unit=u_consumo or 'CC',
                precio_compra=precio_val,
                unidad_compra=u_compra,
                unidad_consumo=u_consumo or 'CC',
                factor_conversion=tras_val,
                costo_unitario=costo_unit,
                bodega=bodega_val,
                articulo_requisicion=p_req,
                codigo_venture=codigo2_val,
                is_active=True
            )
            db.session.add(new_prod)
            prod_map_code[new_code.upper()] = new_prod
            created_count += 1

    db.session.commit()
    print(f"[MIGRACION-COSTOS] [OK] Sincronización completada exitosamente:")
    print(f"   • Productos existentes actualizados con costos/bodega: {updated_count}")
    print(f"   • Nuevos insumos integrados al catálogo: {created_count}")
    print(f"   • Total productos activos con costeo: {Product.query.count()}")
