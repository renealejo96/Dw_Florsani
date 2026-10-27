"""
Script para restaurar en caliente las calificaciones recolectadas por el operario en campo.
Procesa el lote exacto de IDs, estados y observaciones salvados desde la tablet.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from sqlalchemy import text

DATA = [
    {"id":61875,"val":"1","obs":""},{"id":61873,"val":"1","obs":""},{"id":61872,"val":"1","obs":""},{"id":61866,"val":"1","obs":""},
    {"id":61865,"val":"1","obs":""},{"id":61868,"val":"1","obs":""},{"id":61859,"val":"1","obs":""},{"id":61860,"val":"1","obs":""},
    {"id":61862,"val":"1","obs":""},{"id":61821,"val":"1","obs":""},{"id":61823,"val":"1","obs":""},{"id":61829,"val":"1","obs":""},
    {"id":61831,"val":"1","obs":""},{"id":61832,"val":"1","obs":""},{"id":61833,"val":"1","obs":""},{"id":61834,"val":"1","obs":""},
    {"id":61835,"val":"1","obs":""},{"id":61836,"val":"1","obs":""},{"id":61837,"val":"1","obs":""},{"id":61839,"val":"1","obs":""},
    {"id":61838,"val":"1","obs":""},{"id":61840,"val":"1","obs":""},{"id":61841,"val":"1","obs":""},{"id":61843,"val":"1","obs":""},
    {"id":61842,"val":"1","obs":""},{"id":61845,"val":"1","obs":""},{"id":61844,"val":"1","obs":""},{"id":61846,"val":"1","obs":""},
    {"id":61847,"val":"1","obs":""},{"id":61849,"val":"1","obs":""},{"id":61848,"val":"1","obs":""},{"id":61850,"val":"1","obs":""},
    {"id":61851,"val":"1","obs":""},{"id":61852,"val":"1","obs":""},{"id":61853,"val":"1","obs":""},{"id":61854,"val":"1","obs":""},
    {"id":61855,"val":"1","obs":""},{"id":61857,"val":"1","obs":""},{"id":61856,"val":"1","obs":""},{"id":61858,"val":"1","obs":""},
    {"id":61788,"val":"1","obs":""},{"id":61790,"val":"1","obs":""},{"id":61792,"val":"1","obs":""},{"id":61791,"val":"1","obs":""},
    {"id":61793,"val":"1","obs":""},{"id":61794,"val":"1","obs":""},{"id":61796,"val":"1","obs":""},{"id":61795,"val":"1","obs":""},
    {"id":61797,"val":"1","obs":""},{"id":61798,"val":"1","obs":""},{"id":61799,"val":"1","obs":""},{"id":61800,"val":"1","obs":""},
    {"id":61801,"val":"1","obs":""},{"id":61802,"val":"1","obs":""},{"id":61803,"val":"1","obs":""},{"id":61804,"val":"1","obs":""},
    {"id":61805,"val":"1","obs":""},{"id":61806,"val":"1","obs":""},{"id":61808,"val":"1","obs":""},{"id":61807,"val":"1","obs":""},
    {"id":61810,"val":"1","obs":""},{"id":61809,"val":"1","obs":""},{"id":61811,"val":"1","obs":""},{"id":61812,"val":"1","obs":""},
    {"id":61813,"val":"1","obs":""},{"id":61814,"val":"1","obs":""},{"id":61816,"val":"1","obs":""},{"id":61815,"val":"1","obs":""},
    {"id":61818,"val":"1","obs":""},{"id":61817,"val":"1","obs":""},{"id":61819,"val":"1","obs":""},{"id":61820,"val":"1","obs":""},
    {"id":61739,"val":"1","obs":""},{"id":61740,"val":"1","obs":""},{"id":61741,"val":"1","obs":""},{"id":61742,"val":"1","obs":""},
    {"id":61744,"val":"1","obs":""},{"id":61743,"val":"1","obs":""},{"id":61745,"val":"1","obs":""},{"id":61746,"val":"1","obs":""},
    {"id":61748,"val":"1","obs":""},{"id":61750,"val":"1","obs":""},{"id":61749,"val":"1","obs":""},{"id":61751,"val":"1","obs":""},
    {"id":61752,"val":"1","obs":""},{"id":61753,"val":"1","obs":""},{"id":61754,"val":"1","obs":""},{"id":61755,"val":"1","obs":""},
    {"id":61757,"val":"1","obs":""},{"id":61756,"val":"1","obs":""},{"id":61758,"val":"1","obs":""},{"id":61759,"val":"1","obs":""},
    {"id":61760,"val":"1","obs":""},{"id":61761,"val":"1","obs":""},{"id":61762,"val":"1","obs":""},{"id":61764,"val":"1","obs":""},
    {"id":61765,"val":"1","obs":""},{"id":61763,"val":"1","obs":""},{"id":61766,"val":"1","obs":""},{"id":61767,"val":"1","obs":""},
    {"id":61768,"val":"1","obs":""},{"id":61770,"val":"1","obs":""},{"id":61769,"val":"1","obs":""},{"id":61771,"val":"1","obs":""},
    {"id":61772,"val":"1","obs":""},{"id":61773,"val":"1","obs":""},{"id":61774,"val":"1","obs":""},{"id":61776,"val":"1","obs":""},
    {"id":61775,"val":"1","obs":""},{"id":61777,"val":"1","obs":""},{"id":61778,"val":"1","obs":""},{"id":61781,"val":"1","obs":""},
    {"id":61779,"val":"1","obs":""},{"id":61780,"val":"1","obs":""},{"id":61624,"val":"1","obs":""},{"id":61625,"val":"1","obs":""},
    {"id":61627,"val":"1","obs":""},{"id":61626,"val":"1","obs":""},{"id":61628,"val":"1","obs":""},{"id":61617,"val":"1","obs":""},
    {"id":61619,"val":"1","obs":""},{"id":61621,"val":"1","obs":""},{"id":61622,"val":"1","obs":""},{"id":61611,"val":"1","obs":""},
    {"id":61615,"val":"1","obs":""},{"id":61613,"val":"1","obs":""},{"id":61616,"val":"1","obs":""},{"id":61605,"val":"1","obs":""},
    {"id":61609,"val":"1","obs":""},{"id":61610,"val":"1","obs":""},{"id":61607,"val":"1","obs":""},{"id":61608,"val":"1","obs":""},
    {"id":61599,"val":"null","obs":"Haciendo "},{"id":61600,"val":"1","obs":""},{"id":61601,"val":"1","obs":""},{"id":61602,"val":"1","obs":""},
    {"id":61603,"val":"1","obs":""},{"id":61604,"val":"1","obs":""},{"id":61595,"val":"1","obs":""},{"id":61597,"val":"1","obs":""},
    {"id":61596,"val":"1","obs":""},{"id":61598,"val":"1","obs":""},{"id":61583,"val":"1","obs":""},{"id":61584,"val":"1","obs":""},
    {"id":61587,"val":"1","obs":""},{"id":61588,"val":"1","obs":""},{"id":61589,"val":"1","obs":""},{"id":61592,"val":"1","obs":""},
    {"id":61591,"val":"1","obs":""},{"id":61593,"val":"1","obs":""},{"id":61594,"val":"1","obs":""},{"id":61565,"val":"1","obs":""},
    {"id":61566,"val":"1","obs":""},{"id":61567,"val":"1","obs":""},{"id":61568,"val":"1","obs":""},{"id":61569,"val":"1","obs":""},
    {"id":61570,"val":"1","obs":""},{"id":61571,"val":"1","obs":""},{"id":61572,"val":"1","obs":""},{"id":61574,"val":"1","obs":""},
    {"id":61575,"val":"1","obs":""},{"id":61576,"val":"1","obs":""},{"id":61577,"val":"1","obs":""},{"id":61579,"val":"1","obs":""},
    {"id":61581,"val":"1","obs":""},{"id":61582,"val":"1","obs":""},{"id":61580,"val":"1","obs":""},{"id":61553,"val":"1","obs":""},
    {"id":61554,"val":"1","obs":""},{"id":61555,"val":"1","obs":""},{"id":61556,"val":"1","obs":""},{"id":61558,"val":"1","obs":""},
    {"id":61559,"val":"1","obs":""},{"id":61560,"val":"1","obs":""},{"id":61563,"val":"1","obs":""},{"id":61564,"val":"1","obs":""},
    {"id":61552,"val":"1","obs":""},{"id":61551,"val":"1","obs":""},{"id":61548,"val":"1","obs":""},{"id":61547,"val":"1","obs":""},
    {"id":61541,"val":"1","obs":""},{"id":61543,"val":"1","obs":""},{"id":61542,"val":"1","obs":""},{"id":61544,"val":"1","obs":""},
    {"id":61545,"val":"1","obs":""},{"id":61546,"val":"1","obs":""},{"id":61531,"val":"1","obs":""},{"id":61532,"val":"1","obs":""},
    {"id":61534,"val":"1","obs":""},{"id":61535,"val":"1","obs":""},{"id":61536,"val":"1","obs":""},{"id":61537,"val":"1","obs":""},
    {"id":61538,"val":"1","obs":""},{"id":61539,"val":"1","obs":""},{"id":61540,"val":"1","obs":""},{"id":61533,"val":"1","obs":""},
    {"id":61530,"val":"1","obs":""},{"id":61528,"val":"null","obs":"Preguntar"},{"id":61529,"val":"1","obs":""},{"id":61527,"val":"1","obs":""},
    {"id":61526,"val":"1","obs":""},{"id":61525,"val":"1","obs":""},{"id":61523,"val":"null","obs":"Preguntar "},{"id":61524,"val":"1","obs":""},
    {"id":61522,"val":"1","obs":""},{"id":61521,"val":"1","obs":""},{"id":61520,"val":"1","obs":""},{"id":61519,"val":"1","obs":""},
    {"id":61518,"val":"1","obs":""},{"id":61517,"val":"1","obs":""},{"id":61516,"val":"1","obs":""},{"id":61515,"val":"1","obs":""},
    {"id":61513,"val":"1","obs":""}
]

app = create_app()
with app.app_context():
    print(f"[RESTAURACION-CAMPO] Procesando {len(DATA)} calificaciones del operario...")
    updated_cumplidos = 0
    updated_obs = 0

    ids_cumplidos_1 = []
    obs_dict = {}

    for item in DATA:
        cid = int(item['id'])
        v = str(item.get('val', '')).strip().lower()
        obs = str(item.get('obs', '')).strip()

        if v == '1':
            ids_cumplidos_1.append(cid)
            if obs:
                obs_dict[cid] = obs
        elif v == '0':
            db.session.execute(text("""
                UPDATE calificaciones 
                SET cumplido = 0, observacion = :obs, tiene_evaluacion = TRUE, fecha_calificacion = NOW() 
                WHERE id = :id;
            """), {'id': cid, 'obs': obs})
            updated_cumplidos += 1
        elif obs:
            db.session.execute(text("""
                UPDATE calificaciones 
                SET observacion = :obs 
                WHERE id = :id;
            """), {'id': cid, 'obs': obs})
            updated_obs += 1

    if ids_cumplidos_1:
        res = db.session.execute(text("""
            UPDATE calificaciones 
            SET cumplido = 1, tiene_evaluacion = TRUE, fecha_calificacion = NOW() 
            WHERE id = ANY(:ids);
        """), {'ids': ids_cumplidos_1})
        updated_cumplidos += res.rowcount

    for cid, obs in obs_dict.items():
        db.session.execute(text("UPDATE calificaciones SET observacion = :obs WHERE id = :id;"), {'id': cid, 'obs': obs})
        updated_obs += 1

    db.session.commit()
    print(f"[OK] ¡Restauración exitosa! {updated_cumplidos} labores marcadas como Cumplidas y {updated_obs} observaciones guardadas.")
