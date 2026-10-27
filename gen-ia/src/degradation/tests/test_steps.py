"""Probabilidades, rangos de parámetros, presets y config."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from contracts.common import Face
from contracts.degradation import GEOMETRIC_KINDS, DegradationConfig, Range, StepConfig, StepKind
from src.degradation import PRESET_NAMES, degrade_page, load_config, load_preset

from .helpers import INE_MM

ROOT = Path(__file__).resolve().parents[3]
ALL_KINDS = [k for k in StepKind if k != StepKind.RENDER]


@pytest.fixture(scope="module")
def page():
    rng = np.random.default_rng(99)
    img = np.full((638, 1012, 3), 255, np.uint8)
    img[100:300, 200:600] = rng.integers(0, 255, (200, 400, 3), dtype=np.uint8)
    return img


def run(img, steps, seed=0):
    cfg = DegradationConfig(name="t", render_dpi=300, steps=steps)
    return degrade_page(img, cfg, np.random.default_rng(seed), page_index=0, face=Face.ANVERSO,
                        face_size_mm=INE_MM, render_dpi=300)


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_probability_zero_means_untouched(page, kind):
    out, log = run(page, [StepConfig(kind=kind, probability=0.0)])
    assert np.array_equal(out, page)
    assert log.steps[0].kind == StepKind.RENDER and log.steps[0].applied
    s = log.steps[1]
    assert s.kind == kind and not s.applied and s.matrix is None and s.params == {}
    k = 300 / 25.4
    assert log.composed == [[k, 0, 0], [0, k, 0], [0, 0, 1]]
    assert log.final_size_px == (page.shape[1], page.shape[0])


def test_all_probability_zero_default_preset(page):
    cfg = load_preset("default")
    steps = [s.model_copy(update={"probability": 0.0}) for s in cfg.steps]
    out, log = run(page, steps)
    assert np.array_equal(out, page) and not any(s.applied for s in log.steps[1:])


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_probability_one_always_applied_and_params_in_range(page, kind):
    base = load_preset("heavy" if kind == StepKind.SKEW else "default")
    sc = next(s for s in base.steps if s.kind == kind).model_copy(update={"probability": 1.0})
    for seed in range(6):
        _, log = run(page, [sc], seed)
        s = log.steps[1]
        assert s.applied
        assert (s.matrix is not None) == (kind in GEOMETRIC_KINDS)
        if kind == StepKind.SKEW and sc.options.get("mode") == "perspective":
            jit = sc.params["corner_jitter"]
            keys = [f"{a}_{c}" for c in ("tl", "tr", "br", "bl") for a in ("dx", "dy")]
            assert all(jit.min <= s.params[k] <= jit.max for k in keys)
        for name, r in sc.params.items():
            if name in s.params:
                assert r.min <= s.params[name] <= r.max, (name, s.params[name], r)


def test_probability_is_respected_statistically(page):
    small = page[:40, :60]
    hits = 0
    for seed in range(400):
        _, log = degrade_page(small, DegradationConfig(steps=[StepConfig(kind=StepKind.BLUR, probability=0.3)]),
                              np.random.default_rng(seed), page_index=0, face=Face.ANVERSO,
                              face_size_mm=(5.08, 3.39), render_dpi=300)
        hits += log.steps[1].applied
    assert 0.22 < hits / 400 < 0.38


def test_missing_params_use_documented_defaults(page):
    _, log = run(page, [StepConfig(kind=StepKind.ROTATION), StepConfig(kind=StepKind.JPEG)])
    assert -3 <= log.steps[1].params["angle_deg"] <= 3
    assert 30 <= log.steps[2].params["quality"] <= 80 and isinstance(log.steps[2].params["quality"], int)


def test_canvas_placement_geometry(page):
    sc = StepConfig(kind=StepKind.CANVAS_PLACEMENT, params={"margin_mm": Range(min=10, max=10)},
                    options={"canvas": "letter"})
    out, log = run(page, [sc])
    s = log.steps[1]
    assert out.shape[:2] == (3300, 2550)  # carta a 300 dpi
    ox, oy = s.params["offset_px"]
    assert ox == round(10 * 300 / 25.4)
    assert np.array_equal(out[oy:oy + page.shape[0], ox:ox + page.shape[1]], page)
    assert out[0, 0, 0] == round(s.params["background"])


def test_canvas_enlarges_if_face_does_not_fit(page):
    big = np.full((3300, 2550, 3), 255, np.uint8)  # cara carta sobre lienzo carta
    sc = StepConfig(kind=StepKind.CANVAS_PLACEMENT, params={"margin_mm": Range(min=5, max=5)})
    out, log = degrade_page(big, DegradationConfig(steps=[sc]), np.random.default_rng(0), page_index=0,
                            face=Face.UNICA, face_size_mm=(215.9, 279.4), render_dpi=300)
    ox, oy = log.steps[1].params["offset_px"]
    assert out.shape[1] >= 2550 + 2 * ox and out.shape[0] >= 3300 + 2 * oy


def test_render_in_steps_is_rejected(page):
    with pytest.raises(ValueError):
        run(page, [StepConfig(kind=StepKind.RENDER)])


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_presets_load(name):
    cfg = load_preset(name)
    assert cfg.name == name
    if name == "none":
        assert cfg.steps == []


def test_default_preset_equals_contract_fixture():
    fx = load_config(ROOT / "contracts" / "fixtures" / "degradation_default.json")
    assert load_preset("default") == fx
    assert json.loads((ROOT / "src/degradation/presets/default.json").read_text()) == fx.model_dump(mode="json")


def test_none_preset_only_render(page):
    out, log = degrade_page(page, load_preset("none"), np.random.default_rng(0), page_index=0,
                            face=Face.ANVERSO, face_size_mm=INE_MM, render_dpi=300)
    assert np.array_equal(out, page) and [s.kind for s in log.steps] == [StepKind.RENDER]
