#!/usr/bin/env python3
"""
serve.py — arranca la API REST del modulo de IA eligiendo el servidor WSGI
segun el sistema operativo:

  - macOS / Linux / Docker -> gunicorn (multi-proceso, timeout largo)
  - Windows                -> waitress (gunicorn no funciona en Windows)
  - Si ninguno esta instalado -> servidor de desarrollo de Flask

Uso:
    python serve.py            # usa IA_API_HOST / IA_API_PORT del .env
    python serve.py --dev      # fuerza el servidor de desarrollo de Flask
"""
from __future__ import annotations

import importlib.util
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from config import settings  # noqa: E402


def main() -> None:
    sistema = platform.system()
    host, port = settings.IA_API_HOST, settings.IA_API_PORT
    print(f"[ia_documentos] SO detectado: {sistema} ({platform.machine()}) -> "
          f"API en http://{host}:{port}")

    if "--dev" in sys.argv:
        from integration.flask_app import app
        app.run(host=host, port=port)
        return

    if sistema != "Windows" and importlib.util.find_spec("gunicorn"):
        os.execvp(sys.executable, [
            sys.executable, "-m", "gunicorn",
            "--bind", f"{host}:{port}",
            "--workers", str(settings.IA_WORKERS),
            "--threads", "4",
            "--timeout", str(settings.IA_REQUEST_TIMEOUT),
            "--graceful-timeout", "30",
            "--access-logfile", "-",
            "integration.flask_app:app",
        ])

    if importlib.util.find_spec("waitress"):
        from waitress import serve
        from integration.flask_app import app
        serve(app, host=host, port=port, threads=4, channel_timeout=settings.IA_REQUEST_TIMEOUT)
        return

    from integration.flask_app import app
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
