from datetime import datetime, date
from app.extensions import db

def calcular_semana_formato(fecha=None):
    if not fecha:
        fecha = date.today()
    elif isinstance(fecha, str):
        try:
            fecha = datetime.strptime(fecha, "%Y-%m-%d").date()
        except Exception:
            fecha = date.today()
    return f"{str(fecha.year)[2:]}{fecha.isocalendar()[1]:02d}"

class Tecnico(db.Model):
    __tablename__ = 'tecnicos'
    
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), unique=True, nullable=False)
    activo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "nombre": self.nombre,
            "activo": self.activo
        }

class TipoAplicacion(db.Model):
    __tablename__ = 'tipos_aplicacion'
    
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    codigo = db.Column(db.String(50), unique=True, nullable=False)
    icono = db.Column(db.String(50), default="check-circle")
    activo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "nombre": self.nombre,
            "codigo": self.codigo,
            "icono": self.icono,
            "activo": self.activo
        }

class VariedadFerti(db.Model):
    __tablename__ = 'variedades'
    
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), unique=True, nullable=False)
    activo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "nombre": self.nombre,
            "activo": self.activo
        }

class TecnicoBloque(db.Model):
    __tablename__ = 'tecnico_bloques'
    
    id = db.Column(db.Integer, primary_key=True)
    tecnico_nombre = db.Column(db.String(100), nullable=False, index=True)
    bloque = db.Column(db.String(50), nullable=False, index=True)
    cantidad_camas = db.Column(db.Float, default=0.0)
    cama_estandar = db.Column(db.Float, default=0.0)
    camas_fisicas = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "tecnico_nombre": self.tecnico_nombre,
            "bloque": self.bloque,
            "cantidad_camas": round(float(self.cantidad_camas or 0.0), 2),
            "cama_estandar": round(float(self.cama_estandar or 0.0), 2),
            "camas_fisicas": int(self.camas_fisicas or 0)
        }

class DrenchItem(db.Model):
    __tablename__ = 'drench_items'
    
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, default=date.today, index=True)
    semana = db.Column(db.String(10), default=calcular_semana_formato, index=True)
    clasificacion = db.Column(db.String(50), nullable=False, index=True)
    actividad = db.Column(db.String(50), nullable=False)
    bomba = db.Column(db.Text, nullable=False)
    bloque = db.Column(db.String(50), nullable=False, index=True)
    cultivo = db.Column(db.String(50), nullable=False)
    cama_inicial = db.Column(db.Integer, nullable=False)
    cama_final = db.Column(db.Integer, nullable=False)
    cama_estandar = db.Column(db.Float, default=0.0)
    edad = db.Column(db.Integer, nullable=False)
    cantidad_camas = db.Column(db.Integer, default=0)
    grupo_id = db.Column(db.Integer, default=1)
    segmento_edades = db.Column(db.String(50))
    
    # Checklist tracking
    completado = db.Column(db.Boolean, default=False, index=True)
    aplicador = db.Column(db.String(100), nullable=True)
    tecnico = db.Column(db.String(100), nullable=True)
    fecha_completado = db.Column(db.DateTime, nullable=True)
    observaciones = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "fecha": self.fecha.strftime("%Y-%m-%d") if self.fecha else "",
            "semana": self.semana or calcular_semana_formato(self.fecha),
            "clasificacion": self.clasificacion,
            "actividad": self.actividad,
            "bomba": self.bomba,
            "bloque": self.bloque,
            "cultivo": self.cultivo,
            "cama_inicial": self.cama_inicial,
            "cama_final": self.cama_final,
            "cama_estandar": round(float(self.cama_estandar or 0.0), 2),
            "edad": self.edad,
            "cantidad_camas": self.cantidad_camas,
            "completado": self.completado,
            "aplicador": self.aplicador or "",
            "tecnico": self.tecnico or "",
            "fecha_completado": self.fecha_completado.strftime("%Y-%m-%d %H:%M") if self.fecha_completado else "",
            "observaciones": self.observaciones or ""
        }

class RiegoDuchaRegistro(db.Model):
    __tablename__ = 'riego_ducha_registros'
    
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, default=date.today, index=True)
    semana = db.Column(db.String(10), default=calcular_semana_formato, index=True)
    aplicador = db.Column(db.String(100), nullable=False)
    tecnico = db.Column(db.String(100), nullable=False)
    bloque = db.Column(db.String(50), nullable=False)
    pases = db.Column(db.Integer, nullable=False)  # 1 to 5
    tipo_cobertura = db.Column(db.String(50), nullable=False)  # 'bloque_completo' o 'camas_especificas'
    cama_inicial = db.Column(db.Integer, nullable=True)
    cama_final = db.Column(db.Integer, nullable=True)
    camas_fisicas = db.Column(db.Integer, nullable=False)
    completado = db.Column(db.Boolean, default=True)
    fecha_completado = db.Column(db.DateTime, default=datetime.utcnow)
    observaciones = db.Column(db.Text, nullable=True)
    
    # Medición de Rendimientos
    hora_inicio = db.Column(db.DateTime, nullable=True)
    hora_fin = db.Column(db.DateTime, nullable=True)
    duracion_minutos = db.Column(db.Float, default=0.0)
    rendimiento_min_cama = db.Column(db.Float, default=0.0)

    def to_dict(self):
        duracion = round(float(self.duracion_minutos or 0.0), 1)
        rend = round(float(self.rendimiento_min_cama or 0.0), 2)
        camas_hora = round((self.camas_fisicas / duracion * 60), 1) if duracion > 0 else 0.0

        return {
            "id": self.id,
            "fecha": self.fecha.strftime("%Y-%m-%d") if self.fecha else "",
            "semana": self.semana or calcular_semana_formato(self.fecha),
            "aplicador": self.aplicador,
            "tecnico": self.tecnico,
            "bloque": self.bloque,
            "pases": self.pases,
            "tipo_cobertura": self.tipo_cobertura,
            "cama_inicial": self.cama_inicial,
            "cama_final": self.cama_final,
            "camas_fisicas": self.camas_fisicas,
            "completado": self.completado,
            "fecha_completado": self.fecha_completado.strftime("%Y-%m-%d %H:%M") if self.fecha_completado else "",
            "hora_inicio": self.hora_inicio.strftime("%H:%M:%S") if self.hora_inicio else "",
            "hora_fin": self.hora_fin.strftime("%H:%M:%S") if self.hora_fin else "",
            "duracion_minutos": duracion,
            "rendimiento_min_cama": rend,
            "camas_hora": camas_hora,
            "observaciones": self.observaciones or ""
        }

class ValvulaRegistro(db.Model):
    __tablename__ = 'valvulas_registros'
    
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, default=date.today, index=True)
    semana = db.Column(db.String(10), default=calcular_semana_formato, index=True)
    aplicador = db.Column(db.String(100), nullable=False)
    tecnico = db.Column(db.String(100), nullable=False)
    variedad = db.Column(db.String(50), nullable=False)
    cantidad_valvulas = db.Column(db.Integer, nullable=False)
    minutos_riego = db.Column(db.Integer, nullable=False)
    completado = db.Column(db.Boolean, default=True)
    fecha_completado = db.Column(db.DateTime, default=datetime.utcnow)
    observaciones = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "fecha": self.fecha.strftime("%Y-%m-%d") if self.fecha else "",
            "semana": self.semana or calcular_semana_formato(self.fecha),
            "aplicador": self.aplicador,
            "tecnico": self.tecnico,
            "variedad": self.variedad,
            "cantidad_valvulas": self.cantidad_valvulas,
            "minutos_riego": self.minutos_riego,
            "completado": self.completado,
            "fecha_completado": self.fecha_completado.strftime("%Y-%m-%d %H:%M") if self.fecha_completado else "",
            "observaciones": self.observaciones or ""
        }

class UsuarioAplicador(db.Model):
    __tablename__ = 'usuarios_aplicadores'
    
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), unique=True, nullable=False)
    rol = db.Column(db.String(50), default="aplicador")
    activo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "nombre": self.nombre,
            "rol": self.rol,
            "activo": self.activo
        }
