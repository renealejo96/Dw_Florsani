import math
import json
import re
import unicodedata
from functools import lru_cache
from app.extensions import db
from app.shared.models import Crop, Product, Litraje, CropStateRecord, AdditionalApplication
from app.shared.utils import (
    get_operator_for_zone, get_toxicological_color_info, 
    is_integer_unit, round_product_amount, round_to_tank_step,
    safe_float, safe_int, get_week_difference, clean_block_name
)

_NON_ALPHANUM_REGEX = re.compile(r'[^a-zA-Z0-9]+')

class ProjectedCropRecord:
    """
    Lightweight, detached record for calculation engine.
    Holds age projected to the target rotation week.
    """
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

class CropData:
    """
    Lightweight, detached-safe representation of Crop configuration.
    Prevents DetachedInstanceError across transactions and web requests.
    """
    def __init__(self, id=None, name='', category='PRODUCTOS_NUEVOS', aliases=None, 
                 veg_min_age=2, veg_max_age=9, prod_min_age=10, prod_max_age=999, is_active=True):
        self.id = id
        self.name = name or ''
        self.category = category or 'PRODUCTOS_NUEVOS'
        self.aliases = list(aliases) if aliases else []
        self.veg_min_age = max(2, veg_min_age) if veg_min_age is not None else 2
        self.veg_max_age = veg_max_age if veg_max_age is not None else 9
        self.prod_min_age = prod_min_age if prod_min_age is not None else 10
        self.prod_max_age = prod_max_age if prod_max_age is not None else 999
        self.is_active = is_active

    def classify_age(self, age):
        if age is None:
            return 'SIN_EDAD'
        try:
            age_val = float(age)
        except (ValueError, TypeError):
            return 'INVALIDO'
        if age_val < 2.0:
            return 'DESCANSO'  # Post-siembra o post-poda reciente (< 2 semanas). No se fumiga foliar.
        if self.veg_min_age <= age_val <= self.veg_max_age:
            return 'VEGETATIVO'
        elif self.prod_min_age <= age_val <= self.prod_max_age:
            return 'PRODUCTIVO'
        else:
            return 'FUERA_DE_RANGO'


class CalculationEngine:

    @staticmethod
    @lru_cache(maxsize=4096)
    def normalize_crop_name(crop_name: str) -> str:
        if not crop_name:
            return ""
        n = str(crop_name).strip()
        n = unicodedata.normalize('NFKD', n).encode('ASCII', 'ignore').decode('utf-8')
        n = _NON_ALPHANUM_REGEX.sub('_', n).strip('_').upper()
        return n

    _crops_cache = None
    _crops_cache_time = 0
    _litraje_cache = None
    _litraje_cache_time = 0

    @classmethod
    def get_all_crops(cls):
        import time
        now = time.time()
        if cls._crops_cache is None or (now - cls._crops_cache_time > 60):
            try:
                db_crops = Crop.query.all()
                cls._crops_cache = [
                    CropData(
                        id=c.id,
                        name=c.name,
                        category=c.category,
                        aliases=list(c.aliases) if c.aliases else [],
                        veg_min_age=c.veg_min_age,
                        veg_max_age=c.veg_max_age,
                        prod_min_age=c.prod_min_age,
                        prod_max_age=c.prod_max_age,
                        is_active=c.is_active
                    ) for c in db_crops
                ]
            except Exception:
                cls._crops_cache = []
            cls._crops_cache_time = now
        return cls._crops_cache

    @classmethod
    def get_litraje_map(cls):
        import time
        now = time.time()
        if cls._litraje_cache is None or (now - cls._litraje_cache_time > 60):
            all_lits = Litraje.query.all()
            l_map = {}
            for l in all_lits:
                c_clean = cls.normalize_crop_name(l.crop_name)
                l_map[(c_clean, int(round(l.age)))] = l.liters_per_bed
            cls._litraje_cache = l_map
            cls._litraje_cache_time = now
        return cls._litraje_cache

    @classmethod
    def get_crop_config(cls, crop_identifier: str):
        """
        Finds the Crop configuration model by name or alias with tolerant matching using cached crop models.
        """
        if not crop_identifier:
            return None
        crop_id_clean = cls.normalize_crop_name(crop_identifier)
        crops = cls.get_all_crops()
        for c in crops:
            if cls.normalize_crop_name(c.name) == crop_id_clean:
                return c
            for alias in c.aliases:
                alias_clean = cls.normalize_crop_name(alias)
                if alias_clean == crop_id_clean or crop_id_clean in alias_clean or alias_clean in crop_id_clean:
                    return c
        return None

    @classmethod
    def match_records_for_crop(cls, crop_obj, records_query):
        """
        Filters CropStateRecords that belong to the specified crop_obj.
        Excludes VACIO, unclassified or discarded beds.
        Optimized with in-memory property caching to avoid redundant string normalizations.
        """
        if not crop_obj:
            return []

        search_terms = [cls.normalize_crop_name(crop_obj.name)]
        for alias in crop_obj.aliases:
            search_terms.append(cls.normalize_crop_name(alias))

        c_name_u = (crop_obj.name or '').upper()
        if 'HYPERICUM' in c_name_u or 'HYPERYCUM' in c_name_u:
            search_terms.extend(['HYPERICUM', 'HYPERYCUM', 'MAGICAL'])
        elif 'VERONICA' in c_name_u:
            search_terms.extend(['VERONICA', 'VERONICA_SPRAY', 'VERONICA_SPLASH', 'SKYLER'])
        elif 'GYPSOPHILA' in c_name_u or 'GYPSO' in c_name_u:
            search_terms.extend(['GYPSOPHILA', 'XLENCE', 'BILLION_LIGHTS', 'GYPSO'])
        elif 'SOLIDAGO' in c_name_u:
            search_terms.extend(['SOLIDAGO', 'GOLDEN_GLORY_YELLOW'])
        elif 'RUSCUS' in c_name_u:
            search_terms.extend(['RUSCUS'])
        elif 'SUNFLOWER' in c_name_u or 'GIRASOL' in c_name_u:
            search_terms.extend(['SUNFLOWER', 'GIRASOL'])
        elif 'RUMEX' in c_name_u:
            search_terms.extend(['RUMEX'])
        elif 'LYSIMACHIA' in c_name_u:
            search_terms.extend(['LYSIMACHIA'])
        elif 'ASTER' in c_name_u:
            search_terms.extend(['ASTER'])

        search_terms = list(set([cls.normalize_crop_name(s) for s in search_terms if s]))

        matched = []
        for r in records_query:
            if not r.crop_master or r.crop_master.upper() in ('VACIO', 'DESCARTE', 'TUMBAR', 'NAN', 'TOTAL', 'TOTAL_GENERAL'):
                continue

            r_crop_master = getattr(r, '_norm_crop_master', None)
            if r_crop_master is None:
                r_crop_master = cls.normalize_crop_name(r.crop_master)
                r._norm_crop_master = r_crop_master
                r._norm_product = cls.normalize_crop_name(r.product_name or '')
                r._norm_variety = cls.normalize_crop_name(r.variety or '')

            r_product = r._norm_product
            r_variety = r._norm_variety

            is_match = False
            for term in search_terms:
                if not term:
                    continue
                if (term == r_crop_master or 
                    term == r_product or 
                    term in r_product or 
                    term in r_variety or 
                    term in r_crop_master or
                    r_crop_master in term):
                    is_match = True
                    break
                if r_crop_master in ('PROD_NUEVOS', 'PROD_NUEVO', 'NUEVOS_PROD'):
                    if term in r_product or term in r_variety or r_product in term:
                        is_match = True
                        break

            if is_match:
                matched.append(r)

        return matched

    @classmethod
    def get_liters_per_bed(cls, crop_name: str, age: float) -> tuple[float, bool]:
        """
        Looks up liters per bed from in-memory Litraje table cache for given crop and age.
        Skips 0.0 litraje placeholder entries and smoothly falls back to the highest positive litraje for late-harvest beds.
        """
        if age is None:
            return 0.0, False

        int_age = int(round(age))
        crop_clean = cls.normalize_crop_name(crop_name)
        l_map = cls.get_litraje_map()

        # 1. Direct match with positive litraje
        if (crop_clean, int_age) in l_map and l_map[(crop_clean, int_age)] > 0.0:
            return l_map[(crop_clean, int_age)], True

        # 2. Check aliases with positive litraje
        crop_obj = cls.get_crop_config(crop_name)
        candidate_names = [crop_clean]
        if crop_obj:
            candidates = [crop_obj.name.upper()] + [a.upper() for a in crop_obj.aliases]
            if 'GYPSOPHILA' in crop_obj.name.upper():
                candidates.extend(['XLENCE', 'BILLION LIGHTS', 'GYPSO', 'MILLION STAR', 'SMALL BLOOM'])
            elif 'VERONICA' in crop_obj.name.upper():
                candidates.extend(['VERONICA', 'VERONICA SPLASH'])

            for c_name in set(candidates):
                cn_clean = cls.normalize_crop_name(c_name)
                candidate_names.append(cn_clean)
                if (cn_clean, int_age) in l_map and l_map[(cn_clean, int_age)] > 0.0:
                    return l_map[(cn_clean, int_age)], True

        # 3. Fallback to closest configured age with positive litraje
        unique_candidates = set(candidate_names)
        matching_entries = [
            (k_age, ltr) for (k_crop, k_age), ltr in l_map.items()
            if k_crop in unique_candidates and ltr > 0.0
        ]
        if matching_entries:
            max_configured_age = max(k_age for k_age, _ in matching_entries)
            if int_age >= max_configured_age:
                highest_entry = max([e for e in matching_entries if e[0] == max_configured_age], key=lambda x: x[1])
                return highest_entry[1], True
            else:
                closest = min(matching_entries, key=lambda x: abs(x[0] - int_age))
                return closest[1], True

        return 0.0, False

    @staticmethod
    def format_bed_range(bed_nums: list[int]) -> str:
        """
        Formats bed numbers into clean range, e.g. "Camas 1-11" or "Camas 4-54"
        """
        if not bed_nums:
            return ""
        unique_beds = sorted(list(set(bed_nums)))
        if len(unique_beds) == 1:
            return f"Cama {unique_beds[0]}"

        ranges = []
        start = unique_beds[0]
        prev = unique_beds[0]

        for b in unique_beds[1:]:
            if b == prev + 1:
                prev = b
            else:
                if start == prev:
                    ranges.append(str(start))
                else:
                    ranges.append(f"{start}-{prev}")
                start = b
                prev = b
        
        if start == prev:
            ranges.append(str(start))
        else:
            ranges.append(f"{start}-{prev}")

        return f"Camas {', '.join(ranges)}"

    @classmethod
    def get_default_spray_lance(cls, crop_name: str) -> str:
        c = str(crop_name or '').upper()
        if 'SOLIDAGO' in c:
            return 'Aguilón Solidago'
        elif 'VERONICA' in c:
            return 'Lanza Veronica'
        return 'Lanza de 3 salidas (C35)'

    @classmethod
    def calculate_round(cls, round_obj, custom_review_segments=None, cached_crop_records=None):
        """
        Calculates application segments, product quantities, assigned operators and toxicological info for a round.
        If custom_review_segments is provided, it uses the agronomist's approved/adjusted segments!
        """
        warnings = []
        segments = []
        product_summary_map = {}

        if not round_obj or not round_obj.items:
            return {
                'segments': [],
                'product_summaries': [],
                'totals': {'total_liters': 0.0, 'total_standard_beds': 0.0, 'total_segments': 0, 'total_beds': 0},
                'warnings': ["La vuelta no tiene productos ni cultivos asignados."]
            }

        # If custom reviewed segments are provided (from agronomist review tab)
        if custom_review_segments is not None and len(custom_review_segments) > 0:
            # Pre-index round items by crop & stage to sync recipe/doses dynamically
            mixes_by_crop_stage = {}
            for it_idx, item in enumerate(sorted(round_obj.items, key=lambda x: getattr(x, 'order_index', 0))):
                c_key = cls.normalize_crop_name(item.crop_name)
                s_key = (item.phenological_stage or '').strip().upper()
                mix_k = (c_key, s_key)
                if mix_k not in mixes_by_crop_stage:
                    mixes_by_crop_stage[mix_k] = []
                mixes_by_crop_stage[mix_k].append((it_idx, item))

            # Calculate from custom reviewed segments
            total_liters_all = 0.0
            total_std_beds_all = 0.0
            total_beds_count = 0

            for seg in custom_review_segments:
                if not isinstance(seg, dict):
                    continue
                raw_block = (seg.get('block_name') or '').strip()
                suffix = (seg.get('suffix') or 'A').strip().upper()
                clean_blk = clean_block_name(raw_block, suffix)
                crop_name = (seg.get('crop_name') or '').strip()
                variety = (seg.get('variety') or crop_name).strip()
                stage = (seg.get('phenological_stage') or 'VEGETATIVO').strip().upper()
                raw_age = seg.get('real_age', '')
                if raw_age is None:
                    real_age = ''
                elif isinstance(raw_age, (int, float)):
                    real_age = int(round(raw_age)) if raw_age == int(round(raw_age)) else raw_age
                else:
                    real_age = str(raw_age).strip()
                    if real_age.endswith('.0'):
                        real_age = real_age[:-2]
                bed_start = safe_int(seg.get('bed_start'), default=1)
                bed_end = safe_int(seg.get('bed_end'), default=max(1, bed_start))
                if bed_start > bed_end:
                    bed_start, bed_end = bed_end, bed_start
                bed_count = max(1, bed_end - bed_start + 1)
                standard_beds = round(safe_float(seg.get('standard_beds'), default=1.0), 2)
                liters_per_bed = round(safe_float(seg.get('liters_per_bed'), default=0.0), 1)
                raw_liters = standard_beds * liters_per_bed
                segment_liters = round_to_tank_step(raw_liters, step=20)
                zone = (seg.get('zone') or '').strip()
                operator = seg.get('operator') or get_operator_for_zone(zone)
                bed_range_str = seg.get('bed_range', f"Camas {bed_start}-{bed_end}")

                # Retrieve products assigned to this segment (sync with round recipe if not a manually customized block mix)
                is_custom_mix = bool(seg.get('is_custom_mix', False))
                products_list = seg.get('products', [])

                if not is_custom_mix or not products_list:
                    c_norm = cls.normalize_crop_name(crop_name)
                    s_norm = stage.upper()
                    round_items_matching = mixes_by_crop_stage.get((c_norm, s_norm))
                    if not round_items_matching:
                        # Dynamic partial match by crop
                        for (ck, sk), items_list in mixes_by_crop_stage.items():
                            if ck == c_norm or ck in c_norm or c_norm in ck:
                                if sk == s_norm or not sk:
                                    round_items_matching = items_list
                                    break
                    if not round_items_matching and mixes_by_crop_stage:
                        # Fallback to first available mix if crop matches
                        for (ck, sk), items_list in mixes_by_crop_stage.items():
                            if ck == c_norm:
                                round_items_matching = items_list
                                break

                    if round_items_matching:
                        products_list = []
                        for it_idx, it in round_items_matching:
                            prod = it.product
                            dose = it.dose_applied or 0.0
                            dose_unit = it.dose_unit or (prod.unit if prod else 'CC')
                            prod_code = prod.code if prod else 'N/A'
                            comm_name = prod.commercial_name if (prod and prod.commercial_name) else prod_code
                            pest = prod.pest if prod else ''
                            ia = prod.active_ingredient if prod else ''
                            ct = prod.toxicological_category if prod else ''
                            products_list.append({
                                'product_id': prod.id if prod else None,
                                'product_code': prod_code,
                                'commercial_name': comm_name,
                                'dose': dose,
                                'dose_unit': dose_unit,
                                'pest': pest,
                                'active_ingredient': ia,
                                'toxicological_category': ct
                            })

                products_detail = []
                products_summary_text_parts = []

                if isinstance(products_list, list):
                    for it_idx, it in enumerate(products_list):
                        if not isinstance(it, dict):
                            continue
                        prod_id = it.get('product_id')
                        prod_code = (it.get('product_code') or 'N/A').strip()
                        comm_name = (it.get('commercial_name') or prod_code).strip()
                        dose = safe_float(it.get('dose'), default=0.0)
                        dose_unit = (it.get('dose_unit') or 'CC').strip()
                        pest = (it.get('pest') or '').strip()
                        ia = (it.get('active_ingredient') or '').strip()
                        ct = (it.get('toxicological_category') or '').strip()
                        color_info = get_toxicological_color_info(ct)

                        product_amount = round_product_amount(segment_liters * dose, dose_unit)

                        products_detail.append({
                            'product_id': prod_id,
                            'product_code': prod_code,
                            'commercial_name': comm_name,
                            'dose': dose,
                            'dose_unit': dose_unit,
                            'product_amount': product_amount,
                            'pest': pest,
                            'active_ingredient': ia,
                            'toxicological_category': ct,
                            'toxicological_color': color_info['name'],
                            'color_info': color_info,
                            'order_in_mix': it_idx
                        })

                        products_summary_text_parts.append(f"{prod_code} ({dose} {dose_unit}/L)")

                        summary_key = (prod_code, dose, dose_unit)
                        if summary_key not in product_summary_map:
                            product_summary_map[summary_key] = {
                                'product_id': prod_id,
                                'product_code': prod_code,
                                'commercial_name': comm_name,
                                'dose': dose,
                                'dose_unit': dose_unit,
                                'pest': pest,
                                'total_required_quantity': 0.0
                            }
                        product_summary_map[summary_key]['total_required_quantity'] += product_amount

                segment_data = {
                    'round_number': round_obj.round_number,
                    'round_name': round_obj.name,
                    'scheduled_day': round_obj.scheduled_day,
                    'scheduled_date': round_obj.scheduled_date,
                    'operator': operator,
                    'zone': zone,
                    'block_name': clean_blk,
                    'suffix': suffix,
                    'crop_name': crop_name,
                    'variety': variety,
                    'phenological_stage': stage,
                    'real_age': real_age,
                    'bed_start': bed_start,
                    'bed_end': bed_end,
                    'bed_range': bed_range_str,
                    'bed_count': bed_count,
                    'standard_beds': standard_beds,
                    'liters_per_bed': liters_per_bed,
                    'total_liters': segment_liters,
                    'spray_lance': seg.get('spray_lance') or cls.get_default_spray_lance(crop_name),
                    'products': products_list,
                    'products_detail': products_detail,
                    'products_summary_text': ", ".join(products_summary_text_parts),
                    'is_custom_mix': bool(seg.get('is_custom_mix', False)),
                    'is_additional': bool(seg.get('is_additional', False)),
                    'is_modified': bool(seg.get('is_modified', False))
                }

                segments.append(segment_data)
                total_liters_all += segment_liters
                total_std_beds_all += standard_beds
                total_beds_count += bed_count

            # Preserve user-defined order from custom_review_segments (e.g. custom Drag & Drop sequence)
            product_summaries = list(product_summary_map.values())
            for ps in product_summaries:
                ps['total_required_quantity'] = round_product_amount(ps['total_required_quantity'], ps.get('dose_unit', 'CC'))
            product_summaries.sort(key=lambda p: p['product_code'])

            return {
                'segments': segments,
                'product_summaries': product_summaries,
                'totals': {
                    'total_liters': round(total_liters_all, 1),
                    'total_standard_beds': round(total_std_beds_all, 2),
                    'total_segments': len(segments),
                    'total_beds': total_beds_count
                },
                'warnings': warnings
            }

        # Otherwise calculate from database crop state (filtered by rotation week to prevent multi-week duplication)
        if cached_crop_records is not None:
            all_active_records = cached_crop_records
        else:
            rot_week = round_obj.rotation.week.strip() if (round_obj and round_obj.rotation and round_obj.rotation.week) else None
            available_weeks = [w[0] for w in db.session.query(CropStateRecord.week).distinct().order_by(CropStateRecord.week.desc()).all() if w[0]]

            filter_week = None
            if rot_week:
                for aw in available_weeks:
                    if aw.strip() == rot_week or rot_week in aw or aw in rot_week or get_week_difference(rot_week, aw) == 0:
                        filter_week = aw
                        break

            if not filter_week and available_weeks:
                filter_week = available_weeks[0]

            rec_query = CropStateRecord.query.filter(
                CropStateRecord.crop_master.isnot(None),
                ~CropStateRecord.crop_master.in_(['VACIO', 'DESCARTE', 'TUMBAR', 'NAN', 'TOTAL', 'TOTAL_GENERAL'])
            )
            if filter_week:
                rec_query = rec_query.filter(CropStateRecord.week == filter_week)

            raw_records = rec_query.all()
            week_offset = get_week_difference(rot_week, filter_week) if (rot_week and filter_week) else 0

            all_active_records = []
            for r in raw_records:
                base_age = 10.0 if (r.real_age is None or r.real_age < 0) else float(r.real_age)
                proj_age = round(base_age + week_offset, 1)
                # Restricción agronómica: camas con edad menor a 2 semanas no entran a fumigación foliar
                if proj_age < 2.0:
                    continue
                clean_blk = clean_block_name(r.block_num or r.block_full, r.suffix)
                all_active_records.append(ProjectedCropRecord(
                    id=r.id,
                    week=rot_week or r.week,
                    block_full=r.block_full,
                    block_num=clean_blk,
                    bed_num=r.bed_num,
                    suffix=r.suffix or 'A',
                    crop_master=r.crop_master,
                    product_name=r.product_name,
                    variety=r.variety,
                    standard_bed=float(r.standard_bed or 0.0),
                    zone=r.zone,
                    real_age=proj_age,
                    status_raw=r.status_raw
                ))

        mixes_by_crop_stage = {}
        for it_idx, item in enumerate(sorted(round_obj.items, key=lambda x: x.order_index)):
            key = (item.crop_name.strip(), item.phenological_stage.strip().upper())
            if key not in mixes_by_crop_stage:
                mixes_by_crop_stage[key] = []
            mixes_by_crop_stage[key].append((it_idx, item))

        total_liters_all = 0.0
        total_std_beds_all = 0.0
        total_beds_count = 0

        for (crop_name, stage), items_with_idx in mixes_by_crop_stage.items():
            crop_obj = cls.get_crop_config(crop_name)
            if not crop_obj:
                # Dynamic fallback object
                crop_obj = CropData(
                    name=crop_name.strip(),
                    aliases=[crop_name.strip()],
                    veg_min_age=0,
                    veg_max_age=9,
                    prod_min_age=10,
                    prod_max_age=999
                )

            crop_records = cls.match_records_for_crop(crop_obj, all_active_records)
            if not crop_records:
                continue

            stage_records = []
            for r in crop_records:
                classified_stage = crop_obj.classify_age(r.real_age)
                if classified_stage == stage:
                    lit, _ = cls.get_liters_per_bed(crop_name, r.real_age)
                    r._liters_per_bed = lit
                    stage_records.append(r)

            if not stage_records:
                continue

            # 1. Group records for this stage by physical block and zone
            block_stage_groups = {}
            for r in stage_records:
                clean_blk = clean_block_name(r.block_num or r.block_full, r.suffix)
                r._clean_blk = clean_blk
                key = (clean_blk, (r.zone or '').strip())
                if key not in block_stage_groups:
                    block_stage_groups[key] = []
                block_stage_groups[key].append(r)

            # 2. For each block, split into physically contiguous chunks that have identical litraje
            for (clean_blk, zone), block_recs in block_stage_groups.items():
                block_recs.sort(key=lambda x: (x.bed_num, x.suffix))

                chunks = []
                curr_chunk = []
                for r in block_recs:
                    if not curr_chunk:
                        curr_chunk.append(r)
                    else:
                        prev_r = curr_chunk[-1]
                        same_lit = (round(curr_chunk[0]._liters_per_bed, 1) == round(r._liters_per_bed, 1))
                        is_contig = (r.bed_num == prev_r.bed_num or r.bed_num == prev_r.bed_num + 1)
                        if same_lit and is_contig:
                            curr_chunk.append(r)
                        else:
                            chunks.append(curr_chunk)
                            curr_chunk = [r]
                if curr_chunk:
                    chunks.append(curr_chunk)

                # 3. Build each contiguous segment with exact start and end beds
                for recs in chunks:
                    bed_nums = sorted(list(set(r.bed_num for r in recs)))
                    bed_min = bed_nums[0]
                    bed_max = bed_nums[-1]
                    bed_range_str = f"Camas {bed_min}-{bed_max}" if bed_min != bed_max else f"Cama {bed_min}"
                    bed_count = len(bed_nums)
                    total_std_beds = sum(r.standard_bed for r in recs)

                    suffixes = [r.suffix.strip() for r in recs if r.suffix]
                    suffix = suffixes[0] if suffixes else 'A'

                    liters_per_bed = recs[0]._liters_per_bed
                    raw_liters = total_std_beds * liters_per_bed
                    segment_liters = round_to_tank_step(raw_liters, step=20)

                    ages = [int(round(r.real_age)) for r in recs if r.real_age is not None]
                    if ages:
                        min_a, max_a = min(ages), max(ages)
                        real_age = min_a if min_a == max_a else f"{min_a}-{max_a}"
                    else:
                        real_age = ''

                    operator = get_operator_for_zone(zone)
                    products_detail = []
                    products_summary_text_parts = []

                    for it_idx, it in items_with_idx:
                        prod = it.product
                        dose = it.dose_applied or 0.0
                        dose_unit = it.dose_unit or (prod.unit if prod else 'CC')
                        product_amount = round_product_amount(segment_liters * dose, dose_unit)
                        
                        prod_code = prod.code if prod else 'N/A'
                        comm_name = prod.commercial_name if (prod and prod.commercial_name) else prod_code
                        pest = prod.pest if prod else ''
                        ia = prod.active_ingredient if prod else ''
                        ct = prod.toxicological_category if prod else ''
                        color_info = get_toxicological_color_info(ct)

                        products_detail.append({
                            'product_id': prod.id if prod else None,
                            'product_code': prod_code,
                            'commercial_name': comm_name,
                            'dose': dose,
                            'dose_unit': dose_unit,
                            'product_amount': product_amount,
                            'pest': pest,
                            'active_ingredient': ia,
                            'toxicological_category': ct,
                            'toxicological_color': color_info['name'],
                            'color_info': color_info,
                            'order_in_mix': it_idx
                        })

                        products_summary_text_parts.append(f"{prod_code} ({dose} {dose_unit}/L)")

                        summary_key = (prod_code, dose, dose_unit)
                        if summary_key not in product_summary_map:
                            product_summary_map[summary_key] = {
                                'product_id': prod.id if prod else None,
                                'product_code': prod_code,
                                'commercial_name': comm_name,
                                'dose': dose,
                                'dose_unit': dose_unit,
                                'pest': pest,
                                'total_required_quantity': 0.0
                            }
                        product_summary_map[summary_key]['total_required_quantity'] += product_amount

                    # In fumigation work table, show clean crop name without listing all varieties
                    variety_str = crop_name.title() if crop_name else 'Hypericum'

                    segment_data = {
                        'round_number': round_obj.round_number,
                        'round_name': round_obj.name,
                        'scheduled_day': round_obj.scheduled_day,
                        'scheduled_date': round_obj.scheduled_date,
                        'operator': operator,
                        'zone': zone,
                        'block_name': clean_blk,
                        'suffix': suffix,
                        'crop_name': crop_name,
                        'variety': variety_str,
                        'phenological_stage': stage,
                        'real_age': real_age,
                        'bed_start': bed_min,
                        'bed_end': bed_max,
                        'bed_range': bed_range_str,
                        'bed_count': bed_count,
                        'standard_beds': round(total_std_beds, 2),
                        'liters_per_bed': round(liters_per_bed, 1),
                        'total_liters': segment_liters,
                        'spray_lance': cls.get_default_spray_lance(crop_name),
                        'products': [
                            {
                                'product_id': p['product_id'],
                                'product_code': p['product_code'],
                                'commercial_name': p['commercial_name'],
                                'dose': p['dose'],
                                'dose_unit': p['dose_unit'],
                                'pest': p['pest'],
                                'active_ingredient': p['active_ingredient'],
                                'toxicological_category': p['toxicological_category']
                            } for p in products_detail
                        ],
                        'products_detail': products_detail,
                        'products_summary_text': ", ".join(products_summary_text_parts),
                        'is_additional': False
                    }

                    segments.append(segment_data)
                    total_liters_all += segment_liters
                    total_std_beds_all += total_std_beds
                    total_beds_count += bed_count

        # Sort segments sequentially: ZONA -> BLOQUE (natural) -> SUFIJO -> CAMA INICIO -> CAMA FIN
        import re
        def get_seg_sort_key_db(s):
            b_name = s.get('block_name') or ''
            digits = re.findall(r'\d+', b_name)
            b_num = int(digits[0]) if digits else 99999
            return (
                s.get('zone') or '',
                b_num,
                b_name,
                s.get('suffix') or 'A',
                int(s.get('bed_start') or 0),
                int(s.get('bed_end') or 0)
            )

        segments.sort(key=get_seg_sort_key_db)

        product_summaries = list(product_summary_map.values())
        for ps in product_summaries:
            ps['total_required_quantity'] = round_product_amount(ps['total_required_quantity'], ps.get('dose_unit', 'CC'))
        product_summaries.sort(key=lambda p: p['product_code'])

        return {
            'segments': segments,
            'product_summaries': product_summaries,
            'totals': {
                'total_liters': round(total_liters_all, 1),
                'total_standard_beds': round(total_std_beds_all, 2),
                'total_segments': len(segments),
                'total_beds': total_beds_count
            },
            'warnings': list(set(warnings))
        }
