# GeoSuscetibilidade Web App 🏔️

Aplicação Web de Processamento Geográfico para Análise de Suscetibilidade a Movimentos Gravitacionais de Massa (`SUSC_Global_sem_Lin`), Reclassificação por Quebras Naturais / Ajuste Fino em Tempo Real, Vetorização Topológica de Fronteiras Compartilhadas (Chaikin 2 Iterações) e Exportação no Padrão Oficial da CPRM.

---

## 🚀 Como Executar em Servidor Externo

### Opção 1: Via Docker (Recomendado)
Para subir o servidor com 1 único comando em qualquer servidor Linux (Ubuntu/Debian/CentOS/AWS/DigitalOcean):

```bash
docker-compose up -d --build
```
A aplicação estará disponível em `http://IP_DO_SERVIDOR:8000`.

---

### Opção 2: Instalação Manual com Python

1. **Instalar dependências de sistema do GDAL/GEOS** (se em Linux Ubuntu/Debian):
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv gdal-bin libgdal-dev libgeos-dev
```

2. **Criar e ativar ambiente virtual**:
```bash
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# No Windows: venv\Scripts\activate
```

3. **Instalar pacotes Python**:
```bash
pip install -r requirements.txt
```

4. **Iniciar o servidor Web App**:
```bash
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

---

## 📁 Estrutura do Pacote

- `backend/`: Código Python FastAPI (`app.py`) e Motor de Geoprocessamento (`susc_engine.py`).
- `frontend/`: Interface Web Leaflet + GeoReport Design System (`index.html`, `css/style.css`, `js/app.js`).
- `tests/`: Arquivo `MDE.tif` de amostra para cálculo em modo de demonstração.
- `storage/`: Diretório persistente de uploads e saídas GeoTIFF / Shapefile gerados.
- `Dockerfile` & `docker-compose.yml`: Arquivos de conteinerização.
- `requirements.txt`: Lista de dependências Python.
