"""
Pruebas del adaptador REST (integration/flask_app.py) con un pipeline falso:
verifican el contrato HTTP de la Tabla 23 sin necesitar Docling ni Ollama.
"""
import io

import pytest

from config import settings
from domain.entities.documento import DocumentoNoSoportadoError
from infrastructure.ocr.pdf_validator import PdfValidationError
from integration.flask_app import create_app

PDF_MIN = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


class PipelineFalso:
    def __init__(self, error=None):
        self.error = error
        self.procesados = {}

    def process(self, ruta, user_id, doc_id):
        if self.error:
            raise self.error
        r = {"doc_id": doc_id, "user_id": user_id, "tipo_documento": "ine",
             "datos": {"curp": "SACR760818HDFVNL08"}, "metadatos": {"confianza": 0.9}}
        self.procesados[doc_id] = r
        return r

    def obtener_resultado(self, doc_id):
        return self.procesados.get(doc_id)

    def recuperar_texto_crudo(self, doc_id):
        return "TEXTO OCR" if doc_id in self.procesados else None

    def listar_documentos_usuario(self, user_id):
        return [{"doc_id": d} for d, r in self.procesados.items() if r["user_id"] == user_id]

    def health_check(self):
        return {"status": "ok"}


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(settings, "IA_API_KEY", "clave-test")
    pipeline = PipelineFalso()
    app = create_app(pipeline)
    return app.test_client(), pipeline


H = {"X-API-Key": "clave-test"}


def _post(c, contenido=PDF_MIN, nombre="doc.pdf", **form):
    data = {"archivo": (io.BytesIO(contenido), nombre), **form}
    return c.post("/api/v1/documentos/procesa", data=data, headers=H,
                  content_type="multipart/form-data")


def test_health_publico(cliente):
    c, _ = cliente
    assert c.get("/health").status_code == 200


def test_requiere_api_key(cliente):
    c, _ = cliente
    assert c.get("/api/v1/documentos/abc").status_code == 401


def test_procesa_ok_y_recupera(cliente):
    c, _ = cliente
    r = _post(c, user_id="u1", doc_id="d1")
    assert r.status_code == 200
    assert r.json["datos"]["curp"] == "SACR760818HDFVNL08"
    assert c.get("/api/v1/documentos/d1", headers=H).status_code == 200
    assert c.get("/api/v1/documentos/d1/texto", headers=H).json["texto"] == "TEXTO OCR"
    md = c.get("/api/v1/documentos/d1/texto", headers={**H, "Accept": "text/markdown"})
    assert md.mimetype == "text/markdown"
    assert c.get("/api/v1/usuarios/u1/documentos", headers=H).json["documentos"] == [{"doc_id": "d1"}]


def test_sin_archivo_400(cliente):
    c, _ = cliente
    assert c.post("/api/v1/documentos/procesa", headers=H).status_code == 400


def test_no_pdf_422(cliente):
    c, _ = cliente
    assert _post(c, contenido=b"hola", nombre="x.pdf").status_code == 422


def test_id_invalido_400(cliente):
    c, _ = cliente
    assert _post(c, doc_id="../../etc").status_code == 400


def test_no_encontrado_404(cliente):
    c, _ = cliente
    assert c.get("/api/v1/documentos/nada", headers=H).status_code == 404
    assert c.get("/api/v1/documentos/nada/texto", headers=H).status_code == 404


@pytest.mark.parametrize("error,codigo", [
    (PdfValidationError("muy grande"), 422),
    (DocumentoNoSoportadoError(), 415),
    (RuntimeError("sin ocr"), 503),
])
def test_mapeo_errores(monkeypatch, error, codigo):
    monkeypatch.setattr(settings, "IA_API_KEY", "")
    c = create_app(PipelineFalso(error=error)).test_client()
    data = {"archivo": (io.BytesIO(PDF_MIN), "doc.pdf")}
    r = c.post("/api/v1/documentos/procesa", data=data, content_type="multipart/form-data")
    assert r.status_code == codigo
