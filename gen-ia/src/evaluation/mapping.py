"""Mapa del ground truth del generador a los campos del OCR (Tabla 25 de la spec).

Solo se mapea lo que se deriva de forma determinista; un campo del OCR sin equivalente
verificable en el GT queda NOT_APPLICABLE (con el motivo en `reason`). El OCR nunca es verdad.

INE (anverso; GT: nombre[3 líneas ap1/ap2/nombres], domicilio[3 líneas], ...)
  nombre_completo   = "nombres ap1 ap2" (alt. PARTIAL: "ap1 ap2 nombres", orden impreso)
  primer_apellido / segundo_apellido / nombres = líneas 0 / 1 / 2 de `nombre`
  curp, clave_elector, fecha_nacimiento, seccion, localidad, estado, municipio = directos
  sexo = `sexo` (H/M; convención MX)         año_emision = `emision`   año_vigencia = `vigencia`
  domicilio = las 3 líneas unidas con espacio (no se separa calle/colonia/municipio);
  se compara ignorando `, . ;` (K.ADDRESS)
  estado/municipio: el GT trae el CÓDIGO numérico (p. ej. "29", "080"), se compara como código.
ACTA (GT: reg_* = registrado, p1_* / p2_* = progenitores)
  nombre_completo = "reg_nombres reg_primer_apellido reg_segundo_apellido"
  primer_apellido, segundo_apellido, nombres = reg_*;  fecha_nacimiento = reg_fecha_nacimiento
  sexo = reg_sexo;  lugar_nacimiento = reg_lugar_nacimiento
  curp, folio_impresion, identificador_electronico, numero_acta, oficialia, libro,
  entidad_registro, municipio_registro, fecha_registro = directos
  nombre_padre/curp_padre y nombre_madre/curp_madre: p1/p2 NO indican parentesco; se asigna
  por el sexo codificado en la CURP del progenitor (H=padre, M=madre). Si es ambiguo
  (ambos H, ambos M, X, CURP ilegible) => NOT_APPLICABLE.
PASAPORTE (GT: apellidos "AP1 AP2", nombres, mrz[2 líneas])
  nombre_completo = "nombres apellidos" (alt. PARTIAL: "apellidos nombres")
  primer_apellido = `apellidos` si tiene 1 palabra, la primera si tiene 2; con >=3 palabras
  es ambiguo (apellidos compuestos) => NOT_APPLICABLE
  curp, fecha_nacimiento ("DD MM AAAA"), lugar_nacimiento, numero_pasaporte, nacionalidad,
  fecha_expedicion, fecha_caducidad = directos;  sexo = `sexo` (convención ICAO: M=masculino)
  tipo_pasaporte = `tipo`;  pais_expedicion = `codigo_pais` (código "MEX")
  mrz_linea1 / mrz_linea2 = líneas 0 / 1 de `mrz`
CURP (GT: nombre "NOMBRES AP1 AP2", curp, fecha_inscripcion, folio, entidad_registro)
  nombre_completo = `nombre`;  curp, fecha_inscripcion, folio, entidad_registro = directos
  fecha_nacimiento y sexo se DERIVAN de la CURP del GT (AAMMDD, siglo por posición 17:
  dígito => 1900s, letra => 2000s; posición 11 = H/M/X).
  nombres / primer_apellido / segundo_apellido => NOT_APPLICABLE (nombre sin separadores).
  codigo_verificacion => NOT_APPLICABLE (la equivalencia con `codigo_barras_texto` no está
  verificada).
Campos del GT con valor vacío => se espera vacío (OCR vacío = EXACT; con valor = EXTRA).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Callable, Optional, Union

from pydantic import Field

from contracts.common import StrictModel
from contracts.evaluation import GEN_TO_OCR_DOCTYPE, OcrDocType
from contracts.ground_truth import GroundTruth

from .normalize import FieldKind as K

# Campos del OCR por tipo, con el tipo de comparación.
OCR_FIELDS: dict[str, dict[str, K]] = {
    "ine": {
        "nombre_completo": K.TEXT, "primer_apellido": K.TEXT, "segundo_apellido": K.TEXT,
        "nombres": K.TEXT, "curp": K.IDENTIFIER, "fecha_nacimiento": K.DATE, "sexo": K.SEX_MX,
        "clave_elector": K.IDENTIFIER, "domicilio": K.ADDRESS, "estado": K.CODE,
        "municipio": K.CODE, "seccion": K.CODE, "localidad": K.CODE,
        "año_emision": K.CODE, "año_vigencia": K.CODE,
    },
    "acta_nacimiento": {
        "nombre_completo": K.TEXT, "primer_apellido": K.TEXT, "segundo_apellido": K.TEXT,
        "nombres": K.TEXT, "curp": K.IDENTIFIER, "fecha_nacimiento": K.DATE, "sexo": K.SEX_MX,
        "folio_impresion": K.IDENTIFIER, "identificador_electronico": K.IDENTIFIER,
        "numero_acta": K.CODE, "oficialia": K.CODE, "libro": K.CODE,
        "entidad_registro": K.TEXT, "municipio_registro": K.TEXT, "fecha_registro": K.DATE,
        "nombre_padre": K.TEXT, "curp_padre": K.IDENTIFIER, "nombre_madre": K.TEXT,
        "curp_madre": K.IDENTIFIER, "lugar_nacimiento": K.TEXT,
    },
    "pasaporte": {
        "nombre_completo": K.TEXT, "primer_apellido": K.TEXT, "nombres": K.TEXT,
        "curp": K.IDENTIFIER, "fecha_nacimiento": K.DATE, "sexo": K.SEX_ICAO,
        "lugar_nacimiento": K.TEXT, "numero_pasaporte": K.IDENTIFIER, "tipo_pasaporte": K.CODE,
        "pais_expedicion": K.CODE, "nacionalidad": K.TEXT, "fecha_expedicion": K.DATE,
        "fecha_caducidad": K.DATE, "mrz_linea1": K.IDENTIFIER, "mrz_linea2": K.IDENTIFIER,
    },
    "curp": {
        "nombre_completo": K.TEXT, "primer_apellido": K.TEXT, "segundo_apellido": K.TEXT,
        "nombres": K.TEXT, "curp": K.IDENTIFIER, "fecha_nacimiento": K.DATE, "sexo": K.SEX_MX,
        "entidad_registro": K.TEXT, "fecha_inscripcion": K.DATE, "folio": K.IDENTIFIER,
        "codigo_verificacion": K.IDENTIFIER,
    },
}

# Campos que define el esquema de salida del OCR por tipo (Pydantic `CamposINE`, `CamposActaNacimiento`,
# `CamposPasaporte`, `CamposCURP` + `CamposBase`). Copiado el 2026-10-25 de
# tt/ia_documentos/domain/schemas/document_schemas.py. Una clave devuelta que esté aquí pero no tenga
# equivalente en el GT es EXTRA_SCHEMA (no es error); fuera de aquí es EXTRA (posible alucinación).
_BASE_SCHEMA = frozenset({
    "nombre_completo", "primer_apellido", "segundo_apellido", "nombres", "curp",
    "fecha_nacimiento", "sexo",
})
OCR_SCHEMA_FIELDS: dict[str, frozenset[str]] = {
    "ine": _BASE_SCHEMA | {
        "clave_elector", "domicilio_calle", "domicilio_colonia", "domicilio_municipio_ciudad",
        "domicilio", "estado", "municipio", "localidad", "seccion", "año_registro",
        "año_emision", "año_vigencia",
    },
    "acta_nacimiento": _BASE_SCHEMA | {
        "folio_impresion", "identificador_electronico", "numero_certificado_nac", "numero_acta",
        "entidad_registro", "municipio_registro", "fecha_registro", "oficialia", "libro",
        "lugar_nacimiento", "nombre_padre", "primer_apellido_padre", "segundo_apellido_padre",
        "nacionalidad_padre", "curp_padre", "nombre_madre", "primer_apellido_madre",
        "segundo_apellido_madre", "nacionalidad_madre", "curp_madre",
    },
    "pasaporte": _BASE_SCHEMA | {
        "numero_pasaporte", "tipo_pasaporte", "pais_expedicion", "nacionalidad",
        "lugar_nacimiento", "fecha_expedicion", "fecha_caducidad", "mrz_linea1", "mrz_linea2",
        "observaciones",
    },
    "curp": _BASE_SCHEMA | {
        "fecha_inscripcion", "folio", "entidad_registro", "curps_asociadas",
        "codigo_verificacion", "fecha_emision",
    },
}

# Criticidad (peso del error): 3 identificadores críticos, 2 identidad/fechas, 1 resto.
_CRIT3 = {
    "curp", "clave_elector", "numero_pasaporte", "mrz_linea1", "mrz_linea2",
    "identificador_electronico", "folio_impresion", "folio", "codigo_verificacion",
    "curp_padre", "curp_madre",
}
_CRIT2 = {
    "nombre_completo", "nombres", "primer_apellido", "segundo_apellido", "sexo",
    "fecha_nacimiento", "fecha_registro", "fecha_expedicion", "fecha_caducidad",
    "fecha_inscripcion", "numero_acta", "nombre_padre", "nombre_madre",
}


def criticality(field: str) -> int:
    if field in _CRIT3:
        return 3
    if field in _CRIT2:
        return 2
    return 1


class ExpectedField(StrictModel):
    name: str
    kind: K
    applicable: bool
    value: Optional[str] = None          # "" => se espera vacío
    alternatives: list[str] = Field(default_factory=list)  # órdenes alternos => PARTIAL
    reason: Optional[str] = None         # motivo de NOT_APPLICABLE


_Derived = Union[str, tuple[str, list[str]], None]


def _flatten(gt: GroundTruth) -> dict[str, Union[str, list[str], None]]:
    out: dict[str, Union[str, list[str], None]] = {}
    for page in gt.pages:
        for f in page.fields:
            out.setdefault(f.name, f.value)
    return out


def _s(g: dict, key: str) -> Optional[str]:
    v = g.get(key)
    if isinstance(v, str):
        return v.strip()
    return None


def _lines(g: dict, key: str, n: Optional[int] = None) -> Optional[list[str]]:
    v = g.get(key)
    if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
        return None
    if n is not None and len(v) != n:
        return None
    return [x.strip() for x in v]


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


_CURP_RE = re.compile(r"^[A-Z][AEIOUX][A-Z]{2}(\d{2})(\d{2})(\d{2})([HMX])[A-Z]{5}([A-Z\d])\d$")


def curp_birthdate(curp: str) -> Optional[str]:
    m = _CURP_RE.match(curp.strip().upper())
    if not m:
        return None
    yy, mm, dd, _sx, hom = m.groups()
    year = (2000 if hom.isalpha() else 1900) + int(yy)
    try:
        date(year, int(mm), int(dd))
    except ValueError:
        return None
    return f"{int(dd):02d}/{int(mm):02d}/{year}"


def curp_sex(curp: Optional[str]) -> Optional[str]:
    if not curp:
        return None
    m = _CURP_RE.match(curp.strip().upper())
    return m.group(4) if m else None


NA = "__na__"


def _na(reason: str) -> tuple[str, str]:
    return (NA, reason)


def _ine(g: dict) -> dict[str, object]:
    out: dict[str, object] = {}
    nm = _lines(g, "nombre", 3)
    dom = _lines(g, "domicilio")
    if nm is None:
        for k in ("nombre_completo", "primer_apellido", "segundo_apellido", "nombres"):
            out[k] = _na("GT `nombre` sin 3 líneas")
    else:
        ap1, ap2, nombres = nm
        out["nombre_completo"] = (_join(nombres, ap1, ap2), [_join(ap1, ap2, nombres)])
        out["primer_apellido"], out["segundo_apellido"], out["nombres"] = ap1, ap2, nombres
    for k in ("curp", "clave_elector", "fecha_nacimiento", "sexo", "estado", "municipio", "seccion", "localidad"):
        out[k] = _s(g, k)
    out["año_emision"] = _s(g, "emision")
    out["año_vigencia"] = _s(g, "vigencia")
    out["domicilio"] = _join(*dom) if dom is not None else None
    return out


def _acta(g: dict) -> dict[str, object]:
    out: dict[str, object] = {}
    n, a1, a2 = _s(g, "reg_nombres"), _s(g, "reg_primer_apellido"), _s(g, "reg_segundo_apellido")
    if n is None or a1 is None or a2 is None:
        out["nombre_completo"] = _na("GT sin reg_nombres/apellidos")
    else:
        out["nombre_completo"] = (_join(n, a1, a2), [_join(a1, a2, n)])
    out["primer_apellido"], out["segundo_apellido"], out["nombres"] = a1, a2, n
    out["fecha_nacimiento"] = _s(g, "reg_fecha_nacimiento")
    out["sexo"] = _s(g, "reg_sexo")
    out["lugar_nacimiento"] = _s(g, "reg_lugar_nacimiento")
    for k in ("curp", "folio_impresion", "identificador_electronico", "numero_acta", "oficialia",
              "libro", "entidad_registro", "municipio_registro", "fecha_registro"):
        out[k] = _s(g, k)
    parents = {}
    for p in ("p1", "p2"):
        pc = _s(g, f"{p}_curp")
        parents[p] = (curp_sex(pc), pc, _join(*(_s(g, f"{p}_{x}") or "" for x in
                                               ("nombres", "primer_apellido", "segundo_apellido"))))
    sexes = {p: v[0] for p, v in parents.items()}
    padre = [p for p, sx in sexes.items() if sx == "H"]
    madre = [p for p, sx in sexes.items() if sx == "M"]
    if len(padre) == 1 and len(madre) == 1:
        out["nombre_padre"], out["curp_padre"] = parents[padre[0]][2], parents[padre[0]][1]
        out["nombre_madre"], out["curp_madre"] = parents[madre[0]][2], parents[madre[0]][1]
    else:
        why = "parentesco no determinista (sexo de CURP de p1/p2 no es un H y una M)"
        for k in ("nombre_padre", "curp_padre", "nombre_madre", "curp_madre"):
            out[k] = _na(why)
    return out


def _pasaporte(g: dict) -> dict[str, object]:
    out: dict[str, object] = {}
    ap, nom = _s(g, "apellidos"), _s(g, "nombres")
    if ap is None or nom is None:
        out["nombre_completo"] = _na("GT sin apellidos/nombres")
    else:
        out["nombre_completo"] = (_join(nom, ap), [_join(ap, nom)])
    out["nombres"] = nom
    if ap is None:
        out["primer_apellido"] = _na("GT sin apellidos")
    else:
        toks = ap.split()
        if len(toks) in (1, 2):
            out["primer_apellido"] = toks[0] if toks else None
        else:
            out["primer_apellido"] = _na("apellidos con >=3 palabras: separación ambigua")
    for k in ("curp", "fecha_nacimiento", "sexo", "lugar_nacimiento", "numero_pasaporte",
              "nacionalidad", "fecha_expedicion", "fecha_caducidad"):
        out[k] = _s(g, k)
    out["tipo_pasaporte"] = _s(g, "tipo")
    out["pais_expedicion"] = _s(g, "codigo_pais")
    mrz = _lines(g, "mrz", 2)
    if mrz is None:
        out["mrz_linea1"] = out["mrz_linea2"] = _na("GT `mrz` sin 2 líneas")
    else:
        out["mrz_linea1"], out["mrz_linea2"] = mrz
    return out


def _curp(g: dict) -> dict[str, object]:
    out: dict[str, object] = {}
    out["nombre_completo"] = _s(g, "nombre")
    why = "`nombre` del GT sin separadores entre nombres y apellidos"
    for k in ("nombres", "primer_apellido", "segundo_apellido"):
        out[k] = _na(why)
    curp = _s(g, "curp")
    out["curp"] = curp
    bd = curp_birthdate(curp) if curp else None
    out["fecha_nacimiento"] = bd if bd else _na("CURP del GT no permite derivar la fecha")
    sx = curp_sex(curp)
    out["sexo"] = sx if sx else _na("CURP del GT no permite derivar el sexo")
    for k in ("entidad_registro", "fecha_inscripcion", "folio"):
        out[k] = _s(g, k)
    out["codigo_verificacion"] = _na("equivalencia con codigo_barras_texto no verificada")
    return out


_BUILDERS: dict[str, Callable[[dict], dict[str, object]]] = {
    "ine": _ine, "acta_nacimiento": _acta, "pasaporte": _pasaporte, "curp": _curp,
}


def expected_fields(gt: GroundTruth) -> dict[str, ExpectedField]:
    """Campos del OCR esperados para este GT, en orden estable de OCR_FIELDS."""
    ocr_type: OcrDocType = GEN_TO_OCR_DOCTYPE[gt.doc_type]
    g = _flatten(gt)
    derived = _BUILDERS[ocr_type](g)
    out: dict[str, ExpectedField] = {}
    for name, kind in OCR_FIELDS[ocr_type].items():
        d = derived.get(name)
        if isinstance(d, tuple) and d[0] == NA:
            out[name] = ExpectedField(name=name, kind=kind, applicable=False, reason=d[1])
        elif isinstance(d, tuple):
            out[name] = ExpectedField(name=name, kind=kind, applicable=True, value=d[0],
                                      alternatives=[a for a in d[1] if a != d[0]])
        elif d is None:
            out[name] = ExpectedField(name=name, kind=kind, applicable=False,
                                      reason="campo ausente en el GT de este documento/variante")
        else:
            out[name] = ExpectedField(name=name, kind=kind, applicable=True, value=str(d))
    return out
