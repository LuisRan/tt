"""
integration/flask_app.py
Adaptador de entrada REST (FlaskRestAdapter) del modulo de IA.

Expone los endpoints de la Tabla 23 del documento del TT para que el backend
Node.js consuma el pipeline:

  POST /api/v1/documentos/procesa            -> 200 envelope | 400 | 413 | 415 | 422
  GET  /api/v1/documentos/<doc_id>           -> 200 envelope | 404
  GET  /api/v1/documentos/<doc_id>/texto     -> 200 markdown/json | 404
  GET  /api/v1/usuarios/<user_id>/documentos -> 200 lista
  GET  /health                               -> 200 estado del servicio + Ollama + plataforma

Seguridad:
  - Cabecera X-API-Key compartida con el backend (IA_API_KEY). /health es publico.
  - doc_id / user_id se validan contra una lista blanca de caracteres para
    evitar path traversal (se usan como nombre de archivo).
  - El servicio solo se publica dentro de la red interna de Docker; el
    navegador nunca habla directo con este modulo.

No toca nada del dominio ni de la infraestructura: solo traduce HTTP <-> pipeline.
"""
from __future__ import annotations

import hmac
import logging
import os
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, Response

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402
from infrastructure.logging_setup import configurar_logging  # noqa: E402

configurar_logging(settings.LOGS_DIR, nivel=settings.LOG_LEVEL)

from application.pipeline import DocumentPipeline  # noqa: E402
from domain.entities.documento import DocumentoNoSoportadoError  # noqa: E402
from infrastructure.ocr.pdf_validator import PdfValidationError  # noqa: E402

logger = logging.getLogger("ia.api")

_ID_VALIDO = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _id_valido(valor: str | None) -> bool:
    return bool(valor) and bool(_ID_VALIDO.match(valor))


def create_app(pipeline: DocumentPipeline | None = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = (settings.PDF_MAX_SIZE_MB + 1) * 1024 * 1024
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False  # type: ignore[attr-defined]

    estado: dict = {"pipeline": pipeline}
    lock_init = threading.Lock()
    # Docling + LLM consumen mucha memoria: se procesa un documento a la vez.
    semaforo = threading.BoundedSemaphore(int(os.environ.get("IA_MAX_CONCURRENT", "1")))

    def obtener_pipeline() -> DocumentPipeline:
        if estado["pipeline"] is None:
            with lock_init:
                if estado["pipeline"] is None:
                    estado["pipeline"] = DocumentPipeline()
        return estado["pipeline"]

    # ── Seguridad ──────────────────────────────────────────────────────────────

    @app.before_request
    def verificar_api_key():
        if request.path == "/health" or request.method == "OPTIONS":
            return None
        if not settings.IA_API_KEY:
            return None  # modo desarrollo sin clave
        recibida = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(recibida.encode(), settings.IA_API_KEY.encode()):
            logger.warning("api_key_invalida", extra={"ip": request.remote_addr, "ruta": request.path})
            return jsonify({"error": "No autorizado", "codigo": "API_KEY_INVALIDA"}), 401
        return None

    @app.after_request
    def cabeceras_seguridad(resp: Response):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ── Endpoints ──────────────────────────────────────────────────────────────

    @app.post("/api/v1/documentos/procesa")
    def procesar_documento():
        if "archivo" not in request.files:
            return jsonify({"error": "Campo 'archivo' requerido (multipart/form-data)",
                            "codigo": "ARCHIVO_REQUERIDO"}), 400

        archivo = request.files["archivo"]
        user_id = request.form.get("user_id", "anonimo")
        doc_id = request.form.get("doc_id") or uuid.uuid4().hex

        if not _id_valido(user_id) or not _id_valido(doc_id):
            return jsonify({"error": "user_id/doc_id invalidos (solo letras, numeros, - y _)",
                            "codigo": "ID_INVALIDO"}), 400

        nombre = (archivo.filename or "").lower()
        if nombre and not nombre.endswith(".pdf") and archivo.mimetype != "application/pdf":
            return jsonify({"error": "Solo se aceptan archivos PDF", "codigo": "FORMATO_INVALIDO"}), 422

        fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="ia_")
        os.close(fd)
        ruta_tmp = Path(tmp)
        try:
            archivo.save(ruta_tmp)
            with open(ruta_tmp, "rb") as f:
                if f.read(5) != b"%PDF-":
                    return jsonify({"error": "El archivo no es un PDF valido",
                                    "codigo": "FORMATO_INVALIDO"}), 422

            with semaforo:
                resultado = obtener_pipeline().process(ruta_tmp, user_id=user_id, doc_id=doc_id)
            return jsonify(resultado), 200

        except PdfValidationError as e:
            return jsonify({"error": str(e), "codigo": "PDF_INVALIDO"}), 422
        except DocumentoNoSoportadoError as e:
            return jsonify({"error": e.mensaje, "codigo": "DOCUMENTO_NO_SOPORTADO"}), 415
        except RuntimeError as e:
            logger.error("ocr_no_disponible", extra={"error": str(e)})
            return jsonify({"error": str(e), "codigo": "OCR_NO_DISPONIBLE"}), 503
        except Exception as e:  # pragma: no cover
            logger.exception("error_interno_procesando", extra={"doc_id": doc_id})
            return jsonify({"error": "Error interno al procesar el documento",
                            "codigo": "ERROR_INTERNO", "detalle": str(e)[:300]}), 500
        finally:
            ruta_tmp.unlink(missing_ok=True)

    @app.get("/api/v1/documentos/<doc_id>")
    def obtener_documento(doc_id: str):
        if not _id_valido(doc_id):
            return jsonify({"error": "doc_id invalido"}), 400
        resultado = obtener_pipeline().obtener_resultado(doc_id)
        if not resultado:
            return jsonify({"error": "Documento no encontrado", "codigo": "NO_ENCONTRADO"}), 404
        return jsonify(resultado), 200

    @app.get("/api/v1/documentos/<doc_id>/texto")
    def obtener_texto(doc_id: str):
        if not _id_valido(doc_id):
            return jsonify({"error": "doc_id invalido"}), 400
        texto = obtener_pipeline().recuperar_texto_crudo(doc_id)
        if texto is None:
            return jsonify({"error": "Texto no encontrado", "codigo": "NO_ENCONTRADO"}), 404
        if "text/markdown" in request.headers.get("Accept", ""):
            return Response(texto, mimetype="text/markdown; charset=utf-8")
        return jsonify({"doc_id": doc_id, "texto": texto}), 200

    @app.get("/api/v1/usuarios/<user_id>/documentos")
    def listar_documentos(user_id: str):
        if not _id_valido(user_id):
            return jsonify({"error": "user_id invalido"}), 400
        docs = obtener_pipeline().listar_documentos_usuario(user_id)
        return jsonify({"user_id": user_id, "documentos": docs}), 200

    @app.get("/health")
    def health():
        try:
            return jsonify(obtener_pipeline().health_check()), 200
        except Exception as e:  # pragma: no cover
            return jsonify({"status": "error", "error": str(e)}), 503

    @app.errorhandler(413)
    def demasiado_grande(_e):
        return jsonify({"error": f"El archivo excede {settings.PDF_MAX_SIZE_MB} MB",
                        "codigo": "ARCHIVO_DEMASIADO_GRANDE"}), 413

    @app.errorhandler(404)
    def no_encontrado(_e):
        return jsonify({"error": "Ruta no encontrada"}), 404

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    # Desarrollo: python -m integration.flask_app
    app.run(host=settings.IA_API_HOST, port=settings.IA_API_PORT, debug=False)
