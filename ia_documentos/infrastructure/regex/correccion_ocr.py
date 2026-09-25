"""
infrastructure/regex/correccion_ocr.py

Corrección de errores típicos de OCR mediante VALIDACIÓN CRUZADA entre campos.

Los documentos de identidad mexicanos tienen redundancia estructural que
permite detectar y corregir errores de lectura sin inventar datos:

  CURP (18):  AAAA YYMMDD S EE CCC H D
              │    │      │ │  │   │ └ dígito verificador (algoritmo RENAPO)
              │    │      │ │  │   └ homoclave (dígito si nació antes de 2000)
              │    │      │ │  └ consonantes internas de apellidos y nombre
              │    │      │ └ entidad de nacimiento
              │    │      └ sexo (H/M)
              │    └ fecha de nacimiento
              └ iniciales de apellidos y nombre

  Clave de elector (18): CCCCCC YYMMDD EE S HHH
              consonantes de apellidos/nombre, fecha, entidad (numérica),
              sexo y homoclave de 3 dígitos.

Estrategia:
  1. Mapeo de confusiones OCR según la POSICIÓN (en una posición que debe ser
     dígito, 'O'->0, 'S'->5, 'B'->8...; en una que debe ser letra, al revés).
  2. Validación con el dígito verificador de la CURP.
  3. Reconstrucción de las posiciones derivables de otros campos ya leídos
     (nombre, fecha, sexo) y comparación difusa contra el texto OCR.

Un valor corregido SOLO se acepta si es consistente con el resto del documento
y suficientemente parecido (distancia de edición baja) a lo que el OCR leyó.
Así se conserva el principio anti-alucinación: se corrige, no se inventa.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, Optional

# ── Diccionarios de confusión OCR ─────────────────────────────────────────────

_A_DIGITO = {
    "O": "0", "Q": "0", "D": "0", "U": "0", "C": "0",
    "I": "1", "L": "1", "|": "1", "!": "1", "Í": "1", "J": "1", "T": "7",
    "Z": "2", "S": "5", "$": "5", "B": "8", "G": "6", "A": "4", "E": "8", "F": "7",
}
_A_LETRA = {
    "0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "7": "T", "4": "A", "3": "B",
    "|": "I", "!": "I",
}
# Clases de caracteres que el OCR confunde entre sí (para comparación difusa)
_CLASES = [
    "0ODQUC", "1IL|!ÍJ", "5S$", "8BR3E", "6G", "7TF", "2Z", "4A", "UV", "MN", "HN",
]
_CANON = {c: grupo[0] for grupo in _CLASES for c in grupo}

_DICC_CURP = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"

ENTIDADES_CURP = {
    "AS": "01", "BC": "02", "BS": "03", "CC": "04", "CL": "05", "CM": "06", "CS": "07",
    "CH": "08", "DF": "09", "DG": "10", "GT": "11", "GR": "12", "HG": "13", "JC": "14",
    "MC": "15", "MN": "16", "MS": "17", "NT": "18", "NL": "19", "OC": "20", "PL": "21",
    "QT": "22", "QR": "23", "SP": "24", "SL": "25", "SR": "26", "TC": "27", "TS": "28",
    "TL": "29", "VZ": "30", "YN": "31", "ZS": "32", "NE": "87",
}

_PARTICULAS = {"DE", "DEL", "LA", "LAS", "LOS", "Y", "MC", "MAC", "VAN", "VON", "DA", "DI", "DD"}
_NOMBRES_COMUNES = {"JOSE", "J", "J.", "MARIA", "MA", "MA.", "M", "M."}
_VOCALES = set("AEIOU")

RE_CURP_ESTRICTA = re.compile(r"^[A-Z]{4}\d{6}[HM][A-Z]{2}[B-DF-HJ-NP-TV-ZÑ]{3}[A-Z0-9]\d$")
RE_CLAVE_ESTRICTA = re.compile(r"^[A-Z]{6}\d{8}[HM]\d{3}$")


# ── Utilidades ────────────────────────────────────────────────────────────────

def sin_acentos(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c) or c == "̃").replace("Ñ", "Ñ")


def canonico(s: str) -> str:
    """Reduce un texto a su 'forma OCR' (clases de caracteres confundibles)."""
    s = sin_acentos(str(s).upper())
    return "".join(_CANON.get(c, c) for c in s if c.isalnum() or c in "|!$")


def distancia(a: str, b: str) -> int:
    """Distancia de Levenshtein."""
    if len(a) < len(b):
        a, b = b, a
    previa = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i]
        for j, cb in enumerate(b, 1):
            actual.append(min(previa[j] + 1, actual[j - 1] + 1, previa[j - 1] + (ca != cb)))
        previa = actual
    return previa[-1]


def similitud_etiqueta(texto: str, etiqueta: str) -> float:
    a = re.sub(r"[^A-Z ]", "", sin_acentos(texto.upper())).strip()
    b = re.sub(r"[^A-Z ]", "", sin_acentos(etiqueta.upper())).strip()
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def aparece_con_tolerancia(valor: str, texto: str, max_ratio: float = 0.2) -> bool:
    """
    True si `valor` aparece en `texto` admitiendo confusiones típicas de OCR
    y una distancia de edición de hasta max_ratio * len(valor).
    """
    v = canonico(valor)
    if len(v) < 4:
        return v in canonico(texto)
    t = canonico(texto)
    if v in t:
        return True
    limite = max(1, int(len(v) * max_ratio))
    n = len(v)
    for tam in (n - 1, n, n + 1):
        if tam <= 0:
            continue
        for i in range(0, max(1, len(t) - tam + 1)):
            if distancia(v, t[i:i + tam]) <= limite:
                return True
    return False


# ── CURP ──────────────────────────────────────────────────────────────────────

def digito_verificador_curp(curp17: str) -> Optional[str]:
    try:
        suma = sum(_DICC_CURP.index(c) * (18 - i) for i, c in enumerate(curp17[:17]))
    except ValueError:
        return None
    return str((10 - suma % 10) % 10)


def normalizar_curp(curp: Optional[str]) -> Optional[str]:
    """
    Corrige confusiones que el dígito verificador NO detecta: en la posición
    17 (peso 2) 'O' y '0' dan la misma suma módulo 10. Si la persona nació
    antes de 2000 esa posición siempre es un dígito.
    """
    if not curp or len(curp) != 18:
        return curp
    c = list(curp.upper())
    for i in list(range(4, 10)) + [17]:
        if not c[i].isdigit():
            c[i] = _A_DIGITO.get(c[i], c[i])
    if "".join(c[4:6]).isdigit() and int("".join(c[4:6])) > 30 and not c[16].isdigit():
        c[16] = _A_DIGITO.get(c[16], c[16])
    return "".join(c)


def curp_valida(curp: Optional[str]) -> bool:
    return bool(curp) and bool(RE_CURP_ESTRICTA.match(curp)) and digito_verificador_curp(curp) == curp[17]


def _palabras_significativas(texto: str) -> list[str]:
    ps = [p for p in re.split(r"[\s\-]+", sin_acentos((texto or "").upper())) if p]
    return [p for p in ps if p not in _PARTICULAS] or ps


def _primera_vocal_interna(p: str) -> str:
    return next((c for c in p[1:] if c in _VOCALES), "X")


def _primera_consonante_interna(p: str) -> str:
    c = next((c for c in p[1:] if c.isalpha() and c not in _VOCALES), "X")
    return "X" if c == "Ñ" else c


def _nombre_para_curp(nombres: str) -> str:
    ps = _palabras_significativas(nombres)
    if len(ps) > 1 and ps[0] in _NOMBRES_COMUNES:
        return ps[1]
    return ps[0] if ps else ""


def pistas_curp(nombres: Optional[str], ap1: Optional[str], ap2: Optional[str]) -> dict:
    """Posiciones de la CURP derivables del nombre: {indice: caracter}."""
    pistas: dict[int, str] = {}
    a1 = (_palabras_significativas(ap1 or "") or [""])[0]
    a2 = (_palabras_significativas(ap2 or "") or [""])[0]
    nom = _nombre_para_curp(nombres or "")
    if a1:
        pistas[0] = a1[0]
        pistas[1] = _primera_vocal_interna(a1)
        pistas[13] = _primera_consonante_interna(a1)
    if ap2 is not None:
        pistas[2] = a2[0] if a2 else "X"
        pistas[14] = _primera_consonante_interna(a2) if a2 else "X"
    if nom:
        pistas[3] = nom[0]
        pistas[15] = _primera_consonante_interna(nom)
    return {k: v.replace("Ñ", "X") for k, v in pistas.items()}


def _mapear_posiciones(token: str, plantilla: str) -> str:
    """plantilla: 'L' letra, 'D' dígito, 'S' sexo H/M, 'A' alfanumérico."""
    salida = []
    for c, t in zip(token, plantilla):
        if t == "D" and not c.isdigit():
            c = _A_DIGITO.get(c, c)
        elif t in ("L", "S") and c.isdigit():
            c = _A_LETRA.get(c, c)
        salida.append(c)
    return "".join(salida)


_PLANTILLA_CURP = "LLLLDDDDDDSLLLLLAD"


def _candidatos_token(texto: str, largo: int, holgura: int = 2, cerca_de: Optional[str] = None) -> list[str]:
    """
    Fragmentos alfanuméricos del texto (uniendo espacios) cuyo largo esté en
    [largo - holgura, largo + holgura]. Si se da una etiqueta aproximada, se
    priorizan los fragmentos de líneas que la contienen.
    """
    candidatos: list[tuple[int, str]] = []
    for linea in texto.upper().splitlines():
        prioridad = 0
        if cerca_de:
            palabras = linea.split()
            if any(similitud_etiqueta(p, cerca_de) >= 0.6 for p in palabras[:4]):
                prioridad = -1
        # tokens separados por espacio y también pares/tríos unidos (el OCR parte palabras)
        partes = [re.sub(r"[^A-Z0-9ÑÍ|!$]", "", p) for p in linea.split()]
        partes = [p for p in partes if p]
        for i in range(len(partes)):
            union = ""
            for j in range(i, min(i + 4, len(partes))):
                union += partes[j]
                if largo - holgura <= len(union) <= largo + holgura:
                    candidatos.append((prioridad, union))
                if len(union) > largo + holgura:
                    break
    candidatos.sort(key=lambda x: x[0])
    vistos, salida = set(), []
    for _, c in candidatos:
        if c not in vistos:
            vistos.add(c)
            salida.append(c)
    return salida


def corregir_curp(
    texto: str,
    nombres: Optional[str] = None,
    ap1: Optional[str] = None,
    ap2: Optional[str] = None,
    fecha_nacimiento: Optional[str] = None,
    sexo: Optional[str] = None,
) -> tuple[Optional[str], bool]:
    """
    Busca la CURP en el texto OCR y la corrige.
    Devuelve (curp, fue_corregida). None si no hay evidencia suficiente.
    """
    pistas = pistas_curp(nombres, ap1, ap2)
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", fecha_nacimiento or "")
    if m:
        dd, mm, yyyy = m.groups()
        for i, c in enumerate(yyyy[2:] + mm + dd):
            pistas.setdefault(4 + i, c)
    if sexo in ("H", "M"):
        pistas.setdefault(10, sexo)

    mejores: list[tuple[int, str, bool]] = []
    tokens_evidencia: list[str] = []
    for token in _candidatos_token(texto, 18, 2, cerca_de="CURP"):
        antes = len(mejores)
        variantes = [token]
        if len(token) != 18:
            # eliminar/añadir un carácter espurio (el OCR mete o come espacios)
            variantes = [token[:i] + token[i + 1:] for i in range(len(token))] if len(token) > 18 else []
            if len(token) == 17:
                variantes = [token[:i] + "X" + token[i:] for i in range(18)]
            variantes = [v for v in variantes if len(v) == 18]
        for v in variantes:
            base = _mapear_posiciones(v, _PLANTILLA_CURP)
            if not re.match(r"^[A-Z]{4}\d{6}[HM][A-Z]{2}", base):
                continue
            if base[11:13] not in ENTIDADES_CURP:
                continue
            # la homoclave (pos 16) es dígito si nació antes de 2000
            if base[4:6].isdigit() and int(base[4:6]) > 30 and not base[16].isdigit():
                base = base[:16] + _A_DIGITO.get(base[16], base[16]) + base[17]
            # 1) Evidencia fuerte: el dígito verificador LEÍDO por el OCR confirma
            #    la CURP tras corregir el mínimo de posiciones con las pistas.
            dv_ocr = base[17]
            grupos = [(), (13, 14, 15), tuple(range(4, 10)), (13, 14, 15) + tuple(range(4, 10)),
                      (0, 1, 2, 3, 13, 14, 15), tuple(sorted(pistas))]
            aceptada = None
            for grupo in grupos:
                lista = list(base)
                for pos in grupo:
                    if pos in pistas:
                        lista[pos] = pistas[pos]
                cand = "".join(lista)
                if RE_CURP_ESTRICTA.match(cand) and digito_verificador_curp(cand) == dv_ocr:
                    aceptada = cand
                    break
            if aceptada:
                dist = distancia(canonico(aceptada), canonico(token))
                if dist <= 6:
                    mejores.append((dist, aceptada, aceptada != token))
                continue
            # 2) Evidencia débil: se recalcula el dígito verificador con las
            #    pistas; solo se acepta si queda muy parecida a lo leído.
            #    Si el OCR leyó una CURP con formato perfecto y solo falla el
            #    dígito, no se reescribe: el error podría estar en cualquier
            #    posición y se deja para que el usuario la revise.
            if RE_CURP_ESTRICTA.match(token):
                continue
            lista = list(base)
            for pos, c in pistas.items():
                lista[pos] = c
            cand = "".join(lista[:17])
            dv = digito_verificador_curp(cand)
            if dv is None:
                continue
            cand += dv
            if RE_CURP_ESTRICTA.match(cand):
                dist = distancia(canonico(cand), canonico(token))
                if dist <= 4:
                    mejores.append((dist + 2, cand, True))  # penaliza evidencia débil
        if len(mejores) > antes:
            tokens_evidencia.append(token)
    if not mejores:
        return None, False
    return _elegir_por_consenso(mejores, tokens_evidencia)


def _elegir_por_consenso(mejores: list[tuple[int, str, bool]], tokens: list[str]) -> tuple[str, bool]:
    """
    Cuando el mismo dato aparece varias veces en el texto (el OCR de zonas lo
    lee dos veces con errores distintos), gana el candidato más cercano a
    TODAS las lecturas: cada lectura 'vota' por sus caracteres.
    """
    tokens = list(dict.fromkeys(tokens)) or [m[1] for m in mejores]
    puntuados = []
    for penal, cand, corregida in {(m[0], m[1], m[2]) for m in mejores}:
        cc = canonico(cand)
        total = sum(min(distancia(cc, canonico(t)), 8) for t in tokens)
        puntuados.append((total + (2 if penal >= 2 and corregida else 0), penal, cand, corregida))
    puntuados.sort()
    return puntuados[0][2], puntuados[0][3]


# ── Clave de elector ──────────────────────────────────────────────────────────

def _letras_clave(palabra: str) -> str:
    """Primera letra + primera consonante interna (regla de la clave de elector)."""
    p = (_palabras_significativas(palabra) or [""])[0]
    if not p:
        return "XX"
    return (p[0] + _primera_consonante_interna(p)).replace("Ñ", "X")


_PLANTILLA_CLAVE = "LLLLLLDDDDDDDDSDDD"


def corregir_clave_elector(
    texto: str,
    nombres: Optional[str] = None,
    ap1: Optional[str] = None,
    ap2: Optional[str] = None,
    fecha_nacimiento: Optional[str] = None,
    sexo: Optional[str] = None,
    curp: Optional[str] = None,
) -> tuple[Optional[str], bool]:
    """
    Busca la clave de elector y la corrige con validación cruzada.
    Devuelve (clave, fue_corregida).
    """
    esperado = None
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", fecha_nacimiento or "")
    entidad = ENTIDADES_CURP.get(curp[11:13]) if curp and len(curp) >= 13 else None
    if ap1 and nombres and m and sexo in ("H", "M") and entidad:
        dd, mm, yyyy = m.groups()
        esperado = (_letras_clave(ap1) + (_letras_clave(ap2) if ap2 else "XX") + _letras_clave(_nombre_para_curp(nombres))
                    + yyyy[2:] + mm + dd + entidad + sexo)

    mejores: list[tuple[int, str, bool]] = []
    for token in _candidatos_token(texto, 18, 2, cerca_de="ELECTOR"):
        if RE_CLAVE_ESTRICTA.match(token) and (not esperado or token.startswith(esperado[:6])):
            mejores.append((0, token, False))
            continue
        variantes = [token] if len(token) == 18 else (
            [token[:i] + token[i + 1:] for i in range(len(token))] if len(token) == 19 else []
        )
        for v in variantes:
            base = _mapear_posiciones(v, _PLANTILLA_CLAVE)
            cola = base[15:18]
            if not cola.isdigit():
                continue
            if esperado:
                candidato = esperado + cola
                dist = distancia(canonico(candidato), canonico(token))
                if dist <= 7 and canonico(candidato[:4]) == canonico(v[:4]):
                    mejores.append((dist, candidato, candidato != token))
            elif RE_CLAVE_ESTRICTA.match(base):
                mejores.append((distancia(base, token), base, base != token))
    if not mejores:
        return None, False
    mejores.sort(key=lambda x: x[0])
    return mejores[0][1], mejores[0][2]


# ── Limpieza de líneas de nombre ──────────────────────────────────────────────

_ETIQUETAS_INE = ["FECHA DE NACIMIENTO", "SEXO", "DOMICILIO", "NOMBRE", "CLAVE DE ELECTOR",
                  "AÑO DE REGISTRO", "INSTITUTO NACIONAL ELECTORAL", "CREDENCIAL PARA VOTAR"]


def es_etiqueta_ruidosa(texto: str, etiquetas: Iterable[str] = _ETIQUETAS_INE, umbral: float = 0.55) -> bool:
    return any(similitud_etiqueta(texto, e) >= umbral for e in etiquetas)


def limpiar_linea_nombre(linea: str) -> Optional[str]:
    """
    Quita de una línea de nombre lo que el OCR mezcló de la columna derecha
    (fecha de nacimiento, 'SEXO H', ruido en minúsculas) y valida que quede un
    nombre plausible en mayúsculas.
    """
    s = re.sub(r"\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4}", " ", linea)
    palabras = []
    for p in s.split():
        limpia = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", p)
        if not limpia:
            continue
        if limpia != limpia.upper():   # ruido OCR suele venir en minúsculas ('wo', 'aexo')
            break
        palabras.append(limpia)
    # quitar colas de 1-2 letras que no son partícula válida ('H', 'TO'…)
    while palabras and len(palabras[-1]) <= 2 and palabras[-1] not in {"DE", "LA", "Y", "DEL"}:
        palabras.pop()
    if not palabras:
        return None
    resultado = " ".join(palabras)
    if es_etiqueta_ruidosa(resultado) or len(resultado) < 2:
        return None
    return resultado
