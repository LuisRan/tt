"""
application/evaluation.py
Caso de uso de EVALUACION supervisada: procesa un PDF por la misma
`DocumentPipeline.process` del modo web y devuelve un resultado serializable
(contrato OcrPrediction 1.0.0). No conoce Flask, sesiones ni HTTP, y no importa
`config.settings` (los valores efectivos llegan por parametro).
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from domain.entities.documento import DocumentoNoSoportadoError
from infrastructure.ocr.pdf_validator import PdfValidationError

SCHEMA_VERSION = "1.0.0"
EVAL_USER_ID = "eval"
# Misma regex que integration/flask_app.py (doc_id / user_id)
SAMPLE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
TIPOS_VALIDOS = {"ine", "curp", "acta_nacimiento", "pasaporte"}

# Metadatos del envelope que NO se exportan (rutas absolutas / datos con PII)
_META_EXCLUIDOS = {"ruta_texto_crudo"}


class PipelinePort(Protocol):
    def process(self, ruta_pdf: Any, user_id: str, doc_id: Optional[str] = None) -> dict: ...


@dataclass(frozen=True)
class EvaluationRequest:
    sample_id: str
    pdf_path: str


@dataclass
class EvaluationError:
    code: str
    message: str
    retryable: bool = False


@dataclass
class EvaluationTimings:
    total_ms: float = 0.0
    stages_ms: dict = field(default_factory=dict)
    model_load_ms: Optional[float] = None


@dataclass(frozen=True)
class AlgorithmInfo:
    pipeline_version: str
    llm_model: Optional[str]
    config_hash: str
    git_commit: Optional[str] = None
    platform: Optional[str] = None


@dataclass
class EvaluationResult:
    sample_id: str
    run_id: str
    status: str                       # "ok" | "error"
    timings: EvaluationTimings
    algorithm: AlgorithmInfo
    error: Optional[EvaluationError] = None
    tipo_documento: Optional[str] = None
    datos: dict = field(default_factory=dict)
    metadatos: dict = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        # Orden de claves = contrato OcrPrediction
        return {
            "schema_version": self.schema_version,
            "sample_id": self.sample_id,
            "run_id": self.run_id,
            "status": self.status,
            "error": asdict(self.error) if self.error else None,
            "tipo_documento": self.tipo_documento,
            "datos": self.datos,
            "metadatos": self.metadatos,
            "timings": asdict(self.timings),
            "algorithm": asdict(self.algorithm),
        }


def _dentro_de(ruta: Path, base: Path) -> bool:
    try:
        ruta.relative_to(base)
        return True
    except ValueError:
        return False


def resolver_pdf(pdf_path: str, base_dir: Path, max_mb: float) -> Path:
    """Valida y resuelve la ruta del PDF. Lanza EvaluationRejected si no es segura.

    - relativa: se interpreta desde base_dir (dir del manifest) y no puede salir de el
    - absoluta: permitida, pero un symlink cuyo destino quede fuera de base_dir se rechaza
    - debe existir, ser archivo regular y no exceder max_mb
    """
    if not isinstance(pdf_path, str) or not pdf_path.strip() or "\x00" in pdf_path:
        raise EvaluationRejected("PDF_INVALIDO", "pdf_path vacio o invalido")
    base = base_dir.resolve()
    bruto = Path(pdf_path)
    if bruto.is_absolute():
        candidato = Path(os.path.normpath(bruto))
    else:
        candidato = Path(os.path.normpath(base / bruto))
        if not _dentro_de(candidato, base):
            raise EvaluationRejected("RUTA_NO_PERMITIDA",
                                     "pdf_path relativo sale del directorio del manifest")
    if not candidato.exists():
        raise EvaluationRejected("PDF_INVALIDO", "El archivo PDF no existe")
    real = candidato.resolve()
    if candidato.is_symlink() and not _dentro_de(real, base):
        raise EvaluationRejected("RUTA_NO_PERMITIDA",
                                 "symlink con destino fuera del directorio del manifest")
    if _dentro_de(candidato, base) and not _dentro_de(real, base):
        raise EvaluationRejected("RUTA_NO_PERMITIDA",
                                 "la ruta resuelve fuera del directorio del manifest")
    if not real.is_file():
        raise EvaluationRejected("PDF_INVALIDO", "pdf_path no es un archivo regular")
    if real.stat().st_size / (1024 * 1024) > max_mb:
        raise EvaluationRejected("PDF_INVALIDO", f"El PDF excede el limite de {max_mb} MB")
    with open(real, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise EvaluationRejected("FORMATO_INVALIDO", "El archivo no es un PDF valido")
    return real


class EvaluationRejected(Exception):
    """Entrada rechazada antes de llegar al pipeline (error controlado)."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


class _Timeout(Exception):
    pass


def _ejecutar_con_timeout(fn: Callable[[], Any], timeout_s: Optional[float]) -> Any:
    """Ejecuta fn en un hilo daemon. LIMITACION: el pipeline no es cancelable; si
    vence el plazo el hilo sigue ejecutando en segundo plano hasta terminar."""
    if not timeout_s or timeout_s <= 0:
        return fn()
    caja: dict = {}

    def _run():
        try:
            caja["ok"] = fn()
        except BaseException as e:  # noqa: BLE001 - se re-lanza en el hilo principal
            caja["err"] = e

    hilo = threading.Thread(target=_run, daemon=True)
    hilo.start()
    hilo.join(timeout_s)
    if hilo.is_alive():
        raise _Timeout()
    if "err" in caja:
        raise caja["err"]
    return caja["ok"]


def _sanear_metadatos(meta: dict) -> dict:
    limpio = {k: v for k, v in (meta or {}).items() if k not in _META_EXCLUIDOS}
    # "campo=valor" contiene el dato extraido: se conserva solo el nombre del campo
    if "campos_descartados_por_alucinacion" in limpio:
        limpio["campos_descartados_por_alucinacion"] = [
            str(c).split("=", 1)[0] for c in limpio["campos_descartados_por_alucinacion"]
        ]
    return limpio


class EvaluationService:
    def __init__(
        self,
        pipeline: PipelinePort,
        run_id: str,
        algorithm: AlgorithmInfo,
        base_dir: Path,
        max_size_mb: float = 10,
        timeout_s: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._pipeline = pipeline
        self._run_id = run_id
        self._algorithm = algorithm
        self._base_dir = Path(base_dir)
        self._max_mb = max_size_mb
        self._timeout_s = timeout_s
        self._clock = clock
        self._model_load_pendiente = True

    def evaluate(self, req: EvaluationRequest) -> EvaluationResult:
        t0 = self._clock()
        timings = EvaluationTimings()
        try:
            if not isinstance(req.sample_id, str) or not SAMPLE_ID_RE.match(req.sample_id):
                raise EvaluationRejected(
                    "ID_INVALIDO", "sample_id invalido (solo letras, numeros, - y _, max 64)")
            pdf = resolver_pdf(req.pdf_path, self._base_dir, self._max_mb)
            self._cargar_modelo_una_vez(timings)
            envelope = _ejecutar_con_timeout(
                lambda: self._pipeline.process(pdf, user_id=EVAL_USER_ID, doc_id=req.sample_id),
                self._timeout_s,
            )
            return self._ok(req, envelope, timings, t0)
        except EvaluationRejected as e:
            return self._error(req, e.code, e.message, e.retryable, timings, t0)
        except PdfValidationError as e:
            return self._error(req, "PDF_INVALIDO", str(e), False, timings, t0)
        except DocumentoNoSoportadoError as e:
            return self._error(req, "DOCUMENTO_NO_SOPORTADO", e.mensaje, False, timings, t0)
        except _Timeout:
            return self._error(req, "TIMEOUT",
                               f"Excedio --timeout-s={self._timeout_s}s (el hilo no es cancelable)",
                               True, timings, t0)
        except RuntimeError as e:
            return self._error(req, "OCR_NO_DISPONIBLE", str(e)[:300], False, timings, t0)
        except Exception as e:  # noqa: BLE001
            # Solo el tipo: el mensaje podria contener texto extraido (PII)
            return self._error(req, "ERROR_INTERNO",
                               f"Error interno al procesar ({type(e).__name__})", False, timings, t0)

    def rechazar(self, req: EvaluationRequest, code: str, message: str) -> EvaluationResult:
        """Resultado de error controlado para entradas que ni llegan a evaluate()."""
        return self._error(req, code, message, False, EvaluationTimings(), self._clock())

    # ── internos ──────────────────────────────────────────────────────────────

    def _cargar_modelo_una_vez(self, timings: EvaluationTimings) -> None:
        if not self._model_load_pendiente:
            return
        self._model_load_pendiente = False
        precargar = getattr(self._pipeline, "precargar", None)
        if precargar is None:
            return
        t = self._clock()
        precargar()
        timings.model_load_ms = round((self._clock() - t) * 1000, 1)

    def _ok(self, req, envelope, timings, t0) -> EvaluationResult:
        meta = _sanear_metadatos(envelope.get("metadatos"))
        tipo = envelope.get("tipo_documento")
        if tipo not in TIPOS_VALIDOS:
            return self._error(req, "ERROR_INTERNO", "tipo_documento fuera del contrato",
                               False, timings, t0)
        timings.stages_ms = dict(meta.get("tiempos_etapas_ms") or {})
        timings.total_ms = round((self._clock() - t0) * 1000, 1)
        datos = {k: (None if v is None else str(v)) for k, v in (envelope.get("datos") or {}).items()}
        return EvaluationResult(
            sample_id=req.sample_id, run_id=self._run_id, status="ok", timings=timings,
            algorithm=self._algorithm, tipo_documento=tipo, datos=datos, metadatos=meta)

    def _error(self, req, code, message, retryable, timings, t0) -> EvaluationResult:
        timings.total_ms = round((self._clock() - t0) * 1000, 1)
        sid = req.sample_id if isinstance(req.sample_id, str) else str(req.sample_id)
        return EvaluationResult(
            sample_id=sid, run_id=self._run_id, status="error", timings=timings,
            algorithm=self._algorithm, error=EvaluationError(code, message, retryable))
