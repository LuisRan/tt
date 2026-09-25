#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Arranque del sistema completo en macOS / Linux
#
#  ./scripts/start.sh               todo en Docker (IA en contenedor Linux)
#  ./scripts/start.sh --ia-nativa   (recomendado en Mac) el módulo de IA corre
#                                   nativo en macOS: Docling usa ocrmac (Vision)
#                                   y la GPU Metal (MPS); el resto en Docker.
#  ./scripts/start.sh --sin-llm     no configura Ollama (solo extracción regex)
#
#  Detecta el sistema operativo y configura automáticamente:
#   - .env con secretos aleatorios (si no existe)
#   - certificados HTTPS de confianza con mkcert (si está instalado)
#   - Ollama: nativo en Mac (Metal); en Linux nativo o en contenedor (+GPU NVIDIA)
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."
RAIZ="$(pwd)"

IA_NATIVA=false
CON_LLM=true
for arg in "$@"; do
  case "$arg" in
    --ia-nativa) IA_NATIVA=true ;;
    --sin-llm)   CON_LLM=false ;;
    -h|--help)   sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $arg"; exit 1 ;;
  esac
done

azul() { printf '\033[1;34m%s\033[0m\n' "$*"; }
verde() { printf '\033[1;32m%s\033[0m\n' "$*"; }
amarillo() { printf '\033[1;33m%s\033[0m\n' "$*"; }
rojo() { printf '\033[1;31m%s\033[0m\n' "$*"; }

SO="$(uname -s)"
ARQ="$(uname -m)"
case "$SO" in
  Darwin) SO_NOMBRE="macOS" ;;
  Linux)  SO_NOMBRE="Linux" ;;
  *) rojo "Sistema no soportado por este script ($SO). En Windows usa scripts\\start.ps1"; exit 1 ;;
esac
azul "==> Sistema detectado: $SO_NOMBRE ($ARQ)"

# ── Utilidades .env ───────────────────────────────────────────────────────────
leer_env() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }
poner_env() { # clave valor  (portable: macOS usa BSD sed)
  local tmp; tmp="$(mktemp)"
  if grep -qE "^$1=" .env; then
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' .env > "$tmp"
  else
    cat .env > "$tmp"; echo "$1=$2" >> "$tmp"
  fi
  cat "$tmp" > .env; rm -f "$tmp"
}

# ── 1. Docker ─────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  rojo "Docker no está instalado."
  [ "$SO" = "Darwin" ] && echo "  Instala Docker Desktop: https://www.docker.com/products/docker-desktop/  (o: brew install --cask docker)"
  [ "$SO" = "Linux" ] && echo "  Instala Docker Engine: https://docs.docker.com/engine/install/"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  if [ "$SO" = "Darwin" ]; then
    amarillo "Docker Desktop no está corriendo; abriéndolo…"
    open -a Docker || true
    for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 3; done
  fi
  docker info >/dev/null 2>&1 || { rojo "El demonio de Docker no responde. Ábrelo y vuelve a intentar."; exit 1; }
fi
docker compose version >/dev/null 2>&1 || { rojo "Se requiere 'docker compose' v2."; exit 1; }

# ── 2. .env ───────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  azul "==> Generando .env con secretos aleatorios"
  cp .env.example .env
  poner_env POSTGRES_PASSWORD "$(openssl rand -hex 20)"
  poner_env MONGO_PASSWORD "$(openssl rand -hex 20)"
  poner_env JWT_SECRET "$(openssl rand -hex 48)"
  poner_env DATA_ENCRYPTION_KEY "$(openssl rand -base64 32)"
  poner_env IA_API_KEY "$(openssl rand -hex 24)"
  poner_env BACKUP_ENCRYPTION_KEY "$(openssl rand -hex 32)"
  poner_env ADMIN_PASSWORD "Admin-$(openssl rand -hex 6)A9"
  amarillo "  Edita ADMIN_EMAIL y (opcional) RESEND_API_KEY / MAIL_FROM en .env"
fi
MODELO="$(leer_env OLLAMA_MODEL)"; MODELO="${MODELO:-qwen3:4b-instruct-2507-q4_K_M}"
HTTPS_PORT="$(leer_env HTTPS_PORT)"; HTTPS_PORT="${HTTPS_PORT:-8443}"

# ── 3. Certificados HTTPS ────────────────────────────────────────────────────
if [ ! -s nginx/certs/cert.pem ]; then
  if command -v mkcert >/dev/null 2>&1; then
    azul "==> Generando certificado HTTPS de confianza con mkcert"
    mkcert -install >/dev/null 2>&1 || true
    mkcert -cert-file nginx/certs/cert.pem -key-file nginx/certs/key.pem localhost 127.0.0.1 ::1
  else
    amarillo "  mkcert no instalado: se usará un certificado autofirmado (el navegador mostrará una advertencia)."
    [ "$SO" = "Darwin" ] && amarillo "  Para evitarla: brew install mkcert nss && rm -f nginx/certs/*.pem && ./scripts/start.sh"
  fi
fi

# ── 4. Ollama (LLM local) ────────────────────────────────────────────────────
PERFILES=""
ollama_nativo_activo() { curl -fsS --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; }

if $CON_LLM; then
  if [ "$SO" = "Darwin" ]; then
    # En Mac Docker no tiene acceso a la GPU Metal: Ollama debe correr nativo
    if ! command -v ollama >/dev/null 2>&1; then
      if command -v brew >/dev/null 2>&1; then
        azul "==> Instalando Ollama con Homebrew"
        brew install ollama
      else
        amarillo "  Ollama no está instalado. Descárgalo de https://ollama.com/download y vuelve a ejecutar."
        amarillo "  Mientras tanto la extracción funcionará solo con regex."
      fi
    fi
    if command -v ollama >/dev/null 2>&1; then
      if ! ollama_nativo_activo; then
        azul "==> Iniciando servidor de Ollama"
        (open -a Ollama >/dev/null 2>&1 || nohup ollama serve >/tmp/ollama.log 2>&1 &) || true
        for _ in $(seq 1 30); do ollama_nativo_activo && break; sleep 2; done
      fi
      if ollama_nativo_activo; then
        ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODELO" || { azul "==> Descargando modelo $MODELO (solo la primera vez)"; ollama pull "$MODELO"; }
      fi
    fi
    poner_env OLLAMA_HOST "http://host.docker.internal:11434"
  else
    if ollama_nativo_activo; then
      azul "==> Usando Ollama nativo del equipo"
      poner_env OLLAMA_HOST "http://host.docker.internal:11434"
    else
      azul "==> Ollama se ejecutará en contenedor"
      PERFILES="ollama"
      poner_env OLLAMA_HOST "http://ollama:11434"
    fi
  fi
fi

# ── 5. Módulo de IA: contenedor o nativo ─────────────────────────────────────
if $IA_NATIVA; then
  azul "==> Módulo de IA NATIVO en $SO_NOMBRE"
  "$RAIZ/scripts/ia-nativa.sh" --fondo
  poner_env IA_URL "http://host.docker.internal:5001"
else
  PERFILES="ia${PERFILES:+,$PERFILES}"
  poner_env IA_URL "http://ia:5001"
  [ -f .run/ia.pid ] && kill "$(cat .run/ia.pid)" 2>/dev/null && rm -f .run/ia.pid || true
fi
poner_env COMPOSE_PROFILES "$PERFILES"

# ── 6. Docker Compose ─────────────────────────────────────────────────────────
ARCHIVOS="-f docker-compose.yml"
if [[ "$PERFILES" == *ollama* ]] && command -v nvidia-smi >/dev/null 2>&1; then
  azul "==> GPU NVIDIA detectada: Ollama usará la GPU"
  ARCHIVOS="$ARCHIVOS -f docker-compose.gpu.yml"
fi
azul "==> Construyendo y levantando contenedores (perfiles: ${PERFILES:-ninguno})"
amarillo "  La primera vez tarda varios minutos (descarga de imágenes y modelos de Docling)."
# shellcheck disable=SC2086
docker compose $ARCHIVOS up -d --build --remove-orphans

# ── 7. Espera a que responda ──────────────────────────────────────────────────
azul "==> Esperando a que el sistema esté listo…"
for i in $(seq 1 120); do
  if curl -fsSk --max-time 5 "https://localhost:$HTTPS_PORT/api/salud" >/dev/null 2>&1; then break; fi
  sleep 5
  [ "$i" = 120 ] && { rojo "El backend no respondió a tiempo. Revisa: docker compose logs -f backend"; exit 1; }
done

echo
verde "[OK] Sistema en ejecución"
echo "  Aplicación:     https://localhost:$HTTPS_PORT"
echo "  Administrador:  $(leer_env ADMIN_EMAIL)   (contraseña: ADMIN_PASSWORD en .env)"
if [ "$(leer_env MAIL_PROVIDER)" != "resend" ] || [ -z "$(leer_env RESEND_API_KEY)" ]; then
  amarillo "  Correo en modo consola: los códigos 2FA aparecen con:  docker compose logs -f backend | grep -A3 CORREO"
fi
$IA_NATIVA && echo "  Módulo de IA nativo: http://localhost:5001/health   (logs: .run/ia.log)"
echo "  Detener:        ./scripts/stop.sh"
