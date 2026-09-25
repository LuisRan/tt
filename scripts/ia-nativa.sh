#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Módulo de IA NATIVO (sin Docker) en macOS / Linux.
#  En Mac aprovecha ocrmac (Vision de Apple) y la GPU Metal (MPS), tal como se
#  configuró originalmente el prototipo. platform_config.py detecta el SO.
#
#  ./scripts/ia-nativa.sh           primer plano (Ctrl+C para detener)
#  ./scripts/ia-nativa.sh --fondo   segundo plano (log en .run/ia.log)
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."
RAIZ="$(pwd)"
IA="$RAIZ/ia_documentos"
mkdir -p "$RAIZ/.run"

# Python 3.11+
PY=""
for c in python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  if [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then brew install python@3.12; PY=python3.12
  else echo "Se requiere Python 3.11+"; exit 1; fi
fi

# Dependencias del sistema (Tesseract español + Poppler)
if [ "$(uname -s)" = "Darwin" ]; then
  command -v brew >/dev/null 2>&1 || { echo "Instala Homebrew: https://brew.sh"; exit 1; }
  command -v tesseract >/dev/null 2>&1 || brew install tesseract
  tesseract --list-langs 2>/dev/null | grep -qx spa || brew install tesseract-lang
  command -v pdftoppm >/dev/null 2>&1 || brew install poppler
else
  if ! command -v tesseract >/dev/null 2>&1 || ! command -v pdftoppm >/dev/null 2>&1; then
    echo "Instalando tesseract y poppler (requiere sudo)…"
    sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-spa poppler-utils
  fi
fi

# Entorno virtual
if [ ! -x "$IA/.venv/bin/python" ]; then
  echo "==> Creando entorno virtual en ia_documentos/.venv"
  "$PY" -m venv "$IA/.venv"
fi
if [ ! -f "$IA/.venv/.deps-ok" ] || [ "$IA/requirements.txt" -nt "$IA/.venv/.deps-ok" ]; then
  echo "==> Instalando dependencias de Python (Docling, torch… puede tardar)"
  "$IA/.venv/bin/pip" install --upgrade pip >/dev/null
  "$IA/.venv/bin/pip" install -r "$IA/requirements.txt"
  "$IA/.venv/bin/python" "$IA/scripts/warmup_docling.py" || true
  touch "$IA/.venv/.deps-ok"
fi

# En Mac Docker Desktop alcanza los servicios del host en 127.0.0.1;
# en Linux el contenedor llega por la IP del bridge: se escucha en 0.0.0.0
HOST_IA="127.0.0.1"; [ "$(uname -s)" = "Linux" ] && HOST_IA="0.0.0.0"
export IA_API_HOST="$HOST_IA" IA_API_PORT=5001 OLLAMA_HOST="http://localhost:11434"

if [ "${1:-}" = "--fondo" ]; then
  [ -f "$RAIZ/.run/ia.pid" ] && kill "$(cat "$RAIZ/.run/ia.pid")" 2>/dev/null || true
  (cd "$IA" && nohup .venv/bin/python serve.py > "$RAIZ/.run/ia.log" 2>&1 & echo $! > "$RAIZ/.run/ia.pid")
  for _ in $(seq 1 60); do curl -fsS --max-time 3 http://localhost:5001/health >/dev/null 2>&1 && break; sleep 2; done
  echo "  Módulo de IA nativo en http://localhost:5001 (pid $(cat "$RAIZ/.run/ia.pid"))"
else
  cd "$IA" && exec .venv/bin/python serve.py
fi
