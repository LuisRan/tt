<#
 Módulo de IA NATIVO en Windows (sin Docker).
 platform_config.py detecta Windows y busca Tesseract/Poppler en sus rutas
 típicas (C:\Program Files\Tesseract-OCR, ...). Si no hay Poppler usa pypdfium2.

  .\scripts\ia-nativa.ps1          primer plano
  .\scripts\ia-nativa.ps1 -Fondo   segundo plano (log en .run\ia.log)
#>
param([switch]$Fondo)
$ErrorActionPreference = 'Continue'
$Raiz = Split-Path -Parent $PSScriptRoot
$IA = Join-Path $Raiz 'ia_documentos'
New-Item -ItemType Directory -Force -Path (Join-Path $Raiz '.run') | Out-Null

# Python 3.11+
$py = $null
foreach ($c in @('py -3.12', 'py -3.11', 'python')) {
  $partes = $c.Split(' ')
  try {
    & $partes[0] $partes[1..9] -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $partes; break }
  } catch {}
}
if (-not $py) {
  Write-Host 'Instalando Python 3.12 con winget…' -ForegroundColor Yellow
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  $py = @('py', '-3.12')
}

# Tesseract (con español) y Poppler
$tess = @("$env:ProgramFiles\Tesseract-OCR\tesseract.exe", "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $tess -and -not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
  Write-Host 'Instalando Tesseract OCR (UB Mannheim) con winget…' -ForegroundColor Yellow
  winget install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
  $tess = "$env:ProgramFiles\Tesseract-OCR\tesseract.exe"
}
if ($tess) {
  $tessdata = Join-Path (Split-Path $tess) 'tessdata'
  if (-not (Test-Path (Join-Path $tessdata 'spa.traineddata'))) {
    Write-Host 'Descargando idioma español para Tesseract…' -ForegroundColor Yellow
    try {
      Invoke-WebRequest 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/spa.traineddata' -OutFile (Join-Path $env:TEMP 'spa.traineddata')
      Start-Process powershell -Verb RunAs -Wait -ArgumentList "-Command Copy-Item '$env:TEMP\spa.traineddata' '$tessdata'"
    } catch { Write-Host "No se pudo instalar spa.traineddata; se usará inglés. ($_)" -ForegroundColor Yellow }
  }
}
if (-not (Get-Command pdftoppm -ErrorAction SilentlyContinue)) {
  try { winget install -e --id oschwartz10612.Poppler --accept-package-agreements --accept-source-agreements } catch {
    Write-Host 'Poppler no instalado: se usará pypdfium2 para renderizar PDFs.' -ForegroundColor Yellow
  }
}

# Entorno virtual + dependencias
$venvPy = Join-Path $IA '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
  Write-Host '==> Creando entorno virtual ia_documentos\.venv' -ForegroundColor Cyan
  & $py[0] $py[1..9] -m venv (Join-Path $IA '.venv')
}
$marca = Join-Path $IA '.venv\.deps-ok'
if (-not (Test-Path $marca) -or (Get-Item (Join-Path $IA 'requirements.txt')).LastWriteTime -gt (Get-Item $marca).LastWriteTime) {
  Write-Host '==> Instalando dependencias de Python (Docling, torch… puede tardar)' -ForegroundColor Cyan
  if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    # GPU NVIDIA: torch con CUDA para acelerar Docling
    & $venvPy -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
  }
  & $venvPy -m pip install --upgrade pip
  & $venvPy -m pip install -r (Join-Path $IA 'requirements.txt')
  & $venvPy (Join-Path $IA 'scripts\warmup_docling.py')
  New-Item -ItemType File -Force -Path $marca | Out-Null
}

$env:IA_API_HOST = '127.0.0.1'
$env:IA_API_PORT = '5001'
$env:OLLAMA_HOST = 'http://localhost:11434'
$env:PYTHONUTF8 = '1'

if ($Fondo) {
  $pidFile = Join-Path $Raiz '.run\ia.pid'
  if (Test-Path $pidFile) { Stop-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue }
  $p = Start-Process -FilePath $venvPy -ArgumentList 'serve.py' -WorkingDirectory $IA -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Raiz '.run\ia.log') -RedirectStandardError (Join-Path $Raiz '.run\ia.err.log')
  $p.Id | Out-File -Encoding ascii $pidFile
  for ($i = 0; $i -lt 60; $i++) { try { Invoke-RestMethod http://localhost:5001/health -TimeoutSec 3 | Out-Null; break } catch { Start-Sleep 2 } }
  Write-Host "  Módulo de IA nativo en http://localhost:5001 (pid $($p.Id))"
} else {
  Set-Location $IA
  & $venvPy serve.py
}
