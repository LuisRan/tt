"""Tests de T1 (layouts + render). Ejecutar: python -m pytest src/layouts -q"""
from __future__ import annotations

import base64
import io
import itertools
import re
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from contracts import CATALOG, FACE_SIZES_MM, WATERMARK_TEXT, DocType, Face, LayoutSpec, Variant
from contracts.layout_spec import FieldType
from src.layouts import draw_avatar, draw_signature, load_spec, render_document, render_template
from src.layouts.build_specs import spec_json
from src.layouts.render import MIN_FONT_SCALE, MIN_HSCALE, fit_line, wrap_text

LAYOUTS_DIR = Path(__file__).parents[1]
ALL_DOCS = list(DocType)
TEXTUAL = {FieldType.TEXT, FieldType.MULTILINE, FieldType.STATIC, FieldType.MRZ}


def _pages(pdf: bytes) -> pdfium.PdfDocument:
    return pdfium.PdfDocument(pdf)


def _page_text(page) -> str:
    tp = page.get_textpage()
    return tp.get_text_range()


def _overlap(a, b, eps=1e-6) -> bool:
    return a.x0 < b.x1 - eps and b.x0 < a.x1 - eps and a.y0 < b.y1 - eps and b.y0 < a.y1 - eps


def _inside(inner, outer, eps=0.01) -> bool:
    return (inner.x0 >= outer.x0 - eps and inner.x1 <= outer.x1 + eps
            and inner.y0 >= outer.y0 - eps and inner.y1 <= outer.y1 + eps)


# ------------------------------------------------------------------ specs

@pytest.mark.parametrize("dt", ALL_DOCS)
def test_spec_validates_and_matches_catalog(dt):
    spec = load_spec(dt)
    assert isinstance(spec, LayoutSpec)
    assert spec.doc_type == dt
    assert spec.layout_version == "1.0.0"
    assert spec.source_note
    assert [f.face for f in spec.faces] == list(CATALOG[dt].keys())
    w, h = FACE_SIZES_MM[dt]
    for fs in spec.faces:
        assert (fs.width_mm, fs.height_mm) == (w, h)
        by_name = {f.name: f for f in fs.fields}
        for cf in CATALOG[dt][fs.face]:
            assert cf.name in by_name, f"falta {cf.name}"
            f = by_name[cf.name]
            assert f.type == cf.type, cf.name
            assert f.in_ground_truth == cf.in_gt, cf.name
            assert (f.variants or None) == (list(cf.variants) if cf.variants else None), cf.name
        catalog_names = {cf.name for cf in CATALOG[dt][fs.face]}
        for f in fs.fields:
            if f.name not in catalog_names:
                assert f.type in (FieldType.STATIC, FieldType.SHAPE), f.name
                assert f.in_ground_truth is False, f.name


def test_ine_variant_fields():
    spec = load_spec(DocType.INE_2016)
    f = spec.face(Face.ANVERSO).field("leyenda_extranjero")
    assert f.variants == [Variant.EXTRANJERO]


@pytest.mark.parametrize("dt", ALL_DOCS)
def test_spec_json_in_sync_with_builder(dt):
    path = LAYOUTS_DIR / "specs" / f"{dt.value}.json"
    assert path.read_text(encoding="utf-8") == spec_json(dt)


@pytest.mark.parametrize("dt", ALL_DOCS)
def test_bboxes_inside_face(dt):
    for fs in load_spec(dt).faces:
        for f in fs.fields:
            for b in (f.bbox, f.label_bbox):
                if b is None:
                    continue
                assert 0 <= b.x0 < b.x1 <= fs.width_mm, f.name
                assert 0 <= b.y0 < b.y1 <= fs.height_mm, f.name


def _main_text_fields(fs):
    mrz = [f for f in fs.fields if f.type == FieldType.MRZ]
    out = []
    for f in fs.fields:
        if f.type not in TEXTUAL or not f.in_ground_truth:
            continue
        if f.type == FieldType.TEXT and any(_inside(f.bbox, m.bbox) for m in mrz):
            continue  # CIC / OCR incrustados en la MRZ (intencional)
        out.append(f)
    return out


@pytest.mark.parametrize("dt", ALL_DOCS)
def test_main_text_fields_do_not_overlap(dt):
    for fs in load_spec(dt).faces:
        fields = _main_text_fields(fs)
        for a, b in itertools.combinations(fields, 2):
            assert not _overlap(a.bbox, b.bbox), f"{fs.face.value}: {a.name} solapa {b.name}"


@pytest.mark.parametrize("dt", ALL_DOCS)
def test_labels_do_not_overlap_text_values(dt):
    for fs in load_spec(dt).faces:
        fields = _main_text_fields(fs)
        for f in fs.fields:
            if f.label_bbox is None:
                continue
            for g in fields:
                assert not _overlap(f.label_bbox, g.bbox), f"etiqueta de {f.name} solapa {g.name}"


def test_mrz_embedded_fields_align_with_char_grid():
    rev = load_spec(DocType.INE_2016).face(Face.REVERSO)
    mrz = rev.field("mrz")
    pitch = mrz.bbox.width / mrz.max_chars
    for name, start, n in (("cic", 5, 9), ("ocr", 17, 13)):
        f = rev.field(name)
        assert f.bbox.x0 == pytest.approx(mrz.bbox.x0 + start * pitch, abs=1e-3)
        assert f.bbox.x1 == pytest.approx(mrz.bbox.x0 + (start + n) * pitch, abs=1e-3)
        assert f.max_chars == n


# ------------------------------------------------------------------ render

def _render_cases():
    for dt in ALL_DOCS:
        if dt == DocType.INE_2016:
            yield dt, Variant.NACIONAL
            yield dt, Variant.EXTRANJERO
        else:
            yield dt, None


@pytest.mark.parametrize("dt,variant", list(_render_cases()))
def test_render_all_faces_with_watermark(dt, variant):
    spec = load_spec(dt)
    pdf = render_document(spec, variant=variant)
    assert pdf.startswith(b"%PDF")
    doc = _pages(pdf)
    assert len(doc) == len(spec.faces)
    w, h = FACE_SIZES_MM[dt]
    for i in range(len(doc)):
        pw, ph = doc[i].get_size()
        assert pw == pytest.approx(w * mm, abs=0.05)
        assert ph == pytest.approx(h * mm, abs=0.05)
        assert WATERMARK_TEXT in _page_text(doc[i])


def test_page_order_ine():
    pdf = render_document(load_spec(DocType.INE_2016))
    doc = _pages(pdf)
    assert "NOMBRE" in _page_text(doc[0])
    assert "NOMBRE" not in _page_text(doc[1])


@pytest.mark.parametrize("dt", ALL_DOCS)
def test_render_is_deterministic(dt):
    spec = load_spec(dt)
    assert render_document(spec) == render_document(spec)
    vals = {fs.face: {f.name: "ABC123" for f in fs.fields if f.type == FieldType.TEXT} for fs in spec.faces}
    assert render_document(spec, vals) == render_document(load_spec(dt), vals)


def test_no_annotations_or_optional_content():
    for dt in ALL_DOCS:
        pdf = render_document(load_spec(dt))
        assert b"/Annots" not in pdf
        assert b"/OCProperties" not in pdf and b"/OCG" not in pdf
        assert b"/AcroForm" not in pdf


def _gray_raster(page, dpi=100):
    return np.asarray(page.render(scale=dpi / 72).to_pil().convert("RGB")).astype(int)


@pytest.mark.parametrize("dt", ALL_DOCS)
def test_watermark_single_diagonal_text_and_raster(dt):
    """Una sola diagonal 'MOCK DATA' por página: texto extraíble y píxeles grises visibles."""
    spec = load_spec(dt)
    with_wm = _pages(render_document(spec, watermark=True))
    without = _pages(render_document(spec, watermark=False))
    for i in range(len(with_wm)):
        # Texto extraíble, una sola vez por página.
        assert _page_text(with_wm[i]).count(WATERMARK_TEXT) == 1
        assert WATERMARK_TEXT not in _page_text(without[i])
        a = _gray_raster(with_wm[i])
        b = _gray_raster(without[i])
        diff = np.abs(a - b).sum(axis=2) > 30
        hh, ww = diff.shape
        assert diff.mean() > 0.01, "marca de agua casi invisible"
        # Los píxeles cambiados son grises (neutros) y más oscuros que el fondo
        # sobre el que caen (donde éste es claro).
        changed = a[diff]
        light_bg = b[diff].min(axis=1) > 200
        assert light_bg.any()
        sat = changed[light_bg].max(axis=1) - changed[light_bg].min(axis=1)
        assert np.median(sat) < 25
        assert (b[diff][light_bg].mean(axis=1) - changed[light_bg].mean(axis=1)).min() > 10
        # Geometría: coordenadas de píxel respecto del centro, sobre/perpendicular a la diagonal
        # (de esquina inferior-izquierda a superior-derecha, como la marca).
        ys, xs = np.nonzero(diff)
        dx, dy = xs - ww / 2, (hh / 2) - ys
        diag = np.hypot(ww, hh)
        ux, uy = ww / diag, hh / diag
        along = dx * ux + dy * uy
        perp = np.abs(-dx * uy + dy * ux)
        # (1) la marca cruza la mayor parte de la diagonal (>= 60 % medido en raster; nominal 80 %)...
        assert along.max() - along.min() >= 0.60 * diag
        # (2) ...y está concentrada en una única banda diagonal central.
        assert (perp <= 0.12 * diag).mean() > 0.99
        # (3) píxeles grises distinguibles del fondo en el tramo central de la diagonal.
        strip = (np.abs(along) <= 0.25 * diag) & (perp <= 0.05 * diag)
        assert strip.sum() > 0.01 * diag * 0.1 * diag * 0.2


def test_watermark_is_tenuous_but_legible():
    from src.layouts.render import WATERMARK_ALPHA, WATERMARK_GRAY
    assert 0.15 <= WATERMARK_ALPHA <= 0.25
    spec = load_spec(DocType.CURP_2019)
    page = _pages(render_document(spec, {Face.UNICA: {}}, watermark=True))[0]
    blank = _pages(render_document(spec, watermark=False))[0]
    a, b = _gray_raster(page), _gray_raster(blank)
    diff = np.abs(a - b).sum(axis=2)
    # El gris resultante sobre fondo blanco ronda 1-alpha*(1-gray): tenue pero claro.
    on_white = (b.min(axis=2) == 255) & (diff > 30)
    assert on_white.sum() > 1000
    lum = a[on_white].mean(axis=1)
    assert 190 < np.median(lum) < 245


def test_watermark_not_removable_when_values_none_or_given():
    spec = load_spec(DocType.CURP_2019)
    for vals in (None, {Face.UNICA: {"curp": "AAAA000000HDFXXX00"}}):
        doc = _pages(render_document(spec, vals))
        assert all(WATERMARK_TEXT in _page_text(doc[i]) for i in range(len(doc)))


def test_placeholders_and_values_drawn():
    spec = load_spec(DocType.INE_2016)
    doc = _pages(render_document(spec))
    t0 = _page_text(doc[0])
    assert "X" * 18 in t0.replace("\r", "").replace("\n", "")  # clave_elector placeholder
    vals = {Face.ANVERSO: {"nombre": ["PEREZ", "LOPEZ", "ANA"], "clave_elector": "PRLPAN80010109M100"}}
    t1 = _page_text(_pages(render_document(spec, vals))[0])
    assert "PEREZ" in t1 and "PRLPAN80010109M100" in t1
    assert "X" * 4 in t1  # resto en placeholder


def _blank_cases():
    return ["", [], [""], ["", ""], ("", "")]


def _text_only_field(spec, name, **kw):
    """Texto de la cara única con todos los demás campos TEXT/MULTILINE/MRZ llenos con 'ZZ'.

    `kw`: {name: valor} (omitir la clave = campo ausente). Así 'XXXX' en la salida sólo
    puede venir del placeholder de `name`.
    """
    vals = {f.name: "ZZ" for f in spec.faces[0].fields
            if f.type in (FieldType.TEXT, FieldType.MULTILINE, FieldType.MRZ) and f.name != name}
    vals.update(kw)
    return _page_text(_pages(render_document(spec, {Face.UNICA: vals}))[0]).replace(WATERMARK_TEXT, "")


@pytest.mark.parametrize("empty", _blank_cases())
def test_empty_values_are_blank_not_placeholder(empty):
    # Pasaporte: `observaciones` vacío -> sin "XXXX..." pero con su etiqueta.
    spec = load_spec(DocType.PASAPORTE_ELECTRONICO_2021)
    f = spec.faces[0].field("observaciones")
    assert f.max_chars and f.label
    assert "XXXX" in _text_only_field(spec, "observaciones")                      # ausente -> placeholder
    text = _text_only_field(spec, "observaciones", observaciones=empty)
    assert "XXXX" not in text
    assert f.label.split("\n")[0] in text  # la etiqueta sí se dibuja


def test_empty_vs_missing_vs_none():
    spec = load_spec(DocType.PASAPORTE_ELECTRONICO_2021)
    name = "observaciones"
    assert "XXXX" in _page_text(_pages(render_document(spec))[0])               # values=None
    assert "XXXX" in _text_only_field(spec, name, **{name: None})                # None = ausente
    assert "XXXX" not in _text_only_field(spec, name, **{name: ""})
    assert "XXXX" not in _text_only_field(spec, name, **{name: []})
    assert "XXXX" not in _text_only_field(spec, name, **{name: ["", ""]})
    # Un valor vacío tampoco dibuja nada para foto/firma/QR/código de barras.
    vals_empty = {Face.UNICA: {"foto": "", "firma_portador": ""}}
    a = render_document(spec, vals_empty)
    b = render_document(spec, {Face.UNICA: {"foto": "avatar:01", "firma_portador": "firma:01"}})
    assert a != b
    pa = _gray_raster(_pages(a)[0])
    pb = _gray_raster(_pages(b)[0])
    fo = spec.faces[0].field("foto").bbox
    s = 100 / 25.4
    ph_box = pb[int(fo.y0 * s):int(fo.y1 * s), int(fo.x0 * s):int(fo.x1 * s)]
    em_box = pa[int(fo.y0 * s):int(fo.y1 * s), int(fo.x0 * s):int(fo.x1 * s)]
    assert np.abs(ph_box - em_box).sum() > 0


def _qr_modules_bbox(pdf_bytes, spec, field, payload):
    """Píxeles que cambian al renderizar `field` con `payload` frente a un QR mínimo."""
    face = spec.faces[0]
    a = _gray_raster(_pages(render_document(spec, {face.face: {field: payload}}, watermark=False))[0], dpi=150)
    b = _gray_raster(_pages(pdf_bytes)[0], dpi=150)
    return np.abs(a - b).sum(axis=2) > 30


@pytest.mark.parametrize("n", [50, 400, 900])
def test_codigo_2d_qr_long_payload_stays_in_bbox(n):
    spec = load_spec(DocType.ACTA_NACIMIENTO_2015)
    f = spec.faces[0].field("codigo_2d")
    assert f.type == FieldType.QR
    base_pdf = render_document(spec, {Face.UNICA: {"codigo_2d": "MOCK DATA"}}, watermark=False)
    payload = ("MOCK DATA " + "ACTA|ABCDEFGHIJ0123456789|" * 40)[:n]
    diff = _qr_modules_bbox(base_pdf, spec, "codigo_2d", payload)
    assert diff.sum() > 500  # el código cambia con el payload
    s = 150 / 25.4
    ys, xs = np.nonzero(diff)
    eps = 2  # px
    assert xs.min() >= f.bbox.x0 * s - eps and xs.max() <= f.bbox.x1 * s + eps
    assert ys.min() >= f.bbox.y0 * s - eps and ys.max() <= f.bbox.y1 * s + eps
    # El código grande es mucho mayor que el QR pequeño (bloque M).
    assert f.bbox.width > 2 * spec.faces[0].field("qr").bbox.width


def test_acta_codigo_2d_has_no_decorative_box():
    spec = load_spec(DocType.ACTA_NACIMIENTO_2015)
    assert "codigo_bidimensional" not in {f.name for f in spec.faces[0].fields}


def test_variant_controls_fields():
    spec = load_spec(DocType.INE_2016)
    nac = _page_text(_pages(render_document(spec, variant=Variant.NACIONAL))[0])
    ext = _page_text(_pages(render_document(spec, variant="extranjero"))[0])
    assert "EXTRANJERO" not in nac
    assert "PARA VOTAR DESDE EL EXTRANJERO" in ext


def test_static_texts_rendered():
    curp = _page_text(_pages(render_document(load_spec(DocType.CURP_2019)))[0])
    assert "Agradezco tu participación." in curp
    assert "https://renapo.gob.mx/" in curp  # última línea de la leyenda (no truncada)
    acta = _page_text(_pages(render_document(load_spec(DocType.ACTA_NACIMIENTO_2015)))[0])
    assert "Identificador Electrónico" in acta


def test_mrz_with_values_and_embedded_fields():
    spec = load_spec(DocType.INE_2016)
    mrz = ["IDMEX1234567897<<0009876543210", "8001019M3012315MEX<02<<12345<7", "PEREZ<LOPEZ<<ANA<<<<<<<<<<<<<<"]
    vals = {Face.REVERSO: {"mrz": mrz, "cic": "123456789", "ocr": "0009876543210"}}
    text = _page_text(_pages(render_document(spec, vals))[1])
    # La MRZ se dibuja con paso fijo: sin repetir CIC encima de la línea 1.
    assert "IDMEX1234567897<<0009876543210" in text
    assert text.count("123456789") == 1 and text.count("0009876543210") == 1
    # Sin MRZ: CIC se dibuja en su posición de la rejilla y el resto es placeholder.
    text2 = _page_text(_pages(render_document(spec, {Face.REVERSO: {"cic": "123456789"}}))[1])
    assert "123456789" in text2


def test_render_template_single_face():
    spec = load_spec(DocType.INE_2016)
    doc = _pages(render_template(spec, "reverso"))
    assert len(doc) == 1
    assert WATERMARK_TEXT in _page_text(doc[0])


def test_long_values_are_fitted():
    spec = load_spec(DocType.PASAPORTE_ELECTRONICO_2021)
    long = "W" * 300
    vals = {Face.UNICA: {f.name: long for f in spec.faces[0].fields if f.type == FieldType.TEXT}}
    pdf = render_document(spec, vals)
    page = _pages(pdf)[0]
    tp = page.get_textpage()
    ph = page.get_height()
    face = spec.faces[0]
    # Cada caracter 'W' cae dentro del bbox de algún campo TEXT (en pt).
    boxes = [(f.bbox.x0 * mm, ph - f.bbox.y1 * mm, f.bbox.x1 * mm, ph - f.bbox.y0 * mm)
             for f in face.fields if f.type == FieldType.TEXT]
    n = tp.count_chars()
    checked = 0
    for i in range(n):
        if tp.get_text_range(i, 1) != "W":
            continue
        l, b, r, t = tp.get_charbox(i)
        cx, cy = (l + r) / 2, (b + t) / 2
        assert any(x0 - 0.5 <= l and r <= x1 + 0.5 and y0 - 1 <= cy <= y1 + 1 for x0, y0, x1, y1 in boxes)
        checked += 1
    assert checked > 50


@pytest.mark.parametrize("text", ["X" * 200, "ANA MARIA DE LOS ANGELES " * 5, "a", ""])
def test_fit_line_never_exceeds(text):
    width = 40 * mm
    out, size, hs = fit_line(text, "Helvetica", 10, width)
    assert stringWidth(out, "Helvetica", size) * hs <= width + 1e-6
    assert size >= 10 * MIN_FONT_SCALE - 1e-6
    assert hs >= MIN_HSCALE - 1e-6
    assert text.startswith(out)


def test_wrap_text_keeps_paragraphs():
    lines = wrap_text("uno dos tres\n\ncuatro", "Helvetica", 10, 1000)
    assert lines == ["uno dos tres", "", "cuatro"]


# ------------------------------------------------------------------ avatar / firma

def _draw_to_bytes(fn, ident):
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=(100, 120), invariant=1)
    fn(c, 5, 5, 90, 110, ident)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.parametrize("fn,prefix", [(draw_avatar, "avatar:"), (draw_signature, "firma:")])
def test_avatar_signature_deterministic(fn, prefix):
    a = _draw_to_bytes(fn, prefix + "0a1b2c")
    assert a == _draw_to_bytes(fn, prefix + "0a1b2c")
    assert a != _draw_to_bytes(fn, prefix + "ffee99")


def test_photo_and_signature_values_change_output():
    spec = load_spec(DocType.PASAPORTE_ELECTRONICO_2021)
    a = render_document(spec, {Face.UNICA: {"foto": "avatar:01", "firma_portador": "firma:01"}})
    b = render_document(spec, {Face.UNICA: {"foto": "avatar:02", "firma_portador": "firma:02"}})
    assert a != b


# ------------------------------------------------------------------ directriz

RASTER_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
# Términos codificados para que este archivo no los contenga literalmente.
_FORBIDDEN_B64 = [
    "Z3VpbGxvY2hl", "bWljcm90ZXh0", "aG9sb2dy", "YXJjb2lyaXM=", "YXJjb8OtcmlzIA==", "cmFpbmJvdw==",
    "ZmFudGFzbWE=", "a2luZWdyYW0=", "cmVsaWV2ZQ==",
]
FORBIDDEN = [base64.b64decode(t).decode("utf-8").strip() for t in _FORBIDDEN_B64]
FORBIDDEN_WORDS = [base64.b64decode(t).decode("utf-8") for t in ("T1ZJ", "VVY=")]  # palabra completa


def _repo_files():
    return [p for p in LAYOUTS_DIR.rglob("*") if p.is_file() and "__pycache__" not in p.parts]


def test_no_raster_images_in_layouts():
    bad = [p for p in _repo_files() if p.suffix.lower() in RASTER_EXT]
    assert not bad, bad
    for p in _repo_files():
        head = p.read_bytes()[:8]
        assert not head.startswith(b"\x89PNG") and not head.startswith(b"\xff\xd8\xff"), p


def test_no_security_feature_terms():
    for p in _repo_files():
        if p.suffix not in {".py", ".json", ".md", ".txt"}:
            continue
        text = p.read_text(encoding="utf-8").lower()
        for term in FORBIDDEN:
            assert term.lower() not in text, f"{p.name}: término prohibido"
        for word in FORBIDDEN_WORDS:
            assert not re.search(rf"\b{word.lower()}\b", text), f"{p.name}: término prohibido"
