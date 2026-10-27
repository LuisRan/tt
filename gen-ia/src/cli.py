"""CLI: `generate --doc-type ... --n N --seed S` (también `python -m src`).

Sin argumentos y con una TTY (o con `menu` / `generate -i`) lanza el menú interactivo
(`src/menu.py`). Estructura de salida:

    <out>/run.json
    <out>/<doc_type>/manifest.jsonl
    <out>/<doc_type>/<sample_id>.pdf      PDF degradado (lo que consume el OCR)
    <out>/<doc_type>/<sample_id>.json     Ground Truth
    <out>/<doc_type>/_extras/             solo con --extras: <sample_id>.clean.pdf y
                                          <sample_id>.transform_log.json

Archivos planos dentro de la carpeta de cada documento (sin subcarpeta por muestra). Rutas
relativas: tanto en el GT (`clean_pdf`, `degraded_pdf`, `transform_log`) como en
`manifest.jsonl` son relativas a la carpeta del DOCUMENTO.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from contracts import CONTRACTS_VERSION, WATERMARK_TEXT, DocType
from src import layouts
from src.data_injection import GENERATOR_VERSION
from src.pipeline import (
    DEGRADATION_CHOICES,
    MIXED_DEGRADATION,
    MIXED_DEGRADATION_WEIGHTS,
    ON_EXIST_CHOICES,
    PipelineError,
    SampleResult,
    clear_doc_dir,
    doc_dir_for,
    existing_samples,
    generate_batch,
    has_samples,
    is_mixed,
    new_random_seed,
    resolve_degradation,
    sample_index,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130

DEFAULT_OUT = "data"
DOCKER_OUT = "/app/data"  # carpeta de datos dentro del contenedor (montada desde ./data)
DEFAULT_DEGRADATION = MIXED_DEGRADATION
RUN_JSON = "run.json"
RUN_SCHEMA_VERSION = "1.0"

ALL_DOC_TYPES = [d.value for d in DocType]
DOC_CHOICES = ALL_DOC_TYPES + ["all"]
VARIANT_CHOICES = ["nacional", "extranjero", "mixed"]
ON_EXIST_CLI_CHOICES = list(ON_EXIST_CHOICES)
RandomSeedFn = Callable[[], int]


def in_docker() -> bool:
    """True si corre en el contenedor (la imagen define OCR_DOCKER=1)."""
    return os.environ.get("OCR_DOCKER") == "1"


def default_out_dir() -> str:
    """Carpeta de salida sugerida: `/app/data` en Docker, `data` en el equipo."""
    return DOCKER_OUT if in_docker() else DEFAULT_OUT


# ------------------------------------------------------------------ especificación de un run

@dataclass
class RunSpec:
    """Todo lo necesario para repetir un run (lo comparten el CLI y el menú)."""

    counts: dict[str, int]  # doc_type -> muestras (el orden se respeta)
    seed: int = 0
    variant: str | None = None
    degradation: str = DEFAULT_DEGRADATION
    out: str = DEFAULT_OUT
    on_exist: str = "error"
    start_index: int = 0
    extras: bool = False
    argv0: str = field(default="python -m src", repr=False)

    @property
    def doc_types(self) -> list[str]:
        return list(self.counts)

    def validate(self) -> None:
        if not self.counts:
            raise PipelineError("no hay documentos que generar")
        for dt, n in self.counts.items():
            DocType(dt)
            if int(n) < 1:
                raise PipelineError(f"n debe ser >= 1 (documento {dt})")
        if self.on_exist not in ON_EXIST_CHOICES:
            raise PipelineError(f"--on-exist inválido: {self.on_exist!r}")
        if self.start_index < 0:
            raise PipelineError("--start-index debe ser >= 0")

    def command(self) -> str:
        """Comando `generate` equivalente (se puede repetir sin menú)."""
        docs = self.doc_types
        doc_arg = "all" if docs == ALL_DOC_TYPES else ",".join(docs)
        ns = set(self.counts.values())
        n_arg = str(next(iter(ns))) if len(ns) == 1 else ",".join(f"{d}={n}" for d, n in self.counts.items())
        parts = ["generate", "--doc-type", doc_arg, "--n", n_arg, "--seed", str(self.seed)]
        if self.variant is not None:
            parts += ["--variant", self.variant]
        parts += ["--degradation", self.degradation, "--out", self.out]
        if self.extras:
            parts.append("--extras")
        if self.on_exist != "error":
            parts += ["--on-exist", self.on_exist]
        if self.start_index:
            parts += ["--start-index", str(self.start_index)]
        return self.argv0 + " " + " ".join(shlex.quote(p) for p in parts)


@dataclass
class RunOutcome:
    spec: RunSpec
    results: list[SampleResult]
    skipped: list[str]
    run_json: Path | None
    degradation_name: str

    @property
    def out(self) -> Path:
        return Path(self.spec.out)


def run_info(spec: RunSpec, degradation_name: str, render_dpi: int) -> dict:
    """Contenido de run.json: determinista (sin reloj, rutas absolutas ni datos de la máquina)."""
    degradation: dict = {"name": degradation_name, "source": spec.degradation, "render_dpi": render_dpi}
    if is_mixed(spec.degradation):  # pesos usados (en %); el preset de cada muestra va en manifest.jsonl
        degradation["weights"] = {name: w for name, w in MIXED_DEGRADATION_WEIGHTS}
    return {
        "run_schema_version": RUN_SCHEMA_VERSION,
        "synthetic": {"is_synthetic": True, "watermark_text": WATERMARK_TEXT, "faker_locale": "es_MX"},
        "doc_types": spec.doc_types,
        "n": dict(spec.counts),
        "seed": spec.seed,
        "start_index": spec.start_index,
        "extras": spec.extras,
        "variant": spec.variant,
        "degradation": degradation,
        "versions": {
            "contracts": CONTRACTS_VERSION,
            "generator": GENERATOR_VERSION,
            "layouts": {dt: layouts.load_spec(dt).layout_version for dt in spec.doc_types},
        },
        "command": spec.command(),
    }


def run_json_bytes(spec: RunSpec, degradation_name: str, render_dpi: int) -> bytes:
    text = json.dumps(run_info(spec, degradation_name, render_dpi), ensure_ascii=False, indent=2)
    return (text + "\n").encode("utf-8")


ProgressFn = Callable[[int, int, str, int, int], None]  # (doc_pos, doc_total, doc_type, hechas, n)


def check_existing(spec: RunSpec) -> list[str]:
    """Aplica la política `on_exist` sin escribir nada. Devuelve los documentos omitidos
    (`skip`); lanza `PipelineError` si la política lo exige."""
    out = Path(spec.out)
    skipped: list[str] = []
    for dt, n in spec.counts.items():
        doc_dir = doc_dir_for(out, dt)
        if not has_samples(doc_dir):
            continue
        existing = existing_samples(doc_dir)
        if spec.on_exist == "error":
            raise PipelineError(
                f"{doc_dir_for(out, dt)} ya contiene {len(existing)} muestra(s); use "
                "--on-exist overwrite | skip | append (con --start-index) o elija otra carpeta"
            )
        if spec.on_exist == "skip":
            skipped.append(dt)
        elif spec.on_exist == "append":
            taken = {sample_index(p) for p in existing}
            clash = sorted(i for i in range(spec.start_index, spec.start_index + n) if i in taken)
            if clash:
                raise PipelineError(f"{dt}: los índices {clash[:5]} ya existen; use otro --start-index")
    return skipped


def execute_run(spec: RunSpec, progress: ProgressFn | None = None) -> RunOutcome:
    """Ejecuta el run: política de carpetas existentes, `generate_batch` por documento y run.json.

    Las comprobaciones se hacen ANTES de escribir nada. `on_exist`: `error` (default) falla
    si la carpeta del documento ya tiene muestras (archivos `<doc_type>-*.pdf|json`, `manifest.jsonl`
    o subcarpetas de la estructura antigua); `overwrite` borra solo lo generado por la herramienta y
    regenera; `skip` omite ese documento; `append` añade desde `start_index` (falla si algún índice
    ya existe).
    """
    spec.validate()
    if is_mixed(spec.degradation):
        # `mixed` elige un preset por muestra; todos los presets de la tabla deben resolver
        for name, _ in MIXED_DEGRADATION_WEIGHTS:
            resolve_degradation(name)
        degradation = MIXED_DEGRADATION
        deg_name, render_dpi = MIXED_DEGRADATION, resolve_degradation("default").render_dpi
    else:
        degradation = resolve_degradation(spec.degradation)
        deg_name, render_dpi = degradation.name, degradation.render_dpi
    out = Path(spec.out)
    if out.exists() and not out.is_dir():
        raise PipelineError(f"la salida {out} existe y no es una carpeta")
    skipped = check_existing(spec)

    todo = [(dt, n) for dt, n in spec.counts.items() if dt not in skipped]
    results: list[SampleResult] = []
    for pos, (dt, n) in enumerate(todo, start=1):
        if spec.on_exist == "overwrite":
            clear_doc_dir(doc_dir_for(out, dt))
        cb = None
        if progress is not None:
            def cb(done: int, total: int, _p: int = pos, _dt: str = dt) -> None:
                progress(_p, len(todo), _dt, done, total)
        results += generate_batch(dt, n, spec.seed, variant=spec.variant, degradation=degradation,
                                  out_dir=out, start_index=spec.start_index,
                                  extras=spec.extras, progress=cb)

    run_json = None
    if results:
        run_json = out / RUN_JSON
        run_json.write_bytes(run_json_bytes(spec, deg_name, render_dpi))
    return RunOutcome(spec, results, skipped, run_json, deg_name)


def preset_counts(results: list[SampleResult]) -> dict[str, int]:
    """Reparto real de presets del lote, de más a menos frecuente (empates: orden de los pesos)."""
    order = [n for n, _ in MIXED_DEGRADATION_WEIGHTS]
    counts = Counter(r.degradation_preset for r in results)
    key = lambda kv: (-kv[1], order.index(kv[0]) if kv[0] in order else len(order))  # noqa: E731
    return dict(sorted(counts.items(), key=key))


def format_preset_mix(results: list[SampleResult]) -> str:
    return " ".join(f"{k}={v}" for k, v in preset_counts(results).items())


def format_summary(outcome: RunOutcome) -> list[str]:
    spec, results = outcome.spec, outcome.results
    lines = ["Resumen (datos sintéticos de PRUEBA, marca 'MOCK DATA' verificada en cada PDF limpio)"]
    per_doc = Counter((r.doc_type, r.variant) for r in results)
    for (dt, var), count in sorted(per_doc.items()):
        lines.append(f"  {dt:<28} variante={var:<10} muestras={count:<4} PDFs={count}")
    for dt in outcome.skipped:
        lines.append(f"  {dt:<28} omitido (la carpeta ya tiene muestras, --on-exist skip)")
    pages = sum(len(r.ground_truth.pages) for r in results)
    lines.append(f"  total: {len(results)} muestras, {pages} páginas | degradación={outcome.degradation_name}"
                 f" | seed={spec.seed}")
    if is_mixed(spec.degradation):
        lines.append("  presets de degradación del lote: " + format_preset_mix(results))
    out = outcome.out
    lines.append(f"  salida:   {out.resolve()}")
    for dt in sorted({r.doc_type for r in results}):
        n_pdf = sum(1 for r in results if r.doc_type == dt)
        extra = " + _extras/" if spec.extras else ""
        lines.append(f"  carpeta:  {doc_dir_for(out, dt).resolve()}  "
                     f"({n_pdf} PDF + {n_pdf} JSON + manifest.jsonl{extra})")
    if outcome.run_json:
        lines.append(f"  run.json: {outcome.run_json.resolve()}")
    return lines


# ------------------------------------------------------------------ argumentos

def parse_doc_types(text: str) -> list[str]:
    """`ine_2016,curp_2019` | `all` -> lista sin duplicados (orden canónico para `all`)."""
    out: list[str] = []
    for tok in (t.strip() for t in text.split(",")):
        if not tok:
            continue
        if tok == "all":
            names = ALL_DOC_TYPES
        elif tok in ALL_DOC_TYPES:
            names = [tok]
        else:
            raise argparse.ArgumentTypeError(
                f"documento inválido: {tok!r} (opciones: {', '.join(DOC_CHOICES)}; "
                "se admiten varios separados por comas)")
        out += [n for n in names if n not in out]
    if not out:
        raise argparse.ArgumentTypeError("indique al menos un documento")
    return out


def parse_counts(doc_types: list[str], text: str) -> dict[str, int]:
    """`--n 5` (igual para todos) o `--n ine_2016=5,curp_2019=3`."""
    text = str(text).strip()
    try:
        if "=" not in text:
            counts = {d: int(text) for d in doc_types}
        else:
            counts = {}
            for tok in text.split(","):
                name, _, val = tok.partition("=")
                name = name.strip()
                if name not in doc_types:
                    raise ValueError(f"{name!r} no está en --doc-type")
                counts[name] = int(val)
            missing = [d for d in doc_types if d not in counts]
            if missing:
                raise ValueError(f"falta n para {', '.join(missing)}")
            counts = {d: counts[d] for d in doc_types}
    except ValueError as exc:
        raise PipelineError(f"--n inválido: {exc}") from exc
    if any(v < 1 for v in counts.values()):
        raise PipelineError("--n debe ser >= 1")
    return counts


def _add_generate_args(g: argparse.ArgumentParser) -> None:
    g.add_argument("--doc-type", type=parse_doc_types, default=None, metavar="DOC[,DOC...]",
                   help=f"tipo(s) de documento ({', '.join(ALL_DOC_TYPES)}) separados por comas, "
                   "o 'all' (N de cada uno)")
    g.add_argument("--n", default="1", metavar="N|DOC=N,...",
                   help="muestras por documento (default 1); o por documento: ine_2016=5,curp_2019=3")
    g.add_argument("--seed", type=int, default=None,
                   help="seed de datos. Sin --seed se genera una ALEATORIA distinta en cada ejecución "
                   "(se imprime al final y queda en run.json); con --seed N el lote es reproducible")
    g.add_argument("--variant", choices=VARIANT_CHOICES, default=None,
                   help="variante de la INE (ignorada en documentos sin variantes)")
    g.add_argument("--degradation", default=DEFAULT_DEGRADATION, metavar="PRESET|RUTA.json",
                   help=f"preset ({'|'.join(DEGRADATION_CHOICES)}) o ruta a un JSON (default: "
                   f"{DEFAULT_DEGRADATION}: mezcla de presets por muestra, mayoría de documentos limpios)")
    g.add_argument("--out", default=DEFAULT_OUT,
                   help=f"carpeta de datos (default {DEFAULT_OUT}/); una carpeta por documento con los PDF y su "
                   "JSON directamente (sin subcarpetas por muestra)")
    g.add_argument("--extras", action="store_true",
                   help="guarda también el PDF limpio y el log de transformaciones en "
                   "<documento>/_extras/ (por defecto solo PDF degradado + JSON)")
    g.add_argument("--on-exist", choices=ON_EXIST_CLI_CHOICES, default="error",
                   help="si la carpeta del documento ya tiene muestras: error (default), overwrite "
                   "(las borra y regenera), skip (omite el documento) o append (añade desde --start-index)")
    g.add_argument("--start-index", type=int, default=0, metavar="K",
                   help="índice de la primera muestra (default 0; pensado para --on-exist append)")
    g.add_argument("-i", "--interactive", action="store_true",
                   help="lanza el menú interactivo (requiere teclado; no usar en scripts)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="generate",
        description="Genera datos SINTÉTICOS DE PRUEBA (PDF degradado + Ground Truth JSON). "
        "Todos los PDF llevan la marca de agua 'MOCK DATA'. Sin argumentos y con una terminal "
        "se abre el menú interactivo.",
    )
    sub = p.add_subparsers(dest="command")
    _add_generate_args(sub.add_parser("generate", help="genera muestras sintéticas"))
    m = sub.add_parser("menu", help="menú interactivo para elegir qué y cuánto generar")
    m.add_argument("--out", default=None,
                   help=f"carpeta de salida sugerida (default {DEFAULT_OUT}/; {DOCKER_OUT} dentro de Docker)")
    # También admite `generate --doc-type ...` sin subcomando (script de consola `generate`).
    _add_generate_args(p)
    return p


def stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _launch_menu(default_out: str | None) -> int:
    from src.menu import run_menu  # import diferido: menu importa este módulo

    return run_menu(default_out=default_out or default_out_dir())


def main(argv: list[str] | None = None, random_seed_fn: RandomSeedFn = new_random_seed) -> int:
    raw = sys.argv[1:] if argv is None else list(argv)
    parser = build_parser()
    if not raw:
        if stdin_is_tty():
            return _launch_menu(None)
        parser.print_help()  # sin TTY y sin flags: nunca se queda esperando input
        return EXIT_USAGE
    args = parser.parse_args(raw)  # argparse sale con código 2 ante uso inválido
    if args.command == "menu" or args.interactive:
        return _launch_menu(args.out)
    if args.doc_type is None:
        parser.print_usage(sys.stderr)
        print("error: falta --doc-type", file=sys.stderr)
        return EXIT_USAGE

    try:
        counts = parse_counts(args.doc_type, args.n)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    seed = random_seed_fn() if args.seed is None else args.seed  # sin --seed: aleatoria
    spec = RunSpec(counts=counts, seed=seed, variant=args.variant, degradation=args.degradation,
                   out=args.out, on_exist=args.on_exist, start_index=args.start_index,
                   extras=args.extras)
    try:
        outcome = execute_run(spec)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return EXIT_CANCELLED
    except Exception as exc:  # noqa: BLE001 - el CLI nunca debe terminar en 0 ante un fallo
        print(f"error inesperado ({type(exc).__name__}): {exc}", file=sys.stderr)
        return EXIT_ERROR

    print("\n".join(format_summary(outcome)))
    if args.seed is None:
        print(f"  seed aleatoria usada: {seed}")
    print("\nPara repetirlo exactamente (misma seed):")
    print(f"  {spec.command()}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
