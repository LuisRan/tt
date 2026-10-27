"""Reparto dev/val/test por `group_id` (determinista, sin fuga entre conjuntos)."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

from contracts.evaluation import SampleRef, SplitConfig

SPLIT_NAMES = ("dev", "val", "test")


class SplitLeakageError(Exception):
    """Un mismo group_id aparece en más de un conjunto."""


def _unit(seed: int, group_id: str) -> float:
    """sha256(seed, group_id) -> número uniforme en [0, 1)."""
    digest = hashlib.sha256(f"split:{int(seed)}:{group_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def split_for_group(seed: int, group_id: str, cfg: SplitConfig | None = None) -> str:
    """Conjunto ('dev'|'val'|'test') de un grupo. Depende solo de (seed, group_id, proporciones).
    Las proporciones se normalizan; el reparto es estadístico (hash), no exacto."""
    cfg = cfg or SplitConfig()
    parts = [cfg.dev, cfg.val, cfg.test]
    if any(p < 0 for p in parts) or sum(parts) <= 0:
        raise ValueError("las proporciones de split deben ser >= 0 y sumar > 0")
    total = sum(parts)
    u = _unit(seed, group_id)
    if u < cfg.dev / total:
        return "dev"
    if u < (cfg.dev + cfg.val) / total:
        return "val"
    return "test"


def assign_splits(seed: int, group_ids: Iterable[str], cfg: SplitConfig | None = None) -> dict[str, str]:
    return {g: split_for_group(seed, g, cfg) for g in dict.fromkeys(group_ids)}


def verify_no_leakage(refs: Iterable[SampleRef]) -> None:
    """Lanza SplitLeakageError si algún group_id aparece en más de un split."""
    seen: dict[str, str] = {}
    for r in refs:
        prev = seen.setdefault(r.group_id, r.split)
        if prev != r.split:
            raise SplitLeakageError(f"group_id {r.group_id!r} cruza conjuntos: {prev} y {r.split}")
