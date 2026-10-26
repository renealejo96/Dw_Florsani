-- ==============================================================================
-- PROYECTO: DATA WAREHOUSE & BACKEND TRANSACCIONAL - GRUPO FLORICULTOR FLORSANI
-- ARCHIVO: 05_fix_semanas_evaluacion_and_cosechas.sql
-- DESCRIPCIÓN: Blindaje de restricciones PK en semanas_evaluacion y homologación de cosechas
-- ==============================================================================

-- 1. Asegurar Primary Key en semanas_evaluacion para evitar errores en ON CONFLICT
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conrelid = 'semanas_evaluacion'::regclass AND contype = 'p'
    ) THEN
        ALTER TABLE semanas_evaluacion ADD CONSTRAINT pk_semanas_evaluacion PRIMARY KEY (semana_anio, anio);
    END IF;
END $$;

-- 2. Corregir cualquier registro_cosecha con semana en formato 4 dígitos o desincronizada de la fecha real
UPDATE registro_cosecha
SET semana = semana % 100
WHERE semana >= 100;

UPDATE registro_cosecha
SET semana = EXTRACT(WEEK FROM fecha)::INTEGER
WHERE fecha IS NOT NULL AND semana != EXTRACT(WEEK FROM fecha)::INTEGER;
