#!/usr/bin/env bash
# ==============================================================================
# PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
# ARCHIVO: migrar_vps.sh
# DESCRIPCIÓN: Script automatizado para migrar datos desde Render a PostgreSQL en VPS
# ==============================================================================
set -e

SOURCE_URL="${1:-postgresql://rene:BlH0HemPSdcwy8H1AsXEyKp84Dnzzt4A@dpg-d3ttvi7diees73driljg-a.oregon-postgres.render.com/pyganflor?sslmode=require}"
TARGET_URL="${2:-postgresql://postgres:florsani_prod_secure_2026@db:5432/florsani}"

echo "=========================================================="
echo "🚀 Iniciando migración de datos limpios a PostgreSQL en VPS"
echo "=========================================================="

docker exec -it dw_florsani_app python scripts/migrar_a_produccion_limpia.py \
  --source "$SOURCE_URL" \
  --target "$TARGET_URL"

echo ""
echo "=========================================================="
echo "✅ Base de datos local del VPS cargada y lista para producción."
echo "=========================================================="
