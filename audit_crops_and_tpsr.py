from app import create_app
from app.extensions import db
from sqlalchemy import text

app = create_app()
with app.app_context():
    print("=== LITRAJES SUMMARY IN DB ===")
    crops_in_lit = db.session.execute(text("""
        SELECT crop_name, MIN(age), MAX(age), COUNT(*)
        FROM litrajes
        GROUP BY crop_name
        ORDER BY crop_name
    """)).fetchall()
    for c in crops_in_lit:
        print(c)
