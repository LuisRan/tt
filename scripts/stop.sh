#!/usr/bin/env bash
# Detiene los contenedores (los datos se conservan en volúmenes) y la IA nativa.
#   ./scripts/stop.sh            detener
#   ./scripts/stop.sh --borrar   detener y BORRAR bases de datos y archivos
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" = "--borrar" ]; then
  read -r -p "Se borrarán TODOS los datos (usuarios, documentos, BD). ¿Continuar? [s/N] " r
  [ "$r" = "s" ] || exit 0
  docker compose --profile ia --profile ollama down -v
else
  docker compose --profile ia --profile ollama down
fi
if [ -f .run/ia.pid ]; then kill "$(cat .run/ia.pid)" 2>/dev/null || true; rm -f .run/ia.pid; echo "Módulo de IA nativo detenido"; fi
