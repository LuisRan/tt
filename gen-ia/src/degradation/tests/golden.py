"""Captura y condiciones de uso de golden_presets.json.

El golden contiene hashes de bytes (PDF, imagenes) y de matrices en coma flotante.
Esos bytes dependen de la plataforma (libjpeg/OpenCV/pdfium, aritmetica de coma
flotante), asi que el golden esta ATADO al contenedor ``linux/amd64``.

Regenerar (desde la raiz del repo; ver docs/DOCKER.md, "Goldens y plataforma"):

    docker compose build
    docker compose run --rm -T --no-deps --entrypoint python tests \
        -m src.degradation.tests.golden > src/degradation/tests/golden_presets.json
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from importlib import metadata

from src.degradation import degrade_pdf_with_images, load_preset

from .helpers import INE_MM, MARKERS_INE, R_INE_MM, ine_faces, marker_pdf

GOLDEN_PLATFORM = "linux/amd64"
META_KEY = "_meta"
GOLDEN_NAMES = ["none", "light", "default", "heavy"]
GOLDEN_FORCED = [False, True]
GOLDEN_SEEDS = [1, 7]
PACKAGES = ["opencv-python-headless", "numpy", "pillow", "pypdfium2"]

SKIP_REASON = (
    "golden de bytes valido solo en el contenedor linux/amd64 (requiere OCR_DOCKER=1 y "
    "arquitectura x86_64): ejecuta `docker compose run --rm tests`"
)


def golden_applicable() -> bool:
    """True solo dentro del contenedor (OCR_DOCKER=1) y en arquitectura x86_64/AMD64."""
    return os.environ.get("OCR_DOCKER") == "1" and platform.machine().lower() in {"x86_64", "amd64"}


def ine_two_page_pdf() -> bytes:
    return marker_pdf([(INE_MM, MARKERS_INE, R_INE_MM), (INE_MM, MARKERS_INE[::-1][:5], R_INE_MM)])


def case_key(name: str, forced: bool, seed: int) -> str:
    return f"{name}|{forced}|{seed}"


def capture_case(pdf0: bytes, name: str, forced: bool, seed: int) -> dict:
    """Hashes de geometria, imagenes y PDF para un preset (forced: probabilidad 1 en todos los pasos)."""
    cfg = load_preset(name)
    if forced:
        cfg = cfg.model_copy(update={"steps": [s.model_copy(update={"probability": 1.0}) for s in cfg.steps]})
    pdf, log, imgs = degrade_pdf_with_images(pdf0, cfg, sample_id="x", degradation_seed=seed, faces=ine_faces())
    geo = json.dumps([[p.composed for p in log.pages],
                      [[(s.kind.value, s.applied, s.matrix, list(s.output_size_px)) for s in p.steps]
                       for p in log.pages]], sort_keys=True).encode()
    return {
        "geometry": hashlib.sha256(geo).hexdigest(),
        "imgs": [hashlib.sha256(i.tobytes()).hexdigest() for i in imgs],
        "pdf": hashlib.sha256(pdf).hexdigest(),
    }


def build_golden() -> dict:
    pdf0 = ine_two_page_pdf()
    out = {
        META_KEY: {
            "platform": GOLDEN_PLATFORM,
            "machine": platform.machine(),
            "versions": {p: metadata.version(p) for p in PACKAGES},
            "regenerate": "docker compose run --rm -T --no-deps --entrypoint python tests "
                          "-m src.degradation.tests.golden > src/degradation/tests/golden_presets.json",
        }
    }
    for name in GOLDEN_NAMES:
        for forced in GOLDEN_FORCED:
            for seed in GOLDEN_SEEDS:
                out[case_key(name, forced, seed)] = capture_case(pdf0, name, forced, seed)
    return out


def main() -> int:
    if not golden_applicable():
        print(f"error: {SKIP_REASON}", file=sys.stderr)
        return 2
    json.dump(build_golden(), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
