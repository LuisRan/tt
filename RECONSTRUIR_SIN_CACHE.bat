@echo off
setlocal
title TT - Rebuild sin cache
cd /d "%~dp0"

echo ============================================================
echo   TT - RECONSTRUIR BACKEND Y WEB SIN CACHE
echo ============================================================
echo.
echo Usalo cuando los cambios de codigo no se reflejen.
echo.

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


if exist "scripts\stop.ps1" (
  echo [1/3] Deteniendo servicios...
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\stop.ps1"
  if errorlevel 1 (
    echo [ERROR] No se pudieron detener los servicios.
    pause
    exit /b 1
  )
)

echo.
echo [2/3] Construyendo backend y web sin cache...
docker compose build --no-cache backend web
if errorlevel 1 (
  echo [ERROR] Fallo la reconstruccion sin cache.
  pause
  exit /b 1
)

echo.
echo [3/3] Iniciando plataforma...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\start.ps1"
if errorlevel 1 (
  echo [ERROR] El arranque posterior al rebuild fallo.
  pause
  exit /b 1
)

echo.
echo [OK] Reconstruccion finalizada.
start "" "https://localhost:8443"
pause
