"""
config/settings.py
Configuracion central. Todos los valores configurables viven aqui.

Cada valor puede sobreescribirse con una variable de entorno (o con un archivo
.env en la raiz de ia_documentos/ o en la raiz del monorepo). Esto permite que
el mismo codigo corra:
  - nativo en macOS (Apple Silicon)     -> python run_local.py / scripts/run_api.sh
  - nativo en Windows                   -> scripts\\run_api.ps1
  - dentro de Docker (Linux)            -> docker compose up
"""
from __future__ import annotations

import os
from pathlib import Path

# ── .env opcional ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

try:  # python-dotenv es opcional: si no esta, solo se usan variables del SO
    from dotenv import load_dotenv

    # Prioridad: variables del SO > ia_documentos/.env > ../.env (monorepo)
    load_dotenv(BASE_DIR / ".env", override=False)
    load_dotenv(BASE_DIR.parent / ".env", override=False)
except ImportError:  # pragma: no cover
    pass


def _env_str(nombre: str, defecto: str) -> str:
    valor = os.environ.get(nombre)
    return valor.strip() if valor and valor.strip() else defecto


def _env_int(nombre: str, defecto: int) -> int:
    try:
        return int(os.environ.get(nombre, defecto))
    except (TypeError, ValueError):
        return defecto


def _env_float(nombre: str, defecto: float) -> float:
    try:
        return float(os.environ.get(nombre, defecto))
    except (TypeError, ValueError):
        return defecto


def _env_bool(nombre: str, defecto: bool) -> bool:
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


# ── Rutas ─────────────────────────────────────────────────────────────────────
DATA_DIR = Path(_env_str("IA_DATA_DIR", str(BASE_DIR / "data")))

INPUT_DIR        = DATA_DIR / "input"
RAW_TEXT_DIR     = DATA_DIR / "output" / "raw_text"
JSON_RESULTS_DIR = DATA_DIR / "output" / "json_results"
LOGS_DIR         = DATA_DIR / "logs"

for _d in [INPUT_DIR, RAW_TEXT_DIR, JSON_RESULTS_DIR, LOGS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── Validacion de PDF ─────────────────────────────────────────────────────────
PDF_MAX_SIZE_MB = _env_int("PDF_MAX_SIZE_MB", 10)
PDF_MAX_PAGES   = _env_int("PDF_MAX_PAGES", 4)

# ── Ollama / LLM ──────────────────────────────────────────────────────────────
# En Docker se usa http://ollama:11434 (contenedor) o
# http://host.docker.internal:11434 (Ollama nativo del host, recomendado en Mac
# porque dentro de Docker no hay acceso a la GPU Metal).
OLLAMA_HOST = _env_str("OLLAMA_HOST", "http://localhost:11434")

# Modelo recomendado por nivel de precision:
#
#   qwen3:4b-instruct-2507-q4_K_M -> [PREDETERMINADO] SIN razonamiento interno
#                    (<think>): responde directo, ideal para "formatear a JSON".
#                    ~2.5 GB RAM, cabe en Macs de 8 GB.
#                    instalacion: ollama pull qwen3:4b-instruct-2507-q4_K_M
#
#   qwen2.5:7b    -> mas preciso pero mas lento (~4.7 GB RAM, ~20-40s/doc)
#                    excelente seguimiento de instrucciones, raramente alucina
#                    instalacion: ollama pull qwen2.5:7b
#
#   qwen3:4b      -> alternativa equilibrada (~3 GB RAM, ~30s/doc)
#                    instalacion: ollama pull qwen3:4b
#
#   qwen3:1.7b    -> rapido pero alucina con docs complejos (~1.4 GB RAM)
#                    NO recomendado: tiende a inventar CURPs y claves de elector
#
#   gemma3:4b     -> alternativa solida no-Qwen (~3 GB RAM)
#
# Los datos sensibles (CURP, clave de elector, fechas) ademas se verifican con
# regex + digito verificador + anti-alucinacion, por lo que el LLM solo ordena
# los campos; un modelo instruct pequeno es suficiente.
OLLAMA_MODEL = _env_str("OLLAMA_MODEL", "qwen3:4b-instruct-2507-q4_K_M")

LLM_TIMEOUT     = _env_int("LLM_TIMEOUT", 240)      # segundos (mas tiempo para 7b)
# Mantener el modelo cargado en memoria entre documentos (evita recargarlo
# en cada petición, que en Macs con 8 GB puede tardar decenas de segundos)
OLLAMA_KEEP_ALIVE = _env_str("OLLAMA_KEEP_ALIVE", "30m")
# Límite de tokens de salida del LLM (el JSON de un documento cabe en ~600)
LLM_NUM_PREDICT = _env_int("LLM_NUM_PREDICT", 1024)
# Contexto del LLM: suficiente para el texto OCR + prompt sin desperdiciar RAM
LLM_NUM_CTX = _env_int("LLM_NUM_CTX", 8192)
LLM_TEMPERATURE = _env_float("LLM_TEMPERATURE", 0)  # determinista

# ── Pipeline ──────────────────────────────────────────────────────────────────
# "llm_first"   -> hibrido regex + LLM (recomendado)
# "regex_first" -> solo regex, sin LLM (rapido pero menor precision)
EXTRACTION_STRATEGY = _env_str("EXTRACTION_STRATEGY", "llm_first")

# ── OCR / plataforma ──────────────────────────────────────────────────────────
# El motor OCR que Docling usa para zonas rasterizadas se elige segun el SO
# (ver infrastructure/platform_config.py). Se puede forzar con:
#   OCR_ENGINE = auto | ocrmac | tesseract | easyocr | rapidocr
OCR_ENGINE = _env_str("OCR_ENGINE", "auto").lower()
# Dispositivo de inferencia de Docling: auto | cpu | mps | cuda
DOCLING_DEVICE = _env_str("DOCLING_DEVICE", "auto").lower()
# TableFormer puede provocar OOM en Apple M1 con 8 GB. "auto" lo desactiva
# solo en Mac con <= 8 GB de RAM.  Valores: auto | true | false
DOCLING_TABLES = _env_str("DOCLING_TABLES", "auto").lower()
# Rutas manuales (normalmente se detectan solas)
TESSERACT_CMD = os.environ.get("TESSERACT_CMD") or None
POPPLER_PATH  = os.environ.get("POPPLER_PATH") or None
OCR_DPI       = _env_int("OCR_DPI", 200)  # 200 y no 300 para evitar OOM en M1

OCR_MIN_CHARS_NORMAL   = _env_int("OCR_MIN_CHARS_NORMAL", 500)
OCR_MIN_CHARS_FALLBACK = _env_int("OCR_MIN_CHARS_FALLBACK", 300)

# ── Anti-alucinacion ──────────────────────────────────────────────────────────
# Si True: cada valor que el LLM produzca para campos protegidos (CURP, clave
# de elector, codigos numericos, fechas, etc.) DEBE aparecer literalmente en
# el texto crudo del OCR. Si no aparece -> se reemplaza por null.
#
# DEJA ESTO EN True. Si lo desactivas, los datos sensibles pueden ser inventados.
ANTI_HALLUCINATION = _env_bool("ANTI_HALLUCINATION", True)

# ── API REST (Flask) ──────────────────────────────────────────────────────────
IA_API_HOST = _env_str("IA_API_HOST", "0.0.0.0")
IA_API_PORT = _env_int("IA_API_PORT", 5001)
# Clave compartida con el backend Node.js (cabecera X-API-Key).
# Si esta vacia, la API no exige clave (solo recomendable en desarrollo local).
IA_API_KEY = os.environ.get("IA_API_KEY", "").strip()
IA_WORKERS = _env_int("IA_WORKERS", 1)
IA_REQUEST_TIMEOUT = _env_int("IA_REQUEST_TIMEOUT", 600)
LOG_LEVEL = _env_str("LOG_LEVEL", "INFO")

# ── Version del pipeline (se incluye en el envelope JSON) ─────────────────────
PIPELINE_VERSION = "0.3.0"
