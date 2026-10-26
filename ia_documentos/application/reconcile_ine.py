"""
application/reconcile_ine.py

Reglas ADICIONALES de reconciliacion para la INE (tipo `ine`), deterministas, stdlib.
Se activan con RECONCILE_INE=true (default False) y solo dentro de `reconcile()`, es decir,
con POSTPROCESS_RECONCILE=true. Mismos principios que reconcile.py:

  * Anti-alucinacion: todo valor sale del texto crudo (literal) o es la concatenacion
    de palabras literales del texto (nombres, domicilio). Nada se inventa.
  * Ante la duda no se cambia: cada regla exige solucion UNICA.
  * Las reglas que construyan un valor NO literal deben ir en REGLAS_DERIVADAS_INE
    (solo con RECONCILE_ALLOW_DERIVED). Hoy ninguna lo es.

Reglas:
  ine_nombre_por_ventana_curp   (nombres, primer/segundo apellido, nombre_completo)
  ine_domicilio_por_bloque      (domicilio)
Ver docs_ine.md.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Optional

from application import reconcile as _rec
from config import settings
from infrastructure.regex.correccion_ocr import curp_valida, pistas_curp

REGLAS_DERIVADAS_INE: set[str] = {"ine_enie_por_curp"}


_rec.REGLAS_DERIVADAS.update(REGLAS_DERIVADAS_INE)  # Cambio.derivado / anti-alucinacion de pipeline


def _activo() -> bool:
    return bool(getattr(settings, "RECONCILE_INE", False))


# ── Utilidades ────────────────────────────────────────────────────────────────

_PARTICULAS = {"DE", "DEL", "LA", "LAS", "LOS", "Y", "MC", "MAC", "VON", "VAN", "SAN", "SANTA"}
_RE_PALABRA = re.compile(r"[A-ZÁÉÍÓÚÜÑ]{2,}")
_EXTRA_ETIQUETA = {"MEXICO", "PARA", "VOTAR", "SEXO", "HOMBRE", "MUJER", "DOMICILIO"}


def _sin_acentos(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def _vacio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _tokens_nombre(texto: str, etiquetas: set[str]) -> list[str]:
    """Palabras en mayusculas del texto (en orden), sin etiquetas, fechas ni numeros."""
    toks: list[str] = []
    for ln in texto.splitlines():
        for t in ln.split():
            t = t.strip(".,:;()\"'")
            if _RE_PALABRA.fullmatch(t) and _sin_acentos(t) not in etiquetas:
                toks.append(t)
    return toks


def _coincidencias(curp: str, ap1: str, ap2: str, nom: str) -> int:
    """Cuantas de las 7 letras de la CURP que dependen del nombre coinciden (7 = consistente)."""
    p = pistas_curp(_sin_acentos(nom), _sin_acentos(ap1), _sin_acentos(ap2))
    return sum(curp[i] == c for i, c in p.items()) if len(p) == 7 else 0


# La CURP de algunos documentos tiene alguna letra interna que no sigue la regla (homoclave
# de generador/OCR): se toleran hasta 2 de las 7 letras, pero la solucion debe ser UNICA.
_MIN_COINCIDENCIAS = 5


def _nombres_ancla(lineas: list[str], etiquetas: set[str]) -> Optional[str]:
    """Linea de solo mayusculas inmediatamente anterior a una linea 'DOMICILIO' sola."""
    for i, ln in enumerate(lineas):
        if i and re.fullmatch(r"DOMICILIO\s*:?", ln.strip(), re.I):
            prev = lineas[i - 1].split()
            if prev and all(_RE_PALABRA.fullmatch(t) and _sin_acentos(t) not in etiquetas for t in prev):
                return " ".join(prev)
    return None


# ── Regla: nombre y apellidos por ventana consecutiva consistente con la CURP ─
#
# Las letras 0,1,13 (ap1), 2,14 (ap2) y 3,15 (nombre) de la CURP validan la particion de las
# palabras en (ap1, ap2, nombres). Se buscan en el flujo de palabras del texto ventanas
# CONSECUTIVAS (el texto trae ap1 ap2 nombres en ese orden, con etiquetas/fechas de por medio
# que se descartan) y se acepta solo una solucion unica. Si el texto trae una linea limpia de
# nombres justo antes de la etiqueta DOMICILIO (segunda zona del OCR), ese es el ancla.

_MAX_TOK = 300


def _ventanas(toks: list[str], planas: list[str], cur: str, ancla: Optional[str]) -> dict:
    sols: dict[tuple[str, str, str], int] = {}
    n = len(toks)
    for i in range(n):
        if planas[i][0] != cur[0] or planas[i] in _PARTICULAS:
            continue
        for l1 in range(1, 4):
            if i + l1 > n or planas[i + l1 - 1] in _PARTICULAS:
                continue
            j = i + l1
            for l2 in range(1, 4):
                if j + l2 > n or planas[j + l2 - 1] in _PARTICULAS:
                    continue
                if all(p in _PARTICULAS for p in planas[j:j + l2]):
                    continue
                k = j + l2
                ap1, ap2 = " ".join(toks[i:j]), " ".join(toks[j:k])
                if ancla is not None:  # nombres = la linea limpia (no tiene que ser contigua)
                    cands = [ancla]
                else:
                    cands = [" ".join(toks[k:k + l3]) for l3 in range(1, 4) if k + l3 <= n]
                for nom in cands:
                    c = _coincidencias(cur, ap1, ap2, nom)
                    if c >= _MIN_COINCIDENCIAS:
                        sols[(ap1, ap2, nom)] = max(c, sols.get((ap1, ap2, nom), 0))
    return sols


def _regla_nombre(ctx) -> None:
    from application.reconcile import _PALABRAS_ETIQUETA
    d = ctx.datos
    cur = str(d.get("curp") or "").strip().upper()
    if not curp_valida(cur):
        return
    etiquetas = {_sin_acentos(e) for e in _PALABRAS_ETIQUETA} | _EXTRA_ETIQUETA
    toks = _tokens_nombre(ctx.texto, etiquetas)[:_MAX_TOK]
    ancla = _nombres_ancla(ctx.texto.splitlines(), etiquetas)
    if ancla is not None:  # el ancla debe ser un 'nombres' creible: <=3 palabras y letras 3 y 15 de la CURP
        pa = pistas_curp(_sin_acentos(ancla), "X", "X")
        if len(ancla.split()) > 3 or pa.get(3) != cur[3] or pa.get(15) != cur[15]:
            ancla = None
    planas = [_sin_acentos(t) for t in toks]
    sols = _ventanas(toks, planas, cur, None)
    if ancla is not None:
        con_ancla = {k: v for k, v in sols.items() if _sin_acentos(k[2]) == _sin_acentos(ancla)}
        sols = con_ancla or _ventanas(toks, planas, cur, ancla)
    if not sols:
        return
    mejor = max(sols.values())
    fin = [k for k, v in sols.items() if v == mejor]
    if len(fin) > 1:  # empate (p. ej. 'nombres' que se prolonga sobre palabras sueltas): el valor actual
        actual = [_sin_acentos(str(d.get(c) or "")).upper() for c in ("primer_apellido", "segundo_apellido", "nombres")]
        punt = {k: (sum(_sin_acentos(a) == b for a, b in zip(k, actual)), -sum(len(a.split()) for a in k)) for k in fin}
        top = max(punt.values())
        fin = [k for k in fin if punt[k] == top]
    if len(fin) != 1:
        return
    ap1, ap2, nom = fin[0]
    a1, a2, nm = (str(d.get(c) or "").strip() for c in ("primer_apellido", "segundo_apellido", "nombres"))
    con_ancla = ancla is not None and _sin_acentos(nom) == _sin_acentos(ancla)
    if not con_ancla and a1 and a2 and nm and _coincidencias(cur, a1, a2, nm) >= mejor:
        return  # el valor actual ya es tan consistente con la CURP como la solucion: no se toca
    ctx.poner("primer_apellido", ap1, "ine_nombre_por_ventana_curp")
    ctx.poner("segundo_apellido", ap2, "ine_nombre_por_ventana_curp")
    ctx.poner("nombres", nom, "ine_nombre_por_ventana_curp")
    ctx.poner("nombre_completo", f"{nom} {ap1} {ap2}", "ine_nombre_por_ventana_curp")


# ── Regla (derivada): N -> Ñ cuando la CURP delata la Ñ ───────────────────────
# La CURP codifica la Ñ interna como 'X'. Si la primera consonante interna del primer apellido
# (pos. 13), segundo (14) o nombre (15) es 'N' y la CURP trae 'X', la Ñ se perdio en el OCR.
# El valor resultante NO es literal del texto (un caracter cambiado) -> solo con derivados.

_VOCALES = set("AEIOU")


def _enie(componente: Any, letra_curp: str) -> Optional[str]:
    if letra_curp != "X" or _vacio(componente):
        return None
    palabras = str(componente).split(" ")
    for idx, w in enumerate(palabras):
        if _sin_acentos(w).upper() in _PARTICULAS:
            continue
        pl = _sin_acentos(w).upper()
        for k in range(1, len(pl)):
            if pl[k] in _VOCALES or not pl[k].isalpha():
                continue
            if pl[k] == "N" and w[k].upper() == "N":
                palabras[idx] = w[:k] + "Ñ" + w[k + 1:]
                return " ".join(palabras)
            return None
        return None
    return None


def _regla_enie(ctx) -> None:
    if not ctx.derivados:
        return
    d = ctx.datos
    cur = str(d.get("curp") or "").strip().upper()
    if not curp_valida(cur):
        return
    for campo, pos in (("primer_apellido", 13), ("segundo_apellido", 14), ("nombres", 15)):
        nuevo = _enie(d.get(campo), cur[pos])
        if nuevo:
            ctx.poner(campo, nuevo, "ine_enie_por_curp")
    nom, a1, a2 = d.get("nombres"), d.get("primer_apellido"), d.get("segundo_apellido")
    if not (_vacio(nom) or _vacio(a1) or _vacio(a2)):
        completo = f"{nom} {a1} {a2}"
        if _sin_acentos(str(d.get("nombre_completo") or "")).upper() == _sin_acentos(completo).upper() \
                and d.get("nombre_completo") != completo:
            ctx.poner("nombre_completo", completo, "ine_enie_por_curp")


# ── Regla: domicilio por roles (calle / colonia / municipio) ──────────────────
#
# El texto trae el domicilio en 3 lineas limpias (segunda zona del OCR) o revuelto en la
# zona ruidosa (una sola linea, o lineas separadas por otras etiquetas, o con la colonia
# antes que la calle). La colonia ("COL. xxx 12345") es un ancla fiable: termina en un CP
# de 5 digitos. calle = linea con numero sin etiqueta; municipio = linea que acaba en ", ABREV.".

_RE_COL = re.compile(r"\bCOL[.:]?\s+(?P<col>.*?\b\d{5}\b)", re.I)
_RE_MUN = re.compile(r"^(?P<m>[^,]*[A-Za-zÁÉÍÓÚÑ][^,]*,\s*[A-Z]{2,5}[.:]?)$")
_RE_JUNK = re.compile(
    r"FECHA|NACIM|SEXO|CURP|CLAVE|ELECTOR|ESTADO|MUNICIP|SECC|LOCALID|EMISI|VIGENCIA|REGISTRO|"
    r"\d{1,2}/\d{1,2}/\d{4}|DOMICILIO|NOMBRE", re.I)


def _mezcla_alfanum(ln: str) -> bool:
    """Algun token con >=3 letras y >=3 digitos (clave de elector, CURP...): no es una calle."""
    return any(len(re.findall(r"[A-Z]", t)) >= 3 and len(re.findall(r"\d", t)) >= 3 for t in ln.split())


def _linea_calle(ln: str) -> bool:
    ln = ln.strip()
    return bool(re.fullmatch(r"[A-ZÁÉÍÓÚÜÑ0-9][A-ZÁÉÍÓÚÜÑ0-9 .\-/#°]*", ln)) and not _mezcla_alfanum(ln) \
        and bool(re.search(r"\d", ln)) and not _RE_JUNK.search(ln) and not re.search(r"\bCOL[.:]?\s", ln, re.I) \
        and not _RE_MUN.match(ln.strip())


def _candidatos_domicilio(texto: str) -> list[tuple[int, str]]:
    """[(rango, domicilio)]: rango 0 = tres lineas limpias; 1 = reconstruido de lineas revueltas."""
    lineas = texto.splitlines()
    out: list[tuple[int, str]] = []
    for i, ln in enumerate(lineas):
        m = _RE_COL.search(ln)
        if not m:
            continue
        col = "COL. " + m.group("col").strip()
        antes = ln[:m.start()].strip()
        despues = ln[m.end():].strip(" ")
        calle = antes if antes and _linea_calle(antes) else None
        mun = despues if despues and _RE_MUN.match(despues) else None
        if calle is None and despues and mun is None and _linea_calle(despues):
            calle, despues = despues, ""  # variante: "COL. xxx 12345 CALLE 12" y el municipio en otra linea
        limpio = calle is None and mun is None
        if calle is None:
            for k in (i - 1, i + 1, i - 2, i + 2, i - 3, i + 3):
                if 0 <= k < len(lineas) and _linea_calle(lineas[k]):
                    calle = lineas[k].strip()
                    break
        if mun is None:
            for k in (i + 1, i + 2, i + 3, i - 1, i - 2):
                if 0 <= k < len(lineas) and _RE_MUN.match(lineas[k].strip()) and not _RE_JUNK.search(lineas[k]):
                    mun = lineas[k].strip()
                    break
        if calle and mun:
            rango = 0 if (limpio and abs(lineas.index(calle) - i) == 1 if calle in lineas else False) else 1
            out.append((rango, f"{calle} {col} {mun}"))
    return out


def _restaurar_enes(valor: str, texto: str) -> str:
    """Una zona del OCR pierde la tilde de la N: se recupera de la misma palabra con N en otra zona."""
    con_enie = {}
    for t in re.findall(r"[A-ZÁÉÍÓÚÜÑ]*Ñ[A-ZÁÉÍÓÚÜÑ]*", texto.upper()):
        con_enie[t.replace("Ñ", "N")] = t
    salida = []
    for w in valor.split(" "):
        nucleo = w.rstrip(".,;:")
        cola = w[len(nucleo):]
        salida.append(con_enie.get(nucleo.upper(), nucleo) + cola if "Ñ" not in nucleo.upper() else w)
    return " ".join(salida)


def _norm_dom(s: Any) -> str:
    """Normalizacion del evaluador para domicilios: sin , . ; y con espacios colapsados."""
    return " ".join(re.sub(r"[,.;]", " ", str(s or "")).casefold().split())


def _ruido_de_lectura(a: str, b: str) -> bool:
    """a y b difieren como mucho en 2 caracteres alfanumericos (confusion OCR, no de asignacion)."""
    ops = [(a[i1:i2], b[j1:j2]) for t, i1, i2, j1, j2 in SequenceMatcher(None, a, b).get_opcodes() if t != "equal"]
    if any(not (x + y).replace(" ", "").isalnum() for x, y in ops):
        return False  # puntuacion distinta (':' por '.'), espacios: estructura
    if any(set(x + y) & {"ñ"} for x, y in ops):
        return False
    if _sin_acentos(a) == _sin_acentos(b):
        return False  # solo acentos: la zona limpia manda
    return sum(max(len(x), len(y)) for x, y in ops) <= 2


def _regla_domicilio(ctx) -> None:
    cands = _candidatos_domicilio(ctx.texto)
    if not cands:
        return
    mejor = min(r for r, _ in cands)
    vals = {v for r, v in cands if r == mejor}
    if len(vals) != 1:
        return
    nuevo = _restaurar_enes(vals.pop(), ctx.texto).replace(":", ".")
    actual = ctx.datos.get("domicilio")
    a, b = _norm_dom(actual), _norm_dom(nuevo)
    if a == b:
        return
    if not _vacio(actual):
        # diferencias minimas de letras/digitos = ruido de lectura, no de asignacion:
        # el valor actual se respeta (el candidato no es mas fiable que el LLM en eso).
        if _ruido_de_lectura(a, b):
            return
    ctx.poner("domicilio", nuevo, "ine_domicilio_por_bloque")


# ── Regla: clave de elector anclada a su etiqueta ─────────────────────────────
# El OCR repite la clave en dos zonas con lecturas distintas; la regla existente se abstiene
# si hay dos candidatos validos. El token pegado a la etiqueta "CLAVE DE ELECTOR" (segunda zona,
# la limpia) desempata. Estructura: 6 letras + AAMMDD (= CURP[4:10]) + entidad (2) + sexo (= CURP[10]) + 3 digitos.

_RE_CLAVE_ETQ = re.compile(r"CLAVE\s*(?:DE|BE|De)?\s*ELECTOR[,:;]?\s*([A-Za-z0-9]{18})(?![A-Za-z0-9])", re.I)


def _regla_clave_etiqueta(ctx) -> None:
    d = ctx.datos
    cur = str(d.get("curp") or "").strip().upper()
    if not curp_valida(cur):
        return
    ymd, sx = cur[4:10], cur[10]

    def ok(c: str) -> bool:
        return bool(re.fullmatch(r"[A-Z]{6}\d{6}\d{2}[HM]\d{3}", c)) and c[6:12] == ymd and c[14] == sx

    actual = str(d.get("clave_elector") or "").strip().upper()
    if ok(actual):
        return
    cands = set()
    for m in _RE_CLAVE_ETQ.finditer(ctx.plano):
        t = m.group(1).upper()
        c = "".join((_rec._A_LETRA.get(ch, ch) if i < 6 else _rec._A_DIGITO.get(ch, ch) if i != 14 else ch)
                    for i, ch in enumerate(t))
        if ok(c):
            cands.add(c)
    if len(cands) == 1:
        ctx.poner("clave_elector", cands.pop(), "ine_clave_por_etiqueta")


# ── Punto de entrada (llamado desde reconcile.reconcile) ──────────────────────

def aplicar_reglas_ine(ctx, seguro) -> None:
    """`seguro(fn, *args)` ejecuta cada regla aislada (una falla nunca rompe el pipeline)."""
    if not _activo():
        return
    seguro(_regla_nombre, ctx)
    seguro(_regla_enie, ctx)
    seguro(_regla_clave_etiqueta, ctx)
    seguro(_regla_domicilio, ctx)
