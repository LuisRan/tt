import sys, difflib, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_ine_offline as E
from application import reconcile_ine as RI, reconcile as R
C = Path("/Users/luis/Documents/gen-ia/out/campaigns/mejoras-ocr-parcial")
campo = sys.argv[1]; splits = {sys.argv[2]}
res = E.evaluar(C, splits, ["ine", "rec"])
rec = {r.sample_id: r for _, r, _ in res["rec"]}
for ref, sr, camb in res["ine"]:
    if ref.degradation_preset == "heavy" and len(sys.argv) < 4: continue
    for fr in sr.fields:
        if fr.name == campo and not E.acierto(fr):
            ok_rec = any(f.name == campo and E.acierto(f) for f in rec[sr.sample_id].fields)
            e, a = fr.expected or "", fr.actual or ""
            ops = [(t, e[i1:i2], a[j1:j2]) for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, e, a).get_opcodes() if t != "equal"]
            print(ref.sample_id[-26:], fr.status.value, "REG" if ok_rec else "", "reglas:", [c.regla for c in camb if c.campo == campo], "ops:", ops[:4])
