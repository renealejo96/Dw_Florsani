from sqlalchemy import create_engine, text, inspect

eng = create_engine('postgresql://postgres:admin@localhost:5432/florsani')
with eng.connect() as c:
    insp = inspect(eng)
    cols = [col['name'] for col in insp.get_columns('registro_cosecha')]
    print("registro_cosecha columns:", cols)
    cols_est = [col['name'] for col in insp.get_columns('estimado')]
    print("estimado columns:", cols_est)

