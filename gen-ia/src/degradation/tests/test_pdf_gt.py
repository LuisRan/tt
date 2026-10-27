"""degrade_pdf (validez, determinismo), TransformLog y project_ground_truth."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest

from contracts import GroundTruth, TransformLog, apply_matrix
from contracts.common import Face
from src.degradation import (
    degrade_page,
    degrade_pdf,
    degrade_pdf_with_images,
    effective_dpi,
    load_preset,
    project_ground_truth,
    rasterize_pdf,
)

from .helpers import INE_MM, LETTER_MM, MARKERS_INE, MARKERS_LETTER, R_INE_MM, R_LETTER_MM, ine_faces, marker_pdf

ROOT = Path(__file__).resolve().parents[3]
GT_FIXTURE = ROOT / "contracts" / "fixtures" / "ground_truth_example.json"


@pytest.fixture(scope="module")
def ine_pdf():
    return marker_pdf([(INE_MM, MARKERS_INE, R_INE_MM), (INE_MM, MARKERS_INE[::-1][:5], R_INE_MM)])


def _all_on(cfg):
    return cfg.model_copy(update={"steps": [s.model_copy(update={"probability": 1.0}) for s in cfg.steps]})


@pytest.mark.parametrize("preset", ["none", "light", "default", "heavy"])
def test_degrade_pdf_is_valid_pdf(ine_pdf, preset):
    cfg = load_preset(preset)
    pdf, log = degrade_pdf(ine_pdf, cfg, sample_id="ine_2016-1-000000", degradation_seed=3, faces=ine_faces())
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    doc = pdfium.PdfDocument(pdf)
    assert len(doc) == 2 == len(log.pages)
    for i, pl in enumerate(log.pages):
        dpi = effective_dpi(pl)
        w, h = pl.final_size_px
        pw, ph = doc[i].get_size()
        assert pw == pytest.approx(w * 72 / dpi, abs=1e-3) and ph == pytest.approx(h * 72 / dpi, abs=1e-3)
    pages = rasterize_pdf(pdf, 72)
    assert len(pages) == 2
    # Re-rasterizar al DPI efectivo reproduce el tamaño en px de la imagen final.
    for i, pl in enumerate(log.pages):
        img = rasterize_pdf(pdf, effective_dpi(pl))[i]
        assert abs(img.shape[1] - pl.final_size_px[0]) <= 1 and abs(img.shape[0] - pl.final_size_px[1]) <= 1


def test_letter_pdf(tmp_path):
    pdf = marker_pdf([(LETTER_MM, MARKERS_LETTER, R_LETTER_MM)])
    cfg = load_preset("default").model_copy(update={"render_dpi": 150})
    out, log = degrade_pdf(pdf, cfg, sample_id="curp_2019-1-000000", degradation_seed=1,
                           faces=[(Face.UNICA, LETTER_MM)])
    assert len(pdfium.PdfDocument(out)) == 1 and log.pages[0].face == Face.UNICA


def test_page_count_mismatch_raises(ine_pdf):
    with pytest.raises(ValueError):
        degrade_pdf(ine_pdf, load_preset("none"), sample_id="x", degradation_seed=0, faces=ine_faces()[:1])


def test_face_size_mismatch_raises(ine_pdf):
    with pytest.raises(ValueError):
        degrade_pdf(ine_pdf, load_preset("none"), sample_id="x", degradation_seed=0,
                    faces=[(Face.ANVERSO, LETTER_MM), (Face.REVERSO, INE_MM)])


def test_determinism_same_seed(ine_pdf):
    cfg = _all_on(load_preset("default"))
    a = degrade_pdf_with_images(ine_pdf, cfg, sample_id="s", degradation_seed=42, faces=ine_faces())
    b = degrade_pdf_with_images(ine_pdf, cfg, sample_id="s", degradation_seed=42, faces=ine_faces())
    assert a[0] == b[0]  # bytes de PDF idénticos
    assert a[1].model_dump_json() == b[1].model_dump_json()
    assert all(np.array_equal(x, y) for x, y in zip(a[2], b[2]))
    # páginas distintas usan RNG distintos
    assert a[1].pages[0].steps[1].params != a[1].pages[1].steps[1].params


def test_determinism_degrade_page():
    img = rasterize_pdf(marker_pdf([(INE_MM, MARKERS_INE, R_INE_MM)]), 300)[0]
    cfg = _all_on(load_preset("heavy"))
    kw = dict(page_index=0, face=Face.ANVERSO, face_size_mm=INE_MM, render_dpi=300)
    o1, l1 = degrade_page(img, cfg, np.random.default_rng([5, 0]), **kw)
    o2, l2 = degrade_page(img, cfg, np.random.default_rng([5, 0]), **kw)
    o3, l3 = degrade_page(img, cfg, np.random.default_rng([6, 0]), **kw)
    assert np.array_equal(o1, o2) and l1 == l2
    assert l1 != l3 and (o1.shape != o3.shape or not np.array_equal(o1, o3))


def test_different_seed_differs(ine_pdf):
    cfg = load_preset("default")
    a, la = degrade_pdf(ine_pdf, cfg, sample_id="s", degradation_seed=1, faces=ine_faces())
    b, lb = degrade_pdf(ine_pdf, cfg, sample_id="s", degradation_seed=2, faces=ine_faces())
    assert a != b and la.pages != lb.pages


def test_transform_log_contract_and_roundtrip(ine_pdf):
    cfg = load_preset("heavy")
    _, log = degrade_pdf(ine_pdf, cfg, sample_id="ine_2016-9-000001", degradation_seed=9, faces=ine_faces())
    js = log.model_dump_json()
    back = TransformLog.model_validate_json(js)
    assert back == log and back.model_dump_json() == js
    # también por json estándar (lo que escribirá T4 a disco)
    assert TransformLog.model_validate(json.loads(js)).model_dump_json() == js
    assert log.config == cfg and log.config is not cfg and log.config_name == "heavy"
    for pl in log.pages:
        assert pl.steps[0].kind.value == "render"
        assert [s.kind for s in pl.steps[1:]] == [s.kind for s in cfg.steps]
        m = np.eye(3)
        for s in pl.steps:
            if s.applied and s.matrix is not None:
                m = np.array(s.matrix) @ m
        assert np.allclose(m, np.array(pl.composed), rtol=1e-12, atol=1e-9)
        for prev, nxt in zip(pl.steps, pl.steps[1:]):
            assert prev.output_size_px == nxt.input_size_px
        assert pl.steps[-1].output_size_px == pl.final_size_px


def test_project_ground_truth_fixture(ine_pdf):
    gt = GroundTruth.model_validate_json(GT_FIXTURE.read_text())
    original = gt.model_copy(deep=True)
    _, log = degrade_pdf(ine_pdf, _all_on(load_preset("default")), sample_id=gt.sample_id,
                         degradation_seed=7, faces=[(p.face, (p.width_mm, p.height_mm)) for p in gt.pages])
    out = project_ground_truth(gt, log)
    assert gt == original  # entrada intacta
    assert out.degradation_seed == 7
    GroundTruth.model_validate(json.loads(out.model_dump_json()))
    for page, opage in zip(out.pages, original.pages):
        pl = log.pages[page.page_index]
        for f, of in zip(page.fields, opage.fields):
            assert f.bbox_mm == of.bbox_mm
            t = f.transformed
            assert t is not None
            assert (t.image_width_px, t.image_height_px) == pl.final_size_px
            for q, c in zip(t.quad_px.points, of.bbox_mm.corners()):
                e = apply_matrix(pl.composed, c)
                assert q[0] == pytest.approx(e[0], abs=1e-9) and q[1] == pytest.approx(e[1], abs=1e-9)
            xs, ys = [p[0] for p in t.quad_px.points], [p[1] for p in t.quad_px.points]
            assert (t.bbox_px.x0, t.bbox_px.y0, t.bbox_px.x1, t.bbox_px.y1) == (min(xs), min(ys), max(xs), max(ys))
            # con expand, todo campo dentro de la cara queda dentro de la imagen
            assert -1e-6 <= t.bbox_px.x0 and t.bbox_px.x1 <= pl.final_size_px[0] + 1e-6
            assert -1e-6 <= t.bbox_px.y0 and t.bbox_px.y1 <= pl.final_size_px[1] + 1e-6


def test_project_ground_truth_missing_page_raises(ine_pdf):
    gt = GroundTruth.model_validate_json(GT_FIXTURE.read_text())
    _, log = degrade_pdf(ine_pdf, load_preset("none"), sample_id="s", degradation_seed=0, faces=ine_faces())
    log.pages = log.pages[:1]
    with pytest.raises(ValueError):
        project_ground_truth(gt, log)
