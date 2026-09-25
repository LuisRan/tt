"""
infrastructure/ocr/pdf_validator.py
Valida y corrige orientación del PDF antes de pasarlo a Docling.
La corrección de orientación es silenciosa (el usuario no se entera).
"""
from __future__ import annotations
import hashlib
import io
import logging
from pathlib import Path
from typing import Tuple

import pypdf

logger = logging.getLogger(__name__)


class PdfValidationError(Exception):
    """Se lanza cuando el PDF no cumple los requisitos de ingesta."""
    pass


def validar_pdf(ruta: Path, max_mb: int = 10, max_paginas: int = 4) -> Tuple[str, int]:
    """
    Valida el archivo PDF y calcula su hash SHA-256.

    Returns:
        (hash_sha256, num_paginas)

    Raises:
        PdfValidationError si alguna validación falla.
    """
    if not ruta.exists():
        raise PdfValidationError(f"Archivo no encontrado: {ruta}")

    if ruta.suffix.lower() != ".pdf":
        raise PdfValidationError(f"El archivo no es un PDF: {ruta.name}")

    size_mb = ruta.stat().st_size / (1024 * 1024)
    if size_mb > max_mb:
        raise PdfValidationError(
            f"Tamaño {size_mb:.1f} MB excede el límite de {max_mb} MB"
        )

    # Hash y conteo de páginas
    sha256 = hashlib.sha256()
    with open(ruta, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)

    try:
        reader = pypdf.PdfReader(str(ruta))
        num_paginas = len(reader.pages)
    except Exception as e:
        raise PdfValidationError(f"No se pudo leer el PDF: {e}")

    if num_paginas > max_paginas:
        raise PdfValidationError(
            f"El PDF tiene {num_paginas} páginas; máximo permitido: {max_paginas}"
        )

    logger.info(
        "pdf_validado",
        extra={"archivo": ruta.name, "paginas": num_paginas, "size_mb": round(size_mb, 2)},
    )
    return sha256.hexdigest(), num_paginas


def corregir_orientacion(ruta_origen: Path, ruta_destino: Path) -> bool:
    """
    Detecta páginas giradas y las corrige en una copia del PDF.
    Retorna True si se realizó alguna corrección, False si no fue necesario.

    La estrategia: pypdf puede rotar páginas según los metadatos /Rotate.
    Para documentos escaneados girados físicamente, Docling activa su pipeline
    de visión y lo maneja por cuenta propia; aquí solo normalizamos el flag.
    """
    reader = pypdf.PdfReader(str(ruta_origen))
    writer = pypdf.PdfWriter()
    corregido = False

    for page in reader.pages:
        rotation = page.get("/Rotate", 0)
        if rotation and rotation != 0:
            # Normalizar a 0 aplicando la rotación inversa al writer
            page.rotate(-rotation)
            corregido = True
        writer.add_page(page)

    if corregido:
        with open(ruta_destino, "wb") as f:
            writer.write(f)
        logger.info(
            "orientacion_corregida",
            extra={"origen": ruta_origen.name, "destino": ruta_destino.name},
        )
    else:
        # Sin cambios: copiar igual
        import shutil
        shutil.copy2(ruta_origen, ruta_destino)

    return corregido
