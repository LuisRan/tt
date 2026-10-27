"""Fixtures compartidas.

Por defecto los tests usan los layouts de FIXTURE de `contracts/fixtures/`
(bboxes en rejilla). Para re-ejecutarlos contra los layouts reales:

    DATA_INJECTION_LAYOUT_DIR=src/layouts/specs python -m pytest src/data_injection -q

(el directorio debe contener `<doc_type>.json`).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import pytest

from contracts import VARIANTS, DocType, LayoutSpec

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "contracts" / "fixtures"

DOC_VARIANTS = [(dt, v) for dt in DocType for v in VARIANTS[dt]]
DOC_VARIANT_IDS = [f"{dt.value}-{v.value}" for dt, v in DOC_VARIANTS]


@lru_cache(maxsize=None)
def load_layout(doc_type: DocType) -> LayoutSpec:
    custom = os.environ.get("DATA_INJECTION_LAYOUT_DIR")
    if custom:
        path = Path(custom)
        if not path.is_absolute():
            path = REPO_ROOT / path
        path = path / f"{doc_type.value}.json"
    else:
        path = FIXTURES / f"layout_{doc_type.value}.json"
    return LayoutSpec.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.fixture(params=DOC_VARIANTS, ids=DOC_VARIANT_IDS)
def doc_variant(request):
    return request.param
