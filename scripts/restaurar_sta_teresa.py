"""
Script de restauración de campo para Santa Teresa (Semana 44).
Aplica directamente las 213 calificaciones y observaciones del operario sobre STA TERESA 2, 3 y 4.
Permite también incluir STA TERESA 1 si el operario también la completó (--todo-sta-teresa).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text

incluir_st1 = '--todo-sta-teresa' in sys.argv or '--incluir-st1' in sys.argv

app = create_app()
with app.app_context():
    print("=== RESTAURACION SANTA TERESA SEMANA 44 ===")
    query = """
        SELECT id, zona, bloque, cama_inicial, cama_final, labor
        FROM calificaciones
        WHERE semana_anio = 44 
          AND zona IN ('STA TERESA2', 'STA TERESA3', 'STA TERESA4')
        ORDER BY zona ASC, bloque ASC, cama_inicial ASC, id ASC;
    """
    rows = db.session.execute(text(query)).fetchall()
    print(f"Filas encontradas en STA TERESA 2, 3, 4: {len(rows)}")

    obs_indices = {
        9: {'val': None, 'obs': 'Preguntar'},
        14: {'val': None, 'obs': 'Preguntar'},
        75: {'val': None, 'obs': 'Haciendo '}
    }

    cumplidos_count = 0
    obs_count = 0

    for idx, r in enumerate(rows):
        cid = r.id
        if idx in obs_indices:
            val = obs_indices[idx]['val']
            obs = obs_indices[idx]['obs']
            db.session.execute(text("""
                UPDATE calificaciones
                SET cumplido = :val, observacion = :obs, tiene_evaluacion = TRUE, fecha_calificacion = NOW()
                WHERE id = :id;
            """), {'val': val, 'obs': obs, 'id': cid})
            obs_count += 1
            print(f"  [OBS] Fila #{idx} (ID {cid}, {r.zona} Blq {r.bloque}, Cama {r.cama_inicial}-{r.cama_final}): obs='{obs}' ({r.labor[:35]})")
        else:
            db.session.execute(text("""
                UPDATE calificaciones
                SET cumplido = 1, tiene_evaluacion = TRUE, fecha_calificacion = NOW()
                WHERE id = :id;
            """), {'id': cid})
            cumplidos_count += 1

    if incluir_st1:
        st1_res = db.session.execute(text("""
            UPDATE calificaciones
            SET cumplido = 1, tiene_evaluacion = TRUE, fecha_calificacion = NOW()
            WHERE semana_anio = 44 AND zona = 'STA TERESA1';
        """))
        print(f"  [STA TERESA 1] Marcadas {st1_res.rowcount} labores adicionales como cumplidas.")
        cumplidos_count += st1_res.rowcount

    db.session.commit()
    print(f"\n[OK] ¡Restauración exitosa! {cumplidos_count} labores marcadas como CUMPLIDAS (1) y {obs_count} observaciones guardadas.")

    # Verificación final
    stats = db.session.execute(text("""
        SELECT zona, count(*) as total, 
               sum(case when cumplido = 1 then 1 else 0 end) as hechos,
               count(case when observacion is not null and trim(observacion) != '' then 1 end) as con_obs
        FROM calificaciones
        WHERE semana_anio = 44 AND zona LIKE 'STA TERESA%'
        GROUP BY zona
        ORDER BY zona;
    """)).fetchall()
    print("\nEstado actual en Santa Teresa Semana 44:")
    for s in stats:
        print(" ", dict(s._mapping))
