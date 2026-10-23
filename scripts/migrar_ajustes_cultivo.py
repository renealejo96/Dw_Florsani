"""
PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
ARCHIVO: scripts/migrar_ajustes_cultivo.py
DESCRIPCIÓN: Sincronización universal de particiones, camas estándar, metrajes y conteo de plantas.
             Alinea todas las camas de dim_camas y fact_eventos_cultivo con el censo oficial
             más reciente de crop_state_records (ej. Bloque 75 C1: 120 plantas, Bloque 107, etc.).
"""
import sys
from pathlib import Path

# Agregar directorio raíz al PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text

app = create_app()

with app.app_context():
    print("[MIGRACION-CULTIVO] Iniciando sincronización universal de particiones y camas estándar...")
    try:
        # 1. Obtener última semana registrada en crop_state_records
        max_w = db.session.execute(text("""
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
        print(f"[MIGRACION-CULTIVO] Censo de referencia: Semana {max_w}")

        # 2. Consultar catálogo de densidades
        dens_rows = db.session.execute(text("SELECT producto_maestro, plantas_por_cama FROM dim_densidad_plantas;")).fetchall()
        dens_map = {str(r[0]).strip().upper(): int(r[1]) for r in dens_rows if r[0] and r[1]}

        # 3. Cargar todos los registros del último censo
        csr_rows = db.session.execute(text("""
            SELECT 
                block_num,
                block_full,
                bed_num,
                COALESCE(NULLIF(TRIM(suffix), ''), 'A') as sufijo,
                standard_bed,
                crop_master,
                variety,
                status_raw
            FROM crop_state_records
            WHERE week = :max_w
            ORDER BY block_num, bed_num, sufijo;
        """), {'max_w': max_w}).fetchall()

        # 4. Cargar camas actuales en dim_camas
        dim_rows = db.session.execute(text("""
            SELECT id_cama, codigo_bloque, blq, sufijo, num_cama, cama_estandar, num_plantas, metros_netos
            FROM dim_camas;
        """)).fetchall()

        dim_by_id = {r.id_cama: r for r in dim_rows}
        dim_by_b_s_c = {(str(r.blq).strip().upper(), str(r.sufijo).strip().upper(), int(r.num_cama)): r for r in dim_rows if r.num_cama is not None}
        dim_by_cb_s_c = {(str(r.codigo_bloque).strip().upper(), str(r.sufijo).strip().upper(), int(r.num_cama)): r for r in dim_rows if r.num_cama is not None}

        updated_camas = 0
        updated_eventos = 0

        for c in csr_rows:
            b_num = str(c.block_num).strip().upper()
            b_full = str(c.block_full).replace('BL', '').replace(' ', '').strip().upper()
            suf = str(c.sufijo).strip().upper()
            bed = int(c.bed_num)
            std = float(c.standard_bed or 0.0)

            # Encontrar cama en dim_camas
            target_dim = None
            if (b_num, suf, bed) in dim_by_b_s_c:
                target_dim = dim_by_b_s_c[(b_num, suf, bed)]
            elif (b_full, suf, bed) in dim_by_cb_s_c:
                target_dim = dim_by_cb_s_c[(b_full, suf, bed)]
            elif (b_full, suf, bed) in dim_by_b_s_c:
                target_dim = dim_by_b_s_c[(b_full, suf, bed)]
            else:
                cand_id = f"{b_num}{suf}-{suf}-C{bed}"
                if cand_id in dim_by_id:
                    target_dim = dim_by_id[cand_id]

            if not target_dim:
                continue

            dim_std = float(target_dim.cama_estandar or 0.0)
            dim_plantas = target_dim.num_plantas or 0

            # Calcular densidad
            prod_nom = str(c.crop_master or '').strip().upper()
            d_val = dens_map.get(prod_nom)
            if not d_val:
                for dk, dv in dens_map.items():
                    if dk in prod_nom or prod_nom in dk:
                        d_val = dv
                        break
            if not d_val:
                d_val = 1350 if 'HYPER' in prod_nom else (400 if 'GYP' in prod_nom else 600)

            esta_vacia = (c.crop_master == 'VACIO' or 'ERRADICADO' in str(c.status_raw or '').upper() or 'TUMBA' in str(c.status_raw or '').upper())
            calc_plants = 0 if esta_vacia else int(round(std * d_val))
            calc_metros = round(std * 33.0, 2)

            # Si hay diferencia en cama estándar (>0.01) o plantas desalineadas
            if abs(dim_std - std) > 0.01 or (not esta_vacia and dim_plantas != calc_plants and abs(dim_plantas - calc_plants) > 5):
                db.session.execute(text("""
                    UPDATE dim_camas
                    SET cama_estandar = :std,
                        num_plantas = :plantas,
                        metros_netos = :metros
                    WHERE id_cama = :id_cama;
                """), {
                    'std': round(std, 4),
                    'plantas': calc_plants,
                    'metros': calc_metros,
                    'id_cama': target_dim.id_cama
                })
                updated_camas += 1

                # Actualizar también evento activo en fact_eventos_cultivo si aplica
                ev_res = db.session.execute(text("""
                    UPDATE fact_eventos_cultivo
                    SET num_plantas = :plantas
                    WHERE id_cama = :id_cama AND estado_evento = 'ACTIVO';
                """), {
                    'plantas': calc_plants,
                    'id_cama': target_dim.id_cama
                })
                if ev_res.rowcount > 0:
                    updated_eventos += ev_res.rowcount

        db.session.commit()
        print(f"[MIGRACION-CULTIVO] [OK] Sincronización exitosa: {updated_camas} camas y {updated_eventos} eventos actualizados con precisión del censo.")

        # Verificación explícita de Bloque 75 Cama 1
        b75_res = db.session.execute(text("""
            SELECT id_cama, cama_estandar, num_plantas, metros_netos 
            FROM dim_camas 
            WHERE id_cama IN ('75A-A-C1', '75B-B-C1') 
            ORDER BY id_cama;
        """)).fetchall()
        print(f"[MIGRACION-CULTIVO] Verificación Bloque 75: {b75_res}")

    except Exception as e:
        db.session.rollback()
        print(f"[MIGRACION-CULTIVO] Error en sincronización: {e}")
