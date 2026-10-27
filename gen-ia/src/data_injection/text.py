"""Normalización de texto compartida por generadores y validadores."""
from __future__ import annotations

import re
import unicodedata

ENYE = "Ñ"
_PLACEHOLDER = "\x00"


def upper_id(s: str) -> str:
    """Mayúsculas SIN acentos, conservando la Ñ (estilo credencial INE / RENAPO).

    Ü -> U, Á -> A, ... ; espacios colapsados.
    """
    s = s.replace("ñ", _PLACEHOLDER).replace(ENYE, _PLACEHOLDER)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.upper().replace(_PLACEHOLDER, ENYE)
    return re.sub(r"\s+", " ", s).strip()


def name_words(s: str) -> list[str]:
    """Palabras de un nombre normalizado (solo letras A-Z y Ñ)."""
    s = upper_id(s)
    s = re.sub(r"[.'’]", "", s)
    s = re.sub(r"[^A-ZÑ ]", " ", s)
    return s.split()


def icao_transliterate(s: str) -> str:
    """Transliteración ICAO 9303 (parte 3): sin acentos, Ñ->N, Ü->U,
    espacios/guiones/apóstrofos -> '<', cualquier otro símbolo eliminado."""
    s = upper_id(s).replace(ENYE, "N")
    s = re.sub(r"[ \-'’]", "<", s)
    return re.sub(r"[^A-Z0-9<]", "", s)
