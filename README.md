# Florsani DW - Arquitectura Event Sourcing & Backend Analítico

Sistema analítico y transaccional para la gestión de cultivo florícola (3 fincas, 330 hectáreas). Reemplaza el archivo Excel maestro tradicional por un modelo inmutable de **Event Sourcing** con cálculo dinámico de edad, detección automática de podas y tumbas.

---

## 📁 Estructura del Proyecto en VS Code

```
dw_Florsani/
│
├── etl_estado_cultivo.py              # ⭐ SCRIPT PRINCIPAL: Procesa Excel histórico y carga PostgreSQL
│
├── estados de cultivo/                # Carpeta con los 90 archivos semanales (2025-01 a 2026-38)
│   ├── Estado Cultivo PYGAN 2025-01.xlsx
│   └── ...
│
├── sql/
│   ├── 01_schema.sql                  # DDL de dimensiones base (calendario, fincas, recetas)
│   ├── 02_seed_dimensions.sql         # Datos semilla (fincas y calendario)
│   └── 04_update_schema_real.sql      # DDL de producción (dim_bloques, dim_camas, fact_eventos_cultivo)
│
└── README.md
```

---

## 🗄️ Diccionario de Tablas en PostgreSQL (`florsani`)

En **DBeaver** verás únicamente las tablas oficiales y limpias:

| Tabla / Vista | Tipo | Filas | ¿Para qué sirve? |
| :--- | :---: | :---: | :--- |
| **`dim_bloques`** | Tabla | 512 | **Infraestructura física pura por bloque:** metros netos, metros brutos, camas físicas, camas estándar, técnico de riego y zona. |
| **`dim_camas`** | Tabla | 9,200 | **Detalle cama a cama:** código de cama, largo, metros netos y brutos. |
| **`fact_eventos_cultivo`** | Tabla | 57,198 | **Historial inmutable de transacciones:** Siembras, Podas (1..6) y Tumbas con sus fechas exactas y variedad. |
| **`dim_calendario_agricola`** | Tabla | 1,461 | Mapeo de fecha a Semana Agrícola de Campo (ej. 2638) y Semana Absoluta continua. |
| **`dim_fincas`** | Tabla | 3 | Catálogo maestro de las empresas del grupo. |
| **`dim_recetas_fenologicas`** | Tabla | 17 | Dosis de agroquímicos por variedad, edad de cultivo en semanas y m². |
| **`mrt_estado_cultivo_actual`** | Vista | 9,200 | **El reemplazo del Excel:** Muestra el estado vivo actual de cada cama, variedad actual y edad calculada dinámicamente. |
| **`stg_eventos`** | Vista | 57,198 | Vista de eventos limpios filtrando registros anulados por error humano. |

---

## 🚀 Cómo Ejecutar el Script Principal

Abre la terminal en VS Code y ejecuta:

```bash
python etl_estado_cultivo.py
```
