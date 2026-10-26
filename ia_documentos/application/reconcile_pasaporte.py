"""
application/reconcile_pasaporte.py

Reglas propias de reconciliacion del PASAPORTE (flag RECONCILE_PASAPORTE, apagado por
defecto; enganche de pocas lineas en application/reconcile.py). Solo stdlib.

Dos niveles, estrictamente separados:

  LITERAL  (RECONCILE_PASAPORTE)
      El valor escrito esta en el texto crudo, tal cual o con una reparacion OCR minima
      (quitar espacios, normalizar glifos '<', y como mucho UNA edicion de un caracter
      validada por los 5 digitos de control de la MRZ).
        sexo_por_etiqueta            M/F de la fila "Sexo / Sex / Sexe" (o MRZ valida)
        nombres_sin_observaciones    quita "NINGUNA" que Docling funde en la fila de nombres
        apellidos_separados          primer_apellido con los dos apellidos -> separa
        mrz_linea2_literal           linea 2 exacta o con una edicion validada por DV
        mrz_linea1_literal           linea 1 de 44 caracteres exacta (tras glifos/espacios)
        curp_con_espacios            CURP partida por espacios, validada por su DV

  DERIVADO (ademas exige RECONCILE_ALLOW_DERIVED): el valor NO es literal del texto.
        mrz_linea2_reparada_multi    reparacion con varios cambios validada por DV
        mrz_linea2_desde_campos      linea 2 reconstruida de los campos (con DV calculados)
        mrz_linea1_desde_campos      linea 1 normalizada/reconstruida desde nombres y apellidos

Anti-alucinacion: nunca se escribe un valor sin evidencia en el texto del propio
documento; ante empate o duda no se cambia.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Optional

import application.reconcile as R
from application.reconcile import (
    _apellido_basura,
    _dv_mrz,
    _mrz2_valida,
    _mrz_norm,
    _tokens,
    _vacio,
)
from infrastructure.regex.correccion_ocr import curp_valida

logger = logging.getLogger(__name__)

# Reglas que construyen valores no literales (se registran como derivadas)
REGLAS_DERIVADAS_PASAPORTE = {
    "mrz_linea2_reparada_multi", "mrz_linea2_desde_campos", "mrz_linea1_desde_campos",
}
R.REGLAS_DERIVADAS.update(REGLAS_DERIVADAS_PASAPORTE)

_ALF_MRZ = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"


# ── Utilidades ───────────────────────────────────────────────────────────────

def _sin_acentos(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c))


def _mayus(s: Any) -> str:
    return _sin_acentos(str(s or "")).upper()


def _lineas(ctx) -> list[str]:
    return [ln for ln in ctx.texto.splitlines() if ln.strip()]


def _compacta(s: str) -> str:
    """Normaliza glifos '<' y quita todo espacio."""
    return re.sub(r"\s+", "", _mrz_norm(s))


# ── Candidatos de MRZ en el texto ────────────────────────────────────────────

_RE_INI_L2 = re.compile(r"[A-Z0-9]{9}[0-9]MEX[0-9]{6}[0-9][MF<][0-9]{6}[0-9]")
_RE_INI_L2_LAX = re.compile(r"[A-Z0-9]{9}[0-9OQDIL]M[E5]X[0-9OQDIL]{6}")


def _ventanas(ctx, patron: re.Pattern) -> list[str]:
    """Ventanas de 44 caracteres (sin espacios) desde cada coincidencia de `patron`.
    Docling y el OCR alternativo repiten la MRZ: se deduplica."""
    out: list[str] = []
    for ln in _lineas(ctx):
        s = _compacta(ln)
        for m in patron.finditer(s):
            w = s[m.start():m.start() + 44]
            if w not in out:
                out.append(w)
    return out


def _una_edicion(w: str) -> list[str]:
    """Candidatos validos (5 DV) con UNA edicion (sustitucion, borrado o insercion) sobre w."""
    res: set[str] = set()
    n = len(w)
    if n == 44:
        for i in range(44):
            for c in _ALF_MRZ:
                if c != w[i]:
                    res.add(w[:i] + c + w[i + 1:])
    elif n == 45:
        for i in range(45):
            res.add(w[:i] + w[i + 1:])
    elif n == 43:
        for i in range(44):
            for c in _ALF_MRZ:
                res.add(w[:i] + c + w[i:])
    return [c for c in res if _mrz2_valida(c)]


def _plausible(i: int, c: str) -> bool:
    """Caracter esperable en la posicion i de la linea 2 del pasaporte mexicano."""
    if i in (9, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24, 25, 26, 27):
        return c.isdigit()
    if 28 <= i <= 41:
        return c == "<"            # el campo personal va vacio
    if i in (10, 11, 12):
        return c.isalpha()
    if i == 20:
        return c in "MF<"
    return True


def _elegir(base: str, validas: set) -> Optional[str]:
    """Unica candidata valida; si hay varias, la unica cuya edicion arregla un caracter
    implausible por uno plausible (el DV mod 10 deja muchas ediciones equivalentes)."""
    if len(validas) == 1:
        return next(iter(validas))
    if len(base) != 44:
        return None
    explicadas = set()
    for c in validas:
        dif = [i for i in range(44) if c[i] != base[i]]
        if len(dif) == 1 and _plausible(dif[0], c[dif[0]]) and not _plausible(dif[0], base[dif[0]]):
            explicadas.add(c)
    return next(iter(explicadas)) if len(explicadas) == 1 else None


def _l2_literal(ctx) -> Optional[str]:
    """Linea 2 del texto: exacta o con una edicion validada por los 5 DV."""
    ventanas = _ventanas(ctx, _RE_INI_L2)
    for w in ventanas:
        if _mrz2_valida(w):
            return w
    unicas: list[str] = []
    for w in ventanas:
        c = _elegir(w, set(_una_edicion(w)))
        if c and c not in unicas:
            unicas.append(c)
    return unicas[0] if len(unicas) == 1 else None


def _l2_multi(ctx) -> Optional[str]:
    """Reparacion con varios cambios (DERIVADO): el campo personal (pos. 28-41) es '<' en el
    pasaporte mexicano; confusiones tipicas en digitos; se acepta solo si pasan los 5 DV."""
    for w in _ventanas(ctx, _RE_INI_L2) + _ventanas(ctx, _RE_INI_L2_LAX):
        if len(w) < 30:
            continue
        pre = list(w.ljust(44, "<")[:44])
        for i in range(28, 42):
            pre[i] = "<"
        for i in (9, 19, 27, *range(13, 19), *range(21, 27)):
            if not pre[i].isdigit():
                pre[i] = R._A_DIGITO.get(pre[i], pre[i])
        for i, ch in ((10, "M"), (11, "E"), (12, "X")):
            pre[i] = ch
        for dv in (pre[42], "0", "<"):
            pre[42] = dv
            for fin in (pre[43], *"0123456789"):
                pre[43] = fin
                cand = "".join(pre)
                if _mrz2_valida(cand):
                    return cand
    return None


def _yymmdd(fecha: Any) -> Optional[str]:
    m = re.fullmatch(r"\s*(\d{1,2})\D+(\d{1,2})\D+(\d{4})\s*", str(fecha or ""))
    if not m:
        return None
    return f"{m.group(3)[2:]}{int(m.group(2)):02d}{int(m.group(1)):02d}"


def _l2_desde_campos(ctx, referencia: list[str]) -> Optional[str]:
    """Linea 2 construida de los campos visuales (DERIVADO), con digitos de control calculados.
    Exige que una ventana parcial del propio texto concuerde (guarda anti-invencion)."""
    d = ctx.datos
    num = re.sub(r"[^A-Z0-9]", "", _mayus(d.get("numero_pasaporte")))
    nac, cad = _yymmdd(d.get("fecha_nacimiento")), _yymmdd(d.get("fecha_caducidad"))
    sexo = _mayus(d.get("sexo")).strip()
    if len(num) != 9 or not nac or not cad or sexo not in ("M", "F"):
        return None
    cuerpo = (num + _dv_mrz(num) + "MEX" + nac + _dv_mrz(nac) + sexo + cad + _dv_mrz(cad)
              + "<" * 14 + _dv_mrz("<" * 14))
    cand = cuerpo + _dv_mrz(cuerpo[0:10] + cuerpo[13:20] + cuerpo[21:43])
    if not _mrz2_valida(cand):
        return None
    mejor = max((sum(1 for a, b in zip(w[:28], cand[:28]) if a == b) for w in referencia), default=0)
    return cand if mejor >= 14 else None


# ── Linea 1 ───────────────────────────────────────────────────────────────────

def _nombres_mrz(d: dict) -> Optional[str]:
    """'APELLIDOS<<NOMBRES' desde los campos visuales (sin acentos, N por enie)."""
    ap1, ap2, nom = d.get("primer_apellido"), d.get("segundo_apellido"), d.get("nombres")
    if _vacio(ap1) or _vacio(nom) or _apellido_basura(ap1):
        return None
    sur = "<".join(_tokens(ap1) + _tokens(ap2))
    cuerpo = _sin_acentos((sur + "<<" + "<".join(_tokens(nom))).upper())
    return cuerpo if re.fullmatch(r"[A-Z<]+", cuerpo) else None


_RE_INI_L1 = re.compile(r"P[^A-Z0-9]?MEX")


def _ventanas_l1(ctx) -> list[str]:
    out: list[str] = []
    for ln in _lineas(ctx):
        s = _compacta(ln)
        m = _RE_INI_L1.search(s)
        if m and m.start() <= 6 and s[m.start():] not in out:
            out.append(s[m.start():])
    return out


def _l1_valida(w: str, esperado: Optional[str]) -> bool:
    return (len(w) == 44 and re.fullmatch(r"P<MEX[A-Z<]{39}", w) is not None
            and (esperado is None or w[5:].startswith(esperado[:39].rstrip("<"))))


def _l1_literal(ctx) -> Optional[str]:
    esperado = _nombres_mrz(ctx.datos)
    return next((w for w in _ventanas_l1(ctx) if _l1_valida(w, esperado)), None)


def _l1_derivada(ctx) -> Optional[str]:
    """Linea 1 normalizada desde nombres visuales (DERIVADO). Exige una linea 1 ruidosa en el
    texto cuyo inicio concuerda con los nombres (guarda anti-invencion)."""
    esperado = _nombres_mrz(ctx.datos)
    if not esperado:
        return None
    cand = ("P<MEX" + esperado).ljust(44, "<")[:44]
    for w in _ventanas_l1(ctx):
        w2 = re.sub(r"[^A-Z0-9<]", "<", w)
        n = min(len(w2), 5 + len(esperado))
        if n >= 12 and R._distancia(w2[:n], cand[:n]) <= max(2, n // 8):
            return cand
    return None


# ── Reglas ────────────────────────────────────────────────────────────────────

def _valor_bajo_etiqueta(ctx, patron: str) -> Optional[str]:
    """Primera linea tras la primera fila cuya forma plana encaja con `patron`."""
    ls = _lineas(ctx)
    for i, ln in enumerate(ls[:-1]):
        if re.search(patron, _mayus(ln)):
            return ls[i + 1].strip()
    return None


def _regla_sexo(ctx) -> None:
    d = ctx.datos
    votos: list[str] = []
    fila = _valor_bajo_etiqueta(ctx, r"\bSEXO\b")
    if fila:
        m = re.match(r"([MF])\b", _mayus(fila))
        if m:
            votos.append(m.group(1))
    l2 = _l2_literal(ctx)
    if l2 and l2[20] in "MF":
        votos.append(l2[20])
    if not votos:
        return  # solo con la CURP seria derivado: lo cubre la regla existente
    curp = _mayus(d.get("curp")).strip()
    s_curp = ("M" if curp[10] == "H" else "F") if curp_valida(curp) else None
    elegido = votos[0] if len(set(votos)) == 1 else (s_curp if s_curp in votos else None)
    if elegido and _mayus(d.get("sexo")).strip() != elegido:
        ctx.poner("sexo", elegido, "sexo_por_etiqueta")


def _regla_nombres(ctx) -> None:
    d = ctx.datos
    nom = d.get("nombres")
    if _vacio(nom) or len(str(nom).split()) < 2:
        return
    toks = str(nom).split()
    # la fila de etiquetas junta "Nombres" y "Observaciones": Docling funde ambos valores
    junta = any(re.search(r"NOMBRES|GIVEN", _mayus(ln)) and re.search(r"OBSERV|REMARKS", _mayus(ln))
                for ln in _lineas(ctx))
    if not (junta and _mayus(toks[-1]) in ("NINGUNA", "NINGUNO", "N/A")):
        return
    ctx.poner("nombres", " ".join(toks[:-1]), "nombres_sin_observaciones")
    nc = d.get("nombre_completo")
    if not _vacio(nc):
        partes = str(nc).split()
        if toks[-1] in partes:
            i = len(partes) - 1 - partes[::-1].index(toks[-1])
            ctx.poner("nombre_completo", " ".join(partes[:i] + partes[i + 1:]), "nombres_sin_observaciones")


def _regla_apellidos(ctx) -> None:
    d = ctx.datos
    ap1 = d.get("primer_apellido")
    toks = str(ap1).split() if not _vacio(ap1) else []
    if len(toks) != 2 or not _vacio(d.get("segundo_apellido")):
        return
    fila = _valor_bajo_etiqueta(ctx, r"APELLIDOS|SURNAME")
    if fila and _mayus(fila).split() == _mayus(ap1).split():
        ctx.poner("primer_apellido", toks[0], "apellidos_separados")
        ctx.poner("segundo_apellido", toks[1], "apellidos_separados")


def _regla_mrz2(ctx) -> None:
    d = ctx.datos
    actual = d.get("mrz_linea2")
    act_n = re.sub(r"\s+", "", _mrz_norm(actual)) if not _vacio(actual) else ""
    if act_n and _mrz2_valida(act_n):
        if act_n != actual:
            ctx.poner("mrz_linea2", act_n, "mrz_linea2_literal")
        return
    lit = _l2_literal(ctx)
    if lit:
        ctx.poner("mrz_linea2", lit, "mrz_linea2_literal")
        return
    if not ctx.derivados:
        return
    rep = _l2_multi(ctx)
    if rep:
        ctx.poner("mrz_linea2", rep, "mrz_linea2_reparada_multi")
        return
    desde = _l2_desde_campos(ctx, _ventanas(ctx, _RE_INI_L2) + _ventanas(ctx, _RE_INI_L2_LAX))
    if desde:
        ctx.poner("mrz_linea2", desde, "mrz_linea2_desde_campos")


def _regla_mrz1(ctx) -> None:
    d = ctx.datos
    actual = d.get("mrz_linea1")
    act_n = re.sub(r"\s+", "", _mrz_norm(actual)) if not _vacio(actual) else ""
    if _l1_valida(act_n, _nombres_mrz(d)):
        if act_n != actual:
            ctx.poner("mrz_linea1", act_n, "mrz_linea1_literal")
        return
    lit = _l1_literal(ctx)
    if lit:
        ctx.poner("mrz_linea1", lit, "mrz_linea1_literal")
        return
    if ctx.derivados:
        der = _l1_derivada(ctx)
        if der:
            ctx.poner("mrz_linea1", der, "mrz_linea1_desde_campos")


_RE_CURP_ESPACIADA = re.compile(r"(?<![A-Z0-9])((?:[A-Z0-9]{1,6} ){1,4}[A-Z0-9]{1,12})(?![A-Z0-9])")


def _regla_curp_espacios(ctx) -> None:
    d = ctx.datos
    cur = _mayus(d.get("curp")).strip()
    if cur and curp_valida(cur):
        return
    cands: set[str] = set()
    for ln in _lineas(ctx):
        for m in _RE_CURP_ESPACIADA.finditer(_mayus(ln)):
            s = m.group(1).replace(" ", "")
            if len(s) == 18:
                c = R._reparar_curp(s)
                if c:
                    cands.add(c)
    if len(cands) == 1 and R._curp_consistente(next(iter(cands)), d, True):
        ctx.poner("curp", next(iter(cands)), "curp_con_espacios")


def aplicar_pasaporte(ctx) -> None:
    """Punto de entrada (lo invoca reconcile() con el flag RECONCILE_PASAPORTE)."""
    for regla in (_regla_curp_espacios, _regla_nombres, _regla_apellidos, _regla_mrz2,
                  _regla_sexo, _regla_mrz1):
        try:
            regla(ctx)
        except Exception as e:  # una regla defectuosa nunca rompe el pipeline
            logger.warning("reconcile_pasaporte_regla_fallo",
                           extra={"regla": regla.__name__, "error": type(e).__name__})
