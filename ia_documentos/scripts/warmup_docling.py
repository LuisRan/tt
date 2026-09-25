#!/usr/bin/env python3
"""
scripts/warmup_docling.py
Pre-descarga los modelos de Docling (layout, TableFormer, OCR) ejecutando una
conversion de prueba con la MISMA configuracion que usara el pipeline en este
sistema operativo. Asi el primer documento real no tarda minutos.

Uso:  python scripts/warmup_docling.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from PIL import Image, ImageDraw

    from infrastructure.platform_config import crear_conversor_docling, detectar_plataforma

    info = detectar_plataforma()
    print(f"[warmup] SO={info.sistema} arq={info.arquitectura} docker={info.en_docker} "
          f"motor_ocr={info.motor_ocr_docling} dispositivo={info.dispositivo} "
          f"tablas={info.tablas_habilitadas}")
    for nota in info.notas:
        print(f"[warmup] nota: {nota}")

    img = Image.new("RGB", (1240, 400), "white")
    ImageDraw.Draw(img).text((40, 40), "CREDENCIAL PARA VOTAR  PRUEBA DOCLING 2026", fill="black")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "warmup.pdf"
        img.save(pdf, "PDF", resolution=150)
        inicio = time.time()
        conv = crear_conversor_docling()
        conv.convert(str(pdf))
        print(f"[warmup] Docling listo en {time.time() - inicio:.1f}s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # no bloquear la instalacion
        print(f"[warmup] AVISO: no se pudo precargar Docling: {e}")
        sys.exit(0)
