"""Diagnóstico de fallos INE (offline). Imprime causa y forma ENMASCARADA (sin valores)."""
import re, sys, unicodedata
from pathlib import Path
from collections import Counter, defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_ine_offline as E

def n(s): return re.sub(r"[^A-Z0-9]", "", "".join(unicodedata.normalize("NFD", str(s or "")).upper()[:0] or c for c in unicodedata.normalize("NFD", str(s or "")) if not unicodedata.combining(c)).upper())
def shape(s):
    if s is None: return "None"
    return re.sub(r"[A-ZÁÉÍÓÚÑ]", "A", re.sub(r"\d", "#", re.sub(r"[a-z]", "a", str(s))))

def causa(exp, act, raw):
    if act in (None, ""): pref = "faltante"
    else: pref = "distinto"
    e = n(exp)
    if e and e in n(raw): return pref + "/en_texto"
    toks = [n(t) for t in str(exp).split() if n(t)]
    if toks and all(t in n(raw) for t in toks): return pref + "/tokens_en_texto"
    return pref + "/ausente_texto"

def main():
    splits = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else {"dev"}
    modo = sys.argv[1] if len(sys.argv) > 1 else "rec"
    res = E.evaluar(Path("/Users/luis/Documents/gen-ia/out/campaigns/mejoras-ocr-parcial"), splits, [modo])[modo]
    camp = Path("/Users/luis/Documents/gen-ia/out/campaigns/mejoras-ocr-parcial/ocr_data/output/raw_text")
    cnt = Counter(); filas = []
    for ref, sr, _ in res:
        if ref.degradation_preset == "heavy": continue
        raw = (camp / f"{ref.sample_id}.md").read_text(encoding="utf-8")
        for fr in sr.fields:
            if fr.scored and not E.acierto(fr):
                c = causa(fr.expected, fr.actual, raw)
                cnt[(fr.name, c)] += 1
                filas.append((ref.sample_id[-30:], fr.name, fr.status.value, c, shape(fr.expected), shape(fr.actual)))
    for k, v in sorted(cnt.items()): print(v, k)
    if "-v" in sys.argv:
        for f in filas: print(f)
main()
