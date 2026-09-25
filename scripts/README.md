# Scripts de arranque

Ejecútalos siempre desde la raíz del proyecto (`cd ~/Documents/tt`). Detectan el sistema operativo
y preparan lo necesario. Guía completa de operación en [`docs/OPERACION.md`](../docs/OPERACION.md).

| Script | Sistema | Qué hace |
|---|---|---|
| `start.sh` | macOS / Linux | Crea `.env` con secretos aleatorios si falta, genera certificados con `mkcert` si existe, configura Ollama (nativo o contenedor, GPU NVIDIA si hay), descarga el modelo, levanta Docker Compose con `--build` y espera al backend. |
| `start.sh --ia-nativa` | macOS (recomendado) | Igual, pero el módulo de IA corre nativo (Apple Vision + GPU Metal) y el backend lo usa por `host.docker.internal:5001`. |
| `start.sh --sin-llm` | macOS / Linux | No configura Ollama; la extracción usa solo regex + validación cruzada. |
| `stop.sh` | macOS / Linux | Detiene contenedores e IA nativa. Los datos se conservan. |
| `stop.sh --borrar` | macOS / Linux | Detiene y borra volúmenes (BD, archivos). Pide confirmación. |
| `ia-nativa.sh` | macOS / Linux | Crea `ia_documentos/.venv`, instala dependencias si cambió `requirements.txt` y ejecuta el módulo de IA en primer plano. |
| `ia-nativa.sh --fondo` | macOS / Linux | Igual en segundo plano (reinicia el proceso anterior); log en `.run/ia.log`, PID en `.run/ia.pid`. |
| `start.ps1` / `start.ps1 -IaNativa` | Windows | Equivalente a `start.sh` para PowerShell / Docker Desktop. |
| `stop.ps1` | Windows | Equivalente a `stop.sh`. |
| `ia-nativa.ps1` | Windows | IA nativa con Tesseract (y CUDA si hay GPU NVIDIA); servidor waitress. |

En Windows, la primera vez: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Los scripts **no sobrescriben** un `.env` existente: solo agregan o ajustan las variables que
administran (`COMPOSE_PROFILES`, `IA_URL`, `OLLAMA_HOST`).
