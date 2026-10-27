import pytest

from src.evaluation.metrics import (
    cer, classification_metrics, levenshtein, min_samples_for, percentile, percentiles, throughput, wer,
)
from src.evaluation.normalize import (
    FieldKind as K, canonical_sex, compare_values, normalize_code, normalize_identifier, parse_date,
    strip_accents,
)
from src.evaluation.results import FieldStatus as S


# ---- normalización: nunca incorrecto -> correcto

def test_identifier_solo_mayusculas_y_espacios():
    assert compare_values(K.IDENTIFIER, "BAMP460510MTLRSM07", "BAMP460510MTLRSM07") == S.EXACT
    assert compare_values(K.IDENTIFIER, "BAMP460510MTLRSM07", "bamp460510mtlrsm07") == S.NORMALIZED
    assert compare_values(K.IDENTIFIER, "BAMP460510MTLRSM07", " BAMP 460510 MTLRSM07 ") == S.NORMALIZED


@pytest.mark.parametrize("bad", [
    "BAMP46051OMTLRSM07",   # 0 -> O
    "BAMP460510MTLRSM7",    # dígito perdido
    "BAMP460510MTLRSM007",  # dígito extra
    "BAMP46O510MTLRSM07",
])
def test_identifier_no_corrige_errores(bad):
    assert compare_values(K.IDENTIFIER, "BAMP460510MTLRSM07", bad) == S.MISMATCH


def test_identifier_ceros_iniciales_y_o_cero():
    assert compare_values(K.IDENTIFIER, "00123", "123") == S.MISMATCH
    assert compare_values(K.IDENTIFIER, "N01028118", "NO1028118") == S.MISMATCH
    assert normalize_identifier("a b\tc") == "ABC"


def test_code_ceros_iniciales_cuentan():
    assert compare_values(K.CODE, "080", "80") == S.MISMATCH
    assert compare_values(K.CODE, "0036", "36") == S.MISMATCH
    assert compare_values(K.CODE, "080", " 080 ") == S.NORMALIZED
    assert compare_values(K.CODE, "P", "p") == S.NORMALIZED
    assert normalize_code(" ab ") == "AB"
    # los espacios internos NO se eliminan en códigos
    assert compare_values(K.CODE, "2009 01", "200901") == S.MISMATCH


def test_text_niveles():
    assert compare_values(K.TEXT, "NUEVA BURUNDI", "NUEVA BURUNDI") == S.EXACT
    assert compare_values(K.TEXT, "NUEVA BURUNDI", "nueva   burundi ") == S.NORMALIZED
    # acentos: solo parcial, no correcto
    assert compare_values(K.TEXT, "MÉXICO", "MEXICO") == S.PARTIAL
    assert compare_values(K.TEXT, "MEXICO", "MÉXICO") == S.PARTIAL
    assert compare_values(K.TEXT, "NUÑEZ", "NUNEZ") == S.PARTIAL
    assert compare_values(K.TEXT, "MÉXICO", "MEXIKO") == S.MISMATCH


def test_text_nfc_vs_nfd_es_normalizado():
    nfd = "MÉXICO"
    assert compare_values(K.TEXT, "MÉXICO", nfd) == S.NORMALIZED
    assert strip_accents(nfd) == "MEXICO"


def test_text_orden_alterno_es_parcial():
    assert compare_values(K.TEXT, "ANA LOPEZ", "LOPEZ ANA", alternatives=["LOPEZ ANA"]) == S.PARTIAL
    assert compare_values(K.TEXT, "ANA LOPEZ", "LOPEZ ANA") == S.MISMATCH
    assert compare_values(K.TEXT, "ANA LOPEZ", "lópez ana", alternatives=["LOPEZ ANA"]) == S.PARTIAL


def test_date_formatos_y_errores():
    assert compare_values(K.DATE, "27 10 1991", "27 10 1991") == S.EXACT
    assert compare_values(K.DATE, "27 10 1991", "27/10/1991") == S.NORMALIZED
    assert compare_values(K.DATE, "10/05/1946", "1946-05-10") == S.NORMALIZED
    assert compare_values(K.DATE, "10/05/1946", "05/10/1946") == S.MISMATCH  # día<->mes no se corrige
    assert compare_values(K.DATE, "10/05/1946", "10/05/1947") == S.MISMATCH
    assert compare_values(K.DATE, "10/05/1946", "31/02/1946") == S.UNINTERPRETABLE
    assert compare_values(K.DATE, "10/05/1946", "1O/05/1946") == S.UNINTERPRETABLE
    assert compare_values(K.DATE, "10/05/1946", "10/05/46") == S.UNINTERPRETABLE
    assert parse_date("5/3/2020").day == 5
    assert parse_date("2020-13-01") is None


def test_sex_mapa_explicito():
    assert compare_values(K.SEX_MX, "H", "HOMBRE") == S.NORMALIZED
    assert compare_values(K.SEX_MX, "HOMBRE", "H") == S.NORMALIZED
    assert compare_values(K.SEX_MX, "M", "MUJER") == S.NORMALIZED
    assert compare_values(K.SEX_MX, "M", "H") == S.MISMATCH
    assert compare_values(K.SEX_MX, "M", "?") == S.UNINTERPRETABLE
    # pasaporte: M = masculino
    assert compare_values(K.SEX_ICAO, "M", "HOMBRE") == S.NORMALIZED
    assert compare_values(K.SEX_ICAO, "M", "MUJER") == S.MISMATCH
    assert compare_values(K.SEX_ICAO, "F", "MUJER") == S.NORMALIZED
    assert canonical_sex("m", K.SEX_MX) == "MUJER"
    assert canonical_sex("m", K.SEX_ICAO) == "HOMBRE"
    assert canonical_sex("X", K.SEX_MX) == "X"


def test_kind_desconocido():
    with pytest.raises(ValueError):
        compare_values("otro", "a", "b")  # type: ignore[arg-type]


# ---- métricas

def test_levenshtein_cer_wer():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3 and levenshtein("abc", "") == 3 and levenshtein("a", "a") == 0
    assert cer("abcd", "abcd") == 0.0
    assert cer("abcd", "abxd") == 0.25
    assert cer("", "") == 0.0 and cer("", "x") == 1.0
    assert cer("ab", "") == 1.0
    assert wer("a b c d", "a x c d") == 0.25
    assert wer("", "") == 0.0 and wer("", "x") == 1.0
    assert wer("a b", "a b c") == 0.5


def test_classification_metrics():
    y_t = ["ine", "ine", "curp", "curp", "acta", "acta"]
    y_p = ["ine", "curp", "curp", None, "acta", "ine"]
    m = classification_metrics(y_t, y_p)
    assert m.n == 6
    assert m.accuracy == pytest.approx(3 / 6)
    assert m.unclassified_pct == pytest.approx(100 / 6)
    assert m.cross_category_errors == 2
    by = {c.label: c for c in m.per_class}
    assert by["ine"].tp == 1 and by["ine"].fp == 1 and by["ine"].fn == 1
    assert by["ine"].precision == 0.5 and by["ine"].recall == 0.5 and by["ine"].f1 == 0.5
    assert by["curp"].recall == 0.5 and by["curp"].precision == 0.5
    assert m.confusion.labels == ["acta", "curp", "ine"]
    assert m.confusion.columns[-1] == "(sin_clasificar)"
    assert m.confusion.matrix[1] == [0, 1, 0, 1]
    assert m.macro_f1 is not None


def test_classification_pred_fuera_de_verdad_y_vacio():
    m = classification_metrics(["a"], ["zzz"])
    assert m.confusion.labels == ["a", "zzz"]
    assert m.accuracy == 0.0
    by = {c.label: c for c in m.per_class}
    assert by["zzz"].support == 0 and by["zzz"].precision == 0.0 and by["zzz"].recall is None
    e = classification_metrics([], [])
    assert e.accuracy is None and e.unclassified_pct is None
    with pytest.raises(ValueError):
        classification_metrics(["a"], [])


def test_percentiles_n_suficiente():
    vals = list(range(1, 21))  # 20
    p = percentiles(vals)
    assert p["p50"] == 10 and p["p95"] == 19 and p["p99"] is None
    assert percentiles([1.0])["p50"] is None
    big = percentiles(list(range(1, 101)))
    assert big["p99"] == 99
    assert min_samples_for(95) == 20 and min_samples_for(99) == 100 and min_samples_for(50) == 2
    assert min_samples_for(100) > 10**6
    with pytest.raises(ValueError):
        percentile([], 50)
    assert percentiles([1, 2], ps=(99.9,)) == {"p99.9": None}


def test_throughput():
    assert throughput(10, 5.0) == 2.0
    assert throughput(10, 0) is None and throughput(10, None) is None and throughput(0, 5) is None
