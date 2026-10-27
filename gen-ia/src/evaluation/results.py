"""Modelos de resultado de la evaluación supervisada (puros, sin E/S)."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import Field

from contracts.common import DocType, StrictModel


class FieldStatus(str, Enum):
    EXACT = "EXACT"                      # idéntico al GT (cadena cruda)
    NORMALIZED = "NORMALIZED"            # igual tras normalización permitida del campo
    PARTIAL = "PARTIAL"                  # igual solo sin acentos / orden alterno (NO es correcto)
    MISMATCH = "MISMATCH"                # valor distinto
    MISSING = "MISSING"                  # esperado, el OCR devolvió null/vacío/ausente
    EXTRA = "EXTRA"                      # clave fuera del esquema del OCR (o valor donde se espera vacío)
    EXTRA_SCHEMA = "EXTRA_SCHEMA"        # clave del esquema del OCR sin equivalente en el GT (NO es error)
    UNINTERPRETABLE = "UNINTERPRETABLE"  # no se pudo interpretar (fecha/sexo ilegible)
    NOT_APPLICABLE = "NOT_APPLICABLE"    # sin equivalente verificable en el GT


CORRECT_STATUSES = frozenset({FieldStatus.EXACT, FieldStatus.NORMALIZED})
ERROR_STATUSES = frozenset(
    {FieldStatus.MISMATCH, FieldStatus.MISSING, FieldStatus.UNINTERPRETABLE, FieldStatus.EXTRA}
)


class FieldResult(StrictModel):
    name: str
    status: FieldStatus
    expected: Optional[str] = None
    actual: Optional[str] = None
    cer: Optional[float] = None
    wer: Optional[float] = None
    criticality: int = 1          # 3 = identificador crítico
    scored: bool = True           # False => no entra en denominadores de exactitud
    note: Optional[str] = None


class SampleResult(StrictModel):
    sample_id: str
    group_id: str
    split: str
    doc_type: DocType
    degradation_preset: str
    run_id: str
    failed: bool = False
    error_code: Optional[str] = None
    predicted_type: Optional[str] = None
    type_correct: Optional[bool] = None   # None si falló
    fields: list[FieldResult] = Field(default_factory=list)
    total_ms: Optional[float] = None
    stages_ms: dict[str, float] = Field(default_factory=dict)
    model_load_ms: Optional[float] = None


class ClassMetrics(StrictModel):
    label: str
    support: int
    tp: int
    fp: int
    fn: int
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1: Optional[float] = None


class ConfusionMatrix(StrictModel):
    """Filas = verdad, columnas = predicción; la última columna es la de "sin clasificar"."""

    labels: list[str]
    columns: list[str]
    matrix: list[list[int]]


class ClassificationSummary(StrictModel):
    n: int
    accuracy: Optional[float] = None
    macro_f1: Optional[float] = None
    unclassified_pct: Optional[float] = None
    cross_category_errors: int = 0
    per_class: list[ClassMetrics] = Field(default_factory=list)
    confusion: ConfusionMatrix = Field(
        default_factory=lambda: ConfusionMatrix(labels=[], columns=[], matrix=[])
    )


class GroupSummary(StrictModel):
    key: str
    n_samples: int
    n_failed: int
    n_scored_fields: int
    n_correct: int
    n_exact: int
    n_partial: int
    n_extra_schema: int = 0                # claves legítimas del esquema OCR sin GT (no son error)
    accuracy: Optional[float] = None       # (EXACT+NORMALIZED)/scored
    exact_rate: Optional[float] = None
    mean_cer: Optional[float] = None
    type_accuracy: Optional[float] = None


class FieldAggregate(StrictModel):
    field: str
    doc_type: Optional[str] = None          # None = todos los tipos
    criticality: int = 1
    critical: bool = False
    n_scored: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    accuracy: Optional[float] = None
    exact_rate: Optional[float] = None
    partial_rate: Optional[float] = None
    n_errors: int = 0
    n_extra_schema: int = 0
    mean_cer: Optional[float] = None
    mean_wer: Optional[float] = None


class PerfAggregate(StrictModel):
    n_samples: int = 0
    n_timed: int = 0
    error_rate: Optional[float] = None
    latency_mean_ms: Optional[float] = None
    latency_mean_ms_excl_model_load: Optional[float] = None
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    latency_p99_ms: Optional[float] = None
    docs_per_s: Optional[float] = None
    throughput_basis: Optional[str] = None
    model_load_ms: Optional[float] = None
    stages_mean_ms: dict[str, float] = Field(default_factory=dict)


class FailedDocument(StrictModel):
    sample_id: str
    doc_type: DocType
    degradation_preset: str
    split: str
    error_code: Optional[str] = None


class ErrorPattern(StrictModel):
    field: str
    doc_type: str
    status: str
    count: int


class Recommendation(StrictModel):
    priority: int
    scope: str                  # campo | fallos | clasificacion
    target: str
    impact: float
    n_errors: int
    message: str


class CampaignReport(StrictModel):
    run_info: dict[str, Any] = Field(default_factory=dict)
    generated_at: Optional[str] = None
    n_samples: int = 0
    n_failed: int = 0
    overall: Optional[GroupSummary] = None
    by_type: list[GroupSummary] = Field(default_factory=list)
    by_split: list[GroupSummary] = Field(default_factory=list)
    by_preset: list[GroupSummary] = Field(default_factory=list)
    by_type_preset: list[GroupSummary] = Field(default_factory=list)
    fields: list[FieldAggregate] = Field(default_factory=list)
    fields_by_type: list[FieldAggregate] = Field(default_factory=list)
    classification: Optional[ClassificationSummary] = None
    performance: PerfAggregate = Field(default_factory=PerfAggregate)
    failed_documents: list[FailedDocument] = Field(default_factory=list)
    top_errors: list[ErrorPattern] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    samples: list[SampleResult] = Field(default_factory=list)


class MetricDelta(StrictModel):
    scope: str          # global | tipo | campo_critico | rendimiento
    name: str
    baseline: Optional[float] = None
    candidate: Optional[float] = None
    delta: Optional[float] = None
    higher_is_better: bool = True
    verdict: str = "sin_dato"   # mejora | regresion | igual | sin_dato


class Comparison(StrictModel):
    baseline_run: Optional[str] = None
    candidate_run: Optional[str] = None
    comparable: bool = True
    notes: list[str] = Field(default_factory=list)
    quality_tolerance: float = 0.0
    perf_tolerance: float = 0.0
    deltas: list[MetricDelta] = Field(default_factory=list)
    regressions: list[MetricDelta] = Field(default_factory=list)
    improvements: list[MetricDelta] = Field(default_factory=list)
    global_improved: bool = False
    regression_despite_global_gain: bool = False
