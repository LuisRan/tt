"""Contrato c) evaluación supervisada: predicción del OCR y configuración de campaña.

El generador (Servicio A) y el OCR (Servicio B) NO se conocen entre sí. Solo se
hablan por estos JSON, que produce/consume el orquestador (`src/evaluation`).

- El OCR, en modo evaluación, escribe un `OcrPrediction` por documento (JSONL).
- El orquestador lee el ground truth del generador (`contracts/ground_truth.py`),
  mapea nombres/formatos de campo y compara contra `OcrPrediction.datos`.
- La verdad de referencia NUNCA sale del OCR.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field

from .common import DocType, StrictModel

EVALUATION_SCHEMA_VERSION = "1.0.0"

# Valores de `tipo_documento` que devuelve el OCR (ver SegundaVersion 5.12)
OcrDocType = Literal["ine", "curp", "acta_nacimiento", "pasaporte"]

GEN_TO_OCR_DOCTYPE: dict[DocType, OcrDocType] = {
    DocType.INE_2016: "ine",
    DocType.CURP_2019: "curp",
    DocType.ACTA_NACIMIENTO_2015: "acta_nacimiento",
    DocType.PASAPORTE_ELECTRONICO_2021: "pasaporte",
}


class OcrError(StrictModel):
    """Error controlado del OCR (equivalente a los `codigo` HTTP del modo web)."""

    code: str  # p. ej. DOCUMENTO_NO_SOPORTADO, PDF_INVALIDO, OCR_NO_DISPONIBLE, TIMEOUT
    message: str
    retryable: bool = False


class OcrTimings(StrictModel):
    total_ms: float
    # etapas medidas por el pipeline (ingesta, ocr, extracción, validación...)
    stages_ms: dict[str, float] = Field(default_factory=dict)
    # solo se informa en la primera muestra de un proceso (carga perezosa de Docling)
    model_load_ms: Optional[float] = None


class AlgorithmInfo(StrictModel):
    """Qué algoritmo y configuración produjeron la predicción (reproducibilidad)."""

    pipeline_version: str
    llm_model: Optional[str] = None
    config_hash: str  # sha256 de la configuración efectiva relevante del OCR
    git_commit: Optional[str] = None
    platform: Optional[str] = None


class OcrPrediction(StrictModel):
    schema_version: str = EVALUATION_SCHEMA_VERSION
    sample_id: str  # = sample_id del ground truth; ÚNICA clave de unión
    run_id: Optional[str] = None  # campaña/ejecución que lo procesó
    status: Literal["ok", "error"]
    error: Optional[OcrError] = None
    tipo_documento: Optional[str] = None  # clasificación del OCR (None si falló)
    datos: dict[str, Optional[str]] = Field(default_factory=dict)  # campos extraídos
    metadatos: dict[str, Any] = Field(default_factory=dict)  # envelope del OCR, sin PII extra
    timings: Optional[OcrTimings] = None
    algorithm: Optional[AlgorithmInfo] = None


# --------------------------------------------------------------------------- campaña

class OcrClientConfig(StrictModel):
    kind: Literal["cli", "http"] = "cli"
    # kind=cli: ruta del intérprete y del runner de evaluación del OCR
    python: Optional[str] = None
    ocr_project_dir: Optional[str] = None
    # kind=http: URL base y API key (la key se lee de variable de entorno, no del JSON)
    base_url: Optional[str] = None
    api_key_env: str = "IA_API_KEY"
    timeout_s: float = 600.0
    retries: int = Field(default=1, ge=0, le=5)


class SplitConfig(StrictModel):
    """Reparto dev/val/test por GRUPO (identidad base), nunca por muestra suelta,
    para que variantes degradadas del mismo documento no crucen conjuntos."""

    dev: float = 0.6
    val: float = 0.2
    test: float = 0.2


class CampaignConfig(StrictModel):
    name: str
    seed: int
    # nº de documentos base por tipo
    per_doc_type: dict[DocType, int]
    variant: str = "mixed"
    # presets de degradación aplicados a CADA documento base; "none" = limpio.
    # Cada (base, preset) genera una muestra con el mismo group_id.
    degradation_presets: list[str] = Field(default_factory=lambda: ["none", "default", "heavy"])
    # INE: "front" = solo anverso (SegundaVersion 5.4.1); "all" = todas las caras
    ine_pages: Literal["front", "all"] = "front"
    split: SplitConfig = Field(default_factory=SplitConfig)
    ocr: OcrClientConfig = Field(default_factory=OcrClientConfig)
    out_dir: str = "out/campaigns"


class SampleRef(StrictModel):
    """Fila del manifiesto de campaña: enlaza documento, GT, grupo, split y ejecución."""

    sample_id: str
    group_id: str  # identidad base (doc_type, seed, index) compartida por sus variantes
    split: Literal["dev", "val", "test"]
    doc_type: DocType
    degradation_preset: str
    pdf_path: str
    ground_truth_path: str
    submitted_pdf_path: str  # PDF realmente enviado al OCR (p. ej. solo anverso)
    run_id: str
