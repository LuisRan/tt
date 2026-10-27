"""T3 — Degradación (chaos testing) de páginas renderizadas + proyección del GT.

API pública:
    rasterize_pdf(pdf_bytes, dpi) -> list[np.ndarray]
    degrade_page(image, cfg, rng, *, page_index, face, face_size_mm, render_dpi)
        -> (np.ndarray, PageTransformLog)
    degrade_pdf(clean_pdf, cfg, *, sample_id, degradation_seed, faces) -> (bytes, TransformLog)
    project_ground_truth(gt, log) -> GroundTruth
    load_config(path) -> DegradationConfig ; load_preset(name) -> DegradationConfig
"""
from .config import PRESET_NAMES, PRESETS_DIR, load_config, load_preset
from .core import (
    degrade_page,
    degrade_pdf,
    degrade_pdf_with_images,
    effective_dpi,
    page_rng,
    project_ground_truth,
    rasterize_pdf,
)

__all__ = [
    "rasterize_pdf",
    "degrade_page",
    "degrade_pdf",
    "degrade_pdf_with_images",
    "project_ground_truth",
    "effective_dpi",
    "page_rng",
    "load_config",
    "load_preset",
    "PRESET_NAMES",
    "PRESETS_DIR",
]
