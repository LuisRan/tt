"""Variantes acotadas para OCR de documentos fotografiados o escaneados.

No modifica los originales ni guarda imágenes en disco. Los filtros se usan SOLO
cuando el primer OCR es insuficiente y siempre se limita el tamaño de salida.
OpenCV es opcional: si no está disponible se usa Pillow sin romper el pipeline.
"""
from __future__ import annotations

import re
import unicodedata

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

# Se usa para decidir entre lecturas, NO es una probabilidad ni confianza del OCR.
_TERMINOS = (
    "CREDENCIAL", "ELECTOR", "INSTITUTO", "CURP", "RENAPO",
    "PASAPORTE", "PASSPORT", "REGISTRO", "NACIMIENTO", "DOMICILIO",
    "CLAVE", "APELLIDO", "NOMBRE", "VIGENCIA", "SECCION", "MEX",
)


def puntuacion_lectura(texto: str) -> float:
    """Puntaje heurístico de legibilidad, acotado para no premiar basura larga."""
    if not texto or not texto.strip():
        return 0.0
    s = unicodedata.normalize("NFKD", texto.upper())
    s = "".join(c for c in s if not unicodedata.combining(c))
    tokens = re.findall(r"[A-Z0-9]{2,}", s)
    if not tokens:
        return 0.0
    caracteres = sum(c.isalnum() for c in s)
    razon = caracteres / max(1, len(s))
    palabras_utiles = sum(1 for t in set(tokens) if len(t) >= 3)
    marcas = sum(1 for p in _TERMINOS if p in tokens)
    # Combinación de calidad y cobertura; crecimiento limitado por documento.
    return min(palabras_utiles, 60) + 5 * marcas + 12 * razon


def lectura_debil(texto: str, minimo: int = 100) -> bool:
    """Heurística de rescate: poco texto o texto predominantemente ilegible."""
    s = (texto or "").strip()
    if len(s) < minimo:
        return True
    return puntuacion_lectura(s) < 18


def _reducir_riesgo_memoria(img: Image.Image, max_pixeles: int) -> Image.Image:
    if img.width * img.height <= max_pixeles:
        return img
    # No ampliar fotografías gigantescas: limita memoria de OpenCV y Tesseract.
    escala = (max_pixeles / (img.width * img.height)) ** 0.5
    return img.resize((max(1, int(img.width * escala)), max(1, int(img.height * escala))))


def variantes_rescate(img: Image.Image, max_pixeles: int = 12_000_000):
    """Genera como máximo 2 imágenes: contraste local y binarización adaptable.

    No se ejecuta para fotografías que ya se leen correctamente. Cada imagen
    tiene dimensiones limitadas; no se almacena contenido personal ni metadatos.
    """
    gris = _reducir_riesgo_memoria(ImageOps.autocontrast(img.convert("L")), max_pixeles)
    try:
        import cv2
        import numpy as np
        matriz = np.asarray(gris)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(matriz)
        mejorada = Image.fromarray(clahe).filter(ImageFilter.UnsharpMask(radius=1.2, percent=125, threshold=3))
        yield mejorada
        # Umbral adaptativo útil cuando la iluminación del documento es desigual.
        if min(gris.size) >= 35:
            binaria = cv2.adaptiveThreshold(clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                            cv2.THRESH_BINARY, 31, 11)
            yield Image.fromarray(binaria)
    except ImportError:
        # Degradación controlada cuando OpenCV no está instalado.
        yield ImageEnhance.Contrast(gris.filter(ImageFilter.UnsharpMask(radius=1))).enhance(1.5)
