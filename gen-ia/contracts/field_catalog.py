"""Catálogo canónico de campos por documento y cara.

Es el "nombre común" entre T1 (que les asigna bbox/fuente en layout_spec) y
T2 (que les genera valor). Un layout_spec es válido sólo si contiene
EXACTAMENTE los campos con in_gt=True de este catálogo (puede añadir campos
STATIC/SHAPE decorativos con in_ground_truth=False).

`fmt` documenta el formato del valor que T2 debe generar (regex o descripción).
Todos los valores son sintéticos (Faker es_MX + RNG sembrado).
"""
from __future__ import annotations

from typing import NamedTuple

from .common import DocType, Face, Variant
from .layout_spec import FieldType as T


class CatalogField(NamedTuple):
    name: str
    type: T
    fmt: str
    in_gt: bool = True
    # None => todas las variantes del documento.
    variants: tuple[Variant, ...] | None = None


_EXT = (Variant.EXTRANJERO,)


CURP_RE = r"^[A-Z][AEIOUX][A-Z]{2}\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[HMX](AS|BC|BS|CC|CL|CM|CS|CH|DF|DG|GT|GR|HG|JC|MC|MN|MS|NT|NL|OC|PL|QT|QR|SP|SL|SR|TC|TS|TL|VZ|YN|ZS|NE)[B-DF-HJ-NP-TV-Z]{3}[A-Z\d]\d$"
DATE_DMY = r"^\d{2}/\d{2}/\d{4}$"

CATALOG: dict[DocType, dict[Face, list[CatalogField]]] = {
    DocType.INE_2016: {
        Face.ANVERSO: [
            CatalogField("escudo", T.SHAPE, "figura geométrica sustituta", in_gt=False),
            CatalogField("pais", T.STATIC, "MÉXICO"),
            CatalogField("titulo", T.STATIC, "INSTITUTO NACIONAL ELECTORAL"),
            CatalogField("subtitulo", T.STATIC, "CREDENCIAL PARA VOTAR"),
            CatalogField("foto", T.PHOTO, "avatar sintético id"),
            CatalogField("foto_secundaria", T.PHOTO, "avatar sintético id (sustituye foto fantasma)"),
            CatalogField("nombre", T.MULTILINE, "3 líneas: apellido paterno, apellido materno, nombre(s); mayúsculas"),
            CatalogField("leyenda_extranjero", T.STATIC, "PARA VOTAR DESDE EL EXTRANJERO", variants=_EXT),
            CatalogField("domicilio", T.MULTILINE, "3 líneas, mayúsculas. nacional: calle num / COL. colonia CP / municipio, EDO. extranjero: calle num / ciudad, estado CP / PAÍS"),
            CatalogField("clave_elector", T.TEXT, r"^[A-Z]{6}\d{8}[HM]\d{3}$"),
            CatalogField("curp", T.TEXT, CURP_RE),
            CatalogField("anio_registro", T.TEXT, r"^\d{4} \d{2}$"),
            CatalogField("fecha_nacimiento", T.TEXT, DATE_DMY),
            CatalogField("sexo", T.TEXT, r"^[HM]$"),
            CatalogField("estado", T.TEXT, r"^\d{2}$"),
            CatalogField("municipio", T.TEXT, r"^\d{3}$"),
            CatalogField("seccion", T.TEXT, r"^\d{4}$"),
            CatalogField("localidad", T.TEXT, r"^\d{4}$"),
            CatalogField("emision", T.TEXT, r"^\d{4}$"),
            CatalogField("vigencia", T.TEXT, r"^\d{4}$"),
        ],
        Face.REVERSO: [
            CatalogField("codigo_barras", T.BARCODE, "payload sintético alfanumérico"),
            CatalogField("qr", T.QR, "payload sintético (incluye 'MOCK DATA')"),
            CatalogField("recuadros_voto", T.SHAPE, "rejilla de recuadros vacíos", in_gt=False),
            CatalogField("firma", T.SIGNATURE, "garabato sintético id"),
            CatalogField("huella", T.SHAPE, "óvalo genérico (sin huella)", in_gt=False),
            CatalogField("cic", T.TEXT, r"^\d{9}$"),
            CatalogField("ocr", T.TEXT, r"^\d{13}$"),
            CatalogField("mrz", T.MRZ, "TD1: 3 líneas x 30 chars, dígitos verificadores ICAO 9303 válidos. Línea 1 = IDMEX + cic(9) + dígito + << + ocr(13): cic y ocr DEBEN coincidir con esos sub-rangos (pos. 5-13 y 17-29, base 0)"),
        ],
    },
    DocType.CURP_2019: {
        Face.UNICA: [
            CatalogField("escudo", T.SHAPE, "figura geométrica sustituta", in_gt=False),
            CatalogField("logo_dependencia", T.SHAPE, "rectángulo sustituto del logo", in_gt=False),
            CatalogField("bandera", T.SHAPE, "tres franjas lisas sin escudo", in_gt=False),
            CatalogField("titulo", T.STATIC, "ESTADOS UNIDOS MEXICANOS"),
            CatalogField("subtitulo", T.STATIC, "CONSTANCIA DE LA CLAVE ÚNICA DE REGISTRO DE POBLACIÓN"),
            CatalogField("curp", T.TEXT, CURP_RE),
            CatalogField("nombre", T.TEXT, "NOMBRE(S) APELLIDO1 APELLIDO2; mayúsculas"),
            CatalogField("fecha_inscripcion", T.TEXT, DATE_DMY),
            CatalogField("folio", T.TEXT, r"^\d{8}$"),
            CatalogField("entidad_registro", T.TEXT, "nombre de entidad federativa en mayúsculas"),
            CatalogField("qr_datos", T.QR, "payload sintético con curp y 'MOCK DATA'"),
            CatalogField("codigo_barras", T.BARCODE, r"^\d{15}$"),
            CatalogField("codigo_barras_texto", T.TEXT, r"^\d{15}$"),
            CatalogField("estatus_curp", T.TEXT, "'CURP Certificada: verificada con el Registro Civil' o variante no certificada"),
            CatalogField("curps_asociadas", T.MULTILINE, "0..2 CURPs sintéticas, una por línea"),
            CatalogField("nombre_destinatario", T.TEXT, "igual a nombre"),
            CatalogField("lugar_fecha", T.TEXT, "'Ciudad de México, a D de mes de AAAA'"),
            CatalogField("cuerpo", T.STATIC, "texto ORIGINAL de la carta transcrito de la referencia (partes ilegibles: relleno de longitud similar)"),
            CatalogField("firmante", T.TEXT, "nombre Faker en mayúsculas"),
            CatalogField("cargo_firmante", T.STATIC, "SECRETARIA DE GOBERNACIÓN"),
            CatalogField("qr_constancia", T.QR, "payload sintético"),
            CatalogField("leyenda_pie", T.STATIC, "leyenda ORIGINAL de protección de datos transcrita de la referencia"),
        ],
    },
    DocType.ACTA_NACIMIENTO_2015: {
        Face.UNICA: [
            CatalogField("escudo", T.SHAPE, "figura geométrica sustituta", in_gt=False),
            CatalogField("folio_impresion", T.TEXT, r"^\d{8}$"),
            CatalogField("titulo", T.STATIC, "Estados Unidos Mexicanos"),
            CatalogField("subtitulo", T.STATIC, "Acta de Nacimiento"),
            CatalogField("identificador_electronico", T.TEXT, r"^\d{20}$"),
            CatalogField("identificador_barcode", T.BARCODE, "igual a identificador_electronico"),
            CatalogField("curp", T.TEXT, CURP_RE),
            CatalogField("numero_certificado_nacimiento", T.TEXT, r"^\d{9}$"),
            CatalogField("entidad_registro", T.TEXT, "entidad federativa"),
            CatalogField("municipio_registro", T.TEXT, "municipio Faker"),
            CatalogField("oficialia", T.TEXT, r"^\d{1,4}$"),
            CatalogField("fecha_registro", T.TEXT, DATE_DMY),
            CatalogField("libro", T.TEXT, r"^\d{1,4}$"),
            CatalogField("numero_acta", T.TEXT, r"^\d{1,5}$"),
            CatalogField("reg_nombres", T.TEXT, "nombre(s)"),
            CatalogField("reg_primer_apellido", T.TEXT, "apellido"),
            CatalogField("reg_segundo_apellido", T.TEXT, "apellido"),
            CatalogField("reg_sexo", T.TEXT, "HOMBRE|MUJER"),
            CatalogField("reg_fecha_nacimiento", T.TEXT, DATE_DMY),
            CatalogField("reg_lugar_nacimiento", T.TEXT, "entidad federativa"),
            CatalogField("p1_nombres", T.TEXT, "nombre(s)"),
            CatalogField("p1_primer_apellido", T.TEXT, "apellido"),
            CatalogField("p1_segundo_apellido", T.TEXT, "apellido"),
            CatalogField("p1_nacionalidad", T.TEXT, "MEXICANA|..."),
            CatalogField("p1_curp", T.TEXT, CURP_RE),
            CatalogField("p2_nombres", T.TEXT, "nombre(s)"),
            CatalogField("p2_primer_apellido", T.TEXT, "apellido"),
            CatalogField("p2_segundo_apellido", T.TEXT, "apellido"),
            CatalogField("p2_nacionalidad", T.TEXT, "MEXICANA|..."),
            CatalogField("p2_curp", T.TEXT, CURP_RE),
            CatalogField("anotaciones_marginales", T.MULTILINE, "vacío o texto genérico"),
            CatalogField("certificacion", T.MULTILINE, "texto de certificación con la estructura del original y datos sintéticos"),
            CatalogField("firma_electronica", T.MULTILINE, "cadena base64 aleatoria (no firma real)"),
            CatalogField("nombre_director", T.TEXT, "nombre Faker"),
            CatalogField("qr", T.QR, "payload sintético (QR pequeño, bloque M)"),
            CatalogField("codigo_2d", T.QR, "código 2D grande del bloque I; payload sintético largo con 'MOCK DATA'"),
            CatalogField("codigo_verificacion", T.TEXT, r"^\d{15}$"),
            CatalogField("codigo_verificacion_barcode", T.BARCODE, "igual a codigo_verificacion"),
            CatalogField("leyenda_pie", T.STATIC, "leyenda ORIGINAL de verificación (bloque O) transcrita de la referencia"),
        ],
    },
    DocType.PASAPORTE_ELECTRONICO_2021: {
        # Página de datos (TD3) del pasaporte electrónico ordinario, emitido desde oct-2021.
        # Etiquetas trilingües ES/EN/FR como labels del layout. Zonas ICAO 9303 I–VII.
        Face.UNICA: [
            CatalogField("escudo", T.SHAPE, "figura geométrica sustituta", in_gt=False),
            CatalogField("simbolo_chip", T.SHAPE, "rectángulo genérico (icono e-passport)", in_gt=False),
            CatalogField("encabezado", T.STATIC, "ESTADOS UNIDOS MEXICANOS"),
            CatalogField("titulo", T.STATIC, "PASAPORTE / PASSPORT / PASSEPORT"),
            CatalogField("foto", T.PHOTO, "avatar sintético id"),
            CatalogField("foto_secundaria", T.PHOTO, "avatar sintético id (sustituye imagen holográfica/fantasma, centro-derecha)"),
            CatalogField("tipo", T.TEXT, r"^P$"),
            CatalogField("codigo_pais", T.TEXT, r"^MEX$"),
            CatalogField("numero_pasaporte", T.TEXT, r"^[A-Z]\d{8}$"),
            CatalogField("apellidos", T.TEXT, "APELLIDO1 APELLIDO2"),
            CatalogField("nombres", T.TEXT, "NOMBRE(S)"),
            CatalogField("nacionalidad", T.TEXT, r"^MEXICANA$"),
            CatalogField("observaciones", T.TEXT, "vacío o texto genérico"),
            CatalogField("fecha_nacimiento", T.TEXT, r"^\d{2} \d{2} \d{4}$"),
            CatalogField("curp", T.TEXT, CURP_RE),
            CatalogField("sexo", T.TEXT, r"^[FMX]$"),
            CatalogField("lugar_nacimiento", T.TEXT, "ENTIDAD o CIUDAD, mayúsculas"),
            CatalogField("fecha_expedicion", T.TEXT, "mismo formato que fecha_nacimiento"),
            CatalogField("autoridad", T.TEXT, "oficina emisora genérica"),
            CatalogField("fecha_caducidad", T.TEXT, "mismo formato; expedición + 3/6/10 años"),
            CatalogField("firma_portador", T.SIGNATURE, "garabato sintético id"),
            CatalogField("mrz", T.MRZ, "TD3: 2 líneas x 44 chars, P<MEX..., dígitos verificadores ICAO 9303 válidos"),
        ],
    },
}

# Tamaños físicos nominales (mm). Ver PLAN.md §Decisiones.
FACE_SIZES_MM: dict[DocType, tuple[float, float]] = {
    DocType.INE_2016: (85.60, 53.98),            # ISO/IEC 7810 ID-1
    DocType.CURP_2019: (215.9, 279.4),           # carta
    DocType.ACTA_NACIMIENTO_2015: (215.9, 279.4),  # carta
    DocType.PASAPORTE_ELECTRONICO_2021: (125.0, 88.0),     # ICAO 9303 TD3
}


VARIANTS: dict[DocType, list[Variant]] = {
    DocType.INE_2016: [Variant.NACIONAL, Variant.EXTRANJERO],  # NACIONAL = default y foco
    DocType.CURP_2019: [Variant.DEFAULT],
    DocType.ACTA_NACIMIENTO_2015: [Variant.DEFAULT],
    DocType.PASAPORTE_ELECTRONICO_2021: [Variant.DEFAULT],
}


def default_variant(doc_type: DocType) -> Variant:
    return VARIANTS[doc_type][0]


def gt_field_names(doc_type: DocType, face: Face, variant: Variant | None = None) -> list[str]:
    variant = variant or default_variant(doc_type)
    return [
        f.name
        for f in CATALOG[doc_type][face]
        if f.in_gt and (f.variants is None or variant in f.variants)
    ]
