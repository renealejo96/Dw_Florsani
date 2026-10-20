import io
import pandas as pd
import numpy as np
from app.shared.normalizer import ColumnMapper, normalize_text

class ExcelParserService:

    @staticmethod
    def parse_products_excel(file_or_path):
        """
        Parses 'productos y dosis.xlsx'.
        Returns dict with:
        - success: bool
        - data: list of dicts with cleaned product fields
        - total_rows: int
        - errors: list of warnings/errors
        - preview: list of dicts (first 10 rows)
        """
        errors = []
        try:
            # Read sheet (Hoja1 or first sheet)
            xls = pd.ExcelFile(file_or_path)
            sheet_name = 'Hoja1' if 'Hoja1' in xls.sheet_names else xls.sheet_names[0]
            df = pd.read_excel(xls, sheet_name=sheet_name)
            
            # Map columns
            df, col_map = ColumnMapper.map_dataframe_columns(df, ColumnMapper.PRODUCT_ALIASES)
            
            if 'producto' not in df.columns:
                return {
                    'success': False,
                    'error': "No se encontró la columna requerida 'PRODUCTO' en el archivo.",
                    'columns_found': df.columns.tolist()
                }

            cleaned_products = []
            for idx, row in df.iterrows():
                code = str(row['producto']).strip() if pd.notna(row.get('producto')) else ''
                if not code or code.upper() == 'NAN':
                    continue

                comm_name = str(row.get('producto_comercial', '')).strip() if pd.notna(row.get('producto_comercial')) else code
                um = str(row.get('um', 'CC')).strip().upper() if pd.notna(row.get('um')) else 'CC'
                
                # Dosis fumi
                dose_fumi = None
                if 'dosis_fumi' in row and pd.notna(row['dosis_fumi']):
                    try:
                        dose_fumi = float(row['dosis_fumi'])
                    except (ValueError, TypeError):
                        dose_fumi = None
                
                # Dosis drench
                dose_drench = None
                if 'dosis_drench' in row and pd.notna(row['dosis_drench']):
                    try:
                        dose_drench = float(row['dosis_drench'])
                    except (ValueError, TypeError):
                        dose_drench = None

                pest = str(row.get('plaga', '')).strip() if pd.notna(row.get('plaga')) else ''
                ia = str(row.get('ingrediente_activo', '')).strip() if pd.notna(row.get('ingrediente_activo')) else ''
                cat_tox = str(row.get('categoria_toxicologica', '')).strip() if pd.notna(row.get('categoria_toxicologica')) else ''

                cleaned_products.append({
                    'code': code,
                    'commercial_name': comm_name,
                    'unit': um,
                    'dose_fumigation': dose_fumi,
                    'dose_drench': dose_drench,
                    'pest': pest,
                    'active_ingredient': ia,
                    'toxicological_category': cat_tox,
                    'is_active': True
                })

            return {
                'success': True,
                'total_rows': len(cleaned_products),
                'data': cleaned_products,
                'preview': cleaned_products[:15],
                'errors': errors,
                'sheet_name': sheet_name
            }
        except Exception as e:
            return {'success': False, 'error': f"Error al procesar archivo de productos: {str(e)}"}

    @staticmethod
    def parse_litrajes_excel(file_or_path):
        """
        Parses 'litrajes.xlsx'.
        Returns dict with:
        - success: bool
        - data: list of dicts with crop, age, liters_per_bed
        - total_rows: int
        - preview: list of dicts
        """
        errors = []
        try:
            xls = pd.ExcelFile(file_or_path)
            sheet_name = 'Hoja1' if 'Hoja1' in xls.sheet_names else xls.sheet_names[0]
            df = pd.read_excel(xls, sheet_name=sheet_name)
            
            df, col_map = ColumnMapper.map_dataframe_columns(df, ColumnMapper.LITRAJE_ALIASES)
            
            req_cols = ['cultivo', 'edades', 'litrajes']
            missing = [c for c in req_cols if c not in df.columns]
            if missing:
                return {
                    'success': False,
                    'error': f"Faltan columnas requeridas en el archivo de litrajes: {missing}",
                    'columns_found': df.columns.tolist()
                }

            cleaned_litrajes = []
            for idx, row in df.iterrows():
                crop = str(row['cultivo']).strip().upper() if pd.notna(row.get('cultivo')) else ''
                if not crop or crop == 'NAN':
                    continue

                try:
                    age = int(round(float(row['edades'])))
                except (ValueError, TypeError):
                    continue

                try:
                    liters = float(row['litrajes'])
                except (ValueError, TypeError):
                    liters = 0.0

                cleaned_litrajes.append({
                    'crop_name': crop,
                    'age': age,
                    'liters_per_bed': liters
                })

            return {
                'success': True,
                'total_rows': len(cleaned_litrajes),
                'data': cleaned_litrajes,
                'preview': cleaned_litrajes[:15],
                'errors': errors,
                'sheet_name': sheet_name
            }
        except Exception as e:
            return {'success': False, 'error': f"Error al procesar archivo de litrajes: {str(e)}"}

    @staticmethod
    def parse_crop_state_excel(file_or_path, header_row=4, sheet_name='DATOS'):
        """
        Parses 'Estado Cultivo PYGAN 2026-33.xlsx' or any crop state file.
        Header begins at row index 4 (5th line in Excel) by default, with auto-detection fallback.
        Returns dict with:
        - success: bool
        - data: list of cleaned bed records
        - summary: counts by crop, zone, total standard beds
        - preview: sample rows
        """
        errors = []
        try:
            xls = pd.ExcelFile(file_or_path)
            
            # 1. Sheet selection: prioritize explicit sheet_name, then DATOS, then other standard names
            target_sheet = None
            if sheet_name and sheet_name in xls.sheet_names:
                target_sheet = sheet_name
            elif 'DATOS' in xls.sheet_names:
                target_sheet = 'DATOS'
            else:
                for c_name in ['DATOS CULTIVO', 'ESTADO DE CULTIVO', 'ESTADO_CULTIVO', 'ESTADOCULTIVO', 'PLANO DE CULTIVO', 'PLANO']:
                    for s in xls.sheet_names:
                        if normalize_text(s) == normalize_text(c_name):
                            target_sheet = s
                            break
                    if target_sheet:
                        break
            
            if not target_sheet:
                target_sheet = xls.sheet_names[0]
            
            # 2. Read DataFrame with initial header_row and verify columns
            df = pd.read_excel(xls, sheet_name=target_sheet, header=header_row)
            df, col_map = ColumnMapper.map_dataframe_columns(df, ColumnMapper.CROP_STATE_ALIASES)
            
            # If essential columns not found at header_row, scan first 12 rows
            if 'bloques2' not in df.columns and 'cama' not in df.columns:
                for test_hdr in range(12):
                    if test_hdr == header_row:
                        continue
                    try:
                        df_test = pd.read_excel(xls, sheet_name=target_sheet, header=test_hdr)
                        df_test, test_map = ColumnMapper.map_dataframe_columns(df_test, ColumnMapper.CROP_STATE_ALIASES)
                        if 'bloques2' in df_test.columns or ('cama' in df_test.columns and ('producto_maestro' in df_test.columns or 'producto' in df_test.columns)):
                            df = df_test
                            col_map = test_map
                            header_row = test_hdr
                            break
                    except Exception:
                        continue

            # Ensure essential columns exist
            if 'bloques2' not in df.columns:
                bloq_cols = [c for c in df.columns if 'bloq' in str(c).lower() or 'block' in str(c).lower()]
                if bloq_cols:
                    df['bloques2'] = df[bloq_cols[0]]
                else:
                    return {'success': False, 'error': f"No se encontró columna para Bloques (BLOQUES2) en la hoja '{target_sheet}'."}

            cleaned_records = []
            crops_counter = {}
            zones_counter = {}
            total_std_beds = 0.0

            def safe_scalar_str(val, default=''):
                if val is None or pd.isna(val):
                    return default
                if hasattr(val, 'iloc'):
                    val = val.iloc[0]
                s = str(val).strip()
                return s if s.lower() != 'nan' else default

            def safe_scalar_float(val, default=None):
                if val is None or pd.isna(val):
                    return default
                if hasattr(val, 'iloc'):
                    val = val.iloc[0]
                try:
                    f = float(val)
                    return f if not np.isnan(f) else default
                except (ValueError, TypeError):
                    return default

            for idx, row in df.iterrows():
                block_full = safe_scalar_str(row.get('bloques2'))
                if not block_full or block_full.upper() in ('NAN', 'TOTAL', 'BLOQUES2', 'NONE', 'TOTAL GENERAL'):
                    continue

                # Block num
                block_num = safe_scalar_str(row.get('blq'))
                if not block_num:
                    block_num = block_full.replace('BL', '').replace('BLQ', '').strip()

                # Bed number
                bed_num_f = safe_scalar_float(row.get('cama'), default=1.0)
                bed_num = int(bed_num_f) if bed_num_f is not None else 1

                # Suffix
                suffix = safe_scalar_str(row.get('sufijo'), default='A').upper() or 'A'

                # Crop master & Product
                crop_master = safe_scalar_str(row.get('producto_maestro')).upper()
                product_name = safe_scalar_str(row.get('producto')).upper() or crop_master
                variety = safe_scalar_str(row.get('variedades_elite')).upper()
                zone = safe_scalar_str(row.get('zona')).upper()
                status_raw = safe_scalar_str(row.get('estado')).upper()

                # Standard bed
                std_bed = safe_scalar_float(row.get('cama_estandar'), default=1.0)
                if std_bed is None or std_bed <= 0:
                    std_bed = 1.0

                # Real age with fallbacks (edad_real, edad_poda, edad_siem)
                real_age = safe_scalar_float(row.get('edad_real'))
                if real_age is None:
                    real_age = safe_scalar_float(row.get('edad_poda'))
                if real_age is None:
                    real_age = safe_scalar_float(row.get('edad_siem'))

                # If no age found but crop is active, default to 10.0 so beds are never lost
                if real_age is None and crop_master and crop_master not in ('VACIO', 'DESCARTE', 'TUMBAR'):
                    real_age = 10.0

                cleaned_records.append({
                    'block_full': block_full,
                    'block_num': block_num,
                    'bed_num': bed_num,
                    'suffix': suffix,
                    'crop_master': crop_master,
                    'product_name': product_name,
                    'variety': variety,
                    'standard_bed': std_bed,
                    'zone': zone,
                    'real_age': real_age,
                    'status_raw': status_raw
                })

                if crop_master:
                    crops_counter[crop_master] = crops_counter.get(crop_master, 0) + 1
                if zone:
                    zones_counter[zone] = zones_counter.get(zone, 0) + 1
                total_std_beds += std_bed

            return {
                'success': True,
                'total_rows': len(cleaned_records),
                'data': cleaned_records,
                'preview': cleaned_records[:20],
                'summary': {
                    'total_records': len(cleaned_records),
                    'total_standard_beds': round(total_std_beds, 2),
                    'crops_count': crops_counter,
                    'zones_count': zones_counter
                },
                'sheet_name': target_sheet,
                'errors': errors
            }
        except Exception as e:
            return {'success': False, 'error': f"Error al procesar Estado de Cultivo: {str(e)}"}
