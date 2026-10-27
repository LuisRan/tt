"""Orquestador de campañas de evaluación supervisada.

Estructura del run-dir (`<out_dir>/<run_id>/`):

    config.json              config efectiva + versiones + commit
    manifest.jsonl           un SampleRef por línea (rutas relativas al run-dir)
    samples/<preset>/<doc_type>/<gen_id>.pdf|.json   salida del generador, una carpeta por preset
    submitted/<sample_id>.pdf  solo si se recorta el PDF (INE anverso)
    predictions.jsonl        OcrPrediction por documento (se vuelca según llegan)
    ocr_work/, ocr_data/     entrada/salida/log del OCR por CLI y su --data-dir aislado
    results/                 informes escritos por `write_reports`
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts import CONTRACTS_VERSION, DocType
from contracts.evaluation import (
    EVALUATION_SCHEMA_VERSION,
    CampaignConfig,
    OcrPrediction,
    SampleRef,
)
from contracts.ground_truth import GroundTruth

from .ocr_clients import OcrClient, OcrClientError, OcrItem, error_prediction, make_client
from .pdf_pages import prepare_submitted_pdf
from .splits import split_for_group, verify_no_leakage

log = logging.getLogger(__name__)

DEFAULT_MAX_PER_TYPE = 500
SAMPLE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
ALLOWED_PRESETS = ("none", "light", "default", "heavy", "scanner")

CONFIG_JSON = "config.json"
MANIFEST = "manifest.jsonl"
PREDICTIONS = "predictions.jsonl"
RESULTS_DIR = "results"


class CampaignError(Exception):
    """Configuración o estado de campaña inválido."""


class CampaignExistsError(CampaignError):
    """El run-dir ya existe y no se pidió --resume ni --force-new-run."""


# ------------------------------------------------------------------ utilidades

def safe_path(base: Path, rel: str | Path) -> Path:
    """Resuelve `rel` bajo `base`; rechaza rutas absolutas o que escapen (path traversal)."""
    rel_p = Path(rel)
    if rel_p.is_absolute() or ".." in rel_p.parts:
        raise CampaignError(f"ruta fuera del run-dir: {str(rel)!r}")
    root = Path(base).resolve()
    full = (root / rel_p).resolve()
    if not full.is_relative_to(root):
        raise CampaignError(f"ruta fuera del run-dir: {str(rel)!r}")
    return full


def validate_sample_id(sample_id: str) -> str:
    if not SAMPLE_ID_RE.match(sample_id):
        raise CampaignError(f"sample_id inválido (^[A-Za-z0-9_\\-]{{1,64}}$): {sample_id!r}")
    return sample_id


def config_hash(cfg: CampaignConfig) -> str:
    data = cfg.model_dump(mode="json")
    data.pop("out_dir", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:8]


def make_run_id(cfg: CampaignConfig) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "-", cfg.name).strip("-")[:40] or "campaign"
    return f"{slug}-{cfg.seed}-{config_hash(cfg)}"


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def versions(doc_types: list[DocType]) -> dict[str, Any]:
    from src import layouts
    from src.data_injection import GENERATOR_VERSION

    return {"generator_version": GENERATOR_VERSION, "contracts": CONTRACTS_VERSION,
            "evaluation_schema": EVALUATION_SCHEMA_VERSION,
            "layout_version": {d.value: layouts.load_spec(d).layout_version for d in doc_types},
            "git_commit": git_commit()}


def load_config(path: str | Path) -> CampaignConfig:
    try:
        return CampaignConfig.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CampaignError(f"config de campaña inválida ({path}): {exc}") from exc


def validate_config(cfg: CampaignConfig, max_per_type: int) -> None:
    if not cfg.per_doc_type:
        raise CampaignError("per_doc_type está vacío")
    for dt, n in cfg.per_doc_type.items():
        if n < 1:
            raise CampaignError(f"n debe ser >= 1 ({dt.value})")
        if n > max_per_type:
            raise CampaignError(f"n={n} para {dt.value} excede el máximo permitido ({max_per_type}); "
                                "suba --max-per-type si es intencionado")
    if not cfg.degradation_presets or len(set(cfg.degradation_presets)) != len(cfg.degradation_presets):
        raise CampaignError("degradation_presets debe tener al menos un preset y sin repetidos")
    for p in cfg.degradation_presets:
        if p not in ALLOWED_PRESETS:
            raise CampaignError(f"preset de degradación no permitido: {p!r} ({', '.join(ALLOWED_PRESETS)})")
    if cfg.ocr.kind == "cli" and not (cfg.ocr.python and cfg.ocr.ocr_project_dir):
        raise CampaignError("ocr.kind=cli requiere ocr.python y ocr.ocr_project_dir")
    if cfg.ocr.kind == "http" and not cfg.ocr.base_url:
        raise CampaignError("ocr.kind=http requiere ocr.base_url")


# ------------------------------------------------------------------ manifest

def read_manifest(run_dir: Path) -> list[SampleRef]:
    """Lee y valida el manifest: ids, rutas dentro del run-dir, sin fuga entre splits."""
    path = Path(run_dir) / MANIFEST
    if not path.is_file():
        raise CampaignError(f"no existe {path}")
    refs: dict[str, SampleRef] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ref = SampleRef.model_validate_json(line)
        validate_sample_id(ref.sample_id)
        for rel in (ref.pdf_path, ref.ground_truth_path, ref.submitted_pdf_path):
            safe_path(run_dir, rel)
        refs[ref.sample_id] = ref
    out = list(refs.values())
    verify_no_leakage(out)
    return out


def write_manifest(run_dir: Path, refs: list[SampleRef]) -> None:
    tmp = Path(run_dir) / (MANIFEST + ".tmp")
    tmp.write_text("".join(r.model_dump_json() + "\n" for r in refs), encoding="utf-8")
    tmp.replace(Path(run_dir) / MANIFEST)


def read_predictions(path: Path) -> dict[str, OcrPrediction]:
    preds: dict[str, OcrPrediction] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    p = OcrPrediction.model_validate_json(line)
                except ValueError:
                    continue
                preds[p.sample_id] = p
    return preds


# ------------------------------------------------------------------ run-dir

def resolve_run_dir(cfg: CampaignConfig, out_dir: str | Path | None, *, resume: bool, force_new_run: bool,
                    run_dir: str | Path | None = None) -> tuple[Path, str, bool]:
    """Devuelve (run_dir, run_id, reanudando). Nunca reutiliza un run-dir sin `resume`."""
    if run_dir is not None:
        rd = Path(run_dir)
        if not (resume and (rd / CONFIG_JSON).is_file()):
            raise CampaignError("--run-dir solo se usa con --resume sobre una campaña existente")
        stored = json.loads((rd / CONFIG_JSON).read_text(encoding="utf-8"))
        if stored.get("config_hash") != config_hash(cfg):
            raise CampaignError("la config no coincide con la de la campaña a reanudar")
        return rd, stored["run_id"], True
    base = Path(out_dir if out_dir is not None else cfg.out_dir)
    run_id = make_run_id(cfg)
    rd = base / run_id
    if rd.exists():
        if resume:
            stored_p = rd / CONFIG_JSON
            if not stored_p.is_file():
                raise CampaignError(f"{rd} existe pero no es una campaña (falta config.json)")
            stored = json.loads(stored_p.read_text(encoding="utf-8"))
            if stored.get("config_hash") != config_hash(cfg):
                raise CampaignError("la config no coincide con la de la campaña a reanudar")
            return rd, stored["run_id"], True
        if not force_new_run:
            raise CampaignExistsError(f"la campaña {rd} ya existe; use --resume o --force-new-run")
        k = 2
        while (base / f"{run_id}-r{k}").exists():
            k += 1
        run_id = f"{run_id}-r{k}"
        rd = base / run_id
    return rd, run_id, False


def write_config(run_dir: Path, run_id: str, cfg: CampaignConfig) -> None:
    p = run_dir / CONFIG_JSON
    if p.is_file():
        return
    data = {"run_id": run_id, "config_hash": config_hash(cfg), "config": cfg.model_dump(mode="json"),
            "versions": versions(list(cfg.per_doc_type)),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ generación

def _reusable(run_dir: Path, ref: SampleRef) -> bool:
    try:
        return (safe_path(run_dir, ref.pdf_path).is_file()
                and safe_path(run_dir, ref.submitted_pdf_path).is_file()
                and safe_path(run_dir, ref.ground_truth_path).is_file())
    except CampaignError:
        return False


def generate_samples(cfg: CampaignConfig, run_dir: Path, run_id: str, *, resume: bool = False,
                     progress: Callable[[int, int], None] | None = None) -> list[SampleRef]:
    """Un documento por (tipo, índice base, preset) con el MISMO seed/índice: mismos datos,
    distinta degradación. Cada preset en su subdirectorio. Reutiliza lo ya generado si `resume`."""
    from src.pipeline import generate_sample

    prior: dict[tuple[str, str], SampleRef] = {}
    if resume and (run_dir / MANIFEST).is_file():
        prior = {(r.group_id, r.degradation_preset): r for r in read_manifest(run_dir)}
    total = sum(cfg.per_doc_type.values()) * len(cfg.degradation_presets)
    refs: list[SampleRef] = []
    done = 0
    mf = open(run_dir / MANIFEST, "a" if resume else "w", encoding="utf-8")
    try:
        for dt, n in cfg.per_doc_type.items():
            for idx in range(n):
                group_id = f"{dt.value}-{cfg.seed}-{idx}"
                split = split_for_group(cfg.seed, group_id, cfg.split)
                for preset in cfg.degradation_presets:
                    done += 1
                    old = prior.get((group_id, preset))
                    if old is not None and _reusable(run_dir, old):
                        refs.append(old)
                        if progress:
                            progress(done, total)
                        continue
                    sdir = run_dir / "samples" / preset
                    res = generate_sample(dt, cfg.seed, idx, variant=cfg.variant, degradation=preset,
                                          out_dir=sdir)
                    sample_id = validate_sample_id(f"{res.sample_id}-{preset}")
                    pdf = res.path(res.degraded_pdf)
                    gt_file = res.path(res.gt_path)
                    sub = run_dir / "submitted" / f"{sample_id}.pdf"
                    cut = prepare_submitted_pdf(res.ground_truth, pdf, sub, cfg.ine_pages)
                    rel = lambda p: p.resolve().relative_to(run_dir.resolve()).as_posix()  # noqa: E731
                    ref = SampleRef(sample_id=sample_id, group_id=group_id, split=split, doc_type=dt,
                                    degradation_preset=preset, pdf_path=rel(pdf),
                                    ground_truth_path=rel(gt_file),
                                    submitted_pdf_path=rel(sub if cut else pdf), run_id=run_id)
                    for r in (ref.pdf_path, ref.ground_truth_path, ref.submitted_pdf_path):
                        safe_path(run_dir, r)
                    mf.write(ref.model_dump_json() + "\n")
                    mf.flush()
                    refs.append(ref)
                    if progress:
                        progress(done, total)
    finally:
        mf.close()
    verify_no_leakage(refs)
    write_manifest(run_dir, refs)  # versión ordenada y sin duplicados
    log.info("generadas/reutilizadas %d muestras en %s", len(refs), run_dir)
    return refs


# ------------------------------------------------------------------ OCR

def collect_predictions(run_dir: Path, run_id: str, refs: list[SampleRef], client: OcrClient, *,
                        resume: bool = False) -> dict[str, OcrPrediction]:
    """Procesa en el OCR las muestras sin predicción reutilizable. Vuelca cada predicción en
    `predictions.jsonl` según llega. Devuelve una predicción por muestra (SIN_RESPUESTA si falta)."""
    pred_path = run_dir / PREDICTIONS
    existing = read_predictions(pred_path) if resume else {}
    wanted = {r.sample_id for r in refs}
    keep = {sid: p for sid, p in existing.items()
            if sid in wanted and (p.status == "ok" or not (p.error and p.error.retryable))}
    if resume:  # reescribe sin las predicciones que se van a reintentar
        pred_path.write_text("".join(p.model_dump_json() + "\n" for p in keep.values()), encoding="utf-8")
    else:
        pred_path.write_text("", encoding="utf-8")
    items = [OcrItem(r.sample_id, safe_path(run_dir, r.submitted_pdf_path)) for r in refs
             if r.sample_id not in keep]
    preds = dict(keep)
    fatal: OcrClientError | None = None
    if items:
        log.info("OCR: %d documento(s) a procesar (%d reutilizados)", len(items), len(keep))
        with open(pred_path, "a", encoding="utf-8") as out:
            try:
                for p in client.process_batch(items):
                    if p.sample_id not in wanted or p.sample_id in preds:
                        continue
                    p = p.model_copy(update={"run_id": p.run_id or run_id})
                    preds[p.sample_id] = p
                    out.write(p.model_dump_json() + "\n")
                    out.flush()
            except OcrClientError as exc:
                fatal = exc
    if fatal is not None and not preds:
        raise fatal
    if fatal is not None:
        log.error("el OCR terminó con error: %s", fatal)
    for r in refs:
        if r.sample_id not in preds:
            preds[r.sample_id] = error_prediction(r.sample_id, run_id, "SIN_RESPUESTA",
                                                  "el OCR no devolvió predicción para la muestra")
    return preds


# ------------------------------------------------------------------ evaluación

def load_run_config(run_dir: Path) -> dict[str, Any]:
    p = Path(run_dir) / CONFIG_JSON
    if not p.is_file():
        raise CampaignError(f"{run_dir} no es un run-dir de campaña (falta config.json)")
    return json.loads(p.read_text(encoding="utf-8"))


def evaluate_run(run_dir: str | Path, preds: dict[str, OcrPrediction] | None = None):
    """Evalúa un run-dir ya procesado (manifest + predictions.jsonl + GT) -> CampaignReport."""
    from .evaluate import aggregate, evaluate_sample  # W1: import perezoso

    run_dir = Path(run_dir)
    meta = load_run_config(run_dir)
    run_id = meta["run_id"]
    refs = read_manifest(run_dir)
    if preds is None:
        preds = read_predictions(run_dir / PREDICTIONS)
    results = []
    for ref in refs:
        gt = GroundTruth.model_validate_json(safe_path(run_dir, ref.ground_truth_path).read_text(encoding="utf-8"))
        # El id de campaña es `<id_generador>-<preset>`: la clave única por variante.
        # Se comprueba que el GT pertenece a esa muestra y se evalúa con el id de campaña.
        if ref.sample_id != f"{gt.sample_id}-{ref.degradation_preset}":
            raise CampaignError(f"el ground truth {gt.sample_id!r} no corresponde a la muestra {ref.sample_id!r}")
        gt = gt.model_copy(update={"sample_id": ref.sample_id})
        pred = preds.get(ref.sample_id) or error_prediction(
            ref.sample_id, run_id, "SIN_RESPUESTA", "el OCR no devolvió predicción para la muestra")
        results.append(evaluate_sample(gt, pred, ref))
    cfg = meta.get("config", {})
    counts: dict[str, int] = {}
    for r in refs:
        counts[r.split] = counts.get(r.split, 0) + 1
    run_info = {"run_id": run_id, "name": cfg.get("name"), "seed": cfg.get("seed"),
                "config_hash": meta.get("config_hash"), "versions": meta.get("versions", {}),
                "ocr_kind": cfg.get("ocr", {}).get("kind"), "n_samples": len(refs),
                "split_counts": counts, "degradation_presets": cfg.get("degradation_presets"),
                "ine_pages": cfg.get("ine_pages")}
    return aggregate(results, run_info)


def report_run(run_dir: str | Path, preds: dict[str, OcrPrediction] | None = None):
    from .reports import write_reports  # W1: import perezoso

    run_dir = Path(run_dir)
    report = evaluate_run(run_dir, preds)
    files = write_reports(report, run_dir / RESULTS_DIR)
    return report, files


def compare_runs(baseline: str | Path, candidate: str | Path):
    from .evaluate import compare_reports  # W1: import perezoso

    return compare_reports(evaluate_run(baseline), evaluate_run(candidate))


# ------------------------------------------------------------------ run

@dataclass
class RunOutcome:
    run_dir: Path
    run_id: str
    report: Any
    files: dict[str, Path]
    n_samples: int
    n_missing: int


def run(cfg: CampaignConfig, *, client: OcrClient | None = None, out_dir: str | Path | None = None,
        resume: bool = False, force_new_run: bool = False, run_dir: str | Path | None = None,
        max_per_type: int = DEFAULT_MAX_PER_TYPE,
        progress: Callable[[int, int], None] | None = None) -> RunOutcome:
    validate_config(cfg, max_per_type)
    rd, run_id, resuming = resolve_run_dir(cfg, out_dir, resume=resume, force_new_run=force_new_run,
                                           run_dir=run_dir)
    rd.mkdir(parents=True, exist_ok=True)
    write_config(rd, run_id, cfg)
    log.info("campaña %s en %s%s", run_id, rd, " (reanudando)" if resuming else "")
    refs = generate_samples(cfg, rd, run_id, resume=resuming, progress=progress)
    ocr = client if client is not None else make_client(cfg.ocr, run_id, rd)
    preds = collect_predictions(rd, run_id, refs, ocr, resume=resuming)
    report, files = report_run(rd, preds)
    missing = sum(1 for p in preds.values() if p.error and p.error.code == "SIN_RESPUESTA")
    return RunOutcome(rd, run_id, report, files, len(refs), missing)
