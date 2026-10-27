"""Contrato c) degradation_config + transform_log.

degradation_config: lo que se PIDE (rangos y probabilidades). Lo consume T3.
transform_log: lo que se APLICÓ (valores concretos + matriz de cada paso
geométrico). Lo produce T3 por cada página.

Matrices: 3x3 homogéneas que mapean coordenadas del espacio de ENTRADA del paso
al espacio de SALIDA. El primer paso siempre es `render` (mm -> px a dpi de
render). `composed` = M_n · ... · M_1 mapea mm de la cara -> px de la imagen
final. Pasos fotométricos (ruido, jpeg, contraste...) tienen matrix = None.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import Field

from .common import IDENTITY, Face, Matrix3x3, StrictModel


class Range(StrictModel):
    min: float
    max: float


class StepKind(str, Enum):
    # geométricos (registran matriz)
    RENDER = "render"                  # mm -> px al dpi de render
    DPI_RESAMPLE = "dpi_resample"      # baja/sube resolución (escala)
    CANVAS_PLACEMENT = "canvas_placement"  # coloca la cara en una "cama de escáner"
    ROTATION = "rotation"
    SKEW = "skew"                      # shear afín o perspectiva leve (homografía)
    # fotométricos (sin matriz)
    JPEG = "jpeg"
    GAUSSIAN_NOISE = "gaussian_noise"
    SALT_PEPPER = "salt_pepper"
    CONTRAST_BRIGHTNESS = "contrast_brightness"
    ILLUMINATION_SHADOW = "illumination_shadow"
    BLUR = "blur"


GEOMETRIC_KINDS = {
    StepKind.RENDER,
    StepKind.DPI_RESAMPLE,
    StepKind.CANVAS_PLACEMENT,
    StepKind.ROTATION,
    StepKind.SKEW,
}


class StepConfig(StrictModel):
    kind: StepKind
    probability: float = Field(default=1.0, ge=0, le=1)
    # Rangos por parámetro; T3 muestrea uniforme con su RNG sembrado.
    # Ejemplos: rotation {"angle_deg": {min:-3,max:3}}, jpeg {"quality": {min:15,max:60}}
    params: dict[str, Range] = Field(default_factory=dict)
    # Opciones no numéricas (p.ej. {"expand": true, "mode": "perspective"}).
    options: dict[str, Any] = Field(default_factory=dict)


class DegradationConfig(StrictModel):
    name: str = "default"
    render_dpi: int = Field(default=300, ge=72, le=1200)
    # Orden de ejecución = orden de la lista. `render` es implícito y va primero.
    steps: list[StepConfig]
    output_format: str = Field(default="pdf", pattern="^pdf$")
    output_jpeg_quality: int = Field(default=85, ge=1, le=100)  # al empaquetar en PDF


class AppliedStep(StrictModel):
    kind: StepKind
    applied: bool  # False si la probabilidad no lo activó
    params: dict[str, Any] = Field(default_factory=dict)  # valores concretos muestreados
    matrix: Optional[Matrix3x3] = None  # obligatorio si kind geométrico y applied
    input_size_px: tuple[int, int]   # (w, h)
    output_size_px: tuple[int, int]


class PageTransformLog(StrictModel):
    page_index: int
    face: Face
    face_size_mm: tuple[float, float]
    steps: list[AppliedStep]
    composed: Matrix3x3 = Field(default_factory=lambda: [r[:] for r in IDENTITY])
    final_size_px: tuple[int, int]


class TransformLog(StrictModel):
    sample_id: str
    degradation_seed: int
    config_name: str
    config: DegradationConfig  # copia íntegra para reproducibilidad
    pages: list[PageTransformLog]
