"""GUARDRAIL de la directriz crítica: la marca de agua "MOCK DATA" de punta a punta.

1. El PDF limpio de cada documento lleva "MOCK DATA" en CADA página.
2. La marca sigue presente (quemada) en el PDF degradado (raster).
3. Si se fuerza un render sin marca, el pipeline falla y no escribe nada.
"""
from __future__ import annotations

import re

import numpy as np
import pypdfium2 as pdfium
import pytest

from contracts import DocType, Face
from src import layouts
from src.data_injection import inject
from src.degradation import degrade_pdf, load_preset, rasterize_pdf
from src.pipeline import WatermarkMissingError, derive_degradation_seed, generate_sample

DOC_TYPES = [
    pytest.param(d.value, id=d.value)
    for d in DocType
]


def _page_texts(pdf: bytes) -> list[str]:
    doc = pdfium.PdfDocument(pdf)
    try:
        out = []
        for page in doc:
            tp = page.get_textpage()
            out.append(tp.get_text_bounded())
        return out
    finally:
        doc.close()


def _render(doc_type: str, seed: int, *, watermark: bool):
    """Render limpio con valores sintéticos. `watermark=False` SOLO para comparar en tests."""
    spec = layouts.load_spec(doc_type)
    record, gt = inject(spec, seed, 0)
    pdf = layouts.render_document(spec, record, watermark=watermark)
    faces = [(p.face, (p.width_mm, p.height_mm)) for p in gt.pages]
    return pdf, gt, faces


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_clean_pdf_has_mock_data_on_every_page(doc_type, tmp_path):
    r = generate_sample(doc_type, 21, 0, degradation="none", out_dir=tmp_path, extras=True)
    texts = _page_texts(r.path(r.clean_pdf).read_bytes())
    assert len(texts) == len(r.ground_truth.pages) >= 1
    for i, t in enumerate(texts):
        assert "MOCKDATA" in re.sub(r"\s+", "", t).upper(), f"falta MOCK DATA en la página {i}"


def _diagonal_band_mask(h: int, w: int, half_width_frac: float = 0.15):
    """Banda alrededor de cada diagonal de la imagen (distancia perpendicular)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    diag = float(np.hypot(w, h))
    bands = []
    for sign in (1.0, -1.0):
        # sign=1: recta (0, h)-(w, 0); sign=-1: recta (0, 0)-(w, h)
        if sign > 0:
            dist = np.abs(h * xx + w * yy - w * h) / diag
        else:
            dist = np.abs(h * xx - w * yy) / diag
        bands.append(dist < half_width_frac * diag)
    return bands[0], bands[1]


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_watermark_survives_degradation(doc_type):
    """Mismos valores con y sin marca (opción interna de layouts, SOLO aquí) -> tras degradar
    con el preset `none` debe haber una diferencia significativa en la región de la diagonal."""
    with_wm, gt, faces = _render(doc_type, 33, watermark=True)
    without_wm, _, _ = _render(doc_type, 33, watermark=False)
    cfg = load_preset("none")
    seed = derive_degradation_seed(33, 0)
    kw = dict(sample_id="wm-test", degradation_seed=seed, faces=faces)
    deg_a, log_a = degrade_pdf(with_wm, cfg, **kw)
    deg_b, _ = degrade_pdf(without_wm, cfg, **kw)
    imgs_a = rasterize_pdf(deg_a, cfg.render_dpi)
    imgs_b = rasterize_pdf(deg_b, cfg.render_dpi)
    assert len(imgs_a) == len(imgs_b) == len(faces)
    for i, (a, b) in enumerate(zip(imgs_a, imgs_b)):
        assert a.shape == b.shape
        diff = np.abs(a.astype(np.int16) - b.astype(np.int16)).mean(axis=2)
        h, w = diff.shape
        band_tl, band_bl = _diagonal_band_mask(h, w)
        best = best_mean = 0.0
        for band in (band_tl, band_bl):  # la marca va en una diagonal de la página
            region = diff[band]
            best = max(best, float((region >= 10).mean()))
            best_mean = max(best_mean, float(region.mean()))
        assert best >= 0.005 and best_mean >= 0.5, (
            f"página {i}: la marca no se aprecia tras degradar "
            f"(píxeles distintos en la diagonal: {best:.4f}, diferencia media: {best_mean:.3f})"
        )
        # sanity: la propia imagen degradada contiene tinta de la marca (gris medio) en la banda
        assert diff.max() >= 10


def test_watermark_identical_renders_have_no_diff():
    """Control del test anterior: dos renders con marca son idénticos (diff = 0)."""
    a, _, faces = _render("curp_2019", 33, watermark=True)
    b, _, _ = _render("curp_2019", 33, watermark=True)
    assert a == b


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_pipeline_fails_without_watermark(doc_type, tmp_path, monkeypatch):
    real = layouts.render_document

    def no_watermark(*args, **kwargs):
        kwargs["watermark"] = False
        return real(*args, **kwargs)

    monkeypatch.setattr(layouts, "render_document", no_watermark)
    out = tmp_path / "out"
    with pytest.raises(WatermarkMissingError):
        generate_sample(doc_type, 1, 0, out_dir=out)
    assert not out.exists() or not any(out.rglob("*")), "no debe escribirse ningún archivo"


def test_pipeline_fails_if_a_single_page_lacks_watermark(tmp_path, monkeypatch):
    """Basta con que falte en UNA página: se renderiza el anverso con marca y el reverso sin ella."""
    real = layouts.render_document

    def partial(spec, values=None, variant=None, watermark=True, faces=None):
        front = real(spec, values, variant=variant, watermark=True, faces=[Face.ANVERSO])
        back = real(spec, values, variant=variant, watermark=False, faces=[Face.REVERSO])
        # PDF de dos páginas: se concatena con pypdfium2
        doc = pdfium.PdfDocument(front)
        other = pdfium.PdfDocument(back)
        doc.import_pages(other)
        import io
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    monkeypatch.setattr(layouts, "render_document", partial)
    out = tmp_path / "out"
    with pytest.raises(WatermarkMissingError):
        generate_sample("ine_2016", 1, 0, out_dir=out)
    assert not out.exists() or not any(out.rglob("*"))
