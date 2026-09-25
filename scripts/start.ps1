<#
═══════════════════════════════════════════════════════════════════════════
 Arranque del sistema completo en Windows (PowerShell 5.1+ / 7+)

  .\scripts\start.ps1              todo en Docker Desktop (WSL2)
  .\scripts\start.ps1 -IaNativa    módulo de IA nativo en Windows (Tesseract
                                   + CUDA si hay GPU NVIDIA); resto en Docker
  .\scripts\start.ps1 -SinLlm      sin Ollama (solo extracción regex)

 Si PowerShell bloquea el script:
  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
═══════════════════════════════════════════════════════════════════════════
#>
param([switch]$IaNativa, [switch]$SinLlm)
# 'Continue': los comandos nativos (docker, curl) se evalúan por $LASTEXITCODE
$ErrorActionPreference = 'Continue'
$Raiz = Split-Path -Parent $PSScriptRoot
Set-Location $Raiz

function Azul($t) { Write-Host $t -ForegroundColor Cyan }
function Verde($t) { Write-Host $t -ForegroundColor Green }
function Amarillo($t) { Write-Host $t -ForegroundColor Yellow }
function Rojo($t) { Write-Host $t -ForegroundColor Red }

$so = if ($IsMacOS) { 'macOS' } elseif ($IsLinux) { 'Linux' } else { 'Windows' }
if ($so -ne 'Windows') { Rojo "En $so usa ./scripts/start.sh"; exit 1 }
Azul "==> Sistema detectado: Windows ($env:PROCESSOR_ARCHITECTURE)"

# ── Utilidades .env (UTF-8 sin BOM) ──────────────────────────────────────────
$Utf8 = New-Object System.Text.UTF8Encoding($false)
function Leer-Env($clave) {
  if (-not (Test-Path .env)) { return '' }
  $l = Get-Content .env | Where-Object { $_.StartsWith("$clave=") } | Select-Object -Last 1
  if ($l) { return $l.Substring($clave.Length + 1) } else { return '' }
}
function Poner-Env($clave, $valor) {
  $lineas = New-Object System.Collections.Generic.List[string]
  $hecho = $false
  foreach ($l in (Get-Content .env)) {
    if (-not $hecho -and $l.StartsWith("$clave=")) { $lineas.Add("$clave=$valor"); $hecho = $true }
    else { $lineas.Add($l) }
  }
  if (-not $hecho) { $lineas.Add("$clave=$valor") }
  [System.IO.File]::WriteAllLines((Join-Path $Raiz '.env'), $lineas, $Utf8)
}
function Aleatorio-Hex($bytes) {
  $b = New-Object byte[] $bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  return ($b | ForEach-Object { $_.ToString('x2') }) -join ''
}
function Aleatorio-B64($bytes) {
  $b = New-Object byte[] $bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  return [Convert]::ToBase64String($b)
}
function Ollama-Activo {
  try { Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

# ── 1. Docker ─────────────────────────────────────────────────────────────────
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Rojo 'Docker Desktop no está instalado: https://www.docker.com/products/docker-desktop/ (o: winget install Docker.DockerDesktop)'
  exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
  Amarillo 'Docker Desktop no está corriendo; abriéndolo…'
  $dd = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
  if (Test-Path $dd) { Start-Process $dd }
  for ($i = 0; $i -lt 60; $i++) { docker info *> $null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 3 }
  docker info *> $null
  if ($LASTEXITCODE -ne 0) { Rojo 'Docker no responde. Ábrelo manualmente y vuelve a intentar.'; exit 1 }
}

# ── 2. .env ───────────────────────────────────────────────────────────────────
if (-not (Test-Path .env)) {
  Azul '==> Generando .env con secretos aleatorios'
  Copy-Item .env.example .env
  Poner-Env 'POSTGRES_PASSWORD' (Aleatorio-Hex 20)
  Poner-Env 'MONGO_PASSWORD' (Aleatorio-Hex 20)
  Poner-Env 'JWT_SECRET' (Aleatorio-Hex 48)
  Poner-Env 'DATA_ENCRYPTION_KEY' (Aleatorio-B64 32)
  Poner-Env 'IA_API_KEY' (Aleatorio-Hex 24)
  Poner-Env 'BACKUP_ENCRYPTION_KEY' (Aleatorio-Hex 32)
  Poner-Env 'ADMIN_PASSWORD' ("Admin-" + (Aleatorio-Hex 6) + "A9")
  Amarillo '  Edita ADMIN_EMAIL y (opcional) RESEND_API_KEY / MAIL_FROM en .env'
}
$Modelo = Leer-Env 'OLLAMA_MODEL'; if (-not $Modelo) { $Modelo = 'qwen3:4b-instruct-2507-q4_K_M' }
$HttpsPort = Leer-Env 'HTTPS_PORT'; if (-not $HttpsPort) { $HttpsPort = '8443' }

# ── 3. Certificados HTTPS ────────────────────────────────────────────────────
if (-not (Test-Path nginx/certs/cert.pem)) {
  if (Get-Command mkcert -ErrorAction SilentlyContinue) {
    Azul '==> Generando certificado HTTPS de confianza con mkcert'
    mkcert -install | Out-Null
    mkcert -cert-file nginx/certs/cert.pem -key-file nginx/certs/key.pem localhost 127.0.0.1 ::1
  } else {
    Amarillo '  mkcert no instalado: certificado autofirmado (el navegador mostrará advertencia). Opcional: winget install FiloSottile.mkcert'
  }
}

# ── 4. Ollama ────────────────────────────────────────────────────────────────
$perfiles = @()
$hayGpu = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if (-not $SinLlm) {
  if (Ollama-Activo) {
    Azul '==> Usando Ollama nativo de Windows'
    Poner-Env 'OLLAMA_HOST' 'http://host.docker.internal:11434'
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
      $lista = (ollama list) -join "`n"
      if ($lista -notmatch [regex]::Escape($Modelo)) { Azul "==> Descargando modelo $Modelo"; ollama pull $Modelo }
    }
  } else {
    if ($hayGpu) { Azul '==> Ollama se ejecutará en contenedor con GPU NVIDIA' } else { Azul '==> Ollama se ejecutará en contenedor (CPU)' }
    $perfiles += 'ollama'
    Poner-Env 'OLLAMA_HOST' 'http://ollama:11434'
  }
}

# ── 5. Módulo de IA ──────────────────────────────────────────────────────────
if ($IaNativa) {
  Azul '==> Módulo de IA NATIVO en Windows'
  & (Join-Path $PSScriptRoot 'ia-nativa.ps1') -Fondo
  Poner-Env 'IA_URL' 'http://host.docker.internal:5001'
} else {
  $perfiles = @('ia') + $perfiles
  Poner-Env 'IA_URL' 'http://ia:5001'
  if (Test-Path .run/ia.pid) { Stop-Process -Id (Get-Content .run/ia.pid) -ErrorAction SilentlyContinue; Remove-Item .run/ia.pid }
}
Poner-Env 'COMPOSE_PROFILES' ($perfiles -join ',')

# ── 6. Docker Compose ─────────────────────────────────────────────────────────
$archivos = @('-f', 'docker-compose.yml')
if ($perfiles -contains 'ollama' -and $hayGpu) { $archivos += @('-f', 'docker-compose.gpu.yml') }
Azul "==> Construyendo y levantando contenedores (perfiles: $($perfiles -join ','))"
Amarillo '  La primera vez tarda varios minutos (imágenes y modelos de Docling).'
docker compose @archivos up -d --build --remove-orphans
if ($LASTEXITCODE -ne 0) { Rojo 'docker compose falló'; exit 1 }
if ($perfiles -contains 'ollama') { Amarillo "  El modelo $Modelo se descarga en segundo plano (servicio ollama-pull)." }

# ── 7. Espera ─────────────────────────────────────────────────────────────────
Azul '==> Esperando a que el sistema esté listo…'
$listo = $false
for ($i = 0; $i -lt 120; $i++) {
  $r = & curl.exe -fsSk --max-time 5 "https://localhost:$HttpsPort/api/salud" 2>$null
  if ($LASTEXITCODE -eq 0) { $listo = $true; break }
  Start-Sleep 5
}
if (-not $listo) { Rojo 'El backend no respondió a tiempo. Revisa: docker compose logs -f backend'; exit 1 }

Write-Host ''
Verde '[OK] Sistema en ejecución'
Write-Host "  Aplicación:     https://localhost:$HttpsPort"
Write-Host "  Administrador:  $(Leer-Env 'ADMIN_EMAIL')   (contraseña: ADMIN_PASSWORD en .env)"
if ((Leer-Env 'MAIL_PROVIDER') -ne 'resend' -or -not (Leer-Env 'RESEND_API_KEY')) {
  Amarillo '  Correo en modo consola: los códigos 2FA aparecen con:  docker compose logs -f backend'
}
if ($IaNativa) { Write-Host '  Módulo de IA nativo: http://localhost:5001/health  (logs: .run\ia.log)' }
Write-Host '  Detener:        .\scripts\stop.ps1'
