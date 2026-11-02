EJECUTABLES TT - WINDOWS 10/11
=================================

Coloca TODOS estos archivos .bat en la RAIZ del proyecto, al mismo
nivel que README.md y la carpeta scripts.

1) EJECUTAR_TT.bat
   Ejecuta scripts\start.ps1.
   Levanta la plataforma completa con Docker Desktop.
   Al terminar abre https://localhost:8443.

2) EJECUTAR_TT_IA_NATIVA.bat
   Ejecuta scripts\start.ps1 -IaNativa.
   Usa la variante indicada en el README para IA nativa en Windows.

3) DETENER_TT.bat
   Ejecuta scripts\stop.ps1.
   Conserva los datos/volumenes.

4) REINICIAR_TT.bat
   Ejecuta stop.ps1 y despues start.ps1.

5) RECONSTRUIR_SIN_CACHE.bat
   Sigue el flujo del README cuando los cambios no se reflejan:
   detener -> docker compose build --no-cache backend web -> iniciar.

6) VER_LOGS_BACKEND.bat
   Ejecuta docker compose logs -f backend.
   Con MAIL_PROVIDER=console aqui aparecen los codigos de verificacion/2FA.

7) VER_ESTADO_TT.bat
   Ejecuta docker compose ps.

8) ABRIR_TT.bat
   Abre https://localhost:8443.

IMPORTANTE
----------
- No necesitas ejecutar Set-ExecutionPolicy manualmente:
  estos launchers usan -ExecutionPolicy Bypass solo para el proceso lanzado.
- Docker Desktop debe estar instalado.
- Si Docker esta instalado pero cerrado, EJECUTAR_TT intenta abrirlo.
- El README pide al menos 8 GB de RAM asignados a Docker.
- Si aparece advertencia por HTTPS, es normal con certificado autofirmado.
- El .env lo administra scripts\start.ps1 conforme al README.
