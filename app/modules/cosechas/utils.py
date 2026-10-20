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
    """Cargar variedades desde CSV y complementar con base de datos (cat_variedades_maestro, registro_cosecha, dim_variedades, fact_eventos_cultivo y estimado)"""
    variedades = set()
    csv_path = _get_csv_path('variedades.csv')
    try:
        if csv_path.exists():
            with open(csv_path, 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    val = (row.get('variedad') or '').strip()
                    if val:
                        variedades.add(val)
    except Exception as e:
        print(f"Error cargando variedades desde CSV: {e}")
    
    # Complementar con variedades existentes en la base de datos
    try:
        from app.extensions import db
        from sqlalchemy import text
        
        # 0. cat_variedades_maestro
        try:
            vars_cat = db.session.execute(text("SELECT DISTINCT variedad FROM cat_variedades_maestro WHERE variedad IS NOT NULL AND TRIM(variedad) != '';")).fetchall()
            for v in vars_cat:
                if v[0] and v[0].strip():
                    variedades.add(v[0].strip())
        except Exception:
            pass

        # 1. registro_cosecha
        vars_db = db.session.execute(text("SELECT DISTINCT variedad FROM registro_cosecha WHERE variedad IS NOT NULL AND TRIM(variedad) != '';")).fetchall()
        for v in vars_db:
            if v[0] and v[0].strip():
                variedades.add(v[0].strip())
        
        # 2. dim_variedades
        try:
            vars_dim = db.session.execute(text("SELECT DISTINCT variedad FROM dim_variedades WHERE variedad IS NOT NULL AND TRIM(variedad) != '';")).fetchall()
            for v in vars_dim:
                if v[0] and v[0].strip():
                    variedades.add(v[0].strip())
        except Exception:
            pass

        # 3. fact_eventos_cultivo
        try:
            vars_ev = db.session.execute(text("SELECT DISTINCT variedad FROM fact_eventos_cultivo WHERE variedad IS NOT NULL AND TRIM(variedad) != '';")).fetchall()
            for v in vars_ev:
                if v[0] and v[0].strip():
                    variedades.add(v[0].strip())
        except Exception:
            pass

        # 4. estimado
        try:
            vars_est = db.session.execute(text("SELECT DISTINCT variedad FROM estimado WHERE variedad IS NOT NULL AND TRIM(variedad) != '';")).fetchall()
            for v in vars_est:
                if v[0] and v[0].strip():
                    variedades.add(v[0].strip())
        except Exception:
            pass
    except Exception:
        pass

    if not variedades:
        variedades = {'Gold', 'Red Naomi', 'Explorer', 'Freedom', 'High & Magic'}
    
    return sorted(list(variedades), key=lambda x: x.upper())

def guardar_variedades(variedades):
    """Guardar variedades únicas en CSV y sincronizar en PostgreSQL (dim_variedades, dim_productos, variedades)"""
    variedades_limpias = sorted({(v or '').strip() for v in variedades if (v or '').strip()}, key=lambda value: value.upper())
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

def sincronizar_variedad_a_bd(variedad, producto_maestro):
    """Sincroniza una variedad y su producto maestro hacia todas las tablas correspondientes en PostgreSQL"""
    if not variedad or not producto_maestro:
        return
    v_clean = variedad.strip()
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
    """Cargar mapeo {variedad: producto_maestro} unificando cat_variedades_maestro, CSV y base de datos"""
    mapeo = {}
    
    # 1. Cargar desde base de datos cat_variedades_maestro (fuente prioritaria)
    try:
        from app.extensions import db
        from sqlalchemy import text
        res_cat = db.session.execute(text("SELECT variedad, producto_maestro FROM cat_variedades_maestro;")).fetchall()
        for r in res_cat:
            v = (r[0] or '').strip()
            p = (r[1] or '').strip()
            if v and p:
                mapeo[v] = p
    except Exception:
        pass

    # 2. Cargar desde CSV MAESTRO_PROS.csv
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
                                if variedad and prod_maestro and variedad not in mapeo:
                                    mapeo[variedad] = prod_maestro
    except Exception as e:
        print(f"Error cargando maestro productos desde CSV: {e}")
    
    # 3. Complementar con estimado y fact_eventos_cultivo
    try:
        from app.extensions import db
        from sqlalchemy import text
        try:
            res_est = db.session.execute(text("SELECT DISTINCT variedad, producto_maestro FROM estimado WHERE variedad IS NOT NULL AND producto_maestro IS NOT NULL;")).fetchall()
            for r in res_est:
                v = (r[0] or '').strip()
                p = (r[1] or '').strip()
                if v and p and v not in mapeo:
                    mapeo[v] = p
        except Exception:
            pass

        try:
            res_ev = db.session.execute(text("SELECT DISTINCT variedad, producto_maestro FROM fact_eventos_cultivo WHERE variedad IS NOT NULL AND producto_maestro IS NOT NULL;")).fetchall()
            for r in res_ev:
                v = (r[0] or '').strip()
                p = (r[1] or '').strip()
                if v and p and v not in mapeo:
                    mapeo[v] = p
        except Exception:
            pass
    except Exception:
        pass

    return mapeo

def cargar_relaciones_maestro():
    """Cargar relaciones variedad-producto maestro como lista de tuplas únicas combinando CSV y BD"""
    relaciones = []
    vistos = set()
    
    # 1. Cargar desde cat_variedades_maestro de BD
    try:
        from app.extensions import db
        from sqlalchemy import text
        res_cat = db.session.execute(text("SELECT variedad, producto_maestro FROM cat_variedades_maestro;")).fetchall()
        for r in res_cat:
            v = (r[0] or '').strip()
            p = (r[1] or '').strip()
            if v and p:
                clave = (v.upper(), p.upper())
                if clave not in vistos:
                    vistos.add(clave)
                    relaciones.append((v, p))
    except Exception:
        pass

    # 2. Cargar desde CSV MAESTRO_PROS.csv
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
        print(f"Error cargando relaciones maestro desde CSV: {e}")

    # 3. Complementar con cualquier mapeo adicional proveniente de la base de datos
    mapeo_completo = cargar_maestro_productos()
    for var, prod in mapeo_completo.items():
        clave = (var.upper(), prod.upper())
        if clave not in vistos:
            relaciones.append((var, prod))
            vistos.add(clave)

    relaciones.sort(key=lambda item: (item[1].upper(), item[0].upper()))
    return relaciones

def guardar_relaciones_maestro(relaciones):
    """Guardar relaciones variedad-producto maestro en CSV y sincronizar en PostgreSQL"""
    relaciones_unicas = {}
    for variedad, producto in relaciones:
        variedad_limpia = (variedad or '').strip()
        producto_limpio = (producto or '').strip()
        if variedad_limpia and producto_limpio:
            relaciones_unicas[variedad_limpia.upper()] = (variedad_limpia, producto_limpio)

    relaciones_ordenadas = sorted(relaciones_unicas.values(), key=lambda item: (item[1].upper(), item[0].upper()))
    
    # 1. Guardar en CSV
    csv_path = DATA_DIR / 'MAESTRO_PROS.csv'
    try:
        with open(csv_path, 'w', encoding='utf-8', newline='') as file:
            file.write('variedad;prod_maestro\n')
            for variedad, producto in relaciones_ordenadas:
                file.write(f'{variedad};{producto}\n')
    except Exception as e:
        print(f"Error escribiendo MAESTRO_PROS.csv: {e}")

    # 2. Sincronizar en BD PostgreSQL
    for variedad, producto in relaciones_ordenadas:
        sincronizar_variedad_a_bd(variedad, producto)

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
    """Guardar productos maestros únicos en CSV y sincronizar en PostgreSQL (dim_prod_maestros)"""
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

    for p in productos_limpios:
        sincronizar_producto_maestro_a_bd(p)

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
