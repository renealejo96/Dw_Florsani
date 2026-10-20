#!/usr/bin/env bash
# ==============================================================================
# PROYECTO: ERP Y DATA WAREHOUSE AGRÍCOLA - GRUPO FLORICULTOR FLORSANI
# ARCHIVO: deploy_vps.sh
# DESCRIPCIÓN: Script para iniciar o actualizar la aplicación en el VPS Hostinger
# ==============================================================================
set -e

echo "=========================================================="
echo "🌱 Desplegando ERP Florsani en VPS Ubuntu (Hostinger KVM 4)"
echo "=========================================================="

# 1. Obtener últimos cambios de GitHub si es repositorio git
if [ -d .git ]; then
    echo "🔄 Descargando últimos cambios desde GitHub (main)..."
    git pull origin main
fi

# 2. Asegurar directorios de persistencia
mkdir -p backups uploads

# 3. Verificar archivo de entorno
if [ ! -f .env.production ]; then
    echo "⚠️  No se encontró .env.production. Creando a partir de .env.production.example..."
    cp .env.production.example .env.production
fi

# 3. Construir y levantar contenedores en segundo plano
echo "📦 Construyendo y levantando contenedores con Docker Compose..."
docker compose --env-file .env.production up -d --build

# 4. Mostrar estado de los contenedores
echo ""
echo "📊 Estado de los contenedores:"
docker compose ps

echo ""
echo "=========================================================="
echo "✅ Despliegue completado con éxito."
echo "🔹 Web App escuchando internamente en:    http://127.0.0.1:8095"
echo "🔹 PostgreSQL escuchando internamente en: 127.0.0.1:5438"
echo "=========================================================="
