import pytest
from .core_fixtures import GT_VALUES, make_gt, make_pred, make_ref, perfect_datos

from contracts.common import DocType
from src.evaluation.evaluate import aggregate, evaluate_sample
from src.evaluation.mapping import OCR_FIELDS, OCR_SCHEMA_FIELDS
from src.evaluation.normalize import FieldKind as K, compare_values
from src.evaluation.reports import render_markdown
from src.evaluation.results import FieldStatus as S


def _run(dt, ocr, **extra):
    gt = make_gt(dt, GT_VALUES[dt])
    d = perfect_datos(dt)
    d.update(extra)
    return evaluate_sample(gt, make_pred(gt.sample_id, d, tipo=ocr), make_ref(gt))


def test_schema_incluye_campos_mapeados():
    for t, fields in OCR_FIELDS.items():
        assert set(fields) <= OCR_SCHEMA_FIELDS[t]


def test_extra_schema_no_es_error_y_extra_si():
    r = _run(DocType.INE_2016, "ine", domicilio_calle="C X 1", año_registro="2008 02", alucinado="zzz")
    f = {x.name: x for x in r.fields}
    assert f["domicilio_calle"].status == S.EXTRA_SCHEMA and not f["domicilio_calle"].scored
    assert f["año_registro"].status == S.EXTRA_SCHEMA
    assert f["alucinado"].status == S.EXTRA
    rep = aggregate([r], {})
    assert rep.overall.n_extra_schema == 2
    assert rep.overall.accuracy == 1.0
    assert not rep.recommendations
    fa = {(x.field, x.doc_type): x for x in rep.fields_by_type}
    assert fa[("domicilio_calle", "ine_2016")].n_extra_schema == 1 and fa[("domicilio_calle", "ine_2016")].n_errors == 0
    assert all(e.status != "EXTRA_SCHEMA" for e in rep.top_errors)
    assert "EXTRA_SCHEMA" in render_markdown(rep)


def test_domicilio_ignora_puntuacion():
    assert compare_values(K.ADDRESS, "AV X 504, COL. X", "AV X 504 COL. X") == S.NORMALIZED
    assert compare_values(K.ADDRESS, "504, COL. X", "504  COL X;") == S.NORMALIZED
    assert compare_values(K.ADDRESS, "504 COL. X", "540 COL. X") == S.MISMATCH
    assert compare_values(K.ADDRESS, "504 COL. X", "504 COL. Y") == S.MISMATCH
    # el resto de TEXT no se relaja
    assert compare_values(K.TEXT, "504, COL. X", "504 COL. X") == S.MISMATCH
    assert compare_values(K.IDENTIFIER, "AB.12", "AB12") == S.MISMATCH


def test_domicilio_en_evaluacion():
    r = _run(DocType.INE_2016, "ine",
             domicilio="DIAGONAL ALMARAZ 924 COL VIEJA REPUBLICA CHECA 10876 VIEJA GAMBIA TLAX")
    assert {x.name: x for x in r.fields}["domicilio"].status == S.NORMALIZED


@pytest.mark.parametrize("gt_v,ocr_v", [("HOMBRE", "H"), ("MUJER", "M"), ("HOMBRE", "HOMBRE"), ("MUJER", "MUJER")])
def test_sexo_acta_h_m(gt_v, ocr_v):
    assert compare_values(K.SEX_MX, gt_v, ocr_v) in (S.NORMALIZED, S.EXACT)
    vals = dict(GT_VALUES[DocType.ACTA_NACIMIENTO_2015], reg_sexo=gt_v)
    gt = make_gt(DocType.ACTA_NACIMIENTO_2015, vals)
    d = perfect_datos(DocType.ACTA_NACIMIENTO_2015)
    d["sexo"] = ocr_v
    r = evaluate_sample(gt, make_pred(gt.sample_id, d, tipo="acta_nacimiento"), make_ref(gt))
    assert {x.name: x for x in r.fields}["sexo"].status in (S.NORMALIZED, S.EXACT)
    assert compare_values(K.SEX_MX, gt_v, "M" if ocr_v in ("H", "HOMBRE") else "H") == S.MISMATCH
