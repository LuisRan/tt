"""Evaluación por muestra, agregación de campaña y comparación de campañas (puro).

Convenciones:
- Denominador de exactitud = campos `scored` (todo salvo NOT_APPLICABLE y los EXTRA de claves
  desconocidas y los EXTRA_SCHEMA). Correcto = EXACT + NORMALIZED. PARTIAL NO cuenta como correcto.
- Un documento fallido (status=error) no aporta campos: se reporta en tasa de error y
  documentos fallidos, sin inventar métricas de campo.
- Clasificación: solo documentos sin error; pred None = sin clasificar.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable, Iterable, Optional

from contracts.evaluation import GEN_TO_OCR_DOCTYPE, OcrPrediction, SampleRef
from contracts.ground_truth import GroundTruth

from .mapping import OCR_FIELDS, OCR_SCHEMA_FIELDS, criticality, expected_fields
from .metrics import (
    cer,
    classification_metrics,
    percentiles,
    throughput,
    wer,
)
from .normalize import FieldKind, compare_values
from .results import (
    CORRECT_STATUSES,
    ERROR_STATUSES,
    CampaignReport,
    Comparison,
    ErrorPattern,
    FailedDocument,
    FieldAggregate,
    FieldResult,
    FieldStatus,
    GroupSummary,
    MetricDelta,
    PerfAggregate,
    Recommendation,
    SampleResult,
)

S = FieldStatus


def _blank(v: Optional[str]) -> bool:
    return v is None or not v.strip()


def _r(x: Optional[float], nd: int = 6) -> Optional[float]:
    return None if x is None else round(x, nd)


def _mean(xs: list[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


# ------------------------------------------------------------------ por muestra

def evaluate_sample(gt: GroundTruth, pred: OcrPrediction, ref: SampleRef) -> SampleResult:
    if not (gt.sample_id == pred.sample_id == ref.sample_id):
        raise ValueError("sample_id no coincide entre GT, predicción y referencia")
    t = pred.timings
    res = SampleResult(
        sample_id=ref.sample_id, group_id=ref.group_id, split=ref.split, doc_type=gt.doc_type,
        degradation_preset=ref.degradation_preset, run_id=ref.run_id,
        total_ms=t.total_ms if t else None,
        stages_ms=dict(t.stages_ms) if t else {},
        model_load_ms=t.model_load_ms if t else None,
    )
    if pred.status == "error":
        res.failed = True
        res.error_code = pred.error.code if pred.error else "ERROR_DESCONOCIDO"
        return res

    ocr_type = GEN_TO_OCR_DOCTYPE[gt.doc_type]
    res.predicted_type = pred.tipo_documento
    res.type_correct = (pred.tipo_documento == ocr_type) if pred.tipo_documento is not None else False

    fields: list[FieldResult] = []
    for name, ef in expected_fields(gt).items():
        actual = pred.datos.get(name)
        crit = criticality(name)
        if not ef.applicable:
            fields.append(FieldResult(
                name=name, status=S.NOT_APPLICABLE, actual=None if _blank(actual) else actual,
                criticality=crit, scored=False, note=ef.reason))
            continue
        exp = ef.value or ""
        if not exp:  # se espera vacío
            if _blank(actual):
                fields.append(FieldResult(name=name, status=S.EXACT, expected="", actual=actual,
                                          cer=0.0, criticality=crit))
            else:
                fields.append(FieldResult(name=name, status=S.EXTRA, expected="", actual=actual,
                                          cer=1.0, criticality=crit))
            continue
        if _blank(actual):
            fields.append(FieldResult(name=name, status=S.MISSING, expected=exp, actual=actual,
                                      cer=1.0, wer=1.0 if ef.kind in (FieldKind.TEXT, FieldKind.ADDRESS) and len(exp.split()) > 1 else None,
                                      criticality=crit))
            continue
        assert actual is not None
        status = compare_values(ef.kind, exp, actual, ef.alternatives)
        multiword = ef.kind in (FieldKind.TEXT, FieldKind.ADDRESS) and len(exp.split()) > 1
        fields.append(FieldResult(
            name=name, status=status, expected=exp, actual=actual,
            cer=cer(exp, actual), wer=wer(exp, actual) if multiword else None, criticality=crit))
    known = set(OCR_FIELDS[ocr_type])
    schema = OCR_SCHEMA_FIELDS[ocr_type]
    for k in sorted(pred.datos):
        if k not in known and not _blank(pred.datos[k]):
            if k in schema:
                fields.append(FieldResult(name=k, status=S.EXTRA_SCHEMA, actual=pred.datos[k],
                                          criticality=criticality(k), scored=False,
                                          note="campo del esquema del OCR sin equivalente en el GT"))
            else:
                fields.append(FieldResult(name=k, status=S.EXTRA, actual=pred.datos[k],
                                          criticality=criticality(k), scored=False,
                                          note="clave fuera del esquema del OCR"))
    res.fields = fields
    return res


# ------------------------------------------------------------------ agregación

def _scored(s: SampleResult) -> list[FieldResult]:
    return [f for f in s.fields if f.scored]


def _group_summary(key: str, samples: list[SampleResult]) -> GroupSummary:
    scored = [f for s in samples for f in _scored(s)]
    correct = sum(1 for f in scored if f.status in CORRECT_STATUSES)
    exact = sum(1 for f in scored if f.status == S.EXACT)
    partial = sum(1 for f in scored if f.status == S.PARTIAL)
    cers = [f.cer for f in scored if f.cer is not None]
    typed = [s.type_correct for s in samples if s.type_correct is not None]
    n = len(scored)
    n_xs = sum(1 for s in samples for f in s.fields if f.status == S.EXTRA_SCHEMA)
    return GroupSummary(
        key=key, n_samples=len(samples), n_failed=sum(1 for s in samples if s.failed),
        n_scored_fields=n, n_correct=correct, n_exact=exact, n_partial=partial, n_extra_schema=n_xs,
        accuracy=_r(correct / n if n else None), exact_rate=_r(exact / n if n else None),
        mean_cer=_r(_mean(cers)),
        type_accuracy=_r(sum(typed) / len(typed) if typed else None),
    )


def _groups(samples: list[SampleResult], keyf: Callable[[SampleResult], str]) -> list[GroupSummary]:
    d: dict[str, list[SampleResult]] = defaultdict(list)
    for s in samples:
        d[keyf(s)].append(s)
    return [_group_summary(k, d[k]) for k in sorted(d)]


def _field_aggregate(name: str, doc_type: Optional[str], frs: list[FieldResult]) -> FieldAggregate:
    counts = Counter(f.status.value for f in frs)
    scored = [f for f in frs if f.scored]
    n = len(scored)
    correct = sum(1 for f in scored if f.status in CORRECT_STATUSES)
    exact = sum(1 for f in scored if f.status == S.EXACT)
    partial = sum(1 for f in scored if f.status == S.PARTIAL)
    errs = sum(1 for f in scored if f.status in ERROR_STATUSES)
    crit = criticality(name)
    return FieldAggregate(
        field=name, doc_type=doc_type, criticality=crit, critical=crit >= 3, n_scored=n,
        counts={st.value: counts.get(st.value, 0) for st in FieldStatus},
        accuracy=_r(correct / n if n else None), exact_rate=_r(exact / n if n else None),
        partial_rate=_r(partial / n if n else None), n_errors=errs,
        n_extra_schema=counts.get(S.EXTRA_SCHEMA.value, 0),
        mean_cer=_r(_mean([f.cer for f in scored if f.cer is not None])),
        mean_wer=_r(_mean([f.wer for f in scored if f.wer is not None])),
    )


def _perf(samples: list[SampleResult], run_info: dict[str, Any]) -> PerfAggregate:
    n = len(samples)
    ok = [s for s in samples if not s.failed and s.total_ms is not None]
    lat = [s.total_ms for s in ok if s.total_ms is not None]
    excl = [(s.total_ms or 0.0) - (s.model_load_ms or 0.0) for s in ok]
    pc = percentiles(lat) if lat else {}
    wall = run_info.get("wall_clock_s")
    if isinstance(wall, (int, float)) and wall > 0:
        secs, basis = float(wall), "wall_clock_s"
    else:
        timed = [s.total_ms for s in samples if s.total_ms is not None]
        secs, basis = (sum(timed) / 1000.0, "suma_latencias") if timed else (None, None)
    stages: dict[str, list[float]] = defaultdict(list)
    for s in samples:
        for k, v in s.stages_ms.items():
            stages[k].append(v)
    loads = [s.model_load_ms for s in samples if s.model_load_ms is not None]
    return PerfAggregate(
        n_samples=n, n_timed=len(lat),
        error_rate=_r(sum(1 for s in samples if s.failed) / n if n else None),
        latency_mean_ms=_r(_mean(lat), 3), latency_mean_ms_excl_model_load=_r(_mean(excl), 3),
        latency_p50_ms=_r(pc.get("p50"), 3), latency_p95_ms=_r(pc.get("p95"), 3),
        latency_p99_ms=_r(pc.get("p99"), 3),
        docs_per_s=_r(throughput(len(ok), secs), 6), throughput_basis=basis,
        model_load_ms=_r(sum(loads), 3) if loads else None,
        stages_mean_ms={k: _r(sum(v) / len(v), 3) for k, v in sorted(stages.items())},  # type: ignore[misc]
    )


_HINT = {
    S.MISSING: "el campo no se extrae (null/ausente)",
    S.MISMATCH: "el valor extraído es distinto del esperado",
    S.UNINTERPRETABLE: "el valor extraído no es interpretable (formato)",
    S.EXTRA: "se devuelve valor donde se espera vacío",
}


def _recommendations(
    samples: list[SampleResult],
    fields_by_type: list[FieldAggregate],
    classification_wrong: int,
    failed: list[FailedDocument],
    limit: int,
) -> list[Recommendation]:
    # error por (campo, tipo, preset) para detectar sensibilidad a la degradación
    cnt: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for s in samples:
        for f in _scored(s):
            c = cnt[(f.name, s.doc_type.value, s.degradation_preset)]
            c[1] += 1
            c[0] += f.status in ERROR_STATUSES
    recs: list[Recommendation] = []
    for fa in fields_by_type:
        if fa.n_errors <= 0:
            continue
        dominant = max((st for st in _HINT), key=lambda st: (fa.counts.get(st.value, 0), st.value))
        msg = (f"{fa.field} en {fa.doc_type}: {fa.n_errors} de {fa.n_scored} con error; "
               f"predomina {dominant.value} ({fa.counts.get(dominant.value, 0)}): {_HINT[dominant]}.")
        clean = cnt.get((fa.field, fa.doc_type or "", "none"))
        others = [v for (f, t, p), v in cnt.items() if f == fa.field and t == fa.doc_type and p != "none"]
        if clean and clean[1] and others:
            o_err, o_n = sum(v[0] for v in others), sum(v[1] for v in others)
            if o_n and o_err / o_n > clean[0] / clean[1]:
                msg += (f" El error crece con la degradación ({100 * clean[0] / clean[1]:.1f}% limpio "
                        f"vs {100 * o_err / o_n:.1f}% degradado).")
        recs.append(Recommendation(priority=0, scope="campo", target=f"{fa.field}@{fa.doc_type}",
                                   impact=float(fa.n_errors * fa.criticality), n_errors=fa.n_errors,
                                   message=msg))
    if failed:
        by_code = Counter(f.error_code or "ERROR_DESCONOCIDO" for f in failed)
        code, n = sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        recs.append(Recommendation(
            priority=0, scope="fallos", target=code, impact=float(len(failed) * 3), n_errors=len(failed),
            message=f"{len(failed)} documentos fallidos; el código más frecuente es {code} ({n})."))
    if classification_wrong:
        recs.append(Recommendation(
            priority=0, scope="clasificacion", target="tipo_documento",
            impact=float(classification_wrong * 3), n_errors=classification_wrong,
            message=f"{classification_wrong} documentos mal clasificados o sin clasificar."))
    recs.sort(key=lambda r: (-r.impact, r.scope, r.target))
    recs = recs[:limit]
    return [r.model_copy(update={"priority": i + 1}) for i, r in enumerate(recs)]


def aggregate(
    results: list[SampleResult],
    run_info: dict[str, Any],
    top_n: int = 20,
    max_recommendations: int = 15,
) -> CampaignReport:
    samples = sorted(results, key=lambda s: s.sample_id)
    ok = [s for s in samples if not s.failed]

    by_field: dict[str, list[FieldResult]] = defaultdict(list)
    by_ft: dict[tuple[str, str], list[FieldResult]] = defaultdict(list)
    for s in samples:
        for f in s.fields:
            by_field[f.name].append(f)
            by_ft[(f.name, s.doc_type.value)].append(f)

    fields = [_field_aggregate(k, None, v) for k, v in sorted(by_field.items())]
    fields_by_type = [_field_aggregate(k[0], k[1], v) for k, v in sorted(by_ft.items())]

    classification = None
    cls_wrong = 0
    if ok:
        yt = [GEN_TO_OCR_DOCTYPE[s.doc_type] for s in ok]
        yp = [s.predicted_type for s in ok]
        classification = classification_metrics(yt, yp)
        cls_wrong = sum(1 for t, p in zip(yt, yp) if t != p)

    failed = [FailedDocument(sample_id=s.sample_id, doc_type=s.doc_type,
                             degradation_preset=s.degradation_preset, split=s.split,
                             error_code=s.error_code) for s in samples if s.failed]

    pat: Counter[tuple[str, str, str]] = Counter()
    for (name, dt), frs in by_ft.items():
        for f in frs:
            if f.status in ERROR_STATUSES or f.status == S.PARTIAL:
                pat[(name, dt, f.status.value)] += 1
    top = [ErrorPattern(field=k[0], doc_type=k[1], status=k[2], count=c)
           for k, c in sorted(pat.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]]

    return CampaignReport(
        run_info=dict(run_info), generated_at=run_info.get("generated_at"),
        n_samples=len(samples), n_failed=len(failed),
        overall=_group_summary("global", samples),
        by_type=_groups(samples, lambda s: s.doc_type.value),
        by_split=_groups(samples, lambda s: s.split),
        by_preset=_groups(samples, lambda s: s.degradation_preset),
        by_type_preset=_groups(samples, lambda s: f"{s.doc_type.value}|{s.degradation_preset}"),
        fields=fields, fields_by_type=fields_by_type, classification=classification,
        performance=_perf(samples, run_info), failed_documents=failed, top_errors=top,
        recommendations=_recommendations(samples, fields_by_type, cls_wrong, failed, max_recommendations),
        samples=samples,
    )


# ------------------------------------------------------------------ comparación

def _delta(scope: str, name: str, b: Optional[float], c: Optional[float],
           higher: bool, tol: float) -> MetricDelta:
    if b is None or c is None:
        return MetricDelta(scope=scope, name=name, baseline=b, candidate=c, higher_is_better=higher)
    d = c - b
    worse = (-d if higher else d)
    verdict = "regresion" if worse > tol else ("mejora" if -worse > tol else "igual")
    return MetricDelta(scope=scope, name=name, baseline=b, candidate=c, delta=_r(d, 9),
                       higher_is_better=higher, verdict=verdict)


def compare_reports(
    baseline: CampaignReport,
    candidate: CampaignReport,
    quality_tolerance: float = 0.0,
    perf_tolerance: float = 0.0,
) -> Comparison:
    deltas: list[MetricDelta] = []
    qt, pt = quality_tolerance, perf_tolerance
    bo, co = baseline.overall, candidate.overall

    def g(o: Optional[GroupSummary], attr: str) -> Optional[float]:
        return getattr(o, attr) if o else None

    for attr, hi in (("accuracy", True), ("exact_rate", True), ("mean_cer", False), ("type_accuracy", True)):
        deltas.append(_delta("global", attr, g(bo, attr), g(co, attr), hi, qt))
    bp, cp = baseline.performance, candidate.performance
    deltas.append(_delta("global", "error_rate", bp.error_rate, cp.error_rate, False, qt))
    for attr, hi in (("latency_mean_ms", False), ("latency_p50_ms", False), ("latency_p95_ms", False),
                     ("latency_p99_ms", False), ("docs_per_s", True)):
        deltas.append(_delta("rendimiento", attr, getattr(bp, attr), getattr(cp, attr), hi, pt))

    bt = {x.key: x for x in baseline.by_type}
    ct = {x.key: x for x in candidate.by_type}
    for k in sorted(set(bt) | set(ct)):
        deltas.append(_delta("tipo", f"{k}.accuracy", g(bt.get(k), "accuracy"), g(ct.get(k), "accuracy"), True, qt))
        deltas.append(_delta("tipo", f"{k}.mean_cer", g(bt.get(k), "mean_cer"), g(ct.get(k), "mean_cer"), False, qt))

    bf = {(x.field, x.doc_type): x for x in baseline.fields_by_type if x.critical}
    cf = {(x.field, x.doc_type): x for x in candidate.fields_by_type if x.critical}
    for k in sorted(set(bf) | set(cf), key=lambda kk: (kk[0], kk[1] or "")):
        deltas.append(_delta("campo_critico", f"{k[0]}@{k[1]}",
                             bf[k].accuracy if k in bf else None,
                             cf[k].accuracy if k in cf else None, True, qt))

    regs = [d for d in deltas if d.verdict == "regresion"]
    imps = [d for d in deltas if d.verdict == "mejora"]
    acc = next(d for d in deltas if d.scope == "global" and d.name == "accuracy")
    gi = acc.verdict == "mejora"
    notes: list[str] = []
    bs, cs = {s.sample_id for s in baseline.samples}, {s.sample_id for s in candidate.samples}
    comparable = bs == cs
    if not comparable:
        notes.append(f"conjuntos de muestras distintos: {len(bs - cs)} solo en baseline, "
                     f"{len(cs - bs)} solo en candidate; las diferencias pueden no ser atribuibles al algoritmo")
    return Comparison(
        baseline_run=_run_name(baseline), candidate_run=_run_name(candidate),
        comparable=comparable, notes=notes, quality_tolerance=qt, perf_tolerance=pt,
        deltas=deltas, regressions=regs, improvements=imps, global_improved=gi,
        regression_despite_global_gain=gi and any(d.scope in ("tipo", "campo_critico") for d in regs),
    )


def _run_name(r: CampaignReport) -> Optional[str]:
    v = r.run_info.get("run_id") or r.run_info.get("name")
    return str(v) if v is not None else None


__all__: Iterable[str] = [
    "evaluate_sample", "aggregate", "compare_reports",
]
