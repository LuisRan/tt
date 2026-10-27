"""Genera los artefactos derivados de los contratos:

- contracts/schemas/*.schema.json   (JSON Schema de cada contrato)
- contracts/fixtures/layout_<doc>.json  layouts de FIXTURE: bboxes en rejilla
  derivadas del catálogo. NO son la geometría real (eso lo entrega T1); sirven
  para que T2 y T3 trabajen en paralelo.
- contracts/fixtures/degradation_default.json
- contracts/fixtures/ground_truth_example.json

Uso: python -m contracts.build_artifacts
"""
from __future__ import annotations

import json
from pathlib import Path

from .common import BBox, DocType
from .degradation import DegradationConfig, Range, StepConfig, StepKind, TransformLog
from .field_catalog import CATALOG, FACE_SIZES_MM, default_variant
from .ground_truth import FieldGT, GroundTruth, PageGT
from .layout_spec import FaceSpec, FieldSpec, FieldType, FontFamily, FontSpec, LayoutSpec

ROOT = Path(__file__).parent
FIXTURE_LAYOUT_VERSION = "0.0.1"


def fixture_layout(doc_type: DocType) -> LayoutSpec:
    w, h = FACE_SIZES_MM[doc_type]
    faces = []
    for face, fields in CATALOG[doc_type].items():
        n = len(fields)
        margin = w * 0.04
        row_h = (h - 2 * margin) / n
        specs = []
        for i, cf in enumerate(fields):
            y0 = margin + i * row_h
            bbox = BBox(x0=margin, y0=y0, x1=w - margin, y1=y0 + row_h * 0.9)
            textual = cf.type in {FieldType.TEXT, FieldType.MULTILINE, FieldType.STATIC, FieldType.MRZ}
            font = None
            if textual:
                fam = FontFamily.OCRB if cf.type == FieldType.MRZ else FontFamily.SANS
                font = FontSpec(family=fam, size_pt=max(2.0, row_h * 0.9 * 2.0))
            static = cf.fmt if cf.type in {FieldType.STATIC, FieldType.SHAPE} else None
            lines = 3 if cf.type in {FieldType.MULTILINE, FieldType.MRZ} else 1
            specs.append(
                FieldSpec(
                    name=cf.name,
                    type=cf.type,
                    bbox=bbox,
                    font=font,
                    lines=lines,
                    static_value=static,
                    in_ground_truth=cf.in_gt,
                    variants=list(cf.variants) if cf.variants else None,
                )
            )
        faces.append(FaceSpec(face=face, width_mm=w, height_mm=h, fields=specs))
    return LayoutSpec(
        doc_type=doc_type,
        layout_version=FIXTURE_LAYOUT_VERSION,
        source_note="FIXTURE de rejilla generado desde field_catalog; no es geometría real",
        faces=faces,
    )


def default_degradation() -> DegradationConfig:
    R = Range
    return DegradationConfig(
        name="default",
        render_dpi=300,
        steps=[
            StepConfig(kind=StepKind.CANVAS_PLACEMENT, probability=0.0,
                       params={"margin_mm": R(min=5, max=30)}, options={"canvas": "letter"}),
            StepConfig(kind=StepKind.ILLUMINATION_SHADOW, probability=0.5,
                       params={"strength": R(min=0.1, max=0.45)}),
            StepConfig(kind=StepKind.CONTRAST_BRIGHTNESS, probability=0.8,
                       params={"contrast": R(min=0.6, max=1.4), "brightness": R(min=-40, max=40)}),
            StepConfig(kind=StepKind.SKEW, probability=0.5,
                       params={"shear": R(min=-0.03, max=0.03)}, options={"mode": "affine"}),
            StepConfig(kind=StepKind.ROTATION, probability=0.7,
                       params={"angle_deg": R(min=-3, max=3)}, options={"expand": True}),
            StepConfig(kind=StepKind.DPI_RESAMPLE, probability=0.8,
                       params={"target_dpi": R(min=100, max=300)}),
            StepConfig(kind=StepKind.BLUR, probability=0.3, params={"sigma": R(min=0.3, max=1.2)}),
            StepConfig(kind=StepKind.GAUSSIAN_NOISE, probability=0.7, params={"sigma": R(min=3, max=18)}),
            StepConfig(kind=StepKind.SALT_PEPPER, probability=0.4, params={"amount": R(min=0.001, max=0.01)}),
            StepConfig(kind=StepKind.JPEG, probability=0.9, params={"quality": R(min=10, max=60)}),
        ],
    )


def example_ground_truth() -> GroundTruth:
    layout = fixture_layout(DocType.INE_2016)
    variant = default_variant(DocType.INE_2016)
    pages = []
    for idx, face in enumerate(layout.faces):
        fields = [
            FieldGT(name=f.name, type=f.type, value=f.static_value or "EJEMPLO", bbox_mm=f.bbox)
            for f in face.fields_for(variant)
            if f.in_ground_truth
        ]
        pages.append(PageGT(page_index=idx, face=face.face, width_mm=face.width_mm,
                            height_mm=face.height_mm, fields=fields))
    return GroundTruth(
        sample_id="ine_2016-nacional-42-000000",
        doc_type=DocType.INE_2016,
        variant=variant,
        layout_version=layout.layout_version,
        seed=42,
        generator_version="0.0.0",
        pages=pages,
    )


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main() -> None:
    schemas = {
        "layout_spec": LayoutSpec,
        "ground_truth": GroundTruth,
        "degradation_config": DegradationConfig,
        "transform_log": TransformLog,
    }
    for name, model in schemas.items():
        _dump(ROOT / "schemas" / f"{name}.schema.json", model.model_json_schema())
    for dt in DocType:
        _dump(ROOT / "fixtures" / f"layout_{dt.value}.json", fixture_layout(dt).model_dump(mode="json"))
    _dump(ROOT / "fixtures" / "degradation_default.json", default_degradation().model_dump(mode="json"))
    _dump(ROOT / "fixtures" / "ground_truth_example.json", example_ground_truth().model_dump(mode="json"))


if __name__ == "__main__":
    main()
