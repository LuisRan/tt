"""Métricas puras: CER/WER, clasificación, percentiles y throughput (sin dependencias)."""
from __future__ import annotations

import math
from typing import Optional, Sequence

from .results import ClassificationSummary, ClassMetrics, ConfusionMatrix

NONE_LABEL = "(sin_clasificar)"


def levenshtein(a: Sequence, b: Sequence) -> int:
    """Distancia de edición (inserción/borrado/sustitución, costo 1)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    """Tasa de error por carácter. Referencia vacía: 0.0 si la hipótesis también, si no 1.0."""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return levenshtein(reference, hypothesis) / len(reference)


def wer(reference: str, hypothesis: str) -> float:
    """Tasa de error por palabra (separación por espacios)."""
    ref, hyp = reference.split(), hypothesis.split()
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(ref, hyp) / len(ref)


def _div(a: float, b: float) -> Optional[float]:
    return a / b if b else None


def classification_metrics(
    y_true: Sequence[str], y_pred: Sequence[Optional[str]]
) -> ClassificationSummary:
    """Exactitud, P/R/F1 por clase, matriz de confusión, % sin clasificar (pred None)
    y errores entre categorías (pred distinta de la verdad y no None)."""
    if len(y_true) != len(y_pred):
        raise ValueError("y_true y y_pred deben tener la misma longitud")
    n = len(y_true)
    labels = sorted(set(y_true) | {p for p in y_pred if p is not None})
    columns = labels + [NONE_LABEL]
    idx = {lab: i for i, lab in enumerate(labels)}
    matrix = [[0] * len(columns) for _ in labels]
    correct = unclassified = cross = 0
    for t, p in zip(y_true, y_pred):
        if p is None:
            unclassified += 1
            matrix[idx[t]][len(labels)] += 1
            continue
        matrix[idx[t]][idx[p]] += 1
        if p == t:
            correct += 1
        else:
            cross += 1
    per_class: list[ClassMetrics] = []
    for lab in labels:
        i = idx[lab]
        tp = matrix[i][i]
        fn = sum(matrix[i]) - tp
        fp = sum(matrix[r][i] for r in range(len(labels))) - tp
        prec, rec = _div(tp, tp + fp), _div(tp, tp + fn)
        f1 = _div(2 * prec * rec, prec + rec) if prec is not None and rec is not None and (prec + rec) else None
        per_class.append(ClassMetrics(label=lab, support=sum(matrix[i]), tp=tp, fp=fp, fn=fn,
                                      precision=prec, recall=rec, f1=f1))
    f1s = [c.f1 if c.f1 is not None else 0.0 for c in per_class if c.support > 0]
    return ClassificationSummary(
        n=n,
        accuracy=_div(correct, n),
        macro_f1=(sum(f1s) / len(f1s)) if f1s else None,
        unclassified_pct=(100.0 * unclassified / n) if n else None,
        cross_category_errors=cross,
        per_class=per_class,
        confusion=ConfusionMatrix(labels=labels, columns=columns, matrix=matrix),
    )


def percentile(values: Sequence[float], p: float) -> float:
    """Percentil por rango más cercano (p en 0..100). Requiere valores."""
    if not values:
        raise ValueError("sin valores")
    s = sorted(values)
    k = max(1, math.ceil(p / 100.0 * len(s)))
    return s[k - 1]


def min_samples_for(p: float) -> int:
    """n mínimo para que P{p} no sea simplemente el máximo: ceil(1/(1-p/100)); P50 => 2."""
    if p >= 100:
        return 10**9
    return max(2, math.ceil(1.0 / (1.0 - p / 100.0)))


def percentiles(
    values: Sequence[float], ps: Sequence[float] = (50, 95, 99)
) -> dict[str, Optional[float]]:
    """{'p50': ..} con None cuando n es insuficiente (P95 requiere n>=20, P99 n>=100)."""
    out: dict[str, Optional[float]] = {}
    for p in ps:
        key = f"p{int(p) if float(p).is_integer() else p}"
        out[key] = percentile(values, p) if len(values) >= min_samples_for(p) else None
    return out


def throughput(n_docs: int, seconds: Optional[float]) -> Optional[float]:
    """Documentos por segundo; None si no hay tiempo válido."""
    if seconds is None or seconds <= 0 or n_docs <= 0:
        return None
    return n_docs / seconds
