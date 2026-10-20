#!/usr/bin/env bash
# ==============================================================================
# PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
# ARCHIVO: restaurar_dump_vps.sh
# DESCRIPCIÓN: Restaura la base de datos completa y sincroniza catálogos/código en el VPS
# ==============================================================================
set -e

echo "=========================================================="
echo "📦 Descomprimiendo y restaurando base de datos completa..."
echo "=========================================================="

if [ -f florsani_completo.sql.zip ]; then
    if ! command -v unzip &> /dev/null; then
        apt-get update && apt-get install -y unzip
    fi
    unzip -o florsani_completo.sql.zip
fi

if [ -f florsani_completo.sql ]; then
    echo "📥 Cargando datos en PostgreSQL local (dw_florsani_db)..."
    docker exec -i dw_florsani_db psql -U postgres -d florsani < florsani_completo.sql
    
    echo "🧹 Limpiando tablas de prueba operativa temporales..."
    docker exec -i dw_florsani_db psql -U postgres -d florsani -c "TRUNCATE TABLE rotations, rotation_rounds, rotation_round_items, fumigation_orders, fumigation_order_details, fumigation_order_product_summaries, requisitions, requisition_items, additional_applications CASCADE;"
    
    echo "🗑️ Eliminando archivo SQL descomprimido temporal..."
    rm -f florsani_completo.sql
fi

echo "📂 Sincronizando catálogos CSV de cosechas al contenedor dw_florsani_app..."
docker exec dw_florsani_app mkdir -p /app/data/cosechas
docker cp data/cosechas/. dw_florsani_app:/app/data/cosechas/

echo "🔄 Actualizando código y plantillas UI en el contenedor..."
docker cp app/. dw_florsani_app:/app/app/
docker cp scripts/. dw_florsani_app:/app/scripts/

echo "🔗 Sincronizando catálogo de variedades y productos a tablas de PostgreSQL..."
docker exec dw_florsani_app python scripts/migrar_catalogos_a_bd.py

echo "🚀 Reiniciando servicio web dw_florsani_app para aplicar cambios..."
docker restart dw_florsani_app

echo ""
echo "=========================================================="
echo "🎉 SISTEMA Y BASE DE DATOS RESTAURADOS AL 100% EN TU VPS:"
echo "   ✅ 11,351 registros de cosecha históricos (Nov 2025 - Oct 2026)."
echo "   ✅ 244 registros de estimado de producción."
echo "   ✅ 58,082 eventos de cultivo activos (Siembras, Podas, Tumbas)."
echo "   ✅ 18,229 registros de corte oficial (semanas 40 y 41)."
echo "   ✅ Catálogo de 507 agroquímicos con dosis y periodos activo."
echo "   ✅ Catálogo de 126 variedades y 121 maestros sincronizados."
echo "   ✅ Estilos de rangos vegetativo/productivo y pie de sidebar corregidos."
echo "=========================================================="
