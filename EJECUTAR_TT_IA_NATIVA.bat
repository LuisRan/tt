@echo off
setlocal
title TT - Arranque con IA nativa
cd /d "%~dp0"

echo ============================================================
echo   TT - ARRANQUE CON IA NATIVA EN WINDOWS
echo ============================================================
echo.

if not exist "scripts\start.ps1" (
  echo [ERROR] No encuentro scripts\start.ps1
  echo Coloca este .bat en la carpeta raiz del proyecto TT.
  pause
  exit /b 1
)

rem === Verificar Docker ===
where docker >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Docker no esta instalado o no esta agregado al PATH.
  echo Instala/inicia Docker Desktop y vuelve a ejecutar este archivo.
  pause
  exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
  echo [INFO] Docker Desktop no parece estar iniciado.
  if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
    echo [INFO] Intentando abrir Docker Desktop...
    start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    echo [INFO] Esperando a que Docker quede listo...
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$limite=(Get-Date).AddMinutes(3); while((Get-Date)-lt $limite){ docker info *> $null; if($LASTEXITCODE -eq 0){ exit 0 }; Start-Sleep -Seconds 3 }; exit 1"
    if errorlevel 1 (
      echo [ERROR] Docker Desktop no estuvo listo despues de 3 minutos.
      echo Abre Docker Desktop manualmente y vuelve a intentarlo.
      pause
      exit /b 1
    )
  ) else (
    echo [ERROR] Abre Docker Desktop manualmente y vuelve a intentarlo.
    pause
    exit /b 1
  )
)


echo.
echo [INFO] Iniciando Docker + IA nativa...
echo [INFO] El README indica Tesseract y CUDA si existe GPU NVIDIA.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\start.ps1" -IaNativa
if errorlevel 1 (
  echo.
  echo [ERROR] El arranque con IA nativa termino con errores.
  pause
  exit /b 1
)

echo.
echo [OK] Arranque finalizado.
start "" "https://localhost:8443"
pause
