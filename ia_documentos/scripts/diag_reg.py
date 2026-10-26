"""Lista (sin valores) los campos que pasan de correcto a incorrecto entre dos modos."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_ine_offline as E
a, b, splits = sys.argv[1], sys.argv[2], set(sys.argv[3].split(","))
res = E.evaluar(Path("/Users/luis/Documents/gen-ia/out/campaigns/mejoras-ocr-parcial"), splits, [a, b])
ok = {(r.sample_id, f.name) for _, r, _ in res[a] for f in r.fields if f.scored and E.acierto(f)}
for ref, r, camb in res[b]:
    for f in r.fields:
        if f.scored and not E.acierto(f) and (r.sample_id, f.name) in ok:
            print(ref.sample_id[-28:], f.name, f.status.value, [c.regla for c in camb if c.campo == f.name])
