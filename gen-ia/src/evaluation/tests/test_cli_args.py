from __future__ import annotations

import json

import pytest

from src.evaluation import campaign
from src.evaluation.cli import build_parser, main

from .conftest_eval_stub import FakeOcr, install_w1_stub


def write_cfg(tmp_path, **over):
    cfg = {"name": "cli", "seed": 3, "per_doc_type": {"curp_2019": 1}, "degradation_presets": ["none"],
           "out_dir": str(tmp_path / "out"), "ocr": {"kind": "http", "base_url": "http://x"}, **over}
    p = tmp_path / "campaign.json"
    p.write_text(json.dumps(cfg))
    return p


def test_cli_parser_subcommands():
    p = build_parser()
    a = p.parse_args(["run", "--config", "c.json", "--resume", "--force-new-run", "--max-per-type", "5"])
    assert (a.command, a.resume, a.force_new_run, a.max_per_type) == ("run", True, True, 5)
    assert p.parse_args(["report", "--run-dir", "d"]).run_dir == "d"
    c = p.parse_args(["compare", "--baseline", "a", "--candidate", "b"])
    assert (c.baseline, c.candidate) == ("a", "b")


def test_cli_usage_errors_exit_2(capsys):
    assert main([]) == 2
    assert main(["run"]) == 2
    assert main(["compare", "--baseline", "a"]) == 2


def test_cli_run_report_compare(tmp_path, monkeypatch, capsys):
    seen = install_w1_stub(monkeypatch)
    cfgp = write_cfg(tmp_path)
    assert main(["run", "--config", str(cfgp)], client_factory=FakeOcr) == 0
    out = capsys.readouterr().out
    assert "run_id:" in out
    run_dir = next((tmp_path / "out").iterdir())
    assert main(["run", "--config", str(cfgp)], client_factory=FakeOcr) == 1  # existe: no sobrescribe
    assert main(["run", "--config", str(cfgp), "--resume"], client_factory=FakeOcr) == 0
    assert main(["report", "--run-dir", str(run_dir)]) == 0
    assert main(["compare", "--baseline", str(run_dir), "--candidate", str(run_dir)]) == 0
    assert '"cmp"' in capsys.readouterr().out and seen.compared


def test_cli_invalid_config_and_limit(tmp_path, monkeypatch, capsys):
    install_w1_stub(monkeypatch)
    assert main(["run", "--config", str(tmp_path / "nope.json")]) == 1
    cfgp = write_cfg(tmp_path, per_doc_type={"curp_2019": 600})
    assert main(["run", "--config", str(cfgp)], client_factory=FakeOcr) == 1
    assert "excede" in capsys.readouterr().err
    assert campaign.DEFAULT_MAX_PER_TYPE == 500
