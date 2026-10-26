"""
application/reconcile_acta.py

Reglas ADICIONALES de reconciliacion para el acta de nacimiento (`acta_nacimiento`),
deterministas, stdlib. Se activan con RECONCILE_ACTA=true (default False) y solo dentro de
`reconcile()` (es decir, con POSTPROCESS_RECONCILE=true). Pensadas para el texto POR FILAS
(DOCLING_TEXT_MODE=filas_acta): cabeceras en una linea y valores en la de al lado, en el
mismo orden. Mismos principios que reconcile.py:

  * Anti-alucinacion: todo valor sale del texto crudo (literal, o con una reparacion OCR
    minima: letra/digito confundido, signo suelto, diacritico). Nada se inventa.
  * Ante la duda no se cambia: cada regla exige una estructura inequivoca.
  * Las reglas que construyan un valor NO literal van en REGLAS_DERIVADAS_ACTA (solo con
    RECONCILE_ALLOW_DERIVED).

Reglas:
  acta_fila_registro            oficialia, fecha_registro, libro, numero_acta (por posicion)
  acta_filiacion_filas          nombre_padre / nombre_madre, curp_padre / curp_madre (por fila)
  acta_persona_fila             segundo_apellido de la persona por su fila de nombre
  acta_diacriticos              quita diacriticos de OCR (excepto la eñe) en nombres/lugares
  acta_curp_progenitor_dv       (DERIVADA) CURP de progenitor reparada por estructura (fecha, estado,
                                digito verificador, sexo, letras del nombre) con confusiones OCR
Ver docs_acta.md.
"""
from __future__ import annotations

import itertools
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Optional

from application import reconcile as _rec
from config import settings
from infrastructure.regex.correccion_ocr import curp_valida

logger = logging.getLogger(__name__)

REGLAS_DERIVADAS_ACTA: set[str] = {"acta_curp_progenitor_dv"}
_rec.REGLAS_DERIVADAS.update(REGLAS_DERIVADAS_ACTA)  # Cambio.derivado / anti-alucinacion de pipeline


def _vacio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


# ── Utilidades ────────────────────────────────────────────────────────────────

_RUIDO = "•·.,;:|!'\"()[]{}*_~`´¨"


def quitar_diacriticos(s: str) -> str:
    """Quita acentos/cedillas/dieresis de OCR conservando la eñe (la ñ es parte del dato)."""
    out = []
    for c in s:
        if c in "ñÑ":
            out.append(c)
        else:
            d = unicodedata.normalize("NFD", c)
            out.append("".join(x for x in d if not unicodedata.combining(x)))
    return "".join(out).replace("İ", "I").replace("ı", "i")


def _tok(t: str) -> str:
    return t.strip(_RUIDO + " \t-")


_ETIQ = ("NOMBRE", "NOMBRES", "PRIMER", "SEGUNDO", "APELLIDO", "NACIONALIDAD", "CURP",
         "CERTIFICACION", "FILIACION")


def _es_etiqueta_tok(t: str) -> bool:
    """Etiqueta del formulario (Title case o ruidosa); un nombre propio va TODO en mayusculas."""
    b = re.sub(r"\(S\)$", "", quitar_diacriticos(t), flags=re.I)
    if not b:
        return False
    if b == "CURP":
        return True
    if b.isupper():
        return False
    bu = b.upper()
    if bu in _ETIQ:
        return True
    return len(bu) >= 5 and any(SequenceMatcher(None, bu, e).ratio() >= 0.8 for e in _ETIQ if len(e) >= 5)


# ── Regla 1: fila de registro (oficialia / fecha / libro / numero) ───────────

_DIG = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "l": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7"}
_RE_FECHA = re.compile(r"(?<![\w/])([\dOQDILZSBGT]{1,2})[/.\-]([\dOQDILZSBGT]{1,2})[/.\-]([\dOQDILZSBGT]{4})(?![\w/])")


def _a_digitos(s: str) -> str:
    return "".join(_DIG.get(c, c) for c in s)


def _fecha_ok(dd: str, mm: str, aaaa: str) -> bool:
    try:
        d, m, a = int(dd), int(mm), int(aaaa)
    except ValueError:
        return False
    return 1 <= m <= 12 and 1 <= d <= 31 and 1850 <= a <= 2100


def _anio_nac(d: dict) -> Optional[int]:
    m = re.search(r"(\d{4})\s*$", str(d.get("fecha_nacimiento") or ""))
    return int(m.group(1)) if m else None


def _fila_registro(texto: str, d: dict) -> Optional[dict]:
    """
    Region de la fila de registro: entre la etiqueta 'Municipio de Registro' y la primera
    etiqueta 'Nombre(s)' (que abre los datos de la persona). Los numeros sueltos (<= 6 digitos)
    y la fecha, en orden de aparicion, son: oficialia, fecha, libro, numero_acta.
    """
    m0 = re.search(r"Municipio\s+de\s+Reg", texto, re.I)
    ini = m0.end() if m0 else 0
    m1 = re.search(r"Nombre\s*\(s\)", texto[ini:], re.I)
    fin = ini + m1.start() if m1 else len(texto)
    region = texto[ini:fin]
    fechas = [(m.start(), m.end(), (_a_digitos(m.group(1)), _a_digitos(m.group(2)), _a_digitos(m.group(3))))
              for m in _RE_FECHA.finditer(region)]
    fechas = [f for f in fechas if _fecha_ok(*f[2])]
    an = _anio_nac(d)
    if an is not None:
        fechas = [f for f in fechas if int(f[2][2]) >= an] or fechas
    if len(fechas) != 1:
        return None
    s, e, (dd, mm, aa) = fechas[0]
    antes = [t for t in re.findall(r"(?<![\w/.])\d{1,6}(?![\w/])", region[:s])]
    despues = re.findall(r"(?<![\w/.])\d{1,6}(?![\w/])", region[e:])
    res = {"fecha_registro": f"{int(dd):02d}/{int(mm):02d}/{aa}"}
    if antes and len(antes[-1]) <= 3:
        res["oficialia"] = antes[-1]
    if len(despues) >= 2 and len(despues[0]) <= 3 and 3 <= len(despues[1]) <= 6:
        res["libro"], res["numero_acta"] = despues[0], despues[1]
    elif len(despues) == 1 and len(despues[0]) >= 4:
        res["numero_acta"] = despues[0]  # el libro no salio en el texto: nunca se inventa
    return res


def _regla_fila_registro(ctx) -> None:
    fila = _fila_registro(ctx.texto, ctx.datos)
    if not fila:
        return
    for campo in ("oficialia", "fecha_registro", "libro", "numero_acta"):
        if campo in fila:
            ctx.poner(campo, fila[campo], "acta_fila_registro")


# ── Regla 2: filas de filiacion (padre / madre) ──────────────────────────────

_NACIONALIDADES = {
    "MEXICANA", "MEXICANO", "ESTADOUNIDENSE", "COLOMBIANA", "COLOMBIANO", "ARGENTINA", "ARGENTINO",
    "CUBANA", "CUBANO", "VENEZOLANA", "VENEZOLANO", "GUATEMALTECA", "GUATEMALTECO", "HONDURENA",
    "HONDURENO", "SALVADORENA", "SALVADORENO", "PERUANA", "PERUANO", "CHILENA", "CHILENO",
    "ESPANOLA", "ESPANOL", "CANADIENSE", "BRASILENA", "BRASILENO", "ECUATORIANA", "ECUATORIANO",
    "NICARAGUENSE", "FRANCESA", "FRANCES", "ALEMANA", "ALEMAN", "ITALIANA", "ITALIANO", "CHINA",
    "CHINO", "JAPONESA", "JAPONES", "COSTARRICENSE", "PANAMENA", "PANAMENO", "BOLIVIANA",
    "BOLIVIANO", "URUGUAYA", "URUGUAYO", "PARAGUAYA", "PARAGUAYO", "DOMINICANA", "DOMINICANO",
}
_RE_GENTILICIO = re.compile(r"[A-Z]{4,}(ICANA|ICANO|IANA|IANO|ENSE|ENSE)$")


def _es_nacionalidad(t: str) -> bool:
    b = quitar_diacriticos(t).upper().replace("Ñ", "N")
    return b in _NACIONALIDADES or (len(b) >= 8 and bool(_RE_GENTILICIO.fullmatch(b)))


def _parece_nacionalidad(t: str) -> bool:
    """Nacionalidad con ruido de OCR ('MEXIGANA'): solo se usa para el token pegado a la CURP."""
    b = quitar_diacriticos(t).upper().replace("Ñ", "N")
    return _es_nacionalidad(t) or (len(b) >= 7 and any(
        SequenceMatcher(None, b, n).ratio() >= 0.85 for n in ("MEXICANA", "MEXICANO")))


def _es_curp_tok(t: str) -> bool:
    b = re.sub(r"[^A-Za-z0-9]", "", t)
    return 16 <= len(b) <= 20 and sum(c.isdigit() for c in b) >= 6 and sum(c.isalpha() for c in b) >= 6


def _region_filiacion(texto: str) -> Optional[str]:
    m = re.search(r"Datos\s+de\s+F\w{1,4}aci.n(\s+de\s+la\s+Persona\s+Registrada)?", texto, re.I)
    if not m:
        return None
    fin = re.search(r"Anotaciones|Certificaci.n", texto[m.end():], re.I)
    return texto[m.end(): m.end() + fin.start()] if fin else texto[m.end():]


def parsear_filiacion(region: str) -> list[dict]:
    """
    Personas de la region de filiacion: [{'nombre': str, 'nac': str|None, 'curp_tok': str|None}].
    Se descartan las etiquetas (en cualquier orden/posicion) y el flujo de palabras restante se
    corta en cada CURP (o, sin CURP, en cada nacionalidad reconocida).
    """
    toks: list[str] = []
    for ln in region.splitlines():
        for t in ln.split():
            t = _tok(t) if not _es_curp_tok(t) else re.sub(r"[^A-Za-z0-9]", "", t)
            # un nombre propio va TODO en mayusculas: lo que lleva minusculas es etiqueta/ruido
            if not t or _es_etiqueta_tok(t) or re.search(r"[a-zñáéíóúü]", t):
                continue
            toks.append(t)
    personas, acum = [], []
    for i, t in enumerate(toks):
        if _es_curp_tok(t):
            nac = acum.pop() if acum and _parece_nacionalidad(acum[-1]) else None
            if acum:
                personas.append({"nombre": " ".join(acum), "nac": nac, "curp_tok": t})
            acum = []
        elif _es_nacionalidad(t) and acum and not (i + 1 < len(toks) and _es_curp_tok(toks[i + 1])):
            personas.append({"nombre": " ".join(acum), "nac": t, "curp_tok": None})
            acum = []
        else:
            acum.append(t)
    def limpio(n: str) -> bool:
        ws = n.split()
        return bool(re.fullmatch(r"[A-ZÑ'\- ]+", quitar_diacriticos(n).upper())) and 2 <= len(ws) <= 8 \
            and all(a != b for a, b in zip(ws, ws[1:]))  # una palabra repetida seguida = texto duplicado (ruido)
    if not all(limpio(p["nombre"]) for p in personas):
        return []  # estructura dudosa (fila partida o con palabras de otra): ante la duda no se toca nada
    return personas


def _nombre_limpio(s: str) -> str:
    return quitar_diacriticos(s).upper().replace("ñ", "Ñ")


def _rol_personas(personas: list[dict]) -> dict:
    """{'padre': persona, 'madre': persona}: orden del texto, confirmado por el sexo de la CURP."""
    if len(personas) != 2:
        return {}
    p0, p1 = personas
    c0 = _rec._reparar_curp(p0["curp_tok"]) if p0["curp_tok"] else None
    c1 = _rec._reparar_curp(p1["curp_tok"]) if p1["curp_tok"] else None
    s0, s1 = (c0 or "")[10:11], (c1 or "")[10:11]
    if s0 == "M" and s1 == "H":
        return {"padre": p1, "madre": p0}
    return {"padre": p0, "madre": p1}


def _regla_filiacion_filas(ctx) -> None:
    region = _region_filiacion(ctx.texto)
    if not region:
        return
    roles = _rol_personas(parsear_filiacion(region))
    if not roles:
        return
    d = ctx.datos
    for rol, p in roles.items():
        nom = _nombre_limpio(p["nombre"])
        if len(nom.split()) >= 2:
            ctx.poner(f"nombre_{rol}", nom, "acta_filiacion_filas")
        if p["nac"] and _vacio(d.get(f"nacionalidad_{rol}")):
            ctx.poner(f"nacionalidad_{rol}", _nombre_limpio(p["nac"]), "acta_filiacion_filas")
        if p["curp_tok"]:
            c = _rec._reparar_curp(p["curp_tok"])
            if c and c[10] == ("H" if rol == "padre" else "M") and c != str(d.get("curp") or "").strip().upper():
                ctx.poner(f"curp_{rol}", c, "acta_filiacion_filas")


# ── Regla 3: diacriticos de OCR ──────────────────────────────────────────────

_CAMPOS_DIACRITICOS = ("nombres", "primer_apellido", "segundo_apellido", "nombre_completo", "nombre_padre",
                       "nombre_madre", "primer_apellido_padre", "segundo_apellido_padre",
                       "primer_apellido_madre", "segundo_apellido_madre", "entidad_registro",
                       "municipio_registro", "lugar_nacimiento")


def _regla_diacriticos(ctx) -> None:
    for campo in _CAMPOS_DIACRITICOS:
        v = ctx.datos.get(campo)
        if isinstance(v, str) and v.strip():
            n = quitar_diacriticos(v)
            if n != v:
                ctx.poner(campo, n, "acta_diacriticos")


# ── Regla 4 (DERIVADA): CURP de progenitor por digito verificador + estructura ─

# confusiones digito<->digito tipicas de OCR (ademas de las letra<->digito de reconcile.py)
_CONF_DIG = {"0": "86", "1": "7", "2": "7", "3": "8", "5": "6", "6": "850", "7": "12", "8": "9630", "9": "84"}
_ESTADOS = {"AS", "BC", "BS", "CC", "CL", "CM", "CS", "CH", "DF", "DG", "GT", "GR", "HG", "JC", "MC", "MN",
            "MS", "NT", "NL", "OC", "PL", "QT", "QR", "SP", "SL", "SR", "TC", "TS", "TL", "VZ", "YN", "ZS", "NE"}
_CONF_LET = {"I": "L", "L": "I", "O": "D", "D": "O", "Q": "O", "U": "V", "V": "U", "S": "5", "B": "8", "G": "6"}


def _alts_pos(c: str, i: int) -> list[str]:
    """Caracteres alternativos plausibles (confusion OCR) para la posicion i de la CURP."""
    cl = _rec._clase_pos(i)
    out: list[str] = []
    if cl in ("D", "A"):
        out += [_rec._A_DIGITO[c]] if c in _rec._A_DIGITO else []
        out += list(_CONF_DIG.get(c, ""))
    if cl in ("L", "A"):
        out += [_rec._A_LETRA[c]] if c in _rec._A_LETRA else []
        out += [_CONF_LET[c]] if c in _CONF_LET and _CONF_LET[c].isalpha() else []
        if c == "0":
            out += ["O", "D", "Q"]
    if cl == "S":
        out += ["H"] if c in "NK" else (["M"] if c == "N" else [])
    return [x for x in dict.fromkeys(out) if x != c]


def _fecha_curp_valida(c: str) -> bool:
    try:
        mm, dd = int(c[6:8]), int(c[8:10])
    except ValueError:
        return False
    return 1 <= mm <= 12 and 1 <= dd <= 31


def _estructura_ok(c: str) -> bool:
    """Fecha de nacimiento real, estado valido y digito verificador correcto."""
    return bool(re.fullmatch(r"[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d", c)) and _fecha_curp_valida(c) \
        and c[11:13] in _ESTADOS and curp_valida(c)


def _pistas_nombre(toks: list[str]) -> list[dict]:
    """Posibles particiones (nombres, ap1, ap2) de las palabras del nombre -> pistas de CURP."""
    from infrastructure.regex.correccion_ocr import pistas_curp
    part = {"DE", "DEL", "LA", "LAS", "LOS", "Y", "MC", "MAC", "VON", "VAN", "SAN", "SANTA"}
    toks = [quitar_diacriticos(t).upper() for t in toks]
    res = []
    n = len(toks)
    for l2 in range(1, min(4, n - 1) + 1):
        if toks[n - l2] in part and l2 == 1:
            continue
        for l1 in range(1, min(4, n - l2 - 1) + 1):
            if toks[n - l2 - l1] in part and l1 == 1:
                continue
            nom = " ".join(toks[: n - l2 - l1])
            if nom:
                res.append(pistas_curp(nom, " ".join(toks[n - l2 - l1: n - l2]), " ".join(toks[n - l2:])))
    return res


def reparar_curp_estructura(token: str, nombre: str = "", rol_sexo: str = "", max_cambios: int = 2) -> Optional[str]:
    """
    CURP valida (fecha real, estado valido, digito verificador, sexo del rol) con el MINIMO de
    confusiones OCR sobre el token (letra/digito, digito/digito, I/L...). Con el nombre del
    progenitor se desempata por las letras de la CURP que dependen de el. Solo si es unica.
    """
    base = re.sub(r"[^A-Za-z0-9]", "", token).upper()
    if len(base) != 18:
        return None
    pistas = _pistas_nombre(nombre.split()) if nombre else []

    def score(c: str) -> int:
        return max((sum(c[i] == ch for i, ch in p.items()) for p in pistas), default=0)

    def ok(c: str) -> bool:
        return _estructura_ok(c) and (not rol_sexo or c[10] == rol_sexo)

    if ok(base):
        return base
    alts = [_alts_pos(c, i) for i, c in enumerate(base)]
    var = [i for i, a in enumerate(alts) if a]
    for k in range(1, max_cambios + 1):
        validas = set()
        for combo in itertools.combinations(var, k):
            for sel in itertools.product(*[alts[i] for i in combo]):
                c = list(base)
                for i, ch in zip(combo, sel):
                    c[i] = ch
                cand = "".join(c)
                if ok(cand):
                    validas.add(cand)
        if validas:
            if pistas and len(validas) > 1:
                top = max(score(c) for c in validas)
                validas = {c for c in validas if score(c) == top}
            return validas.pop() if len(validas) == 1 else None
    return None


def _regla_curp_progenitor_dv(ctx) -> None:
    if not ctx.derivados:
        return
    region = _region_filiacion(ctx.texto)
    if not region:
        return
    roles = _rol_personas(parsear_filiacion(region))
    for rol, p in roles.items():
        tok = p["curp_tok"]
        if not tok:
            continue
        sexo = "H" if rol == "padre" else "M"
        base = re.sub(r"[^A-Za-z0-9]", "", tok).upper()
        if len(base) == 18 and _estructura_ok(base) and base[10] == sexo:
            continue  # la CURP del texto ya es estructuralmente valida: la hace la regla 2
        c = reparar_curp_estructura(tok, p["nombre"], sexo)
        if c:
            ctx.poner(f"curp_{rol}", c, "acta_curp_progenitor_dv")


# ── Regla 5: segundo apellido de la persona por la fila de nombre ────────────

def _regla_persona_fila(ctx) -> None:
    """
    La fila de la persona ('NOMBRES AP1 AP2', justo antes de la etiqueta 'Nombre(s) Primer Apellido')
    corrige el segundo apellido cuando nombres+primer_apellido (ya extraidos) la encabezan y lo que
    sobra es el segundo apellido (el LLM lo tomo de un progenitor).
    """
    d = ctx.datos
    m = re.search(r"Datos\s+de\s+la\s+Persona\s+Registrada", ctx.texto, re.I)
    if not m or _vacio(d.get("nombres")) or _vacio(d.get("primer_apellido")):
        return
    fila: list[str] = []
    for t in ctx.texto[m.end():].split():
        t = _tok(t)
        if not t or _es_etiqueta_tok(t):
            continue
        if re.fullmatch(r"HOMBRE|MUJER|SEXO|DATOS|[\d/]+", quitar_diacriticos(t), re.I):
            break
        fila.append(quitar_diacriticos(t).upper())
    pre = [quitar_diacriticos(t).upper() for t in str(d["nombres"]).split() + str(d["primer_apellido"]).split()]
    resto = fila[len(pre):]
    if len(fila) <= 8 and fila[:len(pre)] == pre and 1 <= len(resto) <= 3 and all(re.fullmatch(r"[A-ZÑ'\-]+", t) for t in resto):
        ctx.poner("segundo_apellido", " ".join(resto), "acta_persona_fila")


# ── Punto de entrada ─────────────────────────────────────────────────────────

def aplicar_acta(ctx) -> None:
    """Llamada desde reconcile() (flag RECONCILE_ACTA). Cada regla es independiente y segura."""
    for regla in (_regla_fila_registro, _regla_filiacion_filas, _regla_curp_progenitor_dv,
                  _regla_persona_fila, _regla_diacriticos):
        try:
            regla(ctx)
        except Exception as e:  # una regla defectuosa nunca debe romper el pipeline
            logger.warning("reconcile_acta_regla_fallo", extra={"regla": regla.__name__, "error": type(e).__name__})
