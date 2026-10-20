import os
import csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

try:
    from zoneinfo import ZoneInfo
    ZONA_HORARIA = ZoneInfo('America/Guayaquil')
except Exception:
    try:
        import pytz
        ZONA_HORARIA = pytz.timezone('America/Guayaquil')
    except Exception:
        ZONA_HORARIA = timezone(timedelta(hours=-5))

# Directorio de datos de cosechas (dw_Florsani raíz)
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = BASE_DIR / 'data' / 'cosechas'
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _get_csv_path(filename):
    """Retorna la ruta del archivo CSV en data/cosechas/ o en la raíz si existe"""
    target = DATA_DIR / filename
    if target.exists():
        return target
    # Fallbacks de compatibilidad
    fallback_root = BASE_DIR / filename
    if fallback_root.exists():
        return fallback_root
    return target

def formato_semana(fecha):
    """Convertir fecha (date o datetime) a formato AA-SS (ej: 2640)"""
    if isinstance(fecha, str):
        try:
            fecha = datetime.strptime(fecha[:10], '%Y-%m-%d').date()
        except Exception:
            return fecha
    iso_year = fecha.isocalendar()[0]
    year = iso_year % 100
    semana = fecha.isocalendar()[1]
    return f"{year:02d}{semana:02d}"

def cargar_variedades():
    """Cargar variedades desde CSV"""
    variedades = []
    csv_path = _get_csv_path('variedades.csv')
    try:
        if csv_path.exists():
            with open(csv_path, 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    val = (row.get('variedad') or '').strip()
                    if val:
                        variedades.append(val)
        else:
            variedades = ['Gold', 'Red Naomi', 'Explorer', 'Freedom', 'High & Magic']
    except Exception as e:
        print(f"Error cargando variedades: {e}")
        variedades = ['Gold', 'Red Naomi', 'Explorer', 'Freedom', 'High & Magic']
    
    return sorted(list(set(variedades)), key=lambda x: x.upper())

def guardar_variedades(variedades):
    """Guardar variedades únicas en CSV"""
    variedades_limpias = sorted({(v or '').strip() for v in variedades if (v or '').strip()}, key=lambda value: value.upper())
    csv_path = DATA_DIR / 'variedades.csv'
    with open(csv_path, 'w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['variedad'])
        for variedad in variedades_limpias:
            writer.writerow([variedad])

def cargar_responsables():
    """Cargar lista unificada de responsables (Usuarios activos del sistema ERP + CSV histórico)"""
    responsables = set()
    
    # 1. Cargar usuarios del sistema con sesión/login
    try:
        from app.shared.models import User
        usuarios_activos = User.query.filter_by(is_active=True).all()
        for u in usuarios_activos:
            nombre = (u.full_name or u.username).strip()
            if nombre:
                responsables.add(nombre)
    except Exception:
        # En caso de ejecutarse fuera del contexto de Flask o antes de inicializar la BD
        pass

    # 2. Cargar archivo CSV histórico de responsables
    csv_path = _get_csv_path('responsables.csv')
    try:
        if csv_path.exists():
            with open(csv_path, 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    val = (row.get('responsable') or '').strip()
                    if val:
                        responsables.add(val)
    except Exception as e:
        print(f"Error cargando responsables CSV: {e}")

    # 3. Fallback de cortesía si está vacío
    if not responsables:
        responsables = {'Kevin Martinez', 'Maria Rodriguez', 'Juan Perez', 'Ana Garcia'}
    
    return sorted(list(responsables), key=lambda x: x.upper())

def obtener_monitores_sistema():
    """
    Retorna lista detallada con información de monitores y usuarios del sistema
    para desplegar agrupado en la interfaz de nuevo registro y edición.
    """
    monitores = []
    nombres_agregados = set()

    # 1. Cargar usuarios del sistema (User)
    try:
        from app.shared.models import User
        usuarios = User.query.filter_by(is_active=True).order_by(User.full_name.asc()).all()
        for u in usuarios:
            nombre = (u.full_name or u.username).strip()
            es_cosecha = u.has_permission('cosechas') or u.role in ['MONITOR_COSECHA', 'COSECHA', 'AGRONOMO', 'ADMIN']
            monitores.append({
                'id': u.id,
                'nombre': nombre,
                'username': u.username,
                'role': u.role,
                'es_sistema': True,
                'es_cosecha': es_cosecha
            })
            nombres_agregados.add(nombre.lower())
    except Exception:
        pass

    # 2. Agregar responsables históricos de CSV que no tengan cuenta de sistema
    csv_path = _get_csv_path('responsables.csv')
    try:
        if csv_path.exists():
            with open(csv_path, 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    val = (row.get('responsable') or '').strip()
                    if val and val.lower() not in nombres_agregados:
                        monitores.append({
                            'id': None,
                            'nombre': val,
                            'username': '',
                            'role': 'HISTORICO',
                            'es_sistema': False,
                            'es_cosecha': False
                        })
                        nombres_agregados.add(val.lower())
    except Exception:
        pass

    return monitores

import re

def _natural_key(s):
    return [int(text) if text.isdigit() else text.upper() for text in re.split(r'(\d+)', str(s))]

def limpiar_modulo_str(m):
    if not m:
        return ''
    val = re.sub(r'^(mod\.?|modulo|bloque|blq\.?)\s*', '', str(m).strip(), flags=re.IGNORECASE)
    return val.strip()

def cargar_modulos():
    """Cargar módulos/bloques de cosecha limpios y ordenados naturalmente (ej: 4, 11, 12... 150)"""
    modulos = set()
    csv_path = _get_csv_path('modulos.csv')
    try:
        if csv_path.exists():
            with open(csv_path, 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    val = (row.get('modulo') or '').strip()
                    val_clean = limpiar_modulo_str(val)
                    if val_clean:
                        modulos.add(val_clean)
        else:
            modulos = {'4', '11', '12', '13', '14', '15', '16', '21', '22', '71', '89', '100'}
    except Exception as e:
        print(f"Error cargando módulos: {e}")
        modulos = {'4', '11', '12', '13', '14', '15', '16', '21', '22', '71', '89', '100'}
    
    return sorted(list(modulos), key=_natural_key)

def cargar_maestro_productos():
    """Cargar mapeo {variedad: producto_maestro} desde CSV"""
    mapeo = {}
    csv_path = _get_csv_path('MAESTRO_PROS.csv')
    try:
        if csv_path.exists():
            with open(csv_path, 'r', encoding='utf-8-sig') as file:
                content = file.read()
                lines = content.strip().split('\n')
                if len(lines) > 1:
                    for line in lines[1:]:
                        if ';' in line:
                            partes = line.strip().split(';')
                            if len(partes) >= 2:
                                variedad = partes[0].strip()
                                prod_maestro = partes[1].strip()
                                if variedad and prod_maestro:
                                    mapeo[variedad] = prod_maestro
    except Exception as e:
        print(f"Error cargando maestro productos: {e}")
    
    return mapeo

def cargar_relaciones_maestro():
    """Cargar relaciones variedad-producto maestro como lista de tuplas únicas"""
    relaciones = []
    vistos = set()
    csv_path = _get_csv_path('MAESTRO_PROS.csv')

    try:
        if csv_path.exists():
            with open(csv_path, 'r', encoding='utf-8-sig') as file:
                content = file.read()
                lines = content.strip().split('\n')
                if len(lines) > 1:
                    for line in lines[1:]:
                        if ';' not in line:
                            continue
                        partes = line.strip().split(';')
                        if len(partes) < 2:
                            continue
                        variedad = partes[0].strip()
                        producto = partes[1].strip()
                        if not variedad or not producto:
                            continue
                        clave = (variedad.upper(), producto.upper())
                        if clave in vistos:
                            continue
                        vistos.add(clave)
                        relaciones.append((variedad, producto))
    except Exception as e:
        print(f"Error cargando relaciones maestro: {e}")

    relaciones.sort(key=lambda item: (item[1].upper(), item[0].upper()))
    return relaciones

def guardar_relaciones_maestro(relaciones):
    """Guardar relaciones variedad-producto maestro en CSV"""
    relaciones_unicas = {}
    for variedad, producto in relaciones:
        variedad_limpia = (variedad or '').strip()
        producto_limpio = (producto or '').strip()
        if variedad_limpia and producto_limpio:
            relaciones_unicas[variedad_limpia.upper()] = (variedad_limpia, producto_limpio)

    relaciones_ordenadas = sorted(relaciones_unicas.values(), key=lambda item: (item[1].upper(), item[0].upper()))
    csv_path = DATA_DIR / 'MAESTRO_PROS.csv'
    with open(csv_path, 'w', encoding='utf-8', newline='') as file:
        file.write('variedad;prod_maestro\n')
        for variedad, producto in relaciones_ordenadas:
            file.write(f'{variedad};{producto}\n')

def cargar_productos_maestros():
    """Cargar productos maestros desde CSV dedicado o derivarlos del maestro"""
    productos = set()
    productos_csv = _get_csv_path('productos_maestros.csv')

    try:
        if productos_csv.exists():
            with open(productos_csv, 'r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    producto = (row.get('producto_maestro') or '').strip()
                    if producto:
                        productos.add(producto)
    except Exception as e:
        print(f"Error cargando productos maestros: {e}")

    if not productos:
        for _, producto in cargar_relaciones_maestro():
            productos.add(producto)

    return sorted(productos, key=lambda value: value.upper())

def guardar_productos_maestros(productos):
    """Guardar productos maestros únicos en CSV"""
    productos_limpios = sorted({(p or '').strip() for p in productos if (p or '').strip()}, key=lambda value: value.upper())
    csv_path = DATA_DIR / 'productos_maestros.csv'
    with open(csv_path, 'w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['producto_maestro'])
        for producto in productos_limpios:
            writer.writerow([producto])

def construir_productos_con_variedades():
    """Construir estructura {producto_maestro: [variedades]}"""
    productos_con_variedades = defaultdict(list)
    for variedad, producto in cargar_relaciones_maestro():
        productos_con_variedades[producto].append(variedad)

    for producto in productos_con_variedades:
        productos_con_variedades[producto] = sorted(set(productos_con_variedades[producto]), key=lambda value: value.upper())

    productos_solos = cargar_productos_maestros()
    for producto in productos_solos:
        productos_con_variedades[producto] = productos_con_variedades.get(producto, [])

    return dict(sorted(productos_con_variedades.items(), key=lambda item: item[0].upper()))
