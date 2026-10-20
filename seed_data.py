import os
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from app import create_app
from app.extensions import db
from app.shared.models import Crop, Product, Litraje, CropStateRecord, ImportBatch
from app.shared.excel_parser import ExcelParserService

def seed_database(reset=False, skip_large_files=True):
    app = create_app()
    with app.app_context():
        if reset:
            print("Resetting database tables...")
            db.drop_all()
        print("Creating database tables...")
        db.create_all()

        # 1. Seed Default Crops & Age Ranges
        default_crops = [
            {
                'name': 'Hypericum',
                'aliases': ['HYPERICUM', 'HYPERYCUM', 'MAGICAL'],
                'veg_min_age': 0,
                'veg_max_age': 12,
                'prod_min_age': 13,
                'prod_max_age': 25,
                'notes': 'Cultivo principal de Hypericum'
            },
            {
                'name': 'Veronica',
                'aliases': ['VERONICA', 'VERONICA SPRAY', 'VERONICA SPLASH', 'SKYLER'],
                'veg_min_age': 0,
                'veg_max_age': 9,
                'prod_min_age': 10,
                'prod_max_age': 15,
                'notes': 'Cultivo principal de Verónica'
            },
            {
                'name': 'Solidago',
                'aliases': ['SOLIDAGO', 'GOLDEN GLORY YELLOW'],
                'veg_min_age': 0,
                'veg_max_age': 9,
                'prod_min_age': 10,
                'prod_max_age': 15,
                'notes': 'Cultivo de Solidago'
            },
            {
                'name': 'Gypsophila',
                'aliases': ['GYPSOPHILA', 'XLENCE', 'BILLION LIGHTS'],
                'veg_min_age': 0,
                'veg_max_age': 9,
                'prod_min_age': 10,
                'prod_max_age': 16,
                'notes': 'Cultivo de Gypsophila / Xlence / Billion Lights'
            },
            {
                'name': 'Ruscus',
                'aliases': ['RUSCUS'],
                'veg_min_age': 0,
                'veg_max_age': 20,
                'prod_min_age': 21,
                'prod_max_age': 120,
                'notes': 'Cultivo de Ruscus'
            },
            {
                'name': 'Sunflower',
                'aliases': ['SUNFLOWER', 'GIRASOL'],
                'veg_min_age': 0,
                'veg_max_age': 4,
                'prod_min_age': 5,
                'prod_max_age': 12,
                'notes': 'Cultivo de Girasol'
            },
            {
                'name': 'Rumex',
                'aliases': ['RUMEX'],
                'veg_min_age': 0,
                'veg_max_age': 9,
                'prod_min_age': 10,
                'prod_max_age': 20,
                'notes': 'Cultivo de Rumex'
            },
            {
                'name': 'Lysimachia',
                'aliases': ['LYSIMACHIA'],
                'veg_min_age': 0,
                'veg_max_age': 8,
                'prod_min_age': 9,
                'prod_max_age': 16,
                'notes': 'Cultivo de Lysimachia'
            },
            {
                'name': 'Aster',
                'aliases': ['ASTER'],
                'veg_min_age': 0,
                'veg_max_age': 8,
                'prod_min_age': 9,
                'prod_max_age': 16,
                'notes': 'Cultivo de Aster'
            }
        ]

        print("Seeding crops and age configurations...")
        for c_data in default_crops:
            existing = Crop.query.filter_by(name=c_data['name']).first()
            if not existing:
                crop = Crop(
                    name=c_data['name'],
                    veg_min_age=c_data['veg_min_age'],
                    veg_max_age=c_data['veg_max_age'],
                    prod_min_age=c_data['prod_min_age'],
                    prod_max_age=c_data['prod_max_age'],
                    notes=c_data['notes']
                )
                crop.aliases = c_data['aliases']
                db.session.add(crop)
        db.session.commit()

        # 2. Import 'productos y dosis.xlsx' if exists and Product table is empty
        prod_file = BASE_DIR / 'productos y dosis.xlsx'
        if prod_file.exists():
            print(f"Importing products from {prod_file.name}...")
            res = ExcelParserService.parse_products_excel(str(prod_file))
            if res['success']:
                imported = 0
                for item in res['data']:
                    existing_p = Product.query.filter_by(code=item['code']).first()
                    if not existing_p:
                        p = Product(
                            code=item['code'],
                            commercial_name=item['commercial_name'],
                            unit=item['unit'],
                            dose_fumigation=item['dose_fumigation'],
                            dose_drench=item['dose_drench'],
                            pest=item['pest'],
                            active_ingredient=item['active_ingredient'],
                            toxicological_category=item['toxicological_category'],
                            is_active=True
                        )
                        db.session.add(p)
                        imported += 1
                db.session.commit()
                batch = ImportBatch(
                    file_type="PRODUCTOS_DOSIS",
                    filename=prod_file.name,
                    imported_rows=imported,
                    status="SUCCESS",
                    notes="Carga inicial automática"
                )
                db.session.add(batch)
                db.session.commit()
                print(f"  -> Imported {imported} products.")
            else:
                print(f"  -> Error parsing products: {res.get('error')}")

        # 3. Import 'litrajes.xlsx' if exists
        lit_file = BASE_DIR / 'litrajes.xlsx'
        if lit_file.exists():
            print(f"Importing litrajes from {lit_file.name}...")
            res = ExcelParserService.parse_litrajes_excel(str(lit_file))
            if res['success']:
                imported = 0
                for item in res['data']:
                    existing_l = Litraje.query.filter_by(
                        crop_name=item['crop_name'],
                        age=item['age']
                    ).first()
                    if not existing_l:
                        l = Litraje(
                            crop_name=item['crop_name'],
                            age=item['age'],
                            liters_per_bed=item['liters_per_bed']
                        )
                        db.session.add(l)
                        imported += 1
                db.session.commit()
                batch = ImportBatch(
                    file_type="LITRAJES",
                    filename=lit_file.name,
                    imported_rows=imported,
                    status="SUCCESS",
                    notes="Carga inicial automática"
                )
                db.session.add(batch)
                db.session.commit()
                print(f"  -> Imported {imported} litraje rules.")
            else:
                print(f"  -> Error parsing litrajes: {res.get('error')}")

        # 4. Import 'Estado Cultivo PYGAN 2026-33.xlsx' if exists, not skipped, and empty
        ec_file = BASE_DIR / 'Estado Cultivo PYGAN 2026-33.xlsx'
        if not skip_large_files and ec_file.exists():
            existing_count = CropStateRecord.query.count()
            if existing_count == 0:
                print(f"Importing Crop State from {ec_file.name} (reading from row 5 in DATOS)...")
                res = ExcelParserService.parse_crop_state_excel(str(ec_file), header_row=4, sheet_name='DATOS')
                if res['success']:
                    batch = ImportBatch(
                        file_type="ESTADO_CULTIVO",
                        filename=ec_file.name,
                        imported_rows=len(res['data']),
                        status="SUCCESS",
                        notes=f"Carga inicial automática. Total camas estándar: {res['summary']['total_standard_beds']}"
                    )
                    db.session.add(batch)
                    db.session.flush()

                    records = []
                    for r in res['data']:
                        rec = CropStateRecord(
                            batch_id=batch.id,
                            block_full=r['block_full'],
                            block_num=r['block_num'],
                            bed_num=r['bed_num'],
                            suffix=r['suffix'],
                            crop_master=r['crop_master'],
                            product_name=r['product_name'],
                            variety=r['variety'],
                            standard_bed=r['standard_bed'],
                            zone=r['zone'],
                            real_age=r['real_age'],
                            status_raw=r['status_raw']
                        )
                        records.append(rec)
                    
                    db.session.bulk_save_objects(records)
                    db.session.commit()
                    print(f"  -> Imported {len(records)} crop state bed records.")
                else:
                    print(f"  -> Error parsing crop state: {res.get('error')}")
            else:
                print(f"  -> Crop State already contains {existing_count} records.")

        print("Database initialization complete!")

if __name__ == '__main__':
    seed_database()
