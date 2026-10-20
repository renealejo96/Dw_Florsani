"""
Shared utility functions, local timezone helpers, number formatters,
operator zone assignment, and toxicological categorization.
"""

import datetime
from zoneinfo import ZoneInfo

# Local Timezone for Ecuador / Colombia (UTC-5)
LOCAL_TZ = ZoneInfo("America/Guayaquil")

def get_local_now() -> datetime.datetime:
    """
    Returns the current local datetime as a naive datetime in UTC-5 (e.g. 15:12:00).
    Ensures databases like SQLite and PostgreSQL store the exact local farm time.
    """
    return datetime.datetime.now(LOCAL_TZ).replace(tzinfo=None)

def format_local_datetime(dt, fmt="%Y-%m-%d %H:%M") -> str:
    """Safely formats a datetime object in local time."""
    if not dt:
        return ""
    return dt.strftime(fmt)

def is_integer_unit(unit: str) -> bool:
    """
    Returns True if the unit of measurement is counted/weighed in whole units
    without decimals (CC, G, GR, ML, PST, GRAMOS, etc.) because field scales
    cannot measure sub-decimal fractions of CC/GR.
    """
    if not unit:
        return True
    u = str(unit).strip().upper()
    return u in ['CC', 'G', 'GR', 'ML', 'PST', 'PST.', 'GRAMOS', 'CENTIMETROS', 'CM3', 'UNIDAD', 'UNIDADES', 'TAB', 'TABLETA', 'UND', 'UN']

def safe_float(val, default=0.0) -> float:
    """
    Safely converts a value (string, None, int, float, localized number with comma) to a float.
    Never raises an exception; returns `default` on error or empty string.
    """
    if val is None:
        return float(default)
    if isinstance(val, (int, float)):
        import math
        if math.isnan(val) or math.isinf(val):
            return float(default)
        return float(val)
    try:
        s = str(val).strip().replace(',', '.')
        if not s or s.lower() in ('none', 'nan', 'null', 'undefined'):
            return float(default)
        return float(s)
    except (ValueError, TypeError):
        return float(default)

def safe_int(val, default=0) -> int:
    """
    Safely converts a value to an integer (handling strings like '1.0', '1,0', None, NaN).
    Never raises an exception; returns `default` on error or empty string.
    """
    if val is None:
        return int(default)
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        import math
        if math.isnan(val) or math.isinf(val):
            return int(default)
        return int(round(val))
    try:
        s = str(val).strip().replace(',', '.')
        if not s or s.lower() in ('none', 'nan', 'null', 'undefined'):
            return int(default)
        return int(round(float(s)))
    except (ValueError, TypeError):
        return int(default)

def excel_round(val: float, decimals: int = 0) -> float:
    """
    Excel-style arithmetic half-up rounding (e.g. 214.5 -> 215, 0.05 -> 0.1).
    Matches standard Excel REDONDEAR formula.
    """
    if val is None:
        return 0.0
    from decimal import Decimal, ROUND_HALF_UP
    try:
        d = Decimal(str(val))
        if decimals == 0:
            return float(d.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        else:
            q = Decimal('10') ** -decimals
            return float(d.quantize(q, rounding=ROUND_HALF_UP))
    except Exception:
        import math
        factor = 10 ** decimals
        return math.floor(val * factor + 0.5) / factor

def round_to_tank_step(liters: float, step: int = 20) -> float:
    """
    Rounds application liters according to standard tank markings (default 20 L).
    Midpoint rule: if remainder > 10, round up to next 20; if remainder <= 10, round down to current 20.
    For example: 107 -> 100, 110 -> 100, 111 -> 120. (Minimum step if liters > 0).
    """
    if not liters or liters <= 0:
        return 0.0
    import math
    val = float(liters)
    rem = val % step
    base = math.floor(val / step) * step
    if rem > (step / 2):
        return float(base + step)
    else:
        return float(max(step, base) if val > 0 else 0.0)

def format_product_amount(amount: float, unit: str) -> str:
    """
    Business Rule for Product Quantities:
    - CC, G, GR, ML, PST: 0 decimals (integers, e.g. 215 CC, 25 GR - Excel REDONDEAR)
    - LT, KG, L, K: 1 decimal (e.g. 2.5 LT, 1.2 KG)
    """
    if amount is None:
        return "0"
    val = safe_float(amount, default=0.0)

    if is_integer_unit(unit):
        return f"{int(excel_round(val, 0)):,}"
    else:
        u = str(unit or '').strip().upper()
        if u in ['LT', 'KG', 'L', 'K', 'LITROS', 'KILOS']:
            return f"{val:.1f}"
        else:
            r = excel_round(val, 1)
            return f"{int(r) if r == int(r) else r:,}"

def round_product_amount(amount: float, unit: str) -> float:
    """
    Rounds product amount according to farm measurement capabilities (Excel REDONDEAR):
    - CC, G, GR, ML, PST: exact integer (0 decimals, half-up, e.g. 214.5 -> 215)
    - LT, KG, L, K: 1 decimal (or 2 if smaller)
    """
    if amount is None:
        return 0.0
    val = safe_float(amount, default=0.0)

    if is_integer_unit(unit):
        return float(int(excel_round(val, 0)))
    else:
        return excel_round(val, 2)

def is_liquid_unit(unit: str) -> bool:
    """
    Returns True if the unit represents liquid volumes (CC, ML, LT, L, LITROS, CENTIMETROS, CM3).
    """
    if not unit:
        return True
    u = str(unit).strip().upper()
    return u in ['CC', 'ML', 'LT', 'L', 'LITROS', 'CENTIMETROS', 'CM3', 'C.C.', 'CC.']

def is_solid_unit(unit: str) -> bool:
    """
    Returns True if the unit represents solid weights or item units (G, GR, GRAMOS, KG, K, KILOS, PST, PST., TAB, TABLETA, UNIDAD, UND).
    """
    if not unit:
        return False
    u = str(unit).strip().upper()
    return u in ['G', 'GR', 'GRAMOS', 'KG', 'K', 'KILOS', 'PST', 'PST.', 'TAB', 'TABLETA', 'UNIDAD', 'UNIDADES', 'UND', 'UN']

def format_age(age) -> str:
    """Formats crop real age with 0 decimals (integer weeks)."""
    if age is None or age == '':
        return "-"
    try:
        return str(int(round(float(age))))
    except (ValueError, TypeError):
        return str(age)

# Exact Zone -> Operator Mapping:
# ZONA 1, 2 Y 5: RICHARD CAÑAR
# ZONA 4, STA TERESA 1, 2, 3, 4: NELSON PIEDRA
# PALMERAS 1, 2, 3: LUIS BAUTISTA
# ZONA 3: JORGE MEDINA
# VIRGINIA: CRISTOBAL GOMEZ
def get_operator_for_zone(zone_name: str) -> str:
    if not zone_name:
        return "RICHARD CAÑAR"
    z = zone_name.strip().upper().replace(" ", "")
    
    if any(k in z for k in ["ZONA1", "ZONA2", "ZONA5", "Z1", "Z2", "Z5"]):
        return "RICHARD CAÑAR"
    elif any(k in z for k in ["ZONA4", "Z4", "STATERESA", "SANTA TERESA", "TERESA"]):
        return "NELSON PIEDRA"
    elif any(k in z for k in ["PALMERA", "PALMERAS"]):
        return "LUIS BAUTISTA"
    elif any(k in z for k in ["ZONA3", "Z3"]):
        return "JORGE MEDINA"
    elif any(k in z for k in ["VIRGINIA"]):
        return "CRISTOBAL GOMEZ"
    else:
        return "RICHARD CAÑAR"

def get_toxicological_color_info(cat: str) -> dict:
    """
    Returns toxicological color info:
    I -> ROJO
    II -> AMARILLO
    III -> AZUL
    IV -> VERDE
    N/A or empty -> VACÍO
    """
    if not cat:
        return {'name': '', 'bg': 'transparent', 'text': '#000000', 'border': '#cccccc', 'label': ''}
    c = str(cat).strip().upper()
    if c in ['I', '1', 'IA', 'IB']:
        return {'name': 'ROJO', 'bg': '#ef4444', 'text': '#ffffff', 'border': '#b91c1c', 'label': 'I - Rojo'}
    elif c in ['II', '2']:
        return {'name': 'AMARILLO', 'bg': '#eab308', 'text': '#000000', 'border': '#ca8a04', 'label': 'II - Amarillo'}
    elif c in ['III', '3']:
        return {'name': 'AZUL', 'bg': '#3b82f6', 'text': '#ffffff', 'border': '#1d4ed8', 'label': 'III - Azul'}
    elif c in ['IV', '4']:
        return {'name': 'VERDE', 'bg': '#22c55e', 'text': '#ffffff', 'border': '#15803d', 'label': 'IV - Verde'}
    else:
        return {'name': '', 'bg': 'transparent', 'text': '#000000', 'border': '#cccccc', 'label': c}

def get_crop_category(crop_name: str, crop_obj=None) -> str:
    """
    Returns the farm operational printing and warehouse weighing group:
    - 'GYPSOPHILA': Gypsophila, Xlence, Billion Lights, etc.
    - 'PRODUCTOS_NUEVOS': Variedades / Productos Nuevos (Solidago, Verónica, Hypericum, Sunflower, Ruscus, Rumex, etc.)
    - 'PIV': Propagación / Invernadero Vegetativo / PIV
    """
    if crop_obj and getattr(crop_obj, 'category', None):
        return crop_obj.category

    if not crop_name:
        return 'PRODUCTOS_NUEVOS'

    cn = str(crop_name).strip().upper()

    # Dynamic lookup against database Crop catalogue (with request-level cache)
    try:
        from flask import has_app_context, g
        if has_app_context():
            crop_cat_cache = getattr(g, '_crop_category_cache', None)
            if crop_cat_cache is None:
                crop_cat_cache = {}
                from app.shared.models import Crop
                crops = Crop.query.all()
                for c in crops:
                    cat = c.category
                    if not cat or 'GYPSO' in (c.name or '').upper():
                        cat = 'GYPSOPHILA' if ('GYPSO' in (c.name or '').upper()) else (c.category or 'PRODUCTOS_NUEVOS')
                    if c.name:
                        c_upper = c.name.strip().upper()
                        crop_cat_cache[c_upper] = cat
                        # Handle Spanish/Latin spelling i/y (e.g. Lisimachia / Lysimachia)
                        crop_cat_cache[c_upper.replace('Y', 'I')] = cat
                        crop_cat_cache[c_upper.replace('I', 'Y')] = cat
                    if c.aliases:
                        for a in c.aliases:
                            if a:
                                a_upper = str(a).strip().upper()
                                crop_cat_cache[a_upper] = cat
                                crop_cat_cache[a_upper.replace('Y', 'I')] = cat
                                crop_cat_cache[a_upper.replace('I', 'Y')] = cat
                g._crop_category_cache = crop_cat_cache

            if cn in crop_cat_cache:
                return crop_cat_cache[cn]
            cn_i = cn.replace('Y', 'I')
            if cn_i in crop_cat_cache:
                return crop_cat_cache[cn_i]
            # Partial substring match in cache
            for k, cat in crop_cat_cache.items():
                if k in cn or cn in k:
                    return cat
    except Exception:
        pass

    # Heuristic fallback if DB is unreachable
    if any(k in cn for k in ['GYPSO', 'GYPSOPHILA', 'XLENCE', 'BILLION']):
        return 'GYPSOPHILA'
    elif any(k in cn for k in ['PIV', 'PROPAGACION', 'PROPAGACIÓN', 'ENRAIZ', 'INVERNADERO']):
        return 'PIV'
    else:
        return 'PRODUCTOS_NUEVOS'

def get_week_difference(target_week_str: str, base_week_str: str) -> int:
    """
    Computes difference in weeks: target_week - base_week.
    Supports formats like '2026-40', '2640', '2026-39', '2639'.
    Uses dim_calendario_agricola if available, with robust calendar arithmetic fallback.
    """
    if not target_week_str or not base_week_str:
        return 0
    t_clean = str(target_week_str).strip()
    b_clean = str(base_week_str).strip()
    if t_clean == b_clean:
        return 0

    def parse_sc(w_str):
        s = str(w_str).strip().replace('-', '')
        if len(s) == 6 and s.startswith('20'):
            return int(s[2:])
        elif len(s) == 4 and s.isdigit():
            return int(s)
        return None

    sc_target = parse_sc(t_clean)
    sc_base = parse_sc(b_clean)

    # 1. Try DB lookup in dim_calendario_agricola
    try:
        from app.extensions import db
        from sqlalchemy import text
        if sc_target and sc_base and db.engine:
            with db.engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT semana_campo, semana_abs FROM dim_calendario_agricola WHERE semana_campo IN (:st, :sb)"),
                    {'st': sc_target, 'sb': sc_base}
                ).fetchall()
                row_map = {r[0]: r[1] for r in rows}
                if sc_target in row_map and sc_base in row_map:
                    return int(row_map[sc_target]) - int(row_map[sc_base])
    except Exception:
        pass

    # 2. Fallback calendar arithmetic
    def parse_yw(w_str):
        s = str(w_str).strip()
        if '-' in s:
            p = s.split('-')
            return int(p[0]), int(p[1])
        if len(s) == 4 and s.isdigit():
            return 2000 + int(s[:2]), int(s[2:])
        if len(s) == 6 and s.isdigit():
            return int(s[:4]), int(s[4:])
        return None, None

    y_t, w_t = parse_yw(t_clean)
    y_b, w_b = parse_yw(b_clean)
    if y_t is not None and y_b is not None:
        return (y_t - y_b) * 52 + (w_t - w_b)

    return 0

_DIM_BLOQUES_CACHE = None
_DIM_BLOQUES_CACHE_TIME = 0

def get_valid_blocks():
    global _DIM_BLOQUES_CACHE, _DIM_BLOQUES_CACHE_TIME
    import time
    now = time.time()
    if _DIM_BLOQUES_CACHE is None or (now - _DIM_BLOQUES_CACHE_TIME > 300):
        try:
            from app.extensions import db
            from sqlalchemy import text
            if db.engine:
                with db.engine.connect() as conn:
                    rows = conn.execute(text("SELECT DISTINCT blq FROM dim_bloques")).fetchall()
                    _DIM_BLOQUES_CACHE = set(r[0] for r in rows if r[0])
                    _DIM_BLOQUES_CACHE_TIME = now
        except Exception:
            if _DIM_BLOQUES_CACHE is None:
                _DIM_BLOQUES_CACHE = set()
    return _DIM_BLOQUES_CACHE or set()

def clean_block_name(block_name: str, suffix: str = None) -> str:
    """
    Cleans block name by removing duplicate suffix if present at the end of block_full.
    Preserves authoritative base blocks from dim_bloques (e.g. '51A', '38C', '11A').
    Example: '103A' with suffix 'A' -> '103'.
             '38CA' with suffix 'A' -> '38C'.
             '11AA' with suffix 'A' -> '11A'.
             '51A' with suffix 'A' -> '51A' (since '51A' is an independent physical block).
             '51' with suffix 'A' -> '51'.
    """
    if not block_name:
        return ''
    b = str(block_name).strip()
    valid_blocks = get_valid_blocks()
    if b in valid_blocks:
        return b
    if suffix:
        suf = str(suffix).strip()
        if suf and len(b) > len(suf) and b.endswith(suf):
            candidate = b[:-len(suf)]
            if candidate in valid_blocks:
                return candidate
            if any(ch.isdigit() for ch in candidate):
                return candidate
    return b


