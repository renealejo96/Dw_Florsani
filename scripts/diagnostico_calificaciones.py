import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text

app = create_app()
with app.app_context():
    print("=== DIAGNOSTICO CALIFICACIONES VPS ===")
    r = db.session.execute(text("SELECT min(id), max(id), count(*) FROM calificaciones;")).fetchone()
    print("1. Rango de IDs en calificaciones:", r)

    r2 = db.session.execute(text("SELECT semana_anio, count(*) FROM calificaciones GROUP BY semana_anio ORDER BY semana_anio DESC LIMIT 5;")).fetchall()
    print("2. Semanas en calificaciones:", r2)

    test_ids = [61875, 61873, 61872, 61599, 61528, 61513]
    s_ids = ','.join(map(str, test_ids))
    r3 = db.session.execute(text(f"SELECT id, semana_anio, bloque, labor, cumplido FROM calificaciones WHERE id IN ({s_ids});")).fetchall()
    print("3. IDs de la tablet encontrados directamente:", r3)

    # Buscar semanas 43 y 44
    r44 = db.session.execute(text("SELECT min(id), max(id), count(*), sum(case when cumplido = 1 then 1 else 0 end) as cumplidos FROM calificaciones WHERE semana_anio = 44;")).fetchone()
    print("4. Semana 44 stats:", r44)

    r43 = db.session.execute(text("SELECT min(id), max(id), count(*), sum(case when cumplido = 1 then 1 else 0 end) as cumplidos FROM calificaciones WHERE semana_anio = 43;")).fetchone()
    print("5. Semana 43 stats:", r43)

    # Listar bases de datos disponibles
    try:
        dbs = db.session.execute(text("SELECT datname FROM pg_database WHERE datistemplate = false;")).fetchall()
        print("6. Bases de datos en el servidor PostgreSQL:", [d[0] for d in dbs])
    except Exception as e:
        print("Error listando bases de datos:", e)
