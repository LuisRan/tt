"""Tipos compartidos por todos los contratos.

Convención de coordenadas (FIJA para todo el proyecto):
- Unidad del layout: milímetros (mm), float.
- Origen: esquina SUPERIOR IZQUIERDA de la cara/página; x crece a la derecha,
  y crece hacia ABAJO.
- BBox = (x0, y0, x1, y1) con x0 < x1, y0 < y1.
- Coordenadas en píxeles (tras rasterizar/degradar) usan el mismo origen y
  orientación; la conversión mm -> px es una matriz de escala dpi/25.4 que
  queda registrada en el transform_log.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACTS_VERSION = "1.0.0"
WATERMARK_TEXT = "MOCK DATA"
MM_PER_INCH = 25.4


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class DocType(str, Enum):
    INE_2016 = "ine_2016"
    CURP_2019 = "curp_2019"
    ACTA_NACIMIENTO_2015 = "acta_nacimiento_2015"
    PASAPORTE_ELECTRONICO_2021 = "pasaporte_electronico_2021"


class Variant(str, Enum):
    """Variante de un documento. Solo INE tiene más de una."""

    DEFAULT = "default"
    NACIONAL = "nacional"      # INE: domicilio en México (foco principal)
    EXTRANJERO = "extranjero"  # INE: votar desde el extranjero, domicilio en el exterior


class Face(str, Enum):
    ANVERSO = "anverso"
    REVERSO = "reverso"
    UNICA = "unica"  # documentos de una sola cara/página


class Unit(str, Enum):
    MM = "mm"
    PX = "px"


class BBox(StrictModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> "BBox":
        if not (self.x0 < self.x1 and self.y0 < self.y1):
            raise ValueError(f"bbox mal ordenada: {self}")
        return self

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def corners(self) -> list[tuple[float, float]]:
        """Esquinas en orden TL, TR, BR, BL."""
        return [(self.x0, self.y0), (self.x1, self.y0), (self.x1, self.y1), (self.x0, self.y1)]


Point = tuple[float, float]


class Quad(StrictModel):
    """Cuadrilátero (bbox transformada). Orden de puntos: TL, TR, BR, BL del bbox original."""

    points: Annotated[list[Point], Field(min_length=4, max_length=4)]

    def envelope(self) -> BBox:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


# Matriz homogénea 3x3, fila-mayor: [[a,b,c],[d,e,f],[g,h,i]].
# Se aplica a vectores columna (x, y, 1). Afín => g=h=0, i=1.
Matrix3x3 = Annotated[
    list[Annotated[list[float], Field(min_length=3, max_length=3)]],
    Field(min_length=3, max_length=3),
]

IDENTITY: list[list[float]] = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def apply_matrix(m: list[list[float]], pt: Point) -> Point:
    x, y = pt
    xh = m[0][0] * x + m[0][1] * y + m[0][2]
    yh = m[1][0] * x + m[1][1] * y + m[1][2]
    w = m[2][0] * x + m[2][1] * y + m[2][2]
    return (xh / w, yh / w)


def project_bbox(m: list[list[float]], bbox: BBox) -> Quad:
    return Quad(points=[apply_matrix(m, c) for c in bbox.corners()])
