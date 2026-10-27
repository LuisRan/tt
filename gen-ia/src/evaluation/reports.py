"""Escritura determinista de reportes (JSON, CSV, Markdown). Sin reloj: `generated_at` viene
del reporte (lo fija quien llama)."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional

from .results import CampaignReport, FieldStatus, GroupSummary


def _fmt(x: Optional[float], pct: bool = False) -> str:
    if x is None:
        return "n/d"
    return f"{100 * x:.1f}%" if pct else f"{x:.3f}"


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _group_table(title: str, groups: list[GroupSummary]) -> list[str]:
    if not groups:
        return []
    out = [f"### {title}", "", "| Grupo | Muestras | Fallidas | Campos | Exactitud | Exactos | CER medio | Tipo OK | Extra esquema |",
           "|---|---|---|---|---|---|---|---|---|"]
    for g in groups:
        out.append(f"| {g.key} | {g.n_samples} | {g.n_failed} | {g.n_scored_fields} | {_fmt(g.accuracy, True)} "
                   f"| {_fmt(g.exact_rate, True)} | {_fmt(g.mean_cer)} | {_fmt(g.type_accuracy, True)} | {g.n_extra_schema} |")
    return out + [""]


def render_markdown(r: CampaignReport) -> str:
    L: list[str] = ["# Reporte de evaluación supervisada", ""]
    if r.generated_at:
        L += [f"Generado: {r.generated_at}", ""]
    ri = {k: v for k, v in sorted(r.run_info.items()) if k != "generated_at"}
    if ri:
        L += ["## Ejecución", ""] + [f"- {k}: {v}" for k, v in ri.items()] + [""]
    L += ["## Resumen", "", f"- Muestras: {r.n_samples} (fallidas: {r.n_failed})"]
    if r.overall:
        o = r.overall
        L += [f"- Exactitud de campos (EXACT+NORMALIZED): {_fmt(o.accuracy, True)} sobre {o.n_scored_fields} campos",
              f"- Coincidencia exacta: {_fmt(o.exact_rate, True)}; PARTIAL: {o.n_partial}",
              f"- Claves del esquema OCR sin GT (EXTRA_SCHEMA, no son error): {o.n_extra_schema}",
              f"- CER medio: {_fmt(o.mean_cer)}; clasificación correcta: {_fmt(o.type_accuracy, True)}"]
    L += [""]
    L += ["## Resultados por grupo", ""]
    L += _group_table("Por tipo", r.by_type) + _group_table("Por split", r.by_split)
    L += _group_table("Por preset de degradación", r.by_preset) + _group_table("Por tipo y preset", r.by_type_preset)

    L += ["## Campos (tipo × campo)", "", "| Campo | Tipo | Crit. | n | Exactitud | CER | Errores |", "|---|---|---|---|---|---|---|"]
    for f in r.fields_by_type:
        if f.n_scored:
            L.append(f"| {f.field} | {f.doc_type} | {f.criticality} | {f.n_scored} | {_fmt(f.accuracy, True)} "
                     f"| {_fmt(f.mean_cer)} | {f.n_errors} |")
    na = sorted({(f.field, f.doc_type) for f in r.fields_by_type if f.n_scored == 0 and f.counts.get("NOT_APPLICABLE", 0)})
    if na:
        L += ["", "Campos sin equivalente verificable en el GT (NOT_APPLICABLE): "
              + ", ".join(f"{a}@{b}" for a, b in na)]
    xs = sorted((f.doc_type or "", f.field, f.counts.get("EXTRA_SCHEMA", 0))
                for f in r.fields_by_type if f.counts.get("EXTRA_SCHEMA", 0))
    if xs:
        L += ["", "Claves del esquema del OCR sin equivalente en el GT (EXTRA_SCHEMA; no penalizan): "
              + ", ".join(f"{t}.{n}={c}" for t, n, c in xs)]
    L += [""]

    if r.classification:
        c = r.classification
        L += ["## Clasificación de documento", "",
              f"- Exactitud: {_fmt(c.accuracy, True)}; F1 macro: {_fmt(c.macro_f1)}; "
              f"sin clasificar: {_fmt(None if c.unclassified_pct is None else c.unclassified_pct / 100, True)}; "
              f"errores entre categorías: {c.cross_category_errors}", "",
              "| Clase | Soporte | Precisión | Recall | F1 |", "|---|---|---|---|---|"]
        for m in c.per_class:
            L.append(f"| {m.label} | {m.support} | {_fmt(m.precision)} | {_fmt(m.recall)} | {_fmt(m.f1)} |")
        L += [""]

    p = r.performance
    L += ["## Rendimiento", "",
          f"- Latencia media: {_fmt(p.latency_mean_ms)} ms (sin carga de modelo: {_fmt(p.latency_mean_ms_excl_model_load)} ms)",
          f"- P50/P95/P99: {_fmt(p.latency_p50_ms)} / {_fmt(p.latency_p95_ms)} / {_fmt(p.latency_p99_ms)} ms "
          "(n/d si hay muestras insuficientes)",
          f"- Documentos por segundo: {_fmt(p.docs_per_s)} (base: {p.throughput_basis or 'n/d'})",
          f"- Tasa de error: {_fmt(p.error_rate, True)}; carga de modelo: {_fmt(p.model_load_ms)} ms"]
    for k, v in p.stages_mean_ms.items():
        L.append(f"- Etapa {k}: {_fmt(v)} ms")
    L += [""]

    if r.failed_documents:
        L += ["## Documentos fallidos", ""]
        L += [f"- {d.sample_id} ({d.degradation_preset}, {d.split}): {d.error_code}" for d in r.failed_documents] + [""]
    if r.top_errors:
        L += ["## Errores más frecuentes", "", "| Campo | Tipo | Estado | Cuenta |", "|---|---|---|---|"]
        L += [f"| {e.field} | {e.doc_type} | {e.status} | {e.count} |" for e in r.top_errors] + [""]
    if r.recommendations:
        L += ["## Recomendaciones priorizadas", "", "(impacto = nº de errores × criticidad del campo)", ""]
        L += [f"{x.priority}. [{x.scope}] {x.message} Impacto: {x.impact:g}." for x in r.recommendations] + [""]
    return "\n".join(L).rstrip() + "\n"


def write_reports(report: CampaignReport, out_dir: Path | str) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "report.json": out / "report.json", "fields.csv": out / "fields.csv",
        "samples.csv": out / "samples.csv", "confusion.csv": out / "confusion.csv",
        "performance.json": out / "performance.json", "report.md": out / "report.md",
    }
    paths["report.json"].write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    perf = json.loads(report.performance.model_dump_json())
    paths["performance.json"].write_text(json.dumps(perf, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
    statuses = [s.value for s in FieldStatus]
    rows = []
    for scope, lst in (("campo", report.fields), ("campo_tipo", report.fields_by_type)):
        for f in lst:
            rows.append([scope, f.field, f.doc_type or "", f.criticality, f.n_scored, f.n_errors,
                         "" if f.accuracy is None else f.accuracy, "" if f.exact_rate is None else f.exact_rate,
                         "" if f.partial_rate is None else f.partial_rate,
                         "" if f.mean_cer is None else f.mean_cer, "" if f.mean_wer is None else f.mean_wer]
                        + [f.counts.get(s, 0) for s in statuses])
    _write_csv(paths["fields.csv"],
               ["scope", "field", "doc_type", "criticality", "n_scored", "n_errors", "accuracy",
                "exact_rate", "partial_rate", "mean_cer", "mean_wer"] + statuses, rows)

    srows = []
    for s in report.samples:
        sc = [f for f in s.fields if f.scored]
        srows.append([s.sample_id, s.group_id, s.split, s.doc_type.value, s.degradation_preset, s.run_id,
                      int(s.failed), s.error_code or "", s.predicted_type or "",
                      "" if s.type_correct is None else int(s.type_correct),
                      len(sc), sum(1 for f in sc if f.status in (FieldStatus.EXACT, FieldStatus.NORMALIZED)),
                      sum(1 for f in sc if f.status == FieldStatus.PARTIAL),
                      sum(1 for f in sc if f.status in (FieldStatus.MISMATCH, FieldStatus.MISSING,
                                                        FieldStatus.UNINTERPRETABLE, FieldStatus.EXTRA)),
                      "" if s.total_ms is None else s.total_ms])
    _write_csv(paths["samples.csv"],
               ["sample_id", "group_id", "split", "doc_type", "preset", "run_id", "failed", "error_code",
                "predicted_type", "type_correct", "n_scored", "n_correct", "n_partial", "n_errors", "total_ms"],
               srows)

    cm = report.classification.confusion if report.classification else None
    crow = [[lab] + row for lab, row in zip(cm.labels, cm.matrix)] if cm else []
    _write_csv(paths["confusion.csv"], ["verdad\\prediccion"] + (cm.columns if cm else []), crow)

    paths["report.md"].write_text(render_markdown(report), encoding="utf-8")
    return paths
