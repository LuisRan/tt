# ─────────────────────────────────────────────────────────────────────────────
# Modulo de IA (Flask + Docling + Tesseract) — imagen Linux multi-arquitectura
# Funciona igual en Mac (arm64) y Windows/Linux (amd64) con Docker Desktop.
# Dentro del contenedor el SO es Linux: platform_config.py elige Tesseract como
# motor OCR de Docling y CPU como dispositivo.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RUNNING_IN_DOCKER=1 \
    HF_HOME=/home/ia/.cache/huggingface \
    OMP_NUM_THREADS=4

# Tesseract (espanol + ingles), Poppler y librerias graficas que usa Docling
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng \
        poppler-utils libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch CPU primero (evita descargar ~2 GB de CUDA en amd64)
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY requirements-docker.txt .
RUN pip install -r requirements-docker.txt

RUN useradd --create-home --uid 1001 ia && chown ia:ia /app
USER ia

# Descarga de modelos de Docling durante el build (como el usuario final) para
# que el primer documento no tarde minutos y se pueda operar sin internet.
# Solo se copian los archivos necesarios para que esta capa quede en cache
# aunque cambie el resto del codigo.
COPY --chown=ia:ia config/ ./config/
COPY --chown=ia:ia infrastructure/__init__.py infrastructure/platform_config.py ./infrastructure/
COPY --chown=ia:ia scripts/ ./scripts/
ARG PRELOAD_DOCLING_MODELS=true
RUN if [ "$PRELOAD_DOCLING_MODELS" = "true" ]; then python scripts/warmup_docling.py; fi

COPY --chown=ia:ia . .
RUN mkdir -p /app/data/output /app/data/logs

EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -fsS http://localhost:5001/health || exit 1

CMD ["python", "serve.py"]
