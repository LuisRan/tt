#!/usr/bin/env bash
# Ejecuta el puente generador -> OCR -> evaluación en un solo comando.
#
#   ./evaluar.sh                                  prueba pequeña (examples/campaign.smoke.json)
#   ./evaluar.sh ruta/a/mi_campana.json           otra configuración
#
# Genera los documentos sintéticos, los envía al OCR real de tt (Docling + Ollama locales),
# compara contra el ground truth y abre el reporte. No usa la web ni sesión.
set -euo pipefail
cd "$(dirname "$0")"

CONFIG="${1:-examples/campaign.smoke.json}"
[ -f "$CONFIG" ] || { echo "No existe la configuración: $CONFIG"; exit 1; }

# Ollama debe estar encendido (el OCR lo usa para extraer campos)
curl -fsS -m 5 http://localhost:11434/api/tags >/dev/null 2>&1 \
  || { echo "Ollama no responde en localhost:11434. Enciéndelo (abre la app de Ollama) y reintenta."; exit 1; }

export OLLAMA_HOST="http://localhost:11434"
PY=".venv/bin/python"

echo "==> Ejecutando campaña con $CONFIG"
"$PY" -m src.evaluation run --config "$CONFIG" --force-new-run

RUN_DIR="out/campaigns/$(ls -t out/campaigns | head -1)"
echo
echo "==> Resultados en $RUN_DIR/results"
[ -f "$RUN_DIR/results/report.md" ] && open "$RUN_DIR/results/report.md" 2>/dev/null || true
