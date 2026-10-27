from __future__ import annotations

import json
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from contracts.evaluation import OcrClientConfig
from src.evaluation.ocr_clients import (
    CliOcrClient,
    HttpOcrClient,
    OcrClient,
    OcrFatalError,
    OcrItem,
    envelope_to_prediction,
    make_client,
)

FAKE_CLI = textwrap.dedent('''
    import argparse, json, os, sys
    from pathlib import Path
    ap = argparse.ArgumentParser()
    for a in ("manifest", "out", "run-id", "data-dir"):
        ap.add_argument("--" + a)
    a = ap.parse_args()
    Path(a.data_dir).mkdir(parents=True, exist_ok=True)
    marker = Path(__file__).parent / "crashed"
    mode = (Path(__file__).parent / "mode").read_text()
    lines = [json.loads(l) for l in open(a.manifest) if l.strip()]
    if mode == "fatal":
        sys.exit(3)
    with open(a.out, "a") as out:
        for i, l in enumerate(lines):
            out.write(json.dumps({"sample_id": l["sample_id"], "run_id": a.run_id, "status": "ok",
                                  "datos": {"k": "v"}}) + "\\n")
            out.flush()
            if mode == "crash_once" and i == 0 and not marker.exists():
                marker.write_text("x")
                os._exit(1)
''')


def cli_project(tmp_path, mode):
    proj = tmp_path / "ocr"
    (proj / "integration").mkdir(parents=True)
    (proj / "integration" / "__init__.py").write_text("")
    (proj / "integration" / "evaluation_cli.py").write_text(FAKE_CLI)
    (proj / "integration" / "mode").write_text(mode)
    return proj


def cli_client(tmp_path, mode, retries=1):
    proj = cli_project(tmp_path, mode)
    cfg = OcrClientConfig(kind="cli", python=sys.executable, ocr_project_dir=str(proj), retries=retries,
                          timeout_s=30)
    return CliOcrClient(cfg, "R", tmp_path / "work", tmp_path / "data", poll_interval_s=0.05)


def items(tmp_path, n=3):
    return [OcrItem(f"s{i}", tmp_path / f"s{i}.pdf") for i in range(n)]


def test_clients_cli_single_launch_and_ports(tmp_path):
    c = cli_client(tmp_path, "ok")
    assert isinstance(c, OcrClient)
    preds = list(c.process_batch(items(tmp_path)))
    assert [p.sample_id for p in preds] == ["s0", "s1", "s2"] and preds[0].run_id == "R"
    assert len(list((tmp_path / "work").glob("input.attempt*.jsonl"))) == 1
    line = json.loads((tmp_path / "work" / "input.attempt0.jsonl").read_text().splitlines()[0])
    assert set(line) == {"sample_id", "pdf_path"}


def test_clients_cli_relaunches_only_pending_without_duplicates(tmp_path):
    c = cli_client(tmp_path, "crash_once")
    preds = list(c.process_batch(items(tmp_path)))
    ids = [p.sample_id for p in preds]
    assert sorted(ids) == ["s0", "s1", "s2"] and len(ids) == len(set(ids))
    second = (tmp_path / "work" / "input.attempt1.jsonl").read_text().splitlines()
    assert [json.loads(x)["sample_id"] for x in second] == ["s1", "s2"]


def test_clients_cli_fatal_init_raises(tmp_path):
    c = cli_client(tmp_path, "fatal", retries=0)
    with pytest.raises(OcrFatalError):
        list(c.process_batch(items(tmp_path)))


def test_clients_cli_requires_paths_and_factory(tmp_path):
    with pytest.raises(Exception):
        CliOcrClient(OcrClientConfig(kind="cli"), "R", tmp_path, tmp_path)
    cfg = OcrClientConfig(kind="http", base_url="http://127.0.0.1:1")
    assert isinstance(make_client(cfg, "R", tmp_path), HttpOcrClient)


# ---------------------------------------------------------------- HTTP

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits: dict = {}
    keys: list = []
    paths: list = []
    conns: set = set()

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        doc_id = body.split(b'name="doc_id"\r\n\r\n')[1].split(b"\r\n")[0].decode()
        type(self).keys.append(self.headers.get("X-API-Key"))
        type(self).paths.append(self.path)
        type(self).conns.add(self.client_address)
        n = type(self).hits[doc_id] = type(self).hits.get(doc_id, 0) + 1
        status, payload = 200, {
            "tipo_documento": "ine", "datos": {"nombre": "A", "edad": 3, "x": None},
            "metadatos": {"tiempo_procesamiento_ms": 12.5, "tiempos_etapas_ms": {"ocr": 10},
                          "pipeline_version": "9.9", "modelo_llm": "m"}}
        if doc_id == "flaky" and n == 1:
            status, payload = 503, {"error": "busy", "codigo": "SERVICIO_NO_DISPONIBLE"}
        if doc_id == "bad":
            status, payload = 400, {"error": "pdf roto", "codigo": "PDF_INVALIDO"}
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def server():
    _Handler.hits, _Handler.keys, _Handler.paths, _Handler.conns = {}, [], [], set()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def http_client(base, tmp_path, retries=2):
    cfg = OcrClientConfig(kind="http", base_url=base, retries=retries, timeout_s=5)
    sleeps = []
    return HttpOcrClient(cfg, "R", env={"IA_API_KEY": "secret"}, sleep=sleeps.append), sleeps


def pdf_items(tmp_path, *ids):
    out = []
    for i in ids:
        p = tmp_path / f"{i}.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        out.append(OcrItem(i, p))
    return out


def test_clients_http_envelope_retries_and_no_retry_on_4xx(server, tmp_path):
    c, sleeps = http_client(server, tmp_path)
    preds = {p.sample_id: p for p in c.process_batch(pdf_items(tmp_path, "a", "flaky", "bad", "b"))}
    assert preds["a"].status == "ok" and preds["a"].tipo_documento == "ine"
    assert preds["a"].datos == {"nombre": "A", "edad": "3", "x": None}
    assert preds["a"].timings.total_ms == 12.5 and preds["a"].timings.stages_ms == {"ocr": 10.0}
    assert preds["a"].algorithm.pipeline_version == "9.9" and preds["a"].algorithm.llm_model == "m"
    assert preds["flaky"].status == "ok" and _Handler.hits["flaky"] == 2 and len(sleeps) == 1
    assert preds["bad"].status == "error" and preds["bad"].error.code == "PDF_INVALIDO"
    assert _Handler.hits["bad"] == 1 and not preds["bad"].error.retryable
    assert set(_Handler.keys) == {"secret"} and set(_Handler.paths) == {"/api/v1/documentos/procesa"}
    assert len(_Handler.conns) <= 2  # conexión reutilizada (solo se rehace tras el 503)


def test_clients_http_api_key_from_env_only(tmp_path):
    cfg = OcrClientConfig(kind="http", base_url="http://127.0.0.1:1")
    c = HttpOcrClient(cfg, "R", env={})
    with pytest.raises(OcrFatalError, match="IA_API_KEY"):
        list(c.process_batch(pdf_items(tmp_path, "a")))


def test_clients_http_network_error_retried_then_reported(tmp_path):
    cfg = OcrClientConfig(kind="http", base_url="http://127.0.0.1:1", retries=1, timeout_s=1)
    sleeps = []
    c = HttpOcrClient(cfg, "R", env={"IA_API_KEY": "k"}, sleep=sleeps.append)
    (p,) = c.process_batch(pdf_items(tmp_path, "a"))
    assert p.status == "error" and p.error.code == "ERROR_RED" and p.error.retryable and len(sleeps) == 1


def test_clients_envelope_error_shapes():
    p = envelope_to_prediction("s", "R", 422, {"detail": {"error": "m", "codigo": "DOCUMENTO_NO_SOPORTADO"}})
    assert p.error.code == "DOCUMENTO_NO_SOPORTADO" and not p.error.retryable
    p = envelope_to_prediction("s", "R", 429, {"error": "slow", "codigo": "RATE"})
    assert p.error.retryable


def test_clients_cli_resolves_relative_work_and_data_dirs(tmp_path, monkeypatch):
    """Regresión: con run-dir relativo el OCR (otro cwd) no encontraba el manifest."""
    monkeypatch.chdir(tmp_path)
    cfg = OcrClientConfig(kind="cli", python=sys.executable, ocr_project_dir=str(tmp_path))
    client = CliOcrClient(cfg, "R", __import__("pathlib").Path("rel/work"), __import__("pathlib").Path("rel/data"))
    assert client.work_dir.is_absolute() and client.data_dir.is_absolute()
    assert client.work_dir == (tmp_path / "rel/work").resolve()


def test_cli_rutas_relativas_se_vuelven_absolutas(tmp_path, monkeypatch):
    """`python` y `ocr_project_dir` relativos (p. ej. ../ia_documentos desde tt/gen-ia) se anclan al cwd
    de quien lanza la campaña, porque el OCR corre con otro cwd; el .venv no se resuelve a su base."""
    import os
    (tmp_path / "gen").mkdir()
    monkeypatch.chdir(tmp_path / "gen")
    cfg = OcrClientConfig(kind="cli", python="../ia/.venv/bin/python", ocr_project_dir="../ia")
    cli = CliOcrClient(cfg, "run", tmp_path / "w", tmp_path / "d")
    assert os.path.isabs(cli._python) and cli._python == str(tmp_path / "ia/.venv/bin/python")
    assert cli._project_dir == str(tmp_path / "ia")
    assert cli._command(tmp_path / "m.jsonl", tmp_path / "o.jsonl")[0] == cli._python
