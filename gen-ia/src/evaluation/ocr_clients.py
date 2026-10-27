"""Puerto `OcrClient` y adaptadores (CLI por subproceso / HTTP) hacia el OCR.

El generador y el OCR solo se hablan por `OcrPrediction` (contracts/evaluation.py).
Nunca se registra la API key ni valores extraídos (PII) a nivel INFO.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import subprocess
import time
import urllib.parse
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from contracts.evaluation import (
    AlgorithmInfo,
    OcrClientConfig,
    OcrError,
    OcrPrediction,
    OcrTimings,
)

log = logging.getLogger(__name__)

HTTP_PATH = "/api/v1/documentos/procesa"
RETRY_STATUS = {429, 500, 502, 503, 504}


class OcrClientError(Exception):
    """Fallo del cliente OCR (no de un documento concreto)."""


class OcrFatalError(OcrClientError):
    """Fallo fatal: el OCR no llegó a producir ninguna predicción (inicialización)."""


@dataclass(frozen=True)
class OcrItem:
    sample_id: str
    pdf_path: Path  # ruta absoluta del PDF a enviar


@runtime_checkable
class OcrClient(Protocol):
    def process_batch(self, items: Sequence[OcrItem]) -> Iterator[OcrPrediction]:
        """Devuelve (volcando según se procesan) una predicción por documento."""
        ...


def error_prediction(sample_id: str, run_id: str | None, code: str, message: str,
                     retryable: bool = False) -> OcrPrediction:
    return OcrPrediction(sample_id=sample_id, run_id=run_id, status="error",
                         error=OcrError(code=code, message=message, retryable=retryable))


# ----------------------------------------------------------------------------- CLI

class CliOcrClient:
    """Lanza UNA vez `<python> -m integration.evaluation_cli ...` (el modelo se carga una vez).

    Si el proceso muere o se queda colgado se relanza solo con los sample_id pendientes
    (hasta `retries` veces) sin duplicar resultados.
    """

    def __init__(self, cfg: OcrClientConfig, run_id: str, work_dir: Path, data_dir: Path, *,
                 per_doc_timeout_s: float | None = None, startup_grace_s: float | None = None,
                 global_timeout_s: float | None = None, poll_interval_s: float = 0.2) -> None:
        if not cfg.python or not cfg.ocr_project_dir:
            raise OcrClientError("ocr.kind=cli requiere ocr.python y ocr.ocr_project_dir")
        self.cfg, self.run_id = cfg, run_id
        # abspath (no resolve): el python del .venv es un enlace y resolve() saldría del entorno virtual.
        # Relativas al cwd de quien lanza la campaña (p. ej. "../ia_documentos" desde tt/gen-ia).
        self._python = os.path.abspath(cfg.python)
        self._project_dir = os.path.abspath(cfg.ocr_project_dir)
        # absolutas: el OCR corre con otro cwd (ocr_project_dir) y no resolvería rutas relativas
        self.work_dir, self.data_dir = Path(work_dir).resolve(), Path(data_dir).resolve()
        self.per_doc_timeout_s = per_doc_timeout_s if per_doc_timeout_s is not None else cfg.timeout_s
        self.startup_grace_s = startup_grace_s if startup_grace_s is not None else cfg.timeout_s
        self.global_timeout_s = global_timeout_s
        self.poll_interval_s = poll_interval_s

    def _command(self, manifest: Path, out: Path) -> list[str]:
        return [self._python, "-m", "integration.evaluation_cli", "--manifest", str(manifest),
                "--out", str(out), "--run-id", self.run_id, "--data-dir", str(self.data_dir)]

    def process_batch(self, items: Sequence[OcrItem]) -> Iterator[OcrPrediction]:
        pending = {i.sample_id: i for i in items}
        produced = 0
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        last_rc: int | None = None
        for attempt in range(self.cfg.retries + 1):
            if not pending:
                break
            log.info("OCR (cli) intento %d: %d documento(s) pendiente(s)", attempt + 1, len(pending))
            gen = self._run_once(attempt, pending)
            try:
                while True:
                    pred = next(gen)
                    produced += 1
                    yield pred
            except StopIteration as stop:
                last_rc = stop.value
        if pending:
            msg = f"{len(pending)} documento(s) sin predicción tras {self.cfg.retries + 1} intento(s) (rc={last_rc})"
            if produced == 0:
                raise OcrFatalError(f"el OCR no produjo ninguna predicción: {msg}; ver {self.work_dir / 'ocr.log'}")
            log.warning(msg)

    def _run_once(self, attempt: int, pending: dict[str, OcrItem]):
        """Generador: cede predicciones y devuelve el exit code. Quita de `pending` lo recibido."""
        manifest = self.work_dir / f"input.attempt{attempt}.jsonl"
        out = self.work_dir / f"predictions.attempt{attempt}.jsonl"
        manifest.write_text("".join(
            json.dumps({"sample_id": i.sample_id, "pdf_path": str(i.pdf_path)}) + "\n"
            for i in pending.values()), encoding="utf-8")
        out.write_text("", encoding="utf-8")
        logf = open(self.work_dir / "ocr.log", "ab")
        try:
            proc = subprocess.Popen(self._command(manifest, out), cwd=self._project_dir,
                                    stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        except OSError as exc:
            logf.close()
            raise OcrFatalError(f"no se pudo lanzar el OCR: {exc}") from exc
        started = last = time.monotonic()
        total = len(pending)
        global_limit = self.global_timeout_s
        if global_limit is None:
            global_limit = self.startup_grace_s + self.per_doc_timeout_s * total
        offset, buf, got = 0, b"", 0
        try:
            while True:
                exited = proc.poll() is not None
                with open(out, "rb") as fh:
                    fh.seek(offset)
                    data = fh.read()
                offset += len(data)
                buf += data
                *lines, buf = buf.split(b"\n")
                for raw in lines:
                    if not raw.strip():
                        continue
                    try:
                        pred = OcrPrediction.model_validate_json(raw)
                    except ValueError:
                        log.warning("línea de predicción inválida ignorada")
                        continue
                    if pred.sample_id not in pending:
                        continue  # duplicada o ajena
                    del pending[pred.sample_id]
                    got += 1
                    last = time.monotonic()
                    yield pred
                if exited:
                    if buf.strip():  # última línea sin salto de línea
                        try:
                            pred = OcrPrediction.model_validate_json(buf)
                            if pred.sample_id in pending:
                                del pending[pred.sample_id]
                                yield pred
                        except ValueError:
                            pass
                    return proc.returncode
                now = time.monotonic()
                limit = self.per_doc_timeout_s + (self.startup_grace_s if got == 0 else 0)
                if now - last > limit or now - started > global_limit:
                    log.warning("timeout del OCR; se termina el proceso")
                    self._kill(proc)
                    return -1
                time.sleep(self.poll_interval_s)
        finally:
            if proc.poll() is None:
                self._kill(proc)
            logf.close()

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ----------------------------------------------------------------------------- HTTP

def _scalar(v: object) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v)


def encode_multipart(fields: dict[str, str], file_field: str, filename: str, content: bytes,
                     content_type: str = "application/pdf") -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                  f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n').encode())
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def envelope_to_prediction(sample_id: str, run_id: str | None, status: int, body: object) -> OcrPrediction:
    """Convierte el envelope web (`tipo_documento`, `datos`, `metadatos`) o `{error, codigo}`."""
    if not isinstance(body, dict):
        return error_prediction(sample_id, run_id, "RESPUESTA_INVALIDA", f"HTTP {status}: cuerpo no es un objeto",
                                retryable=status >= 500)
    if status >= 400 or ("error" in body and "datos" not in body):
        det = body.get("detail") if isinstance(body.get("detail"), dict) else body
        code = str(det.get("codigo") or det.get("code") or f"HTTP_{status}")
        msg = det.get("error") or det.get("message") or f"HTTP {status}"
        return error_prediction(sample_id, run_id, code, str(msg), retryable=status in RETRY_STATUS)
    meta = body.get("metadatos") if isinstance(body.get("metadatos"), dict) else {}
    datos = body.get("datos") if isinstance(body.get("datos"), dict) else {}
    total = meta.get("tiempo_procesamiento_ms")
    stages = meta.get("tiempos_etapas_ms") if isinstance(meta.get("tiempos_etapas_ms"), dict) else {}
    timings = None
    if isinstance(total, (int, float)):
        timings = OcrTimings(total_ms=float(total),
                             stages_ms={k: float(v) for k, v in stages.items() if isinstance(v, (int, float))})
    algo = None
    if meta.get("pipeline_version"):
        algo = AlgorithmInfo(pipeline_version=str(meta["pipeline_version"]), llm_model=meta.get("modelo_llm"),
                             config_hash="n/a")
    return OcrPrediction(sample_id=sample_id, run_id=run_id, status="ok",
                         tipo_documento=body.get("tipo_documento"),
                         datos={str(k): _scalar(v) for k, v in datos.items()},
                         metadatos=meta, timings=timings, algorithm=algo)


class HttpOcrClient:
    """POST multipart a `{base_url}/api/v1/documentos/procesa` con conexión keep-alive."""

    def __init__(self, cfg: OcrClientConfig, run_id: str, *, env: dict[str, str] | None = None,
                 sleep: Callable[[float], None] = time.sleep, backoff_s: float = 1.0) -> None:
        if not cfg.base_url:
            raise OcrClientError("ocr.kind=http requiere ocr.base_url")
        u = urllib.parse.urlsplit(cfg.base_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise OcrClientError("ocr.base_url debe ser http(s)://host[:puerto]")
        self.cfg, self.run_id = cfg, run_id
        self._u = u
        self._env = os.environ if env is None else env
        self._sleep, self._backoff = sleep, backoff_s
        self._conn: http.client.HTTPConnection | None = None

    def _api_key(self) -> str:
        key = self._env.get(self.cfg.api_key_env)
        if not key:
            raise OcrFatalError(f"falta la variable de entorno {self.cfg.api_key_env} con la API key")
        return key

    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            cls = http.client.HTTPSConnection if self._u.scheme == "https" else http.client.HTTPConnection
            self._conn = cls(self._u.hostname, self._u.port, timeout=self.cfg.timeout_s)
        return self._conn

    def _drop(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def close(self) -> None:
        self._drop()

    def _post(self, body: bytes, ctype: str, key: str) -> tuple[int, object]:
        conn = self._connection()
        path = self._u.path.rstrip("/") + HTTP_PATH
        conn.request("POST", path, body=body, headers={"Content-Type": ctype, "X-API-Key": key,
                                                       "Accept": "application/json"})
        resp = conn.getresponse()
        raw = resp.read()
        try:
            parsed: object = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            parsed = None
        return resp.status, parsed

    def process_one(self, item: OcrItem) -> OcrPrediction:
        key = self._api_key()
        try:
            content = item.pdf_path.read_bytes()
        except OSError as exc:
            return error_prediction(item.sample_id, self.run_id, "PDF_ILEGIBLE", str(exc))
        body, ctype = encode_multipart({"user_id": "eval", "doc_id": item.sample_id}, "archivo",
                                       f"{item.sample_id}.pdf", content)
        last: OcrPrediction | None = None
        for attempt in range(self.cfg.retries + 1):
            if attempt:
                self._sleep(self._backoff * 2 ** (attempt - 1))
            try:
                status, parsed = self._post(body, ctype, key)
            except (OSError, http.client.HTTPException) as exc:
                self._drop()
                last = error_prediction(item.sample_id, self.run_id, "ERROR_RED", type(exc).__name__,
                                        retryable=True)
                continue
            if status in RETRY_STATUS:
                self._drop()  # el servidor puede haber cerrado la conexión
                last = envelope_to_prediction(item.sample_id, self.run_id, status, parsed)
                continue
            return envelope_to_prediction(item.sample_id, self.run_id, status, parsed)  # 2xx y 4xx: sin reintento
        assert last is not None
        return last

    def process_batch(self, items: Sequence[OcrItem]) -> Iterator[OcrPrediction]:
        self._api_key()  # falla pronto y sin tocar la red
        try:
            for it in items:
                yield self.process_one(it)
        finally:
            self.close()


def make_client(cfg: OcrClientConfig, run_id: str, run_dir: Path) -> OcrClient:
    """Selecciona el adaptador según `ocr.kind`."""
    if cfg.kind == "cli":
        return CliOcrClient(cfg, run_id, work_dir=run_dir / "ocr_work", data_dir=run_dir / "ocr_data")
    return HttpOcrClient(cfg, run_id)
