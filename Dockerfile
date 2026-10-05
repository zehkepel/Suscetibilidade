FROM python:3.10-slim

# Instala dependências de sistema do GDAL/GEOS/PROJ para rasterio e geopandas
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    proj-data \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia e instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia arquivos da aplicação
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Cria diretórios de armazenamento
RUN mkdir -p storage/uploads storage/outputs

EXPOSE 8000

# Usa a porta definida pelo Google Cloud Run (ou 8080 localmente)
CMD ["sh", "-c", "hypercorn backend.app:app -b 0.0.0.0:${PORT:-8080}"]
