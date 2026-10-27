"""Normalización y comparación POR CAMPO.

Principio: una normalización nunca convierte un valor incorrecto en correcto.

- IDENTIFIER (curp, clave_elector, numero_pasaporte, folios, identificador_electronico,
  mrz_*): solo se permite mayúsculas y quitar espacios (externos e internos de formato).
  Jamás se quitan dígitos ni ceros iniciales ni se confunde O/0, I/1, etc.
- CODE (estado, municipio, seccion, localidad, años, oficialia, libro, numero_acta,
  tipo/pais): se permite mayúsculas y quitar espacios externos; los ceros iniciales cuentan.
- TEXT (nombres, domicilio, lugares): NFC + casefold + colapso de espacios => NORMALIZED.
  Igualdad solo sin acentos (o con orden alterno declarado) => PARTIAL, nunca correcto.
- ADDRESS (solo `domicilio`): como TEXT, pero `, . ;` se reemplazan por espacio y se colapsan
  (ignora puntuación). Un token distinto ('504' vs '540') sigue siendo MISMATCH.
- DATE: se parsea a fecha calendario (DD/MM/AAAA, DD MM AAAA, AAAA-MM-DD); ilegible o
  imposible (31/02) => UNINTERPRETABLE.
- SEX_MX (INE/ACTA/CURP): H=hombre, M=mujer, F=mujer, X, HOMBRE, MUJER.
  SEX_ICAO (pasaporte): M=masculino, F=femenino, H=hombre, X, HOMBRE, MUJER.
  Una letra suelta se interpreta según la convención del documento.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from enum import Enum
from typing import Optional, Sequence

from .results import FieldStatus


class FieldKind(str, Enum):
    IDENTIFIER = "identifier"
    CODE = "code"
    TEXT = "text"
    ADDRESS = "address"  # TEXT que además ignora los signos `, . ;`
    DATE = "date"
    SEX_MX = "sex_mx"
    SEX_ICAO = "sex_icao"


def normalize_identifier(s: str) -> str:
    return "".join(s.upper().split())


def normalize_code(s: str) -> str:
    return s.strip().upper()


def normalize_text(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", s).casefold().split())


_ADDR_PUNCT = re.compile(r"[,.;]")


def normalize_address(s: str) -> str:
    return normalize_text(_ADDR_PUNCT.sub(" ", s))


def strip_accents(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    return unicodedata.normalize("NFC", "".join(c for c in nfd if unicodedata.category(c) != "Mn"))


_DATE_RES = (
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), "dmy"),
    (re.compile(r"^(\d{1,2}) +(\d{1,2}) +(\d{4})$"), "dmy"),
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), "ymd"),
)


def parse_date(s: str) -> Optional[date]:
    t = s.strip()
    for rx, order in _DATE_RES:
        m = rx.match(t)
        if not m:
            continue
        a, b, c = (int(x) for x in m.groups())
        d, mo, y = (a, b, c) if order == "dmy" else (c, b, a)
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


_SEX_COMMON = {"X": "X", "HOMBRE": "HOMBRE", "MUJER": "MUJER", "MASCULINO": "HOMBRE", "FEMENINO": "MUJER"}
_SEX_MX = {**_SEX_COMMON, "H": "HOMBRE", "M": "MUJER", "F": "MUJER"}
_SEX_ICAO = {**_SEX_COMMON, "M": "HOMBRE", "F": "MUJER", "H": "HOMBRE"}


def canonical_sex(s: str, kind: FieldKind) -> Optional[str]:
    table = _SEX_ICAO if kind == FieldKind.SEX_ICAO else _SEX_MX
    return table.get(" ".join(s.upper().split()))


def compare_values(
    kind: FieldKind,
    expected: str,
    actual: str,
    alternatives: Sequence[str] = (),
) -> FieldStatus:
    """Compara dos valores NO vacíos. Devuelve EXACT/NORMALIZED/PARTIAL/MISMATCH/UNINTERPRETABLE."""
    if actual == expected:
        return FieldStatus.EXACT
    if kind == FieldKind.IDENTIFIER:
        ok = normalize_identifier(actual) == normalize_identifier(expected)
        return FieldStatus.NORMALIZED if ok else FieldStatus.MISMATCH
    if kind == FieldKind.CODE:
        ok = normalize_code(actual) == normalize_code(expected)
        return FieldStatus.NORMALIZED if ok else FieldStatus.MISMATCH
    if kind in (FieldKind.TEXT, FieldKind.ADDRESS):
        norm = normalize_address if kind == FieldKind.ADDRESS else normalize_text
        na, ne = norm(actual), norm(expected)
        if na == ne:
            return FieldStatus.NORMALIZED
        alts = [norm(a) for a in alternatives]
        if na in alts:
            return FieldStatus.PARTIAL
        sa = strip_accents(na)
        if sa == strip_accents(ne) or sa in [strip_accents(a) for a in alts]:
            return FieldStatus.PARTIAL
        return FieldStatus.MISMATCH
    if kind == FieldKind.DATE:
        da, de = parse_date(actual), parse_date(expected)
        if da is None or de is None:
            return FieldStatus.UNINTERPRETABLE
        return FieldStatus.NORMALIZED if da == de else FieldStatus.MISMATCH
    if kind in (FieldKind.SEX_MX, FieldKind.SEX_ICAO):
        ca, ce = canonical_sex(actual, kind), canonical_sex(expected, kind)
        if ca is None or ce is None:
            return FieldStatus.UNINTERPRETABLE
        return FieldStatus.NORMALIZED if ca == ce else FieldStatus.MISMATCH
    raise ValueError(f"kind desconocido: {kind}")
