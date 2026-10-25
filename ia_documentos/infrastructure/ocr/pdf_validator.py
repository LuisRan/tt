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
from typing import Any, Optional, Tuple

import pypdf

logger = logging.getLogger(__name__)

# Claves de acciones activas / adjuntos que se rechazan (R2).
_CLAVES_ACTIVAS = ("/JS", "/JavaScript", "/Launch", "/EmbeddedFile", "/EmbeddedFiles")


def _tope_pixeles() -> int:
    from config import settings
    return int(settings.PDF_MAX_PIXELS)


def _tope_dim_pt() -> int:
    from config import settings
    return int(settings.PDF_MAX_DIM_PT)


def _dpi_render() -> int:
    from config import settings
    return int(settings.OCR_DPI)


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
        if reader.is_encrypted:
            raise PdfValidationError("El PDF está cifrado; no se admite")
        num_paginas = len(reader.pages)
    except PdfValidationError:
        raise
    except Exception as e:
        raise PdfValidationError(f"No se pudo leer el PDF: {e}")

    if num_paginas > max_paginas:
        raise PdfValidationError(
            f"El PDF tiene {num_paginas} páginas; máximo permitido: {max_paginas}"
        )

    _verificar_seguridad(reader)
    _verificar_dimensiones(reader)

    logger.info(
        "pdf_validado",
        extra={"archivo": ruta.name, "paginas": num_paginas, "size_mb": round(size_mb, 2)},
    )
    return sha256.hexdigest(), num_paginas


def _buscar_activos(obj: Any, vistos: set, profundidad: int = 0) -> Optional[str]:
    """Busca recursivamente claves de acciones activas; devuelve la primera hallada."""
    if profundidad > 40:
        return None
    try:
        if hasattr(obj, "get_object"):
            obj = obj.get_object()
    except Exception:
        return None
    ident = id(obj)
    if ident in vistos:
        return None
    vistos.add(ident)
    if isinstance(obj, dict):
        for clave, valor in obj.items():
            if str(clave) in _CLAVES_ACTIVAS:
                return str(clave)
            if str(clave) == "/S" and str(valor) in ("/JavaScript", "/Launch"):
                return str(valor)
            if str(clave) in ("/Parent", "/P"):
                continue
            r = _buscar_activos(valor, vistos, profundidad + 1)
            if r:
                return r
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            r = _buscar_activos(v, vistos, profundidad + 1)
            if r:
                return r
    return None


def _verificar_seguridad(reader: "pypdf.PdfReader") -> None:
    """R2: rechaza PDFs cifrados o con JavaScript/Launch/OpenAction/adjuntos."""
    if reader.is_encrypted:
        raise PdfValidationError("El PDF está cifrado; no se admite")
    try:
        raiz = reader.trailer["/Root"]
        hallado = _buscar_activos(raiz, set())
        if not hallado:
            for pagina in reader.pages:
                hallado = _buscar_activos(pagina, set())
                if hallado:
                    break
    except PdfValidationError:
        raise
    except Exception as e:
        raise PdfValidationError(f"No se pudo inspeccionar el PDF: {e}")
    if hallado:
        raise PdfValidationError(
            f"El PDF contiene contenido activo o adjuntos no permitidos ({hallado})"
        )


def _verificar_dimensiones(reader: "pypdf.PdfReader") -> None:
    """R1: rechaza páginas cuya rasterización excedería el tope de píxeles."""
    max_px, max_dim, dpi = _tope_pixeles(), _tope_dim_pt(), _dpi_render()
    for n, pagina in enumerate(reader.pages, start=1):
        try:
            caja = pagina.mediabox
            ancho, alto = abs(float(caja.width)), abs(float(caja.height))
        except Exception as e:
            raise PdfValidationError(f"Página {n}: MediaBox inválido ({e})")
        if ancho > max_dim or alto > max_dim:
            raise PdfValidationError(
                f"Página {n}: dimensiones {ancho:.0f}x{alto:.0f} pt exceden el máximo de {max_dim} pt"
            )
        pixeles = (ancho / 72.0) * (alto / 72.0) * dpi * dpi
        if pixeles > max_px:
            raise PdfValidationError(
                f"Página {n}: {pixeles / 1e6:.0f} MP al rasterizar exceden el máximo de {max_px / 1e6:.0f} MP"
            )


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
