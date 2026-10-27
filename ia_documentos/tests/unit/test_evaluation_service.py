"""Contrato de salida y errores controlados del caso de uso de evaluacion (pipeline falso)."""
import json
import time

import pytest

from application.evaluation import (
    AlgorithmInfo, EvaluationRequest, EvaluationService, _ejecutar_con_timeout,
)
from domain.entities.documento import DocumentoNoSoportadoError
from infrastructure.ocr.pdf_validator import PdfValidationError

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
ALGO = AlgorithmInfo("0.3.0", "m", "h" * 64, "abc1234", "plat")
CAMPOS_CONTRATO = ["schema_version", "sample_id", "run_id", "status", "error", "tipo_documento",
                   "datos", "metadatos", "timings", "algorithm"]


class Falso:
    def __init__(self, error=None, envelope=None, espera=0):
        self.error, self.espera, self.llamadas, self.cargas = error, espera, [], 0
        self.envelope = envelope or {
            "doc_id": "s1", "user_id": "eval", "tipo_documento": "curp",
            "datos": {"curp": "AAAA010101HDFXXX01", "edad": 5, "folio": None},
            "metadatos": {"ruta_texto_crudo": "/abs/x.md", "hash_sha256": "ab",
                          "tiempos_etapas_ms": {"ocr_ms": 10, "extraccion_ms": 5},
                          "campos_descartados_por_alucinacion": ["curp=SECRETO123"]},
            "datos_adicionales": {"x": 1},
        }

    def precargar(self):
        self.cargas += 1
        return True

    def process(self, ruta, user_id, doc_id=None):
        self.llamadas.append((ruta, user_id, doc_id))
        time.sleep(self.espera)
        if self.error:
            raise self.error
        return self.envelope


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(PDF)
    return p


def servicio(tmp_path, pipeline, **kw):
    return EvaluationService(pipeline, "R1", ALGO, tmp_path, **kw)


def test_ok_cumple_contrato(tmp_path, pdf):
    f = Falso()
    r = servicio(tmp_path, f).evaluate(EvaluationRequest("s1", str(pdf))).to_dict()
    assert list(r) == CAMPOS_CONTRATO
    assert r["schema_version"] == "1.0.0" and r["status"] == "ok" and r["error"] is None
    assert r["run_id"] == "R1" and r["tipo_documento"] == "curp"
    assert r["datos"] == {"curp": "AAAA010101HDFXXX01", "edad": "5", "folio": None}
    assert "ruta_texto_crudo" not in r["metadatos"]
    assert r["metadatos"]["campos_descartados_por_alucinacion"] == ["curp"]  # sin el valor
    assert "SECRETO123" not in json.dumps(r)
    assert r["timings"]["stages_ms"] == {"ocr_ms": 10, "extraccion_ms": 5}
    assert set(r["timings"]) == {"total_ms", "stages_ms", "model_load_ms"}
    assert r["algorithm"] == {"pipeline_version": "0.3.0", "llm_model": "m",
                              "config_hash": "h" * 64, "git_commit": "abc1234", "platform": "plat"}
    assert f.llamadas[0][1:] == ("eval", "s1")


def test_model_load_solo_en_primer_documento(tmp_path, pdf):
    f = Falso()
    s = servicio(tmp_path, f)
    r1 = s.evaluate(EvaluationRequest("s1", str(pdf))).to_dict()
    r2 = s.evaluate(EvaluationRequest("s2", str(pdf))).to_dict()
    assert r1["timings"]["model_load_ms"] is not None
    assert r2["timings"]["model_load_ms"] is None
    assert f.cargas == 1


def test_pipeline_sin_precargar(tmp_path, pdf):
    class Simple:
        def process(self, ruta, user_id, doc_id=None):
            return Falso().envelope
    r = servicio(tmp_path, Simple()).evaluate(EvaluationRequest("s1", str(pdf)))
    assert r.status == "ok" and r.timings.model_load_ms is None


@pytest.mark.parametrize("exc,code,retry", [
    (PdfValidationError("malo"), "PDF_INVALIDO", False),
    (DocumentoNoSoportadoError(), "DOCUMENTO_NO_SOPORTADO", False),
    (RuntimeError("sin motor"), "OCR_NO_DISPONIBLE", False),
    (ValueError("contiene CURP SECRETO123"), "ERROR_INTERNO", False),
])
def test_errores_controlados(tmp_path, pdf, exc, code, retry):
    r = servicio(tmp_path, Falso(error=exc)).evaluate(EvaluationRequest("s1", str(pdf))).to_dict()
    assert r["status"] == "error" and r["error"]["code"] == code
    assert r["error"]["retryable"] is retry
    assert r["tipo_documento"] is None and r["datos"] == {}
    assert "SECRETO123" not in json.dumps(r)
    assert list(r) == CAMPOS_CONTRATO


def test_tipo_fuera_de_contrato(tmp_path, pdf):
    env = Falso().envelope
    env["tipo_documento"] = "desconocido"
    r = servicio(tmp_path, Falso(envelope=env)).evaluate(EvaluationRequest("s1", str(pdf)))
    assert r.error.code == "ERROR_INTERNO"


def test_timeout_marca_retryable(tmp_path, pdf):
    r = servicio(tmp_path, Falso(espera=1.0), timeout_s=0.1).evaluate(EvaluationRequest("s1", str(pdf)))
    assert r.error.code == "TIMEOUT" and r.error.retryable is True


def test_timeout_helper_propaga_excepcion_y_resultado():
    assert _ejecutar_con_timeout(lambda: 3, 1) == 3
    assert _ejecutar_con_timeout(lambda: 4, None) == 4
    with pytest.raises(KeyError):
        _ejecutar_con_timeout(lambda: {}["x"], 1)


def test_rechazar(tmp_path):
    r = servicio(tmp_path, Falso()).rechazar(EvaluationRequest("linea_3", ""), "MANIFEST_INVALIDO", "m")
    assert r.error.code == "MANIFEST_INVALIDO"
