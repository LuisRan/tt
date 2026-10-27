"""Contratos entre módulos. Cambiarlos requiere aprobación del orquestador."""
from .common import (
    CONTRACTS_VERSION,
    WATERMARK_TEXT,
    BBox,
    DocType,
    Face,
    Quad,
    Variant,
    apply_matrix,
    matmul,
    project_bbox,
)
from .degradation import (
    AppliedStep,
    DegradationConfig,
    PageTransformLog,
    StepConfig,
    StepKind,
    TransformLog,
)
from .field_catalog import CATALOG, FACE_SIZES_MM, VARIANTS, default_variant, gt_field_names
from .ground_truth import FieldGT, GroundTruth, PageGT, TransformedGeometry
from .layout_spec import FaceSpec, FieldSpec, FieldType, FontSpec, LayoutSpec

__all__ = [n for n in dir() if not n.startswith("_")]
