"""Pruebas de integración del pipeline y del CLI (datos sintéticos de PRUEBA)."""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest

from contracts import DocType, GroundTruth, TransformLog, Variant
from src import layouts
from src.degradation import effective_dpi, load_preset, rasterize_pdf
from src.pipeline import (
    PipelineError,
    SampleResult,
    derive_degradation_seed,
    generate_batch,
    generate_sample,
    mixed_variant,
    resolve_degradation,
)

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = "_extras"

# El contrato ya incluye `codigo_2d` en el Acta; layouts/datos lo añaden en L2/D2.


def _case(doc_type: DocType, variant: str | None, *, marks=()):
    cid = doc_type.value if variant is None else f"{doc_type.value}-{variant}"
    return pytest.param(doc_type.value, variant, id=cid, marks=marks)


CASES = [
    _case(DocType.INE_2016, "nacional"),
    _case(DocType.INE_2016, "extranjero"),
    _case(DocType.CURP_2019, None),
    _case(DocType.ACTA_NACIMIENTO_2015, None),
    _case(DocType.PASAPORTE_ELECTRONICO_2021, None),
]
ALL_DOC_TYPES = [
    pytest.param(d.value, id=d.value)
    for d in DocType
]


def _pdf_pages(path: Path) -> int:
    doc = pdfium.PdfDocument(path.read_bytes())
    try:
        return len(doc)
    finally:
        doc.close()


def _expected_pages(doc_type: str) -> int:
    return len(layouts.load_spec(doc_type).faces)


# ------------------------------------------------------------------ extremo a extremo

def _flat_files(doc_dir: Path) -> list[str]:
    return sorted(p.name for p in doc_dir.iterdir())


@pytest.mark.parametrize("doc_type,variant", CASES)
def test_end_to_end(doc_type, variant, tmp_path):
    results = generate_batch(doc_type, 2, 11, variant=variant, out_dir=tmp_path)
    assert len(results) == 2
    n_pages = _expected_pages(doc_type)
    doc = tmp_path / doc_type
    for i, r in enumerate(results):
        assert isinstance(r, SampleResult)
        assert r.index == i and r.seed == 11 and r.doc_type == doc_type
        assert r.degradation_seed == derive_degradation_seed(11, i)
        assert r.doc_dir == doc  # <out>/<doc_type>, sin carpeta por muestra
        pdf, js = doc / f"{r.sample_id}.pdf", doc / f"{r.sample_id}.json"
        assert (r.degraded_pdf, r.gt_path) == (pdf.name, js.name)
        for f in (pdf, js):
            assert f.is_file() and f.stat().st_size > 0, f
        assert not (doc / r.sample_id).exists()
        assert r.clean_pdf is None and r.transform_log is None
        # el GT escrito valida contra el contrato
        gt = GroundTruth.model_validate_json(js.read_text(encoding="utf-8"))
        assert gt.sample_id == r.sample_id
        assert gt.doc_type.value == doc_type
        assert gt.seed == 11 and gt.degradation_seed == r.degradation_seed
        assert gt.synthetic.is_synthetic is True and gt.synthetic.watermark_text == "MOCK DATA"
        # sin extras: degraded_pdf apunta al PDF hermano; clean_pdf/transform_log son null
        assert gt.degraded_pdf == pdf.name and gt.clean_pdf is None and gt.transform_log is None
        if variant:
            assert gt.variant.value == variant
        # una página por cara en el PDF degradado
        assert len(gt.pages) == n_pages
        assert _pdf_pages(pdf) == n_pages
        # cada campo con bbox original (mm) y transformada (px)
        for page in gt.pages:
            assert page.fields
            for f in page.fields:
                assert f.bbox_mm is not None, f.name
                t = f.transformed
                assert t is not None, f.name
                assert t.bbox_px.x1 > t.bbox_px.x0 and t.bbox_px.y1 > t.bbox_px.y0
                assert t.image_width_px > 0 and t.image_height_px > 0
    # manifest: en la carpeta del documento, con rutas relativas a ESA carpeta
    assert not (tmp_path / "manifest.jsonl").exists()
    lines = (doc / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line, r in zip(lines, results):
        m = json.loads(line)
        assert m["sample_id"] == r.sample_id and m["doc_type"] == doc_type
        assert m["seed"] == 11 and m["degradation_seed"] == r.degradation_seed
        assert m["degraded_pdf"] == f"{r.sample_id}.pdf" and m["gt"] == f"{r.sample_id}.json"
        assert m["clean_pdf"] is None and m["transform_log"] is None
        for key in ("degraded_pdf", "gt"):
            assert (doc / m[key]).is_file()


@pytest.mark.parametrize("extras", [False, True])
def test_only_flat_files_no_per_sample_folders(extras, tmp_path):
    res = generate_batch("curp_2019", 3, 4, degradation="none", out_dir=tmp_path, extras=extras)
    doc = tmp_path / "curp_2019"
    expected = {"manifest.jsonl"}
    for r in res:
        expected |= {f"{r.sample_id}.pdf", f"{r.sample_id}.json"}
    if extras:
        expected.add(EXTRAS)
    assert set(_flat_files(doc)) == expected
    assert [p.name for p in doc.iterdir() if p.is_dir()] == ([EXTRAS] if extras else [])
    if extras:  # _extras es PLANA y solo contiene los dos archivos por muestra
        want = {f"{r.sample_id}{suf}" for r in res for suf in (".clean.pdf", ".transform_log.json")}
        assert set(_flat_files(doc / EXTRAS)) == want
        assert not any(p.is_dir() for p in (doc / EXTRAS).iterdir())


def test_extras_written_referenced_and_watermarked(tmp_path):
    res = generate_batch("ine_2016", 2, 8, degradation="light", out_dir=tmp_path, extras=True)
    doc = tmp_path / "ine_2016"
    for r in res:
        gt = GroundTruth.model_validate_json((doc / f"{r.sample_id}.json").read_text(encoding="utf-8"))
        assert gt.clean_pdf == f"{EXTRAS}/{r.sample_id}.clean.pdf"
        assert gt.transform_log == f"{EXTRAS}/{r.sample_id}.transform_log.json"
        assert gt.degraded_pdf == f"{r.sample_id}.pdf"
        for rel in (gt.clean_pdf, gt.transform_log, gt.degraded_pdf):  # relativas a la carpeta del documento
            assert (doc / rel).is_file()
        clean = (doc / gt.clean_pdf).read_bytes()
        verify_watermark(clean, expected_pages=_expected_pages("ine_2016"))
        assert all("MOCKDATA" in "".join(t.split()).upper() for t in pdf_page_texts(clean))
        log = TransformLog.model_validate_json((doc / gt.transform_log).read_text(encoding="utf-8"))
        assert log.degradation_seed == r.degradation_seed and log.sample_id == r.sample_id
        assert len(log.pages) == len(gt.pages) == _pdf_pages(doc / gt.clean_pdf)
    for line, r in zip((doc / "manifest.jsonl").read_text().splitlines(), res):
        m = json.loads(line)
        assert m["clean_pdf"] == f"{EXTRAS}/{r.sample_id}.clean.pdf"
        assert m["transform_log"] == f"{EXTRAS}/{r.sample_id}.transform_log.json"
        assert (doc / m["clean_pdf"]).is_file() and (doc / m["transform_log"]).is_file()


def test_gt_content_is_the_same_with_and_without_extras(tmp_path):
    """`extras` solo cambia las referencias del GT, no la geometría ni los datos."""
    a = generate_sample("curp_2019", 5, 0, out_dir=tmp_path / "a")
    b = generate_sample("curp_2019", 5, 0, out_dir=tmp_path / "b", extras=True)
    ga = json.loads(a.path(a.gt_path).read_text())
    gb = json.loads(b.path(b.gt_path).read_text())
    for key in ("clean_pdf", "transform_log"):
        assert ga.pop(key) is None and gb.pop(key)
    assert ga == gb
    assert a.path(a.degraded_pdf).read_bytes() == b.path(b.degraded_pdf).read_bytes()


def test_json_is_deterministic_text(tmp_path):
    r = generate_sample("curp_2019", 3, 0, out_dir=tmp_path)
    text = r.path(r.gt_path).read_text(encoding="utf-8")
    assert text.endswith("\n") and "\n  " in text  # indentado
    assert json.loads(text)["synthetic"]["is_synthetic"] is True


# ------------------------------------------------------------------ reproducibilidad

def _digests(r: SampleResult) -> dict[str, bytes]:
    out = {"pdf": r.path(r.degraded_pdf).read_bytes(), "json": r.path(r.gt_path).read_bytes()}
    if r.clean_pdf:
        out["clean"] = r.path(r.clean_pdf).read_bytes()
        out["log"] = r.path(r.transform_log).read_bytes()
    return out


@pytest.mark.parametrize("extras", [False, True])
@pytest.mark.parametrize("doc_type,variant", CASES)
def test_reproducible_and_seed_sensitive(doc_type, variant, extras, tmp_path):
    kw = dict(variant=variant, extras=extras)
    a = generate_sample(doc_type, 5, 0, out_dir=tmp_path / "a", **kw)
    b = generate_sample(doc_type, 5, 0, out_dir=tmp_path / "b", **kw)
    assert a.sample_id == b.sample_id
    da, db = _digests(a), _digests(b)
    assert set(da) == ({"pdf", "json", "clean", "log"} if extras else {"pdf", "json"})
    for name in da:
        assert da[name] == db[name], f"{name} difiere entre dos ejecuciones con la misma seed"
    c = generate_sample(doc_type, 6, 0, out_dir=tmp_path / "c", **kw)
    dc = _digests(c)
    assert dc["json"] != da["json"]
    assert dc["pdf"] != da["pdf"]
    # otro índice con la misma seed también produce otra muestra
    d = generate_sample(doc_type, 5, 1, out_dir=tmp_path / "d", **kw)
    assert _digests(d)["json"] != da["json"]


def test_degradation_seed_is_derived_deterministically():
    assert derive_degradation_seed(1, 0) == derive_degradation_seed(1, 0)
    assert len({derive_degradation_seed(s, i) for s in range(5) for i in range(5)}) == 25


# ------------------------------------------------------------------ variantes y lotes

def test_mixed_variant_ratio_and_determinism():
    picks = [mixed_variant(DocType.INE_2016, i) for i in range(100)]
    assert picks == [mixed_variant(DocType.INE_2016, i) for i in range(100)]
    assert picks.count(Variant.EXTRANJERO) == 20 and picks.count(Variant.NACIONAL) == 80
    assert all(mixed_variant(DocType.CURP_2019, i) is None for i in range(10))


def test_batch_mixed_ine_and_ignored_for_others(tmp_path):
    none = load_preset("none")
    res = generate_batch("ine_2016", 5, 2, variant="mixed", degradation=none, out_dir=tmp_path / "ine")
    assert [r.variant for r in res] == ["nacional"] * 4 + ["extranjero"]
    res = generate_batch("curp_2019", 2, 2, variant="mixed", degradation=none, out_dir=tmp_path / "curp")
    assert {r.variant for r in res} == {"default"}
    res = generate_batch("curp_2019", 1, 2, variant="extranjero", degradation=none, out_dir=tmp_path / "c2")
    assert res[0].variant == "default"


def test_invalid_variant_for_ine_and_bad_degradation(tmp_path):
    with pytest.raises(Exception):
        generate_sample("ine_2016", 1, 0, variant="default", out_dir=tmp_path)
    with pytest.raises(PipelineError):
        resolve_degradation("no-existe")
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["none", "light", "default", "heavy", "scanner"])
def test_presets_resolve(name):
    assert resolve_degradation(name).render_dpi > 0


def test_scanner_preset_places_page_on_canvas():
    cfg = resolve_degradation("scanner")
    cp = [s for s in cfg.steps if s.kind.value == "canvas_placement"]
    assert cp and cp[0].probability == 1.0


def test_degradation_from_json_path(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(load_preset("light").model_dump_json(), encoding="utf-8")
    r = generate_sample("curp_2019", 1, 0, degradation=str(cfg_path), out_dir=tmp_path / "o")
    assert r.log.config_name == "light"


def test_watermark_cannot_be_disabled_from_the_api():
    assert "watermark" not in inspect.signature(generate_sample).parameters
    assert "watermark" not in inspect.signature(generate_batch).parameters
    for rel in ("src/pipeline.py", "src/cli.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "watermark=False" not in text.replace(" ", "")


# ------------------------------------------------------------------ CLI

def _run_cli(*args: str, cwd: Path = ROOT):
    return subprocess.run([sys.executable, "-m", "src", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=300)


@pytest.mark.parametrize("doc_type", ALL_DOC_TYPES)
def test_cli_generate_n1(doc_type, tmp_path):
    proc = _run_cli("generate", "--doc-type", doc_type, "--n", "1", "--seed", "4",
                    "--degradation", "light", "--out", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "Resumen" in proc.stdout and doc_type in proc.stdout
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["run.json", doc_type])
    doc = tmp_path / doc_type
    assert not any(p.is_dir() for p in doc.iterdir())  # sin subcarpetas por muestra
    assert sorted(p.suffix for p in doc.iterdir()) == [".json", ".jsonl", ".pdf"]
    sid = json.loads((doc / "manifest.jsonl").read_text().splitlines()[0])["sample_id"]
    assert (doc / f"{sid}.pdf").is_file() and (doc / f"{sid}.json").is_file()
    assert str(doc.resolve()) in proc.stdout  # ruta absoluta de la carpeta del documento
    assert json.loads((tmp_path / "run.json").read_text())["extras"] is False


def test_cli_extras_flag(tmp_path):
    proc = _run_cli("generate", "--doc-type", "curp_2019", "--n", "2", "--seed", "4",
                    "--degradation", "none", "--extras", "--out", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    doc = tmp_path / "curp_2019"
    assert sorted(p.name for p in doc.iterdir() if p.is_dir()) == ["_extras"]
    assert len(list((doc / "_extras").glob("*.clean.pdf"))) == 2
    assert len(list((doc / "_extras").glob("*.transform_log.json"))) == 2
    assert len(list(doc.glob("*.pdf"))) == 2 and len(list(doc.glob("*.json"))) == 2
    assert "--extras" in proc.stdout and "_extras/" in proc.stdout
    assert json.loads((tmp_path / "run.json").read_text())["extras"] is True


def test_cli_without_subcommand_and_variant_mixed(tmp_path):
    proc = _run_cli("--doc-type", "ine_2016", "--n", "5", "--seed", "1", "--variant", "mixed",
                    "--degradation", "none", "--out", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    variants = [json.loads(line)["variant"] for line in
                (tmp_path / "ine_2016" / "manifest.jsonl").read_text().splitlines()]
    assert variants.count("extranjero") == 1 and variants.count("nacional") == 4


def test_cli_all(tmp_path):
    proc = _run_cli("generate", "--doc-type", "all", "--n", "1", "--seed", "2",
                    "--degradation", "none", "--out", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    docs = [json.loads(line)["doc_type"]
            for d in DocType for line in (tmp_path / d.value / "manifest.jsonl").read_text().splitlines()]
    assert sorted(docs) == sorted(d.value for d in DocType)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["run.json", *(d.value for d in DocType)])


@pytest.mark.parametrize("args", [
    ["generate", "--doc-type", "ine_2016", "--degradation", "inexistente"],
    ["generate", "--doc-type", "ine_2016", "--degradation", "/no/existe.json"],
    ["generate", "--doc-type", "pasaporte_viejo"],
    ["generate"],
    ["generate", "--doc-type", "ine_2016", "--n", "0"],
])
def test_cli_errors_have_nonzero_exit(args, tmp_path):
    proc = _run_cli(*args, "--out", str(tmp_path))
    assert proc.returncode != 0
    assert proc.stderr.strip()
    assert not any(p.is_dir() for p in tmp_path.iterdir())


# ------------------------------------------------------------------ precisión de las bboxes

def _relative_darkness(g: np.ndarray, x0, y0, x1, y1):
    """(fondo local - media de la región) / fondo local; None si cae fuera de la imagen."""
    H, W = g.shape
    x0, y0, x1, y1 = (int(round(v)) for v in (x0, y0, x1, y1))
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H or x1 - x0 < 4 or y1 - y0 < 4:
        return None
    h, w = y1 - y0, x1 - x0
    window = g[max(0, y0 - h):y1 + h, max(0, x0 - w // 2):x1 + w // 2]
    bg = float(np.median(window))
    return (bg - float(g[y0:y1, x0:x1].mean())) / max(bg, 1.0)


TEXTUAL = {"text", "multiline", "mrz"}


@pytest.mark.parametrize("doc_type, variant", CASES)
def test_transformed_bboxes_contain_value_ink(doc_type, variant):
    """Prueba diferencial exacta: se renderiza el documento con valores y con los
    campos de texto vacíos, se degradan ambos con la MISMA seed (pasos geométricos
    de 'heavy') y la diferencia (= solo la tinta de los valores) debe caer dentro
    de las bboxes transformadas del GT."""
    from src.data_injection import inject
    from src.degradation import degrade_pdf_with_images, project_ground_truth

    dt = DocType(doc_type)
    heavy = load_preset("heavy")
    geometric = {"render", "dpi_resample", "canvas_placement", "rotation", "skew"}
    cfg = heavy.model_copy(update={"steps": [st for st in heavy.steps if st.kind.value in geometric]})
    spec = layouts.load_spec(dt)
    for seed in (1, 2, 3):
        record, gt = inject(spec, seed, 0, variant=Variant(variant) if variant else None)
        blank = {face: {k: ("" if not isinstance(v, list) else [])
                        if spec.face(face).field(k).type.value in TEXTUAL else v
                        for k, v in vals.items()}
                 for face, vals in record.items()}
        faces = [(pg.face, (pg.width_mm, pg.height_mm)) for pg in gt.pages]
        kw = dict(sample_id=gt.sample_id, degradation_seed=seed, faces=faces)
        _, log, full = degrade_pdf_with_images(layouts.render_document(spec, record, variant=gt.variant), cfg, **kw)
        _, log2, empty = degrade_pdf_with_images(layouts.render_document(spec, blank, variant=gt.variant), cfg, **kw)
        assert log == log2
        gt = project_ground_truth(gt, log)
        for page, a, b in zip(gt.pages, full, empty):
            diff = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > 40
            mask = np.zeros_like(diff)
            h, w = diff.shape
            for f in page.fields:
                if f.type.value in TEXTUAL:
                    bb = f.transformed.bbox_px
                    mask[max(0, int(bb.y0) - 3):min(h, int(np.ceil(bb.y1)) + 3),
                         max(0, int(bb.x0) - 3):min(w, int(np.ceil(bb.x1)) + 3)] = True
            ink = int(diff.sum())
            assert ink > 500, f"{gt.sample_id} p{page.page_index}: casi no hay tinta de valores ({ink})"
            inside = int((diff & mask).sum()) / ink
            assert inside >= 0.98, f"{gt.sample_id} p{page.page_index}: solo {inside:.1%} de la tinta cae en las bboxes"


# ------------------------------------------------------------------ degradación "mixed"

import os  # noqa: E402
from collections import Counter  # noqa: E402

from src.pipeline import (  # noqa: E402
    MIXED_DEGRADATION_WEIGHTS,
    mixed_degradation_preset,
    pdf_page_texts,
    verify_watermark,
)


def test_mixed_weights_are_documented_constant():
    assert dict(MIXED_DEGRADATION_WEIGHTS) == {"none": 10, "light": 45, "default": 25,
                                              "scanner": 12, "heavy": 8}
    assert sum(w for _, w in MIXED_DEGRADATION_WEIGHTS) == 100
    for name, _ in MIXED_DEGRADATION_WEIGHTS:
        assert resolve_degradation(name).name == name  # config_name del log == preset


def test_mixed_preset_same_seed_index_same_preset_across_processes():
    docs = ["ine_2016", "curp_2019", "acta_nacimiento_2015", "pasaporte_electronico_2021"]
    pairs = [(s, d, i) for s in (0, 1, 42, 2**31 - 1) for d in docs for i in range(0, 20)]
    local = [mixed_degradation_preset(s, d, i) for s, d, i in pairs]
    assert local == [mixed_degradation_preset(s, d, i) for s, d, i in pairs]
    code = ("import json; from src.pipeline import mixed_degradation_preset as m; "
            f"print(json.dumps([m(s, d, i) for s, d, i in {pairs!r}]))")
    for hashseed in ("0", "1", "random"):
        proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                              timeout=120, env={**os.environ, "PYTHONHASHSEED": hashseed})
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == local, f"PYTHONHASHSEED={hashseed}"
    assert len(set(local)) == 5  # y no es constante
    assert [mixed_degradation_preset(1, "curp_2019", i) for i in range(40)] != [
        mixed_degradation_preset(2, "curp_2019", i) for i in range(40)]
    assert [mixed_degradation_preset(1, "curp_2019", i) for i in range(40)] != [
        mixed_degradation_preset(1, "ine_2016", i) for i in range(40)]


def test_mixed_distribution_close_to_weights():
    n = 2000
    for seed in (0, 7):
        counts = Counter(mixed_degradation_preset(seed, "curp_2019", i) for i in range(n))
        for name, weight in MIXED_DEGRADATION_WEIGHTS:
            assert abs(counts[name] / n * 100 - weight) <= 4, (seed, name, counts)
        assert (counts["none"] + counts["light"]) / n > 0.5  # la mayoría son "normales"


def test_mixed_batch_small_valid_files_manifest_and_watermark(tmp_path):
    seed = next(s for s in range(100)
                if len({mixed_degradation_preset(s, "curp_2019", i) for i in range(6)}) >= 3)
    res = generate_batch("curp_2019", 6, seed, degradation="mixed", out_dir=tmp_path)
    assert len(res) == 6
    doc = tmp_path / "curp_2019"
    manifest = [json.loads(line) for line in (doc / "manifest.jsonl").read_text().splitlines()]
    assert [m["index"] for m in manifest] == list(range(6))
    for m, r in zip(manifest, res):
        expected = mixed_degradation_preset(seed, "curp_2019", m["index"])
        assert m["degradation_preset"] == expected == r.degradation_preset
        assert not (doc / m["sample_id"]).exists()
        assert r.log.config_name == m["degradation_preset"]  # el log refleja el preset concreto
        GroundTruth.model_validate_json((doc / m["gt"]).read_text())
        assert _pdf_pages(doc / m["degraded_pdf"]) == _expected_pages("curp_2019")
    assert sorted(p.name for p in doc.iterdir()) == sorted(
        ["manifest.jsonl", *(f"{m['sample_id']}{e}" for m in manifest for e in (".pdf", ".json"))])
    assert len({m["degradation_preset"] for m in manifest}) >= 3
    # un preset fijo también registra su nombre
    one = generate_sample("curp_2019", 1, 0, degradation="light", out_dir=tmp_path / "o")
    assert one.manifest_entry()["degradation_preset"] == "light"


def test_cli_mixed_default_summary_and_run_json(tmp_path):
    proc = _run_cli("generate", "--doc-type", "curp_2019", "--n", "6", "--seed", "3",
                    "--out", str(tmp_path))  # sin --degradation: default mixed
    assert proc.returncode == 0, proc.stderr
    run = json.loads((tmp_path / "run.json").read_text())
    assert run["degradation"]["name"] == "mixed" and run["degradation"]["source"] == "mixed"
    assert run["degradation"]["weights"] == dict(MIXED_DEGRADATION_WEIGHTS)
    assert run["seed"] == 3
    presets = Counter(mixed_degradation_preset(3, "curp_2019", i) for i in range(6))
    for name, c in presets.items():
        assert f"{name}={c}" in proc.stdout
    assert "--seed 3" in proc.stdout and "Para repetirlo" in proc.stdout


def test_mixed_varies_across_documents_for_the_same_index(tmp_path):
    docs = [d.value for d in DocType]
    seed = 5
    by_index = [{mixed_degradation_preset(seed, d, i) for d in docs} for i in range(8)]
    assert any(len(group) >= 2 for group in by_index)  # el mismo índice, presets distintos entre documentos
    assert len({tuple(mixed_degradation_preset(seed, d, i) for i in range(8)) for d in docs}) > 1
    assert len({mixed_degradation_preset(seed, d, i) for d in docs for i in range(8)}) >= 3  # 32 muestras
    # y el lote real (--doc-type all, n=8) lo registra así en los manifests
    from src.cli import RunSpec, execute_run
    out = tmp_path / "all"
    res = execute_run(RunSpec(counts={d: 8 for d in docs}, seed=seed, degradation="mixed", out=str(out))).results
    assert len(res) == 32
    assert {r.degradation_preset for r in res} == {mixed_degradation_preset(seed, r.doc_type, r.index) for r in res}
    assert len({r.degradation_preset for r in res}) >= 3
    for d in docs:
        entries = [json.loads(line) for line in (out / d / "manifest.jsonl").read_text().splitlines()]
        assert [e["degradation_preset"] for e in entries] == [mixed_degradation_preset(seed, d, i) for i in range(8)]
