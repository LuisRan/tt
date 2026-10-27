"""Motor de render de plantillas (reportlab) a partir de una LayoutSpec.

- Una página por cara, del tamaño físico de la cara (mm).
- Coordenadas de la spec: mm, origen arriba-izquierda. Se convierten a puntos
  de reportlab (origen abajo-izquierda) en `_Page`.
- Salida determinista: `Canvas(invariant=1)` y ningún dato dependiente de la
  hora o del azar no sembrado.
- La marca de agua "MOCK DATA" se dibuja como contenido normal de la página,
  al final (encima de todo).
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any, Mapping

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.code128 import Code128
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import black
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from contracts import WATERMARK_TEXT, DocType, Face, LayoutSpec, Variant, default_variant
from contracts.layout_spec import Align, FieldSpec, FieldType, FontFamily

from .shapes import draw_avatar, draw_shape, draw_signature

SPECS_DIR = Path(__file__).parent / "specs"

# Familias lógicas -> fuentes base PDF (incluidas en reportlab, sin archivos de fuente).
FONT_MAP: dict[FontFamily, str] = {
    FontFamily.SANS: "Helvetica",
    FontFamily.SANS_BOLD: "Helvetica-Bold",
    FontFamily.SERIF: "Times-Roman",
    FontFamily.SERIF_BOLD: "Times-Bold",
    FontFamily.MONO: "Courier-Bold",
    FontFamily.OCRB: "Courier",  # sin OCR-B con licencia libre -> monoespaciada
}

# Estilo de las etiquetas impresas (label) por documento: (fuente, color).
LABEL_STYLE: dict[DocType, tuple[str, tuple[int, int, int]]] = {
    DocType.INE_2016: ("Helvetica-Bold", (45, 45, 45)),
    DocType.CURP_2019: ("Helvetica", (40, 40, 40)),
    DocType.ACTA_NACIMIENTO_2015: ("Helvetica", (55, 55, 55)),
    DocType.PASAPORTE_ELECTRONICO_2021: ("Helvetica", (180, 45, 60)),
}

# Tamaño máximo (pt) de las etiquetas, medido sobre las referencias.
LABEL_MAX_PT: dict[DocType, float] = {
    DocType.INE_2016: 5.0,
    DocType.CURP_2019: 8.5,
    DocType.ACTA_NACIMIENTO_2015: 9.0,
    DocType.PASAPORTE_ELECTRONICO_2021: 5.5,
}

CAP_HEIGHT = 0.70      # altura de mayúsculas aprox. (fracción del cuerpo) de las fuentes base
MIN_FONT_SCALE = 0.60  # reducción máxima del tamaño de fuente
MIN_HSCALE = 0.80      # condensación horizontal máxima antes de reducir el tamaño
WATERMARK_GRAY = 0.30
WATERMARK_ALPHA = 0.20
WATERMARK_DIAG_FRACTION = 0.80  # ancho del texto como fracción de la diagonal de la página

TEXTUAL = {FieldType.TEXT, FieldType.MULTILINE, FieldType.STATIC, FieldType.MRZ}


# ------------------------------------------------------------------ carga

def load_spec(doc_type: DocType | str) -> LayoutSpec:
    """Carga y valida `src/layouts/specs/<doc_type>.json`."""
    dt = DocType(doc_type)
    data = json.loads((SPECS_DIR / f"{dt.value}.json").read_text(encoding="utf-8"))
    return LayoutSpec.model_validate(data)


# ------------------------------------------------------------------ placeholders

def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def placeholder_value(doc_type: DocType, field: FieldSpec) -> Any:
    """Valor de relleno determinista para un campo sin valor."""
    t = field.type
    if t == FieldType.TEXT:
        return "X" * field.max_chars if field.max_chars else field.name.upper()
    if t == FieldType.MULTILINE:
        line = "X" * field.max_chars if field.max_chars else field.name.upper()
        return [line] * field.lines
    if t == FieldType.MRZ:
        return ["X" * (field.max_chars or 30)] * field.lines
    if t == FieldType.QR:
        return f"{WATERMARK_TEXT} {doc_type.value} {field.name}"
    if t == FieldType.BARCODE:
        return "MOCKDATA" + _short_hash(field.name)[:6].upper()
    if t == FieldType.PHOTO:
        return "avatar:" + _short_hash(doc_type.value + field.name)
    if t == FieldType.SIGNATURE:
        return "firma:" + _short_hash(doc_type.value + field.name)
    return field.static_value


# ------------------------------------------------------------------ texto

def fit_line(text: str, font: str, size: float, width: float) -> tuple[str, float, float]:
    """Ajusta una línea a `width` pt. Devuelve (texto, tamaño, escala_horizontal).

    Orden: condensar hasta MIN_HSCALE, luego reducir tamaño hasta MIN_FONT_SCALE,
    luego truncar caracteres. Nunca sale del ancho.
    """
    w = stringWidth(text, font, size)
    if w <= width or not text:
        return text, size, 1.0
    hs = max(MIN_HSCALE, width / w)
    if w * hs <= width:
        return text, size, hs
    hs = MIN_HSCALE
    new_size = size * width / (w * hs)
    if new_size >= size * MIN_FONT_SCALE:
        return text, new_size * 0.999, hs
    new_size = size * MIN_FONT_SCALE
    while text and stringWidth(text, font, new_size) * hs > width:
        text = text[:-1]
    return text, new_size, hs


def wrap_text(text: str, font: str, size: float, width: float) -> list[str]:
    """Ajuste por palabras; '\\n' separa párrafos y una línea vacía se conserva."""
    out: list[str] = []
    for para in text.split("\n"):
        words = para.split()
        if not words:
            out.append("")
            continue
        line = words[0]
        for wd in words[1:]:
            cand = line + " " + wd
            if stringWidth(cand, font, size) <= width:
                line = cand
            else:
                out.append(line)
                line = wd
        out.append(line)
    return out


class _Page:
    """Envuelve el canvas con conversión mm (arriba-izquierda) -> pt (abajo-izquierda)."""

    def __init__(self, c: Canvas, width_mm: float, height_mm: float):
        self.c = c
        self.w = width_mm
        self.h = height_mm

    def box(self, b) -> tuple[float, float, float, float]:
        """BBox mm -> (x, y, w, h) en pt, con y = borde inferior."""
        return b.x0 * mm, (self.h - b.y1) * mm, (b.x1 - b.x0) * mm, (b.y1 - b.y0) * mm

    def text_line(self, text: str, font: str, size: float, color, x0: float, x1: float,
                  top: float, bottom: float, align: Align) -> None:
        """Una línea centrada verticalmente en [top, bottom] (mm), ajustada a [x0, x1] (mm)."""
        width = (x1 - x0) * mm
        text, size, hs = fit_line(text, font, size, width)
        if not text:
            return
        tw = stringWidth(text, font, size) * hs
        if align == Align.CENTER:
            x = x0 * mm + (width - tw) / 2
        elif align == Align.RIGHT:
            x = x1 * mm - tw
        else:
            x = x0 * mm
        center = (top + bottom) / 2
        base = (self.h - center) * mm - CAP_HEIGHT * size / 2
        c = self.c
        c.setFillColorRGB(*(v / 255.0 for v in color))
        t = c.beginText()
        t.setFont(font, size)
        t.setHorizScale(hs * 100)
        t.setTextOrigin(x, base)
        t.textOut(text)
        c.drawText(t)


# ------------------------------------------------------------------ campos

def _as_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return str(value).split("\n")


def _draw_fixed_lines(pg: _Page, f: FieldSpec, lines: list[str]) -> None:
    font = FONT_MAP[f.font.family]
    slot = f.bbox.height / f.lines
    size = min(f.font.size_pt, slot * mm / 1.05)
    for i, line in enumerate(lines[: f.lines]):
        if f.font.uppercase:
            line = line.upper()
        top = f.bbox.y0 + i * slot
        pg.text_line(line, font, size, f.font.color_rgb, f.bbox.x0, f.bbox.x1, top, top + slot, f.align)


def _draw_wrapped(pg: _Page, f: FieldSpec, text: str) -> None:
    """Párrafos con ajuste de línea.

    1) intenta caber en `f.lines` líneas reduciendo la fuente hasta 85 %;
    2) si no, admite más líneas (interlineado menor) reduciendo hasta MIN_FONT_SCALE;
    3) si aún no cabe, trunca las líneas sobrantes.
    """
    font = FONT_MAP[f.font.family]
    if f.font.uppercase:
        text = text.upper()
    width = f.bbox.width * mm
    height = f.bbox.height * mm
    base = f.font.size_pt
    size = base
    lines = wrap_text(text, font, size, width)
    while len(lines) > f.lines and size > base * 0.85:
        size = max(base * 0.85, size * 0.97)
        lines = wrap_text(text, font, size, width)
    while True:
        leading = height / max(f.lines, len(lines))
        if size * 1.1 <= leading or size <= base * MIN_FONT_SCALE:
            break
        size = max(base * MIN_FONT_SCALE, size * 0.95)
        lines = wrap_text(text, font, size, width)
    leading = height / max(f.lines, len(lines))
    if size * 1.1 > leading:  # ni al tamaño mínimo cabe: truncar
        leading = size * 1.1
        lines = lines[: max(1, int(height / leading + 1e-6))]
        leading = height / max(f.lines, len(lines))
    size = min(size, leading / 1.05)
    slot_mm = leading / mm
    for i, line in enumerate(lines):
        top = f.bbox.y0 + i * slot_mm
        pg.text_line(line, font, size, f.font.color_rgb, f.bbox.x0, f.bbox.x1, top, top + slot_mm, f.align)


def _draw_text_field(pg: _Page, f: FieldSpec, value: Any) -> None:
    if f.type == FieldType.TEXT or (f.type == FieldType.STATIC and f.lines == 1):
        text = " ".join(_as_lines(value))
        _draw_fixed_lines(pg, f.model_copy(update={"lines": 1}), [text])
    elif f.type == FieldType.MULTILINE and isinstance(value, (list, tuple)):
        _draw_fixed_lines(pg, f, [str(v) for v in value])
    else:  # STATIC multilínea o MULTILINE como str
        _draw_wrapped(pg, f, str(value))


def _mrz_geometry(f: FieldSpec) -> tuple[float, float, float]:
    """(paso mm, alto de línea mm, tamaño pt) de una zona MRZ."""
    pitch = f.bbox.width / (f.max_chars or 30)
    slot = f.bbox.height / f.lines
    font = FONT_MAP[f.font.family]
    char_w = stringWidth("M", font, 1.0)
    size = min(f.font.size_pt, pitch * mm * 0.98 / char_w, slot * mm / 1.05)
    return pitch, slot, size


def _draw_mrz_chars(pg: _Page, f_mrz: FieldSpec, line_idx: int, start: int, text: str) -> None:
    pitch, slot, size = _mrz_geometry(f_mrz)
    font = FONT_MAP[f_mrz.font.family]
    c = pg.c
    c.setFillColorRGB(*(v / 255.0 for v in f_mrz.font.color_rgb))
    c.setFont(font, size)
    center = f_mrz.bbox.y0 + (line_idx + 0.5) * slot
    base = (pg.h - center) * mm - CAP_HEIGHT * size / 2
    for i, ch in enumerate(text):
        pos = start + i
        if pos >= (f_mrz.max_chars or 30) or ch == " ":
            continue
        cx = (f_mrz.bbox.x0 + (pos + 0.5) * pitch) * mm
        c.drawCentredString(cx, base, ch)


def _embedded_in(f: FieldSpec, mrz: FieldSpec) -> bool:
    b, m, e = f.bbox, mrz.bbox, 0.01
    return b.x0 >= m.x0 - e and b.x1 <= m.x1 + e and b.y0 >= m.y0 - e and b.y1 <= m.y1 + e


def _embedded_position(f: FieldSpec, mrz: FieldSpec) -> tuple[int, int]:
    pitch, slot, _ = _mrz_geometry(mrz)
    return int((f.bbox.y0 - mrz.bbox.y0) / slot + 1e-6), round((f.bbox.x0 - mrz.bbox.x0) / pitch)


def _draw_qr(pg: _Page, f: FieldSpec, payload: str) -> None:
    x, y, w, h = pg.box(f.bbox)
    side = min(w, h)
    x += (w - side) / 2
    y += (h - side) / 2
    widget = QrCodeWidget(str(payload), barBorder=1)
    bx0, by0, bx1, by1 = widget.getBounds()
    d = Drawing(side, side, transform=[side / (bx1 - bx0), 0, 0, side / (by1 - by0), 0, 0])
    d.add(widget)
    pg.c.setFillColorRGB(1, 1, 1)
    pg.c.rect(x, y, side, side, stroke=0, fill=1)
    renderPDF.draw(d, pg.c, x, y)


def _draw_barcode(pg: _Page, f: FieldSpec, payload: str) -> None:
    x, y, w, h = pg.box(f.bbox)
    value = str(payload).encode("ascii", "replace").decode("ascii") or "0"
    probe = Code128(value, barWidth=1, barHeight=h, quiet=0, humanReadable=0)
    bw = w / probe.width
    bc = Code128(value, barWidth=bw, barHeight=h, quiet=0, humanReadable=0, barFillColor=black)
    pg.c.saveState()
    pg.c.setFillColorRGB(0, 0, 0)
    bc.drawOn(pg.c, x, y)
    pg.c.restoreState()


def _draw_label(pg: _Page, doc_type: DocType, f: FieldSpec) -> None:
    if not f.label or f.label_bbox is None:
        return
    font, color = LABEL_STYLE[doc_type]
    lines = f.label.split("\n")
    b = f.label_bbox
    slot = b.height / len(lines)
    # cuerpo ~ altura de línea (mm) * 2.5 -> mayúsculas ~ 65 % del alto, con tope por documento
    size = min(slot * 2.5, LABEL_MAX_PT[doc_type])
    for i, line in enumerate(lines):
        top = b.y0 + i * slot
        pg.text_line(line, font, size, color, b.x0, b.x1, top, top + slot, f.align)


def is_blank(value: Any) -> bool:
    """True si `value` es un valor vacío: "", lista vacía o lista de cadenas vacías."""
    if isinstance(value, (list, tuple)):
        return all(is_blank(v) for v in value)
    return isinstance(value, str) and value == ""


def _given(values: Mapping[str, Any] | None, name: str) -> bool:
    """True si el campo viene en `values` con un valor (vacío o no). `None` = ausente."""
    return values is not None and values.get(name) is not None


def _value_for(values: Mapping[str, Any] | None, f: FieldSpec, doc_type: DocType) -> tuple[Any, bool]:
    """(valor, es_placeholder).

    El placeholder sólo se usa si `values` es None o el campo no viene (clave
    ausente o valor None). Un valor vacío presente ("", [] o lista de cadenas
    vacías) se respeta: el campo se deja en blanco.
    """
    if f.type in (FieldType.STATIC, FieldType.SHAPE):
        return f.static_value, False
    if _given(values, f.name):
        return values[f.name], False
    return placeholder_value(doc_type, f), True


def draw_watermark(c: Canvas, width_mm: float, height_mm: float, text: str = WATERMARK_TEXT) -> None:
    """Marca de agua diagonal, gris semitransparente, como contenido normal de la página.

    UNA sola copia centrada sobre la diagonal de la página (tarjetas y cartas),
    que cruza la mayor parte de la diagonal; se dibuja por encima del contenido.
    """
    w, h = width_mm * mm, height_mm * mm
    diag = math.hypot(w, h)
    angle = math.degrees(math.atan2(h, w))
    font = "Helvetica-Bold"
    unit = stringWidth(text, font, 1.0)
    size = WATERMARK_DIAG_FRACTION * diag / unit
    c.saveState()
    c.setFillColorRGB(WATERMARK_GRAY, WATERMARK_GRAY, WATERMARK_GRAY)
    c.setFillAlpha(WATERMARK_ALPHA)
    c.translate(w / 2, h / 2)
    c.rotate(angle)
    c.setFont(font, size)
    c.drawCentredString(0, -CAP_HEIGHT * size / 2, text)
    c.restoreState()


def _draw_face(c: Canvas, spec: LayoutSpec, face_idx: int, values: Mapping[str, Any] | None,
               variant: Variant, watermark: bool) -> None:
    fs = spec.faces[face_idx]
    pg = _Page(c, fs.width_mm, fs.height_mm)
    c.setFillColorRGB(*(v / 255.0 for v in fs.background_rgb))
    c.rect(0, 0, fs.width_mm * mm, fs.height_mm * mm, stroke=0, fill=1)
    fields = fs.fields_for(variant)
    mrz_fields = [f for f in fields if f.type == FieldType.MRZ]
    embedded: dict[str, FieldSpec] = {}
    for f in fields:
        if f.type == FieldType.TEXT:
            for m in mrz_fields:
                if _embedded_in(f, m):
                    embedded[f.name] = m
    for f in fields:
        _draw_label(pg, spec.doc_type, f)
        value, is_ph = _value_for(values, f, spec.doc_type)
        x, y, w, h = pg.box(f.bbox)
        blank = not is_ph and f.type not in (FieldType.STATIC, FieldType.SHAPE) and is_blank(value)
        if f.type == FieldType.SHAPE:
            draw_shape(c, x, y, w, h, f.static_value)
        elif blank:
            continue  # valor vacío presente: sólo la etiqueta (ya dibujada)
        elif f.type == FieldType.PHOTO:
            draw_avatar(c, x, y, w, h, str(value), faded="secundaria" in f.name)
        elif f.type == FieldType.SIGNATURE:
            draw_signature(c, x, y, w, h, str(value))
        elif f.type == FieldType.QR:
            _draw_qr(pg, f, value)
        elif f.type == FieldType.BARCODE:
            _draw_barcode(pg, f, value)
        elif f.type == FieldType.MRZ:
            lines = _as_lines(value)
            if is_ph:
                # Deja en blanco las posiciones de los campos incrustados (CIC, OCR...).
                lines = [list(line) for line in lines]
                for name, m in embedded.items():
                    if m is f:
                        li, start = _embedded_position(fs.field(name), f)
                        n = fs.field(name).max_chars or 0
                        if li < len(lines):
                            for k in range(start, min(start + n, len(lines[li]))):
                                lines[li][k] = " "
                lines = ["".join(line) for line in lines]
            for i, line in enumerate(lines[: f.lines]):
                _draw_mrz_chars(pg, f, i, 0, line)
        elif f.name in embedded:
            m = embedded[f.name]
            mrz_given = _given(values, m.name) and not is_blank(values[m.name])
            if not mrz_given:  # si la MRZ trae valor, el texto ya está en ella
                li, start = _embedded_position(f, m)
                _draw_mrz_chars(pg, m, li, start, str(value)[: f.max_chars or None])
        elif f.type in TEXTUAL:
            _draw_text_field(pg, f, value)
    if watermark:
        draw_watermark(c, fs.width_mm, fs.height_mm)


FACE_ORDER = [Face.ANVERSO, Face.REVERSO, Face.UNICA]


def _normalize_values(values) -> dict[Face, Mapping[str, Any]]:
    if not values:
        return {}
    return {Face(k): v for k, v in values.items()}


def _new_canvas(buf: io.BytesIO, spec: LayoutSpec) -> Canvas:
    c = Canvas(buf, invariant=1, pageCompression=1)
    c.setTitle(f"{WATERMARK_TEXT} - {spec.doc_type.value} {spec.layout_version}")
    c.setSubject("Datos sintéticos de prueba (no es un documento real)")
    c.setCreator("ocr-synth src.layouts")
    c.setAuthor(WATERMARK_TEXT)
    return c


def render_document(spec: LayoutSpec, values: Mapping[Face | str, Mapping[str, Any]] | None = None,
                    variant: Variant | str | None = None, watermark: bool = True,
                    faces: list[Face] | None = None) -> bytes:
    """Renderiza la spec a PDF (una página por cara: anverso, reverso / unica).

    `values[face][field]`: TEXT str; MULTILINE/MRZ list[str]; QR/BARCODE str;
    PHOTO "avatar:<hex>"; SIGNATURE "firma:<hex>". Los faltantes se rellenan con
    placeholders. `watermark=False` es SÓLO para pruebas internas del módulo:
    PROHIBIDO en producción (la directriz exige la marca en cada página).
    `faces` permite renderizar sólo algunas caras (en el orden estándar).
    """
    var = Variant(variant) if variant is not None else default_variant(spec.doc_type)
    vals = _normalize_values(values)
    order = sorted(range(len(spec.faces)), key=lambda i: FACE_ORDER.index(spec.faces[i].face))
    if faces is not None:
        wanted = {Face(f) for f in faces}
        order = [i for i in order if spec.faces[i].face in wanted]
    buf = io.BytesIO()
    c = _new_canvas(buf, spec)
    for i in order:
        fs = spec.faces[i]
        c.setPageSize((fs.width_mm * mm, fs.height_mm * mm))
        _draw_face(c, spec, i, vals.get(fs.face), var, watermark)
        c.showPage()
    c.save()
    return buf.getvalue()


def render_template(spec: LayoutSpec, face: Face | str, values: Mapping[str, Any] | None = None,
                    variant: Variant | str | None = None, watermark: bool = True) -> bytes:
    """PDF de una sola cara (`values` = dict campo -> valor de esa cara)."""
    f = Face(face)
    return render_document(spec, {f: values or {}}, variant=variant, watermark=watermark, faces=[f])
