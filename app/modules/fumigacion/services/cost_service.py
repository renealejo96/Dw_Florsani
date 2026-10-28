"""
PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
ARCHIVO: app/modules/fumigacion/services/cost_service.py
DESCRIPCIÓN: Motor de costeo agronómico para aplicaciones foliares, trichos, desinfecciones y drench.
             Calcula costos totales en $, costo por hectárea ($/ha), desglose por bodega
             de almacén y distribución granular bloque a bloque a partir de órdenes impresas/ejecutadas.
"""
from typing import Dict, List, Any, Optional
from app.extensions import db
from sqlalchemy import text
from app.shared.models import Product, FumigationOrder, FumigationOrderDetail, Rotation, RotationRound

class CostService:
    @classmethod
    def get_product_cost_map(cls) -> Dict[str, Dict[str, Any]]:
        """
        Retorna un diccionario indexado por código, nombre comercial y artículo de requisición
        con los costos unitarios de consumo, unidad y bodega de cada insumo.
        """
        all_prods = Product.query.filter_by(is_active=True).all()
        cost_map = {}
        for p in all_prods:
            unit_cost = p.calculated_unit_cost or 0.0
            bod = (p.bodega or 'SIN BODEGA').strip().upper()
            cod_alm = getattr(p, 'codigo_almacen', None) or ''
            info = {
                'id': p.id,
                'code': p.code,
                'commercial_name': p.commercial_name or p.code,
                'articulo_requisicion': p.articulo_requisicion or '',
                'codigo_almacen': cod_alm,
                'precio_compra': p.precio_compra,
                'unidad_compra': p.unidad_compra or '',
                'unidad_consumo': p.unidad_consumo or p.unit or 'CC',
                'factor_conversion': float(p.factor_conversion or 1.0),
                'costo_unitario': float(unit_cost),
                'bodega': bod
            }
            if p.code:
                cost_map[p.code.strip().upper()] = info
            if p.commercial_name:
                cost_map[p.commercial_name.strip().upper()] = info
            if p.articulo_requisicion:
                cost_map[p.articulo_requisicion.strip().upper()] = info
        return cost_map

    @classmethod
    def get_block_area_map(cls) -> Dict[str, Dict[str, Any]]:
        """
        Retorna el área total en hectáreas (ha), metros netos y camas estándar por bloque base.
        """
        query = text("""
            SELECT 
                TRIM(UPPER(blq)) as blq_clean,
                COALESCE(SUM(ha), 0.0) as total_ha,
                COALESCE(SUM(cama_estandar), 0.0) as total_std,
                COALESCE(SUM(metros_netos), 0.0) as total_netos,
                MAX(zona) as zona
            FROM dim_camas
            GROUP BY TRIM(UPPER(blq));
        """)
        rows = db.session.execute(query).fetchall()
        area_map = {}
        for r in rows:
            ha_val = float(r.total_ha or 0.0)
            netos_val = float(r.total_netos or 0.0)
            std_val = float(r.total_std or 0.0)
            # Si ha es 0 pero hay metros netos, aproximar 1 ha = 10,000 m2
            if ha_val <= 0.0001 and netos_val > 0:
                ha_val = round((netos_val * 1.2) / 10000.0, 4)
            elif ha_val <= 0.0001 and std_val > 0:
                ha_val = round((std_val * 33.0 * 1.2) / 10000.0, 4)
            
            area_map[r.blq_clean] = {
                'ha': round(ha_val, 4),
                'std_beds': round(std_val, 2),
                'metros_netos': round(netos_val, 2),
                'zona': r.zona or 'SIN ZONA'
            }
        return area_map

    @classmethod
    def calculate_order_costs(cls, order: FumigationOrder, prod_map: Optional[Dict] = None, area_map: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Calcula la liquidación económica completa de una orden de fumigación.
        """
        if prod_map is None:
            prod_map = cls.get_product_cost_map()
        if area_map is None:
            area_map = cls.get_block_area_map()

        total_cost = 0.0
        by_bodega = {}
        by_block = {}
        by_product = {}

        blocks_seen_ha = set()
        total_ha_order = 0.0

        for d in order.details:
            p_code = (d.product_code or '').strip().upper()
            c_nom = (d.commercial_name or '').strip().upper()
            cost_info = prod_map.get(p_code) or prod_map.get(c_nom) or {}
            unit_cost = float(cost_info.get('costo_unitario') or 0.0)
            bodega_name = cost_info.get('bodega') or 'SIN BODEGA'
            
            p_amt = float(d.product_amount or 0.0)
            line_cost = round(p_amt * unit_cost, 4)
            total_cost += line_cost

            # 1. Desglose por Bodega
            if bodega_name not in by_bodega:
                by_bodega[bodega_name] = {'total_cost': 0.0, 'items_count': 0}
            by_bodega[bodega_name]['total_cost'] += line_cost
            by_bodega[bodega_name]['items_count'] += 1

            # 2. Desglose por Bloque
            blk_clean = str(d.block_name).replace('BL', '').strip().upper()
            if blk_clean not in by_block:
                b_info = area_map.get(blk_clean, {'ha': 0.0, 'std_beds': 0.0, 'zona': d.zone or ''})
                b_ha = b_info['ha']
                if blk_clean not in blocks_seen_ha:
                    total_ha_order += b_ha
                    blocks_seen_ha.add(blk_clean)

                by_block[blk_clean] = {
                    'block_name': d.block_name,
                    'crop': d.crop_name,
                    'zone': d.zone or b_info.get('zona', ''),
                    'cost': 0.0,
                    'ha': b_ha,
                    'standard_beds': float(d.standard_beds or 0.0),
                    'liters': float(d.total_liters or 0.0),
                    'products': []
                }
            by_block[blk_clean]['cost'] += line_cost
            by_block[blk_clean]['products'].append({
                'product_code': d.product_code,
                'commercial_name': d.commercial_name or d.product_code,
                'dose': d.dose,
                'amount': p_amt,
                'unit': d.unit,
                'unit_cost': unit_cost,
                'cost': line_cost,
                'bodega': bodega_name
            })

            # 3. Desglose por Producto
            prod_key = d.commercial_name or d.product_code
            if prod_key not in by_product:
                by_product[prod_key] = {
                    'product_code': d.product_code,
                    'commercial_name': prod_key,
                    'bodega': bodega_name,
                    'unit': d.unit,
                    'unit_cost': unit_cost,
                    'total_amount': 0.0,
                    'total_cost': 0.0
                }
            by_product[prod_key]['total_amount'] += p_amt
            by_product[prod_key]['total_cost'] += line_cost

        # Fallback a FumigationOrderProductSummary si order.details no tiene costos calculados
        if total_cost <= 0.0 and getattr(order, 'product_summaries', None):
            for ps in order.product_summaries:
                p_code = (ps.product_code or '').strip().upper()
                c_nom = (ps.commercial_name or '').strip().upper()
                cost_info = prod_map.get(p_code) or prod_map.get(c_nom) or {}
                unit_cost = float(cost_info.get('costo_unitario') or 0.0)
                bodega_name = cost_info.get('bodega') or 'SIN BODEGA'
                p_amt = float(ps.total_required_quantity or 0.0)
                line_cost = round(p_amt * unit_cost, 4)
                total_cost += line_cost

                if bodega_name not in by_bodega:
                    by_bodega[bodega_name] = {'total_cost': 0.0, 'items_count': 0}
                by_bodega[bodega_name]['total_cost'] += line_cost
                by_bodega[bodega_name]['items_count'] += 1

                p_key = c_nom or p_code
                if p_key not in by_product:
                    by_product[p_key] = {
                        'product_code': p_code,
                        'commercial_name': p_key,
                        'bodega': bodega_name,
                        'unit': ps.dose_unit or 'CC',
                        'unit_cost': unit_cost,
                        'total_amount': 0.0,
                        'total_cost': 0.0
                    }
                by_product[p_key]['total_amount'] += p_amt
                by_product[p_key]['total_cost'] += line_cost

        # Calcular costo por ha por bloque
        block_list = []
        for b_key, b_data in by_block.items():
            b_cost = b_data['cost']
            b_ha = b_data['ha']
            cost_ha = round(b_cost / b_ha, 2) if b_ha > 0 else 0.0
            block_list.append({
                'block_name': b_data['block_name'],
                'crop': b_data['crop'],
                'zone': b_data['zone'],
                'cost': round(b_cost, 2),
                'ha': b_ha,
                'cost_per_ha': cost_ha,
                'standard_beds': b_data['standard_beds'],
                'liters': b_data['liters'],
                'products_count': len(b_data['products'])
            })
        block_list.sort(key=lambda x: x['cost'], reverse=True)

        cost_per_ha_total = round(total_cost / total_ha_order, 2) if total_ha_order > 0 else 0.0

        return {
            'order_id': order.id,
            'order_number': order.order_number,
            'week': order.week,
            'round_number': order.round_number,
            'round_name': order.round_name,
            'is_printed': order.is_printed,
            'status': order.status,
            'total_cost': round(total_cost, 2),
            'total_ha': round(total_ha_order, 4),
            'cost_per_ha': cost_per_ha_total,
            'by_bodega': {k: {'total_cost': round(v['total_cost'], 2), 'items_count': v['items_count']} for k, v in by_bodega.items()},
            'blocks': block_list,
            'products': list(by_product.values())
        }

    @classmethod
    def get_farm_cultivated_area(cls, week: Optional[str] = None) -> float:
        """
        Retorna el área cultivada oficial de la finca (19.99 ha) para el cálculo de $/ha de cultivo.
        Filtra por la semana específica o toma el último estado de cultivo disponible.
        """
        try:
            if week and str(week).strip().upper() != 'ALL':
                w_str = str(week).strip()
                val = db.session.execute(text("""
                    SELECT ROUND(COALESCE(SUM(ha), 19.99)::numeric, 2)
                    FROM dim_distribucion_areas 
                    WHERE es_cultivo = TRUE AND (semana_iso = :w OR semana_iso ILIKE :w_like)
                """), {'w': w_str, 'w_like': f"%{w_str}%"}).scalar()
                if val and float(val) > 0:
                    return float(val)

            # Fallback: última semana registrada en dim_distribucion_areas
            val = db.session.execute(text("""
                SELECT ROUND(COALESCE(SUM(ha), 19.99)::numeric, 2)
                FROM dim_distribucion_areas
                WHERE es_cultivo = TRUE AND semana_iso = (
                    SELECT semana_iso FROM dim_distribucion_areas ORDER BY anio DESC, semana DESC LIMIT 1
                )
            """)).scalar()
            return float(val) if val and float(val) > 0 else 19.99
        except Exception:
            return 19.99

    @classmethod
    def get_crop_official_areas(cls, week: Optional[str] = None) -> Dict[str, float]:
        """
        Retorna el mapa oficial de áreas activas por producto comercial / maestro para una semana.
        """
        try:
            target_week = None
            if week and str(week).strip().upper() != 'ALL':
                w_str = str(week).strip()
                chk = db.session.execute(text("SELECT semana_iso FROM dim_distribucion_areas WHERE semana_iso = :w LIMIT 1"), {'w': w_str}).scalar()
                if chk:
                    target_week = chk

            if not target_week:
                target_week = db.session.execute(text("""
                    SELECT semana_iso FROM dim_distribucion_areas ORDER BY anio DESC, semana DESC LIMIT 1
                """)).scalar()

            rows = db.session.execute(text("""
                SELECT UPPER(TRIM(producto_comercial)), ha 
                FROM dim_distribucion_areas 
                WHERE es_cultivo = TRUE AND semana_iso = :w
            """), {'w': target_week}).fetchall()
            return {r[0]: float(r[1]) for r in rows}
        except Exception:
            return {
                'SOLIDAGO': 4.60,
                'HYPERICUM': 5.61,
                'VERONICA': 3.63,
                'VERONICA SPLASH': 1.50,
                'BILLION LIGHTS': 4.12,
                'G49': 0.31,
                'G5': 0.12,
                'PIV': 0.04,
                'BRILLANTINA': 0.04,
                'LYSIMACHIA': 0.02
            }

    @classmethod
    def get_archived_week_cost(cls, week_str: str) -> Optional[Dict[str, Any]]:
        """
        Consulta si una semana ya se encuentra congelada y archivada en 'historico_costos_semanales'.
        Si está archivada, sus costos y $/ha son inmutables.
        IMPORTANTE: La semana actual en curso NUNCA se congela porque las aplicaciones y vueltas
        se siguen registrando durante la semana.
        """
        try:
            parts = str(week_str).split('-') if '-' in str(week_str) else ['2026', str(week_str)]
            y_val = int(parts[0]) if parts[0].isdigit() else 2026
            w_val = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else int(week_str) if str(week_str).isdigit() else 44
            w_iso = f"{y_val}-{w_val:02d}"

            # La semana actual en curso SIEMPRE se calcula en vivo dinámicamente
            from datetime import date
            today_cal = date.today().isocalendar()
            curr_y, curr_w = today_cal[0], today_cal[1]
            if (y_val > curr_y) or (y_val == curr_y and w_val >= curr_w):
                return None

            row = db.session.execute(text("""
                SELECT costo_total, area_ha, costo_por_ha, ha_tratada, costo_por_ha_tratada, ordenes_count, estado
                FROM historico_costos_semanales
                WHERE semana_iso = :w AND cultivo = 'TOTAL' AND estado = 'CERRADA'
            """), {'w': w_iso}).fetchone()
            if row:
                return dict(row._mapping)
        except Exception:
            pass
        return None

    @classmethod
    def archive_closed_week(cls, week_str: str, summary: Dict[str, Any]) -> None:
        """
        Congela y archiva una semana cerrada con el último hectareaje disponible de esa semana.
        Solo se archivan semanas estrictamente PASADAS que ya hayan concluido.
        """
        try:
            parts = str(week_str).split('-') if '-' in str(week_str) else ['2026', str(week_str)]
            y_val = int(parts[0]) if parts[0].isdigit() else 2026
            w_val = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else int(week_str) if str(week_str).isdigit() else 44
            w_iso = f"{y_val}-{w_val:02d}"

            from datetime import date
            today_cal = date.today().isocalendar()
            curr_y, curr_w = today_cal[0], today_cal[1]
            if (y_val > curr_y) or (y_val == curr_y and w_val >= curr_w):
                # La semana activa no se congela
                return

            area_finca = cls.get_farm_cultivated_area()
            tot_cost = float(summary.get('total_cost', 0.0))
            ha_trat = float(summary.get('total_ha', 0.0))
            c_ha_finca = round(tot_cost / area_finca, 2) if area_finca > 0 else 0.0
            c_ha_trat = round(tot_cost / ha_trat, 2) if ha_trat > 0 else 0.0

            db.session.execute(text("""
                INSERT INTO historico_costos_semanales (
                    anio, semana, semana_iso, cultivo, costo_total, area_ha, costo_por_ha,
                    ha_tratada, costo_por_ha_tratada, ordenes_count, estado, updated_at
                ) VALUES (
                    :y, :w, :w_iso, 'TOTAL', :tc, :ha, :cha, :ht, :cht, :oc, 'CERRADA', NOW()
                )
                ON CONFLICT (semana_iso, cultivo) DO UPDATE SET
                    costo_total = EXCLUDED.costo_total,
                    area_ha = EXCLUDED.area_ha,
                    costo_por_ha = EXCLUDED.costo_por_ha,
                    ha_tratada = EXCLUDED.ha_tratada,
                    costo_por_ha_tratada = EXCLUDED.costo_por_ha_tratada,
                    ordenes_count = EXCLUDED.ordenes_count,
                    estado = 'CERRADA',
                    updated_at = NOW();
            """), {
                'y': y_val, 'w': w_val, 'w_iso': w_iso, 'tc': tot_cost, 'ha': area_finca,
                'cha': c_ha_finca, 'ht': ha_trat, 'cht': c_ha_trat, 'oc': int(summary.get('orders_count', 0))
            })
            db.session.commit()
        except Exception:
            db.session.rollback()



    @classmethod
    def get_dashboard_cost_summary(cls, week: Optional[str] = None) -> Dict[str, Any]:
        """
        Genera el resumen de costos agronómicos para el Dashboard General y Ejecutivo:
        - Costo total invertido ($)
        - Costo por hectárea ($/ha)
        - Distribución por Bodega ($ y %)
        - Top bloques por inversión ($ y $/ha)
        - Histórico semana a semana de aplicaciones impresas/finalizadas.
        """
        prod_map = cls.get_product_cost_map()
        area_map = cls.get_block_area_map()

        def _build_week_filter(model_col, w_input):
            w_clean = str(w_input).strip()
            filters = [model_col == w_clean]
            digits = ''.join(c for c in w_clean if c.isdigit())
            if '-' in w_clean:
                parts = w_clean.split('-')
                try:
                    y = int(parts[0])
                    w = int(parts[1])
                    filters.extend([
                        model_col == f"{y}-{w:02d}",
                        model_col == f"{y}-{w}",
                        model_col == str(w),
                        model_col == f"{y%100:02d}{w:02d}",
                        model_col.ilike(f"%-{w:02d}"),
                        model_col.ilike(f"%-{w}"),
                        model_col.ilike(f"%{w:02d}"),
                        model_col.ilike(f"%{w}")
                    ])
                except Exception:
                    pass
            elif len(digits) == 4:
                y = 2000 + int(digits[:2])
                w = int(digits[2:])
                filters.extend([
                    model_col == f"{y}-{w:02d}",
                    model_col == f"{y}-{w}",
                    model_col == str(w),
                    model_col == digits,
                    model_col.ilike(f"%-{w:02d}"),
                    model_col.ilike(f"%{w:02d}"),
                    model_col.ilike(f"%{w}")
                ])
            elif digits:
                w = int(digits)
                filters.extend([
                    model_col == str(w),
                    model_col.ilike(f"%-{w:02d}"),
                    model_col.ilike(f"%-{w}"),
                    model_col.ilike(f"%{w:02d}"),
                    model_col.ilike(f"%{w}")
                ])
            return db.or_(*filters)

        # Consultar órdenes que estén impresas o finalizadas/aprobadas
        query = FumigationOrder.query
        if week and str(week).strip().upper() != 'ALL':
            query = query.filter(_build_week_filter(FumigationOrder.week, week))
        
        # Priorizar órdenes registradas
        orders = query.order_by(FumigationOrder.created_at.desc()).all()

        total_cost_all = 0.0
        total_ha_all = 0.0
        bodega_totals = {}
        bodega_products_map = {}
        block_totals = {}
        crop_totals = {}
        weekly_history = {}

        orders_count = 0
        blocks_treated_set = set()

        for o in orders:
            if not o.details:
                continue
            
            calc = cls.calculate_order_costs(o, prod_map=prod_map, area_map=area_map)
            orders_count += 1
            total_cost_all += calc['total_cost']
            
            w_key = o.week or 'S/S'
            if w_key not in weekly_history:
                weekly_history[w_key] = {'week': w_key, 'total_cost': 0.0, 'ha': 0.0, 'orders_count': 0}
            weekly_history[w_key]['total_cost'] += calc['total_cost']
            weekly_history[w_key]['ha'] += calc['total_ha']
            weekly_history[w_key]['orders_count'] += 1

            for bod_nom, b_data in calc['by_bodega'].items():
                bodega_totals[bod_nom] = bodega_totals.get(bod_nom, 0.0) + b_data['total_cost']

            # Acumular productos por bodega
            for p_item in calc.get('products', []):
                b_name = (p_item.get('bodega') or 'FUMIGANTES').strip()
                if b_name not in bodega_products_map:
                    bodega_products_map[b_name] = {}
                p_code = (p_item.get('product_code') or '').strip().upper()
                p_name = (p_item.get('commercial_name') or p_code).strip()
                p_key = f"{b_name}|||{p_code or p_name}"
                if p_key not in bodega_products_map[b_name]:
                    bodega_products_map[b_name][p_key] = {
                        'product_code': p_code,
                        'commercial_name': p_name,
                        'unit': p_item.get('unit') or 'CC',
                        'unit_cost': float(p_item.get('unit_cost') or 0.0),
                        'total_amount': 0.0,
                        'total_cost': 0.0
                    }
                bodega_products_map[b_name][p_key]['total_amount'] += float(p_item.get('total_amount') or 0.0)
                bodega_products_map[b_name][p_key]['total_cost'] += float(p_item.get('total_cost') or 0.0)

            for b_row in calc['blocks']:
                b_name = b_row['block_name']
                blocks_treated_set.add(b_name)
                if b_name not in block_totals:
                    block_totals[b_name] = {
                        'block_name': b_name,
                        'crop': b_row['crop'],
                        'zone': b_row['zone'],
                        'total_cost': 0.0,
                        'ha': b_row['ha'],
                        'applications_count': 0
                    }
                block_totals[b_name]['total_cost'] += b_row['cost']
                block_totals[b_name]['applications_count'] += 1

                c_nom = b_row['crop'] or 'VARIOS'
                if c_nom not in crop_totals:
                    crop_totals[c_nom] = {'crop': c_nom, 'total_cost': 0.0, 'ha': 0.0}
                crop_totals[c_nom]['total_cost'] += b_row['cost']
                crop_totals[c_nom]['ha'] += b_row['ha']

        # Si no hay órdenes con costo mayor a 0, consultar rotaciones de la semana
        if total_cost_all <= 0.0:
            rot_query = Rotation.query
            if week and str(week).strip().upper() != 'ALL':
                rot_query = rot_query.filter(_build_week_filter(Rotation.week, week))

            active_rots = rot_query.filter(
                ~Rotation.status.in_(['NO_EJECUTADA', 'ELIMINADA', 'CANCELADA'])
            ).order_by(Rotation.id.desc()).all()

            if not active_rots and (not week or str(week).strip().upper() == 'ALL'):
                active_rots = Rotation.query.filter(
                    ~Rotation.status.in_(['NO_EJECUTADA', 'ELIMINADA', 'CANCELADA'])
                ).order_by(Rotation.id.desc()).limit(1).all()

            for active_rot in active_rots:
                if not active_rot.rounds:
                    continue
                import json
                rev_by_round = {}
                if active_rot.review_data_by_round_json:
                    try:
                        rev_by_round = json.loads(active_rot.review_data_by_round_json)
                    except Exception:
                        pass

                from app.modules.fumigacion.services.calculation_engine import CalculationEngine
                w_key = active_rot.week or (str(week) if week else 'S/S')

                for rd in active_rot.rounds:
                    custom_segs = rev_by_round.get(str(rd.id))
                    if not custom_segs and active_rot.review_data_json:
                        try:
                            custom_segs = json.loads(active_rot.review_data_json)
                        except Exception:
                            pass

                    calc_res = CalculationEngine.calculate_round(rd, custom_review_segments=custom_segs)
                    rd_prods = calc_res.get('product_summaries', [])
                    rd_segs = calc_res.get('segments', [])

                    # Fallback si rd_prods no devolvió insumos pero la vuelta tiene items configurados
                    if not rd_prods and getattr(rd, 'items', None):
                        for item in rd.items:
                            from app.shared.models import Product
                            prod_obj = Product.query.get(item.product_id)
                            if prod_obj:
                                p_c = (prod_obj.code or '').strip().upper()
                                c_n = (prod_obj.commercial_name or p_c).strip().upper()
                                est_qty = float(item.dose_applied or 0.0) * 10.0
                                rd_prods.append({
                                    'product_code': p_c,
                                    'commercial_name': c_n,
                                    'total_required_quantity': est_qty
                                })

                    rd_cost = 0.0
                    for ps in rd_prods:
                        p_code = str(ps.get('product_code', '')).strip().upper()
                        c_nom = str(ps.get('commercial_name', '')).strip().upper()
                        c_info = prod_map.get(p_code) or prod_map.get(c_nom) or {}
                        unit_cost = float(c_info.get('costo_unitario') or 0.0)
                        tot_req = float(ps.get('total_required_quantity') or 0.0)
                        line_cost = round(tot_req * unit_cost, 4)
                        rd_cost += line_cost
                        total_cost_all += line_cost

                        bod_name = c_info.get('bodega') or 'FUMIGANTES'
                        bodega_totals[bod_name] = bodega_totals.get(bod_name, 0.0) + line_cost
                        p_k = f"{bod_name}|||{p_code or c_nom}"
                        if bod_name not in bodega_products_map:
                            bodega_products_map[bod_name] = {}
                        if p_k not in bodega_products_map[bod_name]:
                            bodega_products_map[bod_name][p_k] = {
                                'product_code': p_code,
                                'commercial_name': c_nom or p_code,
                                'unit': c_info.get('unidad_medida') or 'CC',
                                'unit_cost': unit_cost,
                                'total_amount': 0.0,
                                'total_cost': 0.0
                            }
                        bodega_products_map[bod_name][p_k]['total_amount'] += tot_req
                        bodega_products_map[bod_name][p_k]['total_cost'] += line_cost

                    rd_liters = float(calc_res.get('totals', {}).get('total_liters', 1.0)) or 1.0
                    for seg in rd_segs:
                        raw_blk = str(seg.get('block_name', '')).replace('BL', '').strip().upper()
                        if not raw_blk:
                            continue
                        blocks_treated_set.add(raw_blk)
                        b_info = area_map.get(raw_blk, {'ha': 0.0, 'zona': seg.get('zone', '')})
                        b_ha = b_info['ha']
                        c_nom = str(seg.get('crop_name', 'VARIOS')).strip().upper()

                        seg_liters = float(seg.get('total_liters', 0.0))
                        seg_cost = round((seg_liters / rd_liters) * rd_cost, 2)

                        if raw_blk not in block_totals:
                            block_totals[raw_blk] = {
                                'block_name': raw_blk,
                                'crop': c_nom,
                                'zone': seg.get('zone', b_info.get('zona', '')),
                                'total_cost': 0.0,
                                'ha': b_ha,
                                'applications_count': 0
                            }
                        block_totals[raw_blk]['total_cost'] += seg_cost
                        block_totals[raw_blk]['applications_count'] += 1

                        if c_nom not in crop_totals:
                            crop_totals[c_nom] = {'crop': c_nom, 'total_cost': 0.0, 'ha': 0.0}
                        crop_totals[c_nom]['total_cost'] += seg_cost
                        crop_totals[c_nom]['ha'] += b_ha

                    orders_count += 1

                if w_key not in weekly_history:
                    weekly_history[w_key] = {'week': w_key, 'total_cost': round(total_cost_all, 2), 'ha': round(sum(b['ha'] for b in block_totals.values()), 2), 'orders_count': orders_count}

        # Asegurar que el histórico semanal incluya todas las rotaciones aprobadas
        try:
            from app.modules.fumigacion.services.calculation_engine import CalculationEngine
            all_approved_rots = Rotation.query.filter(Rotation.status == 'APROBADA').all()
            for a_rot in all_approved_rots:
                a_wk = a_rot.week or 'S/S'
                if a_wk not in weekly_history:
                    rot_cost_hist = 0.0
                    rot_ha_hist = 0.0
                    for a_rd in a_rot.rounds:
                        c_res = CalculationEngine.calculate_round(a_rd)
                        for a_ps in c_res.get('product_summaries', []):
                            p_c = str(a_ps.get('product_code', '')).strip().upper()
                            u_c = float(prod_map.get(p_c, {}).get('costo_unitario') or 0.0)
                            rot_cost_hist += float(a_ps.get('total_required_quantity') or 0.0) * u_c
                        rot_ha_hist += sum(area_map.get(str(s.get('block_name', '')).replace('BL', '').strip().upper(), {}).get('ha', 0.0) for s in c_res.get('segments', []))
                    weekly_history[a_wk] = {
                        'week': a_wk,
                        'total_cost': round(rot_cost_hist, 2),
                        'ha': round(rot_ha_hist, 2),
                        'orders_count': len(a_rot.rounds)
                    }
        except Exception:
            pass

        # Área cultivada de la finca oficial de la semana (19.99 ha) y áreas por cultivo
        farm_cultivated_area = cls.get_farm_cultivated_area(week)
        crop_official_areas = cls.get_crop_official_areas(week)

        # Calcular hectáreas acumuladas de los bloques tratados
        total_ha_treated = sum(area_map.get(str(b).replace('BL', '').strip().upper(), {}).get('ha', 0.0) for b in blocks_treated_set)
        if total_ha_treated <= 0:
            total_ha_treated = sum(v['ha'] for v in block_totals.values())

        # Costo por hectárea tratada y costo por hectárea cultivada de la finca
        cost_per_ha_finca = round(total_cost_all / farm_cultivated_area, 2) if farm_cultivated_area > 0 else 0.0
        avg_cost_ha = round(total_cost_all / total_ha_treated, 2) if total_ha_treated > 0 else 0.0

        # Si se consultó una semana específica pasada y ya está archivada, usar sus valores congelados
        if week and str(week).strip().upper() != 'ALL':
            archived = cls.get_archived_week_cost(str(week).strip())
            if archived:
                total_cost_all = float(archived.get('costo_total', total_cost_all))
                farm_cultivated_area = float(archived.get('area_ha', farm_cultivated_area))
                cost_per_ha_finca = float(archived.get('costo_por_ha', cost_per_ha_finca))
                total_ha_treated = float(archived.get('ha_tratada', total_ha_treated))
                avg_cost_ha = float(archived.get('costo_por_ha_tratada', avg_cost_ha))

        # Formatear lista de top bloques
        top_blocks = []
        for b_name, b_val in block_totals.items():
            ha_b = b_val['ha']
            c_ha = round(b_val['total_cost'] / ha_b, 2) if ha_b > 0 else 0.0
            top_blocks.append({
                'block_name': b_name,
                'crop': b_val['crop'],
                'zone': b_val['zone'],
                'total_cost': round(b_val['total_cost'], 2),
                'ha': round(ha_b, 4),
                'cost_per_ha': c_ha,
                'applications_count': b_val['applications_count']
            })
        top_blocks.sort(key=lambda x: x['total_cost'], reverse=True)

        # Formatear bodegas con porcentajes y lista de insumos ordenados de mayor a menor consumo ($)
        bodega_list = []
        for bod_nom, bod_c in sorted(bodega_totals.items(), key=lambda x: x[1], reverse=True):
            pct = round((bod_c / total_cost_all) * 100, 1) if total_cost_all > 0 else 0.0
            b_prods_dict = bodega_products_map.get(bod_nom, {})
            b_prods = list(b_prods_dict.values())
            b_prods.sort(key=lambda p: p['total_cost'], reverse=True)
            for p in b_prods:
                p['pct_bodega'] = round((p['total_cost'] / bod_c) * 100, 1) if bod_c > 0 else 0.0
                p['total_amount'] = round(p['total_amount'], 2)
                p['total_cost'] = round(p['total_cost'], 2)
                p['unit_cost'] = round(p['unit_cost'], 4)

            bodega_list.append({
                'bodega': bod_nom,
                'cost': round(bod_c, 2),
                'pct': pct,
                'products': b_prods
            })

        # Formatear by_crop usando el área oficial del cultivo si está disponible
        by_crop_list = []
        for k, v in sorted(crop_totals.items(), key=lambda x: x[1]['total_cost'], reverse=True):
            k_clean = str(k).strip().upper()
            c_ha = crop_official_areas.get(k_clean) or v['ha']
            c_cost_ha = round(v['total_cost'] / c_ha, 2) if c_ha > 0 else 0.0
            by_crop_list.append({
                'crop': k, 
                'total_cost': round(v['total_cost'], 2), 
                'ha': round(c_ha, 2), 
                'cost_per_ha': c_cost_ha
            })

        summary_data = {
            'total_cost': round(total_cost_all, 2),
            'total_ha': round(total_ha_treated, 2),
            'farm_cultivated_ha': round(farm_cultivated_area, 2),
            'avg_cost_per_ha': avg_cost_ha,
            'cost_per_ha_finca': cost_per_ha_finca,
            'orders_count': orders_count,
            'blocks_count': len(blocks_treated_set),
            'by_bodega': bodega_list,
            'top_blocks': top_blocks[:10],
            'all_blocks': top_blocks,
            'by_crop': by_crop_list,
            'weekly_history': list(weekly_history.values())
        }

        # Si se consultó una semana específica y ya es estrictamente pasada, congelarla
        try:
            from datetime import date
            today_cal = date.today().isocalendar()
            curr_y, curr_w = today_cal[0], today_cal[1]
            parts_w = str(week).split('-') if '-' in str(week) else ['2026', str(week)]
            y_q = int(parts_w[0]) if parts_w[0].isdigit() else 2026
            w_q = int(parts_w[1]) if len(parts_w) > 1 and parts_w[1].isdigit() else int(week) if str(week).isdigit() else 0
            if week and str(week).strip().upper() != 'ALL' and total_cost_all > 0 and (y_q < curr_y or (y_q == curr_y and w_q < curr_w)):
                cls.archive_closed_week(str(week).strip(), summary_data)
        except Exception:
            pass

        return summary_data
