"""T2 — Datos sintéticos y Ground Truth.

API pública:
    generate_record(doc_type, seed, index=0, variant=None) -> dict[Face, dict[str, value]]
    build_ground_truth(layout, record, *, variant, seed, index, generator_version="0.1.0") -> GroundTruth
    inject(layout, seed, index=0, variant=None) -> (record, GroundTruth)
    validators (módulo): CURP, clave de elector, MRZ ICAO 9303, fechas, validate_record
"""
from . import validators
from .generators import Record, generate_record, record_digest
from .ground_truth import GENERATOR_VERSION, MissingValueError, build_ground_truth, inject, make_sample_id
from .validators import validate_record

__all__ = [
    "GENERATOR_VERSION",
    "MissingValueError",
    "Record",
    "build_ground_truth",
    "generate_record",
    "inject",
    "make_sample_id",
    "record_digest",
    "validate_record",
    "validators",
]
