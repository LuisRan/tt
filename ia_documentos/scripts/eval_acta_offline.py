"""
Evaluador OFFLINE del acta de nacimiento: reaplica la reconciliacion sobre texto crudo + datos
guardados de una campana y puntua con el evaluador del puente (gen-ia, solo lectura).

Uso (cwd = ia_documentos):
  PYTHONPATH=/Users/luis/Documents/gen-ia python scripts/eval_acta_offline.py \
      --campana /Users/luis/Documents/gen-ia/out/campaigns/filas-acta-completa --splits dev
  opciones: --modos base,sin,con,sin+acta,con+acta   --por-campo   --fallos

Modos: base = predicciones guardadas (reconcile apagado); sin/con = reconcile existente
sin/con derivados; "+acta" = ademas las reglas de application/reconcile_acta.py.
No ejecuta OCR, LLM ni Ollama. No imprime valores personales (solo conteos y nombres de campo).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.evaluation import SampleRef  # noqa: E402
from contracts.ground_truth import GroundTruth  # noqa: E402
from src.evaluation.campaign import read_predictions  # noqa: E402
from src.evaluation.evaluate import evaluate_sample  # noqa: E402
from src.evaluation.results import CORRECT_STATUSES  # noqa: E402

import application.reconcile as R  # noqa: E402
import application.pipeline as P  # noqa: E402

TIPO = "acta_nacimiento_2015"


def aplicar(modo: str, texto: str, datos: dict) -> dict:
    if modo == "base":
        return dict(datos)
    P.RECONCILE_ALLOW_DERIVED = modo.startswith("con")
    R.ACTA_RULES_ON = modo.endswith("+acta")
    try:
        d, _ = P.DocumentPipeline._reconciliar("acta_nacimiento", texto, dict(datos), "offline")
    finally:
        R.ACTA_RULES_ON = None
    return d


def cargar(campana: Path, splits: set[str]):
    refs = [SampleRef.model_validate_json(l) for l in (campana / "manifest.jsonl").read_text().splitlines() if l.strip()]
    refs = [r for r in refs if r.doc_type == TIPO and r.split in splits]
    preds = read_predictions(campana / "predictions.jsonl")
    out = []
    for r in refs:
        gt = GroundTruth.model_validate_json((campana / r.ground_truth_path).read_text(encoding="utf-8"))
        gt = gt.model_copy(update={"sample_id": r.sample_id})
        raw = campana / "ocr_data/output/raw_text" / f"{r.sample_id}.md"
        texto = raw.read_text(encoding="utf-8") if raw.is_file() else ""
        out.append((r, gt, preds[r.sample_id], texto))
    return out


def puntuar(muestras, modo: str):
    """-> lista de (ref, SampleResult)"""
    res = []
    for r, gt, p, texto in muestras:
        if p.status != "error":
            if modo == "base":
                pp = p
            else:
                nuevos = aplicar(modo, texto, p.datos)
                pp = p.model_copy(update={"datos": nuevos})
        else:
            pp = p
        res.append((r, evaluate_sample(gt, pp, r)))
    return res


def agregar(res):
    """exactitud agrupada: clave -> (aciertos, total, docs_fallidos, docs)"""
    g = defaultdict(lambda: [0, 0, 0, 0])
    f = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r, s in res:
        for clave in (r.degradation_preset, "none+default" if r.degradation_preset != "heavy" else None, "todo"):
            if clave is None:
                continue
            g[clave][3] += 1
            if s.failed:
                g[clave][2] += 1
                continue
            for fr in s.fields:
                if not fr.scored:
                    continue
                g[clave][1] += 1
                ok = fr.status in CORRECT_STATUSES
                g[clave][0] += ok
                f[clave][fr.name][0] += ok
                f[clave][fr.name][1] += 1
    return g, f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campana", default="/Users/luis/Documents/gen-ia/out/campaigns/filas-acta-completa")
    ap.add_argument("--splits", default="dev")
    ap.add_argument("--modos", default="base,sin,con")
    ap.add_argument("--por-campo", action="store_true")
    a = ap.parse_args()
    muestras = cargar(Path(a.campana), set(a.splits.split(",")))
    print(f"splits={a.splits} muestras_acta={len(muestras)}")
    for modo in a.modos.split(","):
        res = puntuar(muestras, modo)
        g, f = agregar(res)
        print(f"\n== modo {modo}")
        for k in ("none", "default", "none+default", "heavy", "todo"):
            if k in g:
                ac, tot, fall, docs = g[k]
                print(f"  {k:13s} exactitud={100*ac/max(tot,1):5.1f}%  campos={tot:4d}  docs={docs} fallidos={fall}")
        if a.por_campo:
            for k in ("none", "default", "none+default"):
                print(f"  -- por campo [{k}]")
                for nm, (ac, tot) in sorted(f[k].items(), key=lambda x: (x[1][0] / max(x[1][1], 1), x[0])):
                    print(f"     {nm:20s} {100*ac/max(tot,1):5.1f}%  ({ac}/{tot})")


if __name__ == "__main__":
    main()
