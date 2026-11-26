"""
Servicio centralizado de reconciliación de Censo / Estado de Cultivo con dim_camas y fact_eventos_cultivo.
Garantiza que cualquier tumba, erradicación o cambio de cultivo registrado en crop_state_records
se propague inmediatamente a dim_camas y a la vista viva mrt_estado_cultivo_actual.
"""
from app.extensions import db
from sqlalchemy import text
import re
import time

_MRT_CACHE_ROWS = None
_MRT_CACHE_TIME = 0.0

def invalidate_mrt_cache():
    """Invalida la caché en memoria de mrt_estado_cultivo_actual tras cambios de campo o censo."""
    global _MRT_CACHE_ROWS, _MRT_CACHE_TIME
    _MRT_CACHE_ROWS = None
    _MRT_CACHE_TIME = 0.0
    try:
        from app.modules.fumigacion.services.calculation_engine import CalculationEngine
        CalculationEngine.invalidate_projected_cache()
    except Exception:
        pass

def get_mrt_rows_cached(ttl_seconds=25):
    """
    Retorna las filas de mrt_estado_cultivo_actual con caché en memoria ultrarrápida (25s TTL),
    evitando escaneos repetitivos en ráfagas de navegación entre módulos.
    """
    global _MRT_CACHE_ROWS, _MRT_CACHE_TIME
    now = time.time()
    if _MRT_CACHE_ROWS is not None and (now - _MRT_CACHE_TIME) < ttl_seconds:
        return _MRT_CACHE_ROWS

    with db.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT 
                COALESCE(blq, codigo_bloque) AS blq,
                COALESCE(sufijo, 'A') AS sufijo,
                num_cama,
                producto_actual,
                variedad_actual,
                ultimo_evento,
                semana_campo_ultimo_evento,
                fecha_ultimo_evento,
                cama_estandar,
                zona,
                ha,
                id_cama,
                codigo_bloque
            FROM mrt_estado_cultivo_actual
            WHERE num_cama IS NOT NULL;
        """)).fetchall()
    _MRT_CACHE_ROWS = rows
    _MRT_CACHE_TIME = now
    return rows

def reconciliar_censo_vigente(startup_only_if_needed=False):
    """
    1. Obtiene la semana más reciente cargada en crop_state_records.
    2. Identifica bloques y camas activos vs vacíos/tumbados.
    3. Si un bloque activo en dim_camas ya no tiene camas activas en el último censo,
       marca todas sus camas en dim_camas con area_producto_comercial = 'VACIO',
       variedad_comercial = 'VACIO' y registra el evento TUMBA en fact_eventos_cultivo.
    4. Sincroniza las camas explícitamente marcadas como VACIO/ERRADICADO.
    """
    try:
        with db.engine.begin() as conn:
            # 1. Obtener la semana más reciente de censo disponible (ordenamiento robusto)
            latest_week = conn.execute(text("""
                SELECT week
                FROM crop_state_records 
                WHERE week IS NOT NULL AND week != ''
                ORDER BY 
                    CASE 
                        WHEN week ~ '^20[0-9]{2}-[0-9]{1,2}$' THEN (SUBSTRING(week, 1, 4)::int * 100 + SUBSTRING(week, 6, 2)::int)
                        WHEN week ~ '^[0-9]{4}$' THEN (2000 + SUBSTRING(week, 1, 2)::int) * 100 + SUBSTRING(week, 3, 2)::int
                        ELSE 202640
                    END DESC
                LIMIT 1;
            """)).scalar()

            if not latest_week:
                return {"success": False, "message": "No hay semanas en crop_state_records"}

            mig_key = f"censo_reconciled_v1_{latest_week}"
            if startup_only_if_needed:
                already_done = conn.execute(text(
                    "SELECT 1 FROM _system_one_time_migrations WHERE migration_key = :mk"
                ), {'mk': mig_key}).scalar()
                if already_done:
                    return {"success": True, "latest_week": latest_week, "skipped": True, "bloques_tumbados": []}

            # 2. Cargar registros del censo más reciente
            csr_rows = conn.execute(text("""
                SELECT 
                    id,
                    COALESCE(NULLIF(TRIM(block_num), ''), TRIM(block_full)) AS blq,
                    COALESCE(NULLIF(TRIM(suffix), ''), 'A') AS suf,
                    bed_num,
                    COALESCE(TRIM(crop_master), '') AS crop_master,
                    COALESCE(TRIM(variety), '') AS variety,
                    COALESCE(TRIM(status_raw), '') AS status_raw
                FROM crop_state_records
                WHERE week = :w;
            """), {'w': latest_week}).fetchall()

            if not csr_rows:
                return {"success": False, "message": f"Sin registros para semana {latest_week}"}

            total_filas = len(csr_rows)
            vacio_keywords = ('VACIO', 'DESCARTE', 'TUMBAR', 'ERRADICADO', 'NAN', 'TOTAL', 'TOTAL GENERAL', '', 'SIN CULTIVO', 'NONE')
            
            active_blocks = set()
            camas_explicitas_vacias = []

            for r in csr_rows:
                cm = str(r[4] or '').strip().upper()
                st = str(r[6] or '').strip().upper()
                is_v = (cm in vacio_keywords) or (st in ('VACIO', 'ERRADICADO', 'TUMBA', 'TUMBAR', 'DESCARTE'))
                
                raw_b = str(r[1] or '').strip().upper()
                clean_b = raw_b.replace('BL', '').replace('BLQ', '').strip()
                suf = str(r[2] or 'A').strip().upper()
                cod_bloque = f"{clean_b}{suf}" if suf and suf != 'A' else clean_b
                
                n_cama = int(r[3]) if r[3] is not None and str(r[3]).isdigit() else None

                if is_v:
                    camas_explicitas_vacias.append((cod_bloque, clean_b, suf, n_cama))
                else:
                    active_blocks.add(clean_b)
                    active_blocks.add(cod_bloque)

            # Auto-registrar cualquier cultivo activo nuevo del censo en el catálogo 'crops' (ej. BRILLANTINA)
            try:
                crops_en_censo = conn.execute(text("""
                    SELECT DISTINCT UPPER(TRIM(crop_master))
                    FROM crop_state_records
                    WHERE week = :w
                      AND crop_master IS NOT NULL
                      AND TRIM(crop_master) != ''
                      AND UPPER(TRIM(crop_master)) NOT IN ('VACIO', 'DESCARTE', 'TUMBAR', 'ERRADICADO', 'NAN', 'TOTAL', 'TOTAL GENERAL', 'SIN CULTIVO', 'NONE')
                """), {'w': latest_week}).fetchall()

                existing_crops = conn.execute(text("SELECT UPPER(TRIM(name)) FROM crops")).fetchall()
                existing_set = {row[0] for row in existing_crops if row[0]}

                for cr in crops_en_censo:
                    c_name_raw = cr[0]
                    if c_name_raw and c_name_raw not in existing_set:
                        c_title = c_name_raw.capitalize()
                        conn.execute(text("""
                            INSERT INTO crops (name, category, is_active, veg_min_age, veg_max_age, prod_min_age, prod_max_age, aliases_json, created_at, updated_at)
                            VALUES (:name, 'PRODUCTOS_NUEVOS', TRUE, 0, 12, 13, 99, '[]', NOW(), NOW())
                            ON CONFLICT (name) DO NOTHING;
                        """), {'name': c_title})
                        existing_set.add(c_name_raw)
            except Exception as e_cr:
                print(f"[CENSO_SYNC] Nota al sincronizar catálogo crops: {e_cr}")

            # 3. Detectar bloques en dim_camas que antes tenían cultivo pero en el censo vigente NO tienen camas activas (TUMBADOS COMPLETOS)
            # Aplica si el censo tiene representatividad (>1000 camas)
            bloques_tumbados_detectados = []
            if total_filas > 1000:
                dim_active = conn.execute(text("""
                    SELECT DISTINCT 
                        codigo_bloque,
                        COALESCE(NULLIF(TRIM(blq), ''), codigo_bloque) AS blq
                    FROM dim_camas
                    WHERE area_producto_comercial IS NOT NULL 
                      AND UPPER(TRIM(area_producto_comercial)) NOT IN ('VACIO', 'ERRADICADO', 'TOTAL', 'NAN', '', 'DESCARTE', 'SIN CULTIVO');
                """)).fetchall()

                for d_cod, d_blq in dim_active:
                    d_cod_c = str(d_cod or '').strip().upper()
                    d_blq_c = str(d_blq or '').strip().upper()
                    clean_d_blq = d_blq_c.replace('BL', '').replace('BLQ', '').strip()
                    # Si ni el código de bloque ni el número de bloque constan como activos en el censo vigente
                    if d_cod_c not in active_blocks and d_blq_c not in active_blocks and clean_d_blq not in active_blocks:
                        bloques_tumbados_detectados.append(d_cod_c)

            # 4. Actualizar dim_camas a VACIO para todos los bloques tumbados completos
            if bloques_tumbados_detectados:
                # Extraer año y semana del censo vigente
                nums = re.findall(r'\d+', str(latest_week))
                sc_num = 2647
                if len(nums) >= 2:
                    sc_num = int(nums[0]) % 100 * 100 + int(nums[1])
                elif len(nums) == 1:
                    v_n = int(nums[0])
                    sc_num = v_n if v_n > 2000 else 2600 + v_n

                bloques_list = list(set(bloques_tumbados_detectados))

                # Actualizar dim_camas de manera atómica
                conn.execute(text("""
                    UPDATE dim_camas
                    SET area_producto_comercial = 'VACIO',
                        variedad_comercial = 'VACIO'
                    WHERE codigo_bloque = ANY(:bloques) OR blq = ANY(:bloques);
                """), {'bloques': bloques_list})

                # Registrar evento TUMBA en fact_eventos_cultivo
                conn.execute(text("""
                    INSERT INTO fact_eventos_cultivo (
                        id_cama, codigo_bloque, fecha_evento, semana_campo,
                        tipo_evento, producto_maestro, variedad, origen_evento, estado_evento
                    )
                    SELECT 
                        c.id_cama, c.codigo_bloque, CURRENT_DATE, :sc,
                        'TUMBA', 'VACIO', 'ERRADICADO', :orig, 'ACTIVO'
                    FROM dim_camas c
                    WHERE (c.codigo_bloque = ANY(:bloques) OR c.blq = ANY(:bloques))
                      AND NOT EXISTS (
                          SELECT 1 FROM fact_eventos_cultivo f 
                          WHERE f.id_cama = c.id_cama 
                            AND f.tipo_evento = 'TUMBA' 
                            AND f.semana_campo = :sc
                      );
                """), {
                    'sc': sc_num,
                    'bloques': bloques_list,
                    'orig': f"Tumba detectada por censo semana {latest_week}"
                })

            # 5. También actualizar camas individuales explícitamente marcadas como VACIO
            if camas_explicitas_vacias:
                nums = re.findall(r'\d+', str(latest_week))
                sc_num = 2647
                if len(nums) >= 2:
                    sc_num = int(nums[0]) % 100 * 100 + int(nums[1])
                elif len(nums) == 1:
                    v_n = int(nums[0])
                    sc_num = v_n if v_n > 2000 else 2600 + v_n

                for cod_b, blq, suf, n_c in camas_explicitas_vacias:
                    if n_c is not None:
                        conn.execute(text("""
                            UPDATE dim_camas
                            SET area_producto_comercial = 'VACIO',
                                variedad_comercial = 'VACIO'
                            WHERE (codigo_bloque = :cb OR blq = :blq) AND num_cama = :nc;
                        """), {'cb': cod_b, 'blq': blq, 'nc': n_c})

                        conn.execute(text("""
                            INSERT INTO fact_eventos_cultivo (
                                id_cama, codigo_bloque, fecha_evento, semana_campo,
                                tipo_evento, producto_maestro, variedad, origen_evento, estado_evento
                            )
                            SELECT 
                                c.id_cama, c.codigo_bloque, CURRENT_DATE, :sc,
                                'TUMBA', 'VACIO', 'ERRADICADO', :orig, 'ACTIVO'
                            FROM dim_camas c
                            WHERE (c.codigo_bloque = :cb OR c.blq = :blq) AND c.num_cama = :nc
                              AND NOT EXISTS (
                                  SELECT 1 FROM fact_eventos_cultivo f 
                                  WHERE f.id_cama = c.id_cama 
                                    AND f.tipo_evento = 'TUMBA' 
                                    AND f.semana_campo = :sc
                              );
                        """), {
                            'cb': cod_b, 'blq': blq, 'nc': n_c,
                            'sc': sc_num,
                            'orig': f"Cama vacía detectada por censo semana {latest_week}"
                        })

            try:
                conn.execute(text("""
                    INSERT INTO _system_one_time_migrations (migration_key)
                    VALUES (:mk)
                    ON CONFLICT (migration_key) DO NOTHING;
                """), {'mk': mig_key})
            except Exception:
                pass

        invalidate_mrt_cache()
        print(f"[CENSO_SYNC] Reconciliación completada con semana {latest_week}: {len(bloques_tumbados_detectados)} bloques tumbados actualizados ({bloques_tumbados_detectados}).")
        return {
            "success": True,
            "latest_week": latest_week,
            "bloques_tumbados": bloques_tumbados_detectados
        }
    except Exception as e:
        print(f"[WARN] Error en reconciliar_censo_vigente: {e}")
        return {"success": False, "error": str(e)}
