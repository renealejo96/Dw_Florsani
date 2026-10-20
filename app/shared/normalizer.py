import re
import unicodedata

def normalize_text(text: str) -> str:
    """
    Normalizes a text string by:
    - Trimming leading/trailing spaces
    - Decomposing accents and converting to ASCII
    - Lowercasing
    - Replacing consecutive non-alphanumeric chars with a single underscore
    - Stripping leading/trailing underscores
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    
    text = text.strip()
    text = unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8')
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    text = text.strip('_')
    return text


class ColumnMapper:
    """
    Tolerant column mapper that resolves aliases to logical canonical field names
    without ambiguous substring collisions.
    """

    # Aliases for Estado de Cultivo (exact matches after normalize_text)
    CROP_STATE_ALIASES = {
        'bloques2': {'bloques2', 'bloque2', 'bloques_2', 'blq2', 'bloques', 'bloque', 'blq_completo', 'block', 'blocks', 'bloq', 'bloque_nombre'},
        'blq': {'blq', 'bloque_num', 'num_bloque', 'numero_bloque', 'blq_num', 'block_num'},
        'cama': {'cama', 'cama_num', 'num_cama', 'numero_cama', 'bed', 'camas', 'beds'},
        'sufijo': {'sufijo', 'suffix', 'sub_bloque', 'letra_cama', 'suf', 'lado'},
        'cama_estandar': {'cama_estandar', 'cama_estandard', 'camas_estandar', 'cama_est', 'cama_std', 'standard_bed', 'cama_estndar', 'cama_estandar_total', 'metros_estandar'},
        'zona': {'zona', 'sector', 'zone', 'area', 'zonas', 'bloque_zona'},
        'edad_real': {'edad_real', 'edad_planta', 'real_age', 'edad', 'edades', 'edad_semanas', 'semanas', 'edad_actual', 'semanas_edad', 'edad_pl'},
        'edad_poda': {'edad_poda', 'edad_de_poda'},
        'edad_siem': {'edad_siem', 'edad_siembra', 'edad_de_siembra'},
        'producto_maestro': {'producto_maestro', 'cultivo_maestro', 'master_crop', 'cultivo', 'especie', 'rubro'},
        'producto': {'producto', 'producto_nombre', 'cultivo_variedad', 'crop_name', 'nombre_producto'},
        'variedades_elite': {'variedades_elite', 'variedad_elite', 'variedad', 'variedades', 'variedades_florsani'},
        'variedades_florsani': {'variedades_florsani'},
        'estado': {'estado', 'estado_cultivo', 'status'}
    }

    # Aliases for Productos y Dosis
    PRODUCT_ALIASES = {
        'producto': {'producto', 'codigo', 'cod_producto', 'nombre_corto', 'clave_producto', 'item'},
        'producto_comercial': {'producto_comercial', 'nombre_comercial', 'comercial', 'trade_name', 'descripcion'},
        'um': {'um', 'unidad', 'unidad_medida', 'u_m', 'unidad_de_medida', 'unit'},
        'dosis_fumi': {'dosis_fumi', 'dosis_fumigacion', 'dosis_fumi_l', 'dosis_fumigacion_l', 'dosis_foliar', 'dosis'},
        'dosis_drench': {'dosis_drench', 'dosis_drench_l', 'dosis_suelo', 'drench_dose'},
        'plaga': {'plaga', 'blanco_biologico', 'enfermedad', 'target_pest', 'plagas'},
        'ingrediente_activo': {'ingrediente_activo', 'i_a', 'ingrediente', 'active_ingredient', 'principio_activo'},
        'categoria_toxicologica': {'categoria_toxicologica', 'cat_tox', 'toxicologia', 'toxicidad', 'categoria_tox', 'franja'}
    }

    # Aliases for Litrajes
    LITRAJE_ALIASES = {
        'cultivo': {'cultivo', 'crop', 'nombre_cultivo', 'especie'},
        'edades': {'edades', 'edad', 'edad_semanas', 'edad_real', 'semanas', 'age'},
        'litrajes': {'litrajes', 'litraje', 'litros_cama', 'litros_por_cama', 'l_cama', 'litros'}
    }

    @classmethod
    def match_column(cls, col_name: str, alias_dict: dict) -> str:
        norm = normalize_text(col_name)
        
        # 1. Exact match with canonical key
        if norm in alias_dict:
            return norm
        
        # 2. Exact match with alias set
        for canonical, aliases in alias_dict.items():
            if norm in aliases:
                return canonical

        # 3. Fallback to original normalized name
        return norm

    @classmethod
    def map_dataframe_columns(cls, df, alias_dict: dict):
        """
        Renames DataFrame columns using the provided alias dictionary.
        Guarantees all column names in the resulting DataFrame are unique.
        """
        seen_cols = set()
        new_cols = []
        col_map = {}
        
        for col in df.columns:
            canonical = cls.match_column(col, alias_dict)
            if canonical in alias_dict and canonical not in seen_cols:
                target_name = canonical
            else:
                target_name = normalize_text(col) or 'col'
            
            unique_name = target_name
            counter = 1
            while unique_name in seen_cols:
                counter += 1
                unique_name = f"{target_name}_{counter}"
            
            seen_cols.add(unique_name)
            new_cols.append(unique_name)
            col_map[col] = unique_name

        df_renamed = df.copy()
        df_renamed.columns = new_cols
        return df_renamed, col_map
