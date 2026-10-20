-- ==============================================================================
-- PROYECTO: DATA WAREHOUSE & BACKEND TRANSACCIONAL - GRUPO FLORICULTOR FLORSANI
-- ARCHIVO: 02_seed_dimensions.sql
-- DESCRIPCIÓN: Datos iniciales de catálogo (Fincas, Camas, Calendario, Recetas)
-- ==============================================================================

-- 1. POBLAR DIM_FINCAS (3 empresas del grupo, total 330 hectáreas)
INSERT INTO dim_fincas (id_finca, codigo_finca, nombre_finca, hectareas)
VALUES 
    (1, 'FN01', 'Finca Santa Rosa', 120.00),
    (2, 'FN02', 'Finca El Rosal', 110.00),
    (3, 'FN03', 'Finca Bella Vista', 100.00)
ON CONFLICT (id_finca) DO UPDATE 
SET nombre_finca = EXCLUDED.nombre_finca, hectareas = EXCLUDED.hectareas;

-- Asegurar que la secuencia de SERIAL continúe desde el id máximo
SELECT setval('dim_fincas_id_finca_seq', COALESCE((SELECT MAX(id_finca) FROM dim_fincas), 1));


-- 2. POBLAR DIM_CAMAS (Catálogo físico base)
INSERT INTO dim_camas (id_cama, id_finca, zona, bloque, numero_cama, metros_cuadrados, tecnico_asignado)
VALUES 
    -- Finca 1: Santa Rosa
    ('F1-Z1-B01-C01', 1, 'Zona Norte', 'Bloque 1', 'Cama 01', 32.50, 'Ing. Carlos Mendoza'),
    ('F1-Z1-B01-C02', 1, 'Zona Norte', 'Bloque 1', 'Cama 02', 32.50, 'Ing. Carlos Mendoza'),
    ('F1-Z1-B02-C01', 1, 'Zona Norte', 'Bloque 2', 'Cama 01', 30.00, 'Ing. Carlos Mendoza'),
    ('F1-Z2-B01-C01', 1, 'Zona Sur',   'Bloque 1', 'Cama 01', 35.00, 'Ing. Diana Paredes'),
    
    -- Finca 2: El Rosal
    ('F2-Z1-B01-C01', 2, 'Zona Este',  'Bloque 1', 'Cama 01', 30.00, 'Ing. Roberto Silva'),
    ('F2-Z1-B01-C02', 2, 'Zona Este',  'Bloque 1', 'Cama 02', 30.00, 'Ing. Roberto Silva'),
    ('F2-Z2-B01-C01', 2, 'Zona Oeste', 'Bloque 1', 'Cama 01', 28.00, 'Ing. Lucía Gómez'),

    -- Finca 3: Bella Vista
    ('F3-Z1-B01-C01', 3, 'Zona Central', 'Bloque 1', 'Cama 01', 31.00, 'Ing. Mateo Torres'),
    ('F3-Z1-B01-C02', 3, 'Zona Central', 'Bloque 1', 'Cama 02', 31.00, 'Ing. Mateo Torres'),
    ('F3-Z1-B02-C01', 3, 'Zona Central', 'Bloque 2', 'Cama 01', 33.00, 'Ing. Mateo Torres')
ON CONFLICT (id_cama) DO NOTHING;


-- 3. POBLAR DIM_CALENDARIO_AGRICOLA (Años 2024 al 2027)
-- Generamos dinámicamente cada día y calculamos:
--   - semana_campo: año (dos dígitos) + semana del año a dos dígitos (ej: 2638)
--   - semana_abs: entero continuo ininterrumpido (base semana 1 del 2024 = semana_abs 1001)
INSERT INTO dim_calendario_agricola (fecha, fecha_inicio_semana, fecha_fin_semana, semana_campo, semana_abs, anio, semana_del_anio)
SELECT 
    d::DATE AS fecha,
    date_trunc('week', d)::DATE AS fecha_inicio_semana,
    (date_trunc('week', d) + interval '6 days')::DATE AS fecha_fin_semana,
    -- Formato año 2 dígitos + semana ISO (ej. 2026 semana 38 -> 2638)
    ((EXTRACT(ISOYEAR FROM d)::INT % 100) * 100 + EXTRACT(WEEK FROM d)::INT) AS semana_campo,
    -- Semana absoluta continua: número de semanas enteras transcurridas desde el 1 de enero de 2020 + 1000
    (1000 + FLOOR((d::DATE - '2020-01-06'::DATE) / 7))::INT AS semana_abs,
    EXTRACT(ISOYEAR FROM d)::INT AS anio,
    EXTRACT(WEEK FROM d)::INT AS semana_del_anio
FROM generate_series('2024-01-01'::DATE, '2027-12-31'::DATE, '1 day'::interval) AS d
ON CONFLICT (fecha) DO NOTHING;


-- 4. POBLAR DIM_RECETAS_FENOLOGICAS
-- Catálogo de dosis de agroquímicos por variedad, edad de cultivo (en semanas) y m2.
INSERT INTO dim_recetas_fenologicas (variedad, edad_semanas, etapa_fenologica, producto, tipo_producto, dosis_por_m2, unidad_medida)
VALUES
    -- VARIEDAD FREEDOM
    -- Edad 0 a 4 (Enraizamiento y brotación inicial)
    ('Freedom', 0, 'ENRAIZAMIENTO', 'Enraizador Raizex',    'FERTILIZANTE', 1.5000, 'ml'),
    ('Freedom', 2, 'ENRAIZAMIENTO', 'Fungicida Preventivo Mancozeb', 'FUNGICIDA', 0.8000, 'gr'),
    ('Freedom', 4, 'VEGETATIVO',    'Nitrato de Calcio',     'FERTILIZANTE', 2.0000, 'gr'),
    -- Edad 10 a 20 (Crecimiento de tallos)
    ('Freedom', 12, 'VEGETATIVO',   'Fosfato Monopotásico',  'FERTILIZANTE', 1.8000, 'gr'),
    ('Freedom', 15, 'VEGETATIVO',   'Insecticida Neem Plus', 'INSECTICIDA',  0.5000, 'ml'),
    -- Edad 38 a 40 (Formación y apertura de botón floral - corte programado)
    ('Freedom', 38, 'BOTON FLORAL', 'Fungicida Botrytis Stop','FUNGICIDA',   1.2000, 'ml'),
    ('Freedom', 40, 'CORTE/COSECHA','Sulfato de Potasio',    'FERTILIZANTE', 2.5000, 'gr'),
    ('Freedom', 40, 'CORTE/COSECHA','Fungicida Pre-Cosecha', 'FUNGICIDA',    1.0000, 'ml'),

    -- VARIEDAD EXPLORER
    ('Explorer', 0, 'ENRAIZAMIENTO', 'Enraizador Raizex',    'FERTILIZANTE', 1.6000, 'ml'),
    ('Explorer', 4, 'VEGETATIVO',    'Nitrato de Calcio',     'FERTILIZANTE', 2.2000, 'gr'),
    ('Explorer', 15, 'VEGETATIVO',   'Insecticida Neem Plus', 'INSECTICIDA',  0.6000, 'ml'),
    ('Explorer', 40, 'CORTE/COSECHA','Sulfato de Potasio',    'FERTILIZANTE', 2.8000, 'gr'),
    ('Explorer', 40, 'CORTE/COSECHA','Fungicida Botrytis Stop','FUNGICIDA',  1.4000, 'ml'),

    -- VARIEDAD MONDIAL
    ('Mondial', 0, 'ENRAIZAMIENTO', 'Enraizador Raizex',     'FERTILIZANTE', 1.4000, 'ml'),
    ('Mondial', 4, 'VEGETATIVO',    'Nitrato de Calcio',      'FERTILIZANTE', 1.9000, 'gr'),
    ('Mondial', 40, 'CORTE/COSECHA','Sulfato de Potasio',     'FERTILIZANTE', 2.4000, 'gr'),
    ('Mondial', 40, 'CORTE/COSECHA','Fungicida Botrytis Stop','FUNGICIDA',   1.1000, 'ml')
ON CONFLICT (variedad, edad_semanas, producto) DO UPDATE
SET dosis_por_m2 = EXCLUDED.dosis_por_m2, etapa_fenologica = EXCLUDED.etapa_fenologica;
