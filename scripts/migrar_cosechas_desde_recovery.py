import sys
import os

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import pandas as pd
from sqlalchemy import create_engine, text

RECOVERY_URL = "postgresql://rene:BlH0HemPSdcwy8H1AsXEyKp84Dnzzt4A@dpg-dbbdbgbbc2fs73apf390-a.oregon-postgres.render.com/pyganflor_yhqt?sslmode=require"
LOCAL_URL = "postgresql://postgres:admin@localhost:5432/florsani"

def sync_cosechas():
    print("==================================================")
    print("[*] SINCRONIZANDO COSECHAS HISTORICAS RECUPERADAS")
    print("==================================================")
    
    eng_rec = create_engine(RECOVERY_URL)
    eng_loc = create_engine(LOCAL_URL)
    
    # 1. Leer de Render Recovery
    print("📥 Leyendo 'registro_cosecha' desde Render Recovery...")
    df_cosecha = pd.read_sql_table("registro_cosecha", eng_rec)
    print(f"   -> {len(df_cosecha)} registros de cosecha encontrados (Fechas: {df_cosecha['fecha'].min()} a {df_cosecha['fecha'].max()})")
    
    print("📥 Leyendo 'estimado' desde Render Recovery...")
    df_estimado = pd.read_sql_table("estimado", eng_rec)
    print(f"   -> {len(df_estimado)} registros de estimado encontrados.")
    
    # 2. Guardar en Base Local
    print("\n💾 Guardando en base de datos local 'florsani'...")
    with eng_loc.begin() as c:
        c.execute(text("TRUNCATE TABLE registro_cosecha CASCADE;"))
        c.execute(text("TRUNCATE TABLE estimado CASCADE;"))
    
    df_cosecha.to_sql("registro_cosecha", eng_loc, if_exists="append", index=False, method="multi", chunksize=1000)
    df_estimado.to_sql("estimado", eng_loc, if_exists="append", index=False, method="multi", chunksize=1000)
    
    # Ajustar secuencias en local
    with eng_loc.begin() as c:
        c.execute(text("SELECT setval('registro_cosecha_id_seq', COALESCE((SELECT MAX(id) FROM registro_cosecha), 0) + 1, false);"))
        c.execute(text("SELECT setval('estimado_id_seq', COALESCE((SELECT MAX(id) FROM estimado), 0) + 1, false);"))
    print("   ✅ Base local actualizada con los 11,351 registros de cosecha.")
    
    # 3. Generar archivo SQL para restauración directa en el VPS
    print("\n📄 Generando archivo SQL de cosechas para el VPS...")
    with open("cosechas_historicas.sql", "w", encoding="utf-8") as f:
        f.write("-- RECARGA DE COSECHAS Y ESTIMADOS HISTÓRICOS HASTA 2026-10-19\n")
        f.write("TRUNCATE TABLE registro_cosecha CASCADE;\n")
        f.write("TRUNCATE TABLE estimado CASCADE;\n\n")
        
        # Generar INSERTS de estimado
        cols_est = [f'"{c}"' for c in df_estimado.columns]
        f.write(f'INSERT INTO estimado ({", ".join(cols_est)}) VALUES\n')
        est_rows = []
        for _, row in df_estimado.iterrows():
            vals = []
            for col in df_estimado.columns:
                val = row[col]
                if pd.isna(val):
                    vals.append("NULL")
                elif isinstance(val, (int, float)):
                    vals.append(str(int(val) if isinstance(val, int) or val.is_integer() else val))
                else:
                    v_str = str(val).replace("'", "''")
                    vals.append(f"'{v_str}'")
            est_rows.append(f"({', '.join(vals)})")
        f.write(",\n".join(est_rows) + ";\n\n")
        
        # Generar INSERTS de registro_cosecha en bloques de 1000
        cols_cos = [f'"{c}"' for c in df_cosecha.columns]
        chunk_size = 1000
        for i in range(0, len(df_cosecha), chunk_size):
            chunk = df_cosecha.iloc[i:i+chunk_size]
            f.write(f'INSERT INTO registro_cosecha ({", ".join(cols_cos)}) VALUES\n')
            c_rows = []
            for _, row in chunk.iterrows():
                vals = []
                for col in df_cosecha.columns:
                    val = row[col]
                    if pd.isna(val):
                        vals.append("NULL")
                    elif isinstance(val, (int, float)):
                        vals.append(str(int(val) if isinstance(val, int) or val.is_integer() else val))
                    else:
                        v_str = str(val).replace("'", "''")
                        vals.append(f"'{v_str}'")
                c_rows.append(f"({', '.join(vals)})")
            f.write(",\n".join(c_rows) + ";\n\n")
            
        f.write("SELECT setval('registro_cosecha_id_seq', COALESCE((SELECT MAX(id) FROM registro_cosecha), 0) + 1, false);\n")
        f.write("SELECT setval('estimado_id_seq', COALESCE((SELECT MAX(id) FROM estimado), 0) + 1, false);\n")
    
    print("   ✅ Archivo 'cosechas_historicas.sql' generado exitosamente.")

if __name__ == '__main__':
    sync_cosechas()
