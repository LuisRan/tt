<# Detiene los contenedores (los datos se conservan) y la IA nativa.
   .\scripts\stop.ps1 -Borrar   también BORRA bases de datos y archivos #>
param([switch]$Borrar)
Set-Location (Split-Path -Parent $PSScriptRoot)
if ($Borrar) {
  $r = Read-Host 'Se borrarán TODOS los datos. ¿Continuar? [s/N]'
  if ($r -ne 's') { exit 0 }
  docker compose --profile ia --profile ollama down -v
} else {
  docker compose --profile ia --profile ollama down
}
if (Test-Path .run/ia.pid) { Stop-Process -Id (Get-Content .run/ia.pid) -ErrorAction SilentlyContinue; Remove-Item .run/ia.pid; 'Módulo de IA nativo detenido' }
