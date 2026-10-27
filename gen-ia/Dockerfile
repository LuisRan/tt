# syntax=docker/dockerfile:1.7
# ocr-synth -- generador de datos sinteticos de PRUEBA ("MOCK DATA") para OCR.
#
# Objetivo: que el generador produzca los MISMOS bytes en cualquier maquina.
# Para eso se fijan: la imagen base (tag exacto + digest), TODAS las
# dependencias de Python (requirements.lock con hashes) y las variables de
# entorno que afectan al determinismo.
#
# La imagen NO contiene formatos/ (referencias oficiales), .venv, .git, data/ ni
# ningun asset oficial: .dockerignore los excluye del contexto de build y solo se
# copian explicitamente contracts/ src/ tests/ pyproject.toml PLAN.md README.md y
# .gitignore (que algun guardrail lee).

# Indice multi-arquitectura de python:3.11.16-slim-bookworm (amd64 + arm64).
# Para actualizarlo: docker buildx imagetools inspect python:3.11.16-slim-bookworm
FROM python:3.11.16-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC \
    PYTHONHASHSEED=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    SOURCE_DATE_EPOCH=0 \
    OCR_DOCKER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTEST_ADDOPTS="-p no:cacheprovider"

WORKDIR /app

# 1) Dependencias SOLO desde el lock (versiones + hashes). --no-deps impide que
#    pip resuelva nada por su cuenta; --only-binary evita compilar sdists.
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes \
        -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

# 2) Usuario no root + directorio de datos (se monta ./data del host encima).
#    /app/data es escribible por cualquier uid para que `user: UID:GID` del
#    compose (o `docker run --user`) pueda escribir aunque no sea el 1000.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin ocr \
    && mkdir -p /app/data \
    && chown ocr:ocr /app/data \
    && chmod 1777 /app/data

# 3) Codigo: lo minimo para generar y para correr los tests.
COPY contracts/ /app/contracts/
COPY src/ /app/src/
COPY tests/ /app/tests/
COPY pyproject.toml PLAN.md README.md .gitignore /app/

USER ocr

VOLUME ["/app/data"]

# Sin argumentos (y con TTY) abre el menu interactivo; con argumentos ejecuta
# el modo por flags: docker run ... ocr-synth generate --doc-type all --n 10 --seed 1
ENTRYPOINT ["python", "-m", "src"]
