# ia_documentos — Pipeline de Extraccion de Documentos de Identidad Mexicanos

Pipeline local en Python que extrae datos estructurados de documentos de identidad
oficiales mexicanos (INE, Acta de Nacimiento, Pasaporte, Constancia de CURP) usando
una combinacion de OCR (Docling + Pytesseract) y LLM local (Ollama).

## Caracteristicas Clave

- **Local y privado**: todos los datos se procesan en tu equipo, nunca se envian a la nube.
- **Hibrido regex + LLM**: el regex extrae lo determinista, el LLM completa el resto.
- **Anti-alucinacion**: cada campo del LLM se valida contra el texto crudo OCR.
  Si el LLM inventa un dato que no esta en el documento, se descarta automaticamente.
- **OCR adaptativo**: estrategia diferente por tipo de documento.
  Para CURP: forza OCR sobre la zona util superior. Para Pasaporte: combina Docling + Pytesseract.
- **Lazy loading**: arranque rapido (~600ms), Docling se carga solo al procesar el primer PDF.
- **Multiplataforma**: detecta el sistema operativo (macOS / Windows / Linux / Docker) y configura
  Docling, Tesseract y Poppler automaticamente (`infrastructure/platform_config.py`).
- **API REST (Flask)**: endpoints de la Tabla 23 para el backend Node.js (`integration/flask_app.py`).

## Instalacion

### Requisitos del sistema

```bash
# Linux
sudo apt-get install poppler-utils tesseract-ocr tesseract-ocr-spa

# macOS
brew install poppler tesseract tesseract-lang
```

### Dependencias Python

```bash
pip install -r requirements.txt
```

### Ollama y modelo

```bash
# Instalar Ollama: https://ollama.com/download

# Modelo predeterminado (sin razonamiento interno, rapido, ~2.5 GB RAM):
ollama pull qwen3:4b-instruct-2507-q4_K_M

# Alternativas:
# ollama pull qwen2.5:7b    # mas lento, ~6 GB RAM
# ollama pull qwen3:1.7b    # ~1.4 GB RAM, alucina con datos sensibles
```

El modelo por defecto es `qwen3:4b-instruct-2507-q4_K_M`: no genera bloques `<think>`, por lo
que responde directo y solo se encarga de ordenar los campos en JSON. Los datos sensibles se
verifican despues con regex, digito verificador y anti-alucinacion, asi que un modelo pequeno
es suficiente. Se cambia con `OLLAMA_MODEL` en `.env`.

Variables de rendimiento del LLM:

| Variable | Defecto | Efecto |
|---|---|---|
| `OLLAMA_KEEP_ALIVE` | `30m` | Mantiene el modelo cargado entre documentos (evita recargarlo). |
| `LLM_NUM_CTX` | `8192` | Ventana de contexto; acotarla reduce memoria y tiempo. |
| `LLM_NUM_PREDICT` | `1024` | Maximo de tokens de salida (el JSON cabe de sobra). |
| `LLM_TIMEOUT` | `240` | Segundos maximos por llamada. |

El resultado incluye `metadatos.tiempos_etapas_ms` (`ocr_ms`, `extraccion_ms`) para medir cada etapa.

## Uso

```bash
# Coloca tus PDFs en data/input/

# Menu interactivo:
python run_local.py

# Directo con archivo:
python run_local.py mi_ine.pdf --user_id usuario_001

# Verificar estado del servicio:
python run_local.py --health
```

## API REST (integracion con el backend Node.js)

```bash
python serve.py          # gunicorn en macOS/Linux, waitress en Windows (puerto 5001)
python serve.py --dev    # servidor de desarrollo de Flask
```

| Metodo | Ruta | Respuesta |
|---|---|---|
| POST | `/api/v1/documentos/procesa` (multipart: `archivo`, `user_id`, `doc_id`) | 200 envelope · 400 · 413 · 415 no soportado · 422 PDF invalido · 503 sin OCR |
| GET | `/api/v1/documentos/{doc_id}` | 200 envelope · 404 |
| GET | `/api/v1/documentos/{doc_id}/texto` | 200 `{texto}` (o `text/markdown` con `Accept`) · 404 |
| GET | `/api/v1/usuarios/{user_id}/documentos` | 200 lista |
| GET | `/health` | estado del OCR, de Ollama y plataforma detectada |

Todas las rutas (excepto `/health`) exigen la cabecera `X-API-Key` = `IA_API_KEY` cuando esta definida.
Toda la configuracion se lee de variables de entorno / `.env` (ver `config/settings.py`).

## Correccion de errores de lectura (validacion cruzada)

`infrastructure/regex/correccion_ocr.py` corrige confusiones tipicas del OCR (`0/O`, `1/I`, `5/S`,
`8/B`, `2/Z`...) en campos con estructura conocida:

- **CURP**: se prueban variantes por posicion (letras vs. digitos) y se acepta la que cumple el
  **digito verificador** oficial de RENAPO y es consistente con nombre, fecha de nacimiento, sexo y entidad.
- **Clave de elector (INE)**: se reconstruye a partir de apellidos, nombre, fecha, entidad y sexo
  cuando la lectura tiene errores.
- **Consenso**: si hay varias lecturas (Docling, Tesseract, Apple Vision), se vota por la mas consistente.
- Los valores del LLM que contradicen una CURP valida se descartan.

Los campos corregidos se reportan en `metadatos.campos_corregidos_ocr` y la interfaz los marca para
que el usuario los revise. Detalle de la estrategia en [OCR_STRATEGY.md](OCR_STRATEGY.md).

## Deteccion del sistema operativo

| SO | Motor OCR de Docling | Acelerador | Binarios |
|---|---|---|---|
| macOS | ocrmac (Vision) | MPS | Homebrew |
| Windows | Tesseract CLI / RapidOCR | CUDA o CPU | `C:\Program Files\Tesseract-OCR`, Poppler o pypdfium2 |
| Linux / Docker | Tesseract CLI | CPU / CUDA | `/usr/bin` |

Se puede forzar con `OCR_ENGINE`, `DOCLING_DEVICE`, `DOCLING_TABLES`, `TESSERACT_CMD`, `POPPLER_PATH`.
`python scripts/warmup_docling.py` precarga los modelos de Docling con la configuracion del SO.

## Docker

```bash
docker build -t tt-ia .
docker run -p 5001:5001 -e IA_API_KEY=secreto -e OLLAMA_HOST=http://host.docker.internal:11434 tt-ia
```
Normalmente se levanta junto con el resto del sistema desde la raiz del monorepo (`docker compose up`).

## Estructura del Proyecto

```
ia_documentos/
├── README.md                       # este archivo
├── OCR_STRATEGY.md                 # detalle de la estrategia OCR
├── TODO_FLASK.md                   # guia para integrar con Flask
├── requirements.txt
├── pyproject.toml                  # config pytest + coverage
├── run_local.py                    # CLI interactivo
├── serve.py                        # arranca la API (gunicorn / waitress segun SO)
├── Dockerfile
├── integration/flask_app.py        # adaptador REST (FlaskRestAdapter)
├── scripts/warmup_docling.py       # precarga de modelos
├── config/
│   └── settings.py                 # toda la configuracion centralizada
├── domain/
│   ├── entities/documento.py       # entidades del dominio
│   ├── ports/interfaces.py         # contratos (puertos)
│   └── schemas/document_schemas.py # schemas Pydantic por tipo
├── infrastructure/
│   ├── ocr/
│   │   ├── pdf_validator.py        # validacion + correccion de orientacion
│   │   └── docling_adapter.py      # OCR con estrategia adaptativa
│   ├── llm/ollama_qwen_adapter.py  # adaptador LLM con prompts por tipo
│   ├── regex/regex_heuristic_adapter.py  # extractor regex determinista
│   ├── storage/file_adapters.py    # persistencia .md y .json
│   ├── platform_config.py          # deteccion de SO + config de Docling/Tesseract/Poppler
│   └── logging_setup.py            # logging estructurado
├── application/
│   ├── pipeline.py                 # orquestador con anti-alucinacion
│   └── validator.py                # validacion Pydantic
├── tests/unit/                     # 76 pruebas unitarias
└── data/
    ├── input/                      # AQUI van los PDFs a procesar
    ├── output/
    │   ├── raw_text/               # texto crudo extraido (.md)
    │   └── json_results/           # resultado final (.json)
    └── logs/                       # logs estructurados
```

## Flujo del Pipeline

```
PDF de entrada
    |
    v
[1] Validacion: tamaño, paginas, tipo MIME
    |
    v
[2] Correccion silenciosa de orientacion
    |
    v
[3] OCR adaptativo por tipo:
    - INE/Acta: Docling normal
    - CURP: Docling + OCR forzado de zona superior (pytesseract)
    - Pasaporte: Docling + OCR alternativo combinado
    - Si Docling extrae poco: multi-zona o pytesseract fallback
    |
    v
[4] Deteccion de tipo de documento por patrones
    |
    v
[5] Extraccion de campos: regex deterministico
    |
    v
[6] LLM enriquece y completa el resto
    |
    v
[7] Fusion: LLM tiene prioridad, regex llena nulls
    |
    v
[8] ANTI-ALUCINACION: cada campo protegido (CURP, clave elector, etc.)
    debe aparecer literalmente en el texto crudo OCR.
    Si no aparece -> se descarta.
    |
    v
[9] Validacion Pydantic del schema
    |
    v
JSON final con metadatos completos
```

## Anti-alucinacion (Critico)

El LLM puede memorizar patrones de su entrenamiento y devolver datos plausibles
pero falsos. Por ejemplo, qwen3:1.7b vio INEs reales y a veces "completa" datos
que no estan en el documento procesado.

Para prevenir esto, el pipeline tiene una capa de validacion que verifica que
cada valor de campos sensibles APAREZCA LITERALMENTE en el texto crudo OCR.
Si no aparece, el valor se reemplaza por null.

Campos protegidos: CURP, clave_elector, estado, municipio, seccion, localidad,
fechas, numeros de pasaporte, folios, codigos de verificacion, etc.

Esto se controla con `ANTI_HALLUCINATION = True` en `config/settings.py`.
**No lo desactives** salvo que sepas exactamente lo que haces.

## Tests

```bash
python -m pytest tests/ -v
```

76 tests (dominio, API Flask y deteccion de SO), ~98% cobertura del dominio.

## Proximos pasos sugeridos

- ~~Integracion HTTP con Flask~~ (hecho: `integration/flask_app.py`)
- Cache de documentos por hash SHA256
- Procesamiento batch de carpetas
- Expandir reglas regex con mas variaciones regionales
