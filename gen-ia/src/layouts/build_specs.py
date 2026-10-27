"""Construye las LayoutSpec reales (T1) y las escribe en `src/layouts/specs/<doc>.json`.

Todas las cotas están en mm, origen arriba-izquierda, medidas PROPORCIONALMENTE
sobre las referencias locales de `formatos/` (ver README.md, sección "Método de
medición"). Las referencias nunca se copian: aquí sólo hay números.

Convenciones de este módulo:
- Los campos del catálogo toman `type`, `in_ground_truth` y `variants` del
  catálogo (`contracts.CATALOG`), así no pueden divergir.
- Los campos decorativos extra (no catálogo) son STATIC/SHAPE con
  `in_ground_truth=False`.
- SHAPE: `static_value` = "<figura> [clave=valor ...] | descripción". El
  renderer interpreta la figura (ver `shapes.draw_shape`).

Uso: python -m src.layouts.build_specs
"""
from __future__ import annotations

import json
from pathlib import Path

from contracts import CATALOG, FACE_SIZES_MM, BBox, DocType, Face, LayoutSpec
from contracts.layout_spec import Align, FaceSpec, FieldSpec, FieldType, FontFamily, FontSpec

LAYOUT_VERSION = "1.0.0"
SPECS_DIR = Path(__file__).parent / "specs"

# ---------------------------------------------------------------- helpers


class _Face:
    """Acumula campos de una cara y completa los metadatos desde el catálogo."""

    def __init__(self, doc: DocType, face: Face, background=(255, 255, 255)):
        self.doc = doc
        self.face = face
        self.background = background
        self.catalog = {cf.name: cf for cf in CATALOG[doc][face]}
        self.fields: list[FieldSpec] = []

    def add(self, name: str, bbox, *, type: FieldType | None = None, size: float | None = None,
            family: str = "sans", color=(0, 0, 0), upper: bool = False, align: str = "left",
            lines: int = 1, max_chars: int | None = None, static: str | None = None,
            label: str | None = None, label_bbox=None) -> None:
        cf = self.catalog.get(name)
        if cf is not None:
            ftype = cf.type
            if type is not None and type != ftype:
                raise ValueError(f"{name}: tipo {type} != catálogo {ftype}")
            in_gt = cf.in_gt
            variants = list(cf.variants) if cf.variants else None
            if ftype in (FieldType.STATIC, FieldType.SHAPE) and static is None:
                static = cf.fmt
        else:
            if type not in (FieldType.STATIC, FieldType.SHAPE):
                raise ValueError(f"{name}: campo extra debe ser STATIC/SHAPE")
            ftype, in_gt, variants = type, False, None
        font = None
        if size is not None:
            font = FontSpec(family=FontFamily(family), size_pt=size, color_rgb=tuple(color), uppercase=upper)
        self.fields.append(FieldSpec(
            name=name, type=ftype, bbox=BBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]),
            font=font, align=Align(align), lines=lines, max_chars=max_chars, static_value=static,
            label=label,
            label_bbox=BBox(x0=label_bbox[0], y0=label_bbox[1], x1=label_bbox[2], y1=label_bbox[3]) if label_bbox else None,
            in_ground_truth=in_gt, variants=variants,
        ))

    def shape(self, name: str, bbox, desc: str) -> None:
        self.add(name, bbox, type=FieldType.SHAPE, static=desc)

    def static(self, name: str, bbox, text: str, size: float, **kw) -> None:
        self.add(name, bbox, type=FieldType.STATIC, static=text, size=size, **kw)

    def build(self) -> FaceSpec:
        w, h = FACE_SIZES_MM[self.doc]
        return FaceSpec(face=self.face, width_mm=w, height_mm=h,
                        background_rgb=tuple(self.background), fields=self.fields)


# ---------------------------------------------------------------- INE 2016

def ine_2016() -> LayoutSpec:
    D = DocType.INE_2016
    # --- Anverso. Marco medido en modeloactual2016-ine.pdf p.1 (render 432 dpi):
    # x 778–2781 px, y 1280–2525 px -> 85.60 x 53.98 mm.
    a = _Face(D, Face.ANVERSO, background=(238, 234, 227))
    dark = (35, 35, 35)
    a.shape("banda_encabezado", (0, 0, 85.6, 13.6),
            "header_band color=166,156,146 | banda superior de tinte plano con borde inferior curvo")
    a.shape("escudo", (8.2, 3.0, 18.8, 13.6), "circle stroke=60,60,60 | círculo de contorno (sustituto geométrico)")
    a.add("pais", (20.0, 5.8, 31.2, 9.0), size=8.4, color=dark)
    a.add("titulo", (32.0, 3.4, 82.8, 7.0), size=10.0, color=dark)
    a.add("subtitulo", (32.0, 7.6, 66.4, 10.4), size=8.0, family="sans_bold", color=dark)
    a.add("leyenda_extranjero", (32.0, 10.6, 68.0, 12.8), size=5.5, family="sans_bold", color=dark)
    a.add("foto", (6.5, 15.0, 27.5, 46.0))
    lab = dict(size=7.5, color=(20, 20, 20), upper=True)
    a.add("nombre", (29.0, 16.0, 68.5, 23.6), lines=3, max_chars=30, label="NOMBRE",
          label_bbox=(29.0, 14.2, 40.0, 15.9), **lab)
    a.add("domicilio", (29.0, 27.2, 71.5, 35.0), lines=3, max_chars=40, label="DOMICILIO",
          label_bbox=(29.0, 25.1, 40.0, 26.8), **lab)
    a.add("clave_elector", (44.4, 35.8, 72.0, 38.4), max_chars=18, label="CLAVE DE ELECTOR",
          label_bbox=(29.0, 36.3, 44.0, 37.9), **lab)
    a.add("curp", (34.0, 38.8, 62.2, 41.4), max_chars=18, label="CURP",
          label_bbox=(29.0, 39.3, 33.8, 40.9), **lab)
    a.add("anio_registro", (76.6, 38.8, 85.4, 41.4), max_chars=7, label="AÑO DE REGISTRO",
          label_bbox=(62.4, 39.3, 76.4, 40.9), **lab)
    a.add("estado", (38.6, 42.4, 44.5, 44.9), max_chars=2, label="ESTADO",
          label_bbox=(29.0, 42.9, 35.5, 44.6), **lab)
    a.add("municipio", (55.8, 42.4, 61.6, 44.9), max_chars=3, label="MUNICIPIO",
          label_bbox=(46.8, 42.9, 55.4, 44.6), **lab)
    a.add("seccion", (70.6, 42.4, 76.8, 44.9), max_chars=4, label="SECCIÓN",
          label_bbox=(62.2, 42.9, 69.6, 44.6), **lab)
    a.add("localidad", (39.0, 45.9, 45.2, 48.4), max_chars=4, label="LOCALIDAD",
          label_bbox=(29.0, 46.4, 38.0, 48.1), **lab)
    a.add("emision", (56.0, 45.9, 62.0, 48.4), max_chars=4, label="EMISIÓN",
          label_bbox=(46.8, 46.4, 55.0, 48.1), **lab)
    a.add("vigencia", (70.6, 45.9, 76.8, 48.4), max_chars=4, label="VIGENCIA",
          label_bbox=(62.2, 46.4, 70.0, 48.1), **lab)
    a.add("fecha_nacimiento", (69.6, 16.0, 84.6, 18.8), max_chars=10, align="right",
          label="FECHA NACIMIENTO", label_bbox=(68.8, 14.2, 84.4, 15.9), **lab)
    a.add("sexo", (82.0, 18.9, 84.6, 21.5), max_chars=1, align="right", label="SEXO",
          label_bbox=(76.6, 19.6, 81.6, 21.3), **lab)
    a.add("foto_secundaria", (72.4, 24.2, 84.2, 37.6))

    # --- Reverso. Marco: x 798–2780 px, y 3246–4500 px (misma página).
    r = _Face(D, Face.REVERSO, background=(234, 232, 229))
    r.shape("zona_superior", (0, 0, 22.4, 5.9), "fill color=255,255,255 | zona clara superior izquierda")
    r.shape("barras_superior", (4.8, 1.0, 17.6, 5.4),
            "stripes seed=7 | franjas verticales decorativas, no codifican datos")
    r.shape("recuadros_voto", (22.8, 0.6, 70.2, 5.4), "boxes n=2 split=0.37 | dos recuadros vacíos")
    r.shape("logo_institucion", (76.0, 0.6, 84.0, 3.6), "hexagon stroke=90,90,90 | hexágono de contorno (sustituto geométrico)")
    r.add("codigo_barras", (2.7, 6.2, 69.5, 19.0))
    r.add("qr", (71.2, 6.2, 83.9, 19.0))
    r.shape("recuadro_firma", (2.7, 19.8, 62.4, 29.4), "rect fill=255,255,255 stroke=40,40,40 | recuadro de firma")
    r.add("firma", (16.0, 20.4, 48.5, 28.8))
    r.add("huella", (50.4, 20.4, 57.0, 28.8), static="oval stroke=110,110,110 | óvalo vacío (sin huella)")
    # MRZ TD1: 3 x 30 caracteres, paso fijo = 82/30 mm. CIC y OCR son sub-rangos
    # de la línea 1 (posiciones 5–13 y 17–29, base 0) -> el renderer los alinea.
    mrz_x0, mrz_x1, mrz_y0, mrz_y1 = 2.0, 84.0, 35.6, 50.4
    pitch = (mrz_x1 - mrz_x0) / 30
    line_h = (mrz_y1 - mrz_y0) / 3
    r.add("mrz", (mrz_x0, mrz_y0, mrz_x1, mrz_y1), size=12.5, family="ocrb", lines=3, max_chars=30)
    r.add("cic", (round(mrz_x0 + 5 * pitch, 3), mrz_y0, round(mrz_x0 + 14 * pitch, 3), round(mrz_y0 + line_h, 3)),
          size=12.5, family="ocrb", max_chars=9)
    r.add("ocr", (round(mrz_x0 + 17 * pitch, 3), mrz_y0, mrz_x1, round(mrz_y0 + line_h, 3)),
          size=12.5, family="ocrb", max_chars=13)
    return LayoutSpec(
        doc_type=D, layout_version=LAYOUT_VERSION, faces=[a.build(), r.build()],
        source_note=("Medido proporcionalmente sobre formatos/modeloactual2016-ine.pdf pág. 1 "
                     "(rasterizada a 432 dpi; marco anverso 2003x1245 px, reverso 1982x1254 px; "
                     "conversión independiente por eje a 85.60x53.98 mm). Elementos de seguridad omitidos; "
                     "escudo/logo -> figuras geométricas; fotos -> avatares; huella -> óvalo."),
    )


# ---------------------------------------------------------------- CURP 2019

CURP_CUERPO = (
    "Desde 2014, el derecho a la identidad está consagrado en nuestra Constitución. En la Secretaría de "
    "Gobernación trabajamos todos los días para garantizar que las y los mexicanos gocen de este derecho "
    "plenamente; y de esta forma puedan acceder de manera más sencilla a trámites y servicios.\n\n"
    "En ese sentido, nuestro objetivo es que el uso y adopción de la Clave Única de Registro de Población "
    "(CURP) permita a la población tener una sola llave de acceso a servicios gubernamentales, ser atendida "
    "rápidamente y poder realizar trámites desde cualquier computadora con acceso a internet dentro o fuera "
    "del país.\n\n"
    "Nuestro compromiso es que la identidad de cada persona esté protegida y segura, por ello contamos con "
    "los máximos estándares para la protección de los datos personales. En este marco, es importante que "
    "verifiques que la información contenida en la constancia anexa sea correcta para contribuir a la "
    "construcción de un registro fiel y confiable de la identidad de la población.\n\n"
    "Agradezco tu participación."
)

CURP_LEYENDA = (
    "Los Datos Personales recabados, incorporados y tratados en la Base de Datos Nacional de la Clave Única "
    "de Registro de Población, son utilizados como elementos de apoyo en la función de la Secretaría de "
    "Gobernación, a través de la Dirección General del Registro Nacional de Población e Identificación "
    "Personal en el registro y acreditación de la identidad de la población del país, y de los nacionales "
    "residentes en el extranjero; asignando y expidiendo la Clave Única de Registro de Población. Dicha Base "
    "de Datos, se encuentra registrada en el Sistema Persona del Instituto Nacional de Transparencia, Acceso a "
    "la Información Pública y Protección de Datos Personales (http://persona.ifai.org.mx/persona/welcome.do). "
    "La transferencia de los Datos Personales y el ejercicio de los derechos de acceso, rectificación, "
    "cancelación y oposición, deben realizarse conforme a la Ley General de Protección de Datos Personales en "
    "Posesión de los Sujetos Obligados, y demás normatividad aplicable. Para ver la versión integral del "
    "aviso de privacidad ingresar a https://renapo.gob.mx/"
)


def curp_2019() -> LayoutSpec:
    D = DocType.CURP_2019
    # Diapositiva1.png (960x720 px): la hoja no tiene borde visible; se fija la escala
    # 2.376 px/mm con el recuadro verde (x 246–700 px) a 12.7 mm de cada borde lateral.
    u = _Face(D, Face.UNICA)
    grey = (70, 70, 70)
    u.shape("marco", (12.7, 12.5, 203.8, 102.8), "frame stroke=46,110,60 inset=1.6 width=0.8 | marco de línea lisa")
    u.shape("escudo", (100.6, 13.6, 114.4, 26.8), "circle stroke=46,110,60 | círculo de contorno (sustituto geométrico)")
    u.shape("logo_dependencia", (18.0, 29.0, 60.0, 42.0), "rect stroke=120,40,60 | rectángulo de contorno (sustituto del logo)")
    u.add("titulo", (64.0, 26.6, 152.0, 33.0), size=15.0, family="serif", color=grey, align="center")
    u.add("subtitulo", (64.0, 33.4, 152.0, 44.6), size=11.5, family="serif", color=grey, align="center", lines=2)
    u.static("dependencia_emisora", (152.0, 34.0, 198.0, 42.6),
             "DIRECCIÓN GENERAL DEL REGISTRO NACIONAL DE POBLACIÓN E IDENTIFICACIÓN PERSONAL",
             5.5, family="serif", color=(110, 110, 110), align="center", lines=3)
    u.shape("bandera", (27.0, 53.0, 51.6, 69.2), "flag3 | tres franjas verticales lisas")
    u.add("curp", (65.0, 53.0, 140.0, 58.0), size=13.0, family="sans_bold", max_chars=18, upper=True,
          label="Clave:", label_bbox=(65.0, 48.6, 90.0, 51.8))
    u.add("nombre", (65.0, 68.8, 172.0, 74.0), size=11.0, family="sans_bold", max_chars=50, upper=True,
          label="Nombre:", label_bbox=(65.0, 63.6, 90.0, 66.9))
    u.add("qr_datos", (175.2, 50.4, 197.6, 72.0))
    u.add("fecha_inscripcion", (65.0, 90.6, 100.0, 94.2), size=8.5, family="sans_bold", max_chars=10,
          label="Fecha de inscripción", label_bbox=(65.0, 85.3, 100.0, 89.0))
    u.add("folio", (104.0, 90.6, 130.0, 94.2), size=8.5, family="sans_bold", max_chars=8,
          label="Folio", label_bbox=(104.0, 85.3, 130.0, 89.0))
    u.add("entidad_registro", (134.0, 90.6, 198.0, 94.2), size=8.5, family="sans_bold", max_chars=30, upper=True,
          label="Entidad de registro", label_bbox=(134.0, 85.3, 198.0, 89.0))
    u.add("codigo_barras", (13.5, 103.8, 54.0, 111.5))
    u.add("codigo_barras_texto", (13.5, 112.2, 54.0, 115.4), size=7.0, align="center", max_chars=15)
    u.add("estatus_curp", (132.0, 107.4, 204.0, 110.3), size=6.0, align="center", max_chars=60)
    u.add("curps_asociadas", (147.0, 114.8, 204.0, 120.4), size=6.0, align="center", lines=2, max_chars=18,
          label="CURP's asociadas por corrección", label_bbox=(147.0, 111.4, 204.0, 114.4))
    u.add("nombre_destinatario", (13.0, 130.2, 120.0, 133.6), size=8.0, family="sans_bold", max_chars=50, upper=True)
    u.static("presente", (13.0, 145.4, 40.0, 148.4), "PRESENTE", 8.0)
    u.add("lugar_fecha", (110.0, 145.4, 204.6, 148.4), size=7.5, align="right", max_chars=50)
    u.add("cuerpo", (12.7, 150.2, 204.7, 186.4), size=7.5, lines=12, static=CURP_CUERPO)
    u.add("firmante", (40.0, 196.2, 174.6, 199.8), size=9.0, family="sans_bold", align="center", max_chars=50, upper=True)
    u.add("cargo_firmante", (42.0, 203.2, 174.6, 206.6), size=8.0, align="center", color=grey)
    u.add("qr_constancia", (174.4, 192.4, 197.9, 215.6))
    u.static("aviso_telcurp", (13.0, 215.6, 170.0, 218.4),
             "Estamos a sus órdenes para cualquier aclaración o duda sobre la conformación de su clave en "
             "TELCURP, marcando el 01 800 911 11 11", 5.8)
    u.static("aviso_impresion", (13.0, 224.2, 180.0, 227.2),
             "La impresión de la constancia CURP en papel bond, a color o blanco y negro, es válida y debe ser "
             "aceptada para realizar todo trámite.", 5.8, family="sans_bold")
    u.static("tramite_gratuito", (80.0, 229.4, 136.6, 233.0), "TRÁMITE GRATUITO", 9.0,
             family="sans_bold", align="center")
    u.add("leyenda_pie", (13.0, 235.3, 204.6, 258.2), size=6.8, lines=7, static=CURP_LEYENDA)
    return LayoutSpec(
        doc_type=D, layout_version=LAYOUT_VERSION, faces=[u.build()],
        source_note=("Medido proporcionalmente sobre formatos/Diapositiva1.png (960x720 px, ampliada 3.4x con "
                     "rejilla mm). Escala 2.376 px/mm fijada por el recuadro verde a 12.7 mm de los bordes "
                     "laterales de la hoja carta; origen de la hoja en (216, 100) px. Textos 'cuerpo' y "
                     "'leyenda_pie' transcritos de la referencia. Logos/escudo -> figuras geométricas; bandera -> "
                     "franjas lisas; marco -> línea lisa."),
    )


# ---------------------------------------------------------------- Acta 2015

ACTA_LEYENDA = (
    "El contenido de esta acta puede ser verificado en la siguiente página web: www.gob.mx/actas  capturando "
    "el Identificador Electrónico.\n"
    "Para su consulta en dispositivos móviles, descarga una aplicación para lectura del Código QR."
)


def acta_nacimiento_2015() -> LayoutSpec:
    D = DocType.ACTA_NACIMIENTO_2015
    # OFICIO ... ANEXOS.pdf pág. 3 (216 dpi): marco exterior x 268–1500 px, y 572–2172 px
    # -> hoja carta completa (relación 0.770 vs 0.773 de la carta).
    u = _Face(D, Face.UNICA)
    grey = (60, 60, 60)
    bar = "fill color=232,232,236 | barra de etiqueta de tinte plano"
    red = (200, 30, 30)
    u.shape("marco", (1.6, 1.6, 214.3, 277.8), "frame stroke=60,110,70 inset=5.8 width=0.6 | marco de línea lisa")
    u.shape("escudo", (97.0, 6.2, 121.0, 30.0), "circle stroke=60,110,70 | círculo de contorno (sustituto geométrico)")
    u.add("folio_impresion", (18.0, 12.8, 48.0, 17.2), size=11.0, family="sans_bold", color=red, align="center",
          max_chars=8, label="Folio de Impresión", label_bbox=(18.0, 9.2, 48.0, 12.6))
    u.add("identificador_electronico", (150.0, 17.5, 198.0, 21.8), size=9.0, align="center", max_chars=20,
          label="Identificador Electrónico", label_bbox=(150.0, 13.0, 198.0, 17.0))
    u.add("identificador_barcode", (158.0, 21.9, 190.0, 26.2))
    u.add("titulo", (24.0, 45.4, 118.0, 52.6), size=19.5, family="sans_bold", color=(70, 70, 80), align="center")
    u.add("subtitulo", (24.0, 55.6, 118.0, 63.0), size=19.5, family="sans_bold", color=(70, 70, 80), align="center")
    # Columna derecha: barra de etiqueta + valor centrado.
    rows = [
        ("curp", "Clave Única de Registro de Población", 28.2, 33.2, 33.6, 39.6, 18),
        ("numero_certificado_nacimiento", "Número de Certificado de Nacimiento", 42.0, 47.0, 47.2, 52.2, 9),
        ("entidad_registro", "Entidad de Registro", 52.6, 57.4, 57.6, 62.6, 30),
        ("municipio_registro", "Municipio de Registro", 62.8, 67.6, 67.8, 72.8, 40),
    ]
    for name, label, ly0, ly1, vy0, vy1, mc in rows:
        u.shape(f"barra_{name}", (133.0, ly0, 203.0, ly1), bar)
        u.add(name, (133.0, vy0, 203.0, vy1), size=9.0, align="center", max_chars=mc, upper=True,
              label=label, label_bbox=(133.0, ly0 + 0.3, 203.0, ly1 - 0.3))
    u.shape("tabla_registro", (133.0, 73.0, 203.0, 83.6), "grid rows=2 cols=4 stroke=90,90,90 | tabla de líneas lisas")
    cols = [("oficialia", "Oficialía", 4), ("fecha_registro", "Fecha de\nRegistro", 10),
            ("libro", "Libro", 4), ("numero_acta", "Número\nde Acta", 5)]
    for i, (name, label, mc) in enumerate(cols):
        x0 = 133.0 + i * 17.5
        u.add(name, (x0 + 0.4, 78.6, x0 + 17.1, 83.4), size=8.0, align="center", max_chars=mc,
              label=label, label_bbox=(x0 + 0.4, 73.4, x0 + 17.1, 78.0) if "\n" in label else (x0 + 0.4, 74.6, x0 + 17.1, 76.8))
    # Bloque E
    u.shape("barra_registrada", (15.0, 84.0, 203.0, 89.8), "fill color=232,232,236 | barra de sección")
    u.static("seccion_registrada", (15.0, 84.4, 203.0, 89.4), "Datos de la Persona Registrada", 9.0,
             align="center", color=grey)
    val = dict(size=9.0, align="center", upper=True)
    reg = [
        [("reg_nombres", "Nombre(s)", 15.0, 73.0, 40), ("reg_primer_apellido", "Primer Apellido", 77.0, 143.0, 30),
         ("reg_segundo_apellido", "Segundo Apellido", 145.0, 203.0, 30)],
        [("reg_sexo", "Sexo", 15.0, 69.0, 6), ("reg_fecha_nacimiento", "Fecha de Nacimiento", 77.0, 143.0, 10),
         ("reg_lugar_nacimiento", "Lugar de Nacimiento", 145.0, 203.0, 30)],
    ]
    for (vy0, vy1, line_y, ly0, ly1), row in zip([(93.2, 98.8, 99.0, 100.2, 104.6), (110.4, 116.0, 116.4, 117.4, 121.8)], reg):
        u.shape(f"linea_{row[0][0]}", (15.0, line_y, 203.0, line_y + 0.4), "hline stroke=60,60,60 | línea lisa")
        for name, label, x0, x1, mc in row:
            u.add(name, (x0, vy0, x1, vy1), max_chars=mc, label=label, label_bbox=(x0, ly0, x1, ly1), **val)
    # Bloque F
    u.shape("barra_filiacion", (15.0, 124.6, 203.0, 131.4), "fill color=232,232,236 | barra de sección")
    u.static("seccion_filiacion", (15.0, 125.2, 203.0, 130.8), "Datos de Filiación de la Persona Registrada", 9.0,
             align="center", color=grey)
    pcols = [("nombres", "Nombre(s)", 15.0, 47.0, 30), ("primer_apellido", "Primer Apellido", 49.0, 85.0, 25),
             ("segundo_apellido", "Segundo Apellido", 86.5, 123.0, 25), ("nacionalidad", "Nacionalidad", 127.0, 164.6, 20),
             ("curp", "CURP", 165.4, 202.6, 18)]
    for p, (vy0, vy1, line_y, ly0, ly1) in (("p1", (139.0, 144.6, 144.8, 145.8, 150.0)),
                                            ("p2", (160.6, 166.1, 166.3, 167.3, 171.5))):
        u.shape(f"linea_{p}", (15.0, line_y, 203.0, line_y + 0.4), "hline stroke=60,60,60 | línea lisa")
        for suffix, label, x0, x1, mc in pcols:
            u.add(f"{p}_{suffix}", (x0, vy0, x1, vy1), size=8.0, align="center", upper=True, max_chars=mc,
                  label=label, label_bbox=(x0, ly0, x1, ly1))
    # Bloques G y H
    u.shape("encabezado_anotaciones", (13.5, 176.0, 124.6, 183.2), "fill color=232,232,236 | barra de encabezado")
    u.shape("recuadro_anotaciones", (13.5, 176.0, 124.6, 215.0), "rect stroke=60,60,60 | recuadro liso")
    u.shape("encabezado_certificacion", (124.6, 176.0, 202.6, 183.2), "fill color=232,232,236 | barra de encabezado")
    u.shape("recuadro_certificacion", (124.6, 176.0, 202.6, 215.0), "rect stroke=60,60,60 | recuadro liso")
    u.shape("linea_certificacion", (124.6, 206.3, 202.6, 206.7), "hline stroke=60,60,60 | línea lisa")
    u.add("anotaciones_marginales", (15.0, 184.0, 123.4, 214.4), size=7.0, lines=8, max_chars=80,
          label="Anotaciones Marginales:", label_bbox=(16.0, 177.0, 90.0, 182.4))
    u.add("certificacion", (126.0, 184.0, 201.6, 206.0), size=6.5, lines=7, max_chars=70,
          label="Certificación:", label_bbox=(126.0, 177.0, 170.0, 182.4))
    # Bloques I–O
    u.add("codigo_2d", (13.5, 216.6, 60.0, 263.0))  # bloque I: código bidimensional grande
    u.add("firma_electronica", (125.0, 222.6, 203.0, 232.8), size=5.5, family="mono", lines=4, max_chars=64,
          label="Firma Electrónica Avanzada", label_bbox=(125.0, 217.2, 182.0, 221.8))
    u.add("codigo_verificacion", (64.0, 251.6, 100.0, 255.2), size=8.0, family="sans_bold", color=red,
          align="center", max_chars=15, label="Código de Verificación", label_bbox=(64.0, 247.8, 100.0, 251.2))
    u.add("codigo_verificacion_barcode", (66.5, 255.6, 98.0, 259.8))
    u.add("nombre_director", (131.0, 249.4, 182.6, 254.8), size=8.0, align="center", max_chars=45, upper=True,
          label="Director General de Registro Civil", label_bbox=(131.0, 255.0, 182.6, 259.0))
    u.add("qr", (183.2, 237.6, 202.4, 257.2), label="Código QR", label_bbox=(183.2, 233.4, 202.4, 236.8))
    u.add("leyenda_pie", (16.0, 264.4, 176.0, 270.6), size=7.2, lines=2, static=ACTA_LEYENDA)
    return LayoutSpec(
        doc_type=D, layout_version=LAYOUT_VERSION, faces=[u.build()],
        source_note=("Medido proporcionalmente sobre formatos/OFICIO DE SEGOB DG-410-0075-2017 Y ANEXOS.pdf "
                     "pág. 3 (rasterizada a 216 dpi; marco exterior 1232x1600 px -> 215.9x279.4 mm). "
                     "'leyenda_pie' (bloque O) transcrita de la referencia. Marco decorado -> doble línea lisa; "
                     "escudo -> círculo."),
    )


# ---------------------------------------------------------------- Pasaporte 2021

def pasaporte_electronico_2021() -> LayoutSpec:
    D = DocType.PASAPORTE_ELECTRONICO_2021
    # pasaportelibretae.pdf (360 dpi, página de datos rotada 90°): marco 1210x800 px
    # -> 125x88 mm (TD3). La MRZ se ajusta a ICAO 9303 (paso 2.54 mm, 44 caracteres).
    u = _Face(D, Face.UNICA, background=(239, 235, 219))
    v = dict(size=9.5, family="mono", upper=True)
    u.shape("zona_mrz", (0.0, 69.6, 125.0, 88.0), "fill color=249,248,243 | zona inferior lisa")
    u.add("titulo", (3.0, 2.2, 37.0, 6.4), size=10.0, family="serif", color=(40, 40, 40))
    u.add("encabezado", (38.0, 2.4, 92.0, 6.6), size=11.0, family="serif_bold", color=(40, 40, 40))
    u.add("simbolo_chip", (99.0, 1.6, 106.0, 6.6), static="chip stroke=60,60,60 | rectángulo genérico con círculo")
    u.add("escudo", (110.6, 0.8, 117.0, 7.2), static="circle stroke=90,80,60 | círculo de contorno (sustituto geométrico)")
    u.add("foto", (4.5, 13.5, 34.0, 53.5))
    u.add("tipo", (45.5, 10.2, 54.0, 13.6), max_chars=1, label="Tipo / Type / Type",
          label_bbox=(38.0, 7.2, 55.0, 9.8), **v)
    u.add("codigo_pais", (71.5, 10.2, 84.0, 13.6), max_chars=3,
          label="Clave del país de expedición\nIssuing State code / Code de l'État émetteur",
          label_bbox=(56.0, 6.8, 89.0, 9.9), **v)
    u.add("numero_pasaporte", (101.0, 10.6, 121.5, 14.2), max_chars=9, size=10.5, family="mono", upper=True,
          label="Pasaporte No. / Passport No. / No du Passeport", label_bbox=(90.0, 7.2, 121.5, 9.8))
    u.add("foto_secundaria", (84.0, 14.8, 104.0, 26.2))
    u.add("apellidos", (38.0, 21.2, 83.0, 24.6), max_chars=39, label="Apellidos / Surname / Nom",
          label_bbox=(38.0, 18.4, 80.0, 20.8), **v)
    u.add("nombres", (38.0, 28.2, 80.0, 31.4), max_chars=39, label="Nombres / Given names / Prénoms",
          label_bbox=(38.0, 25.4, 80.0, 27.8), **v)
    u.add("observaciones", (81.0, 29.0, 122.0, 32.0), max_chars=30,
          label="Observaciones / Remarks / Observations", label_bbox=(81.0, 26.6, 122.0, 28.6), **v)
    u.add("nacionalidad", (38.0, 35.2, 80.0, 38.4), max_chars=8,
          label="Nacionalidad / Nationality / Nationalité", label_bbox=(38.0, 32.6, 80.0, 34.9), **v)
    u.add("curp", (81.0, 35.2, 118.0, 38.4), max_chars=18, label="CURP / Personal No. / No personnel",
          label_bbox=(81.0, 32.6, 122.0, 34.9), **v)
    u.add("fecha_nacimiento", (38.0, 42.2, 80.0, 45.6), max_chars=10,
          label="Fecha de nacimiento / Date of birth / Date de naissance", label_bbox=(38.0, 39.4, 90.0, 41.9), **v)
    u.add("sexo", (38.0, 49.2, 55.0, 52.4), max_chars=1, label="Sexo / Sex / Sexe",
          label_bbox=(38.0, 46.3, 56.0, 48.7), **v)
    u.add("lugar_nacimiento", (58.0, 49.2, 121.0, 52.4), max_chars=30,
          label="Lugar de nacimiento / Place of birth / Lieu de naissance", label_bbox=(58.0, 46.3, 110.0, 48.7), **v)
    u.add("fecha_expedicion", (38.0, 55.4, 80.0, 58.8), max_chars=10,
          label="Fecha de expedición / Date of issue / Date de délivrance", label_bbox=(38.0, 52.7, 82.0, 55.1), **v)
    u.add("autoridad", (84.0, 55.4, 121.0, 58.8), max_chars=20, label="Autoridad / Authority / Autorité",
          label_bbox=(84.0, 52.9, 110.0, 55.1), **v)
    u.add("fecha_caducidad", (38.0, 64.0, 80.0, 67.6), max_chars=10,
          label="Fecha de caducidad / Date of expiry / Date d'expiration", label_bbox=(38.0, 61.4, 90.0, 63.8), **v)
    u.add("firma_portador", (7.0, 62.4, 34.0, 69.4),
          label="Firma del titular\nHolder's signature / Signature du titulaire", label_bbox=(5.0, 56.6, 36.0, 61.6))
    # ICAO 9303 TD3: 2 x 44 caracteres a 2.54 mm de paso, centrada horizontalmente.
    u.add("mrz", (6.62, 71.4, 118.38, 82.6), size=11.5, family="ocrb", lines=2, max_chars=44)
    return LayoutSpec(
        doc_type=D, layout_version=LAYOUT_VERSION, faces=[u.build()],
        source_note=("Proporciones de la familia de diseño medidas sobre formatos/pasaportelibretae.pdf "
                     "(página de datos tipo 'E' rotada, rasterizada a 360 dpi; marco 1210x800 px -> 125x88 mm, "
                     "conversión independiente por eje) y adaptadas al pasaporte electrónico ordinario 2021 "
                     "(Tipo 'P'); MRZ según ICAO 9303 TD3 (2x44, paso 2.54 mm). Imagen secundaria -> avatar; "
                     "escudo/chip -> figuras geométricas."),
    )


BUILDERS = {
    DocType.INE_2016: ine_2016,
    DocType.CURP_2019: curp_2019,
    DocType.ACTA_NACIMIENTO_2015: acta_nacimiento_2015,
    DocType.PASAPORTE_ELECTRONICO_2021: pasaporte_electronico_2021,
}


def spec_json(doc_type: DocType) -> str:
    spec = BUILDERS[doc_type]()
    return json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    for dt in DocType:
        (SPECS_DIR / f"{dt.value}.json").write_text(spec_json(dt), encoding="utf-8")
        print("escrito", dt.value)


if __name__ == "__main__":
    main()
