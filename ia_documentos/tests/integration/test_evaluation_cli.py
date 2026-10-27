"""Adaptador CLI de evaluacion: contrato JSONL, aislamiento, sin Flask, prueba real opcional."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from application.evaluation import AlgorithmInfo
from integration import evaluation_cli

ROOT = Path(__file__).resolve().parents[2]
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
GENIA_PY = "/Users/luis/Documents/gen-ia/.venv/bin/python"
PROBE_DIR = Path("/private/tmp/claude-501/-Users-luis-Documents-gen-ia/"
                 "53007b1d-3e39-4a02-83e8-3f9765d8c215/scratchpad/ocr_probe")


class Falso:
    def precargar(self):
        return True

    def process(self, ruta, user_id, doc_id=None):
        return {"tipo_documento": "ine", "datos": {"curp": "X"},
                "metadatos": {"tiempos_etapas_ms": {"ocr_ms": 1}}}


def factory():
    return Falso(), AlgorithmInfo("t", "m", "h" * 64, None, None), 10


def jsonl(path, filas):
    path.write_text("\n".join(f if isinstance(f, str) else json.dumps(f) for f in filas) + "\n")


def leer(path):
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_main_en_proceso_contrato_y_exit0_con_errores(tmp_path):
    (tmp_path / "a.pdf").write_bytes(PDF)
    jsonl(tmp_path / "in.jsonl", [
        {"sample_id": "ok1", "pdf_path": "a.pdf"},
        {"sample_id": "../mal", "pdf_path": "a.pdf"},
        {"sample_id": "falta", "pdf_path": "nada.pdf"},
        "esto no es json",
        {"sample_id": 3, "pdf_path": "a.pdf"},
        {"sample_id": "ok2", "pdf_path": "a.pdf"},
    ])
    out = tmp_path / "out" / "p.jsonl"
    rc = evaluation_cli.main(["--manifest", str(tmp_path / "in.jsonl"), "--out", str(out),
                              "--run-id", "R1", "--data-dir", str(tmp_path / "d")],
                             runtime_factory=factory)
    assert rc == 0
    filas = leer(out)
    assert [f["status"] for f in filas] == ["ok", "error", "error", "error", "error", "ok"]
    assert [f["error"]["code"] for f in filas if f["error"]] == [
        "ID_INVALIDO", "PDF_INVALIDO", "MANIFEST_INVALIDO", "MANIFEST_INVALIDO"]
    assert filas[0]["timings"]["model_load_ms"] is not None
    assert filas[5]["timings"]["model_load_ms"] is None
    assert all(f["run_id"] == "R1" and f["schema_version"] == "1.0.0" for f in filas)


def test_limit(tmp_path):
    (tmp_path / "a.pdf").write_bytes(PDF)
    jsonl(tmp_path / "in.jsonl", [{"sample_id": f"s{i}", "pdf_path": "a.pdf"} for i in range(5)])
    out = tmp_path / "p.jsonl"
    evaluation_cli.main(["--manifest", str(tmp_path / "in.jsonl"), "--out", str(out),
                         "--run-id", "R", "--limit", "2"], runtime_factory=factory)
    assert len(leer(out)) == 2


def test_fallos_de_inicializacion(tmp_path):
    base = ["--out", str(tmp_path / "o.jsonl")]
    assert evaluation_cli.main(["--manifest", str(tmp_path / "no.jsonl"), "--run-id", "R"] + base,
                               runtime_factory=factory) == 2
    (tmp_path / "in.jsonl").write_text("")
    assert evaluation_cli.main(["--manifest", str(tmp_path / "in.jsonl"), "--run-id", "../R"] + base,
                               runtime_factory=factory) == 2

    def roto():
        raise RuntimeError("boom")
    assert evaluation_cli.main(["--manifest", str(tmp_path / "in.jsonl"), "--run-id", "R"] + base,
                               runtime_factory=roto) == 2


def test_config_hash_estable_y_sensible():
    a = evaluation_cli.config_hash({"x": 1, "y": 2})
    assert a == evaluation_cli.config_hash({"y": 2, "x": 1}) and len(a) == 64
    assert a != evaluation_cli.config_hash({"x": 1, "y": 3})


def test_filtro_redacta_valores():
    import logging
    r = logging.LogRecord("n", 20, "f", 1, "m", (), None)
    r.campos_descartados = ["curp=SECRETO"]
    r.valor = "SECRETO"
    assert evaluation_cli._RedactarValores().filter(r) is True
    assert r.campos_descartados == ["curp"] and r.valor == "[redactado]"


def _snapshot(d: Path):
    return sorted((str(p.relative_to(d)), p.stat().st_mtime_ns) for p in d.rglob("*"))


def test_cli_real_aisla_data_dir_y_no_importa_flask(tmp_path):
    """Subproceso con settings REALES: salidas solo en --data-dir; data/ del usuario intacto."""
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 roto")  # rechazado antes del pipeline
    jsonl(tmp_path / "in.jsonl", [{"sample_id": "../x", "pdf_path": "a.pdf"},
                                  {"sample_id": "s1", "pdf_path": "a.pdf"}])
    real_data = ROOT / "data"
    antes = _snapshot(real_data) if real_data.exists() else None
    dd = tmp_path / "datadir"
    r = subprocess.run(
        [sys.executable, "-m", "integration.evaluation_cli", "--manifest", str(tmp_path / "in.jsonl"),
         "--out", str(tmp_path / "p.jsonl"), "--run-id", "R", "--data-dir", str(dd)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
        env={**os.environ, "IA_DATA_DIR": "/nonexistente/ignorado"})
    assert r.returncode == 0, r.stderr
    assert [f["error"]["code"] for f in leer(tmp_path / "p.jsonl")] == ["ID_INVALIDO", "PDF_INVALIDO"]
    assert (dd / "logs").is_dir() and (dd / "output" / "json_results").is_dir()
    assert (_snapshot(real_data) if real_data.exists() else None) == antes
    assert str(dd) in r.stderr


def test_cli_sin_data_dir_usa_temporal_y_lo_borra(tmp_path):
    (tmp_path / "in.jsonl").write_text(json.dumps({"sample_id": "../x", "pdf_path": "a.pdf"}) + "\n")
    real_data = ROOT / "data"
    antes = _snapshot(real_data) if real_data.exists() else None
    r = subprocess.run(
        [sys.executable, "-m", "integration.evaluation_cli", "--manifest", str(tmp_path / "in.jsonl"),
         "--out", str(tmp_path / "p.jsonl"), "--run-id", "R"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    destino = r.stderr.split("data_dir=")[1].strip()
    assert "ia_eval_" in destino and not Path(destino).exists()
    assert (_snapshot(real_data) if real_data.exists() else None) == antes


def test_adaptador_no_importa_flask():
    code = ("import sys; import integration.evaluation_cli, application.evaluation; "
            "assert 'flask' not in sys.modules, 'flask importado'; "
            "assert 'config.settings' not in sys.modules")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(os.environ.get("EVAL_REAL") != "1",
                    reason="Prueba real (Docling+Ollama locales, lenta): EVAL_REAL=1")
def test_real_pdf_sintetico_gen_ia(tmp_path):
    gen = subprocess.run(
        [GENIA_PY, "-m", "src", "generate", "--doc-type", "curp_2019", "--n", "1", "--seed", "7",
         "--degradation", "none", "--on-exist", "skip", "--out", str(PROBE_DIR)],
        cwd="/Users/luis/Documents/gen-ia", capture_output=True, text=True, timeout=300)
    assert gen.returncode == 0, gen.stderr
    pdf = next(PROBE_DIR.rglob("*.pdf"))
    jsonl(tmp_path / "in.jsonl", [{"sample_id": "curp_7", "pdf_path": str(pdf)}])
    r = subprocess.run(
        [sys.executable, "-m", "integration.evaluation_cli", "--manifest", str(tmp_path / "in.jsonl"),
         "--out", str(tmp_path / "p.jsonl"), "--run-id", "real", "--data-dir", str(tmp_path / "d"),
         "--timeout-s", "900"],
        cwd=ROOT, capture_output=True, text=True, timeout=1200,
        env={**os.environ, "OLLAMA_HOST": os.environ.get("OLLAMA_HOST", "http://localhost:11434")})
    assert r.returncode == 0, r.stderr
    fila = leer(tmp_path / "p.jsonl")[0]
    print(json.dumps(fila["timings"]), fila["status"], fila["error"], fila["tipo_documento"])
    assert fila["timings"]["model_load_ms"] is not None
    assert fila["status"] in ("ok", "error")
