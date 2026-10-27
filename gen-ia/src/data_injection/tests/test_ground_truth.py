"""Ground Truth contra el contrato y determinismo (mismo proceso y subprocesos)."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest

from contracts import DocType, Face, GroundTruth, Variant, gt_field_names
from contracts.layout_spec import FieldType
from src.data_injection import MissingValueError, build_ground_truth, generate_record, inject

from .conftest import DOC_VARIANTS, REPO_ROOT, load_layout


def test_gt_validates_and_matches_catalog(doc_variant):
    doc_type, variant = doc_variant
    layout = load_layout(doc_type)
    for i in range(25):
        record, gt = inject(layout, 31, i, variant)
        again = GroundTruth.model_validate_json(gt.model_dump_json())
        assert again == gt
        assert gt.sample_id == f"{doc_type.value}-{variant.value}-31-{i:06d}"
        assert gt.doc_type == doc_type and gt.variant == variant and gt.seed == 31
        assert gt.layout_version == layout.layout_version
        assert gt.synthetic.is_synthetic and gt.synthetic.watermark_text == "MOCK DATA"
        assert gt.synthetic.faker_locale == "es_MX"
        assert [p.face for p in gt.pages] == [f.face for f in layout.faces]
        assert [p.page_index for p in gt.pages] == list(range(len(layout.faces)))
        for page, face_spec in zip(gt.pages, layout.faces):
            names = [f.name for f in page.fields]
            assert len(names) == len(set(names))
            assert set(names) == set(gt_field_names(doc_type, page.face, variant))
            assert (page.width_mm, page.height_mm) == (face_spec.width_mm, face_spec.height_mm)
            for f in page.fields:
                spec = face_spec.field(f.name)
                assert f.bbox_mm == spec.bbox and f.type == spec.type and f.transformed is None
                if spec.type == FieldType.STATIC:
                    assert f.value == spec.static_value
                else:
                    assert f.value == record[page.face][f.name]


def test_ine_variant_specific_fields():
    layout = load_layout(DocType.INE_2016)
    _, gt_n = inject(layout, 1, 0, Variant.NACIONAL)
    _, gt_e = inject(layout, 1, 0, Variant.EXTRANJERO)
    names_n = {f.name for f in gt_n.pages[0].fields}
    names_e = {f.name for f in gt_e.pages[0].fields}
    assert "leyenda_extranjero" not in names_n
    assert "leyenda_extranjero" in names_e
    assert gt_e.sample_id.startswith("ine_2016-extranjero-")


def test_missing_value_raises_clear_error():
    layout = load_layout(DocType.CURP_2019)
    record = copy.deepcopy(generate_record(DocType.CURP_2019, 1, 0))
    del record[Face.UNICA]["folio"]
    with pytest.raises(MissingValueError, match="folio"):
        build_ground_truth(layout, record, variant=None, seed=1, index=0)


def test_wrong_value_type_raises():
    layout = load_layout(DocType.INE_2016)
    record = copy.deepcopy(generate_record(DocType.INE_2016, 1, 0))
    record[Face.REVERSO]["mrz"] = "\n".join(record[Face.REVERSO]["mrz"])
    with pytest.raises(TypeError, match="mrz"):
        build_ground_truth(layout, record, variant=Variant.NACIONAL, seed=1, index=0)


def test_invalid_variant_for_layout_raises():
    layout = load_layout(DocType.PASAPORTE_ELECTRONICO_2021)
    with pytest.raises(ValueError):
        inject(layout, 1, 0, Variant.EXTRANJERO)


def _digest_all(seed: int = 123, n: int = 5) -> str:
    h = hashlib.sha256()
    for dt, v in DOC_VARIANTS:
        layout = load_layout(dt)
        for i in range(n):
            _, gt = inject(layout, seed, i, v)
            h.update(gt.model_dump_json().encode("utf-8"))
    return h.hexdigest()


def test_json_identical_between_runs(doc_variant):
    doc_type, variant = doc_variant
    layout = load_layout(doc_type)
    a = [inject(layout, 77, i, variant)[1].model_dump_json() for i in range(10)]
    # intercalar otras generaciones no debe alterar el resultado (sin estado global)
    generate_record(doc_type, 5, 5, variant)
    b = [inject(layout, 77, i, variant)[1].model_dump_json() for i in range(10)]
    assert a == b
    assert json.loads(a[0])["seed"] == 77


def test_json_identical_across_processes_and_hashseeds():
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from src.data_injection.tests.test_ground_truth import _digest_all\n"
        "print(_digest_all())\n"
    ) % str(REPO_ROOT)
    outs = set()
    for hashseed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": hashseed}
        res = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
                             capture_output=True, text=True, check=True)
        outs.add(res.stdout.strip())
    outs.add(_digest_all())
    assert len(outs) == 1


def test_different_seeds_give_different_gt(doc_variant):
    doc_type, variant = doc_variant
    layout = load_layout(doc_type)
    jsons = {inject(layout, s, 0, variant)[1].model_dump_json() for s in range(20)}
    assert len(jsons) == 20
