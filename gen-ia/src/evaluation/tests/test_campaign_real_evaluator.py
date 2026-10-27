"""Regresión de integración W1+W2: la campaña usa el evaluador REAL (sin stub).

Antes, el id de campaña (`<id_generador>-<preset>`) no coincidía con el del GT y
`evaluate_sample` abortaba; las pruebas con stub no lo detectaban.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from contracts.evaluation import CampaignConfig
from src.evaluation import campaign

from .conftest_eval_stub import FakeOcr


def _cfg(tmp_path: Path) -> CampaignConfig:
    return CampaignConfig.model_validate({
        "name": "real-eval", "seed": 11, "per_doc_type": {"curp_2019": 1},
        "degradation_presets": ["none", "heavy"], "out_dir": str(tmp_path / "campaigns"),
        "ocr": {"kind": "http", "base_url": "http://x"}})


def test_campaign_runs_with_real_evaluator_and_preset_suffixed_ids(tmp_path):
    out = campaign.run(_cfg(tmp_path), client=FakeOcr())
    refs = campaign.read_manifest(out.run_dir)
    assert len(refs) == 2 and all(r.sample_id.endswith(("-none", "-heavy")) for r in refs)
    report = campaign.evaluate_run(out.run_dir)
    assert report is not None
    assert (out.run_dir / "results" / "report.json").is_file()


def test_evaluate_run_rejects_ground_truth_of_another_sample(tmp_path):
    out = campaign.run(_cfg(tmp_path), client=FakeOcr())
    ref = campaign.read_manifest(out.run_dir)[0]
    gt_file = out.run_dir / ref.ground_truth_path
    gt_file.write_text(gt_file.read_text(encoding="utf-8").replace(
        f'"sample_id": "{ref.sample_id.rsplit("-", 1)[0]}"', '"sample_id": "otra-muestra-000000"', 1),
        encoding="utf-8")
    with pytest.raises(campaign.CampaignError):
        campaign.evaluate_run(out.run_dir)
