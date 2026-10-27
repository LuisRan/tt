"""Menú interactivo, estructura de carpetas por documento, `--on-exist` y run.json."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from src import cli, menu
from src.cli import RunSpec, build_parser, main, parse_counts
from src.menu import (
    collect_run_spec,
    format_estimate,
    parse_count,
    parse_degradation,
    parse_seed,
    parse_selection,
    parse_yes_no,
    run_menu,
)

ROOT = Path(__file__).resolve().parents[1]
ALL_DOCS = ["ine_2016", "curp_2019", "acta_nacimiento_2015", "pasaporte_electronico_2021"]


def scripted(answers: list[str]):
    """input_fn que devuelve las respuestas en orden y lanza EOFError al agotarlas (Ctrl+D)."""
    queue = list(answers)
    prompts: list[str] = []

    def input_fn(prompt: str = "") -> str:
        prompts.append(prompt)
        if not queue:
            raise EOFError
        item = queue.pop(0)
        if isinstance(item, BaseException) or (isinstance(item, type) and issubclass(item, BaseException)):
            raise item
        return item

    input_fn.prompts = prompts  # type: ignore[attr-defined]
    input_fn.remaining = queue  # type: ignore[attr-defined]
    return input_fn


class Out:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, *args, **kwargs):
        self.lines.append(" ".join(str(a) for a in args))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


# ------------------------------------------------------------------ parseo puro

@pytest.mark.parametrize("text,expected", [
    ("1,3", [1, 3]),
    ("2-4", [2, 3, 4]),
    ("a", [1, 2, 3, 4]),
    ("A", [1, 2, 3, 4]),
    ("todos", [1, 2, 3, 4]),
    ("4", [4]),
    (" 3 , 1 ", [1, 3]),
    ("1,3-4", [1, 3, 4]),
    ("3,1,3,1", [1, 3]),          # duplicados
    ("1-3,2", [1, 2, 3]),         # solapamiento
])
def test_parse_selection_valid(text, expected):
    assert parse_selection(text, 4) == expected


@pytest.mark.parametrize("text", [
    "", "   ", "x", "1,x", "1,,3", ",", "0", "5", "1-5", "0-2", "4-2", "1-", "-3", "1.5", "a,1x", "--", "1;3",
])
def test_parse_selection_invalid(text):
    with pytest.raises(ValueError):
        parse_selection(text, 4)


def test_parse_count_seed_yes_no_degradation():
    assert parse_count("5") == 5 and parse_count(" 2000 ") == menu.MAX_PER_DOC
    for bad in ("", "0", "-1", "abc", "2001", "1.5"):
        with pytest.raises(ValueError):
            parse_count(bad)
    assert parse_seed("0") == 0 and parse_seed("42") == 42
    for bad in ("", "-1", "x", str(2**32)):
        with pytest.raises(ValueError):
            parse_seed(bad)
    assert [parse_yes_no(t) for t in ("s", "S", "sí", "si", "y")] == [True] * 5
    assert [parse_yes_no(t) for t in ("n", "N", "no")] == [False] * 3
    with pytest.raises(ValueError):
        parse_yes_no("quizá")
    assert parse_degradation("4") == "default" and parse_degradation("heavy") == "heavy"
    assert parse_degradation("1") == "mixed" and parse_degradation("mixed") == "mixed"
    assert parse_degradation("7") == ""  # señal: pedir la ruta
    for bad in ("", "9", "no-existe", "/no/existe.json"):
        with pytest.raises(ValueError):
            parse_degradation(bad)


def test_estimate_text():
    text = format_estimate(30)
    assert "30 muestras" in text and "30 s" in text and "60 s" in text


# ------------------------------------------------------------------ recorrido del menú

def test_collect_full_walkthrough_spec(tmp_path):
    out_dir = str(tmp_path / "d")
    inp = scripted(["1,3", "2", "4", "7", "2", "2", "42", out_dir, "", "s"])
    spec = collect_run_spec(inp, Out())
    assert spec == RunSpec(counts={"ine_2016": 4, "acta_nacimiento_2015": 7}, seed=42,
                           variant="extranjero", degradation="none", out=out_dir, extras=False)
    assert not inp.remaining
    assert not (tmp_path / "d").exists()  # recolectar no escribe nada
    # con extras
    inp = scripted(["2", "1", "2", "1", out_dir, "s", "s"])
    out = Out()
    spec = collect_run_spec(inp, out)
    assert spec.extras is True and not inp.remaining
    assert "--extras" in spec.command() and "--extras" in out.text
    assert "PDF limpio y log de transformaciones (_extras/)" in out.text
    assert "¿Guardar también el PDF limpio y el log de transformaciones? [s/N]: " in inp.prompts


def test_invalid_answers_are_asked_again(tmp_path):
    out = Out()
    # CURP solo: pasos 3 no aplica; degradación: "zzz" inválida, luego "light"
    inp = scripted(["x", "9", "", "2", "0", "abc", "3", "zzz", "light", "-5", "x", "9", str(tmp_path), "", "s"])
    spec = collect_run_spec(inp, out)
    assert spec.counts == {"curp_2019": 3} and spec.degradation == "light" and spec.seed == 9
    assert spec.variant is None
    assert out.text.count("Entrada no válida") == 8  # 3 + 2 + 1 + 2


def test_defaults_with_enter(tmp_path):
    out = Out()
    inp = scripted(["a", "", "", "", "", "", "", "", ""])  # todo por defecto, incluida la confirmación
    spec = collect_run_spec(inp, out, default_out=str(tmp_path / "dd"), random_seed_fn=lambda: 1234567)
    assert spec.counts == {d: 10 for d in ALL_DOCS}
    assert (spec.variant, spec.degradation, spec.seed, spec.out) == (
        "nacional", "mixed", 1234567, str(tmp_path / "dd"))
    assert spec.extras is False and "--extras" not in spec.command() and "--extras" not in out.text
    assert "Archivos por muestra: PDF + JSON (GT)" in out.text and "_extras" not in out.text.split("Paso 7/7")[1]
    assert "sin subcarpetas por muestra" in out.text
    assert "Seed usada: 1234567" in out.text and "--seed 1234567" in out.text
    assert "Seed [aleatoria]: " in inp.prompts
    assert "Comando equivalente" in out.text
    assert spec.command() in out.text
    assert "tiempo estimado 40 s" in out.text  # 40 muestras x 1 s


def test_user_declines_confirmation(tmp_path):
    d = tmp_path / "d"
    rc = run_menu(scripted(["2", "1", "1", "1", str(d), "", "n"]), Out())
    assert rc == 0 and not d.exists()


def test_menu_generates_exact_folder_structure(tmp_path):
    d = tmp_path / "datos"
    out = Out()
    inp = scripted(["1,3", "2", "2", "1", "2", "2", "7", str(d), "", "s"])
    #  docs  modo  n_ine n_acta variante=extranjero degradación=none seed carpeta extras=no
    rc = run_menu(inp, out)
    assert rc == 0, out.text
    assert (d / "run.json").is_file()
    ine, acta = "ine_2016-extranjero-7-00000", "acta_nacimiento_2015-default-7-00000"
    assert tree(d) == sorted([
        "run.json",
        "ine_2016", "ine_2016/manifest.jsonl",
        f"ine_2016/{ine}0.json", f"ine_2016/{ine}0.pdf", f"ine_2016/{ine}1.json", f"ine_2016/{ine}1.pdf",
        "acta_nacimiento_2015", "acta_nacimiento_2015/manifest.jsonl",
        f"acta_nacimiento_2015/{acta}0.json", f"acta_nacimiento_2015/{acta}0.pdf",
    ])  # solo los documentos elegidos, sin subcarpetas por muestra ni _extras
    # progreso por documento y resumen con rutas absolutas por documento
    assert "[1/2] ine_2016: 2/2" in out.text and "[2/2] acta_nacimiento_2015: 1/1" in out.text
    assert str(d.resolve()) in out.text and "run.json" in out.text
    assert f"carpeta:  {(d / 'ine_2016').resolve()}" in out.text
    assert f"carpeta:  {(d / 'acta_nacimiento_2015').resolve()}" in out.text
    assert "PDFs=2" in out.text and "PDFs=1" in out.text
    run = json.loads((d / "run.json").read_text(encoding="utf-8"))
    assert run["doc_types"] == ["ine_2016", "acta_nacimiento_2015"]
    assert run["n"] == {"ine_2016": 2, "acta_nacimiento_2015": 1}
    assert run["seed"] == 7 and run["variant"] == "extranjero" and run["degradation"]["name"] == "none"
    assert run["extras"] is False
    assert run["synthetic"]["watermark_text"] == "MOCK DATA"
    assert set(run["versions"]["layouts"]) == {"ine_2016", "acta_nacimiento_2015"}
    # el comando equivalente reproduce el run
    argv = shlex.split(run["command"])[3:]
    assert argv[0] == "generate"
    args = build_parser().parse_args(argv)
    assert parse_counts(args.doc_type, args.n) == run["n"] and args.seed == 7 and args.extras is False


def test_menu_with_extras_exact_structure(tmp_path):
    d = tmp_path / "datos"
    out = Out()
    rc = run_menu(scripted(["2", "2", "2", "7", str(d), "s", "s"]), out)  # CURP, 2, none, seed 7, extras sí
    assert rc == 0, out.text
    sid = "curp_2019-default-7-00000"
    assert tree(d) == sorted([
        "run.json", "curp_2019", "curp_2019/manifest.jsonl",
        f"curp_2019/{sid}0.json", f"curp_2019/{sid}0.pdf", f"curp_2019/{sid}1.json", f"curp_2019/{sid}1.pdf",
        "curp_2019/_extras",
        f"curp_2019/_extras/{sid}0.clean.pdf", f"curp_2019/_extras/{sid}0.transform_log.json",
        f"curp_2019/_extras/{sid}1.clean.pdf", f"curp_2019/_extras/{sid}1.transform_log.json",
    ])
    run = json.loads((d / "run.json").read_text())
    assert run["extras"] is True and "--extras" in run["command"]
    assert "--extras" in out.text and "_extras/" in out.text


def test_menu_ine_both_faces_and_mixed(tmp_path):
    d = tmp_path / "m"
    rc = run_menu(scripted(["1", "5", "3", "2", "0", str(d), "", "s"]), Out())
    assert rc == 0
    variants = [json.loads(line)["variant"] for line in
                (d / "ine_2016" / "manifest.jsonl").read_text().splitlines()]
    assert variants == ["nacional"] * 4 + ["extranjero"]


# ------------------------------------------------------------------ cancelación y TTY

@pytest.mark.parametrize("answers", [
    [],                                      # Ctrl+D en el primer paso
    ["1", "2"],                              # Ctrl+D en mitad del recorrido
    ["1", "2", "1", "1", "0", "OUT", ""],    # Ctrl+D en la confirmación
    ["1", KeyboardInterrupt],                # Ctrl+C
    [KeyboardInterrupt],
])
def test_cancel_returns_130_and_writes_nothing(answers, tmp_path):
    d = tmp_path / "salida"
    answers = [str(d) if a == "OUT" else a for a in answers]
    out = Out()
    rc = run_menu(scripted(answers), out)
    assert rc == 130
    assert "Cancelado" in out.text and "Traceback" not in out.text
    assert not d.exists()


def test_cancel_subprocess_no_traceback(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "src", "menu", "--out", str(tmp_path / "x")],
                          cwd=ROOT, input="1\n", capture_output=True, text=True, timeout=60)
    assert proc.returncode == 130
    assert "Traceback" not in proc.stderr + proc.stdout
    assert not (tmp_path / "x").exists()


def test_no_tty_and_no_flags_prints_help_exit_2():
    proc = subprocess.run([sys.executable, "-m", "src"], cwd=ROOT, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2
    assert "usage" in proc.stdout.lower() and "--doc-type" in proc.stdout


def test_no_tty_inprocess_and_tty_launches_menu(monkeypatch, capsys):
    monkeypatch.delenv("OCR_DOCKER", raising=False)
    monkeypatch.setattr(cli, "stdin_is_tty", lambda: False)
    monkeypatch.setattr(menu, "run_menu", lambda **kw: pytest.fail("no debe abrir el menú sin TTY"))
    assert main([]) == 2
    assert "--doc-type" in capsys.readouterr().out
    calls = []
    monkeypatch.setattr(cli, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(menu, "run_menu", lambda **kw: calls.append(kw) or 0)
    assert main([]) == 0 and calls == [{"default_out": "data"}]
    assert main(["menu", "--out", "zz"]) == 0 and calls[-1] == {"default_out": "zz"}
    assert main(["generate", "-i"]) == 0 and calls[-1] == {"default_out": "data"}
    assert main(["--interactive"]) == 0 and len(calls) == 4


def test_generate_without_flags_still_usage_error(capsys):
    assert main(["generate"]) == 2
    assert "--doc-type" in capsys.readouterr().err


def test_menu_via_piped_stdin_subprocess(tmp_path):
    d = tmp_path / "pipe"
    script = f"2\n1\n2\n5\n{d}\n\ns\n"  # CURP, 1 muestra, none, seed 5, carpeta, extras (Enter=no), confirmar
    proc = subprocess.run([sys.executable, "-m", "src", "menu"], cwd=ROOT, input=script,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "[1/1] curp_2019: 1/1" in proc.stdout
    assert tree(d) == ["curp_2019", "curp_2019/curp_2019-default-5-000000.json",
                       "curp_2019/curp_2019-default-5-000000.pdf", "curp_2019/manifest.jsonl", "run.json"]


# ------------------------------------------------------------------ --on-exist y run.json

def _gen(tmp_path, *extra, doc="curp_2019", n="1", seed="3"):
    return main(["generate", "--doc-type", doc, "--n", n, "--seed", seed, "--degradation", "none",
                 "--out", str(tmp_path), *extra])


def test_on_exist_error_is_default_and_keeps_files(tmp_path, capsys):
    assert _gen(tmp_path) == 0
    doc = tmp_path / "curp_2019"
    sample = doc / "curp_2019-default-3-000000.json"
    (doc / "sentinel.txt").write_text("x")
    before = sample.read_bytes()
    assert _gen(tmp_path) == 1
    err = capsys.readouterr().err
    assert "ya contiene" in err and "--on-exist" in err
    assert (doc / "sentinel.txt").exists() and sample.read_bytes() == before
    # otro documento en la misma carpeta no es conflicto
    assert _gen(tmp_path, doc="acta_nacimiento_2015") == 0


def test_on_exist_overwrite(tmp_path):
    assert _gen(tmp_path, "--extras", n="2") == 0
    doc = tmp_path / "curp_2019"
    assert (doc / "_extras").is_dir()
    assert _gen(tmp_path, "--on-exist", "overwrite", n="1") == 0
    # queda una sola muestra y los extras de la tanda anterior se borraron (la carpeta, vacía, también)
    assert sorted(p.name for p in doc.iterdir()) == [
        "curp_2019-default-3-000000.json", "curp_2019-default-3-000000.pdf", "manifest.jsonl"]
    assert len((doc / "manifest.jsonl").read_text().splitlines()) == 1


def test_on_exist_overwrite_does_not_delete_foreign_files(tmp_path):
    assert _gen(tmp_path, "--extras", n="2") == 0
    doc = tmp_path / "curp_2019"
    foreign = [doc / "notas.txt", doc / "otro-000001.pdf", doc / "curp_2019.json", doc / "otra.json",
               doc / "_extras" / "mio.txt", doc / "carpeta_usuario" / "dato.json"]
    for f in foreign:
        f.parent.mkdir(exist_ok=True)
        f.write_text("mio")
    assert _gen(tmp_path, "--on-exist", "overwrite", n="1") == 0
    for f in foreign:  # nada ajeno se borra (ni siquiera _extras, que ya no está vacía)
        assert f.read_text() == "mio", f
    assert not (doc / "curp_2019-default-3-000001.pdf").exists()
    assert not (doc / "_extras" / "curp_2019-default-3-000001.clean.pdf").exists()
    assert (doc / "curp_2019-default-3-000000.pdf").is_file()


def _legacy_sample(doc: Path, index: int):
    """Muestra con la estructura ANTIGUA: subcarpeta por muestra con gt.json."""
    d = doc / f"curp_2019-default-3-{index:06d}"
    d.mkdir(parents=True)
    for name in ("clean.pdf", "degraded.pdf", "gt.json", "transform_log.json"):
        (d / name).write_text("viejo")
    return d


def test_legacy_structure_counts_as_existing_samples(tmp_path, capsys):
    from src.pipeline import existing_samples, has_samples, next_free_index
    doc = tmp_path / "curp_2019"
    old = [_legacy_sample(doc, 0), _legacy_sample(doc, 1)]
    assert existing_samples(doc) == [d.name for d in old] and has_samples(doc)
    assert next_free_index(doc) == 2
    # --on-exist error (default): detecta la estructura antigua y no escribe nada
    assert _gen(tmp_path) == 1
    assert "ya contiene" in capsys.readouterr().err
    assert sorted(p.name for p in doc.iterdir()) == [d.name for d in old]
    # append con índice ocupado por la estructura antigua: error
    assert _gen(tmp_path, "--on-exist", "append", "--start-index", "1") == 1
    assert "ya existen" in capsys.readouterr().err
    # overwrite: borra las subcarpetas antiguas con gt.json, pero no otras subcarpetas
    (doc / "otra_carpeta").mkdir()
    (doc / "otra_carpeta" / "x.txt").write_text("mio")
    assert _gen(tmp_path, "--on-exist", "overwrite") == 0
    assert sorted(p.name for p in doc.iterdir()) == [
        "curp_2019-default-3-000000.json", "curp_2019-default-3-000000.pdf", "manifest.jsonl", "otra_carpeta"]
    assert (doc / "otra_carpeta" / "x.txt").read_text() == "mio"


def test_manifest_only_folder_counts_as_existing(tmp_path):
    doc = tmp_path / "curp_2019"
    doc.mkdir()
    (doc / "manifest.jsonl").write_text("")
    assert _gen(tmp_path) == 1


def test_on_exist_skip(tmp_path, capsys):
    assert _gen(tmp_path) == 0
    doc = tmp_path / "curp_2019"
    (doc / "sentinel.txt").write_text("x")
    before = (doc / "curp_2019-default-3-000000.json").read_bytes()
    capsys.readouterr()
    assert _gen(tmp_path, "--on-exist", "skip", doc="curp_2019,acta_nacimiento_2015") == 0
    assert "omitido" in capsys.readouterr().out
    assert (doc / "sentinel.txt").exists() and (doc / "curp_2019-default-3-000000.json").read_bytes() == before
    assert (tmp_path / "acta_nacimiento_2015" / "manifest.jsonl").is_file()
    # todo omitido: no se escribe run.json nuevo
    run_before = (tmp_path / "run.json").read_bytes()
    assert _gen(tmp_path, "--on-exist", "skip") == 0
    assert (tmp_path / "run.json").read_bytes() == run_before


def test_on_exist_append_with_start_index(tmp_path, capsys):
    assert _gen(tmp_path, n="2") == 0
    assert _gen(tmp_path, "--on-exist", "append", "--start-index", "2", n="2") == 0
    doc = tmp_path / "curp_2019"
    lines = [json.loads(line) for line in (doc / "manifest.jsonl").read_text().splitlines()]
    assert [m["index"] for m in lines] == [0, 1, 2, 3]
    for m in lines:
        assert (doc / m["gt"]).is_file() and (doc / m["degraded_pdf"]).is_file()
    assert len(list(doc.glob("curp_2019-*.pdf"))) == len(list(doc.glob("curp_2019-*.json"))) == 4
    # índices repetidos: error y no se toca nada
    assert _gen(tmp_path, "--on-exist", "append", "--start-index", "3", n="2") == 1
    assert "ya existen" in capsys.readouterr().err
    assert len((doc / "manifest.jsonl").read_text().splitlines()) == 4
    assert len(list(doc.glob("curp_2019-*.pdf"))) == 4


def test_menu_existing_folder_append_and_overwrite_and_cancel(tmp_path):
    assert _gen(tmp_path, n="2") == 0
    doc = tmp_path / "curp_2019"
    base = ["2", "1", "2", "3", str(tmp_path), ""]  # CURP, 1 muestra, none, seed 3, carpeta, extras no
    # cancelar (Enter = cancelar, la opción segura)
    out = Out()
    assert run_menu(scripted(base + [""]), out) == 0
    assert "Ya hay muestras" in out.text and len(list(doc.glob("curp_2019-*.json"))) == 2
    # añadir: el índice sugerido (Enter) es el siguiente libre
    out = Out()
    assert run_menu(scripted(base + ["2", "", "s"]), out) == 0, out.text
    assert sorted(p.name for p in doc.glob("*.pdf"))[-1] == "curp_2019-default-3-000002.pdf"
    assert "--on-exist append --start-index 2" in out.text
    # sobrescribir: queda una sola muestra
    out = Out()
    assert run_menu(scripted(base + ["1", "s"]), out) == 0
    assert sorted(p.name for p in doc.glob("*.pdf")) == ["curp_2019-default-3-000000.pdf"]
    assert "BORRARÁN" in out.text


def test_run_json_is_deterministic_and_clockless(tmp_path):
    a = tmp_path / "a"
    args = dict(doc="ine_2016,curp_2019", n="ine_2016=2,curp_2019=1")
    assert _gen(a, "--on-exist", "overwrite", **args) == 0
    first = (a / "run.json").read_bytes()
    assert _gen(a, "--on-exist", "overwrite", **args) == 0
    assert (a / "run.json").read_bytes() == first          # mismos parámetros => mismos bytes
    run = json.loads(first)
    assert not re.search(r"\d{4}-\d{2}-\d{2}|T\d{2}:\d{2}", first.decode("utf-8"))  # sin fechas/hora
    assert not {"created_at", "timestamp", "date", "hostname", "commit"} & set(run)
    # mismo contenido que generar con la API (sin pasar por argparse)
    spec = RunSpec(counts={"ine_2016": 2, "curp_2019": 1}, seed=3, degradation="none", out=str(a),
                   on_exist="overwrite")
    assert cli.execute_run(spec).run_json.read_bytes() == first
    assert run["extras"] is False
    # otra seed => otro run.json
    assert _gen(a, "--on-exist", "overwrite", seed="4", **args) == 0
    assert (a / "run.json").read_bytes() != first
    # --extras => run.json distinto, y también determinista
    assert _gen(a, "--on-exist", "overwrite", "--extras", **args) == 0
    with_extras = (a / "run.json").read_bytes()
    assert with_extras != first and json.loads(with_extras)["extras"] is True
    assert _gen(a, "--on-exist", "overwrite", "--extras", **args) == 0
    assert (a / "run.json").read_bytes() == with_extras


@pytest.mark.parametrize("extras", [False, True])
def test_manifest_and_gt_paths_are_relative_to_document_folder(extras, tmp_path):
    assert _gen(tmp_path, *(["--extras"] if extras else []), doc="ine_2016", n="2", seed="1") == 0
    doc = tmp_path / "ine_2016"
    for line in (doc / "manifest.jsonl").read_text().splitlines():
        m = json.loads(line)
        sid = m["sample_id"]
        assert m["degraded_pdf"] == f"{sid}.pdf" and m["gt"] == f"{sid}.json"
        assert (doc / m["degraded_pdf"]).is_file() and (doc / m["gt"]).is_file()
        gt = json.loads((doc / m["gt"]).read_text())  # el GT usa las mismas rutas (relativas al documento)
        assert gt["degraded_pdf"] == m["degraded_pdf"]
        for key, rel in (("clean_pdf", f"_extras/{sid}.clean.pdf"),
                         ("transform_log", f"_extras/{sid}.transform_log.json")):
            assert m[key] == gt[key] == (rel if extras else None)
            if extras:
                assert (doc / rel).is_file()


def test_gt_pairs_with_pdf_and_bbox_px_reconstruct_positions(tmp_path):
    """Ejemplo del README: emparejar X.pdf con X.json y leer bbox_px de la imagen final."""
    import pypdfium2 as pdfium
    assert _gen(tmp_path, doc="curp_2019", n="2", seed="1") == 0
    doc = tmp_path / "curp_2019"
    for pdf in sorted(doc.glob("*.pdf")):
        gt = json.loads(pdf.with_suffix(".json").read_text())
        assert gt["degraded_pdf"] == pdf.name
        pages = pdfium.PdfDocument(pdf)
        assert len(pages) == len(gt["pages"])
        for page, gpage in zip(pages, gt["pages"]):
            w_mm, h_mm = gpage["width_mm"], gpage["height_mm"]
            for f in gpage["fields"]:
                t = f["transformed"]
                assert t["bbox_px"]["x1"] > t["bbox_px"]["x0"]
                assert 0 <= t["bbox_px"]["x0"] and t["bbox_px"]["x1"] <= t["image_width_px"] + 2
                assert 0 <= t["bbox_px"]["y0"] and t["bbox_px"]["y1"] <= t["image_height_px"] + 2
            assert w_mm > 0 and h_mm > 0 and page.get_width() > 0


def test_cli_n_per_document_and_errors(tmp_path, capsys):
    assert _gen(tmp_path, doc="curp_2019,acta_nacimiento_2015", n="curp_2019=2,acta_nacimiento_2015=1") == 0
    assert len(list((tmp_path / "curp_2019").glob("curp_2019-*.json"))) == 2
    assert len(list((tmp_path / "acta_nacimiento_2015").glob("acta_nacimiento_2015-*.pdf"))) == 1
    other = tmp_path / "o"
    for bad in ("acta_nacimiento_2015=2", "pasaporte_electronico_2021=1,curp_2019=1", "curp_2019=0", "x"):
        assert _gen(other, doc="curp_2019", n=bad) == 2
    assert not other.exists()
    with pytest.raises(SystemExit):
        _gen(other, doc="foo")


def test_default_out_is_data_and_gitignored():
    assert cli.DEFAULT_OUT == "data"
    assert build_parser().parse_args([]).out == "data"
    assert "data/" in (ROOT / ".gitignore").read_text().splitlines()


# ------------------------------------------------------------------ seed aleatoria y degradación mixed

def test_random_seed_default_is_31_bits_and_varies():
    from src.pipeline import new_random_seed
    seeds = {new_random_seed() for _ in range(20)}
    assert len(seeds) > 1 and all(0 <= s < 2**31 for s in seeds)


def test_menu_enter_on_seed_uses_injected_random_and_shows_it(tmp_path):
    out = Out()
    # curp, 1 muestra, degradación Enter (mixed), seed Enter, carpeta, confirmar
    inp = scripted(["2", "1", "", "", str(tmp_path / "o"), "", "s"])
    spec = collect_run_spec(inp, out, random_seed_fn=lambda: 987654321)
    assert spec.seed == 987654321 and spec.degradation == "mixed"
    assert "Seed usada: 987654321" in out.text
    assert out.text.index("Seed usada") < out.text.index("Resumen")
    assert "--seed 987654321" in spec.command() and "--seed 987654321" in out.text
    assert "Seed [aleatoria]: " in inp.prompts
    # un número explícito se respeta y no llama a la función aleatoria
    inp = scripted(["2", "1", "", "42", str(tmp_path / "o"), "", "s"])
    spec = collect_run_spec(inp, Out(), random_seed_fn=lambda: pytest.fail("no debe llamarse"))
    assert spec.seed == 42
    # seed inválida: se vuelve a preguntar
    out = Out()
    inp = scripted(["2", "1", "", "-1", "x", "", str(tmp_path / "o"), "", "s"])
    assert collect_run_spec(inp, out, random_seed_fn=lambda: 5).seed == 5
    assert out.text.count("Entrada no válida") == 2


def test_two_consecutive_menu_runs_with_enter_differ_and_repeat_reproduces(tmp_path):
    """Efecto práctico: Enter en la seed => dos ejecuciones distintas; la seed impresa repite la 1ª."""
    answers = lambda d: ["2", "1", "2", "", str(d), "", "s"]  # CURP, 1, none, seed Enter  # noqa: E731
    outs = []
    for name in ("a", "b"):
        out = Out()
        assert run_menu(scripted(answers(tmp_path / name)), out) == 0, out.text  # función de seed REAL
        outs.append(out)
    runs = [json.loads((tmp_path / n / "run.json").read_text()) for n in ("a", "b")]
    assert runs[0]["seed"] != runs[1]["seed"]
    gts = [next((tmp_path / n / "curp_2019").glob("curp_2019-*.json")) for n in ("a", "b")]
    gt_a, gt_b = (json.loads(g.read_text()) for g in gts)
    assert gt_a["seed"] != gt_b["seed"] and gt_a["pages"] != gt_b["pages"]  # datos distintos
    # repetir con la seed impresa reproduce el primer lote exactamente
    argv = shlex.split(runs[0]["command"])[3:]
    assert f"--seed {runs[0]['seed']}" in outs[0].text
    argv[argv.index("--out") + 1] = str(tmp_path / "c")
    assert main(argv) == 0
    for ext in (".json", ".pdf"):  # mismos bytes del GT y del PDF degradado
        f = gts[0].with_suffix(ext)
        assert (tmp_path / "c" / "curp_2019" / f.name).read_bytes() == f.read_bytes()


def test_cli_without_seed_is_random_and_prints_it(tmp_path, capsys):
    seeds = iter([111, 222])
    for name in ("a", "b"):
        rc = main(["generate", "--doc-type", "curp_2019", "--n", "1", "--degradation", "none",
                   "--out", str(tmp_path / name)], random_seed_fn=lambda: next(seeds))
        assert rc == 0
    out = capsys.readouterr().out
    assert "seed=111" in out and "seed=222" in out and "--seed 222" in out
    assert [json.loads((tmp_path / n / "run.json").read_text())["seed"] for n in ("a", "b")] == [111, 222]
    assert build_parser().parse_args(["generate", "--doc-type", "curp_2019"]).seed is None
    assert build_parser().parse_args(["generate", "--doc-type", "curp_2019"]).degradation == "mixed"
    # con --seed el comportamiento es el de siempre (la función aleatoria no se usa)
    assert main(["generate", "--doc-type", "curp_2019", "--n", "1", "--seed", "9", "--degradation", "none",
                 "--out", str(tmp_path / "c")], random_seed_fn=lambda: pytest.fail("no")) == 0


def test_menu_accepts_mixed_by_number_name_and_enter(tmp_path):
    assert parse_degradation("1") == parse_degradation("mixed") == "mixed"
    assert menu.DEGRADATIONS[0][0] == "mixed"
    base = lambda deg: scripted(["2", "1", deg, "5", str(tmp_path / "o"), "", "s"])  # noqa: E731
    for deg in ("1", "mixed", "MIXED", ""):
        assert collect_run_spec(base(deg), Out()).degradation == "mixed", deg
    text, inp = Out(), base("")
    collect_run_spec(inp, text)
    assert "1) mixed: mezcla recomendada" in text.text and "45 % de ellos más sucios" in text.text
    assert "7) ruta a un JSON" in text.text
    assert "Degradación (número, nombre o ruta) [mixed]: " in inp.prompts


def test_menu_mixed_small_batch_end_to_end(tmp_path):
    d = tmp_path / "m"
    out = Out()
    rc = run_menu(scripted(["2", "6", "", "3", str(d), "", "s"]), out)  # CURP, 6, mixed, seed 3
    assert rc == 0, out.text
    run = json.loads((d / "run.json").read_text())
    assert run["degradation"]["name"] == "mixed" and run["seed"] == 3
    manifest = [json.loads(line) for line in (d / "curp_2019" / "manifest.jsonl").read_text().splitlines()]
    assert len(manifest) == 6 and all("degradation_preset" in m for m in manifest)
    assert "presets de degradación del lote:" in out.text


def test_out_dir_default_and_docker(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # que un ./data real del usuario no altere las preguntas del menú
    monkeypatch.delenv("OCR_DOCKER", raising=False)
    assert cli.default_out_dir() == "data"
    out = Out()
    spec = collect_run_spec(scripted(["2", "1", "", "1", "", "", "s"]), out)  # carpeta: Enter
    assert spec.out == "data" and "Docker" not in out.text
    monkeypatch.setenv("OCR_DOCKER", "1")
    assert cli.default_out_dir() == "/app/data"
    out = Out()
    spec = collect_run_spec(scripted(["2", "1", "", "1", "", "", "s"]), out)
    assert spec.out == "/app/data"
    assert "en su equipo: ./data" in out.text and "--out /app/data" in out.text
    # una carpeta escrita a mano manda; ~ se expande y las comillas se quitan
    assert menu.parse_out_dir("~/x") == str(Path("~/x").expanduser())
    assert menu.parse_out_dir(f"'{tmp_path}'") == str(tmp_path)
    seen = []
    monkeypatch.setattr(menu, "run_menu", lambda **kw: seen.append(kw) or 0)
    monkeypatch.setattr(cli, "stdin_is_tty", lambda: True)
    assert main([]) == 0 and seen == [{"default_out": "/app/data"}]
    assert main(["menu", "--out", "zz"]) == 0 and seen[-1] == {"default_out": "zz"}
