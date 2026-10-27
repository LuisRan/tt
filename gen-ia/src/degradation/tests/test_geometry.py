"""Precisión geométrica: centroides detectados vs composed·p (<= 1.5 px)."""
from __future__ import annotations

import numpy as np
import pytest

from contracts.common import Face
from contracts.degradation import DegradationConfig, Range, StepConfig, StepKind
from src.degradation import degrade_page, effective_dpi, load_preset, rasterize_pdf

from .helpers import (
    INE_MM,
    LETTER_MM,
    MARKERS_INE,
    MARKERS_LETTER,
    R_INE_MM,
    R_LETTER_MM,
    marker_errors,
    marker_pdf,
)

TOL_PX = 1.5
# Pasos geométricos aislados sin ruido: el error real es ~0.05 px, así que un
# umbral estricto atrapa confusiones borde/centro (offset 0.5·(1-s) px en
# resize, 0.5 px en render/traslación) que el umbral de 1.5 px dejaría pasar.
TOL_CLEAN_PX = 0.3
MAX_ERR: list[float] = []  # error máximo observado (se imprime al final)


@pytest.fixture(scope="module")
def ine_img():
    return rasterize_pdf(marker_pdf([(INE_MM, MARKERS_INE, R_INE_MM)]), 300)[0]


@pytest.fixture(scope="module")
def letter_img():
    return rasterize_pdf(marker_pdf([(LETTER_MM, MARKERS_LETTER, R_LETTER_MM)]), 150)[0]


def one(kind, params=None, options=None):
    return DegradationConfig(name="t", render_dpi=300, steps=[StepConfig(
        kind=kind, probability=1.0,
        params={k: Range(min=a, max=b) for k, (a, b) in (params or {}).items()},
        options=options or {})])


def check(img, cfg, seed, markers, r_mm, size_mm, dpi, tol=TOL_PX):
    out, log = degrade_page(img, cfg, np.random.default_rng(seed), page_index=0, face=Face.ANVERSO,
                            face_size_mm=size_mm, render_dpi=dpi)
    assert log.final_size_px == (out.shape[1], out.shape[0])
    err = marker_errors(out, log.composed, markers, r_mm, effective_dpi(log))
    MAX_ERR.append(float(err.max()))
    assert err.max() <= tol, f"error máx {err.max():.3f}px; {err}"
    return out, log, err


def test_render_only_is_exact(ine_img):
    cfg = DegradationConfig(name="none", render_dpi=300, steps=[])
    _, log, err = check(ine_img, cfg, 0, MARKERS_INE, R_INE_MM, INE_MM, 300)
    k = 300 / 25.4
    assert log.composed == [[k, 0, 0], [0, k, 0], [0, 0, 1]]
    assert err.max() < 0.25  # sin warps el centroide debe ser casi exacto (medio píxel = 0.5)


CASES = [
    ("rot+", StepKind.ROTATION, {"angle_deg": (4.0, 4.0)}, {"expand": True}),
    ("rot-", StepKind.ROTATION, {"angle_deg": (-7.0, -7.0)}, {"expand": True}),
    ("rot_noexpand", StepKind.ROTATION, {"angle_deg": (2.0, 2.0)}, {"expand": False}),
    ("shear+", StepKind.SKEW, {"shear": (0.05, 0.05)}, {"mode": "affine"}),
    ("shear-xy", StepKind.SKEW, {"shear": (-0.04, -0.04), "shear_y": (0.03, 0.03)}, {"mode": "affine"}),
    ("perspective", StepKind.SKEW, {"corner_jitter": (-0.04, 0.04)}, {"mode": "perspective"}),
    ("dpi100", StepKind.DPI_RESAMPLE, {"target_dpi": (100.0, 100.0)}, {}),
    ("dpi150", StepKind.DPI_RESAMPLE, {"target_dpi": (150.0, 150.0)}, {}),
    ("dpi75", StepKind.DPI_RESAMPLE, {"target_dpi": (75.0, 75.0)}, {}),
    ("dpi_up", StepKind.DPI_RESAMPLE, {"target_dpi": (400.0, 400.0)}, {}),
    ("canvas", StepKind.CANVAS_PLACEMENT, {"margin_mm": (5.0, 30.0)}, {"canvas": "letter"}),
]


@pytest.mark.parametrize("name,kind,params,options", CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("seed", [1, 2])
def test_single_geometric_step_ine(ine_img, name, kind, params, options, seed):
    out, log, _ = check(ine_img, one(kind, params, options), seed, MARKERS_INE, R_INE_MM, INE_MM, 300,
                        tol=TOL_CLEAN_PX)
    assert log.steps[1].applied and log.steps[1].matrix is not None
    if kind == StepKind.SKEW and options.get("mode") == "perspective":
        m = np.array(log.steps[1].matrix)
        assert abs(m[2, 0]) + abs(m[2, 1]) > 0  # homografía no afín


@pytest.mark.parametrize("name,kind,params,options",
                         [c for c in CASES if c[0] in ("rot-", "perspective", "dpi100", "canvas")],
                         ids=["rot-", "perspective", "dpi100", "canvas"])
def test_single_geometric_step_letter(letter_img, name, kind, params, options):
    cfg = one(kind, params, options).model_copy(update={"render_dpi": 150})
    check(letter_img, cfg, 3, MARKERS_LETTER, R_LETTER_MM, LETTER_MM, 150, tol=TOL_CLEAN_PX)


def _all_on(cfg: DegradationConfig) -> DegradationConfig:
    return cfg.model_copy(update={"steps": [s.model_copy(update={"probability": 1.0}) for s in cfg.steps]})


@pytest.mark.parametrize("seed", [0, 7, 123, 2024])
def test_full_default_chain_all_steps_on(ine_img, seed):
    """Cadena completa del preset default con TODOS los pasos activos (incluye
    canvas, skew, rotación, dpi y todos los fotométricos)."""
    _, log, _ = check(ine_img, _all_on(load_preset("default")), seed, MARKERS_INE, R_INE_MM, INE_MM, 300)
    assert all(s.applied for s in log.steps)


@pytest.mark.parametrize("seed", [5, 11])
def test_full_heavy_chain_perspective(ine_img, seed):
    cfg = _all_on(load_preset("heavy"))
    # heavy usa dpi 80-200: a 80 dpi el disco de 1.2 mm mide ~3.8 px de radio, aún detectable.
    check(ine_img, cfg, seed, MARKERS_INE, R_INE_MM, INE_MM, 300)


def test_double_geometric_chain_tracks_dpi(ine_img):
    """dpi_resample dos veces + rotación entre medio: el DPI efectivo se sigue."""
    cfg = DegradationConfig(name="t", render_dpi=300, steps=[
        StepConfig(kind=StepKind.DPI_RESAMPLE, params={"target_dpi": Range(min=200, max=200)}),
        StepConfig(kind=StepKind.ROTATION, params={"angle_deg": Range(min=3, max=3)}, options={"expand": True}),
        StepConfig(kind=StepKind.DPI_RESAMPLE, params={"target_dpi": Range(min=120, max=120)}),
        StepConfig(kind=StepKind.SKEW, params={"shear": Range(min=0.02, max=0.02)}),
    ])
    _, log, _ = check(ine_img, cfg, 0, MARKERS_INE, R_INE_MM, INE_MM, 300, tol=TOL_CLEAN_PX)
    assert log.steps[3].params["dpi_in"] == 200.0
    assert effective_dpi(log) == 120.0


@pytest.mark.parametrize("kind,params,options", [
    (StepKind.ROTATION, {"angle_deg": (-8, 8)}, {"expand": True}),
    (StepKind.SKEW, {"shear": (-0.05, 0.05)}, {}),
    (StepKind.SKEW, {"corner_jitter": (-0.05, 0.05)}, {"mode": "perspective"}),
])
def test_expand_never_crops_page(ine_img, kind, params, options):
    """Directriz: la página completa (incluida la marca MOCK DATA) se conserva."""
    W, H = INE_MM
    for seed in range(10):
        _, log = degrade_page(ine_img, one(kind, params, options), np.random.default_rng(seed), page_index=0,
                              face=Face.ANVERSO, face_size_mm=INE_MM, render_dpi=300)
        m = np.array(log.composed)
        w, h = log.final_size_px
        for x, y in [(0, 0), (W, 0), (W, H), (0, H)]:
            v = m @ [x, y, 1]
            px, py = v[:2] / v[2]
            assert -1e-6 <= px <= w + 1e-6 and -1e-6 <= py <= h + 1e-6


def test_zz_report_max_error():
    print(f"\n[T3] error geométrico máximo medido: {max(MAX_ERR):.3f} px en {len(MAX_ERR)} casos")
    assert max(MAX_ERR) <= TOL_PX
