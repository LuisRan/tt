"""Menú interactivo (solo biblioteca estándar) para elegir qué y cuánto generar.

La lógica de parseo/validación son funciones puras (`parse_*`) y la interacción
(`collect_run_spec`, `run_menu`) acepta `input_fn` / `print_fn` inyectables, de modo que
se prueba sin TTY. Tras confirmar, ejecuta el mismo pipeline que el CLI
(`src.cli.execute_run` -> `generate_batch`).

Cancelar (Ctrl+C / Ctrl+D) termina con código 130, sin traza.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, TypeVar

from src.cli import (
    ALL_DOC_TYPES,
    DOCKER_OUT,
    EXIT_CANCELLED,
    EXIT_ERROR,
    EXIT_OK,
    RandomSeedFn,
    RunSpec,
    default_out_dir,
    execute_run,
    format_summary,
    in_docker,
)
from src.pipeline import (
    MIXED_DEGRADATION,
    MIXED_DEGRADATION_WEIGHTS,
    PipelineError,
    doc_dir_for,
    existing_samples,
    has_samples,
    new_random_seed,
    next_free_index,
    resolve_degradation,
)

T = TypeVar("T")
InputFn = Callable[[str], str]
PrintFn = Callable[..., None]

# (doc_type, nombre mostrado) en el orden del menú
DOCUMENTS: list[tuple[str, str]] = [
    ("ine_2016", "INE 2016 (anverso y reverso)"),
    ("curp_2019", "Constancia CURP 2019"),
    ("acta_nacimiento_2015", "Acta de Nacimiento 2015"),
    ("pasaporte_electronico_2021", "Pasaporte electrónico 2021"),
]
assert [d for d, _ in DOCUMENTS] == ALL_DOC_TYPES

MAX_PER_DOC = 2000
DEFAULT_COUNT = 10
SECONDS_PER_SAMPLE = (1, 2)  # estimación orientativa (mín, máx)
MAX_SEED = 2**32 - 1

VARIANTS: list[tuple[str, str]] = [
    ("nacional", "nacional (credencial para votar en México)"),
    ("extranjero", "extranjero (\"para votar desde el extranjero\")"),
    ("mixed", "mixta (~80 % nacional / 20 % extranjero)"),
]
_PCT = {name: w for name, w in MIXED_DEGRADATION_WEIGHTS}
_DIRTY_PCT = 100 - _PCT["none"] - _PCT["light"]
DEGRADATIONS: list[tuple[str, str]] = [
    (MIXED_DEGRADATION, "mixed: mezcla recomendada — mayoría de documentos normales/limpios y algunos "
     f"escaneados, sucios o muy degradados en el mismo lote ({_DIRTY_PCT} % de ellos más sucios)"),
    ("none", "none: sin degradación, solo el render a 300 dpi"),
    ("light", "light: ruido y rotación suaves, JPEG moderado"),
    ("default", "default: rotación, sombras, ruido, remuestreo y JPEG variados"),
    ("heavy", "heavy: perspectiva, ruido y compresión fuertes"),
    ("scanner", "scanner: como default, sobre una cama de escáner tamaño carta"),
]
CUSTOM_DEGRADATION_OPTION = len(DEGRADATIONS) + 1  # ruta a un JSON
DEFAULT_DEGRADATION_OPTION = "1"  # mixed

YES = {"s", "si", "sí", "y", "yes"}
NO = {"n", "no"}


class MenuCancelled(Exception):
    """El usuario canceló (Ctrl+C / Ctrl+D)."""


# ------------------------------------------------------------------ parseo puro

def parse_selection(text: str, n_options: int) -> list[int]:
    """`'1,3'`, `'2-4'`, `'a'` (todos), combinables (`'1,3-4'`) -> lista ordenada sin duplicados.

    Lanza `ValueError` con un mensaje en español si la entrada está vacía, tiene basura o
    números fuera de `1..n_options`.
    """
    raw = text.strip().lower()
    if not raw:
        raise ValueError("escriba al menos una opción (por ejemplo 1,3 o a para todos)")
    if raw in {"a", "t", "todos", "todas"}:
        return list(range(1, n_options + 1))
    chosen: set[int] = set()
    for tok in raw.replace(" ", "").split(","):
        if not tok:
            raise ValueError("hay una coma de más")
        if "-" in tok:
            lo_s, _, hi_s = tok.partition("-")
            if not (lo_s.isdigit() and hi_s.isdigit()):
                raise ValueError(f"rango inválido: {tok!r} (use por ejemplo 2-4)")
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi:
                raise ValueError(f"rango invertido: {tok!r}")
            nums = range(lo, hi + 1)
        elif tok.isdigit():
            nums = [int(tok)]
        else:
            raise ValueError(f"no entiendo {tok!r}: use números, rangos (1-3) o a")
        for n in nums:
            if not 1 <= n <= n_options:
                raise ValueError(f"la opción {n} no existe (hay de 1 a {n_options})")
            chosen.add(n)
    return sorted(chosen)


def parse_count(text: str, lo: int = 1, hi: int = MAX_PER_DOC) -> int:
    raw = text.strip()
    if not raw.lstrip("-").isdigit():
        raise ValueError("escriba un número entero")
    n = int(raw)
    if n < lo:
        raise ValueError(f"la cantidad debe ser al menos {lo}")
    if n > hi:
        raise ValueError(f"la cantidad máxima por documento es {hi}")
    return n


def parse_seed(text: str) -> int:
    raw = text.strip()
    if not raw.isdigit():
        raise ValueError("la seed debe ser un entero >= 0")
    n = int(raw)
    if n > MAX_SEED:
        raise ValueError(f"la seed máxima es {MAX_SEED}")
    return n


def parse_seed_or_random(text: str) -> int | None:
    """Vacío -> None (seed aleatoria); entero >= 0 -> esa seed."""
    if not text.strip():
        return None
    return parse_seed(text)


def parse_yes_no(text: str) -> bool:
    raw = text.strip().lower()
    if raw in YES:
        return True
    if raw in NO:
        return False
    raise ValueError("responda s (sí) o n (no)")


def parse_option(text: str, options: list[tuple[str, str]], extra: int = 0) -> str | int:
    """Número (1..len) o nombre de la opción -> nombre. Con `extra`, ese número extra devuelve el
    entero (para la opción "otra" que pide más datos)."""
    raw = text.strip().lower()
    names = [n for n, _ in options]
    if raw.isdigit():
        k = int(raw)
        if 1 <= k <= len(names):
            return names[k - 1]
        if extra and k == extra:
            return extra
        raise ValueError(f"la opción {k} no existe (hay de 1 a {len(names) + (1 if extra else 0)})")
    if raw in names:
        return raw
    raise ValueError(f"no entiendo {text.strip()!r}: escriba el número o el nombre")


def parse_degradation(text: str, allow_custom: bool = True) -> str:
    """Número (1-6 presets; 7 pide la ruta), nombre de preset (incl. `mixed`) o ruta existente a
    un .json -> nombre/ruta validados con el pipeline. Con `allow_custom`, la opción 7 devuelve ''
    (señal para pedir la ruta)."""
    raw = text.strip()
    if not raw:
        raise ValueError("escriba una opción")
    if allow_custom and raw.isdigit() and int(raw) == CUSTOM_DEGRADATION_OPTION:
        return ""
    if raw.isdigit() or raw.lower() in [n for n, _ in DEGRADATIONS]:
        return str(parse_option(raw, DEGRADATIONS))
    try:
        resolve_degradation(raw)
    except PipelineError as exc:
        raise ValueError(str(exc)) from exc
    return raw


def parse_out_dir(text: str) -> str:
    raw = text.strip()
    if not raw:
        raise ValueError("escriba una carpeta")
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":  # carpeta arrastrada a la terminal
        raw = raw[1:-1].strip()
    p = Path(raw).expanduser()  # el shell no expande ~ dentro de un input()
    if p.exists() and not p.is_dir():
        raise ValueError(f"{raw!r} existe y no es una carpeta")
    return str(p)


def estimate_seconds(total_samples: int) -> tuple[int, int]:
    return total_samples * SECONDS_PER_SAMPLE[0], total_samples * SECONDS_PER_SAMPLE[1]


def format_duration(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


def format_estimate(total_samples: int) -> str:
    lo, hi = estimate_seconds(total_samples)
    per = f"~{SECONDS_PER_SAMPLE[0]}–{SECONDS_PER_SAMPLE[1]} s por muestra"
    word = "muestra" if total_samples == 1 else "muestras"
    return f"{total_samples} {word} en total ({per}): tiempo estimado {format_duration(lo)}–{format_duration(hi)}"


# ------------------------------------------------------------------ interacción

class _Session:
    def __init__(self, input_fn: InputFn, print_fn: PrintFn):
        self.input_fn, self.print = input_fn, print_fn

    def ask(self, prompt: str, parse: Callable[[str], T], default: str | None = None,
            hint: str | None = None) -> T:
        """Pregunta hasta obtener una entrada válida. Enter acepta `default` (si lo hay);
        `hint` cambia lo que se muestra entre corchetes (p. ej. S/n)."""
        shown = hint if hint is not None else default
        suffix = f" [{shown}]" if shown is not None else ""
        while True:
            try:
                text = self.input_fn(f"{prompt}{suffix}: ")
            except (EOFError, KeyboardInterrupt) as exc:
                raise MenuCancelled from exc
            if not text.strip() and default is not None:
                text = default
            try:
                return parse(text)
            except ValueError as exc:
                self.print(f"  Entrada no válida: {exc}")


def collect_run_spec(
    input_fn: InputFn = input,
    print_fn: PrintFn = print,
    default_out: str | None = None,
    random_seed_fn: RandomSeedFn = new_random_seed,
) -> RunSpec | None:
    """Recorre los pasos del menú. Devuelve el `RunSpec` confirmado, o `None` si el usuario
    no confirma. Lanza `MenuCancelled` ante Ctrl+C / Ctrl+D. No escribe nada en disco.

    `random_seed_fn` genera la seed cuando el usuario pulsa Enter en el paso 5 (inyectable)."""
    if default_out is None:
        default_out = default_out_dir()
    s = _Session(input_fn, print_fn)
    p = s.print
    p("=" * 64)
    p(" Generador de datos SINTÉTICOS de prueba para OCR (marca 'MOCK DATA')")
    p("=" * 64)
    p("Enter acepta el valor entre corchetes. Ctrl+C o Ctrl+D cancelan.\n")

    # 1. documentos
    p("Paso 1/7. Documentos a generar")
    for i, (_, label) in enumerate(DOCUMENTS, start=1):
        p(f"  {i}) {label}")
    p("  Elija uno o varios: números separados por comas (1,3), rangos (1-3) o a para todos.")
    picked = s.ask("Documentos", lambda t: parse_selection(t, len(DOCUMENTS)))
    docs = [DOCUMENTS[i - 1][0] for i in picked]
    labels = dict(DOCUMENTS)
    p("  -> " + ", ".join(labels[d] for d in docs) + "\n")

    # 2. cantidad
    p("Paso 2/7. Cantidad de muestras")
    counts: dict[str, int] = {}
    same = True
    if len(docs) > 1:
        p("  1) La misma cantidad para todos los documentos")
        p("  2) Una cantidad distinta por documento")
        same = s.ask("Modo", lambda t: parse_option(
            t, [("misma", ""), ("distinta", "")]) == "misma", default="1")
    if same:
        n = s.ask(f"Muestras{' por documento' if len(docs) > 1 else ''} (1-{MAX_PER_DOC})",
                  parse_count, default=str(DEFAULT_COUNT))
        counts = {d: n for d in docs}
    else:
        for d in docs:
            counts[d] = s.ask(f"Muestras de {labels[d]} (1-{MAX_PER_DOC})", parse_count,
                              default=str(DEFAULT_COUNT))
    p("  " + format_estimate(sum(counts.values())) + "\n")

    # 3. variante INE
    variant: str | None = None
    if "ine_2016" in docs:
        p("Paso 3/7. Variante de la INE")
        for i, (_, label) in enumerate(VARIANTS, start=1):
            p(f"  {i}) {label}")
        variant = str(s.ask("Variante", lambda t: parse_option(t, VARIANTS), default="1"))
        p("")
    else:
        p("Paso 3/7. Variante de la INE: no aplica (no eligió la INE)\n")

    # 4. degradación
    p("Paso 4/7. Degradación (qué tan 'sucio' queda el documento)")
    for i, (_, label) in enumerate(DEGRADATIONS, start=1):
        p(f"  {i}) {label}")
    p(f"  {CUSTOM_DEGRADATION_OPTION}) ruta a un JSON con una configuración propia (DegradationConfig)")
    degradation = s.ask("Degradación (número, nombre o ruta)", parse_degradation,
                        default=DEFAULT_DEGRADATION_OPTION, hint=MIXED_DEGRADATION)
    if degradation == "":
        degradation = s.ask("Ruta al JSON", lambda t: parse_degradation(t, allow_custom=False))
    p("")

    # 5. seed
    p("Paso 5/7. Seed")
    p("  Enter = se genera una seed aleatoria distinta en cada ejecución; escriba un número "
      "para repetir exactamente un lote anterior.")
    chosen_seed = s.ask("Seed", parse_seed_or_random, default="", hint="aleatoria")
    seed = random_seed_fn() if chosen_seed is None else chosen_seed
    p(f"  Seed usada: {seed}" + ("  (aleatoria)" if chosen_seed is None else ""))
    p("")

    # 6. carpeta
    p("Paso 6/7. Carpeta de salida")
    p("  Dentro se crea una carpeta por tipo de documento con los PDFs y su JSON directamente, "
      "sin subcarpetas por muestra (más run.json).")
    if in_docker():
        p(f"  Está dentro de Docker: la carpeta del contenedor es {DOCKER_OUT} "
          "(en su equipo: ./data).")
    out = s.ask("Carpeta", parse_out_dir, default=default_out)
    p("  Por muestra se guardan el PDF degradado y su JSON de Ground Truth (mismo nombre base). "
      "Opcionalmente también el PDF limpio y el log de transformaciones, en <documento>/_extras/.")
    extras = s.ask("¿Guardar también el PDF limpio y el log de transformaciones?", parse_yes_no,
                   default="n", hint="s/N")
    p("")

    spec = RunSpec(counts=counts, seed=seed, variant=variant, degradation=degradation, out=out,
                   extras=extras)

    # carpetas que ya tienen muestras: nunca se sobrescribe en silencio
    existing = {d: existing_samples(doc_dir_for(out, d)) for d in docs if has_samples(doc_dir_for(out, d))}
    if existing:
        p("Ya hay muestras en la carpeta de salida:")
        for d, v in existing.items():
            p(f"  - {doc_dir_for(out, d)}: {len(v)} muestra(s)")
        p("  1) Sobrescribir (borra esas muestras y las regenera)")
        p("  2) Añadir con otro índice inicial (conserva las existentes)")
        p("  3) Cancelar")
        choice = s.ask("¿Qué hacer?", lambda t: parse_option(
            t, [("overwrite", ""), ("append", ""), ("cancel", "")]), default="3")
        if choice == "cancel":
            return None
        spec.on_exist = str(choice)
        if choice == "append":
            suggested = max(next_free_index(doc_dir_for(out, d)) for d in existing)
            spec.start_index = s.ask("Índice inicial", lambda t: parse_count(t, lo=0, hi=10**6 - 1),
                                     default=str(suggested))
        p("")

    # 7. resumen y confirmación
    p("Paso 7/7. Resumen")
    p("  Documentos:")
    for d in docs:
        p(f"    - {labels[d]} ({d}): {counts[d]} muestra(s) -> {doc_dir_for(out, d)}/")
    if variant is not None:
        p(f"  Variante INE: {variant}")
    p(f"  Degradación:  {degradation}")
    if degradation == MIXED_DEGRADATION:
        p("                (un preset por muestra: " +
          ", ".join(f"{n} {w} %" for n, w in MIXED_DEGRADATION_WEIGHTS) + ")")
    p(f"  Seed:         {seed}" + ("  (aleatoria; el comando de abajo la repite)" if chosen_seed is None else ""))
    p(f"  Salida:       {out}/  (+ run.json)")
    p("  Archivos por muestra: PDF + JSON (GT)" if not extras else
      "  Archivos por muestra: PDF + JSON (GT) + PDF limpio y log de transformaciones (_extras/)")
    if spec.on_exist == "overwrite":
        p("  Se BORRARÁN las muestras existentes de los documentos listados arriba.")
    elif spec.on_exist == "append":
        p(f"  Se añadirán muestras desde el índice {spec.start_index}.")
    p(f"  {format_estimate(sum(counts.values()))}")
    p("  Comando equivalente (repite esto sin menú):")
    p(f"    {spec.command()}")
    ok = s.ask("¿Generar?", parse_yes_no, default="s", hint="S/n")
    return spec if ok else None


def _make_progress(print_fn: PrintFn):
    def progress(pos: int, total: int, doc_type: str, done: int, n: int) -> None:
        step = max(1, n // 10)
        if done == n or done % step == 0:
            print_fn(f"[{pos}/{total}] {doc_type}: {done}/{n}")

    return progress


def run_menu(
    input_fn: InputFn = input,
    print_fn: PrintFn = print,
    default_out: str | None = None,
    random_seed_fn: RandomSeedFn = new_random_seed,
) -> int:
    """Menú completo + ejecución. Devuelve el código de salida (0 ok o no confirmado, 1 error,
    130 cancelado con Ctrl+C / Ctrl+D)."""
    started = False
    try:
        spec = collect_run_spec(input_fn, print_fn, default_out, random_seed_fn)
        if spec is None:
            print_fn("\nNo se generó nada.")
            return EXIT_OK
        print_fn("\nGenerando...")
        started = True
        outcome = execute_run(spec, progress=_make_progress(print_fn))
    except (MenuCancelled, KeyboardInterrupt, EOFError):
        print_fn("\nCancelado." + (" Pueden haber quedado muestras parciales." if started else
                                    " No se generó nada."))
        return EXIT_CANCELLED
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001
        print(f"error inesperado ({type(exc).__name__}): {exc}", file=sys.stderr)
        return EXIT_ERROR

    print_fn("")
    for line in format_summary(outcome):
        print_fn(line)
    print_fn(f"\nPara repetirlo exactamente (seed {spec.seed}) sin menú:")
    print_fn(f"  {spec.command()}")
    return EXIT_OK
