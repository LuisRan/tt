"""Construcción del Ground Truth (contrato `contracts.GroundTruth`) a partir de
un LayoutSpec y un registro generado."""
from __future__ import annotations

from contracts import DocType, Face, FieldGT, GroundTruth, LayoutSpec, PageGT, Variant
from contracts.layout_spec import FieldType

from .generators import FAKER_LOCALE, Record, generate_record, resolve_variant

GENERATOR_VERSION = "0.1.0"
_LIST_TYPES = {FieldType.MULTILINE, FieldType.MRZ}


class MissingValueError(KeyError):
    """El layout pide un campo para el que el registro no tiene valor."""

    def __str__(self) -> str:  # KeyError por defecto muestra repr
        return str(self.args[0])


def make_sample_id(doc_type: DocType, variant: Variant, seed: int, index: int) -> str:
    return f"{doc_type.value}-{variant.value}-{int(seed)}-{int(index):06d}"


def _coerce(name: str, ftype: FieldType, value):
    if ftype in _LIST_TYPES:
        if isinstance(value, str):
            raise TypeError(f"campo {name} ({ftype.value}) requiere list[str], recibió str")
        return [str(x) for x in value]
    if not isinstance(value, str):
        raise TypeError(f"campo {name} ({ftype.value}) requiere str, recibió {type(value).__name__}")
    return value


def build_ground_truth(layout: LayoutSpec, record: Record, *, variant: Variant | str | None,
                       seed: int, index: int, generator_version: str = GENERATOR_VERSION) -> GroundTruth:
    """Una PageGT por cara (orden del layout) y un FieldGT por campo del layout con
    in_ground_truth=True visible en la variante. STATIC/SHAPE toman `static_value`.
    `transformed` queda en None (lo llena el módulo de degradación)."""
    doc_type = DocType(layout.doc_type)
    v = resolve_variant(doc_type, variant)
    pages: list[PageGT] = []
    for page_index, face_spec in enumerate(layout.faces):
        face_values = record.get(face_spec.face)
        if face_values is None:
            face_values = record.get(face_spec.face.value)  # tolera claves str
        fields: list[FieldGT] = []
        for fs in face_spec.fields_for(v):
            if not fs.in_ground_truth:
                continue
            if fs.type in (FieldType.STATIC, FieldType.SHAPE):
                value = fs.static_value
            else:
                if face_values is None or fs.name not in face_values:
                    raise MissingValueError(
                        f"falta valor para {doc_type.value}/{face_spec.face.value}/{fs.name} "
                        f"(variante {v.value}); el registro no corresponde al layout"
                    )
                value = _coerce(fs.name, fs.type, face_values[fs.name])
            fields.append(FieldGT(name=fs.name, type=fs.type, value=value, bbox_mm=fs.bbox))
        pages.append(PageGT(page_index=page_index, face=face_spec.face, width_mm=face_spec.width_mm,
                            height_mm=face_spec.height_mm, fields=fields))
    return GroundTruth(
        sample_id=make_sample_id(doc_type, v, seed, index),
        doc_type=doc_type,
        variant=v,
        layout_version=layout.layout_version,
        seed=int(seed),
        generator_version=generator_version,
        synthetic={"is_synthetic": True, "faker_locale": FAKER_LOCALE},
        pages=pages,
    )


def inject(layout: LayoutSpec, seed: int, index: int = 0,
           variant: Variant | str | None = None) -> tuple[Record, GroundTruth]:
    """Atajo: genera el registro para `layout.doc_type` y su Ground Truth."""
    doc_type = DocType(layout.doc_type)
    v = resolve_variant(doc_type, variant)
    record = generate_record(doc_type, seed, index, v)
    return record, build_ground_truth(layout, record, variant=v, seed=seed, index=index)
