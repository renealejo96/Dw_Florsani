from datetime import datetime
from app.extensions import db

class SemanaEvaluacion(db.Model):
    __tablename__ = 'semanas_evaluacion'

    semana_anio = db.Column(db.BigInteger, primary_key=True)
    anio = db.Column(db.BigInteger, default=2026)
    estado = db.Column(db.Text, default='ABIERTA')  # 'ABIERTA', 'CERRADA'
    fecha_inicio = db.Column(db.DateTime, nullable=True)
    fecha_fin = db.Column(db.DateTime, nullable=True)
    fecha_cierre = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'semana_anio': self.semana_anio,
            'anio': self.anio,
            'estado': self.estado,
            'fecha_cierre': self.fecha_cierre.strftime('%d/%m/%Y %H:%M') if self.fecha_cierre else None
        }


class MatrizFenograma(db.Model):
    __tablename__ = 'matriz_fenogramas'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    sheet_name = db.Column(db.Text, nullable=True)
    producto = db.Column(db.Text, nullable=False)
    actividad_tipo = db.Column(db.Text, nullable=False)  # 'Siembra', 'Poda'
    semana = db.Column(db.BigInteger, nullable=False)     # Edad en semanas
    labor = db.Column(db.Text, nullable=False)
    auxiliar = db.Column(db.Text, nullable=True)
    supervisor = db.Column(db.Text, nullable=True)
    fecha_carga = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'sheet_name': self.sheet_name,
            'producto': self.producto,
            'actividad_tipo': self.actividad_tipo,
            'semana': self.semana,
            'labor': self.labor,
            'auxiliar': self.auxiliar or '',
            'supervisor': self.supervisor or ''
        }


class PlanoCultivo(db.Model):
    __tablename__ = 'plano_cultivo'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    zona = db.Column(db.Text, nullable=True)
    bloque = db.Column(db.Text, nullable=False)
    actividad = db.Column(db.Text, nullable=True)  # 'Siembra', 'Poda'
    producto_maestro = db.Column(db.Text, nullable=True)
    cama_inicial = db.Column(db.BigInteger, nullable=False)
    cama_final = db.Column(db.BigInteger, nullable=False)
    sufijo = db.Column(db.Text, nullable=True)
    edad_real = db.Column(db.BigInteger, nullable=False)
    fecha_carga = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'zona': self.zona,
            'bloque': self.bloque,
            'actividad': self.actividad,
            'producto_maestro': self.producto_maestro,
            'cama_inicial': self.cama_inicial,
            'cama_final': self.cama_final,
            'sufijo': self.sufijo or '',
            'edad_real': self.edad_real
        }


class Calificacion(db.Model):
    __tablename__ = 'calificaciones'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    semana_anio = db.Column(db.BigInteger, nullable=False, index=True)
    anio = db.Column(db.BigInteger, default=2026)
    zona = db.Column(db.Text, nullable=True)
    bloque = db.Column(db.Text, nullable=False)
    actividad_tipo = db.Column(db.Text, nullable=True)
    producto_maestro = db.Column(db.Text, nullable=True)
    cama_inicial = db.Column(db.BigInteger, nullable=False)
    cama_final = db.Column(db.BigInteger, nullable=False)
    sufijo = db.Column(db.Text, nullable=True)
    edad_real = db.Column(db.BigInteger, nullable=False)
    labor = db.Column(db.Text, nullable=False)
    auxiliar = db.Column(db.Text, nullable=True)
    supervisor = db.Column(db.Text, nullable=True)
    cumplido = db.Column(db.Float, nullable=True)  # 1.0 = Cumplido, 0.0 = Incumplido, None = Sin calificar
    observacion = db.Column(db.Text, nullable=True)
    tiene_evaluacion = db.Column(db.Boolean, default=True)
    es_pendiente_anterior = db.Column(db.Boolean, default=False)
    semana_origen = db.Column(db.BigInteger, nullable=True)
    fecha_calificacion = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'semana_anio': self.semana_anio,
            'anio': self.anio,
            'zona': self.zona,
            'bloque': self.bloque,
            'actividad_tipo': self.actividad_tipo,
            'producto_maestro': self.producto_maestro,
            'cama_inicial': self.cama_inicial,
            'cama_final': self.cama_final,
            'sufijo': self.sufijo or '',
            'edad_real': self.edad_real,
            'labor': self.labor,
            'auxiliar': self.auxiliar or '',
            'supervisor': self.supervisor or '',
            'cumplido': int(self.cumplido) if self.cumplido is not None else None,
            'observacion': self.observacion or '',
            'tiene_evaluacion': self.tiene_evaluacion,
            'es_pendiente_anterior': self.es_pendiente_anterior,
            'semana_origen': self.semana_origen
        }
