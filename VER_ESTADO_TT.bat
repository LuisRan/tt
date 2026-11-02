@echo off
setlocal
title TT - Estado de servicios
cd /d "%~dp0"

echo ============================================================
echo   TT - ESTADO DE SERVICIOS
echo ============================================================
echo.

docker compose ps

echo.
echo URL principal esperada: https://localhost:8443
echo.
pause
