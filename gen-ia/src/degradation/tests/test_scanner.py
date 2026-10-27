"""canvas_placement con position="random" y preset `scanner`."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from contracts.common import Face
from contracts.degradation import DegradationConfig, Range, StepConfig, StepKind
from src.degradation import degrade_page, degrade_pdf_with_images, effective_dpi, load_preset, rasterize_pdf

from .golden import (
    GOLDEN_FORCED,
    GOLDEN_NAMES,
    GOLDEN_PLATFORM,
    GOLDEN_SEEDS,
    META_KEY,
    PACKAGES,
    SKIP_REASON,
    capture_case,
    case_key,
    golden_applicable,
)
from .helpers import (
    INE_MM,
    LETTER_MM,
    MARKERS_INE,
    MARKERS_LETTER,
    R_INE_MM,
    R_LETTER_MM,
    ine_faces,
    marker_errors,
    marker_pdf,
)

HERE = Path(__file__).parent
SEEDS = range(40)
MM = 25.4
MAX_ERR: list[float] = []


def _face_img(size_mm, dpi=300):
    w, h = int(np.ceil(size_mm[0] * dpi / MM)), int(np.ceil(size_mm[1] * dpi / MM))
    img = np.full((h, w, 3), 255, np.uint8)
    img[h // 3:h // 2, w // 4:w // 2] = 40
    return img


def _canvas_cfg(margin, position="random", canvas="letter", margin_y=None):
    params = {"margin_mm": Range(min=margin[0], max=margin[1])}
    if margin_y is not None:
        params["margin_y_mm"] = Range(min=margin_y, max=margin_y)
    return DegradationConfig(name="t", render_dpi=300, steps=[StepConfig(
        kind=StepKind.CANVAS_PLACEMENT, probability=1.0, params=params,
        options={"canvas": canvas, "position": position})])


def _place(img, cfg, seed, size_mm):
    return degrade_page(img, cfg, np.random.default_rng(seed), page_index=0, face=Face.UNICA,
                        face_size_mm=size_mm, render_dpi=300)


# ------------------------------------------------------------- position=random
@pytest.mark.parametrize("size_mm,margin", [
    (INE_MM, (0.0, 0.0)), (INE_MM, (5.0, 30.0)), (INE_MM, (0.0, 5.0)),
    (LETTER_MM, (0.0, 0.0)), (LETTER_MM, (5.0, 5.0)), (LETTER_MM, (0.0, 10.0)),
])
def test_random_position_face_always_inside_canvas(size_mm, margin):
    img = _face_img(size_mm)
    h, w = img.shape[:2]
    cfg = _canvas_cfg(margin)
    for seed in SEEDS:
        out, log = _place(img, cfg, seed, size_mm)
        s = log.steps[1]
        ox, oy = s.params["offset_px"]
        mx = round(s.params["margin_mm"] * 300 / MM)
        my = round(s.params["margin_y_mm"] * 300 / MM)
        ch, cw = out.shape[:2]
        # margen mínimo respetado y la cara entera dentro del lienzo
        assert ox >= mx and oy >= my
        assert ox + w + mx <= cw and oy + h + my <= ch
        assert np.array_equal(out[oy:oy + h, ox:ox + w], img)  # sin recortar ni remuestrear
        # matriz de traslación exacta
        assert np.array_equal(np.array(s.matrix), np.array([[1, 0, ox], [0, 1, oy], [0, 0, 1]], float))
        assert log.composed[0][2] == pytest.approx(ox) and log.composed[1][2] == pytest.approx(oy)
        # params registrados: offset en px y mm coherentes
        assert s.params["position"] == "random"
        assert s.params["offset_mm"] == pytest.approx([ox * MM / 300, oy * MM / 300])


def test_random_position_letter_on_letter_canvas_has_zero_slack():
    # Cara carta sobre lienzo carta con margen 0: solo hay una posición posible
    # (o el lienzo crece, como ya hacía el código; nunca recorta).
    img = _face_img(LETTER_MM)
    for seed in range(10):
        out, log = _place(img, _canvas_cfg((0.0, 0.0)), seed, LETTER_MM)
        ox, oy = log.steps[1].params["offset_px"]
        assert out.shape[1] >= img.shape[1] + ox and out.shape[0] >= img.shape[0] + oy
        assert ox == 0 and oy == 0  # margen 0: la única posición posible


def test_random_position_is_actually_random_and_spans_canvas():
    img = _face_img(INE_MM)
    cfg = _canvas_cfg((2.0, 2.0))
    offs = np.array([_place(img, cfg, s, INE_MM)[1].steps[1].params["offset_px"] for s in range(200)])
    ch, cw = 3300, 2550
    assert len(set(map(tuple, offs))) > 150
    lo_x, hi_x = round(2 * 300 / MM), cw - img.shape[1] - round(2 * 300 / MM)
    lo_y, hi_y = round(2 * 300 / MM), ch - img.shape[0] - round(2 * 300 / MM)
    assert offs[:, 0].min() < lo_x + 0.1 * (hi_x - lo_x) and offs[:, 0].max() > hi_x - 0.1 * (hi_x - lo_x)
    assert offs[:, 1].min() < lo_y + 0.1 * (hi_y - lo_y) and offs[:, 1].max() > hi_y - 0.1 * (hi_y - lo_y)
    # uniforme: la media cae hacia el centro del rango
    assert abs(offs[:, 0].mean() - (lo_x + hi_x) / 2) < 0.1 * (hi_x - lo_x)
    assert abs(offs[:, 1].mean() - (lo_y + hi_y) / 2) < 0.1 * (hi_y - lo_y)


def test_random_position_is_deterministic():
    img = _face_img(INE_MM)
    cfg = _canvas_cfg((0.0, 5.0))
    a, la = _place(img, cfg, 5, INE_MM)
    b, lb = _place(img, cfg, 5, INE_MM)
    c, lc = _place(img, cfg, 6, INE_MM)
    assert np.array_equal(a, b) and la == lb
    assert la.steps[1].params["offset_px"] != lc.steps[1].params["offset_px"]


def test_default_position_is_margin_and_unchanged():
    img = _face_img(INE_MM)
    explicit = _canvas_cfg((10.0, 10.0), position="margin", margin_y=10.0)
    implicit = DegradationConfig(name="t", render_dpi=300, steps=[StepConfig(
        kind=StepKind.CANVAS_PLACEMENT, params={"margin_mm": Range(min=10, max=10),
                                                "margin_y_mm": Range(min=10, max=10)},
        options={"canvas": "letter"})])
    a, la = _place(img, explicit, 0, INE_MM)
    b, lb = _place(img, implicit, 0, INE_MM)
    assert np.array_equal(a, b)
    s = lb.steps[1]
    assert s.params["offset_px"] == [118, 118] and s.params["position"] == "margin"
    assert s.params["offset_mm"] == pytest.approx([118 * MM / 300] * 2)


def test_margin_mode_does_not_consume_extra_rng():
    # Con "margin" el rng de pasos posteriores no cambia respecto a no tener opción.
    img = _face_img(INE_MM)
    steps = [StepConfig(kind=StepKind.CANVAS_PLACEMENT, params={"margin_mm": Range(min=5, max=9)},
                        options={"position": "margin"}),
             StepConfig(kind=StepKind.GAUSSIAN_NOISE)]
    steps2 = [steps[0].model_copy(update={"options": {}}), steps[1]]
    a, _ = _place(img, DegradationConfig(steps=steps), 3, INE_MM)
    b, _ = _place(img, DegradationConfig(steps=steps2), 3, INE_MM)
    assert np.array_equal(a, b)


def test_unknown_position_rejected():
    with pytest.raises(ValueError):
        _place(_face_img(INE_MM), _canvas_cfg((5, 5), position="centro"), 0, INE_MM)


# -------------------------------------------------------------- preset scanner
def test_scanner_preset_loads_and_has_expected_steps():
    cfg = load_preset("scanner")
    assert cfg.name == "scanner"
    by = {s.kind: s for s in cfg.steps}
    assert by[StepKind.CANVAS_PLACEMENT].probability == 1.0
    assert by[StepKind.CANVAS_PLACEMENT].options == {"canvas": "letter", "position": "random"}
    assert (by[StepKind.ROTATION].params["angle_deg"].min, by[StepKind.ROTATION].params["angle_deg"].max) == (-2, 2)
    assert (by[StepKind.DPI_RESAMPLE].params["target_dpi"].min, by[StepKind.DPI_RESAMPLE].params["target_dpi"].max) == (150, 300)
    assert (by[StepKind.JPEG].params["quality"].min, by[StepKind.JPEG].params["quality"].max) == (50, 85)
    assert StepKind.ILLUMINATION_SHADOW in by and StepKind.GAUSSIAN_NOISE in by


@pytest.fixture(scope="module")
def ine_pdf():
    return marker_pdf([(INE_MM, MARKERS_INE, R_INE_MM), (INE_MM, MARKERS_INE[::-1][:5], R_INE_MM)])


def test_scanner_marker_accuracy_ine(ine_pdf):
    cfg = load_preset("scanner")
    for seed in range(12):
        _, log, imgs = degrade_pdf_with_images(ine_pdf, cfg, sample_id="s", degradation_seed=seed, faces=ine_faces())
        for i, (pl, img) in enumerate(zip(log.pages, imgs)):
            assert all(s.applied for s in pl.steps)
            markers = MARKERS_INE if i == 0 else MARKERS_INE[::-1][:5]
            err = marker_errors(img, pl.composed, markers, R_INE_MM, effective_dpi(pl))
            MAX_ERR.append(float(err.max()))
            assert err.max() <= 1.5, f"seed {seed} pág {i}: {err}"


def test_scanner_marker_accuracy_letter():
    pdf = marker_pdf([(LETTER_MM, MARKERS_LETTER, R_LETTER_MM)])
    cfg = load_preset("scanner")
    for seed in range(6):
        _, log, imgs = degrade_pdf_with_images(pdf, cfg, sample_id="s", degradation_seed=seed,
                                               faces=[(Face.UNICA, LETTER_MM)])
        pl = log.pages[0]
        err = marker_errors(imgs[0], pl.composed, MARKERS_LETTER, R_LETTER_MM, effective_dpi(pl))
        MAX_ERR.append(float(err.max()))
        assert err.max() <= 1.5, f"seed {seed}: {err}"


def test_scanner_face_inside_final_image(ine_pdf):
    cfg = load_preset("scanner")
    for seed in range(20):
        _, log, _ = degrade_pdf_with_images(ine_pdf, cfg, sample_id="s", degradation_seed=seed, faces=ine_faces())
        for pl in log.pages:
            w, h = pl.final_size_px
            m = np.array(pl.composed)
            for x, y in ((0, 0), (INE_MM[0], 0), (INE_MM[0], INE_MM[1]), (0, INE_MM[1])):
                v = m @ np.array([x, y, 1.0])
                assert 0 <= v[0] <= w and 0 <= v[1] <= h


def test_scanner_deterministic(ine_pdf):
    cfg = load_preset("scanner")
    kw = dict(sample_id="s", faces=ine_faces())
    a, la = degrade_pdf_with_images(ine_pdf, cfg, degradation_seed=11, **kw)[:2]
    b, lb = degrade_pdf_with_images(ine_pdf, cfg, degradation_seed=11, **kw)[:2]
    c, _ = degrade_pdf_with_images(ine_pdf, cfg, degradation_seed=12, **kw)[:2]
    assert a == b and la == lb and a != c


def test_scanner_pdf_reraster_matches_grid(ine_pdf):
    from src.degradation import degrade_pdf
    pdf, log = degrade_pdf(ine_pdf, load_preset("scanner"), sample_id="s", degradation_seed=2, faces=ine_faces())
    for i, pl in enumerate(log.pages):
        img = rasterize_pdf(pdf, effective_dpi(pl))[i]
        assert abs(img.shape[1] - pl.final_size_px[0]) <= 1 and abs(img.shape[0] - pl.final_size_px[1]) <= 1


# ------------------------------------------------- presets existentes intactos
@pytest.mark.skipif(not golden_applicable(), reason=SKIP_REASON)
@pytest.mark.parametrize("name", GOLDEN_NAMES)
@pytest.mark.parametrize("forced", GOLDEN_FORCED)
@pytest.mark.parametrize("seed", GOLDEN_SEEDS)
def test_existing_presets_unchanged(ine_pdf, name, forced, seed):
    """Bytes del PDF, imágenes y geometría idénticos a los capturados antes de
    añadir position/scanner (golden_presets.json). `forced` pone todas las
    probabilidades a 1 (incluido canvas_placement en modo margin por defecto).

    Los bytes dependen de la plataforma: el golden está atado al contenedor
    linux/amd64 y este test solo corre allí (OCR_DOCKER=1 + x86_64); fuera se omite.
    """
    golden = json.loads((HERE / "golden_presets.json").read_text())[case_key(name, forced, seed)]
    assert capture_case(ine_pdf, name, forced, seed) == golden


def test_golden_metadata_declares_platform():
    """El golden declara con qué plataforma/versiones se capturó (corre en todas partes)."""
    meta = json.loads((HERE / "golden_presets.json").read_text())[META_KEY]
    assert meta["platform"] == GOLDEN_PLATFORM
    assert set(meta["versions"]) == set(PACKAGES)


def test_zz_report_max_error():
    if not MAX_ERR:
        pytest.skip("sin medidas")
    print(f"\nscanner: error máximo de marcadores = {max(MAX_ERR):.3f} px ({len(MAX_ERR)} páginas)")
