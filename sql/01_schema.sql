-- ==============================================================================
-- PROYECTO: DATA WAREHOUSE & BACKEND TRANSACCIONAL - GRUPO FLORICULTOR FLORSANI
-- ARCHIVO: 01_schema.sql
-- DESCRIPCIÓN: Definición de Esquema DDL en PostgreSQL
-- ==============================================================================

-- 1. DIMENSIÓN CALENDARIO AGRÍCOLA
-- Mapea fechas normales del calendario gregoriano a Semanas Agrícolas.
-- semana_campo: Formato año-semana de negocio (ej. 2638 = Año 2026, Semana 38).
-- semana_abs: Entero secuencial ininterrumpido (1001, 1002, 1003...). 
--             Permite calcular diferencias de semanas simplemente con: (semana_futura - semana_origen).
CREATE TABLE IF NOT EXISTS dim_calendario_agricola (
    id_calendario       SERIAL PRIMARY KEY,
    fecha               DATE NOT NULL UNIQUE,
    fecha_inicio_semana DATE NOT NULL,
    fecha_fin_semana    DATE NOT NULL,
    semana_campo        INTEGER NOT NULL, -- Ej: 2638
    semana_abs          INTEGER NOT NULL, -- Entero secuencial (ej: 1038)
    anio                INTEGER NOT NULL,
    semana_del_anio     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cal_fecha ON dim_calendario_agricola(fecha);
CREATE INDEX IF NOT EXISTS idx_cal_semana_campo ON dim_calendario_agricola(semana_campo);
CREATE INDEX IF NOT EXISTS idx_cal_semana_abs ON dim_calendario_agricola(semana_abs);


-- 2. DIMENSIÓN FINCAS (Empresas del Grupo Floricultor)
-- Catálogo maestro de las 3 fincas que componen las 330 hectáreas.
CREATE TABLE IF NOT EXISTS dim_fincas (
    id_finca            SERIAL PRIMARY KEY,
    codigo_finca        VARCHAR(10) NOT NULL UNIQUE, -- Ej: 'FN01', 'FN02', 'FN03'
    nombre_finca        VARCHAR(100) NOT NULL UNIQUE,
    hectareas           NUMERIC(8, 2) NOT NULL CHECK (hectareas > 0),
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);


-- 3. DIMENSIÓN CAMAS (Infraestructura Física Inmutable)
-- Las camas son divisiones físicas del terreno que nunca cambian de ubicación.
-- Tienen una relación obligatoria (FOREIGN KEY) con dim_fincas.
CREATE TABLE IF NOT EXISTS dim_camas (
    id_cama             VARCHAR(50) PRIMARY KEY, -- Identificador único (ej: 'F1-Z1-B01-C001')
    id_finca            INTEGER NOT NULL REFERENCES dim_fincas(id_finca) ON UPDATE CASCADE ON DELETE RESTRICT,
    zona                VARCHAR(50) NOT NULL,
    bloque              VARCHAR(50) NOT NULL,
    numero_cama         VARCHAR(20) NOT NULL,
    metros_cuadrados    NUMERIC(8, 2) NOT NULL DEFAULT 30.00 CHECK (metros_cuadrados > 0),
    tecnico_asignado    VARCHAR(100),
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_camas_finca ON dim_camas(id_finca);


-- 4. FACT TABLE: EVENTOS DE CULTIVO (Event Sourcing Inmutable)
-- Cada acción agronómica en una cama es un hecho inmutable. NUNCA se sobrescribe ni se elimina.
-- Si hay un error de digitación, se anula lógicamente (Soft Delete) cambiando estado_evento = 'ANULADO'.
CREATE TABLE IF NOT EXISTS fact_eventos_cultivo (
    id_evento           BIGSERIAL PRIMARY KEY,
    id_cama             VARCHAR(50) NOT NULL REFERENCES dim_camas(id_cama) ON UPDATE CASCADE ON DELETE RESTRICT,
    fecha_evento        DATE NOT NULL,
    tipo_evento         VARCHAR(20) NOT NULL CHECK (tipo_evento IN ('SIEMBRA', 'PODA', 'TUMBA')),
    variedad            VARCHAR(100) NOT NULL,
    estado_evento       VARCHAR(20) NOT NULL DEFAULT 'ACTIVO' CHECK (estado_evento IN ('ACTIVO', 'ANULADO')),
    fecha_anulacion     TIMESTAMP WITH TIME ZONE NULL,
    motivo_anulacion    TEXT NULL,
    usuario_registro    VARCHAR(50) DEFAULT CURRENT_USER,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fact_eventos_cama_fecha ON fact_eventos_cultivo(id_cama, fecha_evento DESC);
CREATE INDEX IF NOT EXISTS idx_fact_eventos_estado ON fact_eventos_cultivo(estado_evento);


-- 5. DIMENSIÓN RECETAS FENOLÓGICAS (Planificación de Agroquímicos)
-- Dosis requeridas de agroquímicos según variedad, edad del cultivo en semanas y m2 de la cama.
CREATE TABLE IF NOT EXISTS dim_recetas_fenologicas (
    id_receta           SERIAL PRIMARY KEY,
    variedad            VARCHAR(100) NOT NULL,
    edad_semanas        INTEGER NOT NULL CHECK (edad_semanas >= 0),
    etapa_fenologica    VARCHAR(50) NOT NULL, -- Ej: 'ENRAIZAMIENTO', 'VEGETATIVO', 'BOTON FLORAL'
    producto            VARCHAR(100) NOT NULL, -- Ej: 'Nitrato de Calcio', 'Fungicida Mancozeb'
    tipo_producto       VARCHAR(50) NOT NULL, -- 'FERTILIZANTE', 'FUNGICIDA', 'INSECTICIDA'
    dosis_por_m2        NUMERIC(10, 4) NOT NULL CHECK (dosis_por_m2 > 0),
    unidad_medida       VARCHAR(20) NOT NULL, -- 'ml', 'gr', 'cc'
    CONSTRAINT uq_receta_variedad_edad_prod UNIQUE (variedad, edad_semanas, producto)
);

CREATE INDEX IF NOT EXISTS idx_recetas_variedad_edad ON dim_recetas_fenologicas(variedad, edad_semanas);
