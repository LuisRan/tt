"""Generación de registros sintéticos coherentes (Faker es_MX + RNG sembrado).

Nunca se usan datos de personas reales: nombres, calles y ciudades salen de
Faker; claves (CURP, clave de elector, MRZ) se construyen con las reglas de
`validators` a partir de esos datos inventados.
"""
from __future__ import annotations

import base64
import hashlib
import random
import string
import textwrap
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Union

from faker import Faker

from contracts import DocType, Face, Variant, VARIANTS, default_variant

from .catalogs import (
    CALLES_EXTRANJERO,
    CIUDADES_EXTRANJERO,
    ENTIDADES,
    MESES,
    NACIDO_EXTRANJERO,
    NACIONALIDADES_EXTRANJERAS,
    SUFIJOS_CALLE,
    Entidad,
)
from .text import upper_id
from .validators import (
    acta_2d_core,
    add_years,
    age_on,
    build_clave_elector,
    build_curp,
    build_td1_ine,
    build_td3_passport,
    curp_check_digit,
    format_date,
    ine_sex_to_mrz,
)

Value = Union[str, list[str]]
Record = dict[Face, dict[str, Value]]

FAKER_LOCALE = "es_MX"
# Fecha "hoy" fija para que el generador no dependa del reloj (reproducibilidad).
REFERENCE_DATE = date(2026, 9, 30)

_FAKER: Faker | None = None


def _faker() -> Faker:
    global _FAKER
    if _FAKER is None:
        _FAKER = Faker(FAKER_LOCALE)
    return _FAKER


def sample_key(doc_type: DocType, variant: Variant, seed: int, index: int) -> str:
    return f"{int(seed)}:{doc_type.value}:{variant.value}:{int(index)}"


@dataclass
class Ctx:
    rng: random.Random
    fake: Faker

    def digits(self, n: int) -> str:
        return "".join(self.rng.choice(string.digits) for _ in range(n))

    def hex16(self) -> str:
        return f"{self.rng.getrandbits(64):016x}"

    def date_between(self, start: date, end: date) -> date:
        if end < start:
            raise ValueError(f"rango de fechas vacío {start}..{end}")
        return start + timedelta(days=self.rng.randint(0, (end - start).days))

    def chance(self, p: float) -> bool:
        return self.rng.random() < p


def make_ctx(doc_type: DocType, variant: Variant, seed: int, index: int) -> Ctx:
    # random.Random(str) usa SHA-512 del texto: estable entre procesos (no depende de hash()).
    rng = random.Random(sample_key(doc_type, variant, seed, index))
    fake = _faker()
    fake.seed_instance(rng.getrandbits(64))
    return Ctx(rng, fake)


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------
@dataclass
class Person:
    nombres: str
    ap1: str
    ap2: str
    sexo: str  # H | M
    birth: date
    entidad: Entidad
    curp: str

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombres} {self.ap1} {self.ap2}"


def _first_name(ctx: Ctx, sexo: str) -> str:
    f = ctx.fake.first_name_male if sexo == "H" else ctx.fake.first_name_female
    name = upper_id(f())
    if " " not in name and ctx.chance(0.2):
        second = upper_id(f())
        if second != name:
            name = f"{name} {second}"
    return name


def _last_name(ctx: Ctx) -> str:
    return upper_id(ctx.fake.last_name())


def faker_full_name(ctx: Ctx) -> str:
    """Nombre de funcionario/firmante (NOMBRE APELLIDO APELLIDO), siempre Faker."""
    return f"{_first_name(ctx, ctx.rng.choice('HM'))} {_last_name(ctx)} {_last_name(ctx)}"


def _homoclave_curp(ctx: Ctx, birth: date, exclude: set[str] = frozenset()) -> str:
    pool = string.digits if birth.year < 2000 else string.ascii_uppercase
    return ctx.rng.choice([c for c in pool if c not in exclude])


def new_person(ctx: Ctx, *, birth: date, sexo: str | None = None, entidad: Entidad | None = None,
               ap1: str | None = None, ap2: str | None = None) -> Person:
    sexo = sexo or ctx.rng.choice("HM")
    nombres = _first_name(ctx, sexo)
    ap1 = ap1 or _last_name(ctx)
    ap2 = ap2 or _last_name(ctx)
    entidad = entidad or ctx.rng.choice(ENTIDADES)
    curp = build_curp(nombres, ap1, ap2, birth, sexo, entidad.curp, _homoclave_curp(ctx, birth))
    return Person(nombres, ap1, ap2, sexo, birth, entidad, curp)


def avatar_id(ctx: Ctx) -> str:
    return f"avatar:{ctx.hex16()}"


def firma_id(ctx: Ctx) -> str:
    return f"firma:{ctx.hex16()}"


def _fecha_larga(d: date) -> str:
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


# ---------------------------------------------------------------------------
# INE 2016
# ---------------------------------------------------------------------------
def short_text(ctx: Ctx, make, max_len: int, tries: int = 12) -> str:
    """Valor Faker (mayúsculas sin acentos) de a lo sumo `max_len` caracteres:
    reintenta y, si no lo logra, recorta por palabras."""
    for _ in range(tries):
        v = upper_id(make())
        if len(v) <= max_len:
            return v
    words, out = v.split(), ""
    for w in words:
        if len(out) + len(w) + (1 if out else 0) > max_len:
            break
        out = f"{out} {w}".strip()
    return out or v[:max_len]


# Longitud máxima por línea de domicilio en la credencial (ID-1).
INE_LINE_MAX = 38


def _domicilio_nacional(ctx: Ctx, ent: Entidad) -> list[str]:
    num = str(ctx.rng.randint(1, 2999))
    if ctx.chance(0.15):
        num += f" INT {ctx.rng.randint(1, 40)}"
    calle = short_text(ctx, ctx.fake.street_name, INE_LINE_MAX - len(num) - 1)
    cp = f"{ctx.rng.randint(1000, 99999):05d}"
    colonia = short_text(ctx, ctx.fake.city, INE_LINE_MAX - len("COL.  ") - len(cp))
    municipio = short_text(ctx, ctx.fake.city, INE_LINE_MAX - len(f", {ent.abrev}."))
    return [f"{calle} {num}", f"COL. {colonia} {cp}", f"{municipio}, {ent.abrev}."]


def _domicilio_extranjero(ctx: Ctx) -> list[str]:
    ciudad, estado, pais, cp_pref = ctx.rng.choice(CIUDADES_EXTRANJERO)
    num = ctx.rng.randint(100, 29999)
    if pais == "ESPAÑA":
        l1 = f"CALLE {_last_name(ctx)} {num % 300 + 1}"
    else:
        l1 = f"{num} {ctx.rng.choice(CALLES_EXTRANJERO)} {ctx.rng.choice(SUFIJOS_CALLE)}"
        if ctx.chance(0.3):
            l1 += f" APT {ctx.rng.randint(1, 450)}"
    if cp_pref == "CA":
        L = string.ascii_uppercase
        r = ctx.rng
        cp = f"{r.choice(L)}{r.randint(0, 9)}{r.choice(L)} {r.randint(0, 9)}{r.choice(L)}{r.randint(0, 9)}"
    else:
        cp = cp_pref + ctx.digits(2)
    return [l1, f"{ciudad}, {estado} {cp}", pais]


def gen_ine(ctx: Ctx, variant: Variant) -> Record:
    rng = ctx.rng
    emision = ctx.date_between(date(2016, 2, 1), REFERENCE_DATE)
    birth = ctx.date_between(add_years(emision, -85), add_years(emision, -18))
    p = new_person(ctx, birth=birth)
    reg_year = rng.randint(max(birth.year + 18, 1991), emision.year)
    if variant == Variant.EXTRANJERO:
        dom_ent = p.entidad  # entidad de referencia en México: la de nacimiento
        domicilio = _domicilio_extranjero(ctx)
    else:
        dom_ent = p.entidad if ctx.chance(0.6) else rng.choice(ENTIDADES)
        domicilio = _domicilio_nacional(ctx, dom_ent)
    seccion = f"{rng.randint(1, 9999):04d}"
    clave = build_clave_elector(p.nombres, p.ap1, p.ap2, birth, p.entidad.num, p.sexo, ctx.digits(3))
    cic = ctx.digits(9)
    ocr = seccion + ctx.digits(9)
    vigencia = emision.year + 10
    foto = avatar_id(ctx)
    anverso: dict[str, Value] = {
        "foto": foto,
        "foto_secundaria": foto,  # misma persona: la "foto fantasma" es el mismo avatar
        "nombre": [p.ap1, p.ap2, p.nombres],
        "domicilio": domicilio,
        "clave_elector": clave,
        "curp": p.curp,
        "anio_registro": f"{reg_year} {rng.randint(0, 4):02d}",
        "fecha_nacimiento": format_date(birth),
        "sexo": p.sexo,
        "estado": f"{dom_ent.num:02d}",
        "municipio": f"{rng.randint(1, 125):03d}",
        "seccion": seccion,
        "localidad": f"{rng.randint(1, 400):04d}",
        "emision": f"{emision.year}",
        "vigencia": f"{vigencia}",
    }
    reverso: dict[str, Value] = {
        "codigo_barras": f"MOCK{cic}{ctx.digits(4)}",
        "qr": f"MOCK DATA|INE2016|{variant.value.upper()}|{cic}|{clave}|{ctx.hex16()}",
        "firma": firma_id(ctx),
        "cic": cic,
        "ocr": ocr,
        "mrz": build_td1_ine(cic=cic, ocr=ocr, birth=birth, sexo=p.sexo, vigencia_year=vigencia,
                             ap1=p.ap1, ap2=p.ap2, nombres=p.nombres),
    }
    return {Face.ANVERSO: anverso, Face.REVERSO: reverso}


# ---------------------------------------------------------------------------
# Constancia CURP 2019
# ---------------------------------------------------------------------------
def gen_curp(ctx: Ctx, variant: Variant) -> Record:
    rng = ctx.rng
    expedida = ctx.date_between(date(2019, 1, 1), REFERENCE_DATE)
    birth = ctx.date_between(date(1935, 1, 1), expedida - timedelta(days=30))
    p = new_person(ctx, birth=birth)
    inscripcion = ctx.date_between(max(birth, date(1997, 1, 1)), expedida)
    folio = ctx.digits(8)
    barras = ctx.digits(15)
    n_asoc = rng.choices([0, 1, 2], weights=[80, 15, 5])[0]
    used = {p.curp[16]}
    asociadas = []
    for _ in range(n_asoc):
        h = _homoclave_curp(ctx, birth, used)
        used.add(h)
        base = p.curp[:16] + h
        asociadas.append(base + curp_check_digit(base))
    nombre = p.nombre_completo
    u: dict[str, Value] = {
        "curp": p.curp,
        "nombre": nombre,
        "fecha_inscripcion": format_date(inscripcion),
        "folio": folio,
        "entidad_registro": p.entidad.nombre,
        "qr_datos": (f"MOCK DATA|{p.curp}|{p.ap1}|{p.ap2}|{p.nombres}|{p.sexo}|"
                     f"{format_date(birth)}|{p.entidad.curp}"),
        "codigo_barras": barras,
        "codigo_barras_texto": barras,
        "estatus_curp": ("CURP Certificada: verificada con el Registro Civil" if ctx.chance(0.9)
                         else "CURP no certificada: pendiente de verificación con el Registro Civil"),
        "curps_asociadas": asociadas,
        "nombre_destinatario": nombre,
        "lugar_fecha": f"Ciudad de México, a {_fecha_larga(expedida)}",
        "firmante": faker_full_name(ctx),
        "qr_constancia": f"MOCK DATA|CONSTANCIA CURP|{p.curp}|FOLIO {folio}|{ctx.hex16()}",
    }
    return {Face.UNICA: u}


# ---------------------------------------------------------------------------
# Acta de nacimiento 2015
# ---------------------------------------------------------------------------
def _progenitor(ctx: Ctx, child_birth: date, sexo: str, max_age: int) -> tuple[Person, str]:
    # La CURP solo distingue siglos 1900/2000 (posición 17): nacimientos >= 1900.
    oldest = max(add_years(child_birth, -max_age), date(1900, 1, 1))
    birth = ctx.date_between(oldest, add_years(child_birth, -16))
    if ctx.chance(0.05):
        nac, ent = ctx.rng.choice(NACIONALIDADES_EXTRANJERAS), NACIDO_EXTRANJERO
    else:
        nac, ent = "MEXICANA", ctx.rng.choice(ENTIDADES)
    return new_person(ctx, birth=birth, sexo=sexo, entidad=ent), nac


def _wrap(text: str, width: int = 95) -> list[str]:
    return textwrap.wrap(text, width=width, break_long_words=False)


def gen_acta(ctx: Ctx, variant: Variant) -> Record:
    rng = ctx.rng
    expedida = ctx.date_between(date(2016, 1, 1), REFERENCE_DATE)
    birth = ctx.date_between(date(1935, 1, 1), expedida - timedelta(days=10))
    fecha_registro = min(birth + timedelta(days=rng.randint(0, 730)), expedida)
    padre, nac1 = _progenitor(ctx, birth, "H", 55)
    madre, nac2 = _progenitor(ctx, birth, "M", 45)
    reg = new_person(ctx, birth=birth, ap1=padre.ap1, ap2=madre.ap1)
    ent_reg = reg.entidad if ctx.chance(0.9) else rng.choice(ENTIDADES)
    municipio = short_text(ctx, ctx.fake.city, 35)
    oficialia = str(rng.randint(1, 150))
    libro = str(rng.randint(1, 40))
    acta = str(rng.randint(1, 99999))
    ident = (f"{ent_reg.num:02d}{rng.randint(1, 125):03d}{int(oficialia):04d}"
             f"{fecha_registro.year:04d}{int(acta):05d}{ctx.digits(2)}")
    director = faker_full_name(ctx)
    verif = ctx.digits(15)
    r = rng.random()
    if r < 0.6:
        anotaciones: list[str] = []
    elif r < 0.85:
        anotaciones = ["SIN ANOTACIONES MARGINALES."]
    else:
        anotaciones = _wrap(
            f"ANOTACION GENERICA DE PRUEBA: CON FECHA {format_date(ctx.date_between(fecha_registro, expedida))} "
            f"SE REALIZO ACLARACION ADMINISTRATIVA DEL ACTA {acta} SIN MODIFICAR LA FILIACION."
        )
    certificacion = _wrap(
        f"EL (LA) C. {director}, DIRECTOR(A) DEL REGISTRO CIVIL DE {ent_reg.nombre}, CERTIFICA QUE "
        f"LA PRESENTE ES REPRODUCCION DE LOS DATOS DEL ACTA {acta}, LIBRO {libro}, OFICIALIA "
        f"{oficialia} DE {municipio}, QUE OBRA EN LA BASE DE DATOS NACIONAL. SE EXPIDE EN "
        f"{municipio}, {ent_reg.nombre}, A {format_date(expedida)}. DOCUMENTO SINTETICO DE PRUEBA."
    )
    sig = base64.b64encode(rng.getrandbits(256 * 8).to_bytes(256, "big")).decode("ascii")
    firma = [sig[i:i + 86] for i in range(0, len(sig), 86)]
    u: dict[str, Value] = {
        "folio_impresion": ctx.digits(8),
        "identificador_electronico": ident,
        "identificador_barcode": ident,
        "curp": reg.curp,
        "numero_certificado_nacimiento": ctx.digits(9),
        "entidad_registro": ent_reg.nombre,
        "municipio_registro": municipio,
        "oficialia": oficialia,
        "fecha_registro": format_date(fecha_registro),
        "libro": libro,
        "numero_acta": acta,
        "reg_nombres": reg.nombres,
        "reg_primer_apellido": reg.ap1,
        "reg_segundo_apellido": reg.ap2,
        "reg_sexo": "HOMBRE" if reg.sexo == "H" else "MUJER",
        "reg_fecha_nacimiento": format_date(birth),
        "reg_lugar_nacimiento": reg.entidad.nombre,
        "p1_nombres": padre.nombres,
        "p1_primer_apellido": padre.ap1,
        "p1_segundo_apellido": padre.ap2,
        "p1_nacionalidad": nac1,
        "p1_curp": padre.curp,
        "p2_nombres": madre.nombres,
        "p2_primer_apellido": madre.ap1,
        "p2_segundo_apellido": madre.ap2,
        "p2_nacionalidad": nac2,
        "p2_curp": madre.curp,
        "anotaciones_marginales": anotaciones,
        "certificacion": certificacion,
        "firma_electronica": firma,
        "nombre_director": director,
        "qr": f"MOCK DATA|ACTA|{ident}|{reg.curp}|{verif}",
        "codigo_verificacion": verif,
        "codigo_verificacion_barcode": verif,
    }
    # Último en consumir RNG: no altera los demás campos. Núcleo = datos del acta;
    # cola = bloque base64 aleatorio sembrado, hasta ~380-520 caracteres en total.
    core = "|".join(acta_2d_core(u)) + "|"
    objetivo = rng.randint(380, 520)
    nbytes = max(((objetivo - len(core)) // 4) * 3, 48)
    u["codigo_2d"] = core + base64.b64encode(rng.getrandbits(nbytes * 8).to_bytes(nbytes, "big")).decode("ascii")
    return {Face.UNICA: u}


# ---------------------------------------------------------------------------
# Pasaporte electrónico 2021
# ---------------------------------------------------------------------------
def gen_pasaporte(ctx: Ctx, variant: Variant) -> Record:
    rng = ctx.rng
    exp = ctx.date_between(date(2021, 10, 1), REFERENCE_DATE)
    if (exp.month, exp.day) == (2, 29):
        exp = exp.replace(day=28)
    birth = ctx.date_between(date(1935, 1, 1), exp - timedelta(days=30))
    p = new_person(ctx, birth=birth)
    years = 3 if age_on(birth, exp) < 18 else rng.choice([3, 6, 10])
    cad = add_years(exp, years)
    numero = rng.choice("GN") + ctx.digits(8)
    sexo = ine_sex_to_mrz(p.sexo)
    apellidos = f"{p.ap1} {p.ap2}"
    oficina = rng.choice(ENTIDADES).nombre
    u: dict[str, Value] = {
        "foto": (foto := avatar_id(ctx)),
        "foto_secundaria": foto,
        "tipo": "P",
        "codigo_pais": "MEX",
        "numero_pasaporte": numero,
        "apellidos": apellidos,
        "nombres": p.nombres,
        "nacionalidad": "MEXICANA",
        "observaciones": "" if ctx.chance(0.8) else rng.choice(["NINGUNA", "SIN OBSERVACIONES"]),
        "fecha_nacimiento": format_date(birth, "spaced"),
        "curp": p.curp,
        "sexo": sexo,
        "lugar_nacimiento": p.entidad.nombre,
        "fecha_expedicion": format_date(exp, "spaced"),
        "autoridad": f"OFICINA DE PASAPORTES {oficina}",
        "fecha_caducidad": format_date(cad, "spaced"),
        "firma_portador": firma_id(ctx),
        "mrz": build_td3_passport(numero=numero, birth=birth, sexo=sexo, expiry=cad,
                                  apellidos=apellidos, nombres=p.nombres),
    }
    return {Face.UNICA: u}


_GENERATORS = {
    DocType.INE_2016: gen_ine,
    DocType.CURP_2019: gen_curp,
    DocType.ACTA_NACIMIENTO_2015: gen_acta,
    DocType.PASAPORTE_ELECTRONICO_2021: gen_pasaporte,
}


def resolve_variant(doc_type: DocType, variant: Variant | str | None) -> Variant:
    v = default_variant(doc_type) if variant is None else Variant(variant)
    if v not in VARIANTS[doc_type]:
        raise ValueError(f"variante {v.value!r} no válida para {doc_type.value}: {[x.value for x in VARIANTS[doc_type]]}")
    return v


def generate_record(doc_type: DocType | str, seed: int, index: int = 0,
                    variant: Variant | str | None = None) -> Record:
    """Valores sintéticos para todos los campos in_gt (no STATIC) del catálogo.

    Determinista: misma (doc_type, variant, seed, index) => mismo registro, en
    cualquier proceso (no depende de PYTHONHASHSEED ni del reloj).
    """
    doc_type = DocType(doc_type)
    v = resolve_variant(doc_type, variant)
    ctx = make_ctx(doc_type, v, seed, index)
    return _GENERATORS[doc_type](ctx, v)


def record_digest(record: Record) -> str:
    """SHA-256 de la serialización canónica de un registro (útil en tests)."""
    import json

    canon = json.dumps({f.value: vals for f, vals in record.items()}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
