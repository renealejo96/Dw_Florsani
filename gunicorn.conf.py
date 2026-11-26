"""
Configuración de alto rendimiento de Gunicorn para Florsani ERP (VPS Docker).
Con --preload carga la aplicación una sola vez en memoria (Copy-on-Write)
y recicla el pool de conexiones SQLAlchemy en cada worker hijo (post_fork) para seguridad total con PostgreSQL.
"""
import os

bind = f":{os.environ.get('PORT', '8095')}"
workers = int(os.environ.get('GUNICORN_WORKERS', '4'))
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
worker_class = "gthread"
preload_app = True
timeout = 180
keepalive = 5
max_requests = 5000
max_requests_jitter = 500

def post_fork(server, worker):
    """Asegura que cada proceso worker tenga su propio pool limpio de conexiones PostgreSQL."""
    try:
        from run import app
        from app.extensions import db
        with app.app_context():
            db.engine.dispose()
    except Exception:
        pass
