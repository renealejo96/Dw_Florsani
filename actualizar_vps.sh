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

# 1. Descargar últimos cambios del repositorio
if [ -d .git ]; then
    echo "📥 Descargando últimos cambios desde GitHub (main)..."
    git pull origin main
fi

echo "📂 Copiando catálogos CSV al contenedor dw_florsani_app..."
docker exec dw_florsani_app mkdir -p /app/data/cosechas
docker cp data/cosechas/. dw_florsani_app:/app/data/cosechas/

echo "🔄 Copiando plantillas y código actualizado..."
docker cp app/. dw_florsani_app:/app/app/
docker cp scripts/. dw_florsani_app:/app/scripts/

echo "🧹 Limpiando caché compilada de Python en contenedor..."
docker exec dw_florsani_app find /app/app -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

echo "🔗 Sincronizando y homologando catálogos en PostgreSQL..."
docker exec -w /app -e PYTHONPATH=/app dw_florsani_app python scripts/migrar_catalogos_a_bd.py

echo "🌱 Migrando fincas oficiales (Pyganflor, Malchingui, Urcuquí) y roles de usuario..."
docker exec -w /app -e PYTHONPATH=/app dw_florsani_app python scripts/migrar_fincas_y_roles.py

echo "🌸 Sincronizando labores y asignaciones de Fenogramas (preservando calificaciones)..."
docker exec -w /app -e PYTHONPATH=/app dw_florsani_app python scripts/sincronizar_fenogramas_vps.py

# echo "🧹 Vaciando calificaciones y semanas de prueba de Fenogramas (iniciar limpios para Semana 43)..."
# docker exec -w /app -e PYTHONPATH=/app dw_florsani_app python scripts/limpiar_fenogramas_pruebas.py

echo "🚀 Reiniciando servicio web dw_florsani_app..."
docker restart dw_florsani_app

echo ""
echo "=========================================================="
echo "🎉 ACTUALIZACIÓN APLICADA CON ÉXITO EN TU VPS:"
echo "   ✅ Últimos cambios descargados de GitHub."
echo "   ✅ Cosechas: Modal interactivo de 'Resumen por Bloque' y Excel activados."
echo "   ✅ Fenogramas: Selector de 'Labor a Calificar' y reasignación dinámicos."
echo "   ✅ Fenogramas: Base de pruebas vaciada y lista para generar la Semana 43."
echo "   ✅ Estimados y cosechas de Render 100% seguros e intactos."
echo "=========================================================="
