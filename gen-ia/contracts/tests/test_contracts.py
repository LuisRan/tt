import json
import math
from pathlib import Path

import pytest

from contracts import (
    CATALOG,
    BBox,
    DegradationConfig,
    DocType,
    GroundTruth,
    LayoutSpec,
    matmul,
    project_bbox,
)
from contracts.common import Face, Variant
from contracts.field_catalog import gt_field_names
from contracts.build_artifacts import default_degradation, example_ground_truth, fixture_layout

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.mark.parametrize("dt", list(DocType))
def test_fixture_layouts_cover_catalog(dt):
    layout = fixture_layout(dt)
    for face in layout.faces:
        expected = {f.name for f in CATALOG[dt][face.face]}
        assert {f.name for f in face.fields} == expected


@pytest.mark.parametrize("dt", list(DocType))
def test_committed_fixtures_roundtrip(dt):
    data = json.loads((FIXTURES / f"layout_{dt.value}.json").read_text())
    assert LayoutSpec.model_validate(data) == fixture_layout(dt)


def test_degradation_and_gt_fixtures_roundtrip():
    cfg = json.loads((FIXTURES / "degradation_default.json").read_text())
    assert DegradationConfig.model_validate(cfg) == default_degradation()
    gt = json.loads((FIXTURES / "ground_truth_example.json").read_text())
    assert GroundTruth.model_validate(gt) == example_ground_truth()


def test_bbox_rejects_inverted():
    with pytest.raises(ValueError):
        BBox(x0=10, y0=0, x1=5, y1=1)


def test_project_bbox_render_then_rotate():
    dpi = 300
    s = dpi / 25.4
    render = [[s, 0, 0], [0, s, 0], [0, 0, 1]]
    a = math.radians(90)
    rot = [[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]]
    q = project_bbox(matmul(rot, render), BBox(x0=0, y0=0, x1=25.4, y1=25.4 * 2))
    tl, tr, br, bl = q.points
    assert tl == pytest.approx((0, 0), abs=1e-9)
    assert tr == pytest.approx((0, 300), abs=1e-6)
    assert bl == pytest.approx((-600, 0), abs=1e-6)


def test_ine_variants():
    nac = gt_field_names(DocType.INE_2016, Face.ANVERSO, Variant.NACIONAL)
    ext = gt_field_names(DocType.INE_2016, Face.ANVERSO, Variant.EXTRANJERO)
    assert "leyenda_extranjero" not in nac
    assert set(ext) - set(nac) == {"leyenda_extranjero"}
    assert gt_field_names(DocType.INE_2016, Face.ANVERSO) == nac  # nacional es el default
