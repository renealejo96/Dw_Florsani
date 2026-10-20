-- ==============================================================================
-- PROYECTO: DATA WAREHOUSE & BACKEND TRANSACCIONAL - GRUPO FLORICULTOR FLORSANI
-- ARCHIVO: 03_analytical_models.sql
-- DESCRIPCIÓN: Vistas y Transformaciones Analíticas (Simulación de Modelos dbt)
-- ==============================================================================

-- ==============================================================================
-- 1. MODELO STAGING: stg_eventos
-- Capa de limpieza inicial. Filtra estrictamente eventos anulados (Soft Deletes)
-- para que los errores humanos corregidos no contaminen el análisis.
-- ==============================================================================
CREATE OR REPLACE VIEW stg_eventos AS
SELECT 
    id_evento,
    id_cama,
    fecha_evento,
    tipo_evento,
    variedad,
    estado_evento,
    usuario_registro,
    created_at
FROM fact_eventos_cultivo
WHERE estado_evento = 'ACTIVO';


-- ==============================================================================
-- 2. MODELO MART: mrt_estado_cultivo_actual
-- Este modelo REEMPLAZA al archivo Excel maestro tradicional.
--
-- LÓGICA DE VENTANA (Window Function):
-- Usamos: ROW_NUMBER() OVER(PARTITION BY id_cama ORDER BY fecha_evento DESC, id_evento DESC)
-- 1. PARTITION BY id_cama: Separa el historial evento por evento de cada cama individual.
-- 2. ORDER BY fecha_evento DESC: Ordena de más reciente a más antiguo.
-- 3. ROW_NUMBER(): Asigna 1 al evento más reciente de cada cama.
-- Al filtrar "WHERE fila_num = 1", garantizamos obtener el "Estado Vivo" actual de cada cama.
-- ==============================================================================
CREATE OR REPLACE VIEW mrt_estado_cultivo_actual AS
WITH ultimos_eventos AS (
    SELECT 
        e.id_evento,
        e.id_cama,
        e.fecha_evento,
        e.tipo_evento,
        e.variedad,
        ROW_NUMBER() OVER(
            PARTITION BY e.id_cama 
            ORDER BY e.fecha_evento DESC, e.id_evento DESC
        ) AS fila_num
    FROM stg_eventos e
),
semana_hoy AS (
    -- Obtiene la semana absoluta continua correspondiente a la fecha actual
    SELECT semana_abs AS semana_abs_hoy, semana_campo AS semana_campo_hoy
    FROM dim_calendario_agricola
    WHERE fecha = CURRENT_DATE
    LIMIT 1
)
SELECT 
    -- 1. Identificación y Empresa (Multi-Finca)
    f.id_finca,
    f.nombre_finca,
    
    -- 2. Atributos Físicos de la Cama
    c.id_cama,
    c.zona,
    c.bloque,
    c.numero_cama,
    c.metros_cuadrados,
    c.tecnico_asignado,
    
    -- 3. Estado Agronómico Actual
    ue.tipo_evento AS ultimo_evento,
    ue.fecha_evento AS fecha_ultimo_evento,
    cal_ev.semana_campo AS semana_campo_ultimo_evento,
    ue.variedad,
    
    -- 4. Cálculo Dinámico de la Edad del Cultivo
    -- REGLA DE NEGOCIO: Si el último evento fue 'TUMBA' (erradicación), la cama está vacía (edad = NULL)
    -- Si es 'SIEMBRA' o 'PODA', la edad es la diferencia en semanas absolutas
    CASE 
        WHEN ue.tipo_evento = 'TUMBA' THEN NULL
        ELSE (sh.semana_abs_hoy - cal_ev.semana_abs)
    END AS edad_semanas_actual,

    -- Estado descriptivo amigable para gerencia y Power BI
    CASE 
        WHEN ue.tipo_evento IS NULL THEN 'SIN EVENTOS REGISTRADOS'
        WHEN ue.tipo_evento = 'TUMBA' THEN 'ERRADICADO / DISPONIBLE PARA SIEMBRA'
        WHEN ue.tipo_evento = 'PODA' THEN 'CULTIVO EN REBROTE (PODADO)'
        WHEN ue.tipo_evento = 'SIEMBRA' THEN 'CULTIVO NUEVO (SIEMBRA)'
        ELSE 'EN PRODUCCIÓN'
    END AS estado_cultivo,
    
    sh.semana_campo_hoy AS semana_agricola_actual

FROM dim_camas c
INNER JOIN dim_fincas f 
    ON c.id_finca = f.id_finca
LEFT JOIN ultimos_eventos ue 
    ON c.id_cama = ue.id_cama AND ue.fila_num = 1
LEFT JOIN dim_calendario_agricola cal_ev 
    ON ue.fecha_evento = cal_ev.fecha
CROSS JOIN semana_hoy sh;


-- ==============================================================================
-- 3. CONSULTA DE PROYECCIÓN DE COMPRAS: SEMANA AGRÍCOLA 2640 (Semana 40)
-- Permite a gerencia y compras "viajar en el tiempo" a una semana futura.
-- Calcula qué edad tendrá cada cama en esa semana y la cruza con la receta
-- fenológica correspondiente para consolidar los requerimientos de agroquímicos por Finca.
-- ==============================================================================
WITH target_week AS (
    -- Parámetro: Semana objetivo a proyectar (Semana 40 del 2026 -> 2640)
    SELECT DISTINCT semana_abs AS semana_abs_objetivo, semana_campo
    FROM dim_calendario_agricola
    WHERE semana_campo = 2640
    LIMIT 1
),
camas_proyectadas AS (
    SELECT 
        m.id_finca,
        m.nombre_finca,
        m.id_cama,
        m.metros_cuadrados,
        m.variedad,
        m.ultimo_evento,
        -- Edad proyectada en la semana objetivo
        CASE 
            WHEN m.ultimo_evento = 'TUMBA' THEN NULL
            ELSE (tw.semana_abs_objetivo - cal_ev.semana_abs)
        END AS edad_proyectada_semanas,
        tw.semana_campo AS semana_proyectada
    FROM mrt_estado_cultivo_actual m
    LEFT JOIN dim_calendario_agricola cal_ev 
        ON m.fecha_ultimo_evento = cal_ev.fecha
    CROSS JOIN target_week tw
    WHERE m.ultimo_evento IS NOT NULL AND m.ultimo_evento <> 'TUMBA'
)
SELECT 
    cp.id_finca,
    cp.nombre_finca,
    cp.semana_proyectada,
    r.producto,
    r.tipo_producto,
    r.etapa_fenologica,
    r.unidad_medida,
    COUNT(DISTINCT cp.id_cama) AS total_camas_aplicadas,
    SUM(cp.metros_cuadrados) AS total_m2_a_tratar,
    -- Dosis total requerida = metros cuadrados de las camas * dosis por m2 según receta
    ROUND(SUM(cp.metros_cuadrados * r.dosis_por_m2), 2) AS cantidad_total_a_comprar
FROM camas_proyectadas cp
INNER JOIN dim_recetas_fenologicas r 
    ON cp.variedad = r.variedad 
   AND cp.edad_proyectada_semanas = r.edad_semanas
GROUP BY 
    cp.id_finca,
    cp.nombre_finca,
    cp.semana_proyectada,
    r.producto,
    r.tipo_producto,
    r.etapa_fenologica,
    r.unidad_medida
ORDER BY 
    cp.nombre_finca, 
    r.tipo_producto, 
    r.producto;
