-- ==============================================================================
-- PROYECTO: DATA WAREHOUSE & BACKEND TRANSACCIONAL - GRUPO FLORICULTOR FLORSANI
-- ARCHIVO: 04_update_schema_real.sql
-- DESCRIPCIÓN: Esquema adaptado a la estructura real de producción de Florsani/Pygan
-- ==============================================================================

-- 1. TABLA DIM_BLOQUES (Catálogo maestro por bloque con agregaciones físicas)
CREATE TABLE IF NOT EXISTS dim_bloques (
    codigo_bloque           VARCHAR(20) PRIMARY KEY, -- Ej: '38C', '11A'
    id_finca                INTEGER NOT NULL REFERENCES dim_fincas(id_finca) DEFAULT 1,
    blq                     VARCHAR(10) NOT NULL,    -- Ej: '38', '11'
    sufijo                  VARCHAR(10) NULL,        -- Ej: 'C', 'A'
    zona                    VARCHAR(50) NOT NULL,    -- Ej: 'ZONA3', 'VIRGINIA'
    tecnico_de_riego        VARCHAR(50) NULL,        -- Ej: 'C1', 'C2'
    total_camas_fisicas     INTEGER NOT NULL DEFAULT 0,
    total_camas_estandar    NUMERIC(10, 4) NOT NULL DEFAULT 0,
    area_neta_m2            NUMERIC(10, 2) NOT NULL DEFAULT 0,
    area_bruta_m2           NUMERIC(10, 2) NOT NULL DEFAULT 0,
    ha                      NUMERIC(10, 4) NOT NULL DEFAULT 0,
    updated_at              TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bloques_zona ON dim_bloques(zona);
CREATE INDEX IF NOT EXISTS idx_bloques_tecnico ON dim_bloques(tecnico_de_riego);


-- 2. TABLA DIM_CAMAS_REAL (Catálogo físico cama a cama detallado)
CREATE TABLE IF NOT EXISTS dim_camas_real (
    id_cama                 VARCHAR(50) PRIMARY KEY, -- Ej: '38C-A-C1', '36A-A-C27_1'
    codigo_bloque           VARCHAR(20) NOT NULL REFERENCES dim_bloques(codigo_bloque) ON UPDATE CASCADE,
    id_finca                INTEGER NOT NULL REFERENCES dim_fincas(id_finca) DEFAULT 1,
    blq                     VARCHAR(10) NOT NULL,
    sufijo                  VARCHAR(10) NULL,
    num_cama                INTEGER NOT NULL,
    largo_de_cama           NUMERIC(8, 2) NULL,
    cama_estandar           NUMERIC(8, 4) NOT NULL DEFAULT 0,
    metros_netos            NUMERIC(8, 2) NOT NULL DEFAULT 0,
    metros_brutos           NUMERIC(8, 2) NOT NULL DEFAULT 0,
    ha                      NUMERIC(10, 6) NOT NULL DEFAULT 0,
    zona                    VARCHAR(50) NOT NULL,
    tecnico_de_riego        VARCHAR(50) NULL,
    created_at              TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_camas_real_bloque ON dim_camas_real(codigo_bloque);
CREATE INDEX IF NOT EXISTS idx_camas_real_num ON dim_camas_real(num_cama);


-- 3. FACT_EVENTOS_CULTIVO_REAL (Event Sourcing Histórico con ciclo y número de poda)
CREATE TABLE IF NOT EXISTS fact_eventos_cultivo_real (
    id_evento               BIGSERIAL PRIMARY KEY,
    id_cama                 VARCHAR(50) NOT NULL REFERENCES dim_camas_real(id_cama) ON UPDATE CASCADE,
    codigo_bloque           VARCHAR(20) NOT NULL,
    fecha_evento            DATE NOT NULL,
    semana_campo            INTEGER NULL,             -- Ej: 2421, 2515, 2621
    tipo_evento             VARCHAR(20) NOT NULL CHECK (tipo_evento IN ('SIEMBRA', 'PODA', 'TUMBA')),
    num_poda                INTEGER NULL,             -- 0 para Siembra, 1, 2, 3, 4... para Podas, NULL para Tumba
    producto_maestro        VARCHAR(100) NULL,
    variedad                VARCHAR(100) NOT NULL,
    origen_evento           VARCHAR(100) NULL,        -- Archivo semanal de origen
    estado_evento           VARCHAR(20) NOT NULL DEFAULT 'ACTIVO' CHECK (estado_evento IN ('ACTIVO', 'ANULADO')),
    fecha_anulacion         TIMESTAMP WITH TIME ZONE NULL,
    motivo_anulacion        TEXT NULL,
    created_at              TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_evento_cama_tipo_fecha UNIQUE (id_cama, tipo_evento, fecha_evento, num_poda)
);

CREATE INDEX IF NOT EXISTS idx_fact_real_cama_fecha ON fact_eventos_cultivo_real(id_cama, fecha_evento DESC);
CREATE INDEX IF NOT EXISTS idx_fact_real_bloque ON fact_eventos_cultivo_real(codigo_bloque);
CREATE INDEX IF NOT EXISTS idx_fact_real_tipo ON fact_eventos_cultivo_real(tipo_evento);
