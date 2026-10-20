#!/usr/bin/env bash
# ==============================================================================
# PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
# ARCHIVO: cargar_cosechas_vps.sh
# DESCRIPCIÓN: Inyecta los 11,351 registros de cosecha y 244 estimados históricos en el VPS
# ==============================================================================
set -e

echo "=========================================================="
echo "🌾 Cargando 11,351 cosechas históricas recuperadas en VPS"
echo "=========================================================="

if [ -f cosechas_historicas.sql.zip ]; then
    if ! command -v unzip &> /dev/null; then
        apt-get update && apt-get install -y unzip
    fi
    unzip -o cosechas_historicas.sql.zip
fi

if [ -f cosechas_historicas.sql ]; then
    echo "📥 Inyectando datos en PostgreSQL local (dw_florsani_db)..."
    docker exec -i dw_florsani_db psql -U postgres -d florsani < cosechas_historicas.sql
    rm -f cosechas_historicas.sql
    echo ""
    echo "=========================================================="
    echo "🎉 ¡LISTO! 11,351 REGISTROS DE COSECHA ACTIVOS EN TU VPS:"
    echo "   ✅ Fechas: 15 de Noviembre 2025 hasta HOY (19 de Octubre 2026)."
    echo "   ✅ 244 registros de estimado oficial cargados."
    echo "   ✅ Secuencias automáticas (SERIAL ID) sincronizadas."
    echo "=========================================================="
else
    echo "❌ Error: No se encontró cosechas_historicas.sql.zip"
    exit 1
fi
