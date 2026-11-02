@echo off
setlocal
title TT - Logs backend
cd /d "%~dp0"

echo ============================================================
echo   TT - LOGS DEL BACKEND
echo ============================================================
echo Aqui aparecen tambien los codigos de correo/2FA cuando
echo MAIL_PROVIDER=console, conforme al README.
echo.
echo Presiona Ctrl+C para salir de los logs.
echo.

docker compose logs -f backend
pause
