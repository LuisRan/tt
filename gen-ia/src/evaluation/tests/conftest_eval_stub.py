"""Utilidades de test compartidas por los tests del orquestador (stub de W1 y OCR falso)."""
from __future__ import annotations

import sys
import types
from collections.abc import Iterator, Sequence
from types import SimpleNamespace

from contracts.evaluation import OcrPrediction

from src.evaluation.ocr_clients import OcrItem


class FakeOcr:
    """OcrClient falso: una predicción ok por documento (salvo `skip`)."""

    def __init__(self, skip: set[str] | None = None, error_ids: set[str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.skip = skip or set()
        self.error_ids = error_ids or set()

    def process_batch(self, items: Sequence[OcrItem]) -> Iterator[OcrPrediction]:
        self.calls.append([i.sample_id for i in items])
        for it in items:
            if any(it.sample_id.startswith(s) or it.sample_id == s for s in self.skip):
                continue
            if it.sample_id in self.error_ids:
                yield OcrPrediction(sample_id=it.sample_id, status="error",
                                    error={"code": "PDF_INVALIDO", "message": "x", "retryable": False})
                continue
            yield OcrPrediction(sample_id=it.sample_id, status="ok", tipo_documento="ine",
                                datos={"nombre": "X"})


def install_w1_stub(monkeypatch) -> SimpleNamespace:
    """Inyecta src.evaluation.evaluate / .reports falsos con las firmas fijas de W1."""
    seen = SimpleNamespace(evaluated=[], aggregated=[], written=[], compared=[])

    ev = types.ModuleType("src.evaluation.evaluate")

    def evaluate_sample(gt, pred, ref):
        seen.evaluated.append((gt.sample_id, pred.sample_id, ref.sample_id, pred.status,
                               pred.error.code if pred.error else None))
        return SimpleNamespace(sample_id=ref.sample_id, status=pred.status)

    def aggregate(results, run_info):
        seen.aggregated.append((list(results), run_info))
        return SimpleNamespace(results=list(results), run_info=run_info)

    def compare_reports(baseline, candidate):
        seen.compared.append((baseline, candidate))
        return SimpleNamespace(model_dump_json=lambda indent=2: '{"cmp": true}')

    ev.evaluate_sample, ev.aggregate, ev.compare_reports = evaluate_sample, aggregate, compare_reports

    rp = types.ModuleType("src.evaluation.reports")

    def write_reports(report, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / "report.json"
        f.write_text("{}", encoding="utf-8")
        seen.written.append(out_dir)
        return {"json": f}

    rp.write_reports = write_reports
    monkeypatch.setitem(sys.modules, "src.evaluation.evaluate", ev)
    monkeypatch.setitem(sys.modules, "src.evaluation.reports", rp)
    return seen
