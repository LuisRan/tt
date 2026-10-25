"""
infrastructure/ocr/clasificador_tolerante.py

Clasificador de tipo de documento TOLERANTE AL RUIDO del OCR. Solo se consulta cuando el
clasificador exacto (regex sobre frases literales) no encontro NINGUN tipo; nunca lo
sustituye ni puede cambiar un tipo que ya se detecto.

En lugar de una sola frase, combina varias senales independientes por tipo:
  * frases clave con coincidencia DIFUSA (difflib, umbral alto) sobre ventanas de palabras
    (tolera acentos, espacios partidos/unidos, letras confundidas);
  * estructuras verificables: MRZ de pasaporte (P<MEX..., linea 2 con 'MEX' + fechas),
    clave de elector (6 letras + 8 digitos + sexo + 3 digitos), cadenas de '<'.
Se exige puntuacion minima y MARGEN sobre el segundo tipo: ante la duda devuelve None
(DOCUMENTO_NO_SOPORTADO es preferible a un tipo equivocado).
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

from domain.entities.documento import TipoDocumento

PUNTOS_MINIMOS = 3      # puntuacion minima del tipo ganador
MARGEN_MINIMO = 2       # ventaja minima sobre el segundo tipo
UMBRAL_FRASE = 0.84     # similitud minima de una frase clave
MAX_CHARS = 30_000      # cota de coste (un documento real tiene unos pocos miles)

# (frase, puntos). Cada senal cuenta UNA vez.
_FRASES: dict[TipoDocumento, list[tuple[str, int]]] = {
    TipoDocumento.PASAPORTE: [
        ("PASAPORTE", 2), ("PASSPORT", 1), ("PASSEPORT", 1),
        ("SECRETARIA DE RELACIONES EXTERIORES", 1), ("DATE OF BIRTH", 1),
        ("DATE OF EXPIRY", 1), ("AUTHORITY", 1),
    ],
    TipoDocumento.INE: [
        ("CREDENCIAL PARA VOTAR", 2), ("INSTITUTO NACIONAL ELECTORAL", 2),
        ("CLAVE DE ELECTOR", 2), ("ANO DE REGISTRO", 1), ("VIGENCIA", 1),
        ("EMISION", 1), ("LOCALIDAD", 1), ("SECCION", 1), ("DOMICILIO", 1),
    ],
    TipoDocumento.ACTA_NACIMIENTO: [
        ("ACTA DE NACIMIENTO", 2), ("REGISTRO CIVIL", 1), ("DATOS DE FILIACION", 2),
        ("ANOTACIONES MARGINALES", 2), ("IDENTIFICADOR ELECTRONICO", 1),
        ("FOLIO DE IMPRESION", 1), ("NUMERO DE CERTIFICADO", 1), ("ENTIDAD DE REGISTRO", 1),
        ("MUNICIPIO DE REGISTRO", 1), ("FIRMA ELECTRONICA AVANZADA", 1),
    ],
    TipoDocumento.CURP: [
        ("CLAVE UNICA DE REGISTRO DE POBLACION", 2), ("CONSTANCIA DE LA CLAVE", 2),
        ("TRAMITE GRATUITO", 1), ("DERECHO A LA IDENTIDAD", 1), ("RENAPO", 1),
        ("TELCURP", 1), ("CURP CERTIFICADA", 1),
    ],
}

# Estructuras (sobre el texto compacto, sin espacios): (tipo, regex, puntos)
_ESTRUCTURAS = [
    (TipoDocumento.PASAPORTE, re.compile(r"P[<K«]{1,2}MEX"), 2),                 # MRZ linea 1
    (TipoDocumento.PASAPORTE, re.compile(r"MEX\d{6,7}[MF<]\d{6}"), 2),            # MRZ linea 2
    (TipoDocumento.PASAPORTE, re.compile(r"<{6,}"), 1),                           # relleno MRZ
    (TipoDocumento.INE, re.compile(r"[A-Z]{6}\d{8}[HM]\d{3}"), 2),                # clave de elector
]


def _normalizar(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    return s[:MAX_CHARS]


def _frase_presente(palabras: list[str], frase: str) -> bool:
    objetivo = frase.replace(" ", "")
    k = len(frase.split())
    tol = max(2, int(0.2 * len(objetivo)))
    sm = SequenceMatcher(None)
    sm.set_seq2(objetivo)
    for i in range(len(palabras)):
        for m in {max(1, k - 1), k, k + 1}:
            if i + m > len(palabras):
                continue
            ventana = "".join(palabras[i:i + m])
            if abs(len(ventana) - len(objetivo)) > tol:
                continue
            sm.set_seq1(ventana)
            if sm.real_quick_ratio() >= UMBRAL_FRASE and sm.quick_ratio() >= UMBRAL_FRASE \
                    and sm.ratio() >= UMBRAL_FRASE:
                return True
    return False


def puntuar(texto: str) -> tuple[dict[TipoDocumento, int], dict[TipoDocumento, int]]:
    norm = _normalizar(texto)
    palabras = re.findall(r"[A-Z0-9<]+", norm)
    compacto = "".join(palabras)
    puntos = {t: 0 for t in _FRASES}
    fuertes = {t: 0 for t in _FRASES}  # senales de >= 2 puntos halladas
    for tipo, frases in _FRASES.items():
        for frase, p in frases:
            if _frase_presente(palabras, frase):
                puntos[tipo] += p
                fuertes[tipo] += p >= 2
    for tipo, patron, p in _ESTRUCTURAS:
        if patron.search(compacto):
            puntos[tipo] += p
            fuertes[tipo] += p >= 2
    return puntos, fuertes


def clasificar_tolerante(texto: str) -> Optional[TipoDocumento]:
    """Tipo ganador con puntuacion y margen suficientes, o None (no clasificar con basura)."""
    try:
        puntos, fuertes = puntuar(texto)
    except Exception:
        return None
    orden = sorted(puntos.items(), key=lambda kv: -kv[1])
    (mejor, p1), (_, p2) = orden[0], orden[1]
    # al menos UNA senal fuerte: varias palabras genericas (DOMICILIO, VIGENCIA...) no bastan
    if p1 >= PUNTOS_MINIMOS and p1 - p2 >= MARGEN_MINIMO and fuertes[mejor] >= 1:
        return mejor
    return None
