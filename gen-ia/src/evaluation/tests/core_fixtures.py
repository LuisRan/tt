"""Construcción de GT/predicciones sintéticas para las pruebas del núcleo de evaluación."""
from __future__ import annotations

from typing import Any, Optional

from contracts.common import BBox, DocType, Face
from contracts.evaluation import OcrError, OcrPrediction, OcrTimings, SampleRef
from contracts.ground_truth import FieldGT, GroundTruth, PageGT
from contracts.layout_spec import FieldType

BB = BBox(x0=0, y0=0, x1=1, y1=1)


def _fields(values: dict[str, Any]) -> list[FieldGT]:
    out = []
    for k, v in values.items():
        t = FieldType.MULTILINE if isinstance(v, list) else FieldType.TEXT
        out.append(FieldGT(name=k, type=t, value=v, bbox_mm=BB))
    return out


def make_gt(doc_type: DocType, values: dict[str, Any], sample_id: Optional[str] = None,
            face: Face = Face.UNICA) -> GroundTruth:
    return GroundTruth(
        sample_id=sample_id or f"{doc_type.value}-x-1-000000", doc_type=doc_type, layout_version="t",
        seed=1, generator_version="t",
        pages=[PageGT(page_index=0, face=face, width_mm=10, height_mm=10, fields=_fields(values))],
    )


INE = {
    "nombre": ["BARAJAS", "MESA", "PAMELA"],
    "domicilio": ["DIAGONAL ALMARAZ 924", "COL. VIEJA REPUBLICA CHECA 10876", "VIEJA GAMBIA, TLAX."],
    "clave_elector": "BRMSPM46051029M440", "curp": "BAMP460510MTLRSM07", "anio_registro": "2009 01",
    "estado": "29", "municipio": "080", "seccion": "7614", "localidad": "0036", "emision": "2019",
    "vigencia": "2029", "fecha_nacimiento": "10/05/1946", "sexo": "M",
}
ACTA = {
    "folio_impresion": "40129132", "identificador_electronico": "09024001319491052913",
    "curp": "OUAM470710HDFQRR77", "entidad_registro": "CIUDAD DE MEXICO", "municipio_registro": "NUEVA BURUNDI",
    "oficialia": "13", "fecha_registro": "31/05/1949", "libro": "17", "numero_acta": "10529",
    "reg_nombres": "MARCOS", "reg_primer_apellido": "OQUENDO", "reg_segundo_apellido": "AREVALO",
    "reg_sexo": "HOMBRE", "reg_fecha_nacimiento": "10/07/1947", "reg_lugar_nacimiento": "CIUDAD DE MEXICO",
    "p1_nombres": "ADAN", "p1_primer_apellido": "OQUENDO", "p1_segundo_apellido": "LEDESMA",
    "p1_curp": "OULA100703HSLQDD32",
    "p2_nombres": "MARIA ELENA", "p2_primer_apellido": "AREVALO", "p2_segundo_apellido": "JASSO",
    "p2_curp": "AEJE200626MDFRSL88",
}
PASAPORTE = {
    "tipo": "P", "codigo_pais": "MEX", "numero_pasaporte": "N01028118", "apellidos": "ANGUIANO MARRERO",
    "nombres": "TIMOTEO", "nacionalidad": "MEXICANA", "curp": "AUMT911027HDGNRM35",
    "fecha_nacimiento": "27 10 1991", "sexo": "M", "lugar_nacimiento": "DURANGO",
    "fecha_expedicion": "19 04 2022", "fecha_caducidad": "19 04 2025",
    "mrz": ["P<MEXANGUIANO<MARRERO<<TIMOTEO<<<<<<<<<<<<<<", "N010281184MEX9110270M2504199<<<<<<<<<<<<<<02"],
}
CURP = {
    "curp": "CORA591103MSLRLN64", "nombre": "ANEL CORDOVA RAEL", "fecha_inscripcion": "21/03/2017",
    "folio": "64106883", "entidad_registro": "SINALOA", "codigo_barras_texto": "150543364553112",
}

GT_VALUES = {
    DocType.INE_2016: INE, DocType.ACTA_NACIMIENTO_2015: ACTA,
    DocType.PASAPORTE_ELECTRONICO_2021: PASAPORTE, DocType.CURP_2019: CURP,
}


def perfect_datos(doc_type: DocType) -> dict[str, str]:
    """Salida OCR perfecta (nombres de campo del OCR) para los GT de arriba."""
    if doc_type == DocType.INE_2016:
        return {
            "nombre_completo": "PAMELA BARAJAS MESA", "primer_apellido": "BARAJAS", "segundo_apellido": "MESA",
            "nombres": "PAMELA", "curp": "BAMP460510MTLRSM07", "fecha_nacimiento": "10/05/1946", "sexo": "M",
            "clave_elector": "BRMSPM46051029M440",
            "domicilio": "DIAGONAL ALMARAZ 924 COL. VIEJA REPUBLICA CHECA 10876 VIEJA GAMBIA, TLAX.",
            "estado": "29", "municipio": "080", "seccion": "7614", "localidad": "0036",
            "año_emision": "2019", "año_vigencia": "2029",
        }
    if doc_type == DocType.ACTA_NACIMIENTO_2015:
        return {
            "nombre_completo": "MARCOS OQUENDO AREVALO", "primer_apellido": "OQUENDO",
            "segundo_apellido": "AREVALO", "nombres": "MARCOS", "curp": "OUAM470710HDFQRR77",
            "fecha_nacimiento": "10/07/1947", "sexo": "HOMBRE", "folio_impresion": "40129132",
            "identificador_electronico": "09024001319491052913", "numero_acta": "10529", "oficialia": "13",
            "libro": "17", "entidad_registro": "CIUDAD DE MEXICO", "municipio_registro": "NUEVA BURUNDI",
            "fecha_registro": "31/05/1949", "nombre_padre": "ADAN OQUENDO LEDESMA",
            "curp_padre": "OULA100703HSLQDD32", "nombre_madre": "MARIA ELENA AREVALO JASSO",
            "curp_madre": "AEJE200626MDFRSL88", "lugar_nacimiento": "CIUDAD DE MEXICO",
        }
    if doc_type == DocType.PASAPORTE_ELECTRONICO_2021:
        return {
            "nombre_completo": "TIMOTEO ANGUIANO MARRERO", "primer_apellido": "ANGUIANO", "nombres": "TIMOTEO",
            "curp": "AUMT911027HDGNRM35", "fecha_nacimiento": "27 10 1991", "sexo": "M",
            "lugar_nacimiento": "DURANGO", "numero_pasaporte": "N01028118", "tipo_pasaporte": "P",
            "pais_expedicion": "MEX", "nacionalidad": "MEXICANA", "fecha_expedicion": "19 04 2022",
            "fecha_caducidad": "19 04 2025", "mrz_linea1": PASAPORTE["mrz"][0], "mrz_linea2": PASAPORTE["mrz"][1],
        }
    return {
        "nombre_completo": "ANEL CORDOVA RAEL", "curp": "CORA591103MSLRLN64", "fecha_nacimiento": "03/11/1959",
        "sexo": "M", "entidad_registro": "SINALOA", "fecha_inscripcion": "21/03/2017", "folio": "64106883",
    }


def make_pred(sample_id: str, datos: dict[str, Optional[str]], tipo: Optional[str] = "ine",
              total_ms: Optional[float] = 100.0, model_load_ms: Optional[float] = None,
              stages: Optional[dict[str, float]] = None) -> OcrPrediction:
    return OcrPrediction(
        sample_id=sample_id, status="ok", tipo_documento=tipo, datos=datos,
        timings=None if total_ms is None else OcrTimings(
            total_ms=total_ms, stages_ms=stages or {}, model_load_ms=model_load_ms),
    )


def make_error_pred(sample_id: str, code: str = "TIMEOUT") -> OcrPrediction:
    return OcrPrediction(sample_id=sample_id, status="error",
                         error=OcrError(code=code, message="x"),
                         timings=OcrTimings(total_ms=5.0))


def make_ref(gt: GroundTruth, preset: str = "none", split: str = "dev", group: str = "g1") -> SampleRef:
    return SampleRef(sample_id=gt.sample_id, group_id=group, split=split, doc_type=gt.doc_type,  # type: ignore[arg-type]
                     degradation_preset=preset, pdf_path="a.pdf", ground_truth_path="a.json",
                     submitted_pdf_path="a.pdf", run_id="r1")
