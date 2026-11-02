@echo off
setlocal
title TT - Detener
cd /d "%~dp0"

echo ============================================================
echo   TT - DETENER SERVICIOS
echo ============================================================
echo.

if not exist "scripts\stop.ps1" (
  echo [ERROR] No encuentro scripts\stop.ps1
  echo Coloca este .bat en la carpeta raiz del proyecto TT.
  pause
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\stop.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] Hubo un problema al detener los servicios.
  pause
  exit /b 1
)

echo.
echo [OK] Servicios detenidos. Los volumenes/datos se conservan.
pause
