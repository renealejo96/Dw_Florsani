#!/usr/bin/env bash
# ==============================================================================
# PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
# ARCHIVO: actualizar_vps.sh
# DESCRIPCIÓN: Aplica cambios de código, plantillas y catálogos SIN sobreescribir la base de datos
# ==============================================================================
set -e

echo "=========================================================="
echo "🚀 Actualizando Florsani ERP en VPS (Preservando Base de Datos)..."
echo "=========================================================="

echo "📂 Copiando catálogos CSV al contenedor dw_florsani_app..."
docker exec dw_florsani_app mkdir -p /app/data/cosechas
docker cp data/cosechas/. dw_florsani_app:/app/data/cosechas/

echo "🔄 Copiando plantillas y código actualizado..."
docker cp app/. dw_florsani_app:/app/app/
docker cp scripts/. dw_florsani_app:/app/scripts/

echo "🔗 Sincronizando y homologando catálogos en PostgreSQL..."
docker exec dw_florsani_app python scripts/migrar_catalogos_a_bd.py

echo "🚀 Reiniciando servicio web dw_florsani_app..."
docker restart dw_florsani_app

echo ""
echo "=========================================================="
echo "🎉 ACTUALIZACIÓN APLICADA CON ÉXITO EN TU VPS:"
echo "   ✅ Tarjetas masivas removidas de 'Registros de Cosecha' (vista limpia y directa)."
echo "   ✅ SKYLER DUO CRISTAL corregido a SKYLER DUO CRYSTAL en todos los registros históricos."
echo "   ✅ Catálogo general en PostgreSQL homologado y activo."
echo "   ✅ Tus nuevos estimados y datos quedan 100% seguros y permanentes."
echo "=========================================================="
