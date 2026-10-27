"""T4 Integración: pipeline de punta a punta.

layout -> datos + GT -> PDF limpio (con marca "MOCK DATA") -> verificación de la
marca -> degradación -> proyección del GT -> escritura de archivos.

DIRECTRIZ CRÍTICA: solo se generan datos de PRUEBA. La marca de agua siempre se
dibuja en el render limpio (antes de degradar) y se verifica antes de escribir
nada; este módulo no expone ninguna opción para desactivarla.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Union

import pypdfium2 as pdfium

from contracts import (
    WATERMARK_TEXT,
    DegradationConfig,
    DocType,
    GroundTruth,
    TransformLog,
    Variant,
    default_variant,
)
from contracts.field_catalog import VARIANTS
from src import layouts
from src.data_injection import inject, validate_record
from src.degradation import PRESET_NAMES, degrade_pdf, load_config, load_preset, project_ground_truth

# Estructura de salida (archivos PLANOS dentro de la carpeta de cada documento):
#   <out>/<doc_type>/<sample_id>.pdf      PDF degradado (lo que consume el OCR)
#   <out>/<doc_type>/<sample_id>.json     Ground Truth
#   <out>/<doc_type>/manifest.jsonl
#   <out>/<doc_type>/_extras/<sample_id>.clean.pdf y <sample_id>.transform_log.json   (solo con extras)
PDF_SUFFIX = ".pdf"
GT_SUFFIX = ".json"
CLEAN_SUFFIX = ".clean.pdf"
LOG_SUFFIX = ".transform_log.json"
EXTRAS_DIR = "_extras"
LEGACY_GT_NAME = "gt.json"  # estructura antigua: una subcarpeta por muestra con gt.json
MANIFEST_NAME = "manifest.jsonl"

MIXED = "mixed"
EXTRANJERO_FRACTION = 0.2  # variant="mixed": ~80 % nacional / 20 % extranjero

DegradationSpec = Union[str, Path, DegradationConfig]
# Presets que resuelve el pipeline además de los de src.degradation.
EXTRA_PRESET_NAMES = ("scanner",)
ALL_PRESET_NAMES = tuple(dict.fromkeys((*PRESET_NAMES, *EXTRA_PRESET_NAMES)))

# --degradation mixed: para CADA muestra se elige un preset de forma determinista a partir de
# (seed, index) con un hash estable (SHA-256; nunca hash() de Python ni random global).
# Pesos por defecto (suman 100): la mayoría de los documentos salen "normales" (none + light =
# 55 %) y el 45 % restante son ejemplos más sucios (default, scanner, heavy).
MIXED_DEGRADATION = "mixed"
MIXED_DEGRADATION_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("none", 10),
    ("light", 45),
    ("default", 25),
    ("scanner", 12),
    ("heavy", 8),
)
DEGRADATION_CHOICES = (MIXED_DEGRADATION, *ALL_PRESET_NAMES)


class PipelineError(Exception):
    """Error del pipeline (configuración o datos inválidos)."""


class WatermarkMissingError(PipelineError):
    """El PDF limpio no contiene la marca de agua "MOCK DATA" en alguna página."""


ON_EXIST_CHOICES = ("error", "overwrite", "skip", "append")
_SAMPLE_INDEX_RE = re.compile(r"-(\d+)$")


@dataclass
class SampleResult:
    sample_id: str
    doc_type: str
    variant: str
    seed: int
    index: int
    degradation_seed: int
    degradation_preset: str  # preset efectivo de ESTA muestra (en `mixed`, el elegido por muestra)
    doc_dir: Path  # carpeta del documento: <raíz>/<doc_type>
    degraded_pdf: str  # `<sample_id>.pdf`; rutas relativas a la carpeta del documento
    gt_path: str  # `<sample_id>.json`
    clean_pdf: str | None  # `_extras/<sample_id>.clean.pdf` (solo con extras)
    transform_log: str | None  # `_extras/<sample_id>.transform_log.json` (solo con extras)
    ground_truth: GroundTruth = field(repr=False)
    log: TransformLog = field(repr=False)

    def path(self, rel: str) -> Path:
        """Ruta absoluta de un archivo de la muestra (`degraded_pdf`, `gt_path`, ...)."""
        return self.doc_dir / rel

    def manifest_entry(self) -> dict[str, Any]:
        """Entrada del manifest; las rutas son relativas a la carpeta del documento
        (`<raíz>/<doc_type>/`), que es donde vive `manifest.jsonl`. `clean_pdf` y
        `transform_log` son null si no se generó con extras."""
        return {
            "sample_id": self.sample_id,
            "doc_type": self.doc_type,
            "variant": self.variant,
            "seed": self.seed,
            "index": self.index,
            "degradation_seed": self.degradation_seed,
            "degradation_preset": self.degradation_preset,
            "degraded_pdf": self.degraded_pdf,
            "gt": self.gt_path,
            "clean_pdf": self.clean_pdf,
            "transform_log": self.transform_log,
        }


# ------------------------------------------------------------------ helpers

def derive_degradation_seed(seed: int, index: int) -> int:
    """Seed de degradación determinista a partir de (seed, index): 32 bits de SHA-256."""
    digest = hashlib.sha256(f"degradation:{int(seed)}:{int(index)}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def is_mixed(spec: object) -> bool:
    return isinstance(spec, str) and spec == MIXED_DEGRADATION


def mixed_degradation_preset(
    seed: int, doc_type: DocType | str, index: int,
    weights: tuple[tuple[str, int], ...] = MIXED_DEGRADATION_WEIGHTS,
) -> str:
    """Preset de degradación de la muestra `(seed, doc_type, index)` bajo `--degradation mixed`.

    Determinista y estable entre procesos/plataformas: 64 bits de SHA-256 de
    `(seed, doc_type, index)` -> posición en la tabla acumulada de `weights`. Incluir el
    `doc_type` evita que el mismo índice reciba el mismo preset en todos los documentos."""
    total = sum(w for _, w in weights)
    digest = hashlib.sha256(f"degradation_mix:{int(seed)}:{DocType(doc_type).value}:{int(index)}".encode()).digest()
    point = int.from_bytes(digest[:8], "big") % total
    acc = 0
    for name, weight in weights:
        acc += weight
        if point < acc:
            return name
    raise AssertionError("unreachable")  # pragma: no cover


def _scanner_config() -> DegradationConfig:
    """Preset 'scanner': 'default' colocado siempre sobre una cama de escáner (carta)."""
    cfg = load_preset("default").model_copy(deep=True)
    cfg.name = "scanner"
    for step in cfg.steps:
        if step.kind.value == "canvas_placement":
            step.probability = 1.0
    return cfg


def resolve_degradation(spec: DegradationSpec | None) -> DegradationConfig:
    """DegradationConfig | nombre de preset | ruta a un JSON -> DegradationConfig.

    `"mixed"` NO es un preset: se resuelve por muestra (`mixed_degradation_preset`)."""
    if spec is None:
        spec = "default"
    if is_mixed(spec):
        raise PipelineError("'mixed' se resuelve por muestra: use generate_sample/generate_batch")
    if isinstance(spec, DegradationConfig):
        return spec
    text = str(spec)
    if text == "scanner":
        try:  # si src.degradation incorpora un preset propio, tiene prioridad
            return load_preset("scanner")
        except ValueError:
            return _scanner_config()
    if text in PRESET_NAMES:
        return load_preset(text)
    path = Path(text)
    if path.is_file():
        try:
            return load_config(path)
        except Exception as exc:  # noqa: BLE001
            raise PipelineError(f"configuración de degradación inválida en {path}: {exc}") from exc
    raise PipelineError(
        f"degradación desconocida {text!r}: use {', '.join(DEGRADATION_CHOICES)} o la ruta de un .json"
    )


def new_random_seed() -> int:
    """Seed aleatoria de 31 bits (criptográficamente aleatoria, distinta en cada llamada)."""
    return secrets.randbelow(2**31)


def mixed_variant(doc_type: DocType, index: int) -> Variant | None:
    """Variante para variant='mixed': INE ~80 % nacional / 20 % extranjero, por índice
    (1 de cada 5, repartido de forma uniforme). Sin variantes -> None."""
    if len(VARIANTS[doc_type]) < 2:
        return None
    if int((index + 1) * EXTRANJERO_FRACTION) > int(index * EXTRANJERO_FRACTION):
        return Variant.EXTRANJERO
    return Variant.NACIONAL


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


def pdf_page_texts(pdf_bytes: bytes) -> list[str]:
    """Texto extraído (pypdfium2) de cada página de un PDF."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        texts = []
        for page in doc:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_bounded())
            textpage.close()
            page.close()
        return texts
    finally:
        doc.close()


def verify_watermark(pdf_bytes: bytes, expected_pages: int | None = None) -> None:
    """Exige "MOCK DATA" en el texto de TODAS las páginas del PDF limpio."""
    texts = pdf_page_texts(pdf_bytes)
    if not texts or (expected_pages is not None and len(texts) != expected_pages):
        raise WatermarkMissingError(
            f"el PDF limpio tiene {len(texts)} páginas; se esperaban {expected_pages}"
        )
    needle = _normalize_text(WATERMARK_TEXT)
    missing = [i for i, t in enumerate(texts) if needle not in _normalize_text(t)]
    if missing:
        raise WatermarkMissingError(
            f"falta la marca de agua {WATERMARK_TEXT!r} en las páginas {missing} del PDF limpio"
        )


def _dumps(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


# ------------------------------------------------------------------ API

def generate_sample(
    doc_type: DocType | str,
    seed: int,
    index: int,
    *,
    variant: Variant | str | None = None,
    degradation: DegradationSpec = "default",
    out_dir: str | Path,
    extras: bool = False,
) -> SampleResult:
    """Genera una muestra completa y escribe `out_dir/<doc_type>/<sample_id>.pdf` (PDF degradado) y
    `<sample_id>.json` (Ground Truth), archivos planos sin subcarpeta por muestra.

    `out_dir` es la carpeta raíz de datos (default del CLI: `data/`). Con `extras=True` también se
    guardan el PDF limpio y el transform_log en `out_dir/<doc_type>/_extras/`
    (`<sample_id>.clean.pdf`, `<sample_id>.transform_log.json`) y el GT los referencia; sin extras
    esos dos campos del GT son null (el PDF limpio y el log solo viven en memoria).

    Si falta la marca de agua en el PDF limpio lanza `WatermarkMissingError` sin
    escribir nada. La marca no se puede desactivar desde esta API.
    """
    dt = DocType(doc_type)
    seed, index = int(seed), int(index)
    if variant == MIXED:
        variant = mixed_variant(dt, index)
    elif variant is not None and len(VARIANTS[dt]) < 2:
        variant = None  # documentos sin variantes: se ignora
    var = default_variant(dt) if variant is None else Variant(variant)
    if var not in VARIANTS[dt]:
        raise PipelineError(f"variante {var.value!r} no válida para {dt.value}")
    if is_mixed(degradation):
        degradation = mixed_degradation_preset(seed, dt, index)
    cfg = resolve_degradation(degradation)

    # 1-2. layout, datos y GT (solo Faker con seed registrada)
    spec = layouts.load_spec(dt)
    record, gt = inject(spec, seed, index, var)
    errors = validate_record(dt, record, var)
    if errors:
        raise PipelineError(f"registro sintético inválido para {gt.sample_id}: {errors[:5]}")
    gt = GroundTruth.model_validate(gt.model_dump(mode="json"))

    # 3. PDF limpio: la marca de agua siempre se dibuja (watermark=True fijo)
    clean_pdf = layouts.render_document(spec, record, variant=var, watermark=True)

    # 4. la marca debe estar en el PDF limpio ANTES de degradar
    verify_watermark(clean_pdf, expected_pages=len(gt.pages))

    # 5. degradación con seed derivada de (seed, index)
    dseed = derive_degradation_seed(seed, index)
    faces = [(p.face, (p.width_mm, p.height_mm)) for p in gt.pages]
    degraded_pdf, log = degrade_pdf(clean_pdf, cfg, sample_id=gt.sample_id,
                                    degradation_seed=dseed, faces=faces)

    # 6. proyección del GT y referencias relativas a la carpeta del documento
    gt = project_ground_truth(gt, log)
    sid = gt.sample_id
    degraded_rel, gt_rel = f"{sid}{PDF_SUFFIX}", f"{sid}{GT_SUFFIX}"
    clean_rel = f"{EXTRAS_DIR}/{sid}{CLEAN_SUFFIX}" if extras else None
    log_rel = f"{EXTRAS_DIR}/{sid}{LOG_SUFFIX}" if extras else None
    gt.clean_pdf, gt.degraded_pdf, gt.transform_log = clean_rel, degraded_rel, log_rel
    gt = GroundTruth.model_validate(gt.model_dump(mode="json"))
    if not gt.synthetic.is_synthetic or gt.synthetic.watermark_text != WATERMARK_TEXT:
        raise PipelineError("el GT no está marcado como sintético con la marca de agua")
    for page in gt.pages:
        for f in page.fields:
            if f.transformed is None:
                raise PipelineError(f"campo {f.name} sin geometría transformada")

    # 7. escritura (todo validado en memoria antes de tocar el disco)
    doc_dir = Path(out_dir) / dt.value
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / degraded_rel).write_bytes(degraded_pdf)
    (doc_dir / gt_rel).write_bytes(_dumps(gt.model_dump(mode="json")))
    if extras:
        (doc_dir / EXTRAS_DIR).mkdir(exist_ok=True)
        (doc_dir / clean_rel).write_bytes(clean_pdf)
        (doc_dir / log_rel).write_bytes(_dumps(log.model_dump(mode="json")))

    return SampleResult(
        sample_id=sid, doc_type=dt.value, variant=var.value, seed=seed, index=index,
        degradation_seed=dseed, degradation_preset=cfg.name, doc_dir=doc_dir,
        degraded_pdf=degraded_rel, gt_path=gt_rel, clean_pdf=clean_rel, transform_log=log_rel,
        ground_truth=gt, log=log,
    )


def doc_dir_for(out_dir: str | Path, doc_type: DocType | str) -> Path:
    return Path(out_dir) / DocType(doc_type).value


def _own_files(doc_dir: Path) -> list[Path]:
    """Archivos planos de la carpeta del documento con el patrón de nombres de la herramienta
    (`<doc_type>-*.pdf` y `<doc_type>-*.json`). Nunca incluye archivos con otros nombres."""
    if not doc_dir.is_dir():
        return []
    prefix = doc_dir.name + "-"
    return sorted(p for p in doc_dir.iterdir()
                  if p.is_file() and p.name.startswith(prefix) and p.suffix in (PDF_SUFFIX, GT_SUFFIX))


def _legacy_sample_dirs(doc_dir: Path) -> list[Path]:
    """Subcarpetas de la estructura antigua (una por muestra, con `gt.json`)."""
    if not doc_dir.is_dir():
        return []
    return sorted(p for p in doc_dir.iterdir() if p.is_dir() and (p / LEGACY_GT_NAME).is_file())


def existing_samples(doc_dir: str | Path) -> list[str]:
    """`sample_id` de las muestras ya generadas en la carpeta de un documento: archivos planos
    `<doc_type>-*.pdf|json` y, por compatibilidad, subcarpetas antiguas con `gt.json`."""
    d = Path(doc_dir)
    ids = {p.name[: -len(p.suffix)] for p in _own_files(d)}
    ids |= {p.name for p in _legacy_sample_dirs(d)}
    return sorted(ids)


def has_samples(doc_dir: str | Path) -> bool:
    """True si la carpeta del documento ya tiene muestras (o su `manifest.jsonl`)."""
    d = Path(doc_dir)
    return bool(existing_samples(d)) or (d / MANIFEST_NAME).is_file()


def sample_index(sample_id: str | Path) -> int | None:
    """Índice numérico de un sample_id (`ine_2016-nacional-42-000007` -> 7)."""
    m = _SAMPLE_INDEX_RE.search(Path(sample_id).name)
    return int(m.group(1)) if m else None


def next_free_index(doc_dir: str | Path) -> int:
    """Primer índice libre tras las muestras existentes (0 si no hay)."""
    idx = [i for i in (sample_index(s) for s in existing_samples(doc_dir)) if i is not None]
    return max(idx) + 1 if idx else 0


def clear_doc_dir(doc_dir: str | Path) -> None:
    """Borra lo que generó esta herramienta en la carpeta de un documento: los archivos
    `<doc_type>-*.pdf|json`, `manifest.jsonl`, los extras generados (`_extras/<doc_type>-*`, y la
    carpeta si queda vacía) y las subcarpetas antiguas con `gt.json`. Nunca borra otros archivos."""
    d = Path(doc_dir)
    for p in _own_files(d):
        p.unlink()
    for p in _legacy_sample_dirs(d):
        shutil.rmtree(p)
    (d / MANIFEST_NAME).unlink(missing_ok=True)
    extras = d / EXTRAS_DIR
    if extras.is_dir():
        prefix = d.name + "-"
        for p in extras.iterdir():
            if p.is_file() and p.name.startswith(prefix) and p.name.endswith((CLEAN_SUFFIX, LOG_SUFFIX)):
                p.unlink()
        if not any(extras.iterdir()):
            extras.rmdir()


def write_manifest(results: list[SampleResult], out_dir: str | Path, *, merge: bool = True) -> list[Path]:
    """Escribe `<out_dir>/<doc_type>/manifest.jsonl` (una línea JSON por muestra) por cada
    tipo de documento presente en `results`. Las rutas del manifest son relativas a la
    carpeta del documento. Con `merge=True` se conservan las entradas previas de otros
    sample_id (modo añadir). Devuelve las rutas escritas."""
    paths: list[Path] = []
    by_doc: dict[str, list[SampleResult]] = {}
    for r in results:
        by_doc.setdefault(r.doc_type, []).append(r)
    for doc_type, rs in by_doc.items():
        d = doc_dir_for(out_dir, doc_type)
        d.mkdir(parents=True, exist_ok=True)
        path = d / MANIFEST_NAME
        entries = {r.sample_id: r.manifest_entry() for r in rs}
        if merge and path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    old = json.loads(line)
                    entries.setdefault(old["sample_id"], old)
        ordered = sorted(entries.values(), key=lambda e: (e["index"], e["sample_id"]))
        path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ordered),
                        encoding="utf-8")
        paths.append(path)
    return paths


def generate_batch(
    doc_type: DocType | str,
    n: int,
    seed: int,
    *,
    variant: Variant | str | None = None,
    degradation: DegradationSpec = "default",
    out_dir: str | Path,
    write_manifest_file: bool = True,
    start_index: int = 0,
    extras: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> list[SampleResult]:
    """Genera `n` muestras (índices `start_index .. start_index+n-1`) como archivos planos
    `<sample_id>.pdf` + `<sample_id>.json` en `out_dir/<doc_type>/` y escribe
    `out_dir/<doc_type>/manifest.jsonl`. Con `extras=True` guarda además el PDF limpio y el
    transform_log en `_extras/`.

    `variant="mixed"`: INE ~80 % nacional / 20 % extranjero (determinista por índice).
    `degradation="mixed"`: cada muestra usa un preset elegido por `(seed, doc_type, index)` con los pesos
    de `MIXED_DEGRADATION_WEIGHTS` (registrado en `degradation_preset` del manifest).
    `progress(hechas, n)` se llama tras cada muestra. Esta función sobrescribe muestras con
    el mismo sample_id; la política de carpetas existentes (`--on-exist`) vive en `src.cli`.
    """
    if n < 0:
        raise PipelineError("n debe ser >= 0")
    if start_index < 0:
        raise PipelineError("start_index debe ser >= 0")
    # falla pronto si la config es inválida; `mixed` se resuelve por muestra
    cfg = degradation if is_mixed(degradation) else resolve_degradation(degradation)
    results = []
    for k in range(n):
        results.append(generate_sample(doc_type, seed, start_index + k, variant=variant,
                                       degradation=cfg, out_dir=out_dir, extras=extras))
        if progress is not None:
            progress(k + 1, n)
    if write_manifest_file and results:
        write_manifest(results, out_dir)
    return results


__all__ = [
    "ALL_PRESET_NAMES",
    "DEGRADATION_CHOICES",
    "MIXED_DEGRADATION",
    "MIXED_DEGRADATION_WEIGHTS",
    "EXTRAS_DIR",
    "ON_EXIST_CHOICES",
    "PipelineError",
    "SampleResult",
    "WatermarkMissingError",
    "clear_doc_dir",
    "derive_degradation_seed",
    "doc_dir_for",
    "existing_samples",
    "generate_batch",
    "generate_sample",
    "has_samples",
    "is_mixed",
    "mixed_degradation_preset",
    "mixed_variant",
    "new_random_seed",
    "next_free_index",
    "resolve_degradation",
    "sample_index",
    "verify_watermark",
    "write_manifest",
]
