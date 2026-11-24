"""
Servicio centralizado de reconciliación de Censo / Estado de Cultivo con dim_camas y fact_eventos_cultivo.
Garantiza que cualquier tumba, erradicación o cambio de cultivo registrado en crop_state_records
se propague inmediatamente a dim_camas y a la vista viva mrt_estado_cultivo_actual.
"""
from app.extensions import db
from sqlalchemy import text
import re

def reconciliar_censo_vigente():
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

            print(f"[CENSO_SYNC] Reconciliación completada con semana {latest_week}: {len(bloques_tumbados_detectados)} bloques tumbados actualizados ({bloques_tumbados_detectados}).")
            return {
                "success": True,
                "latest_week": latest_week,
                "bloques_tumbados": bloques_tumbados_detectados
            }
    except Exception as e:
        print(f"[WARN] Error en reconciliar_censo_vigente: {e}")
        return {"success": False, "error": str(e)}
