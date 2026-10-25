"""
application/reconcile.py

Reconciliación determinista de los campos extraídos (validación cruzada y
reasignación) SIN modelos ni dependencias externas (solo stdlib + el módulo
de corrección OCR, que también es stdlib pura).

Principios (compatibles con la spec existente):
  * Anti-alucinación: todo valor nuevo sale del texto crudo del OCR (o se
    ensambla con campos que ya salen de él, p. ej. nombre_completo). Nunca se
    inventa.
  * El valor del LLM tiene prioridad: solo se sustituye cuando es claramente
    basura (etiqueta, entidad federativa como apellido, CURP del progenitor
    en la persona, valor truncado...) o es inconsistente con una estructura
    verificable del formato (dígito verificador de la CURP/MRZ, fecha y sexo
    codificados en la CURP...) y existe un candidato literal que SÍ lo cumple.
  * Ante la duda no se cambia.
  * Cada cambio queda registrado (campo, anterior -> nuevo, regla).

Uso:   reconcile(doc_type, raw_text, datos) -> ReconcileResult(datos, cambios)
"""
from __future__ import annotations

import html
import itertools
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional

from infrastructure.regex.correccion_ocr import (
    curp_valida,
    digito_verificador_curp,
    pistas_curp,
)


# ── Resultado ─────────────────────────────────────────────────────────────────

@dataclass
class Cambio:
    campo: str
    anterior: Any
    nuevo: Any
    regla: str


@dataclass
class ReconcileResult:
    datos: dict
    cambios: list = field(default_factory=list)


# ── Utilidades de texto ───────────────────────────────────────────────────────

def _plano(s: str) -> str:
    """Quita acentos conservando la longitud (índices alineados con el original)."""
    return "".join(unicodedata.normalize("NFD", c)[0] for c in s)


def _n(s: Any) -> str:
    """Normalización para comparar: sin acentos, mayúsculas, solo A-Z0-9."""
    return re.sub(r"[^A-Z0-9]", "", _plano(str(s or "")).upper())


def _tokens(s: Any) -> list[str]:
    return [t for t in re.split(r"\s+", _plano(str(s or "")).upper().strip()) if t]


def _vacio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


PARTICULAS = {"DE", "DEL", "LA", "LAS", "LOS", "Y", "MC", "MAC", "VON", "VAN", "SAN", "SANTA"}

_ENTIDADES = {
    "AGUASCALIENTES", "BAJACALIFORNIA", "BAJACALIFORNIASUR", "CAMPECHE", "COAHUILA",
    "COAHUILADEZARAGOZA", "COLIMA", "CHIAPAS", "CHIHUAHUA", "CIUDADDEMEXICO", "DISTRITOFEDERAL",
    "DURANGO", "GUANAJUATO", "GUERRERO", "HIDALGO", "JALISCO", "MEXICO", "ESTADODEMEXICO",
    "MICHOACAN", "MICHOACANDEOCAMPO", "MORELOS", "NAYARIT", "NUEVOLEON", "OAXACA", "PUEBLA",
    "QUERETARO", "QUINTANAROO", "SANLUISPOTOSI", "SINALOA", "SONORA", "TABASCO", "TAMAULIPAS",
    "TLAXCALA", "VERACRUZ", "VERACRUZDEIGNACIODELALLAVE", "YUCATAN", "ZACATECAS",
}

# Palabras de etiqueta de los formularios: nunca forman parte de un nombre.
_PALABRAS_ETIQUETA = {
    "NOMBRE", "NOMBRES", "APELLIDO", "APELLIDOS", "SEXO", "FECHA", "NACIMIENTO", "DOMICILIO",
    "ESTADO", "MUNICIPIO", "SECCION", "LOCALIDAD", "EMISION", "VIGENCIA", "CLAVE", "ELECTOR",
    "CURP", "REGISTRO", "CODIGO", "QR", "FIRMA", "NACIONALIDAD", "LUGAR", "DATOS", "PERSONA",
    "REGISTRADA", "FILIACION", "ACTA", "ENTIDAD", "CREDENCIAL", "VOTAR", "INSTITUTO", "NACIONAL",
    "ELECTORAL", "PASAPORTE", "PASSPORT", "FOLIO", "CERTIFICADO", "OFICIALIA", "LIBRO",
}
_ETIQUETAS_LARGAS = [p for p in _PALABRAS_ETIQUETA if len(p) >= 6]


def _es_etiqueta(valor: Any) -> bool:
    """El valor es (o contiene) texto de etiqueta del formulario, no un dato."""
    toks = [re.sub(r"[^A-Z0-9]", "", t) for t in _tokens(valor)]
    toks = [t for t in toks if t]
    if not toks:
        return False
    if any(t in _PALABRAS_ETIQUETA for t in toks):
        return True
    for t in toks:  # etiqueta ruidosa ('NAEMENTO', 'SECCON'): parecido alto a una etiqueta larga
        if len(t) >= 6 and any(SequenceMatcher(None, t, e).ratio() >= 0.85 for e in _ETIQUETAS_LARGAS):
            return True
    return False


def _es_entidad(valor: Any) -> bool:
    return _n(valor) in _ENTIDADES


def _apellido_basura(valor: Any) -> bool:
    return _es_etiqueta(valor) or _es_entidad(valor)


def _limpiar_raw(raw: str) -> str:
    t = html.unescape(raw or "")
    t = re.sub(r"\A---.*?---\s*", "", t, flags=re.S)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    lineas = []
    for ln in t.splitlines():
        ln = re.sub(r"^[\s|#·*>]+", "", ln)
        ln = re.sub(r"[|]+", " ", ln)
        ln = re.sub(r"[ \t]+", " ", ln).strip(" \t·")
        if ln and not re.fullmatch(r"[-:\s]+", ln):
            lineas.append(ln)
    return "\n".join(lineas)


# ── CURP ──────────────────────────────────────────────────────────────────────

_A_DIGITO = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7"}
_A_LETRA = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "7": "T"}
_RE_CURP_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9|!]{4} ?[A-Za-z0-9|!]{14}(?![A-Za-z0-9])")


def _clase_pos(i: int) -> str:
    if i < 4 or 11 <= i <= 15:
        return "L"
    if 4 <= i <= 9 or i == 17:
        return "D"
    if i == 10:
        return "S"
    return "A"  # 16: alfanumérico


def _alternativas(c: str, i: int) -> list[str]:
    cl = _clase_pos(i)
    if cl == "L":
        return ([c] + ([_A_LETRA[c]] if c in _A_LETRA else []) + (["I"] if c in "L1" and c != "I" else [])
                + (["D", "Q"] if c == "0" else []))
    if cl == "D":
        return [c] + ([_A_DIGITO[c]] if c in _A_DIGITO else [])
    if cl == "S":
        return [c] + (["H"] if c in "NK" else []) + (["M"] if c == "N" else [])
    return [c] + ([_A_DIGITO[c]] if c in _A_DIGITO else []) + ([_A_LETRA[c]] if c in _A_LETRA else [])


def _reparar_curp(token: str) -> Optional[str]:
    """
    Devuelve una CURP con dígito verificador válido a partir de un token ruidoso
    cambiando el MÍNIMO de caracteres (confusiones típicas de OCR dependientes de
    la posición). Si no hay una única solución con el mínimo de cambios -> None.
    """
    base = re.sub(r"\s", "", token)
    if len(base) != 18:
        return None
    base = "".join("I" if c == "l" else c.upper() for c in base).replace("|", "I").replace("!", "I")
    # nacidos antes de 2000 (año de 2 dígitos > 26): la homoclave (pos. 16) es SIEMPRE un dígito
    if base[4:6].isdigit() and int(base[4:6]) > 26 and not base[16].isdigit():
        base = base[:16] + _A_DIGITO.get(base[16], base[16]) + base[17:]
    if curp_valida(base):
        return base
    opciones = [_alternativas(c, i) for i, c in enumerate(base)]
    variables = [i for i, o in enumerate(opciones) if len(o) > 1]
    for k in (1, 2):
        validas = set()
        for combo in itertools.combinations(variables, k):
            alts = [opciones[i][1:] for i in combo]
            for sel in itertools.product(*alts):
                c = list(base)
                for i, ch in zip(combo, sel):
                    c[i] = ch
                cand = "".join(c)
                if curp_valida(cand):
                    validas.add(cand)
        if len(validas) == 1:
            return validas.pop()
        if len(validas) > 1:
            return None
    return None


def _curps_en_texto(texto: str) -> list[tuple[int, str]]:
    """[(posición, CURP válida)] encontradas (literal o con confusiones OCR reparables)."""
    res = []
    for m in _RE_CURP_TOKEN.finditer(texto):
        c = _reparar_curp(m.group(0))
        if c:
            res.append((m.start(), c))
    return res


def _fecha_de_curp(curp: str) -> Optional[str]:
    try:
        yy, mm, dd = int(curp[4:6]), int(curp[6:8]), int(curp[8:10])
    except ValueError:
        return None
    siglo = 2000 if not curp[16].isdigit() else 1900
    return f"{dd:02d}/{mm:02d}/{siglo + yy}"


def _yymmdd(fecha: Any) -> Optional[str]:
    m = re.fullmatch(r"\s*(\d{1,2})[/\-. ](\d{1,2})[/\-. ](\d{4})\s*", str(fecha or ""))
    return f"{m.group(3)[2:]}{int(m.group(2)):02d}{int(m.group(1)):02d}" if m else None


def _sexo_mx(sexo: Any, icao: bool = False) -> Optional[str]:
    s = _n(sexo)
    if s in ("H", "HOMBRE", "MASCULINO"):
        return "H"
    if s in ("MUJER", "FEMENINO", "F"):
        return "M"
    if s == "M":
        return "H" if icao else "M"
    return None


def _curp_consistente(curp: str, d: dict, icao: bool = False) -> bool:
    """La CURP no contradice la fecha, el sexo ni (si hay) las letras del nombre."""
    ymd = _yymmdd(d.get("fecha_nacimiento"))
    if ymd and curp[4:10] != ymd:
        return False
    sx = _sexo_mx(d.get("sexo"), icao)
    if sx and curp[10] != sx:
        return False
    return True


def _nombre_consistente(curp: str, nombres: Any, ap1: Any, ap2: Any) -> bool:
    if _vacio(nombres) or _vacio(ap1):
        return True
    p = pistas_curp(str(nombres), str(ap1), None if _vacio(ap2) else str(ap2))
    return all(curp[i] == c for i, c in p.items())


# ── Contexto de reconciliación ────────────────────────────────────────────────

class _Ctx:
    def __init__(self, raw: str, datos: dict):
        self.raw = raw or ""
        self.texto = _limpiar_raw(self.raw)
        self.plano = _plano(self.texto)
        self.datos = datos
        self.cambios: list[Cambio] = []

    def poner(self, campo: str, nuevo: Any, regla: str) -> None:
        ant = self.datos.get(campo)
        if ant == nuevo or (isinstance(ant, str) and isinstance(nuevo, str) and ant.strip() == nuevo.strip()):
            return
        self.datos[campo] = nuevo
        self.cambios.append(Cambio(campo, ant, nuevo, regla))


# ── Regla: CURP ───────────────────────────────────────────────────────────────

def _regla_curp(ctx: _Ctx, tipo: str, excluir: Optional[set] = None) -> None:
    """
    CURP ausente / inválida / contradictoria con fecha y sexo del propio documento:
    se toma del texto crudo la ÚNICA CURP con dígito verificador válido (tras
    reparar confusiones OCR de mínimo cambio) que sea consistente.
    """
    d = ctx.datos
    icao = tipo == "pasaporte"
    cur = d.get("curp")
    cur_n = str(cur).strip().upper() if not _vacio(cur) else None
    cur_ok = bool(cur_n) and curp_valida(cur_n) and _curp_consistente(cur_n, d, icao) \
        and (excluir is None or cur_n not in excluir)
    if cur_ok:
        return
    cands = {c for _, c in _curps_en_texto(ctx.texto) if c != cur_n}
    if excluir:
        cands -= excluir
    cands = {c for c in cands if _curp_consistente(c, d, icao)}
    if len(cands) > 1:  # las letras del nombre solo desempatan (el nombre extraído puede ser el erróneo)
        cands = {c for c in cands
                 if _nombre_consistente(c, d.get("nombres"), d.get("primer_apellido"), d.get("segundo_apellido"))}
    if len(cands) == 1:
        ctx.poner("curp", cands.pop(), "curp_recuperada_del_texto")


def _regla_sexo_fecha_desde_curp(ctx: _Ctx, tipo: str) -> None:
    """
    Con CURP válida, RELLENA sexo y fecha de nacimiento ausentes con los que codifica.
    (Solo rellena: sustituir un valor ya extraído exigiría resolver el siglo de la
    fecha, que la CURP solo infiere por la homoclave; en la medición esa variante
    produjo una regresión y se descartó.)
    """
    d = ctx.datos
    cur = str(d.get("curp") or "").strip().upper()
    if not curp_valida(cur):
        return
    if tipo != "pasaporte" and _vacio(d.get("sexo")):
        ctx.poner("sexo", cur[10], "sexo_desde_curp")
    if _vacio(d.get("fecha_nacimiento")):
        f = _fecha_de_curp(cur)
        if f:
            ctx.poner("fecha_nacimiento", f, "fecha_nacimiento_desde_curp")


# ── Regla: nombres y apellidos ────────────────────────────────────────────────

def _resegmentar(words: list[str], curp: str) -> Optional[tuple[str, str, str]]:
    """Reparte las palabras en (ap1, ap2, nombres) de forma única consistente con la CURP."""
    n = len(words)
    sols = set()
    for i in range(1, n):
        if words[i - 1] in PARTICULAS:
            continue
        for j in range(i, n):
            if j > i and words[j - 1] in PARTICULAS:
                continue
            if j >= n:
                continue
            ap1, ap2, nom = " ".join(words[:i]), " ".join(words[i:j]), " ".join(words[j:])
            p = pistas_curp(nom, ap1, ap2)
            if all(curp[k] == c for k, c in p.items()):
                sols.add((ap1, ap2, nom))
    return next(iter(sols)) if len(sols) == 1 else None


def _regla_nombres(ctx: _Ctx, tipo: str) -> None:
    d = ctx.datos
    # 1) apellido que es una etiqueta del formulario o una entidad federativa -> basura
    for c in ("primer_apellido", "segundo_apellido"):
        if not _vacio(d.get(c)) and _apellido_basura(d[c]):
            ctx.poner(c, None, "apellido_es_etiqueta_o_entidad")
    if not _vacio(d.get("nombres")) and _es_etiqueta(d["nombres"]):
        ctx.poner("nombres", None, "nombres_es_etiqueta")
    # 2) reparto consistente con la CURP cuando los valores actuales la contradicen
    cur = str(d.get("curp") or "").strip().upper()
    if curp_valida(cur) and not _vacio(d.get("nombres")) and not _vacio(d.get("primer_apellido")):
        dos_apellidos_juntos = _vacio(d.get("segundo_apellido")) and len(
            [t for t in _tokens(d.get("primer_apellido")) if t not in PARTICULAS]) >= 2
        if dos_apellidos_juntos or not _nombre_consistente(
                cur, d.get("nombres"), d.get("primer_apellido"), d.get("segundo_apellido")):
            words = _tokens(d.get("primer_apellido")) + _tokens(d.get("segundo_apellido")) + _tokens(d.get("nombres"))
            sol = _resegmentar(words, cur)
            if sol:
                for campo, val in zip(("primer_apellido", "segundo_apellido", "nombres"), sol):
                    ctx.poner(campo, val or None, "nombres_resegmentados_por_curp")
    # 3) nombre_completo = nombres + primer apellido + segundo apellido
    nombres, ap1, ap2 = d.get("nombres"), d.get("primer_apellido"), d.get("segundo_apellido")
    if _vacio(nombres) or _vacio(ap1):
        return
    if curp_valida(cur) and not _nombre_consistente(cur, nombres, ap1, ap2):
        return
    ensamblado = " ".join(str(x).strip() for x in (nombres, ap1, ap2) if not _vacio(x))
    actual = d.get("nombre_completo")
    if _vacio(actual):
        ctx.poner("nombre_completo", ensamblado, "nombre_completo_ensamblado")
        return
    a, e = _tokens(actual), _tokens(ensamblado)
    if sorted(a) == sorted(e):
        if a != e:
            ctx.poner("nombre_completo", ensamblado, "nombre_completo_ensamblado")
    elif curp_valida(cur) or _es_etiqueta(actual) or all(t in a for t in e) or all(t in e for t in a):
        # contiene basura añadida / truncado: con CURP que confirma los componentes manda el ensamblado
        ctx.poner("nombre_completo", ensamblado, "nombre_completo_ensamblado")


# ── Regla: acta de nacimiento (maquetación del formulario) ───────────────────

_RE_FIL = re.compile(r"Datos de Filiaci.n", re.I)
_RE_ANOT = re.compile(r"Anotaciones Marginales", re.I)
_RE_TOK_MAYUS = re.compile(r"[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ'.\-]*")


def _valor_antes(texto: str, plano: str, m: re.Match) -> Optional[str]:
    """Valor impreso JUSTO ANTES de una etiqueta (diseño 'VALOR Etiqueta' de las actas)."""
    ini = texto.rfind("\n", 0, m.start()) + 1
    previo = texto[ini:m.start()].strip()
    if not previo:  # la etiqueta está sola en su línea -> el valor es la línea anterior
        if ini == 0:
            return None
        fin = ini - 1
        ini2 = texto.rfind("\n", 0, fin) + 1
        previo = texto[ini2:fin].strip()
    toks = []
    for t in reversed(previo.split()):
        t = t.strip("·.,;:")
        if _RE_TOK_MAYUS.fullmatch(t) and len(toks) < 6:
            toks.append(t)
        else:
            break
    return " ".join(reversed(toks)) or None


def _fuera_region(ctx: _Ctx, patron: str, fil_ini: int, fil_fin: int) -> list[Optional[str]]:
    vals = []
    for m in re.finditer(patron, ctx.plano, re.I):
        if fil_ini <= m.start() < fil_fin:
            continue
        vals.append(_valor_antes(ctx.texto, ctx.plano, m))
    return vals


def _decidir_reemplazo(cur: Any, cand: Optional[str], otros: set, campo: str, apellido: bool) -> bool:
    if _vacio(cand):
        return False
    if _vacio(cur):
        return True
    cn = _n(cur)
    if cn == _n(cand):
        return False
    if cn in otros or _es_etiqueta(cur) or (apellido and _es_entidad(cur)):
        return True  # asignado al campo equivocado / basura identificable
    tc, tk = _tokens(cur), _tokens(cand)
    return len(tc) < len(tk) and tk[:len(tc)] == tc or (len(tc) < len(tk) and all(t in tk for t in tc))


def _regla_acta(ctx: _Ctx) -> None:
    d = ctx.datos
    m_fil = _RE_FIL.search(ctx.plano)
    if not m_fil:
        return
    fil_ini = m_fil.end()
    m_anot = _RE_ANOT.search(ctx.plano, fil_ini)
    fil_fin = m_anot.start() if m_anot else len(ctx.plano)

    # persona registrada: etiquetas FUERA de la región de filiación (exactamente una ocurrencia)
    etiq = {
        "nombres": r"Nombre\(s\)", "primer_apellido": r"Primer Apellido",
        "segundo_apellido": r"Segundo Apellido", "lugar_nacimiento": r"Lugar de Nacimiento",
    }
    cands: dict[str, Optional[str]] = {}
    for campo, pat in etiq.items():
        vals = _fuera_region(ctx, pat, fil_ini, fil_fin)
        cands[campo] = vals[0] if len(vals) == 1 else None
    for campo, cand in cands.items():
        if not cand:
            continue
        otros = {_n(v) for k, v in cands.items() if k != campo and v}
        ap = campo.endswith("apellido")
        if ap and _apellido_basura(cand):
            continue
        if _decidir_reemplazo(d.get(campo), cand, otros, campo, ap):
            ctx.poner(campo, cand, "acta_valor_por_etiqueta")
    # sexo de la persona
    vs = _fuera_region(ctx, r"\bSexo\b", fil_ini, fil_fin)
    if len(vs) == 1 and vs[0] and _sexo_mx(vs[0]) and _sexo_mx(d.get("sexo")) != _sexo_mx(vs[0]):
        if _vacio(d.get("sexo")) or _es_etiqueta(d.get("sexo")):
            ctx.poner("sexo", _sexo_mx(vs[0]), "acta_valor_por_etiqueta")

    # filiación: dos columnas (1ª y 2ª ocurrencia de cada etiqueta)
    sub = {"nombre": r"Nombre\(s\)", "ap1": r"Primer Apellido", "ap2": r"Segundo Apellido",
           "nac": r"Nacionalidad"}
    col = {}
    for k, pat in sub.items():
        vals = [_valor_antes(ctx.texto, ctx.plano, m)
                for m in re.finditer(pat, ctx.plano, re.I) if fil_ini <= m.start() < fil_fin]
        col[k] = vals if len(vals) == 2 else [None, None]
    curps = _curps_en_texto(ctx.texto[fil_ini:fil_fin])
    # CURP por sexo: H -> padre, M -> madre (estructura de la CURP)
    por_sexo = {}
    for _, c in curps:
        por_sexo.setdefault(c[10], []).append(c)
    curp_padre = por_sexo["H"][0] if len(por_sexo.get("H", [])) == 1 else None
    curp_madre = por_sexo["M"][0] if len(por_sexo.get("M", [])) == 1 else None
    # ¿la columna 0 es el padre? orden del texto, confirmado por el sexo de las CURP si se conocen
    padre_idx = 0
    if len(curps) == 2 and curps[0][1][10] == "M" and curps[1][1][10] == "H":
        padre_idx = 1
    for rol, idx, curp_rol in (("padre", padre_idx, curp_padre), ("madre", 1 - padre_idx, curp_madre)):
        partes = {"nombre": col["nombre"][idx], "ap1": col["ap1"][idx], "ap2": col["ap2"][idx]}
        if partes["ap1"] and _apellido_basura(partes["ap1"]):
            continue
        for k, campo in (("ap1", f"primer_apellido_{rol}"), ("ap2", f"segundo_apellido_{rol}"),
                         ("nac", f"nacionalidad_{rol}")):
            v = col[k][idx]
            if v and _decidir_reemplazo(d.get(campo), v, set(), campo, k != "nac"):
                ctx.poner(campo, v, "acta_filiacion_por_etiqueta")
        if partes["nombre"] and partes["ap1"]:
            completo = " ".join(x for x in (partes["nombre"], partes["ap1"], partes["ap2"]) if x)
            cur = d.get(f"nombre_{rol}")
            if _vacio(cur) or _n(cur) != _n(completo) and (
                    all(t in _tokens(completo) for t in _tokens(cur)) or _es_etiqueta(cur)):
                ctx.poner(f"nombre_{rol}", completo, "acta_filiacion_por_etiqueta")
        # CURP del progenitor: el sexo codificado debe corresponder al rol
        campo_c = f"curp_{rol}"
        cur = d.get(campo_c)
        esperado = "H" if rol == "padre" else "M"
        cur_n = str(cur).strip().upper() if not _vacio(cur) else None
        if curp_rol and cur_n != curp_rol:
            if cur_n is None or not curp_valida(cur_n) or cur_n[10] != esperado or _n(cur_n) != _n(curp_rol):
                ctx.poner(campo_c, curp_rol, "acta_curp_progenitor_por_sexo")
        elif cur_n and curp_valida(cur_n) and cur_n[10] != esperado:
            ctx.poner(campo_c, None, "acta_curp_progenitor_sexo_incoherente")

    # CURP de la persona: nunca la de un progenitor
    ctx_parent = {c for _, c in curps}
    _regla_curp(ctx, "acta_nacimiento", excluir=ctx_parent)
    # identificador electrónico: número de 20 dígitos
    id20 = set(re.findall(r"(?<!\d)\d{20}(?!\d)", ctx.texto))
    cur = d.get("identificador_electronico")
    if len(id20) == 1 and (_vacio(cur) or not re.fullmatch(r"\d{20}", str(cur).strip())):
        ctx.poner("identificador_electronico", id20.pop(), "acta_identificador_20_digitos")


# ── Regla: constancia CURP (folio) ───────────────────────────────────────────

def _regla_folio_curp(ctx: _Ctx) -> None:
    if not _vacio(ctx.datos.get("folio")):
        return
    cands = set()
    for m in re.finditer(r"\bFolio\b", ctx.texto, re.I):
        ventana = ctx.texto[max(0, m.start() - 60): m.end() + 80]
        cands |= set(re.findall(r"(?<![\d/.\-])\d{8}(?![\d/.\-])", ventana))
    if len(cands) == 1:
        ctx.poner("folio", cands.pop(), "curp_folio_8_digitos_junto_a_etiqueta")


# ── Regla: INE (apellidos por CURP, clave de elector, domicilio) ─────────────

def _palabras_candidatas(ctx: _Ctx) -> set[str]:
    """Palabras en MAYÚSCULAS que aparecen solas en una línea o en una línea de solo mayúsculas."""
    pool = set()
    for ln in ctx.texto.splitlines():
        toks = ln.split()
        if 1 <= len(toks) <= 5 and all(re.fullmatch(r"[A-ZÁÉÍÓÚÜÑ]{2,}", t) for t in toks):
            for t in toks:
                if not _es_etiqueta(t):
                    pool.add(t)
    return pool


def _componente_ok(cur: str, rol: str, valor: Any) -> bool:
    """Las letras de la CURP que dependen de ESTE componente coinciden con él."""
    if _vacio(valor):
        return False
    pos = _POS_CURP[rol]
    pw = pistas_curp(valor if rol == "nom" else "X", valor if rol == "ap1" else "X", valor if rol == "ap2" else "X")
    return all(pw.get(k) == cur[k] for k in pos)


_POS_CURP = {"ap1": (0, 1, 13), "ap2": (2, 14), "nom": (3, 15)}
_CAMPO_ROL = {"primer_apellido": "ap1", "segundo_apellido": "ap2", "nombres": "nom"}


def _regla_nombre_por_pool(ctx: _Ctx) -> None:
    """
    Con CURP válida, cada componente del nombre debe cumplir sus letras en la CURP.
    Si uno no las cumple, se busca en el texto la ÚNICA palabra literal que sí
    (descartando las palabras ya usadas por otros componentes que sí cumplen).
    """
    d = ctx.datos
    cur = str(d.get("curp") or "").strip().upper()
    if not curp_valida(cur):
        return
    pool = _palabras_candidatas(ctx)
    for campo, rol in _CAMPO_ROL.items():
        if rol == "ap2" and _vacio(d.get(campo)):
            continue
        if _componente_ok(cur, rol, d.get(campo)):
            continue
        usadas = {_n(d.get(c)) for c, r in _CAMPO_ROL.items() if r != rol and _componente_ok(cur, r, d.get(c))}
        propios = [w for w in pool if w not in PARTICULAS and _n(w) not in usadas and _componente_ok(cur, rol, w)]
        if len(propios) == 1:
            ctx.poner(campo, propios[0], "componente_nombre_por_curp_y_texto")


def _regla_clave_elector(ctx: _Ctx) -> None:
    """Clave de elector = 6 letras + fecha (6 dígitos) + entidad (2) + sexo + 3 dígitos."""
    d = ctx.datos
    cur = str(d.get("curp") or "").strip().upper()
    ymd = cur[4:10] if curp_valida(cur) else _yymmdd(d.get("fecha_nacimiento"))
    sx = cur[10] if curp_valida(cur) else _sexo_mx(d.get("sexo"))
    if not ymd or not sx:
        return

    def ok(c: str) -> bool:
        return bool(re.fullmatch(r"[A-Z]{6}\d{6}\d{2}[HM]\d{3}", c)) and c[6:12] == ymd and c[14] == sx

    actual = str(d.get("clave_elector") or "").strip().upper()
    if ok(actual):
        return
    cands = set()
    for m in re.finditer(r"(?<![A-Za-z0-9])[A-Za-z0-9]{18}(?![A-Za-z0-9])", ctx.plano):
        t = m.group(0).upper()
        c = "".join(
            (_A_LETRA.get(ch, ch) if i < 6 else _A_DIGITO.get(ch, ch) if i != 14 else ch)
            for i, ch in enumerate(t))
        if ok(c):
            cands.add(c)
    if len(cands) == 1:
        ctx.poner("clave_elector", cands.pop(), "clave_elector_consistente_con_curp")


def _regla_ine(ctx: _Ctx) -> None:
    d = ctx.datos
    partes = [d.get("domicilio_calle"), d.get("domicilio_colonia"), d.get("domicilio_municipio_ciudad")]
    if _vacio(d.get("domicilio")) and sum(not _vacio(p) for p in partes) >= 2:
        ctx.poner("domicilio", " ".join(str(p).strip() for p in partes if not _vacio(p)),
                  "domicilio_ensamblado")


# ── Regla: pasaporte (MRZ ICAO 9303 TD3) ─────────────────────────────────────

_MRZ_GLIFOS = str.maketrans({"‹": "<", "«": "<", "＜": "<", "≤": "<", "〈": "<"})
_PESOS = (7, 3, 1)


def _val_mrz(c: str) -> int:
    if c.isdigit():
        return int(c)
    if c == "<":
        return 0
    return ord(c) - 55 if "A" <= c <= "Z" else -1


def _dv_mrz(s: str) -> str:
    return str(sum(_val_mrz(c) * _PESOS[i % 3] for i, c in enumerate(s)) % 10)


def _mrz2_valida(s: str) -> bool:
    return (len(s) == 44 and re.fullmatch(r"[A-Z0-9<]{44}", s) is not None
            and _dv_mrz(s[0:9]) == s[9] and _dv_mrz(s[13:19]) == s[19] and _dv_mrz(s[21:27]) == s[27]
            and _dv_mrz(s[28:42]) == s[42]
            and _dv_mrz(s[0:10] + s[13:20] + s[21:43]) == s[43])


def _mrz_norm(s: str) -> str:
    return html.unescape(str(s)).translate(_MRZ_GLIFOS).upper()


def _reparar_mrz2(s: str, curp: Optional[str]) -> Optional[str]:
    """Repara la línea 2 posicionalmente; solo se acepta si pasan los 5 dígitos de control."""
    s = re.sub(r"\s+", "", _mrz_norm(s))
    s = re.sub(r"[^A-Z0-9<]", "<", s)
    if len(s) < 30:
        return None
    pre, post = s[:28], s[-2:]
    pre = list(pre.ljust(28, "<"))
    for i in (9, 19, 27) + tuple(range(13, 19)) + tuple(range(21, 27)):
        if not pre[i].isdigit():
            pre[i] = _A_DIGITO.get(pre[i], pre[i])
    for i in (10, 11, 12):
        if not pre[i].isalpha():
            pre[i] = _A_LETRA.get(pre[i], pre[i])
    if pre[20] not in "MF<":
        pre[20] = {"E": "F", "P": "F", "H": "M", "N": "M"}.get(pre[20], pre[20])
        if pre[20] not in "MF<" and curp and curp_valida(curp):
            pre[20] = "M" if curp[10] == "H" else "F"
    post = "".join(_A_DIGITO.get(c, c) if not c.isdigit() else c for c in post)
    medio = s[28:-2]
    candidatos = []
    if len(medio) == 14 and re.fullmatch(r"[A-Z0-9<]{14}", medio):
        candidatos.append(medio)
    candidatos.append("<" * 14)  # el campo opcional suele ir vacío: relleno '<'
    for m in candidatos:
        cand = "".join(pre) + m + post
        if _mrz2_valida(cand):
            return cand
    return None


def _parse_mrz1(s: str) -> Optional[tuple[str, str]]:
    s = _mrz_norm(s)
    s = re.sub(r"\s", "<", s)
    s = re.sub(r"[^A-Z0-9<]", "<", s)
    m = re.match(r"P<MEX([A-Z]+(?:<[A-Z]+)*)<<([A-Z]+(?:<[A-Z]+)*)", s)
    return (m.group(1), m.group(2)) if m else None


def _construir_mrz1(sur: str, nom: str) -> str:
    return ("P<MEX" + sur + "<<" + nom).ljust(44, "<")[:44]


def _distancia(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _regla_pasaporte(ctx: _Ctx) -> None:
    d = ctx.datos
    curp = str(d.get("curp") or "").strip().upper() or None
    # línea 2
    l2 = d.get("mrz_linea2")
    l2n = re.sub(r"\s+", "", _mrz_norm(l2)) if not _vacio(l2) else ""
    if _vacio(l2) or not _mrz2_valida(l2n):
        rep = _reparar_mrz2(l2, curp) if not _vacio(l2) else None
        if rep is None:  # buscar en el texto crudo
            plano = re.sub(r"\s+", "", _mrz_norm(ctx.texto))
            for m in re.finditer(r"[A-Z0-9]{9}[0-9OQDIL]MEX[0-9]{6}[0-9OQDIL][MF<EPHN][0-9OQDIL]{6}[0-9OQDIL]", plano):
                rep = _reparar_mrz2(plano[m.start():m.start() + 44], curp)
                if rep:
                    break
        if rep:
            ctx.poner("mrz_linea2", rep, "mrz_linea2_reparada_por_digitos_control")
            l2n = rep
    elif l2n != l2:
        ctx.poner("mrz_linea2", l2n, "mrz_linea2_reparada_por_digitos_control")
    # línea 1
    l1 = d.get("mrz_linea1")
    if _vacio(l1):
        for ln in ctx.texto.splitlines():
            i = _mrz_norm(ln).find("P<MEX")
            if i >= 0:
                l1 = _mrz_norm(ln)[i:]
                l1 = re.split(r"\s[A-Z0-9]{9}[0-9]MEX", l1)[0]
                break
    if not _vacio(l1):
        parsed = _parse_mrz1(l1)
        if parsed:
            cand = _construir_mrz1(*parsed)
            # los campos visuales (literales en el texto) corrigen 1-2 errores de lectura de la MRZ
            if not any(_vacio(d.get(c)) for c in ("primer_apellido", "nombres")) \
                    and not _apellido_basura(d["primer_apellido"]):
                sur = "<".join(_tokens(d["primer_apellido"]) + _tokens(d.get("segundo_apellido")))
                nom = "<".join(_tokens(d["nombres"]))
                por_campos = _construir_mrz1(sur, nom)
                if re.fullmatch(r"[A-Z<]+", por_campos) and _distancia(por_campos, cand) <= 2:
                    cand = por_campos
            if re.fullmatch(r"[A-Z0-9<]{44}", cand):
                ctx.poner("mrz_linea1", cand, "mrz_linea1_normalizada")
    # sexo (convención ICAO M/F): línea 2 válida o CURP
    s_mrz = l2n[20] if len(l2n) == 44 and _mrz2_valida(l2n) and l2n[20] in "MF" else None
    s_curp = ("M" if curp[10] == "H" else "F") if curp and curp_valida(curp) else None
    objetivo = s_mrz or s_curp
    if objetivo and str(d.get("sexo") or "").strip().upper() != objetivo:
        ctx.poner("sexo", objetivo, "sexo_desde_mrz_o_curp")


# ── API pública ───────────────────────────────────────────────────────────────

def reconcile(doc_type: str, raw_text: str, datos: dict) -> ReconcileResult:
    """Aplica las reglas de reconciliación. No modifica `datos` (trabaja sobre una copia)."""
    copia = dict(datos or {})
    ctx = _Ctx(raw_text, copia)
    tipo = str(getattr(doc_type, "value", doc_type) or "").lower()

    def _seguro(fn, *args):
        try:
            fn(*args)
        except Exception:  # una regla defectuosa nunca debe romper el pipeline
            pass

    if tipo == "acta_nacimiento":
        _seguro(_regla_acta, ctx)
    if tipo in ("ine", "curp", "pasaporte"):
        _seguro(_regla_curp, ctx, tipo)
    if tipo == "pasaporte":
        _seguro(_regla_pasaporte, ctx)
    if tipo in ("ine", "curp", "acta_nacimiento"):
        _seguro(_regla_sexo_fecha_desde_curp, ctx, tipo)
    if tipo in ("curp", "acta_nacimiento", "pasaporte"):
        _seguro(_regla_nombres, ctx, tipo)
    if tipo == "ine":
        _seguro(_regla_nombre_por_pool, ctx)
        _seguro(_regla_nombres, ctx, tipo)
        _seguro(_regla_clave_elector, ctx)
        _seguro(_regla_ine, ctx)
    if tipo == "curp":
        _seguro(_regla_folio_curp, ctx)
    return ReconcileResult(copia, ctx.cambios)
