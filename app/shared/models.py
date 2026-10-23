import datetime
import json
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db
from app.shared.utils import get_operator_for_zone, get_toxicological_color_info, get_local_now

class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(30), default='USUARIO', nullable=False)  # ADMIN, USUARIO, AGRONOMO, BODEGA, EVALUADOR_FENOGRAMA, MONITOR_COSECHA, OPERARIO
    finca_id = db.Column(db.Integer, default=1, nullable=False)  # 1: Pyganflor, 2: Malchingui, 3: Urcuquí, 0: Todas
    fincas_permitidas_json = db.Column(db.Text, default='[1]', nullable=True)
    permissions_json = db.Column(db.Text, default='["fumigacion", "orden_compra", "cosechas", "fenogramas", "tpsr", "drench", "trichos", "desinfecciones", "catalogos", "importador"]')
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=get_local_now)
    updated_at = db.Column(db.DateTime, default=get_local_now, onupdate=get_local_now)

    @property
    def finca_nombre(self):
        fincas_map = {1: 'Pyganflor', 2: 'Malchingui', 3: 'Urcuquí', 0: 'Todas las Fincas'}
        return fincas_map.get(self.finca_id, 'Pyganflor')

    @property
    def allowed_finca_ids(self):
        if self.role == 'ADMIN' or self.finca_id == 0:
            return [1, 2, 3]
        try:
            ids = json.loads(self.fincas_permitidas_json or '[]')
            if not ids:
                return [self.finca_id or 1]
            return [int(x) for x in ids]
        except Exception:
            return [self.finca_id or 1]

    @property
    def permissions(self):
        try:
            return json.loads(self.permissions_json or '[]')
        except Exception:
            return []

    @permissions.setter
    def permissions(self, val):
        if isinstance(val, list):
            self.permissions_json = json.dumps([str(x).strip() for x in val])
        else:
            self.permissions_json = '[]'

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_permission(self, perm_name):
        if self.is_active is False:
            return False
        if self.role == 'ADMIN':
            return True
        perms = self.permissions
        return perm_name in perms

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'full_name': self.full_name,
            'role': self.role,
            'finca_id': self.finca_id,
            'finca_nombre': self.finca_nombre,
            'allowed_finca_ids': self.allowed_finca_ids,
            'permissions': self.permissions,
            'is_active': self.is_active
        }


class Crop(db.Model):
    __tablename__ = 'crops'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False, index=True)
    category = db.Column(db.String(50), default='PRODUCTOS_NUEVOS', nullable=False)  # 'GYPSOPHILA', 'PRODUCTOS_NUEVOS', 'PIV'
    aliases_json = db.Column(db.Text, default='[]')
    veg_min_age = db.Column(db.Integer, default=0, nullable=False)
    veg_max_age = db.Column(db.Integer, default=12, nullable=False)
    prod_min_age = db.Column(db.Integer, default=13, nullable=False)
    prod_max_age = db.Column(db.Integer, default=25, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now)
    updated_at = db.Column(db.DateTime, default=get_local_now, onupdate=get_local_now)

    @property
    def aliases(self):
        try:
            return json.loads(self.aliases_json or '[]')
        except Exception:
            return []

    @aliases.setter
    def aliases(self, val):
        if isinstance(val, list):
            self.aliases_json = json.dumps([str(x).strip() for x in val if str(x).strip()])
        elif isinstance(val, str):
            parts = [p.strip() for p in val.split(',') if p.strip()]
            self.aliases_json = json.dumps(parts)
        else:
            self.aliases_json = '[]'

    def classify_age(self, age):
        if age is None:
            return 'SIN_EDAD'
        try:
            age_val = float(age)
        except (ValueError, TypeError):
            return 'INVALIDO'
        
        if self.veg_min_age <= age_val <= self.veg_max_age:
            return 'VEGETATIVO'
        elif self.prod_min_age <= age_val <= self.prod_max_age:
            return 'PRODUCTIVO'
        else:
            return 'FUERA_DE_RANGO'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'category': self.category or 'PRODUCTOS_NUEVOS',
            'aliases': self.aliases,
            'veg_min_age': self.veg_min_age,
            'veg_max_age': self.veg_max_age,
            'prod_min_age': self.prod_min_age,
            'prod_max_age': self.prod_max_age,
            'is_active': self.is_active,
            'notes': self.notes
        }


class Product(db.Model):
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    commercial_name = db.Column(db.String(150), nullable=True)
    unit = db.Column(db.String(20), default='CC', nullable=False)
    dose_fumigation = db.Column(db.Float, nullable=True)
    dose_drench = db.Column(db.Float, nullable=True)
    dose_trichos = db.Column(db.Float, nullable=True)
    dose_solidos = db.Column(db.Float, nullable=True)
    dose_desinfecciones = db.Column(db.Float, nullable=True)
    pest = db.Column(db.String(150), nullable=True)
    active_ingredient = db.Column(db.String(200), nullable=True)
    toxicological_category = db.Column(db.String(50), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    
    # Costos, unidades y categorización de inventario / bodega (Venture)
    precio_compra = db.Column(db.Float, nullable=True)
    unidad_compra = db.Column(db.String(30), nullable=True)
    unidad_consumo = db.Column(db.String(30), nullable=True)
    factor_conversion = db.Column(db.Float, default=1.0, nullable=True)
    costo_unitario = db.Column(db.Float, nullable=True)
    bodega = db.Column(db.String(100), nullable=True)
    articulo_requisicion = db.Column(db.String(255), nullable=True)
    codigo_venture = db.Column(db.String(100), nullable=True)

    created_at = db.Column(db.DateTime, default=get_local_now)
    updated_at = db.Column(db.DateTime, default=get_local_now, onupdate=get_local_now)

    @property
    def color_info(self):
        return get_toxicological_color_info(self.toxicological_category)

    @property
    def calculated_unit_cost(self):
        if self.precio_compra is not None:
            tras = float(self.factor_conversion or 1.0)
            if tras > 0:
                return round(self.precio_compra / tras, 5)
            return round(self.precio_compra, 5)
        return round(self.costo_unitario or 0.0, 5)

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'commercial_name': self.commercial_name or self.code,
            'unit': self.unit or 'CC',
            'dose_fumigation': self.dose_fumigation,
            'dose_drench': self.dose_drench,
            'dose_trichos': self.dose_trichos,
            'dose_solidos': self.dose_solidos,
            'dose_desinfecciones': self.dose_desinfecciones,
            'pest': self.pest or '',
            'active_ingredient': self.active_ingredient or '',
            'toxicological_category': self.toxicological_category or '',
            'color': self.color_info['name'],
            'is_active': self.is_active,
            'notes': self.notes or '',
            'precio_compra': self.precio_compra,
            'unidad_compra': self.unidad_compra or '',
            'unidad_consumo': self.unidad_consumo or self.unit or 'CC',
            'factor_conversion': self.factor_conversion or 1.0,
            'costo_unitario': self.calculated_unit_cost,
            'bodega': self.bodega or 'SIN BODEGA',
            'articulo_requisicion': self.articulo_requisicion or '',
            'codigo_venture': self.codigo_venture or ''
        }


class Litraje(db.Model):
    __tablename__ = 'litrajes'
    __table_args__ = (db.UniqueConstraint('crop_name', 'age', name='uq_crop_age'),)

    id = db.Column(db.Integer, primary_key=True)
    crop_name = db.Column(db.String(100), nullable=False, index=True)
    age = db.Column(db.Integer, nullable=False, index=True)
    liters_per_bed = db.Column(db.Float, default=0.0, nullable=False)
    created_at = db.Column(db.DateTime, default=get_local_now)
    updated_at = db.Column(db.DateTime, default=get_local_now, onupdate=get_local_now)

    def to_dict(self):
        return {
            'id': self.id,
            'crop_name': self.crop_name,
            'age': self.age,
            'liters_per_bed': self.liters_per_bed
        }


class CropStateRecord(db.Model):
    __tablename__ = 'crop_state_records'

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('import_batches.id', ondelete='SET NULL'), nullable=True, index=True)
    week = db.Column(db.String(20), default='2026-33', nullable=False, index=True)  # Week of the crop state map
    block_full = db.Column(db.String(50), nullable=False, index=True)
    block_num = db.Column(db.String(20), nullable=True, index=True)
    bed_num = db.Column(db.Integer, nullable=False, index=True)
    suffix = db.Column(db.String(20), default='A', nullable=False, index=True)
    crop_master = db.Column(db.String(100), nullable=True, index=True)
    product_name = db.Column(db.String(100), nullable=True, index=True)
    variety = db.Column(db.String(100), nullable=True)
    standard_bed = db.Column(db.Float, default=1.0, nullable=False)
    zone = db.Column(db.String(100), nullable=True, index=True)
    real_age = db.Column(db.Float, nullable=True, index=True)
    status_raw = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now)

    def to_dict(self):
        return {
            'id': self.id,
            'week': self.week,
            'block_full': self.block_full,
            'block_num': self.block_num,
            'bed_num': self.bed_num,
            'suffix': self.suffix,
            'crop_master': self.crop_master,
            'product_name': self.product_name,
            'variety': self.variety,
            'standard_bed': self.standard_bed,
            'zone': self.zone,
            'real_age': self.real_age,
            'status_raw': self.status_raw
        }


class Rotation(db.Model):
    __tablename__ = 'rotations'

    id = db.Column(db.Integer, primary_key=True)
    week = db.Column(db.String(20), nullable=False, index=True)
    version = db.Column(db.Integer, default=1, nullable=False)
    title = db.Column(db.String(150), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default='BORRADOR', nullable=False)  # BORRADOR, APROBADA, ANULADA, EJECUTADA
    created_by = db.Column(db.String(100), default='Agrónomo', nullable=True)
    approved_by = db.Column(db.String(100), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    review_data_json = db.Column(db.Text, nullable=True)  # JSON with review adjustments (beds configuration) - DEPRECATED, use review_data_by_round_json
    applied_rounds_json = db.Column(db.Text, nullable=True)  # JSON array with round IDs where beds config applies - DEPRECATED
    review_data_by_round_json = db.Column(db.Text, nullable=True)  # NEW: JSON dict {round_id: [segments], ...} - cumulative adjustments per round
    confirmed_rounds_json = db.Column(db.Text, nullable=True)  # JSON with confirmed rounds status
    is_salidas_printed = db.Column(db.Boolean, default=False, nullable=False)
    salidas_printed_at = db.Column(db.DateTime, nullable=True)
    salidas_printed_by = db.Column(db.String(100), nullable=True)
    is_salidas_digitadas = db.Column(db.Boolean, default=False, nullable=False)
    salidas_digitadas_at = db.Column(db.DateTime, nullable=True)
    salidas_digitadas_by = db.Column(db.String(100), nullable=True)
    is_salidas_dispatched = db.Column(db.Boolean, default=False, nullable=False)
    salidas_dispatched_at = db.Column(db.DateTime, nullable=True)
    salidas_dispatched_by = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now)
    updated_at = db.Column(db.DateTime, default=get_local_now, onupdate=get_local_now)

    rounds = db.relationship('RotationRound', backref='rotation', cascade='all, delete-orphan', order_by='RotationRound.round_number')
    requisitions = db.relationship('Requisition', backref='rotation', cascade='all, delete-orphan')
    orders = db.relationship('FumigationOrder', backref='rotation', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'week': self.week,
            'version': self.version,
            'title': self.title or f'Rotación Semana {self.week}',
            'notes': self.notes or '',
            'status': self.status,
            'is_salidas_printed': self.is_salidas_printed,
            'salidas_printed_at': self.salidas_printed_at.strftime('%Y-%m-%d %H:%M') if self.salidas_printed_at else '',
            'salidas_printed_by': self.salidas_printed_by or '',
            'created_by': self.created_by,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
            'rounds_count': len(self.rounds)
        }


class RotationRound(db.Model):
    __tablename__ = 'rotation_rounds'

    id = db.Column(db.Integer, primary_key=True)
    rotation_id = db.Column(db.Integer, db.ForeignKey('rotations.id', ondelete='CASCADE'), nullable=False, index=True)
    round_number = db.Column(db.Integer, default=1, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    scheduled_day = db.Column(db.String(50), nullable=False)
    scheduled_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now)
    updated_at = db.Column(db.DateTime, default=get_local_now, onupdate=get_local_now)

    items = db.relationship('RotationRoundItem', backref='round', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'rotation_id': self.rotation_id,
            'round_number': self.round_number,
            'name': self.name,
            'scheduled_day': self.scheduled_day,
            'scheduled_date': self.scheduled_date.strftime('%Y-%m-%d') if self.scheduled_date else '',
            'notes': self.notes or '',
            'items': [item.to_dict() for item in self.items]
        }


class RotationRoundItem(db.Model):
    __tablename__ = 'rotation_round_items'

    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('rotation_rounds.id', ondelete='CASCADE'), nullable=False, index=True)
    crop_name = db.Column(db.String(100), nullable=False, index=True)
    phenological_stage = db.Column(db.String(50), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id', ondelete='RESTRICT'), nullable=False)
    dose_applied = db.Column(db.Float, nullable=False)
    dose_unit = db.Column(db.String(20), default='CC', nullable=False)
    order_index = db.Column(db.Integer, default=0, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now)

    product = db.relationship('Product')

    def to_dict(self):
        return {
            'id': self.id,
            'round_id': self.round_id,
            'crop_name': self.crop_name,
            'phenological_stage': self.phenological_stage,
            'product_id': self.product_id,
            'product_code': self.product.code if self.product else '',
            'commercial_name': self.product.commercial_name if self.product else '',
            'dose_applied': self.dose_applied,
            'dose_unit': self.dose_unit,
            'order_index': self.order_index,
            'notes': self.notes or ''
        }


class FumigationOrder(db.Model):
    __tablename__ = 'fumigation_orders'

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(100), unique=True, nullable=False, index=True)
    title = db.Column(db.String(255), nullable=True)  # Custom farm program name (e.g. "Programa Botrytis - Vuelta 1")
    rotation_id = db.Column(db.Integer, db.ForeignKey('rotations.id', ondelete='SET NULL'), nullable=True)
    round_id = db.Column(db.Integer, db.ForeignKey('rotation_rounds.id', ondelete='SET NULL'), nullable=True)
    week = db.Column(db.String(20), nullable=False, index=True)
    round_number = db.Column(db.Integer, default=1, nullable=False)
    round_name = db.Column(db.String(100), nullable=False)
    scheduled_day = db.Column(db.String(50), nullable=False)
    scheduled_date = db.Column(db.Date, nullable=True)
    agronomist = db.Column(db.String(100), default='Agrónomo Responsable', nullable=False)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default='GENERADA', nullable=False)  # GENERADA, APROBADA, EJECUTADA, CANCELADA
    total_liters = db.Column(db.Float, default=0.0, nullable=False)
    total_standard_beds = db.Column(db.Float, default=0.0, nullable=False)
    total_segments = db.Column(db.Integer, default=0, nullable=False)
    is_printed = db.Column(db.Boolean, default=False, nullable=False)
    printed_at = db.Column(db.DateTime, nullable=True)
    printed_by = db.Column(db.String(100), nullable=True)
    is_dispatched = db.Column(db.Boolean, default=False, nullable=False)
    dispatched_at = db.Column(db.DateTime, nullable=True)
    dispatched_by = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now)
    updated_at = db.Column(db.DateTime, default=get_local_now, onupdate=get_local_now)

    details = db.relationship('FumigationOrderDetail', backref='order', cascade='all, delete-orphan')
    products_summary = db.relationship('FumigationOrderProductSummary', backref='order', cascade='all, delete-orphan')

    @property
    def display_title(self):
        if self.title and self.title.strip():
            return self.title.strip()
        return f"{self.round_name} • {self.scheduled_day}"

    def to_dict(self):
        return {
            'id': self.id,
            'order_number': self.order_number,
            'title': self.title or '',
            'display_title': self.display_title,
            'rotation_id': self.rotation_id,
            'round_id': self.round_id,
            'week': self.week,
            'round_number': self.round_number,
            'round_name': self.round_name,
            'scheduled_day': self.scheduled_day,
            'scheduled_date': self.scheduled_date.strftime('%Y-%m-%d') if self.scheduled_date else '',
            'agronomist': self.agronomist,
            'notes': self.notes or '',
            'status': self.status,
            'is_printed': self.is_printed,
            'printed_at': self.printed_at.strftime('%Y-%m-%d %H:%M') if self.printed_at else '',
            'printed_by': self.printed_by or '',
            'total_liters': round(self.total_liters, 2),
            'total_standard_beds': round(self.total_standard_beds, 2),
            'total_segments': self.total_segments,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else ''
        }


class WarehouseDispatchLog(db.Model):
    __tablename__ = 'warehouse_dispatch_logs'

    id = db.Column(db.Integer, primary_key=True)
    week = db.Column(db.String(20), nullable=False, index=True)
    rotation_id = db.Column(db.Integer, db.ForeignKey('rotations.id', ondelete='SET NULL'), nullable=True)
    order_id = db.Column(db.Integer, db.ForeignKey('fumigation_orders.id', ondelete='SET NULL'), nullable=True)
    action = db.Column(db.String(50), nullable=False)  # 'IMPRESION_SALIDAS', 'DESPACHO_BODEGA', 'IMPRESION_ORDEN', 'EXPORTAR_EXCEL'
    performed_by = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(50), default='ASISTENTE', nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now, index=True)

    rotation = db.relationship('Rotation')
    order = db.relationship('FumigationOrder')

    def to_dict(self):
        return {
            'id': self.id,
            'week': self.week,
            'rotation_id': self.rotation_id,
            'order_id': self.order_id,
            'action': self.action,
            'performed_by': self.performed_by,
            'role': self.role,
            'notes': self.notes or '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else ''
        }


class FumigationOrderDetail(db.Model):
    __tablename__ = 'fumigation_order_details'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('fumigation_orders.id', ondelete='CASCADE'), nullable=False, index=True)
    round_number = db.Column(db.Integer, default=1, nullable=False)
    round_name = db.Column(db.String(255), default='Vuelta 1', nullable=False)
    scheduled_day = db.Column(db.String(100), default='Lunes', nullable=False)
    scheduled_date = db.Column(db.Date, nullable=True)
    operator = db.Column(db.String(255), nullable=False)
    zone = db.Column(db.String(255), nullable=True)
    block_name = db.Column(db.String(100), nullable=False)
    suffix = db.Column(db.String(50), default='A', nullable=False)
    crop_name = db.Column(db.String(255), nullable=False)
    variety_specific = db.Column(db.String(255), nullable=True)
    phenological_stage = db.Column(db.String(100), nullable=False)
    real_age = db.Column(db.String(255), default='0', nullable=True)
    standard_beds = db.Column(db.Float, default=1.0, nullable=False)
    bed_range = db.Column(db.Text, nullable=False)
    bed_count = db.Column(db.Integer, default=1, nullable=False)
    product_code = db.Column(db.String(255), nullable=False)
    commercial_name = db.Column(db.String(255), nullable=True)
    unit = db.Column(db.String(50), default='CC', nullable=False)
    dose = db.Column(db.Float, default=0.0, nullable=False)
    product_amount = db.Column(db.Float, default=0.0, nullable=False)
    total_liters = db.Column(db.Float, default=0.0, nullable=False)
    liters_per_bed = db.Column(db.Float, default=0.0, nullable=False)
    spray_lance = db.Column(db.String(100), default='Lanza de 3 salidas (C35)', nullable=True)  # Tipo de Lanza / Boquilla
    pest = db.Column(db.Text, nullable=True)
    active_ingredient = db.Column(db.Text, nullable=True)
    toxicological_category = db.Column(db.String(100), nullable=True)
    toxicological_color = db.Column(db.String(100), nullable=True)
    order_in_mix = db.Column(db.Integer, default=0, nullable=False)
    is_additional = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=get_local_now)

    @property
    def crop_category(self):
        from app.shared.utils import get_crop_category
        return get_crop_category(self.crop_name or self.variety_specific)

    def to_dict(self):
        return {
            'id': self.id,
            'round_name': self.round_name,
            'scheduled_day': self.scheduled_day,
            'operator': self.operator,
            'zone': self.zone or '',
            'block_name': self.block_name,
            'suffix': self.suffix,
            'crop_name': self.crop_name,
            'phenological_stage': self.phenological_stage,
            'real_age': self.real_age,
            'standard_beds': round(self.standard_beds, 2),
            'bed_range': self.bed_range,
            'product_code': self.product_code,
            'commercial_name': self.commercial_name or self.product_code,
            'unit': self.unit,
            'dose': self.dose,
            'product_amount': round(self.product_amount, 2),
            'total_liters': round(self.total_liters, 2),
            'liters_per_bed': round(self.liters_per_bed, 2),
            'pest': self.pest or '',
            'active_ingredient': self.active_ingredient or '',
            'toxicological_category': self.toxicological_category or '',
            'toxicological_color': self.toxicological_color or ''
        }


class FumigationOrderProductSummary(db.Model):
    __tablename__ = 'fumigation_order_product_summaries'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('fumigation_orders.id', ondelete='CASCADE'), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id', ondelete='SET NULL'), nullable=True)
    product_code = db.Column(db.String(255), nullable=False)
    commercial_name = db.Column(db.String(255), nullable=True)
    dose = db.Column(db.Float, nullable=False)
    dose_unit = db.Column(db.String(50), default='CC', nullable=False)
    total_required_quantity = db.Column(db.Float, default=0.0, nullable=False)
    pest = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now)

    def to_dict(self):
        return {
            'id': self.id,
            'product_code': self.product_code,
            'commercial_name': self.commercial_name or self.product_code,
            'dose': self.dose,
            'dose_unit': self.dose_unit,
            'total_required_quantity': round(self.total_required_quantity, 2),
            'pest': self.pest or ''
        }


class AdditionalApplication(db.Model):
    __tablename__ = 'additional_applications'

    id = db.Column(db.Integer, primary_key=True)
    week = db.Column(db.String(20), nullable=False, index=True)
    application_type = db.Column(db.String(50), default='FUMIGACION', nullable=False)
    scheduled_day = db.Column(db.String(50), default='Lunes', nullable=False)
    scheduled_date = db.Column(db.Date, nullable=True)
    zone = db.Column(db.String(100), nullable=True)
    block_name = db.Column(db.String(50), nullable=False)
    suffix = db.Column(db.String(20), default='A', nullable=False)
    crop_name = db.Column(db.String(100), nullable=False)
    bed_start = db.Column(db.Integer, default=1, nullable=False)
    bed_end = db.Column(db.Integer, default=1, nullable=False)
    standard_beds = db.Column(db.Float, default=1.0, nullable=False)
    liters_per_bed = db.Column(db.Float, default=0.0, nullable=False)
    total_liters = db.Column(db.Float, default=0.0, nullable=False)
    spray_lance = db.Column(db.String(100), default='Lanza de 3 salidas (C35)', nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id', ondelete='SET NULL'), nullable=True)
    product_code = db.Column(db.String(100), nullable=False)
    dose_applied = db.Column(db.Float, default=0.0, nullable=False)
    dose_unit = db.Column(db.String(20), default='CC', nullable=False)
    total_product = db.Column(db.Float, default=0.0, nullable=False)
    operator = db.Column(db.String(100), nullable=True)
    reason = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now)

    product = db.relationship('Product')

    def to_dict(self):
        return {
            'id': self.id,
            'week': self.week,
            'application_type': self.application_type,
            'scheduled_day': self.scheduled_day,
            'zone': self.zone or '',
            'block_name': self.block_name,
            'suffix': self.suffix,
            'crop_name': self.crop_name,
            'bed_range': f"{self.bed_start}-{self.bed_end}" if self.bed_start != self.bed_end else str(self.bed_start),
            'standard_beds': round(self.standard_beds, 2),
            'liters_per_bed': round(self.liters_per_bed, 2),
            'total_liters': round(self.total_liters, 2),
            'spray_lance': self.spray_lance or 'Lanza de 3 salidas (C35)',
            'product_code': self.product_code,
            'dose_applied': self.dose_applied,
            'dose_unit': self.dose_unit,
            'total_product': round(self.total_product, 2),
            'operator': self.operator or get_operator_for_zone(self.zone),
            'reason': self.reason or '',
            'notes': self.notes or ''
        }


class Requisition(db.Model):
    __tablename__ = 'requisitions'

    id = db.Column(db.Integer, primary_key=True)
    rotation_id = db.Column(db.Integer, db.ForeignKey('rotations.id', ondelete='SET NULL'), nullable=True)
    week = db.Column(db.String(20), nullable=False, index=True)
    title = db.Column(db.String(150), nullable=True)
    status = db.Column(db.String(30), default='PENDIENTE', nullable=False)  # PENDIENTE, APROBADO
    total_liters = db.Column(db.Float, default=0.0, nullable=False)
    budget_adjustments_json = db.Column(db.Text, nullable=True)  # JSON with budget beds by crop/age
    approved_by = db.Column(db.String(100), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now)
    updated_at = db.Column(db.DateTime, default=get_local_now, onupdate=get_local_now)

    items = db.relationship('RequisitionItem', backref='requisition', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'rotation_id': self.rotation_id,
            'week': self.week,
            'title': self.title or f"Requisición Semana {self.week}",
            'status': self.status,
            'total_liters': round(self.total_liters, 2),
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
            'items': [item.to_dict() for item in self.items]
        }


class RequisitionItem(db.Model):
    __tablename__ = 'requisition_items'

    id = db.Column(db.Integer, primary_key=True)
    requisition_id = db.Column(db.Integer, db.ForeignKey('requisitions.id', ondelete='CASCADE'), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id', ondelete='SET NULL'), nullable=True)
    product_code = db.Column(db.String(100), nullable=False)
    commercial_name = db.Column(db.String(150), nullable=True)
    average_dose = db.Column(db.Float, default=0.0, nullable=False)
    unit = db.Column(db.String(20), default='CC', nullable=False)
    quantity_forecast = db.Column(db.Float, default=0.0, nullable=False)
    quantity_final = db.Column(db.Float, default=0.0, nullable=False)
    difference = db.Column(db.Float, default=0.0, nullable=False)
    pest = db.Column(db.String(150), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'product_code': self.product_code,
            'commercial_name': self.commercial_name or self.product_code,
            'average_dose': self.average_dose,
            'unit': self.unit,
            'quantity_forecast': round(self.quantity_forecast, 2),
            'quantity_final': round(self.quantity_final, 2),
            'difference': round(self.difference, 2),
            'pest': self.pest or ''
        }


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    module = db.Column(db.String(50), nullable=False, index=True)
    action = db.Column(db.String(50), nullable=False)
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.String(50), nullable=True)
    user = db.Column(db.String(100), default='Sistema', nullable=True)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now, index=True)

    def to_dict(self):
        return {
            'id': self.id,
            'module': self.module,
            'action': self.action,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'user': self.user,
            'details': self.details,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else ''
        }


class ImportBatch(db.Model):
    __tablename__ = 'import_batches'

    id = db.Column(db.Integer, primary_key=True)
    file_type = db.Column(db.String(50), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    week = db.Column(db.String(20), default='2026-33', nullable=False)
    imported_rows = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(30), default='SUCCESS', nullable=False)
    imported_by = db.Column(db.String(100), default='Sistema', nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=get_local_now)

    def to_dict(self):
        return {
            'id': self.id,
            'file_type': self.file_type,
            'filename': self.filename,
            'week': self.week,
            'imported_rows': self.imported_rows,
            'status': self.status,
            'imported_by': self.imported_by,
            'notes': self.notes,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else ''
        }


class RegistroCosecha(db.Model):
    __tablename__ = 'registro_cosecha'

    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, nullable=False, index=True)
    viaje = db.Column(db.String(100), nullable=False)
    semana = db.Column(db.Integer, nullable=False, index=True)
    tallos = db.Column(db.Integer, nullable=False)  # 10, 15, 25, 30
    mallas = db.Column(db.Integer, nullable=False)
    modulo = db.Column(db.String(100), nullable=False, index=True)
    total_tallos = db.Column(db.Integer, nullable=False)  # tallos * mallas
    variedad = db.Column(db.String(100), nullable=False, index=True)
    hora = db.Column(db.Time, nullable=False)
    responsable = db.Column(db.String(100), nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=get_local_now)

    def get_hora_local(self):
        return self.hora

    def to_dict(self):
        return {
            'id': self.id,
            'fecha': self.fecha.strftime('%Y-%m-%d') if self.fecha else '',
            'hora': self.hora.strftime('%H:%M:%S') if self.hora else '',
            'viaje': self.viaje,
            'semana': self.semana,
            'modulo': self.modulo,
            'variedad': self.variedad,
            'tallos': self.tallos,
            'mallas': self.mallas,
            'total_tallos': self.total_tallos,
            'responsable': self.responsable,
            'fecha_creacion': self.fecha_creacion.strftime('%Y-%m-%d %H:%M:%S') if self.fecha_creacion else ''
        }

    def __repr__(self):
        return f'<RegistroCosecha {self.id} - {self.variedad} - {self.total_tallos} tallos>'


class Estimado(db.Model):
    __tablename__ = 'estimado'

    id = db.Column(db.Integer, primary_key=True)
    semana = db.Column(db.String(10), nullable=False, index=True)  # Formato AASS (ej: 2526)
    producto_maestro = db.Column(db.String(100), nullable=False, index=True)
    variedad = db.Column(db.String(100), nullable=False, index=True)
    cantidad_estimada = db.Column(db.Integer, nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=get_local_now)
    fecha_modificacion = db.Column(db.DateTime, default=get_local_now, onupdate=get_local_now)

    def to_dict(self):
        return {
            'id': self.id,
            'semana': self.semana,
            'producto_maestro': self.producto_maestro,
            'variedad': self.variedad,
            'cantidad_estimada': self.cantidad_estimada,
            'fecha_creacion': self.fecha_creacion.strftime('%Y-%m-%d %H:%M:%S') if self.fecha_creacion else '',
            'fecha_modificacion': self.fecha_modificacion.strftime('%Y-%m-%d %H:%M:%S') if self.fecha_modificacion else ''
        }

    def __repr__(self):
        return f'<Estimado {self.semana} - {self.producto_maestro} - {self.variedad}: {self.cantidad_estimada}>'
