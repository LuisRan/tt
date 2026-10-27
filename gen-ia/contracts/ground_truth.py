"""Contrato b) ground_truth.schema.

Lo PRODUCE T2 (valores + bbox original en mm). T3 provee
`project_ground_truth(gt, transform_log)` que rellena `transformed` en cada
campo. T4 escribe el JSON final junto al PDF degradado.
"""
from __future__ import annotations

from typing import Optional, Union

from pydantic import Field

from .common import (
    CONTRACTS_VERSION,
    WATERMARK_TEXT,
    BBox,
    DocType,
    Face,
    Quad,
    StrictModel,
    Variant,
)
from .layout_spec import FieldType


class TransformedGeometry(StrictModel):
    """Posición del campo en la imagen FINAL degradada (px), tras aplicar
    la matriz compuesta del transform_log a las 4 esquinas del bbox original."""

    quad_px: Quad
    bbox_px: BBox  # envolvente alineada a ejes del quad
    image_width_px: int
    image_height_px: int


class FieldGT(StrictModel):
    name: str
    type: FieldType
    # TEXT/STATIC: str. MULTILINE/MRZ: list[str] (una por línea).
    # QR/BARCODE: payload codificado (str). PHOTO/SIGNATURE: id del asset sintético.
    value: Union[str, list[str], None]
    bbox_mm: BBox  # bbox ORIGINAL en mm (origen top-left de la cara)
    transformed: Optional[TransformedGeometry] = None


class PageGT(StrictModel):
    page_index: int = Field(ge=0)  # página en el PDF de salida
    face: Face
    width_mm: float
    height_mm: float
    fields: list[FieldGT]


class SyntheticMarker(StrictModel):
    is_synthetic: bool = True
    watermark_text: str = WATERMARK_TEXT
    faker_locale: str = "es_MX"


class GroundTruth(StrictModel):
    schema_version: str = CONTRACTS_VERSION
    sample_id: str  # determinista: f"{doc_type}-{variant}-{seed}-{index:06d}"
    doc_type: DocType
    variant: Variant = Variant.DEFAULT
    layout_version: str
    seed: int  # seed de datos (Faker + RNG)
    degradation_seed: Optional[int] = None
    generator_version: str
    synthetic: SyntheticMarker = SyntheticMarker()
    pages: list[PageGT]
    # Referencias relativas a archivos hermanos en la carpeta de salida.
    clean_pdf: Optional[str] = None
    degraded_pdf: Optional[str] = None
    transform_log: Optional[str] = None
