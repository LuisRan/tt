import json

import pytest
from .core_fixtures import GT_VALUES, make_error_pred, make_gt, make_pred, make_ref, perfect_datos

from contracts.common import DocType
from src.evaluation.evaluate import aggregate, compare_reports, evaluate_sample
from src.evaluation.mapping import OCR_FIELDS, curp_birthdate, curp_sex, expected_fields
from src.evaluation.reports import write_reports
from src.evaluation.results import FieldStatus as S

OCR_NAME = {DocType.INE_2016: "ine", DocType.ACTA_NACIMIENTO_2015: "acta_nacimiento",
            DocType.PASAPORTE_ELECTRONICO_2021: "pasaporte", DocType.CURP_2019: "curp"}


def run(dt, datos=None, preset="none", split="dev", tipo=None, sid=None, **kw):
    gt = make_gt(dt, GT_VALUES[dt], sample_id=sid)
    d = perfect_datos(dt) if datos is None else datos
    pred = make_pred(gt.sample_id, d, tipo=tipo or OCR_NAME[dt], **kw)
    return evaluate_sample(gt, pred, make_ref(gt, preset=preset, split=split))


def by(res):
    return {f.name: f for f in res.fields}


@pytest.mark.parametrize("dt", list(GT_VALUES))
def test_salida_perfecta_sin_errores(dt):
    r = run(dt)
    assert r.type_correct is True and not r.failed
    bad = [(f.name, f.status) for f in r.fields
           if f.status not in (S.EXACT, S.NOT_APPLICABLE)]
    assert bad == []
    assert {f.name for f in r.fields} == set(OCR_FIELDS[OCR_NAME[dt]])


def test_mapa_not_applicable():
    ef = expected_fields(make_gt(DocType.CURP_2019, GT_VALUES[DocType.CURP_2019]))
    for k in ("nombres", "primer_apellido", "segundo_apellido", "codigo_verificacion"):
        assert not ef[k].applicable and ef[k].reason
    assert ef["fecha_nacimiento"].value == "03/11/1959" and ef["sexo"].value == "M"
    pas = expected_fields(make_gt(DocType.PASAPORTE_ELECTRONICO_2021, GT_VALUES[DocType.PASAPORTE_ELECTRONICO_2021]))
    assert pas["primer_apellido"].value == "ANGUIANO" and pas["mrz_linea2"].value.startswith("N0102")
    ine = expected_fields(make_gt(DocType.INE_2016, GT_VALUES[DocType.INE_2016]))
    assert ine["nombres"].value == "PAMELA" and ine["nombre_completo"].alternatives == ["BARAJAS MESA PAMELA"]
    assert ine["año_emision"].value == "2019"


def test_pasaporte_apellidos_ambiguos():
    vals = dict(GT_VALUES[DocType.PASAPORTE_ELECTRONICO_2021], apellidos="DE LA CRUZ")
    ef = expected_fields(make_gt(DocType.PASAPORTE_ELECTRONICO_2021, vals))
    assert not ef["primer_apellido"].applicable
    vals["apellidos"] = "SOLO"
    assert expected_fields(make_gt(DocType.PASAPORTE_ELECTRONICO_2021, vals))["primer_apellido"].value == "SOLO"


def test_acta_padres_por_curp_y_ambiguo():
    vals = dict(GT_VALUES[DocType.ACTA_NACIMIENTO_2015])
    # intercambiados: p1 es la madre
    vals.update(p1_curp="AEJE200626MDFRSL88", p2_curp="OULA100703HSLQDD32",
                p1_nombres="MARIA ELENA", p2_nombres="ADAN")
    ef = expected_fields(make_gt(DocType.ACTA_NACIMIENTO_2015, vals))
    assert ef["curp_padre"].value == "OULA100703HSLQDD32" and ef["curp_madre"].value == "AEJE200626MDFRSL88"
    vals["p1_curp"] = "AEJE200626HDFRSL88"  # ambos H
    ef = expected_fields(make_gt(DocType.ACTA_NACIMIENTO_2015, vals))
    assert all(not ef[k].applicable for k in ("nombre_padre", "curp_padre", "nombre_madre", "curp_madre"))


def test_curp_derivados():
    assert curp_birthdate("CORA591103MSLRLN64") == "03/11/1959"
    assert curp_birthdate("CORA010229MSLRLNA4") is None  # 2001 no bisiesto
    assert curp_birthdate("CORA050101MSLRLNA4") == "01/01/2005"
    assert curp_birthdate("basura") is None
    assert curp_sex("CORA591103MSLRLN64") == "M" and curp_sex(None) is None and curp_sex("x") is None
    vals = dict(GT_VALUES[DocType.CURP_2019], curp="XXXX")
    ef = expected_fields(make_gt(DocType.CURP_2019, vals))
    assert not ef["fecha_nacimiento"].applicable and not ef["sexo"].applicable


def test_gt_sin_nombre_de_3_lineas_ine():
    vals = dict(GT_VALUES[DocType.INE_2016], nombre=["A", "B"])
    ef = expected_fields(make_gt(DocType.INE_2016, vals))
    assert not ef["nombres"].applicable and not ef["nombre_completo"].applicable


def test_estados_por_campo():
    d = perfect_datos(DocType.INE_2016)
    d.update(curp="bamp460510mtlrsm07",           # NORMALIZED
             nombre_completo="barajas mesa pamela",  # alterno => PARTIAL
             nombres="PAMELÁ",                    # solo acento => PARTIAL
             fecha_nacimiento="1946-05-10",      # NORMALIZED
             sexo="MUJER",                        # NORMALIZED
             clave_elector=None,                  # MISSING
             seccion="761",                       # MISMATCH
             localidad="0O36",                   # MISMATCH
             segundo_apellido="  ")              # MISSING
    d["campo_raro"] = "algo"                      # EXTRA no puntuado
    f = by(run(DocType.INE_2016, d))
    assert f["curp"].status == S.NORMALIZED and f["curp"].cer > 0
    assert f["nombre_completo"].status == S.PARTIAL
    assert f["nombres"].status == S.PARTIAL
    assert f["fecha_nacimiento"].status == S.NORMALIZED
    assert f["sexo"].status == S.NORMALIZED
    assert f["clave_elector"].status == S.MISSING and f["clave_elector"].cer == 1.0
    assert f["seccion"].status == S.MISMATCH
    assert f["localidad"].status == S.MISMATCH
    assert f["segundo_apellido"].status == S.MISSING
    assert f["campo_raro"].status == S.EXTRA and f["campo_raro"].scored is False
    assert f["domicilio"].wer == 0.0


def test_fecha_ilegible_y_wer():
    d = perfect_datos(DocType.INE_2016)
    d["fecha_nacimiento"] = "borroso"
    d["domicilio"] = "DIAGONAL ALMARAZ 924 COL. VIEJA"
    f = by(run(DocType.INE_2016, d))
    assert f["fecha_nacimiento"].status == S.UNINTERPRETABLE
    assert f["domicilio"].status == S.MISMATCH and 0 < f["domicilio"].wer < 1


def test_not_applicable_no_puntua_aunque_ocr_responda():
    d = perfect_datos(DocType.CURP_2019)
    d["nombres"] = "ANEL"
    f = by(run(DocType.CURP_2019, d))
    assert f["nombres"].status == S.NOT_APPLICABLE and f["nombres"].scored is False
    assert f["nombres"].cer is None


def test_esperado_vacio():
    vals = dict(GT_VALUES[DocType.INE_2016], nombre=["BARAJAS", "", "PAMELA"])
    gt = make_gt(DocType.INE_2016, vals)
    d = perfect_datos(DocType.INE_2016)
    d["segundo_apellido"] = None
    r = evaluate_sample(gt, make_pred(gt.sample_id, d), make_ref(gt))
    assert by(r)["segundo_apellido"].status == S.EXACT
    d["segundo_apellido"] = "MESA"
    r = evaluate_sample(gt, make_pred(gt.sample_id, d), make_ref(gt))
    assert by(r)["segundo_apellido"].status == S.EXTRA and by(r)["segundo_apellido"].scored


def test_error_y_validaciones():
    gt = make_gt(DocType.INE_2016, GT_VALUES[DocType.INE_2016])
    r = evaluate_sample(gt, make_error_pred(gt.sample_id), make_ref(gt))
    assert r.failed and r.error_code == "TIMEOUT" and r.fields == [] and r.type_correct is None
    with pytest.raises(ValueError):
        evaluate_sample(gt, make_pred("otro", {}), make_ref(gt))
    p = make_pred(gt.sample_id, {}, tipo=None, total_ms=None)
    r = evaluate_sample(gt, p, make_ref(gt))
    assert r.type_correct is False and r.total_ms is None
    assert all(f.status in (S.MISSING, S.NOT_APPLICABLE) for f in r.fields)
    p = make_error_pred(gt.sample_id)
    p.error = None
    assert evaluate_sample(gt, p, make_ref(gt)).error_code == "ERROR_DESCONOCIDO"


def _campaign():
    rs = []
    for i, preset in enumerate(["none", "none", "heavy", "heavy"]):
        d = perfect_datos(DocType.INE_2016)
        if preset == "heavy":
            d["curp"] = "XXXX"
        if i == 3:
            d["clave_elector"] = None
        rs.append(run(DocType.INE_2016, d, preset=preset, sid=f"ine-{i}",
                      split="test" if i == 3 else "dev", total_ms=100.0 + i,
                      model_load_ms=50.0 if i == 0 else None, stages={"ocr": 10.0 + i}))
    rs.append(run(DocType.CURP_2019, sid="curp-0", tipo="ine"))  # mal clasificado
    gt = make_gt(DocType.PASAPORTE_ELECTRONICO_2021, GT_VALUES[DocType.PASAPORTE_ELECTRONICO_2021], "pas-0")
    rs.append(evaluate_sample(gt, make_error_pred("pas-0", "PDF_INVALIDO"), make_ref(gt, preset="heavy")))
    return rs


def test_aggregate():
    rep = aggregate(_campaign(), {"run_id": "r1", "generated_at": "2026-01-01T00:00:00Z"})
    assert rep.n_samples == 6 and rep.n_failed == 1
    assert rep.generated_at == "2026-01-01T00:00:00Z"
    assert [g.key for g in rep.by_type] == ["curp_2019", "ine_2016", "pasaporte_electronico_2021"]
    assert {g.key for g in rep.by_preset} == {"none", "heavy"}
    assert {g.key for g in rep.by_split} == {"dev", "test"}
    fa = {(f.field, f.doc_type): f for f in rep.fields_by_type}
    curp = fa[("curp", "ine_2016")]
    assert curp.n_scored == 4 and curp.counts["MISMATCH"] == 2 and curp.accuracy == 0.5 and curp.critical
    assert sum(curp.counts.values()) == 4
    assert rep.classification.n == 5 and rep.classification.cross_category_errors == 1
    assert rep.failed_documents[0].error_code == "PDF_INVALIDO"
    perf = rep.performance
    assert perf.error_rate == pytest.approx(1 / 6, abs=1e-6) and perf.n_timed == 5
    assert perf.latency_p95_ms is None and perf.latency_p50_ms is not None
    assert perf.model_load_ms == 50.0 and perf.throughput_basis == "suma_latencias"
    assert perf.latency_mean_ms_excl_model_load < perf.latency_mean_ms
    assert perf.stages_mean_ms["ocr"] == pytest.approx(11.5)
    # errores y recomendaciones solo derivados de los datos
    assert rep.top_errors[0].field == "curp" and rep.top_errors[0].count == 2
    recs = rep.recommendations
    assert [r.priority for r in recs] == list(range(1, len(recs) + 1))
    assert recs[0].target == "curp@ine_2016" and recs[0].impact == 6.0  # 2 errores x crit 3
    assert "degradación" in recs[0].message
    assert [r.impact for r in recs] == sorted((r.impact for r in recs), reverse=True)
    scopes = {r.scope for r in recs}
    assert {"campo", "fallos", "clasificacion"} <= scopes


def test_aggregate_wall_clock_y_vacio():
    rep = aggregate(_campaign(), {"wall_clock_s": 2.0})
    assert rep.performance.throughput_basis == "wall_clock_s"
    assert rep.performance.docs_per_s == pytest.approx(5 / 2.0)
    empty = aggregate([], {})
    assert empty.n_samples == 0 and empty.classification is None and empty.recommendations == []
    assert empty.performance.error_rate is None and empty.overall.accuracy is None


def test_aggregate_determinista_y_orden():
    a = aggregate(_campaign(), {"run_id": "r"})
    b = aggregate(list(reversed(_campaign())), {"run_id": "r"})
    assert a.model_dump_json() == b.model_dump_json()


def _rep(mods, extra=None):
    rs = []
    for i in range(3):
        for dt in (DocType.INE_2016, DocType.CURP_2019):
            d = perfect_datos(dt)
            d.update(mods.get((dt, i), {}))
            rs.append(run(dt, d, sid=f"{dt.value}-{i}"))
    return aggregate(rs + (extra or []), {"run_id": "x"})


def test_compare_regresion_aunque_suba_global():
    base = _rep({(DocType.INE_2016, 0): {"nombres": "XX", "sexo": "H", "seccion": "1", "estado": "9",
                                         "municipio": "1", "localidad": "1"}})
    cand = _rep({(DocType.CURP_2019, 0): {"curp": "MAL"}})
    c = compare_reports(base, cand)
    assert c.comparable and c.global_improved
    assert c.regression_despite_global_gain
    names = {d.name for d in c.regressions}
    assert "curp@curp_2019" in names and "curp_2019.accuracy" in names
    assert any(d.name == "ine_2016.accuracy" for d in c.improvements)
    assert c.baseline_run == "x"
    same = compare_reports(base, base)
    assert not same.regressions and not same.improvements and not same.regression_despite_global_gain


def test_compare_tolerancia_y_no_comparable_y_sin_dato():
    base, cand = _rep({}), _rep({(DocType.CURP_2019, 0): {"folio": "1"}})
    assert compare_reports(base, cand).regressions
    assert not compare_reports(base, cand, quality_tolerance=0.5).regressions
    other = aggregate([run(DocType.INE_2016, sid="otro")], {})
    c = compare_reports(base, other)
    assert not c.comparable and c.notes
    assert any(d.verdict == "sin_dato" for d in c.deltas)
    # rendimiento: más lento => regresión con tolerancia 0, no con tolerancia grande
    slow = aggregate([run(DocType.INE_2016, sid="s", total_ms=100.0)], {"wall_clock_s": 1})
    slower = aggregate([run(DocType.INE_2016, sid="s", total_ms=900.0)], {"wall_clock_s": 9})
    d = {x.name: x for x in compare_reports(slow, slower).deltas}
    assert d["latency_mean_ms"].verdict == "regresion" and d["docs_per_s"].verdict == "regresion"
    d2 = {x.name: x for x in compare_reports(slow, slower, perf_tolerance=10_000).deltas}
    assert d2["latency_mean_ms"].verdict == "igual"


def test_write_reports(tmp_path):
    rep = aggregate(_campaign(), {"run_id": "r1", "generated_at": "T"})
    paths = write_reports(rep, tmp_path / "out")
    assert set(paths) == {"report.json", "fields.csv", "samples.csv", "confusion.csv",
                          "performance.json", "report.md"}
    assert all(p.exists() for p in paths.values())
    assert json.loads(paths["report.json"].read_text())["n_samples"] == 6
    assert json.loads(paths["performance.json"].read_text())["n_samples"] == 6
    md = paths["report.md"].read_text()
    for needle in ("Recomendaciones priorizadas", "Documentos fallidos", "NOT_APPLICABLE", "Rendimiento"):
        assert needle in md
    assert paths["confusion.csv"].read_text().startswith("verdad\\prediccion,")
    assert len(paths["samples.csv"].read_text().splitlines()) == 7
    snap = {k: p.read_bytes() for k, p in paths.items()}
    paths2 = write_reports(aggregate(list(reversed(_campaign())), {"run_id": "r1", "generated_at": "T"}),
                           tmp_path / "out2")
    assert snap == {k: p.read_bytes() for k, p in paths2.items()}


def test_write_reports_vacio(tmp_path):
    paths = write_reports(aggregate([], {}), tmp_path)
    assert "Muestras: 0" in paths["report.md"].read_text()
    assert paths["confusion.csv"].read_text().strip() == "verdad\\prediccion"
