from sqlalchemy import create_engine, text

eng = create_engine('postgresql://postgres:admin@localhost:5432/florsani')
with eng.connect() as c:
    rows = c.execute(text("SELECT variedad, producto_maestro FROM cat_variedades_maestro WHERE variedad ILIKE '%CRISTAL%' OR variedad ILIKE '%CRYSTAL%'")).fetchall()
    print("cat_variedades_maestro:", rows)
    rows_reg = c.execute(text("SELECT DISTINCT variedad FROM registro_cosecha WHERE variedad ILIKE '%CRISTAL%' OR variedad ILIKE '%CRYSTAL%'")).fetchall()
    print("registro_cosecha:", rows_reg)



