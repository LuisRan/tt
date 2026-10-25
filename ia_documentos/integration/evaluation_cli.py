"""
integration/evaluation_cli.py
Adaptador de entrada de EVALUACION (por lotes, sin HTTP).

    cd ia_documentos
    python -m integration.evaluation_cli --manifest in.jsonl --out predictions.jsonl \
        --run-id R1 [--data-dir DIR] [--timeout-s 300] [--limit N]

Entrada : JSONL, una linea {"sample_id": str, "pdf_path": str}
Salida  : JSONL, una linea OcrPrediction por documento (flush por documento).
Exit    : 0 aunque haya documentos con error; != 0 solo si falla la inicializacion.

NO importa Flask. Reutiliza DocumentPipeline.process (una instancia por proceso).
Ver docs_evaluacion.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform as _platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.evaluation import (  # noqa: E402
    SAMPLE_ID_RE,
    AlgorithmInfo,
    EvaluationRequest,
    EvaluationService,
)

EXIT_INIT_FALLIDA = 2
logger = logging.getLogger("ia.eval")


class _RedactarValores(logging.Filter):
    """Evita que los logs del pipeline lleven valores extraidos (PII)."""

    def filter(self, record: logging.LogRecord) -> bool:
        d = record.__dict__
        if "campos_descartados" in d:
            d["campos_descartados"] = [str(c).split("=", 1)[0] for c in d["campos_descartados"]]
        if "valor" in d:
            d["valor"] = "[redactado]"
        return True


def _git_commit() -> Optional[str]:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def config_hash(cfg: dict) -> str:
    blob = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _config_efectiva(s) -> dict:
    """Solo configuracion que afecta al resultado (sin hosts, rutas ni claves)."""
    return {
        "pipeline_version": s.PIPELINE_VERSION,
        "llm_model": s.OLLAMA_MODEL,
        "llm_temperature": s.LLM_TEMPERATURE,
        "llm_num_ctx": s.LLM_NUM_CTX,
        "llm_num_predict": s.LLM_NUM_PREDICT,
        "extraction_strategy": s.EXTRACTION_STRATEGY,
        "anti_hallucination": s.ANTI_HALLUCINATION,
        "ocr_dpi": s.OCR_DPI,
        "ocr_engine": s.OCR_ENGINE,
        "docling_device": s.DOCLING_DEVICE,
        "docling_tables": s.DOCLING_TABLES,
        "ocr_min_chars_normal": s.OCR_MIN_CHARS_NORMAL,
        "ocr_min_chars_fallback": s.OCR_MIN_CHARS_FALLBACK,
        "pdf_max_pages": s.PDF_MAX_PAGES,
        "pdf_max_size_mb": s.PDF_MAX_SIZE_MB,
    }


def _construir_runtime():
    """Importa settings (ya con IA_DATA_DIR fijado) y crea el pipeline real."""
    from config import settings
    from infrastructure.logging_setup import configurar_logging

    configurar_logging(settings.LOGS_DIR, nivel="WARNING")
    for h in logging.getLogger().handlers:
        h.addFilter(_RedactarValores())
    from application.pipeline import DocumentPipeline

    pipeline = DocumentPipeline()
    cfg = _config_efectiva(settings)
    algo = AlgorithmInfo(
        pipeline_version=settings.PIPELINE_VERSION,
        llm_model=settings.OLLAMA_MODEL,
        config_hash=config_hash(cfg),
        git_commit=_git_commit(),
        platform=_platform.platform(),
    )
    return pipeline, algo, settings.PDF_MAX_SIZE_MB


def _leer_manifest(path: Path):
    """Genera (n_linea, EvaluationRequest | None, motivo)."""
    with open(path, encoding="utf-8") as f:
        for n, linea in enumerate(f, 1):
            if not linea.strip():
                continue
            try:
                obj = json.loads(linea)
                if not isinstance(obj, dict):
                    raise ValueError("la linea no es un objeto")
            except ValueError:
                yield n, None, "linea de manifest no es JSON objeto valido"
                continue
            sid, pdf = obj.get("sample_id"), obj.get("pdf_path")
            if not isinstance(sid, str) or not isinstance(pdf, str):
                yield n, EvaluationRequest(sid if isinstance(sid, str) else f"linea_{n}", ""), \
                    "sample_id y pdf_path deben ser str"
                continue
            yield n, EvaluationRequest(sid, pdf), ""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m integration.evaluation_cli",
                                description="Evaluacion por lotes del pipeline OCR (sin HTTP).")
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--run-id", required=True)
    p.add_argument("--data-dir", type=Path, default=None,
                   help="Directorio de salidas (IA_DATA_DIR). Por defecto: temporal nuevo.")
    p.add_argument("--timeout-s", type=float, default=None, help="Plazo por documento (s).")
    p.add_argument("--limit", type=int, default=None, help="Procesar solo las N primeras lineas.")
    return p


def main(argv: Optional[list[str]] = None, *, runtime_factory: Optional[Callable] = None) -> int:
    """`runtime_factory` (solo pruebas) devuelve (pipeline, AlgorithmInfo, max_mb)
    y evita tocar settings/Docling."""
    args = _parser().parse_args(argv)

    if not SAMPLE_ID_RE.match(args.run_id):
        print("run-id invalido (solo letras, numeros, - y _, max 64)", file=sys.stderr)
        return EXIT_INIT_FALLIDA
    if not args.manifest.is_file():
        print(f"manifest no encontrado: {args.manifest}", file=sys.stderr)
        return EXIT_INIT_FALLIDA

    temporal = args.data_dir is None
    data_dir = Path(tempfile.mkdtemp(prefix="ia_eval_")) if temporal else args.data_dir
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        data_dir = data_dir.resolve()
        if runtime_factory is None:
            # ANTES de importar config.settings: el aislamiento depende de esto
            if "config.settings" in sys.modules:
                from config import settings as _s
                if Path(_s.DATA_DIR).resolve() != data_dir:
                    raise RuntimeError("config.settings ya importado con otro IA_DATA_DIR")
            os.environ["IA_DATA_DIR"] = str(data_dir)
        pipeline, algo, max_mb = (runtime_factory or _construir_runtime)()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        salida = open(args.out, "w", encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"fallo de inicializacion: {type(e).__name__}: {e}", file=sys.stderr)
        if temporal:
            shutil.rmtree(data_dir, ignore_errors=True)
        return EXIT_INIT_FALLIDA

    servicio = EvaluationService(pipeline, args.run_id, algo, args.manifest.parent,
                                 max_size_mb=max_mb, timeout_s=args.timeout_s)
    n = n_err = 0
    try:
        with salida:
            for linea, req, motivo in _leer_manifest(args.manifest):
                if args.limit is not None and n >= args.limit:
                    break
                if req is None or motivo:
                    req = req or EvaluationRequest(f"linea_{linea}", "")
                    res = servicio.rechazar(req, "MANIFEST_INVALIDO", motivo)
                else:
                    res = servicio.evaluate(req)
                n += 1
                n_err += res.status == "error"
                salida.write(json.dumps(res.to_dict(), ensure_ascii=False) + "\n")
                salida.flush()
    finally:
        print(f"evaluacion: {n} documentos, {n_err} con error; data_dir={data_dir}", file=sys.stderr)
        if temporal:
            logging.shutdown()
            shutil.rmtree(data_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
