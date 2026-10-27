"""Carga de configuraciones y presets."""
from __future__ import annotations

import json
from pathlib import Path

from contracts.degradation import DegradationConfig

PRESETS_DIR = Path(__file__).parent / "presets"
PRESET_NAMES = ("none", "light", "default", "heavy", "scanner")


def load_config(path: str | Path) -> DegradationConfig:
    """Carga y valida un DegradationConfig desde un archivo JSON."""
    return DegradationConfig.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def load_preset(name: str) -> DegradationConfig:
    """Carga un preset por nombre: none | light | default | heavy | scanner."""
    p = PRESETS_DIR / f"{name}.json"
    if not p.is_file():
        raise ValueError(f"preset desconocido {name!r}; disponibles: {', '.join(PRESET_NAMES)}")
    return load_config(p)
