# Dockerfile optimizado para Producción (Hostinger VPS & Cloud Run)
FROM python:3.11-slim

# Evitar buffers de stdout/stderr para que los logs lleguen inmediatos
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8095

WORKDIR /app

# Instalar dependencias del sistema requeridas para psycopg2 y librerias nativas
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar requerimientos de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código de la aplicación
COPY . .

# Exponer el puerto de producción (8095 por defecto)
EXPOSE 8095

# Iniciar la aplicación Flask mediante Gunicorn con workers y timeout extendido
CMD exec gunicorn --bind :$PORT --workers 3 --threads 4 --timeout 180 "run:app"
