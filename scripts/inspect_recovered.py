from sqlalchemy import create_engine, text

eng = create_engine('postgresql://postgres:admin@localhost:5432/florsani')
with eng.connect() as c:
    rows = c.execute(text("SELECT UPPER(variedad), count(*) FROM cat_variedades_maestro GROUP BY UPPER(variedad) HAVING count(*) > 1")).fetchall()
    print("Collisions if uppercase:", rows)

