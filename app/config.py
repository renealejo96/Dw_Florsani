import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'florsani-erp-agricola-2026')
    DB_PATH = BASE_DIR / 'fumigacion_agricola.db'
    
    # Priority: 1) DATABASE_URL environment variable, 2) Fallback to SQLite
    raw_pg_url = os.environ.get('DATABASE_URL', '').strip()
    if raw_pg_url.startswith('postgres://'):
        raw_pg_url = raw_pg_url.replace('postgres://', 'postgresql://', 1)
        
    sqlite_url = f'sqlite:///{DB_PATH}'
    
    use_pg = False
    chosen_pg_url = None

    if raw_pg_url and 'sqlite' not in raw_pg_url:
        from sqlalchemy import create_engine
        # Intentar conectar con la URL provista (psycopg v3 o psycopg2)
        candidate_urls = [raw_pg_url]
        if raw_pg_url.startswith('postgresql://') and not raw_pg_url.startswith('postgresql+'):
            candidate_urls.append(raw_pg_url.replace('postgresql://', 'postgresql+psycopg2://', 1))

        last_error = None
        for candidate in candidate_urls:
            try:
                test_engine = create_engine(candidate, connect_args={'connect_timeout': 3})
                with test_engine.connect() as conn:
                    use_pg = True
                    chosen_pg_url = candidate
                    break
            except Exception as e:
                last_error = e

        if use_pg:
            print(f"[DB] Conexión exitosa a PostgreSQL en Data Warehouse ({chosen_pg_url.split('@')[-1]}).")
        else:
            print(f"[DB ALERTA] No se pudo conectar a PostgreSQL ({raw_pg_url}): {last_error}")

    if use_pg and chosen_pg_url:
        SQLALCHEMY_DATABASE_URI = chosen_pg_url
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_size': int(os.environ.get('DB_POOL_SIZE', 10)),
            'max_overflow': int(os.environ.get('DB_MAX_OVERFLOW', 20)),
            'pool_timeout': 30,
            'pool_recycle': 1800,
            'pool_pre_ping': True,
        }
    else:
        SQLALCHEMY_DATABASE_URI = sqlite_url
        SQLALCHEMY_ENGINE_OPTIONS = {
            'connect_args': {'timeout': 30}
        }

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = str(BASE_DIR / 'uploads')
    Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)
    MAX_CONTENT_LENGTH = 64 * 1024 * 1024  # 64 MB
    DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() in ('true', '1', 't')
