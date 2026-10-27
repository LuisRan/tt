"""Validadores reutilizables: CURP, clave de elector INE, MRZ ICAO 9303, fechas
y coherencia cruzada de registros generados por `generate_record`.

Convención: las funciones `validate_*` devuelven `list[str]` de errores (vacía
= válido); las `is_valid_*` devuelven bool. Los constructores (`build_*`,
`curp_*`, `mrz_*`) implementan las mismas reglas y los usa el generador.
"""
from __future__ import annotations

import base64
import binascii
import re
from datetime import date
from typing import Any, Iterable, Sequence

from contracts import CATALOG, DocType, Face, Variant, default_variant
from contracts.field_catalog import CURP_RE
from contracts.layout_spec import FieldType

from .catalogs import (
    BY_ABREV,
    BY_CURP,
    BY_NOMBRE,
    BY_NUM,
    CIUDADES_EXTRANJERO,
    CURP_DICT,
    MESES,
    NOMBRES_SALTABLES,
    PALABRAS_INCONVENIENTES,
    PARTICULAS,
)
from .text import ENYE, icao_transliterate, name_words, upper_id

VOWELS = "AEIOU"
CLAVE_ELECTOR_RE = re.compile(r"^[A-Z]{6}\d{8}[HM]\d{3}$")
_CURP_RE = re.compile(CURP_RE)
AVATAR_RE = re.compile(r"^avatar:[0-9a-f]{16}$")
FIRMA_RE = re.compile(r"^firma:[0-9a-f]{16}$")

# ---------------------------------------------------------------------------
# Fechas
# ---------------------------------------------------------------------------
DATE_FORMATS = {
    "dmy": (re.compile(r"^(\d{2})/(\d{2})/(\d{4})$"), "{d:02d}/{m:02d}/{y:04d}"),
    "spaced": (re.compile(r"^(\d{2}) (\d{2}) (\d{4})$"), "{d:02d} {m:02d} {y:04d}"),
}


def format_date(d: date, fmt: str = "dmy") -> str:
    return DATE_FORMATS[fmt][1].format(d=d.day, m=d.month, y=d.year)


def parse_date(s: str, fmt: str = "dmy") -> date:
    """`dmy` = DD/MM/AAAA (INE, CURP, Acta); `spaced` = DD MM AAAA (pasaporte).
    Lanza ValueError si el formato o la fecha no son válidos."""
    m = DATE_FORMATS[fmt][0].match(s)
    if not m:
        raise ValueError(f"fecha {s!r} no tiene formato {fmt}")
    d, mo, y = (int(g) for g in m.groups())
    return date(y, mo, d)


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29-feb
        return d.replace(year=d.year + years, day=28)


def age_on(birth: date, on: date) -> int:
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


def is_valid_date(s: str, fmt: str = "dmy") -> bool:
    try:
        parse_date(s, fmt)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# CURP (instructivo normativo RENAPO)
# ---------------------------------------------------------------------------
def _significant_word(text: str, given_name: bool) -> str:
    words = name_words(text)
    if not words:
        return ""
    filtered = [w for w in words if w not in PARTICULAS] or words
    if given_name and len(filtered) > 1 and filtered[0] in NOMBRES_SALTABLES:
        filtered = filtered[1:]
    return filtered[0]


def _initial(word: str) -> str:
    if not word or word[0] == ENYE:
        return "X"
    return word[0]


def _internal_vowel(word: str) -> str:
    for c in word[1:]:
        if c in VOWELS:
            return c
    return "X"


def _internal_consonant(word: str) -> str:
    for c in word[1:]:
        if c not in VOWELS:
            return "X" if c == ENYE else c
    return "X"


def curp_name_letters(nombres: str, ap1: str, ap2: str | None) -> str:
    """Posiciones 1-4: inicial y 1a vocal interna del 1er apellido, inicial del
    2o apellido (X si no hay), inicial del nombre; palabra inconveniente -> 2a letra X."""
    w1 = _significant_word(ap1, False)
    w2 = _significant_word(ap2 or "", False)
    wn = _significant_word(nombres, True)
    four = _initial(w1) + _internal_vowel(w1) + (_initial(w2) if w2 else "X") + _initial(wn)
    if four in PALABRAS_INCONVENIENTES:
        four = four[0] + "X" + four[2:]
    return four


def curp_consonants(nombres: str, ap1: str, ap2: str | None) -> str:
    """Posiciones 14-16: 1a consonante interna de 1er apellido, 2o apellido y nombre."""
    w1 = _significant_word(ap1, False)
    w2 = _significant_word(ap2 or "", False)
    wn = _significant_word(nombres, True)
    return _internal_consonant(w1) + (_internal_consonant(w2) if w2 else "X") + _internal_consonant(wn)


def curp_check_digit(curp17: str) -> str:
    """Dígito verificador oficial: pesos 18..2 sobre el diccionario RENAPO."""
    if len(curp17) != 17:
        raise ValueError("se requieren los 17 primeros caracteres")
    total = 0
    for i, c in enumerate(curp17):
        idx = CURP_DICT.find(c)
        if idx < 0:
            raise ValueError(f"carácter inválido en CURP: {c!r}")
        total += idx * (18 - i)
    return str((10 - total % 10) % 10)


def curp_homoclave_ok(birth_year: int, h: str) -> bool:
    return h.isdigit() if birth_year < 2000 else (h.isalpha() and h.isupper())


def build_curp(nombres: str, ap1: str, ap2: str | None, birth: date, sexo: str,
               entidad_curp: str, homoclave: str) -> str:
    """sexo: 'H'|'M'|'X'; entidad_curp: código de 2 letras (p.ej. 'JC', 'NE')."""
    if not curp_homoclave_ok(birth.year, homoclave):
        raise ValueError("homoclave: dígito si nació antes de 2000, letra si 2000+")
    base = (
        curp_name_letters(nombres, ap1, ap2)
        + birth.strftime("%y%m%d")
        + sexo
        + entidad_curp
        + curp_consonants(nombres, ap1, ap2)
        + homoclave
    )
    return base + curp_check_digit(base)


def curp_birth_date(curp: str) -> date:
    """Fecha de nacimiento implícita (siglo por la posición 17)."""
    yy, mm, dd = int(curp[4:6]), int(curp[6:8]), int(curp[8:10])
    century = 1900 if curp[16].isdigit() else 2000
    return date(century + yy, mm, dd)


def validate_curp(curp: str) -> list[str]:
    """Estructura (regex del catálogo), fecha real y dígito verificador."""
    if not isinstance(curp, str) or not _CURP_RE.match(curp):
        return [f"CURP {curp!r} no cumple la estructura"]
    errs = []
    try:
        curp_birth_date(curp)
    except ValueError:
        errs.append(f"CURP {curp}: fecha inválida")
    if curp_check_digit(curp[:17]) != curp[17]:
        errs.append(f"CURP {curp}: dígito verificador incorrecto")
    return errs


def is_valid_curp(curp: str) -> bool:
    return not validate_curp(curp)


def validate_curp_person(curp: str, *, nombres: str, ap1: str, ap2: str | None,
                         birth: date, sexo: str, entidad_curp: str | None = None) -> list[str]:
    """Coherencia de la CURP con los datos de la persona."""
    errs = validate_curp(curp)
    if errs:
        return errs
    if curp[:4] != curp_name_letters(nombres, ap1, ap2):
        errs.append(f"CURP {curp}: letras 1-4 no corresponden a {ap1}/{ap2}/{nombres}")
    if curp[4:10] != birth.strftime("%y%m%d"):
        errs.append(f"CURP {curp}: fecha no corresponde a {birth}")
    if curp[10] != sexo:
        errs.append(f"CURP {curp}: sexo no corresponde a {sexo}")
    if entidad_curp is not None and curp[11:13] != entidad_curp:
        errs.append(f"CURP {curp}: entidad no corresponde a {entidad_curp}")
    if curp[13:16] != curp_consonants(nombres, ap1, ap2):
        errs.append(f"CURP {curp}: consonantes internas incorrectas")
    if not curp_homoclave_ok(birth.year, curp[16]):
        errs.append(f"CURP {curp}: homoclave no corresponde al siglo de nacimiento")
    return errs


def _splits(words: list[str], parts: int) -> Iterable[list[str]]:
    """Todas las particiones de `words` en `parts` grupos contiguos no vacíos."""
    if parts == 1:
        if words:
            yield [" ".join(words)]
        return
    for i in range(1, len(words) - parts + 2):
        for rest in _splits(words[i:], parts - 1):
            yield [" ".join(words[:i])] + rest


def curp_matches_full_name(curp: str, full_name: str, order: str = "nombres_primero") -> bool:
    """¿Existe una partición de `full_name` en (nombres, ap1, ap2) coherente con la CURP?
    order: 'nombres_primero' (NOMBRES AP1 AP2) o 'apellidos' (solo AP1 AP2, sin nombres)."""
    words = full_name.split()
    for parts in _splits(words, 3):
        n, a1, a2 = parts
        if curp[:4] == curp_name_letters(n, a1, a2) and curp[13:16] == curp_consonants(n, a1, a2):
            return True
    return False


def curp_matches_split_apellidos(curp: str, nombres: str, apellidos: str) -> bool:
    for a1, a2 in _splits(apellidos.split(), 2):
        if curp[:4] == curp_name_letters(nombres, a1, a2) and curp[13:16] == curp_consonants(nombres, a1, a2):
            return True
    return False


# ---------------------------------------------------------------------------
# Clave de elector INE
# ---------------------------------------------------------------------------
def clave_elector_letters(nombres: str, ap1: str, ap2: str | None) -> str:
    """Decisión documentada: por cada uno de (1er apellido, 2o apellido, nombre):
    inicial + primera consonante interna (Ñ -> X, faltante -> X)."""
    out = ""
    for text, given in ((ap1, False), (ap2 or "", False), (nombres, True)):
        w = _significant_word(text, given)
        out += (_initial(w) + _internal_consonant(w)) if w else "XX"
    return out


def build_clave_elector(nombres: str, ap1: str, ap2: str | None, birth: date,
                        entidad_num: int, sexo: str, homoclave: str) -> str:
    if not re.fullmatch(r"\d{3}", homoclave):
        raise ValueError("homoclave de clave de elector: 3 dígitos")
    return (clave_elector_letters(nombres, ap1, ap2) + birth.strftime("%y%m%d")
            + f"{entidad_num:02d}" + sexo + homoclave)


def validate_clave_elector(clave: str, *, nombres: str | None = None, ap1: str | None = None,
                           ap2: str | None = None, birth: date | None = None,
                           sexo: str | None = None, entidad_num: int | None = None) -> list[str]:
    if not isinstance(clave, str) or not CLAVE_ELECTOR_RE.match(clave):
        return [f"clave de elector {clave!r} no cumple ^[A-Z]{{6}}\\d{{8}}[HM]\\d{{3}}$"]
    errs = []
    try:
        date(2000 + int(clave[6:8]), int(clave[8:10]), int(clave[10:12]))
    except ValueError:
        errs.append(f"clave {clave}: fecha inválida")
    if int(clave[12:14]) not in BY_NUM:
        errs.append(f"clave {clave}: entidad {clave[12:14]} inexistente")
    if ap1 is not None and nombres is not None and clave[:6] != clave_elector_letters(nombres, ap1, ap2):
        errs.append(f"clave {clave}: letras no corresponden al nombre")
    if birth is not None and clave[6:12] != birth.strftime("%y%m%d"):
        errs.append(f"clave {clave}: fecha no corresponde a {birth}")
    if entidad_num is not None and clave[12:14] != f"{entidad_num:02d}":
        errs.append(f"clave {clave}: entidad no corresponde a {entidad_num:02d}")
    if sexo is not None and clave[14] != sexo:
        errs.append(f"clave {clave}: sexo no corresponde a {sexo}")
    return errs


# ---------------------------------------------------------------------------
# MRZ ICAO 9303
# ---------------------------------------------------------------------------
def _mrz_value(c: str) -> int:
    if c.isdigit():
        return int(c)
    if "A" <= c <= "Z":
        return ord(c) - ord("A") + 10
    if c == "<":
        return 0
    raise ValueError(f"carácter MRZ inválido: {c!r}")


def mrz_check_digit(s: str) -> str:
    """Dígito verificador ICAO 9303 (pesos 7, 3, 1 cíclicos, módulo 10)."""
    weights = (7, 3, 1)
    return str(sum(_mrz_value(c) * weights[i % 3] for i, c in enumerate(s)) % 10)


def mrz_name_field(primary: str, secondary: str, length: int) -> str:
    """`PRIMARIO<<SECUNDARIO` transliterado, relleno con '<' hasta `length`.
    Truncado ICAO: primero se recorta el identificador secundario (manteniendo al
    menos su inicial); si no basta, se recorta el primario."""
    p = icao_transliterate(primary)
    s = icao_transliterate(secondary)
    full = p + ("<<" + s if s else "")
    if len(full) <= length:
        return full.ljust(length, "<")
    avail = length - len(p) - 2
    if avail >= 1:
        return (p + "<<" + s[:avail]).ljust(length, "<")
    return (p[: length - 3] + "<<" + s[:1]).ljust(length, "<")[:length]


def ine_sex_to_mrz(sexo: str) -> str:
    """Sexo mexicano (H/M) -> ICAO (M/F)."""
    return {"H": "M", "M": "F"}[sexo]


_MRZ_CHARS = re.compile(r"^[A-Z0-9<]+$")


def build_td1_ine(*, cic: str, ocr: str, birth: date, sexo: str, vigencia_year: int,
                  ap1: str, ap2: str, nombres: str) -> list[str]:
    """TD1 (3x30) de la credencial INE. sexo en formato INE (H/M)."""
    l1 = "IDMEX" + cic + mrz_check_digit(cic) + "<<" + ocr
    dob = birth.strftime("%y%m%d")
    exp = f"{vigencia_year % 100:02d}1231"
    l2_body = dob + mrz_check_digit(dob) + ine_sex_to_mrz(sexo) + exp + mrz_check_digit(exp) + "MEX" + "<" * 11
    composite = mrz_check_digit(l1[5:30] + l2_body[0:7] + l2_body[8:15] + l2_body[18:29])
    l2 = l2_body + composite
    l3 = mrz_name_field(f"{ap1} {ap2}", nombres, 30)
    return [l1, l2, l3]


def validate_td1(lines: Sequence[str]) -> list[str]:
    """Estructura y dígitos verificadores de una MRZ TD1 (formato INE)."""
    if not isinstance(lines, (list, tuple)) or len(lines) != 3:
        return ["TD1 requiere 3 líneas"]
    errs = []
    for i, ln in enumerate(lines):
        if not isinstance(ln, str) or len(ln) != 30 or not _MRZ_CHARS.match(ln):
            errs.append(f"TD1 línea {i + 1} inválida (30 chars A-Z0-9<): {ln!r}")
    if errs:
        return errs
    l1, l2, l3 = lines
    if not l1.startswith("ID"):
        errs.append("TD1: tipo de documento debe iniciar con 'ID'")
    if l1[2:5] != "MEX":
        errs.append("TD1: estado emisor debe ser MEX")
    if mrz_check_digit(l1[5:14]) != l1[14]:
        errs.append("TD1: dígito verificador del número de documento incorrecto")
    if mrz_check_digit(l2[0:6]) != l2[6]:
        errs.append("TD1: dígito verificador de fecha de nacimiento incorrecto")
    if l2[7] not in "MFX<":
        errs.append("TD1: sexo inválido")
    if mrz_check_digit(l2[8:14]) != l2[14]:
        errs.append("TD1: dígito verificador de vigencia incorrecto")
    if l2[15:18] != "MEX":
        errs.append("TD1: nacionalidad debe ser MEX")
    if mrz_check_digit(l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]) != l2[29]:
        errs.append("TD1: dígito verificador compuesto incorrecto")
    if "<<" not in l3 or not re.match(r"^[A-Z]", l3):
        errs.append("TD1: línea 3 de nombre mal formada")
    return errs


def build_td3_passport(*, numero: str, birth: date, sexo: str, expiry: date,
                       apellidos: str, nombres: str, optional: str = "") -> list[str]:
    """TD3 (2x44). sexo ICAO (F/M/X). `optional` (máx. 14) = número personal; vacío => '<'."""
    l1 = ("P<MEX" + mrz_name_field(apellidos, nombres, 39))
    doc = numero.ljust(9, "<")
    dob = birth.strftime("%y%m%d")
    exp = expiry.strftime("%y%m%d")
    opt = icao_transliterate(optional)[:14].ljust(14, "<")
    # ICAO: si el campo opcional está vacío su dígito puede ser '<' o 0; usamos '0'.
    opt_cd = mrz_check_digit(opt)
    body = doc + mrz_check_digit(doc) + "MEX" + dob + mrz_check_digit(dob) + sexo + exp + mrz_check_digit(exp) + opt + opt_cd
    composite = mrz_check_digit(body[0:10] + body[13:20] + body[21:43])
    return [l1, body + composite]


def validate_td3(lines: Sequence[str]) -> list[str]:
    if not isinstance(lines, (list, tuple)) or len(lines) != 2:
        return ["TD3 requiere 2 líneas"]
    errs = []
    for i, ln in enumerate(lines):
        if not isinstance(ln, str) or len(ln) != 44 or not _MRZ_CHARS.match(ln):
            errs.append(f"TD3 línea {i + 1} inválida (44 chars A-Z0-9<): {ln!r}")
    if errs:
        return errs
    l1, l2 = lines
    if l1[0] != "P":
        errs.append("TD3: tipo de documento debe ser P")
    if l1[2:5] != "MEX":
        errs.append("TD3: estado emisor debe ser MEX")
    if mrz_check_digit(l2[0:9]) != l2[9]:
        errs.append("TD3: dígito verificador del número de pasaporte incorrecto")
    if l2[10:13] != "MEX":
        errs.append("TD3: nacionalidad debe ser MEX")
    if mrz_check_digit(l2[13:19]) != l2[19]:
        errs.append("TD3: dígito verificador de fecha de nacimiento incorrecto")
    if l2[20] not in "MFX<":
        errs.append("TD3: sexo inválido")
    if mrz_check_digit(l2[21:27]) != l2[27]:
        errs.append("TD3: dígito verificador de caducidad incorrecto")
    opt_ok = mrz_check_digit(l2[28:42]) == l2[42] or (set(l2[28:42]) == {"<"} and l2[42] in "<0")
    if not opt_ok:
        errs.append("TD3: dígito verificador del dato opcional incorrecto")
    if mrz_check_digit(l2[0:10] + l2[13:20] + l2[21:43]) != l2[43]:
        errs.append("TD3: dígito verificador compuesto incorrecto")
    return errs


# ---------------------------------------------------------------------------
# Validación de registros completos (formato + coherencia cruzada)
# ---------------------------------------------------------------------------
def _expected_fields(doc_type: DocType, face: Face, variant: Variant):
    return [
        f for f in CATALOG[doc_type][face]
        if f.in_gt and f.type != FieldType.STATIC and (f.variants is None or variant in f.variants)
    ]


_LIST_TYPES = {FieldType.MULTILINE, FieldType.MRZ}


def validate_record_format(doc_type: DocType, record: dict, variant: Variant | None = None) -> list[str]:
    """Campos exactos del catálogo, tipo de valor y regex (`fmt` que empieza con '^')."""
    doc_type = DocType(doc_type)
    variant = Variant(variant) if variant else default_variant(doc_type)
    errs: list[str] = []
    if set(record) != set(CATALOG[doc_type]):
        errs.append(f"caras {sorted(map(str, record))} != catálogo")
        return errs
    for face in CATALOG[doc_type]:
        fields = _expected_fields(doc_type, face, variant)
        values = record[face]
        expected = {f.name for f in fields}
        if set(values) != expected:
            errs.append(f"{face.value}: campos sobrantes {set(values) - expected}, faltantes {expected - set(values)}")
        for f in fields:
            if f.name not in values:
                continue
            v = values[f.name]
            if f.type in _LIST_TYPES:
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    errs.append(f"{f.name}: se esperaba list[str]")
                    continue
            elif not isinstance(v, str):
                errs.append(f"{f.name}: se esperaba str")
                continue
            if f.fmt.startswith("^") and isinstance(v, str) and not re.match(f.fmt, v):
                errs.append(f"{f.name}={v!r} no cumple {f.fmt}")
            if f.type == FieldType.PHOTO and not AVATAR_RE.match(v):
                errs.append(f"{f.name}: id de avatar inválido {v!r}")
            if f.type == FieldType.SIGNATURE and not FIRMA_RE.match(v):
                errs.append(f"{f.name}: id de firma inválido {v!r}")
            if f.type == FieldType.QR and "MOCK DATA" not in v:
                errs.append(f"{f.name}: payload QR sin 'MOCK DATA'")
    return errs


def _upper_clean(name: str, v: Any, errs: list[str]) -> None:
    vals = v if isinstance(v, list) else [v]
    for s in vals:
        if s != upper_id(s):
            errs.append(f"{name}: {s!r} no está en mayúsculas sin acentos")


def _check_ine(record: dict, variant: Variant) -> list[str]:
    a, r = record[Face.ANVERSO], record[Face.REVERSO]
    errs: list[str] = []
    ap1, ap2, nombres = a["nombre"]
    for k in ("nombre", "domicilio", "clave_elector", "curp"):
        _upper_clean(k, a[k], errs)
    birth = parse_date(a["fecha_nacimiento"])
    curp = a["curp"]
    errs += validate_curp_person(curp, nombres=nombres, ap1=ap1, ap2=ap2, birth=birth, sexo=a["sexo"])
    ent = BY_CURP.get(curp[11:13])
    errs += validate_clave_elector(a["clave_elector"], nombres=nombres, ap1=ap1, ap2=ap2, birth=birth,
                                   sexo=a["sexo"], entidad_num=ent.num if ent else None)
    emision, vigencia = int(a["emision"]), int(a["vigencia"])
    reg_year = int(a["anio_registro"][:4])
    if vigencia != emision + 10:
        errs.append("vigencia debe ser emisión + 10")
    if not (birth.year + 18 <= reg_year <= emision):
        errs.append("año de registro fuera de rango (mayoría de edad .. emisión)")
    if age_on(birth, date(emision, 12, 31)) < 18:
        errs.append("menor de edad en la emisión")
    if int(a["estado"]) not in BY_NUM:
        errs.append("estado inexistente")
    if not r["ocr"].startswith(a["seccion"]):
        errs.append("OCR debe iniciar con la sección")
    dom = a["domicilio"]
    if len(dom) != 3:
        errs.append("domicilio debe tener 3 líneas")
    elif variant == Variant.NACIONAL:
        m = re.match(r"^(.+), ([A-Z]+)\.$", dom[2])
        if not dom[1].startswith("COL. ") or not re.search(r" \d{5}$", dom[1]):
            errs.append("domicilio línea 2: 'COL. colonia CP'")
        if not m or m.group(2) not in BY_ABREV or BY_ABREV[m.group(2)].num != int(a["estado"]):
            errs.append("domicilio línea 3 no corresponde al estado")
    else:
        paises = {c[2] for c in CIUDADES_EXTRANJERO}
        if dom[2] not in paises:
            errs.append("domicilio extranjero: línea 3 debe ser el país")
    errs += validate_td1(r["mrz"])
    if not errs:
        l1 = r["mrz"][0]
        if l1[5:14] != r["cic"] or l1[15:17] != "<<" or l1[17:30] != r["ocr"]:
            errs.append("MRZ línea 1: cic (5-13) y ocr (17-29) deben coincidir con los campos")
    if not errs:
        expected = build_td1_ine(cic=r["cic"], ocr=r["ocr"], birth=birth, sexo=a["sexo"],
                                 vigencia_year=vigencia, ap1=ap1, ap2=ap2, nombres=nombres)
        if list(r["mrz"]) != expected:
            errs.append("MRZ no corresponde a los campos del anverso")
    if a["foto"] != a["foto_secundaria"]:
        errs.append("foto_secundaria debe ser el mismo avatar que foto")
    return errs


def _check_curp_doc(record: dict) -> list[str]:
    u = record[Face.UNICA]
    errs = validate_curp(u["curp"])
    _upper_clean("nombre", u["nombre"], errs)
    if errs:
        return errs
    curp = u["curp"]
    if not curp_matches_full_name(curp, u["nombre"]):
        errs.append("CURP no corresponde al nombre")
    if u["nombre_destinatario"] != u["nombre"]:
        errs.append("nombre_destinatario != nombre")
    if u["codigo_barras_texto"] != u["codigo_barras"]:
        errs.append("codigo_barras_texto != codigo_barras")
    ent = BY_CURP[curp[11:13]]
    if u["entidad_registro"] != ent.nombre:
        errs.append("entidad_registro no corresponde a la entidad de la CURP")
    birth = curp_birth_date(curp)
    insc = parse_date(u["fecha_inscripcion"])
    if insc < birth:
        errs.append("fecha_inscripcion anterior al nacimiento")
    m = re.match(r"^Ciudad de México, a (\d{1,2}) de ([a-z]+) de (\d{4})$", u["lugar_fecha"])
    if not m:
        errs.append("lugar_fecha mal formado")
    else:
        expedida = date(int(m.group(3)), MESES.index(m.group(2)) + 1, int(m.group(1)))
        if expedida < insc:
            errs.append("constancia expedida antes de la inscripción")
    if curp not in u["qr_datos"]:
        errs.append("qr_datos no contiene la CURP")
    for c in u["curps_asociadas"]:
        if validate_curp(c) or c == curp or c[:16] != curp[:16]:
            errs.append(f"CURP asociada incoherente: {c}")
    if len(u["curps_asociadas"]) > 2:
        errs.append("máximo 2 CURPs asociadas")
    return errs


ACTA_2D_MIN, ACTA_2D_MAX = 300, 600
ACTA_2D_TAG = "ACTA2D"


def acta_2d_core(u: dict) -> list[str]:
    """Campos (antes del bloque base64) del payload de `codigo_2d` del Acta, derivados
    de los demás campos del registro. Fuente única para el generador y el validador."""
    return [
        "MOCK DATA", ACTA_2D_TAG, u["identificador_electronico"], u["curp"],
        u["reg_primer_apellido"], u["reg_segundo_apellido"], u["reg_nombres"],
        u["reg_sexo"], u["reg_fecha_nacimiento"], u["reg_lugar_nacimiento"],
        u["entidad_registro"], u["municipio_registro"], u["oficialia"], u["libro"],
        u["numero_acta"], u["fecha_registro"], u["p1_curp"], u["p2_curp"],
    ]


def check_codigo_2d(u: dict) -> list[str]:
    """Coherencia de `codigo_2d` con el resto del Acta (y distinto del `qr` pequeño)."""
    v = u["codigo_2d"]
    errs: list[str] = []
    if not v.startswith("MOCK DATA|"):
        errs.append("codigo_2d debe empezar por 'MOCK DATA|'")
    if not (ACTA_2D_MIN <= len(v) <= ACTA_2D_MAX):
        errs.append(f"codigo_2d: longitud {len(v)} fuera de {ACTA_2D_MIN}..{ACTA_2D_MAX}")
    core = acta_2d_core(u)
    parts = v.split("|")
    if parts[:len(core)] != core or len(parts) != len(core) + 1:
        errs.append("codigo_2d no corresponde a los campos del acta")
        return errs
    try:
        if len(base64.b64decode(parts[-1], validate=True)) < 32:
            errs.append("codigo_2d: bloque base64 demasiado corto")
    except (binascii.Error, ValueError):
        errs.append("codigo_2d: bloque final no es base64")
    if v == u["qr"]:
        errs.append("codigo_2d debe ser distinto de qr")
    return errs


def _check_acta(record: dict) -> list[str]:
    u = record[Face.UNICA]
    errs: list[str] = []
    for k in ("reg_nombres", "reg_primer_apellido", "reg_segundo_apellido", "p1_nombres",
              "p1_primer_apellido", "p1_segundo_apellido", "p2_nombres", "p2_primer_apellido",
              "p2_segundo_apellido", "nombre_director"):
        _upper_clean(k, u[k], errs)
    birth = parse_date(u["reg_fecha_nacimiento"])
    sexo = {"HOMBRE": "H", "MUJER": "M"}.get(u["reg_sexo"])
    if sexo is None:
        errs.append("reg_sexo debe ser HOMBRE|MUJER")
        return errs
    errs += validate_curp_person(u["curp"], nombres=u["reg_nombres"], ap1=u["reg_primer_apellido"],
                                 ap2=u["reg_segundo_apellido"], birth=birth, sexo=sexo)
    ent = BY_CURP.get(u["curp"][11:13])
    if ent is None or u["reg_lugar_nacimiento"] != ent.nombre:
        errs.append("reg_lugar_nacimiento no corresponde a la entidad de la CURP")
    for p, sx in (("p1", "H"), ("p2", "M")):
        pc = u[f"{p}_curp"]
        e = validate_curp(pc)
        if e:
            errs += e
            continue
        pb = curp_birth_date(pc)
        errs += validate_curp_person(pc, nombres=u[f"{p}_nombres"], ap1=u[f"{p}_primer_apellido"],
                                     ap2=u[f"{p}_segundo_apellido"], birth=pb, sexo=sx)
        if age_on(pb, birth) < 15:
            errs.append(f"{p}: progenitor debe ser al menos 15 años mayor")
        mexican = u[f"{p}_nacionalidad"] == "MEXICANA"
        if mexican == (pc[11:13] == "NE"):
            errs.append(f"{p}: nacionalidad incoherente con entidad de la CURP")
    if u["reg_primer_apellido"] != u["p1_primer_apellido"] or u["reg_segundo_apellido"] != u["p2_primer_apellido"]:
        errs.append("apellidos del registrado no corresponden a los progenitores")
    freg = parse_date(u["fecha_registro"])
    if freg < birth:
        errs.append("fecha_registro anterior al nacimiento")
    ide = u["identificador_electronico"]
    reg_ent = BY_NOMBRE.get(u["entidad_registro"])
    if reg_ent is None:
        errs.append("entidad_registro inexistente")
    elif ide[0:2] != f"{reg_ent.num:02d}":
        errs.append("identificador_electronico: entidad")
    if ide[5:9] != u["oficialia"].zfill(4) or ide[9:13] != f"{freg.year:04d}" or ide[13:18] != u["numero_acta"].zfill(5):
        errs.append("identificador_electronico no corresponde a oficialía/año/acta")
    if u["identificador_barcode"] != ide:
        errs.append("identificador_barcode != identificador_electronico")
    if u["codigo_verificacion_barcode"] != u["codigo_verificacion"]:
        errs.append("codigo_verificacion_barcode != codigo_verificacion")
    if u["nombre_director"] not in " ".join(u["certificacion"]):
        errs.append("certificación no menciona al director")
    try:
        base64.b64decode("".join(u["firma_electronica"]), validate=True)
    except (binascii.Error, ValueError):
        errs.append("firma_electronica no es base64")
    errs += check_codigo_2d(u)
    return errs


def _check_pasaporte(record: dict) -> list[str]:
    u = record[Face.UNICA]
    errs: list[str] = []
    for k in ("apellidos", "nombres", "lugar_nacimiento"):
        _upper_clean(k, u[k], errs)
    curp = u["curp"]
    errs += validate_curp(curp)
    if errs:
        return errs
    birth = parse_date(u["fecha_nacimiento"], "spaced")
    exp = parse_date(u["fecha_expedicion"], "spaced")
    cad = parse_date(u["fecha_caducidad"], "spaced")
    if not curp_matches_split_apellidos(curp, u["nombres"], u["apellidos"]):
        errs.append("CURP no corresponde a nombres/apellidos")
    if curp_birth_date(curp) != birth:
        errs.append("CURP no corresponde a la fecha de nacimiento")
    if ine_sex_to_mrz(curp[10]) != u["sexo"]:
        errs.append("CURP no corresponde al sexo")
    if BY_CURP[curp[11:13]].nombre != u["lugar_nacimiento"]:
        errs.append("lugar_nacimiento no corresponde a la entidad de la CURP")
    if not (birth < exp < cad):
        errs.append("fechas no ordenadas: nacimiento < expedición < caducidad")
    if cad not in {add_years(exp, n) for n in (3, 6, 10)}:
        errs.append("caducidad debe ser expedición + 3/6/10 años")
    errs += validate_td3(u["mrz"])
    if not errs:
        expected = build_td3_passport(numero=u["numero_pasaporte"], birth=birth, sexo=u["sexo"],
                                      expiry=cad, apellidos=u["apellidos"], nombres=u["nombres"])
        if list(u["mrz"]) != expected:
            errs.append("MRZ no corresponde a los campos")
    if u["foto"] != u["foto_secundaria"]:
        errs.append("foto_secundaria debe ser el mismo avatar que foto")
    return errs


def validate_record(doc_type: DocType, record: dict, variant: Variant | None = None) -> list[str]:
    """Formato (catálogo) + coherencia cruzada. Lista vacía = registro válido."""
    doc_type = DocType(doc_type)
    variant = Variant(variant) if variant else default_variant(doc_type)
    errs = validate_record_format(doc_type, record, variant)
    if errs:
        return errs
    try:
        if doc_type == DocType.INE_2016:
            return _check_ine(record, variant)
        if doc_type == DocType.CURP_2019:
            return _check_curp_doc(record)
        if doc_type == DocType.ACTA_NACIMIENTO_2015:
            return _check_acta(record)
        return _check_pasaporte(record)
    except (KeyError, ValueError, IndexError) as e:  # datos imposibles de interpretar
        return [f"registro ininterpretable: {type(e).__name__}: {e}"]
