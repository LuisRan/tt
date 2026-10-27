from __future__ import annotations

import json
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from contracts import DocType
from contracts.evaluation import CampaignConfig, SampleRef
from src.evaluation import campaign
from src.evaluation.splits import verify_no_leakage

from .conftest_eval_stub import FakeOcr, install_w1_stub

ALL_ONE = {d.value: 1 for d in DocType}


def make_cfg(tmp_path: Path, per=None, presets=("none", "heavy"), **kw) -> CampaignConfig:
    return CampaignConfig.model_validate({
        "name": "t est", "seed": 7, "per_doc_type": per or ALL_ONE, "degradation_presets": list(presets),
        "out_dir": str(tmp_path / "campaigns"), "ocr": {"kind": "http", "base_url": "http://x"}, **kw})


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    seen = install_w1_stub(mp)
    tmp = tmp_path_factory.mktemp("camp")
    cfg = make_cfg(tmp)
    ocr = FakeOcr()
    out = campaign.run(cfg, client=ocr)
    yield cfg, out, ocr, seen
    mp.undo()


def test_campaign_generates_real_samples(built):
    cfg, out, ocr, seen = built
    refs = campaign.read_manifest(out.run_dir)
    assert len(refs) == 8 and out.n_samples == 8 and out.n_missing == 0
    assert out.run_id.startswith("t-est-7-")
    for r in refs:
        assert campaign.SAMPLE_ID_RE.match(r.sample_id) and r.sample_id.endswith("-" + r.degradation_preset)
        assert (out.run_dir / r.pdf_path).is_file() and (out.run_dir / r.ground_truth_path).is_file()
        assert f"samples/{r.degradation_preset}/" in r.pdf_path
        assert r.run_id == out.run_id
    groups = {}
    for r in refs:
        groups.setdefault(r.group_id, []).append(r)
    assert len(groups) == 4
    for g, rs in groups.items():
        assert {x.degradation_preset for x in rs} == {"none", "heavy"}
        assert len({x.split for x in rs}) == 1
        gts = [json.loads((out.run_dir / x.ground_truth_path).read_text())["pages"] for x in rs]
        # mismos datos: mismos valores de campo en las dos degradaciones
        vals = [[f["value"] if "value" in f else f.get("text") for p in pg for f in p["fields"]] for pg in gts]
        assert vals[0] == vals[1]
    verify_no_leakage(refs)


def test_campaign_ine_front_only(built):
    _, out, _, _ = built
    refs = campaign.read_manifest(out.run_dir)
    for r in refs:
        full = pdfium.PdfDocument(str(out.run_dir / r.pdf_path))
        sub = pdfium.PdfDocument(str(out.run_dir / r.submitted_pdf_path))
        if r.doc_type == DocType.INE_2016:
            assert len(full) == 2 and len(sub) == 1
            assert r.submitted_pdf_path.startswith("submitted/")
        else:
            assert r.submitted_pdf_path == r.pdf_path and len(sub) == len(full)


def test_campaign_layout_and_traceability(built):
    cfg, out, ocr, seen = built
    d = out.run_dir
    meta = json.loads((d / "config.json").read_text())
    assert meta["run_id"] == out.run_id and meta["config"]["seed"] == 7
    for k in ("generator_version", "contracts", "layout_version", "git_commit"):
        assert k in meta["versions"]
    preds = [json.loads(line) for line in (d / "predictions.jsonl").read_text().splitlines()]
    assert len(preds) == 8 and all(p["run_id"] == out.run_id for p in preds)
    assert (d / "results" / "report.json").is_file()
    assert {e[0] for e in seen.evaluated} and all(e[1] == e[2] for e in seen.evaluated)


def test_campaign_existing_requires_flag_and_resume_reuses(built, monkeypatch):
    cfg, out, ocr, seen = built
    with pytest.raises(campaign.CampaignExistsError):
        campaign.run(cfg, client=FakeOcr())
    monkeypatch.setattr("src.pipeline.generate_sample",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe regenerar")))
    ocr2 = FakeOcr()
    out2 = campaign.run(cfg, client=ocr2, resume=True)
    assert out2.run_dir == out.run_dir and ocr2.calls == []  # nada pendiente


def test_campaign_resume_processes_only_missing(tmp_path, monkeypatch):
    install_w1_stub(monkeypatch)
    cfg = make_cfg(tmp_path, per={"curp_2019": 2}, presets=("none",))
    ocr = FakeOcr(skip={"curp_2019-"})
    first = campaign.run(cfg, client=ocr)
    assert first.n_missing == 2
    pend = FakeOcr()
    second = campaign.run(cfg, client=pend, resume=True)
    assert len(pend.calls[0]) == 2 and second.n_missing == 0
    lines = (second.run_dir / "predictions.jsonl").read_text().splitlines()
    assert len(lines) == 2  # sin duplicados


def test_campaign_sin_respuesta_is_error_prediction(tmp_path, monkeypatch):
    seen = install_w1_stub(monkeypatch)
    cfg = make_cfg(tmp_path, per={"curp_2019": 1}, presets=("none",))
    campaign.run(cfg, client=FakeOcr(skip={"curp_2019-"}))
    assert seen.evaluated[0][3:] == ("error", "SIN_RESPUESTA")


def test_campaign_force_new_run_never_overwrites(tmp_path, monkeypatch):
    install_w1_stub(monkeypatch)
    cfg = make_cfg(tmp_path, per={"curp_2019": 1}, presets=("none",))
    a = campaign.run(cfg, client=FakeOcr())
    marker = a.run_dir / "marker.txt"
    marker.write_text("x")
    b = campaign.run(cfg, client=FakeOcr(), force_new_run=True)
    assert b.run_dir != a.run_dir and b.run_id.endswith("-r2") and marker.read_text() == "x"


def test_campaign_run_id_deterministic_and_config_sensitive(tmp_path):
    a, b = make_cfg(tmp_path), make_cfg(tmp_path)
    assert campaign.make_run_id(a) == campaign.make_run_id(b)
    assert campaign.make_run_id(a) != campaign.make_run_id(make_cfg(tmp_path, presets=("none",)))


def test_campaign_rejects_path_traversal(tmp_path):
    for bad in ("../x", "/etc/passwd", "a/../../b"):
        with pytest.raises(campaign.CampaignError):
            campaign.safe_path(tmp_path, bad)
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    ref = SampleRef(sample_id="ok-1", group_id="g", split="dev", doc_type=DocType.CURP_2019,
                    degradation_preset="none", pdf_path="../../evil.pdf", ground_truth_path="a.json",
                    submitted_pdf_path="a.pdf", run_id="r")
    (run_dir / "manifest.jsonl").write_text(ref.model_dump_json() + "\n")
    with pytest.raises(campaign.CampaignError):
        campaign.read_manifest(run_dir)
    with pytest.raises(campaign.CampaignError):
        campaign.validate_sample_id("../x")
    with pytest.raises(campaign.CampaignError):
        campaign.validate_sample_id("a" * 65)


def test_campaign_limits_and_presets(tmp_path):
    with pytest.raises(campaign.CampaignError, match="excede"):
        campaign.validate_config(make_cfg(tmp_path, per={"curp_2019": 501}), 500)
    campaign.validate_config(make_cfg(tmp_path, per={"curp_2019": 501}), 1000)
    with pytest.raises(campaign.CampaignError, match="preset"):
        campaign.validate_config(make_cfg(tmp_path, presets=("../evil",)), 500)
