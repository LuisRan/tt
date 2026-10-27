"""Formato (regex del catálogo) y coherencia cruzada sobre 1000 registros por doc/variante."""
from __future__ import annotations

import base64
import copy
import re

import pytest

from contracts import CATALOG, DocType, Face, Variant, gt_field_names
from contracts.layout_spec import FieldType
from src.data_injection import generate_record, record_digest, validate_record
from src.data_injection.text import upper_id
from src.data_injection.validators import (
    build_td1_ine,
    clave_elector_letters,
    curp_birth_date,
    mrz_check_digit,
    parse_date,
    validate_curp,
    validate_curp_person,
)

N = 1000


def _regex_fields(doc_type, face, variant):
    return [
        f for f in CATALOG[doc_type][face]
        if f.in_gt and f.type != FieldType.STATIC and f.fmt.startswith("^")
        and (f.variants is None or variant in f.variants)
    ]


def test_1000_records_match_catalog_regex(doc_variant):
    doc_type, variant = doc_variant
    for i in range(N):
        rec = generate_record(doc_type, 2024, i, variant)
        for face in CATALOG[doc_type]:
            for f in _regex_fields(doc_type, face, variant):
                v = rec[face][f.name]
                assert re.match(f.fmt, v), (i, f.name, v)


def test_1000_records_coherent(doc_variant):
    doc_type, variant = doc_variant
    for i in range(N):
        rec = generate_record(doc_type, 99, i, variant)
        assert validate_record(doc_type, rec, variant) == [], i


def test_record_fields_are_catalog_minus_static(doc_variant):
    doc_type, variant = doc_variant
    rec = generate_record(doc_type, 1, 0, variant)
    assert set(rec) == set(CATALOG[doc_type])
    for face, values in rec.items():
        static = {f.name for f in CATALOG[doc_type][face] if f.type == FieldType.STATIC}
        expected = [n for n in gt_field_names(doc_type, face, variant) if n not in static]
        assert set(values) == set(expected)


def test_default_variant_and_invalid_variant():
    assert generate_record("ine_2016", 5, 3) == generate_record(DocType.INE_2016, 5, 3, Variant.NACIONAL)
    with pytest.raises(ValueError):
        generate_record(DocType.CURP_2019, 1, 0, Variant.EXTRANJERO)


def test_seeds_and_indices_differ(doc_variant):
    doc_type, variant = doc_variant
    digests = {record_digest(generate_record(doc_type, s, 0, variant)) for s in range(50)}
    assert len(digests) == 50
    digests = {record_digest(generate_record(doc_type, 7, i, variant)) for i in range(50)}
    assert len(digests) == 50


def test_ine_variants_differ_and_extranjero_abroad():
    nac = generate_record(DocType.INE_2016, 11, 0, Variant.NACIONAL)
    ext = generate_record(DocType.INE_2016, 11, 0, Variant.EXTRANJERO)
    assert record_digest(nac) != record_digest(ext)
    for i in range(200):
        dom = generate_record(DocType.INE_2016, 11, i, Variant.EXTRANJERO)[Face.ANVERSO]["domicilio"]
        assert dom[2] in {"ESTADOS UNIDOS", "CANADA", "ESPAÑA"}
        assert not dom[1].startswith("COL.")
        dom = generate_record(DocType.INE_2016, 11, i, Variant.NACIONAL)[Face.ANVERSO]["domicilio"]
        assert dom[1].startswith("COL.")


# --------------------------------------------------------------------------
# Coherencia cruzada explícita (además de validate_record)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("variant", [Variant.NACIONAL, Variant.EXTRANJERO])
def test_ine_cross_coherence(variant):
    for i in range(200):
        rec = generate_record(DocType.INE_2016, 3, i, variant)
        a, r = rec[Face.ANVERSO], rec[Face.REVERSO]
        ap1, ap2, nombres = a["nombre"]
        birth = parse_date(a["fecha_nacimiento"])
        assert validate_curp_person(a["curp"], nombres=nombres, ap1=ap1, ap2=ap2, birth=birth, sexo=a["sexo"]) == []
        clave = a["clave_elector"]
        assert clave[:6] == clave_elector_letters(nombres, ap1, ap2)
        assert clave[6:12] == a["curp"][4:10]
        assert clave[14] == a["sexo"] == a["curp"][10]
        mrz = r["mrz"]
        assert mrz[0][5:14] == r["cic"] and mrz[0][17:] == r["ocr"]
        assert mrz[1][0:6] == birth.strftime("%y%m%d")
        assert mrz[1][7] == {"H": "M", "M": "F"}[a["sexo"]]
        assert mrz[1][8:14] == a["vigencia"][2:] + "1231"
        assert mrz == build_td1_ine(cic=r["cic"], ocr=r["ocr"], birth=birth, sexo=a["sexo"],
                                    vigencia_year=int(a["vigencia"]), ap1=ap1, ap2=ap2, nombres=nombres)
        assert mrz[2].startswith(upper_id(ap1).replace("Ñ", "N").replace(" ", "<"))
        # mayúsculas sin acentos (salvo Ñ)
        for s in a["nombre"] + a["domicilio"]:
            assert s == upper_id(s) and not re.search(r"[ÁÉÍÓÚÜ]", s)
        assert int(a["vigencia"]) == int(a["emision"]) + 10


def test_curp_doc_cross_coherence():
    for i in range(200):
        u = generate_record(DocType.CURP_2019, 3, i)[Face.UNICA]
        assert u["nombre_destinatario"] == u["nombre"]
        assert u["codigo_barras_texto"] == u["codigo_barras"]
        assert u["curp"] in u["qr_datos"] and "MOCK DATA" in u["qr_datos"]
        for c in u["curps_asociadas"]:
            assert validate_curp(c) == [] and c != u["curp"]


def test_acta_cross_coherence():
    for i in range(200):
        u = generate_record(DocType.ACTA_NACIMIENTO_2015, 3, i)[Face.UNICA]
        birth = parse_date(u["reg_fecha_nacimiento"])
        assert u["curp"][4:10] == birth.strftime("%y%m%d")
        assert u["curp"][10] == ("H" if u["reg_sexo"] == "HOMBRE" else "M")
        for p in ("p1", "p2"):
            assert curp_birth_date(u[f"{p}_curp"]) < birth
        assert u["identificador_barcode"] == u["identificador_electronico"]
        assert u["codigo_verificacion_barcode"] == u["codigo_verificacion"]
        assert parse_date(u["fecha_registro"]) >= birth


def test_acta_codigo_2d_payload():
    lens = set()
    for i in range(300):
        u = generate_record(DocType.ACTA_NACIMIENTO_2015, 5, i)[Face.UNICA]
        v = u["codigo_2d"]
        lens.add(len(v))
        assert v.startswith("MOCK DATA|") and 300 <= len(v) <= 600
        parts = v.split("|")
        assert u["identificador_electronico"] in parts and u["curp"] in parts
        assert u["p1_curp"] in parts and u["p2_curp"] in parts
        assert u["reg_fecha_nacimiento"] in parts and u["reg_nombres"] in parts
        assert len(base64.b64decode(parts[-1], validate=True)) >= 32
        # el QR pequeño sigue existiendo y es otro payload
        assert u["qr"] and u["qr"] != v and len(u["qr"]) < len(v)
        assert validate_record(DocType.ACTA_NACIMIENTO_2015, generate_record(DocType.ACTA_NACIMIENTO_2015, 5, i)) == []
    assert len(lens) > 1  # longitud variable por seed


def test_acta_codigo_2d_deterministic_and_seeded():
    a = generate_record(DocType.ACTA_NACIMIENTO_2015, 9, 4)[Face.UNICA]["codigo_2d"]
    assert a == generate_record(DocType.ACTA_NACIMIENTO_2015, 9, 4)[Face.UNICA]["codigo_2d"]
    assert a != generate_record(DocType.ACTA_NACIMIENTO_2015, 10, 4)[Face.UNICA]["codigo_2d"]


@pytest.mark.parametrize("fn", [
    lambda v: v.replace("MOCK DATA|", "DATA|", 1),
    lambda v: v[:150],
    lambda v: v.replace("|ACTA2D|", "|ACTA2D|X", 1),
    lambda v: v[:-1] + "!",
])
def test_acta_codigo_2d_tampering_detected(fn):
    assert _mutated(DocType.ACTA_NACIMIENTO_2015, Variant.DEFAULT, Face.UNICA, "codigo_2d", fn) != []


def test_acta_codigo_2d_equal_to_qr_rejected():
    rec = copy.deepcopy(generate_record(DocType.ACTA_NACIMIENTO_2015, 1, 0))
    rec[Face.UNICA]["qr"] = rec[Face.UNICA]["codigo_2d"]
    assert validate_record(DocType.ACTA_NACIMIENTO_2015, rec)


@pytest.mark.parametrize("variant", [Variant.NACIONAL, Variant.EXTRANJERO])
def test_ine_mrz_line1_is_idmex_cic_digit_ocr(variant):
    for i in range(300):
        r = generate_record(DocType.INE_2016, 11, i, variant)[Face.REVERSO]
        l1 = r["mrz"][0]
        assert len(l1) == 30
        assert l1 == "IDMEX" + r["cic"] + mrz_check_digit(r["cic"]) + "<<" + r["ocr"]
        assert l1[5:14] == r["cic"] and l1[17:30] == r["ocr"]  # pos. 5-13 y 17-29


def test_ine_mrz_cic_ocr_mismatch_detected():
    rec = copy.deepcopy(generate_record(DocType.INE_2016, 1, 0))
    r = rec[Face.REVERSO]
    r["ocr"] = r["ocr"][:4] + str((int(r["ocr"][4]) + 1) % 10) + r["ocr"][5:]
    assert validate_record(DocType.INE_2016, rec)


def test_pasaporte_cross_coherence():
    for i in range(200):
        u = generate_record(DocType.PASAPORTE_ELECTRONICO_2021, 3, i)[Face.UNICA]
        l1, l2 = u["mrz"]
        assert l1.startswith("P<MEX")
        assert l2[0:9] == u["numero_pasaporte"]
        assert l2[13:19] == parse_date(u["fecha_nacimiento"], "spaced").strftime("%y%m%d")
        assert l2[20] == u["sexo"]
        assert l2[21:27] == parse_date(u["fecha_caducidad"], "spaced").strftime("%y%m%d")
        assert u["curp"][4:10] == l2[13:19]


# --------------------------------------------------------------------------
# validate_record detecta registros alterados
# --------------------------------------------------------------------------
def _mutated(doc_type, variant, face, field, fn):
    rec = copy.deepcopy(generate_record(doc_type, 8, 0, variant))
    rec[face][field] = fn(rec[face][field])
    return validate_record(doc_type, rec, variant)


@pytest.mark.parametrize("doc_type,variant,face,field,fn", [
    (DocType.INE_2016, Variant.NACIONAL, Face.ANVERSO, "sexo", lambda v: "M" if v == "H" else "H"),
    (DocType.INE_2016, Variant.NACIONAL, Face.ANVERSO, "fecha_nacimiento", lambda v: "01/01/1960" if v != "01/01/1960" else "02/01/1960"),
    (DocType.INE_2016, Variant.EXTRANJERO, Face.ANVERSO, "nombre", lambda v: [v[0], v[1], "ZOE"]),
    (DocType.INE_2016, Variant.NACIONAL, Face.REVERSO, "cic", lambda v: str((int(v) + 1) % 10**9).zfill(9)),
    (DocType.INE_2016, Variant.NACIONAL, Face.REVERSO, "mrz", lambda v: [v[0], v[1][:29] + str((int(v[1][29]) + 1) % 10), v[2]]),
    (DocType.INE_2016, Variant.NACIONAL, Face.ANVERSO, "vigencia", lambda v: str(int(v) + 1)),
    (DocType.CURP_2019, Variant.DEFAULT, Face.UNICA, "nombre_destinatario", lambda v: v + " X"),
    (DocType.CURP_2019, Variant.DEFAULT, Face.UNICA, "codigo_barras_texto", lambda v: v[::-1] if v != v[::-1] else "0" * 15),
    (DocType.CURP_2019, Variant.DEFAULT, Face.UNICA, "curp", lambda v: v[:17] + str((int(v[17]) + 1) % 10)),
    (DocType.CURP_2019, Variant.DEFAULT, Face.UNICA, "qr_datos", lambda v: v.replace("MOCK DATA", "DATA")),
    (DocType.ACTA_NACIMIENTO_2015, Variant.DEFAULT, Face.UNICA, "p1_curp", lambda v: v[:17] + str((int(v[17]) + 1) % 10)),
    (DocType.ACTA_NACIMIENTO_2015, Variant.DEFAULT, Face.UNICA, "reg_sexo", lambda v: "MUJER" if v == "HOMBRE" else "HOMBRE"),
    (DocType.ACTA_NACIMIENTO_2015, Variant.DEFAULT, Face.UNICA, "identificador_barcode", lambda v: "1" * 20 if v != "1" * 20 else "2" * 20),
    (DocType.PASAPORTE_ELECTRONICO_2021, Variant.DEFAULT, Face.UNICA, "numero_pasaporte", lambda v: "Z" + v[1:] if v[0] != "Z" else "Y" + v[1:]),
    (DocType.PASAPORTE_ELECTRONICO_2021, Variant.DEFAULT, Face.UNICA, "fecha_caducidad", lambda v: v[:6] + str(int(v[6:]) + 1)),
    (DocType.PASAPORTE_ELECTRONICO_2021, Variant.DEFAULT, Face.UNICA, "foto", lambda v: "avatar:zz"),
])
def test_validate_record_detects_tampering(doc_type, variant, face, field, fn):
    assert _mutated(doc_type, variant, face, field, fn) != []


def test_validate_record_detects_missing_and_extra_fields():
    rec = copy.deepcopy(generate_record(DocType.CURP_2019, 1, 0))
    del rec[Face.UNICA]["folio"]
    assert validate_record(DocType.CURP_2019, rec)
    rec = copy.deepcopy(generate_record(DocType.CURP_2019, 1, 0))
    rec[Face.UNICA]["titulo"] = "X"  # STATIC: no debe venir en el registro
    assert validate_record(DocType.CURP_2019, rec)


@pytest.mark.parametrize("variant", [Variant.NACIONAL, Variant.EXTRANJERO])
def test_ine_address_lines_fit_card(variant):
    for i in range(500):
        dom = generate_record(DocType.INE_2016, 4, i, variant)[Face.ANVERSO]["domicilio"]
        assert len(dom) == 3 and all(0 < len(x) <= 38 for x in dom), dom
