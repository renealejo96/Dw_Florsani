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

def _get_tombstones_set():
    """Retorna el conjunto de variedades registradas en cat_variedades_eliminadas"""
    tombstones = set()
    try:
        from app.extensions import db
        from sqlalchemy import text
        rows = db.session.execute(text("SELECT UPPER(TRIM(variedad)) FROM cat_variedades_eliminadas WHERE variedad IS NOT NULL")).fetchall()
        for r in rows:
            if r[0]:
                tombstones.add(r[0].strip().upper())
    except Exception:
        try:
            from app.extensions import db
            db.session.rollback()
        except Exception:
            pass
    return tombstones

def cargar_variedades():
    """Cargar variedades activas autorizadas desde cat_variedades_maestro (fuente principal autorizada sin resucitar eliminadas)"""
    variedades = set()
    db_count = 0
    tombstones = _get_tombstones_set()
    
    # 1. Base de datos: cat_variedades_maestro (fuente principal autorizada)
    try:
        from app.extensions import db
        from sqlalchemy import text
        vars_cat = db.session.execute(text("SELECT DISTINCT variedad FROM cat_variedades_maestro WHERE variedad IS NOT NULL AND TRIM(variedad) != '';")).fetchall()
        for v in vars_cat:
            val = (v[0] or '').strip().upper()
            if val and val not in tombstones:
                variedades.add(val)
                db_count += 1
    except Exception:
        pass

    # 2. Archivo CSV variedades.csv solo si la BD no tiene registros (instalación inicial)
    if db_count == 0:
        csv_path = _get_csv_path('variedades.csv')
        try:
            if csv_path.exists():
                with open(csv_path, 'r', encoding='utf-8') as file:
                    reader = csv.DictReader(file)
                    for row in reader:
                        val = (row.get('variedad') or '').strip().upper()
                        if val and val not in tombstones:
                            variedades.add(val)
        except Exception as e:
            print(f"Error cargando variedades desde CSV: {e}")

    if not variedades:
        variedades = {'GOLD', 'RED NAOMI', 'EXPLORER', 'FREEDOM', 'HIGH & MAGIC'}
    
    return sorted(list(variedades), key=lambda x: x.upper())

def guardar_variedades(variedades):
    """Guardar variedades únicas en CSV y sincronizar en PostgreSQL (dim_variedades, dim_productos, variedades)"""
    variedades_limpias = sorted({(v or '').strip().upper() for v in variedades if (v or '').strip()}, key=lambda value: value.upper())
    csv_path = DATA_DIR / 'variedades.csv'
    try:
        with open(csv_path, 'w', encoding='utf-8', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['variedad'])
            for variedad in variedades_limpias:
                writer.writerow([variedad])
    except Exception as e:
        print(f"Error escribiendo variedades.csv: {e}")

    # Sincronizar en Base de Datos PostgreSQL
    try:
        from app.extensions import db
        from sqlalchemy import text
        for v_clean in variedades_limpias:
            try:
                ex_v = db.session.execute(text("SELECT 1 FROM dim_variedades WHERE UPPER(variedad) = :v"), {'v': v_clean.upper()}).scalar()
                if not ex_v:
                    max_vid = db.session.execute(text("SELECT COALESCE(MAX(id_producto), 0) + 1 FROM dim_variedades")).scalar()
                    db.session.execute(text("INSERT INTO dim_variedades (id_producto, variedad) VALUES (:id, :v)"), {'id': max_vid, 'v': v_clean.upper()})
                
                ex_dp = db.session.execute(text("SELECT 1 FROM dim_productos WHERE UPPER(variedad) = :v"), {'v': v_clean.upper()}).scalar()
                if not ex_dp:
                    max_pid = db.session.execute(text("SELECT COALESCE(MAX(id_producto), 0) + 1 FROM dim_productos")).scalar()
                    db.session.execute(text("INSERT INTO dim_productos (id_producto, variedad) VALUES (:id, :v)"), {'id': max_pid, 'v': v_clean.upper()})
                
                ex_var = db.session.execute(text("SELECT 1 FROM variedades WHERE UPPER(nombre) = :v"), {'v': v_clean.upper()}).scalar()
                if not ex_var:
                    db.session.execute(text("INSERT INTO variedades (nombre, activo, created_at) VALUES (:v, true, NOW())"), {'v': v_clean.upper()})
            except Exception:
                pass
        db.session.commit()
    except Exception as e:
        print(f"Error persistiendo variedades a BD: {e}")

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

def sincronizar_variedad_a_bd(variedad, producto_maestro, commit=True):
    """Sincroniza una variedad y su producto maestro hacia todas las tablas correspondientes en PostgreSQL"""
    if not variedad or not producto_maestro:
        return
    v_clean = variedad.strip().upper()
    p_clean = producto_maestro.strip().upper()
    
    try:
        from app.extensions import db
        from sqlalchemy import text
        
        # 1. cat_variedades_maestro (tabla de enlace central)
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS cat_variedades_maestro (
                id SERIAL PRIMARY KEY,
                variedad VARCHAR(150) NOT NULL UNIQUE,
                producto_maestro VARCHAR(150) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        db.session.execute(text("""
            INSERT INTO cat_variedades_maestro (variedad, producto_maestro)
            VALUES (:var, :prod)
            ON CONFLICT (variedad) DO UPDATE 
            SET producto_maestro = EXCLUDED.producto_maestro, updated_at = CURRENT_TIMESTAMP;
        """), {'var': v_clean, 'prod': p_clean})
        
        # 2. dim_prod_maestros
        try:
            ex_pm = db.session.execute(text("SELECT 1 FROM dim_prod_maestros WHERE UPPER(producto_maestro) = :p"), {'p': p_clean}).scalar()
            if not ex_pm:
                max_id = db.session.execute(text("SELECT COALESCE(MAX(id_productos), 0) + 1 FROM dim_prod_maestros")).scalar()
                db.session.execute(text("INSERT INTO dim_prod_maestros (id_productos, producto_maestro) VALUES (:id, :p)"), {'id': max_id, 'p': p_clean})
        except Exception:
            pass

        # 3. dim_variedades
        try:
            ex_v = db.session.execute(text("SELECT 1 FROM dim_variedades WHERE UPPER(variedad) = :v"), {'v': v_clean.upper()}).scalar()
            if not ex_v:
                max_vid = db.session.execute(text("SELECT COALESCE(MAX(id_producto), 0) + 1 FROM dim_variedades")).scalar()
                db.session.execute(text("INSERT INTO dim_variedades (id_producto, variedad) VALUES (:id, :v)"), {'id': max_vid, 'v': v_clean.upper()})
        except Exception:
            pass

        # 4. dim_productos
        try:
            ex_dp = db.session.execute(text("SELECT 1 FROM dim_productos WHERE UPPER(variedad) = :v"), {'v': v_clean.upper()}).scalar()
            if not ex_dp:
                max_pid = db.session.execute(text("SELECT COALESCE(MAX(id_producto), 0) + 1 FROM dim_productos")).scalar()
                db.session.execute(text("INSERT INTO dim_productos (id_producto, variedad) VALUES (:id, :v)"), {'id': max_pid, 'v': v_clean.upper()})
        except Exception:
            pass

        # 5. variedades (tabla general)
        try:
            ex_var = db.session.execute(text("SELECT 1 FROM variedades WHERE UPPER(nombre) = :v"), {'v': v_clean.upper()}).scalar()
            if not ex_var:
                db.session.execute(text("INSERT INTO variedades (nombre, activo, created_at) VALUES (:v, true, NOW())"), {'v': v_clean.upper()})
        except Exception:
            pass
            
        if commit:
            db.session.commit()
    except Exception as e:
        print(f"Error sincronizando variedad a BD: {e}")

def sincronizar_producto_maestro_a_bd(producto_maestro):
    """Sincroniza un producto maestro hacia dim_prod_maestros en PostgreSQL"""
    if not producto_maestro:
        return
    p_clean = producto_maestro.strip().upper()
    try:
        from app.extensions import db
        from sqlalchemy import text
        
        ex_pm = db.session.execute(text("SELECT 1 FROM dim_prod_maestros WHERE UPPER(producto_maestro) = :p"), {'p': p_clean}).scalar()
        if not ex_pm:
            max_id = db.session.execute(text("SELECT COALESCE(MAX(id_productos), 0) + 1 FROM dim_prod_maestros")).scalar()
            db.session.execute(text("INSERT INTO dim_prod_maestros (id_productos, producto_maestro) VALUES (:id, :p)"), {'id': max_id, 'p': p_clean})
        
        db.session.commit()
    except Exception as e:
        print(f"Error sincronizando producto maestro a BD: {e}")

def cargar_maestro_productos():
    """Cargar mapeo {variedad: producto_maestro} unificando cat_variedades_maestro y CSV (fuentes canónicas)"""
    mapeo = {}
    db_count = 0
    tombstones = _get_tombstones_set()
    
    # 1. Cargar desde base de datos cat_variedades_maestro (fuente principal canónica)
    try:
        from app.extensions import db
        from sqlalchemy import text
        res_cat = db.session.execute(text("SELECT variedad, producto_maestro FROM cat_variedades_maestro;")).fetchall()
        for r in res_cat:
            v = (r[0] or '').strip().upper()
            p = (r[1] or '').strip().upper()
            if v and p and v not in tombstones:
                mapeo[v] = p
                db_count += 1
    except Exception:
        pass

    # 2. Cargar desde CSV MAESTRO_PROS.csv solo si la BD está vacía (instalación inicial)
    if db_count == 0:
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
                                    variedad = partes[0].strip().upper()
                                    prod_maestro = partes[1].strip().upper()
                                    if variedad and prod_maestro and variedad not in tombstones and variedad not in mapeo:
                                        mapeo[variedad] = prod_maestro
        except Exception as e:
            print(f"Error cargando maestro productos desde CSV: {e}")
    
    return mapeo

def cargar_relaciones_maestro():
    """Cargar relaciones variedad-producto maestro garantizando asignación unívoca y sin resucitar eliminados"""
    relaciones = []
    vistos = set()
    db_count = 0
    tombstones = _get_tombstones_set()
    
    # 1. Cargar desde cat_variedades_maestro de BD (fuente principal autorizada)
    try:
        from app.extensions import db
        from sqlalchemy import text
        res_cat = db.session.execute(text("SELECT variedad, producto_maestro FROM cat_variedades_maestro ORDER BY producto_maestro, variedad;")).fetchall()
        for r in res_cat:
            v = (r[0] or '').strip().upper()
            p = (r[1] or '').strip().upper()
            if v and p and v not in tombstones:
                if v not in vistos:
                    vistos.add(v)
                    relaciones.append((v, p))
                    db_count += 1
    except Exception:
        pass

    # 2. Solo si la tabla en BD está vacía (instalación inicial), poblar desde CSV
    if db_count == 0:
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
                            variedad = partes[0].strip().upper()
                            producto = partes[1].strip().upper()
                            if not variedad or not producto or variedad in tombstones:
                                continue
                            if variedad in vistos:
                                continue
                            vistos.add(variedad)
                            relaciones.append((variedad, producto))
        except Exception as e:
            print(f"Error cargando relaciones maestro desde CSV: {e}")

    relaciones.sort(key=lambda item: (item[1].upper(), item[0].upper()))
    return relaciones

def guardar_relaciones_maestro(relaciones):
    """Guardar relaciones variedad-producto maestro en CSV y sincronizar bidireccionalmente en PostgreSQL"""
    tombstones = _get_tombstones_set()
    relaciones_unicas = {}
    for variedad, producto in relaciones:
        variedad_limpia = (variedad or '').strip().upper()
        producto_limpio = (producto or '').strip().upper()
        if variedad_limpia and producto_limpio and variedad_limpia not in tombstones:
            # Una sola asignación estricta por variedad (evita duplicados entre cultivos)
            relaciones_unicas[variedad_limpia] = (variedad_limpia, producto_limpio)

    relaciones_ordenadas = sorted(relaciones_unicas.values(), key=lambda item: (item[1].upper(), item[0].upper()))
    
    # 1. Guardar en CSV MAESTRO_PROS.csv
    csv_path = DATA_DIR / 'MAESTRO_PROS.csv'
    try:
        with open(csv_path, 'w', encoding='utf-8', newline='') as file:
            file.write('variedad;prod_maestro\n')
            for variedad, producto in relaciones_ordenadas:
                file.write(f'{variedad};{producto}\n')
    except Exception as e:
        print(f"Error escribiendo MAESTRO_PROS.csv: {e}")

    # 2. Sincronizar en BD PostgreSQL (cat_variedades_maestro)
    try:
        from app.extensions import db
        from sqlalchemy import text
        
        # Asegurar tabla cat_variedades_maestro una sola vez
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS cat_variedades_maestro (
                id SERIAL PRIMARY KEY,
                variedad VARCHAR(150) NOT NULL UNIQUE,
                producto_maestro VARCHAR(150) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        vars_nuevas_set = {v for v, p in relaciones_ordenadas}
        if vars_nuevas_set:
            # Eliminar en BD aquellas variedades que fueron desvinculadas o eliminadas
            from sqlalchemy import bindparam
            stmt = text("DELETE FROM cat_variedades_maestro WHERE UPPER(TRIM(variedad)) NOT IN :vars").bindparams(bindparam('vars', expanding=True))
            db.session.execute(stmt, {'vars': tuple(vars_nuevas_set)})
        else:
            db.session.execute(text("DELETE FROM cat_variedades_maestro"))
            
        for variedad, producto in relaciones_ordenadas:
            sincronizar_variedad_a_bd(variedad, producto, commit=False)
            
        db.session.commit()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"Error sincronizando relaciones con cat_variedades_maestro en BD: {e}")

def cargar_productos_maestros():
    """Cargar productos maestros unificando CSV, cat_variedades_maestro, dim_prod_maestros y crops"""
    productos = set()
    
    # 1. Desde CSV
    productos_csv = _get_csv_path('productos_maestros.csv')
    try:
        if productos_csv.exists():
            with open(productos_csv, 'r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    producto = (row.get('producto_maestro') or '').strip()
                    if producto:
                        productos.add(producto.upper())
    except Exception as e:
        print(f"Error cargando productos maestros CSV: {e}")

    # 2. Desde relaciones maestro activas
    for _, producto in cargar_relaciones_maestro():
        if producto and producto.strip():
            productos.add(producto.strip().upper())

    # 3. Desde dim_prod_maestros de la base de datos
    try:
        from app.extensions import db
        from sqlalchemy import text
        res_dp = db.session.execute(text("SELECT producto_maestro FROM dim_prod_maestros WHERE producto_maestro IS NOT NULL;")).fetchall()
        for r in res_dp:
            if r[0] and r[0].strip():
                productos.add(r[0].strip().upper())
    except Exception:
        pass

    # 4. Desde tabla crops
    try:
        from app.shared.models import Crop
        for c in Crop.query.filter_by(is_active=True).all():
            if c.name and c.name.strip():
                productos.add(c.name.strip().upper())
    except Exception:
        pass

    if not productos:
        productos = {'HYPERICUM', 'GYPSOPHILA', 'SOLIDAGO', 'ASTER', 'SUNFLOWER', 'VERONICA', 'CRASPEDIA', 'PROD.NUEVOS'}

    return sorted(list(productos), key=lambda value: value.upper())

def guardar_productos_maestros(productos):
    """Guardar productos maestros únicos en CSV y sincronizar bidireccionalmente en PostgreSQL (dim_prod_maestros)"""
    productos_limpios = sorted({(p or '').strip().upper() for p in productos if (p or '').strip()}, key=lambda value: value.upper())
    csv_path = DATA_DIR / 'productos_maestros.csv'
    try:
        with open(csv_path, 'w', encoding='utf-8', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['producto_maestro'])
            for producto in productos_limpios:
                writer.writerow([producto])
    except Exception as e:
        print(f"Error escribiendo productos_maestros.csv: {e}")

    try:
        from app.extensions import db
        from sqlalchemy import text
        if productos_limpios:
            from sqlalchemy import bindparam
            stmt = text("DELETE FROM dim_prod_maestros WHERE UPPER(TRIM(producto_maestro)) NOT IN :prods").bindparams(bindparam('prods', expanding=True))
            db.session.execute(stmt, {'prods': tuple(productos_limpios)})
        else:
            db.session.execute(text("DELETE FROM dim_prod_maestros"))
        for p in productos_limpios:
            sincronizar_producto_maestro_a_bd(p)
        db.session.commit()
    except Exception as e:
        print(f"Error sincronizando productos maestros en BD: {e}")

def construir_productos_con_variedades():
    """Construir estructura {producto_maestro: [variedades]} garantizando que todos los productos y variedades aparezcan"""
    productos_con_variedades = defaultdict(list)
    for variedad, producto in cargar_relaciones_maestro():
        if producto and variedad:
            productos_con_variedades[producto.strip().upper()].append(variedad.strip())

    for producto in productos_con_variedades:
        productos_con_variedades[producto] = sorted(set(productos_con_variedades[producto]), key=lambda value: value.upper())

    # Asegurar que productos solos sin variedades asignadas también tengan su clave
    productos_solos = cargar_productos_maestros()
    for producto in productos_solos:
        p_up = producto.strip().upper()
        if p_up not in productos_con_variedades:
            productos_con_variedades[p_up] = []

    return dict(sorted(productos_con_variedades.items(), key=lambda item: item[0].upper()))
