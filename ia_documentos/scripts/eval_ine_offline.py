"""
Evaluador OFFLINE de la reconciliación de INE (sin LLM, sin Docling, sin Ollama).

Toma de una campaña del puente (gen-ia) el texto crudo y los datos ya extraídos,
aplica la etapa de reconciliación en distintos modos y puntúa con el evaluador del
puente (src.evaluation.evaluate.evaluate_sample). Solo lectura sobre la campaña.

Uso (cwd = ia_documentos):
  PYTHONPATH=/Users/luis/Documents/gen-ia python scripts/eval_ine_offline.py \
      --campaign /Users/luis/Documents/gen-ia/out/campaigns/mejoras-ocr-parcial \
      --split dev [--split val] [--modes base,rec,rec_der,ine,ine_der] [--detalle]

Modos:
  base     datos guardados (reconcile apagado)
  rec      reconcile existente (POSTPROCESS_RECONCILE) sin derivados
  rec_der  idem con RECONCILE_ALLOW_DERIVED
  ine      rec + reglas nuevas de INE (RECONCILE_INE), sin derivados
  ine_der  rec + reglas nuevas con derivados (RECONCILE_ALLOW_DERIVED)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402
from application import reconcile as rec_mod  # noqa: E402
from infrastructure.regex.correccion_ocr import aparece_con_tolerancia  # noqa: E402

PROTEGIDOS = {"curp", "clave_elector", "numero_folio", "estado", "municipio", "seccion", "localidad",
              "año_registro", "año_emision", "año_vigencia"}

MODOS = {
    "base": dict(rec=False, der=False, ine=False),
    "rec": dict(rec=True, der=False, ine=False),
    "rec_der": dict(rec=True, der=True, ine=False),
    "ine": dict(rec=True, der=False, ine=True),
    "ine_der": dict(rec=True, der=True, ine=True),
}


def reconciliar(datos: dict, raw: str, modo: dict, doc_id: str = "") -> tuple[dict, list]:
    """Réplica de Pipeline._reconciliar (incluye el revert anti-alucinación)."""
    if not modo["rec"]:
        return dict(datos), []
    settings.RECONCILE_INE = modo["ine"]
    res = rec_mod.reconcile("ine", raw, dict(datos), permitir_derivados=modo["der"])
    out = res.datos
    aceptados = []
    for c in res.cambios:
        if (not c.derivado and c.nuevo not in (None, "") and c.campo in PROTEGIDOS
                and not aparece_con_tolerancia(str(c.nuevo), raw)):
            out[c.campo] = c.anterior
            continue
        aceptados.append(c)
    return out, aceptados


def cargar(campaign: Path, splits: set[str]):
    from contracts.evaluation import SampleRef
    from contracts.ground_truth import GroundTruth
    from src.evaluation.campaign import read_predictions
    preds = read_predictions(campaign / "predictions.jsonl")
    for line in (campaign / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ref = SampleRef.model_validate_json(line)
        if ref.doc_type != "ine_2016" and getattr(ref.doc_type, "value", ref.doc_type) != "ine_2016":
            continue
        if splits and ref.split not in splits:
            continue
        gt = GroundTruth.model_validate_json((campaign / ref.ground_truth_path).read_text(encoding="utf-8"))
        gt = gt.model_copy(update={"sample_id": ref.sample_id})
        raw_p = campaign / "ocr_data/output/raw_text" / f"{ref.sample_id}.md"
        raw = raw_p.read_text(encoding="utf-8") if raw_p.is_file() else ""
        yield ref, gt, preds.get(ref.sample_id), raw


def evaluar(campaign: Path, splits: set[str], modos: list[str]):
    """-> {modo: [(ref, SampleResult, cambios)]}"""
    from src.evaluation.evaluate import evaluate_sample
    muestras = list(cargar(campaign, splits))
    salida = {m: [] for m in modos}
    for ref, gt, pred, raw in muestras:
        if pred is None:
            continue
        for m in modos:
            if pred.status == "ok" and pred.datos is not None:
                datos, cambios = reconciliar(pred.datos, raw, MODOS[m], ref.sample_id)
                p2 = pred.model_copy(update={"datos": datos})
            else:
                p2, cambios = pred, []
            salida[m].append((ref, evaluate_sample(gt, p2, ref), cambios))
    return salida


def acierto(fr) -> bool:
    return fr.status.value in ("EXACT", "NORMALIZED") if hasattr(fr.status, "value") else fr.status in ("EXACT", "NORMALIZED")


def tabla(res):
    """-> {preset: {campo: [ok, n]}} y totales."""
    t = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    docs_fallidos = defaultdict(int)
    for ref, sr, _ in res:
        p = ref.degradation_preset
        if sr.failed:
            docs_fallidos[p] += 1
        for fr in sr.fields:
            if not fr.scored:
                continue
            t[p][fr.name][1] += 1
            t[p][fr.name][0] += int(acierto(fr))
    return t, docs_fallidos


def pct(a, b):
    return 100.0 * a / b if b else float("nan")


def resumen(res):
    t, fall = tabla(res)
    glob = {}
    for p in ("none", "default", "heavy"):
        ok = sum(v[0] for v in t[p].values())
        n = sum(v[1] for v in t[p].values())
        glob[p] = (ok, n, pct(ok, n), fall[p])
    ok = sum(g[0] for g in glob.values()); n = sum(g[1] for g in glob.values())
    nd_ok = glob["none"][0] + glob["default"][0]; nd_n = glob["none"][1] + glob["default"][1]
    return t, glob, (pct(ok, n), pct(nd_ok, nd_n))


def regresiones(res_a, res_b):
    """Campos correctos en A e incorrectos en B -> {campo: n} (B = candidato, A = referencia)."""
    ok_a = {(r.sample_id, f.name) for _, r, _ in res_a for f in r.fields if f.scored and acierto(f)}
    reg, gan = defaultdict(int), defaultdict(int)
    for _, r, _ in res_b:
        for f in r.fields:
            if not f.scored:
                continue
            if acierto(f) and (r.sample_id, f.name) not in ok_a:
                gan[f.name] += 1
            if not acierto(f) and (r.sample_id, f.name) in ok_a:
                reg[f.name] += 1
    return dict(reg), dict(gan)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default="/Users/luis/Documents/gen-ia/out/campaigns/mejoras-ocr-parcial")
    ap.add_argument("--split", action="append", default=[])
    ap.add_argument("--modes", default="base,rec,rec_der,ine,ine_der")
    ap.add_argument("--detalle", action="store_true", help="tabla por campo")
    ap.add_argument("--json", help="guarda el resumen en este archivo")
    a = ap.parse_args(argv)
    modos = a.modes.split(",")
    res = evaluar(Path(a.campaign), set(a.split), modos)
    out = {}
    print(f"splits={a.split or 'todos'}  (exactitud de campos INE, EXACT+NORMALIZED / puntuados)")
    print(f"{'modo':8} {'none':>8} {'default':>8} {'heavy':>8} {'n+d':>8} {'todo':>8}  fallidos(n/d/h)")
    for m in modos:
        t, g, (todo, nd) = resumen(res[m])
        out[m] = {"global": {p: g[p][2] for p in g}, "none+default": nd, "todo": todo,
                  "campos": {p: {c: (v[0], v[1]) for c, v in t[p].items()} for p in t}}
        print(f"{m:8} {g['none'][2]:8.1f} {g['default'][2]:8.1f} {g['heavy'][2]:8.1f} {nd:8.1f} {todo:8.1f}  "
              f"{g['none'][3]}/{g['default'][3]}/{g['heavy'][3]}")
    if a.detalle:
        campos = sorted({c for p in out[modos[0]]["campos"].values() for c in p})
        print("\npor campo (aciertos/puntuados) en none+default | heavy")
        print(f"{'campo':18}" + "".join(f"{m:>16}" for m in modos))
        for c in campos:
            fila = f"{c:18}"
            for m in modos:
                cp = out[m]["campos"]
                ok = sum(cp.get(p, {}).get(c, (0, 0))[0] for p in ("none", "default"))
                n = sum(cp.get(p, {}).get(c, (0, 0))[1] for p in ("none", "default"))
                h = cp.get("heavy", {}).get(c, (0, 0))
                fila += f"{ok:>6}/{n:<3}|{h[0]}/{h[1]:<3}"
            print(fila)
    for m in modos[1:]:
        for ref_m in ("base", "rec"):
            if ref_m in res and ref_m != m:
                reg, gan = regresiones(res[ref_m], res[m])
                print(f"regresiones {ref_m}->{m}: {sum(reg.values())} {reg}  ganancias: {sum(gan.values())}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
