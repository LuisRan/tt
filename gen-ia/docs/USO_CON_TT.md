# Usar gen-ia con el módulo de IA de tt

`gen-ia` vive dentro de `tt/` para que todo el equipo genere documentos de prueba y mida el OCR con los
mismos comandos. Los datos son **MOCK** y la marca de agua es obligatoria.

## 1. Generar documentos (solo Docker)

```bash
cd tt/gen-ia
docker compose build
docker compose run --rm generate --doc-type all --n 5 --seed 1     # salida en ./data
docker compose run --rm tests                                      # pruebas del generador
```
Más opciones (menú interactivo, plataformas, `make`): [DOCKER.md](DOCKER.md).

## 2. Medir el OCR de tt

El puente (`python -m src.evaluation`) genera una campaña, envía los PDFs al OCR, compara con la verdad de
referencia y escribe reportes en `out/campaigns/<campaña>/results/`. Necesita Python 3.11 en tu equipo:

```bash
cd tt/gen-ia
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.lock && .venv/bin/pip install -e .
```

Hay dos formas de llegar al OCR (se elige en `ocr.kind` de la configuración):

**a) Contra el módulo de IA levantado con Docker o nativo (`kind: http`)** – la más sencilla para el equipo:

```bash
cd tt
docker compose -f docker-compose.yml -f docker-compose.eval.yml up -d      # publica la IA en 127.0.0.1:5001
# (en Mac con ./scripts/start.sh la IA nativa ya está en :5001 y no hace falta el segundo archivo)
cd gen-ia
export IA_API_KEY=$(grep '^IA_API_KEY=' ../.env | cut -d= -f2-)
.venv/bin/python -m src.evaluation run --config examples/campaign.validacion-docker.json
.venv/bin/python -m src.evaluation report --run-dir out/campaigns/<campaña>
```

**b) Lanzando el OCR directamente (`kind: cli`)** – usa `tt/ia_documentos/.venv` y Ollama local; ver
[EVALUACION.md](EVALUACION.md) y `examples/campaign.smoke.json`.

Notas para interpretar los números:
- En **Mac (IA nativa, Apple Vision)** la precisión medida en calidad `none`+`default` fue ≥ 94 % por tipo de
  documento; en **Docker/Linux/Windows (Tesseract)** INE y CURP ≥ 98 %, pasaporte ≈ 89 % y acta ≈ 81 %
  (muestra pequeña: 6 documentos base por tipo). Los documentos con degradación `heavy` pueden ser ilegibles.
- Las campañas largas (`baseline100`, ~7 h) conviene correrlas con `caffeinate -dimsu` (Mac) y sin usar la web
  a la vez: comparten Ollama y distorsionan la latencia.
