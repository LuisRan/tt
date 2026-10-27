"""Contrato a) layout_spec: geometría de cada documento y cara.

Lo PRODUCE T1 (src/layouts) como JSON en `src/layouts/specs/<doc_type>.json`.
Lo CONSUME T2 (para saber dónde y cómo inyectar) y T4.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import Field, model_validator

from .common import BBox, DocType, Face, StrictModel, Unit, Variant


class FieldType(str, Enum):
    TEXT = "text"            # texto variable de una línea
    MULTILINE = "multiline"  # texto variable multilínea (lines = n máx.)
    PHOTO = "photo"          # avatar genérico de alto contraste (NUNCA foto real)
    QR = "qr"
    BARCODE = "barcode"      # Code128 por defecto
    MRZ = "mrz"              # zona de lectura mecánica (ICAO 9303)
    SIGNATURE = "signature"  # garabato sintético generado
    STATIC = "static"        # texto fijo de la plantilla (títulos, etiquetas)
    SHAPE = "shape"          # figura geométrica sustituta de escudos/logos


class Align(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class FontFamily(str, Enum):
    """Familias LÓGICAS; T1 las mapea a fuentes libres (ver PLAN.md)."""

    SANS = "sans"
    SANS_BOLD = "sans_bold"
    SERIF = "serif"
    SERIF_BOLD = "serif_bold"
    MONO = "mono"
    OCRB = "ocrb"  # MRZ; fallback a MONO si no hay OCR-B con licencia libre


class FontSpec(StrictModel):
    family: FontFamily = FontFamily.SANS
    size_pt: float = Field(gt=0)
    color_rgb: tuple[int, int, int] = (0, 0, 0)
    uppercase: bool = False


class FieldSpec(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: FieldType
    bbox: BBox
    font: Optional[FontSpec] = None
    align: Align = Align.LEFT
    lines: int = Field(default=1, ge=1)
    max_chars: Optional[int] = Field(default=None, ge=1)
    # Para STATIC: texto fijo. Para SHAPE: descripción de la figura sustituta.
    static_value: Optional[str] = None
    # Campos con label impreso encima/al lado (p.ej. "FECHA DE NACIMIENTO").
    label: Optional[str] = None
    label_bbox: Optional[BBox] = None
    # False => el campo NO entra al Ground Truth (decoración pura).
    in_ground_truth: bool = True
    # None => se dibuja en todas las variantes; si no, solo en las listadas.
    variants: Optional[list[Variant]] = None

    @model_validator(mode="after")
    def _check(self) -> "FieldSpec":
        textual = {FieldType.TEXT, FieldType.MULTILINE, FieldType.STATIC, FieldType.MRZ}
        if self.type in textual and self.font is None:
            raise ValueError(f"campo {self.name}: tipo {self.type} requiere font")
        if self.type in {FieldType.STATIC, FieldType.SHAPE} and self.static_value is None:
            raise ValueError(f"campo {self.name}: {self.type} requiere static_value")
        return self


class FaceSpec(StrictModel):
    face: Face
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    background_rgb: tuple[int, int, int] = (255, 255, 255)
    fields: list[FieldSpec]

    @model_validator(mode="after")
    def _check(self) -> "FaceSpec":
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError(f"nombres de campo duplicados en {self.face}")
        for f in self.fields:
            b = f.bbox
            if b.x0 < 0 or b.y0 < 0 or b.x1 > self.width_mm or b.y1 > self.height_mm:
                raise ValueError(f"campo {f.name} fuera de la cara {self.face}")
        return self

    def field(self, name: str) -> FieldSpec:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    def fields_for(self, variant: Variant) -> list[FieldSpec]:
        return [f for f in self.fields if f.variants is None or variant in f.variants]


class LayoutSpec(StrictModel):
    doc_type: DocType
    layout_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    unit: Unit = Unit.MM  # fijo; cualquier otro valor es error
    origin: str = "top-left"  # fijo
    source_note: str = ""  # de qué referencia de formatos/ se midió (sin copiar imágenes)
    faces: list[FaceSpec]

    @model_validator(mode="after")
    def _check(self) -> "LayoutSpec":
        if self.unit != Unit.MM or self.origin != "top-left":
            raise ValueError("layout_spec debe usar mm y origen top-left")
        return self

    def face(self, face: Face) -> FaceSpec:
        for f in self.faces:
            if f.face == face:
                return f
        raise KeyError(face)
