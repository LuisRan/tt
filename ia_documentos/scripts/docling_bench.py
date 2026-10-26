#!/usr/bin/env python3
"""
scripts/docling_bench.py

Harness de medicion SOLO-OCR (sin LLM, sin Ollama, sin el CLI de evaluacion).
Mide, sobre una campana del generador (solo lectura):

  (a) deteccion de tipo (exacta y con `clasificar_tolerante`), tipo equivocado = 0 obligatorio;
  (b) recall de campos del ground truth EN EL TEXTO CRUDO (por tipo y por campo; campos
      criticos por separado). Normalizacion conservadora: nunca convierte un valor
      incorrecto en correcto;
  (c) tiempo medio y P95 por documento.

Subcomandos:
  baseline  -> metricas sobre el raw_text guardado por la campana (referencia, no corre nada)
  run       -> corre la etapa OCR (modo `docling` = solo el conversor; `adapter` =
               DoclingAdapter.extraer_texto completo) y guarda texto + tiempo por documento
  report    -> metricas de una o varias corridas contra el baseline, mismas muestras

Una configuracion Docling = variables de entorno (ver config/settings.py), p. ej.
  python scripts/docling_bench.py run --name fp --split dev --per-type 8 \
      --mode docling --env DOCLING_OCR_MODE=full_page

Las variables se aplican ANTES de importar `config.settings`, por eso cada corrida es un
proceso. Solo se usan datos sinteticos; los informes dan conteos y nombres de campo (nunca
valores).
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CAMPAIGN = Path(
    "/Users/luis/Documents/gen-ia/out/campaigns/baseline100-4242-1c6975d8"
)

# doc_type del generador -> tipo del OCR (valores de TipoDocumento)
GEN_TO_OCR = {
    "ine_2016": "ine",
    "curp_2019": "curp",
    "acta_nacimiento_2015": "acta_nacimiento",
    "pasaporte_electronico_2021": "pasaporte",
}

# ── Definicion de campos medibles ────────────────────────────────────────────
# (nombre_en_informe, clave_GT, indice_de_linea | None, clase)
# clase: id (exacto sin espacios), code (token exacto), text (sin acentos/mayusculas),
#        date (misma fecha, separadores libres), lines (fraccion de lineas halladas),
#        mrz (id de una linea del MRZ)
# Criticos (misma lista que la evaluacion supervisada, criticidad 3):
CRITICOS = {
    "curp", "clave_elector", "numero_pasaporte", "mrz_linea1", "mrz_linea2",
    "identificador_electronico", "folio_impresion", "folio", "curp_p1", "curp_p2",
}

CAMPOS: dict[str, list[tuple[str, str, Optional[int], str]]] = {
    "ine": [
        ("primer_apellido", "nombre", 0, "text"),
        ("segundo_apellido", "nombre", 1, "text"),
        ("nombres", "nombre", 2, "text"),
        ("domicilio", "domicilio", None, "lines"),
        ("curp", "curp", None, "id"),
        ("clave_elector", "clave_elector", None, "id"),
        ("fecha_nacimiento", "fecha_nacimiento", None, "date"),
        ("seccion", "seccion", None, "code"),
        ("localidad", "localidad", None, "code"),
    ],
    "curp": [
        ("nombre_completo", "nombre", None, "text"),
        ("curp", "curp", None, "id"),
        ("fecha_inscripcion", "fecha_inscripcion", None, "date"),
        ("folio", "folio", None, "id"),
        ("entidad_registro", "entidad_registro", None, "text"),
    ],
    "acta_nacimiento": [
        ("nombres", "reg_nombres", None, "text"),
        ("primer_apellido", "reg_primer_apellido", None, "text"),
        ("segundo_apellido", "reg_segundo_apellido", None, "text"),
        ("curp", "curp", None, "id"),
        ("folio_impresion", "folio_impresion", None, "id"),
        ("identificador_electronico", "identificador_electronico", None, "id"),
        ("numero_acta", "numero_acta", None, "code"),
        ("fecha_registro", "fecha_registro", None, "date"),
        ("fecha_nacimiento", "reg_fecha_nacimiento", None, "date"),
        ("entidad_registro", "entidad_registro", None, "text"),
        ("municipio_registro", "municipio_registro", None, "text"),
        ("lugar_nacimiento", "reg_lugar_nacimiento", None, "text"),
        ("curp_p1", "p1_curp", None, "id"),
        ("curp_p2", "p2_curp", None, "id"),
    ],
    "pasaporte": [
        ("apellidos", "apellidos", None, "text"),
        ("nombres", "nombres", None, "text"),
        ("curp", "curp", None, "id"),
        ("fecha_nacimiento", "fecha_nacimiento", None, "date"),
        ("fecha_expedicion", "fecha_expedicion", None, "date"),
        ("fecha_caducidad", "fecha_caducidad", None, "date"),
        ("lugar_nacimiento", "lugar_nacimiento", None, "text"),
        ("numero_pasaporte", "numero_pasaporte", None, "id"),
        ("nacionalidad", "nacionalidad", None, "text"),
        ("mrz_linea1", "mrz", 0, "mrz"),
        ("mrz_linea2", "mrz", 1, "mrz"),
    ],
}


@dataclass
class Esperado:
    nombre: str
    clase: str
    valores: list[str]  # para `lines` varias lineas; para el resto un solo valor
    critico: bool


def aplanar_gt(gt: dict) -> dict[str, Any]:
    """Primer valor de cada campo por nombre (igual que la evaluacion supervisada)."""
    out: dict[str, Any] = {}
    for pagina in gt.get("pages", []):
        for f in pagina.get("fields", []):
            out.setdefault(f["name"], f.get("value"))
    return out


def esperados(gt: dict, tipo_ocr: str) -> list[Esperado]:
    plano = aplanar_gt(gt)
    res: list[Esperado] = []
    for nombre, clave, idx, clase in CAMPOS[tipo_ocr]:
        v = plano.get(clave)
        if idx is not None or clase == "lines":
            if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
                continue
            if idx is not None:
                if idx >= len(v):
                    continue
                vals = [v[idx].strip()]
            else:
                vals = [x.strip() for x in v if x.strip()]
        else:
            if not isinstance(v, str) or not v.strip():
                continue
            vals = [v.strip()]
        vals = [x for x in vals if x]
        if not vals:
            continue
        res.append(Esperado(nombre, clase, vals, nombre in CRITICOS))
    return res


# ── Normalizacion y coincidencia (conservadora) ──────────────────────────────

def _quitar_acentos(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    return unicodedata.normalize("NFC", "".join(c for c in nfd if unicodedata.category(c) != "Mn"))


def limpiar_texto(texto: str) -> str:
    """Quita el front-matter que escribe la campana y las entidades HTML del markdown."""
    if texto.startswith("---\n"):
        fin = texto.find("\n---", 4)
        if fin != -1:
            texto = texto[fin + 4:]
    texto = html.unescape(texto)
    return texto.replace("\\_", "_")


def _sin_espacios_mayus(s: str) -> str:
    return "".join(s.upper().split())


def _norm_texto(s: str) -> str:
    return " ".join(_quitar_acentos(s).casefold().split())


def _coincide(clase: str, valor: str, texto: str, cache: dict) -> bool:
    if clase in ("id", "mrz"):
        base = cache.setdefault("id", _sin_espacios_mayus(texto))
        return _sin_espacios_mayus(valor) in base
    if clase == "code":
        base = cache.setdefault("code", texto.upper())
        v = re.escape(valor.strip().upper())
        return re.search(rf"(?<![A-Z0-9]){v}(?![A-Z0-9])", base) is not None
    if clase in ("text", "lines"):
        base = cache.setdefault("text", _norm_texto(texto))
        return _norm_texto(valor) in base
    if clase == "date":
        m = re.match(r"^\s*(\d{1,2})\D+(\d{1,2})\D+(\d{4})\s*$", valor)
        if not m:
            return False
        d, mo, y = m.groups()
        return re.search(rf"(?<!\d){d}[ /.\-]+{mo}[ /.\-]+{y}(?!\d)", texto) is not None
    raise ValueError(clase)


def puntuar(esp: Esperado, texto: str, cache: dict) -> float:
    """1.0 si el valor esta presente; para `lines` la fraccion de lineas halladas."""
    hallados = sum(1 for v in esp.valores if _coincide(esp.clase, v, texto, cache))
    return hallados / len(esp.valores)


def mrz_contiguo(gt: dict, texto: str) -> Optional[bool]:
    """Las dos lineas del MRZ completas y contiguas (solo espacios/saltos entre ellas)."""
    mrz = aplanar_gt(gt).get("mrz")
    if not isinstance(mrz, list) or len(mrz) != 2:
        return None
    return _sin_espacios_mayus(mrz[0]) + _sin_espacios_mayus(mrz[1]) in _sin_espacios_mayus(texto)


# ── Agregacion de metricas ───────────────────────────────────────────────────

@dataclass
class Registro:
    sample_id: str
    tipo_ocr: str
    preset: str
    split: str
    texto: str
    tipo_detectado: Optional[str]  # None = sin texto
    tipo_tolerante: Optional[str]
    segundos: Optional[float]
    campos: dict[str, float] = field(default_factory=dict)  # nombre -> puntuacion
    mrz_contiguo: Optional[bool] = None


def p95(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)]


def resumen_por_tipo(registros: list[Registro]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    tipos = sorted({r.tipo_ocr for r in registros})
    for t in tipos + ["TOTAL"]:
        rs = [r for r in registros if t == "TOTAL" or r.tipo_ocr == t]
        n = len(rs)
        exacto = sum(1 for r in rs if r.tipo_detectado == r.tipo_ocr)
        tol = sum(1 for r in rs if (r.tipo_tolerante or r.tipo_detectado) == r.tipo_ocr)
        malo = sum(1 for r in rs if r.tipo_detectado not in (None, "desconocido", r.tipo_ocr))
        malo_tol = sum(1 for r in rs if r.tipo_tolerante not in (None, "desconocido", r.tipo_ocr))
        esp = hit = 0.0
        cesp = chit = 0.0
        por_campo: dict[str, list[float]] = {}
        for r in rs:
            for nom, sc in r.campos.items():
                por_campo.setdefault(nom, []).append(sc)
                esp += 1
                hit += sc
                if nom in CRITICOS:
                    cesp += 1
                    chit += sc
        tiempos = [r.segundos for r in rs if r.segundos is not None]
        mc = [r.mrz_contiguo for r in rs if r.mrz_contiguo is not None]
        out[t] = {
            "n": n, "det_exacta": exacto, "det_tolerante": tol,
            "tipo_equivocado": malo, "tipo_equivocado_tol": malo_tol,
            "recall": hit / esp if esp else None, "recall_n": esp,
            "recall_critico": chit / cesp if cesp else None, "recall_critico_n": cesp,
            "por_campo": {k: (sum(v) / len(v), len(v)) for k, v in sorted(por_campo.items())},
            "t_medio": sum(tiempos) / len(tiempos) if tiempos else None,
            "t_p95": p95(tiempos),
            "mrz_contiguo": (sum(mc), len(mc)) if mc else None,
        }
    return out


def _fmt(x: Optional[float], pct: bool = True) -> str:
    if x is None:
        return "-"
    return f"{100 * x:.1f}%" if pct else f"{x:.1f}"


def tabla_markdown(titulo: str, res: dict[str, dict]) -> str:
    L = [f"### {titulo}", "",
         "| tipo | n | det exacta | det tolerante | equivocado (ex/tol) | recall campos | recall criticos | t medio s | t P95 s |",
         "|---|---|---|---|---|---|---|---|---|"]
    for t, d in res.items():
        L.append(f"| {t} | {d['n']} | {d['det_exacta']}/{d['n']} | {d['det_tolerante']}/{d['n']} | "
                 f"{d['tipo_equivocado']}/{d['tipo_equivocado_tol']} | {_fmt(d['recall'])} | "
                 f"{_fmt(d['recall_critico'])} | {_fmt(d['t_medio'], False)} | {_fmt(d['t_p95'], False)} |")
    return "\n".join(L)


def comparar_campos(base: list[Registro], cand: list[Registro]) -> list[dict]:
    """Por (tipo, campo): aciertos base vs candidato, solo sobre muestras comunes."""
    b = {r.sample_id: r for r in base}
    filas: dict[tuple[str, str], list[float]] = {}
    for r in cand:
        rb = b.get(r.sample_id)
        if rb is None:
            continue
        for nom, sc in r.campos.items():
            sb = rb.campos.get(nom, 0.0)
            f = filas.setdefault((r.tipo_ocr, nom), [0.0, 0.0, 0.0, 0.0])
            f[0] += sb
            f[1] += sc
            f[2] += 1
            if sc < sb:
                f[3] += 1  # documentos donde el candidato pierde algo que el baseline tenia
    return [{"tipo": t, "campo": n, "critico": n in CRITICOS, "base": f[0], "cand": f[1],
             "n": int(f[2]), "docs_regresion": int(f[3])} for (t, n), f in sorted(filas.items())]


# ── Carga de campana y seleccion de muestras ─────────────────────────────────

def cargar_manifest(campana: Path) -> list[dict]:
    return [json.loads(l) for l in (campana / "manifest.jsonl").read_text().splitlines() if l.strip()]


def seleccionar(manifest: list[dict], split: str, por_tipo: Optional[int],
                preset: Optional[str] = None, tipos: Optional[list[str]] = None) -> list[dict]:
    """Determinista: por tipo, ordena por sample_id y reparte los presets en round-robin."""
    sel: list[dict] = []
    for dt in sorted(GEN_TO_OCR):
        if tipos and GEN_TO_OCR[dt] not in tipos:
            continue
        xs = sorted((m for m in manifest if m["doc_type"] == dt and m["split"] == split
                     and (preset is None or m["degradation_preset"] == preset)),
                    key=lambda m: m["sample_id"])
        if por_tipo is None:
            sel.extend(xs)
            continue
        cola = {p: [m for m in xs if m["degradation_preset"] == p] for p in ("none", "default", "heavy")}
        elegidos: list[dict] = []
        while len(elegidos) < por_tipo and any(cola.values()):
            for p in ("none", "default", "heavy"):
                if cola[p] and len(elegidos) < por_tipo:
                    elegidos.append(cola[p].pop(0))
        sel.extend(elegidos)
    return sel


def detectar(texto: str) -> tuple[Optional[str], Optional[str]]:
    """(tipo exacto, tipo con clasificador tolerante) sobre un texto. Sin texto -> (None, None)."""
    if not texto.strip():
        return None, None
    from infrastructure.ocr import docling_adapter as da
    ad = da.DoclingAdapter.__new__(da.DoclingAdapter)
    previo = da.CLASSIFIER_TOLERANT
    try:
        da.CLASSIFIER_TOLERANT = False
        exacto = ad.detectar_tipo(texto).value
        da.CLASSIFIER_TOLERANT = True
        tol = ad.detectar_tipo(texto).value
    finally:
        da.CLASSIFIER_TOLERANT = previo
    return exacto, tol


def construir_registro(m: dict, texto: str, segundos: Optional[float], campana: Path,
                       tipo_detectado: Optional[str] = None) -> Registro:
    tipo = GEN_TO_OCR[m["doc_type"]]
    gt = json.loads((campana / m["ground_truth_path"]).read_text())
    texto = limpiar_texto(texto)
    exacto, tol = detectar(texto)
    if tipo_detectado is not None and texto.strip():
        exacto = tipo_detectado  # modo adapter: el tipo que devolvio el adaptador
    cache: dict = {}
    campos = {e.nombre: puntuar(e, texto, cache) for e in esperados(gt, tipo)}
    return Registro(m["sample_id"], tipo, m["degradation_preset"], m["split"], texto, exacto, tol,
                    segundos, campos, mrz_contiguo(gt, texto) if tipo == "pasaporte" else None)


def texto_baseline(campana: Path, sample_id: str) -> str:
    p = campana / "ocr_data" / "output" / "raw_text" / f"{sample_id}.md"
    return p.read_text() if p.exists() else ""


def tiempos_baseline(campana: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    pj = campana / "predictions.jsonl"
    if pj.exists():
        for l in pj.read_text().splitlines():
            if not l.strip():
                continue
            d = json.loads(l)
            ms = (d.get("timings") or {}).get("stages_ms", {}).get("ocr_ms")
            if ms is not None:
                out[d["sample_id"]] = ms / 1000
    return out


def registros_baseline(campana: Path, muestras: list[dict]) -> list[Registro]:
    tb = tiempos_baseline(campana)
    return [construir_registro(m, texto_baseline(campana, m["sample_id"]), tb.get(m["sample_id"]), campana)
            for m in muestras]


# ── Corridas ─────────────────────────────────────────────────────────────────

def _leer_jsonl(p: Path) -> dict[str, dict]:
    if not p.exists():
        return {}
    out = {}
    for l in p.read_text().splitlines():
        if l.strip():
            d = json.loads(l)
            out[d["sample_id"]] = d
    return out


def correr(args: argparse.Namespace) -> int:
    for kv in args.env or []:
        k, _, v = kv.partition("=")
        os.environ[k] = v
    sys.path.insert(0, str(ROOT))
    from domain.entities.documento import DocumentoIdentidad
    from infrastructure.ocr.docling_adapter import DoclingAdapter

    campana = Path(args.campaign)
    muestras = seleccionar(cargar_manifest(campana), args.split, args.per_type, args.preset, args.tipo)
    if args.fallos:  # solo las muestras que la campana base dejo como error (DOCUMENTO_NO_SOPORTADO)
        ok = set(tiempos_baseline(campana))
        muestras = [m for m in seleccionar(cargar_manifest(campana), args.split, None, args.preset, args.tipo)
                    if m["sample_id"] not in ok]
    out_dir = Path(args.out) / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    destino = out_dir / "run.jsonl"
    hechos = _leer_jsonl(destino)
    ad = DoclingAdapter()
    ad.precargar()
    plat = ad.plataforma
    meta = {"name": args.name, "mode": args.mode, "env": args.env or [], "split": args.split,
            "motor_ocr_docling": plat.get("motor_ocr_docling"), "dispositivo": plat.get("dispositivo"),
            "so": plat.get("sistema")}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"[bench] {args.name}: {len(muestras)} muestras ({len(hechos)} ya hechas) "
          f"motor={meta['motor_ocr_docling']} dispositivo={meta['dispositivo']} modo={args.mode}", flush=True)
    with destino.open("a") as fh:
        for i, m in enumerate(muestras, 1):
            if m["sample_id"] in hechos:
                continue
            pdf = campana / m["submitted_pdf_path"]
            t0 = time.perf_counter()
            tipo = None
            variantes: dict[str, str] = {}
            if args.mode == "docling":
                # Una sola conversion; se exportan las tres variantes de texto (mismo tiempo de Docling)
                from infrastructure.ocr.docling_text import exportar
                try:
                    doc_d = ad._converter.convert(str(pdf)).document
                    for nom, (tp, modo) in {"markdown": (False, "markdown"), "traverse": (True, "markdown"),
                                            "filas": (False, "filas")}.items():
                        variantes[nom] = ad._normalizar(exportar(doc_d, tp, modo))
                except Exception as e:  # conversion fallida: texto vacio (como el adaptador)
                    print(f"[bench] conversion fallida: {type(e).__name__}", flush=True)
                    variantes = {"markdown": "", "traverse": "", "filas": ""}
                texto = variantes["markdown"]
            else:
                doc = DocumentoIdentidad(ruta_pdf=pdf, user_id="bench", doc_id=m["sample_id"])
                te = ad.extraer_texto(doc)
                texto, tipo = te.texto_crudo, te.tipo_detectado.value
            dt = time.perf_counter() - t0
            fh.write(json.dumps({"sample_id": m["sample_id"], "texto": texto, "tipo": tipo,
                                 "variantes": variantes, "segundos": round(dt, 3)}, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"[bench] {i}/{len(muestras)} {GEN_TO_OCR[m['doc_type']]} {m['degradation_preset']} {dt:.1f}s",
                  flush=True)
    return 0


def _valor(v: str) -> Any:
    if v.lower() in {"1", "true"}:
        return True
    if v.lower() in {"0", "false"}:
        return False
    return v


def correr_ab(args: argparse.Namespace) -> int:
    """A/B intercalado en el MISMO proceso y por documento (tiempos comparables). Solo flags a
    nivel adaptador/exportacion (constantes de infrastructure.ocr.docling_adapter), p. ej.
    --ab filas=DOCLING_TEXT_MODE:filas --ab trav=DOCLING_TRAVERSE_PICTURES:1.
    La variante `ctl` (sin cambios) siempre se corre."""
    sys.path.insert(0, str(ROOT))
    from domain.entities.documento import DocumentoIdentidad
    from infrastructure.ocr import docling_adapter as da

    variantes: dict[str, dict[str, Any]] = {"ctl": {}}
    for spec in args.ab:
        nombre, _, kvs = spec.partition("=")
        variantes[nombre] = {kv.split(":")[0]: _valor(kv.split(":", 1)[1]) for kv in kvs.split(",")}
    campana = Path(args.campaign)
    muestras = seleccionar(cargar_manifest(campana), args.split, args.per_type, args.preset, args.tipo)
    if args.fallos:
        ok = set(tiempos_baseline(campana))
        muestras = [m for m in seleccionar(cargar_manifest(campana), args.split, None, args.preset, args.tipo)
                    if m["sample_id"] not in ok]
    ad = da.DoclingAdapter()
    ad.precargar()
    plat = ad.plataforma
    fhs, hechos = {}, {}
    for nombre, cambios in variantes.items():
        d = Path(args.out) / f"{args.name}_{nombre}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({
            "name": f"{args.name}_{nombre}", "mode": "adapter(ab)", "env": [f"{k}={v}" for k, v in cambios.items()],
            "split": args.split, "motor_ocr_docling": plat.get("motor_ocr_docling"),
            "dispositivo": plat.get("dispositivo"), "so": plat.get("sistema")}, indent=1))
        hechos[nombre] = _leer_jsonl(d / "run.jsonl")
        fhs[nombre] = (d / "run.jsonl").open("a")
    print(f"[ab] {len(muestras)} muestras, variantes={list(variantes)} motor={plat.get('motor_ocr_docling')} "
          f"dispositivo={plat.get('dispositivo')}", flush=True)
    nombres = list(variantes)
    for i, m in enumerate(muestras, 1):
        orden = nombres if i % 2 else nombres[::-1]  # alterna el orden para no sesgar tiempos/cache
        for nombre in orden:
            if m["sample_id"] in hechos[nombre]:
                continue
            previos = {k: getattr(da, k) for k in variantes[nombre]}
            for k, v in variantes[nombre].items():
                setattr(da, k, v)
            try:
                doc = DocumentoIdentidad(ruta_pdf=campana / m["submitted_pdf_path"], user_id="bench",
                                         doc_id=m["sample_id"])
                t0 = time.perf_counter()
                te = ad.extraer_texto(doc)
                dt = time.perf_counter() - t0
            finally:
                for k, v in previos.items():
                    setattr(da, k, v)
            fhs[nombre].write(json.dumps({"sample_id": m["sample_id"], "texto": te.texto_crudo,
                                          "tipo": te.tipo_detectado.value, "notas": te.notas_ocr,
                                          "variantes": {}, "segundos": round(dt, 3)}, ensure_ascii=False) + "\n")
            fhs[nombre].flush()
        print(f"[ab] {i}/{len(muestras)}", flush=True)
    return 0


def registros_de_corrida(campana: Path, muestras: list[dict], run_dir: Path,
                         variante: Optional[str] = None) -> list[Registro]:
    datos = _leer_jsonl(run_dir / "run.jsonl")
    out = []
    for m in muestras:
        d = datos.get(m["sample_id"])
        if d is None:
            continue
        texto = (d.get("variantes") or {}).get(variante, d["texto"]) if variante else d["texto"]
        out.append(construir_registro(m, texto, d["segundos"], campana, d.get("tipo")))
    return out


def informe(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    campana = Path(args.campaign)
    manifest = cargar_manifest(campana)
    lineas: list[str] = []
    for rd in args.runs:
        run_dir = Path(rd)
        meta = json.loads((run_dir / "meta.json").read_text()) if (run_dir / "meta.json").exists() else {}
        muestras = seleccionar(manifest, meta.get("split", args.split), args.per_type)
        cand = registros_de_corrida(campana, muestras, run_dir, args.variante)
        ids = {r.sample_id for r in cand}
        base = registros_baseline(campana, [m for m in muestras if m["sample_id"] in ids])
        lineas.append(f"## {meta.get('name', run_dir.name)} variante={args.variante or 'texto guardado'}  (modo={meta.get('mode')}, split={meta.get('split')}, "
                      f"motor={meta.get('motor_ocr_docling')}, dispositivo={meta.get('dispositivo')}, "
                      f"env={meta.get('env')})\n")
        lineas.append(tabla_markdown("baseline (raw_text guardado de la campana, mismas muestras)",
                                     resumen_por_tipo(base)))
        lineas.append("")
        lineas.append(tabla_markdown("candidato", resumen_por_tipo(cand)))
        lineas.append("")
        lineas.append("| tipo | campo | critico | base | cand | n | docs con regresion |")
        lineas.append("|---|---|---|---|---|---|---|")
        for f in comparar_campos(base, cand):
            if f["base"] != f["cand"] or f["docs_regresion"]:
                lineas.append(f"| {f['tipo']} | {f['campo']} | {'si' if f['critico'] else ''} | {f['base']:.1f} | "
                              f"{f['cand']:.1f} | {f['n']} | {f['docs_regresion']} |")
        lineas.append("")
        if args.json:
            Path(args.json).write_text(json.dumps(
                {"base": resumen_por_tipo(base), "cand": resumen_por_tipo(cand)}, indent=1, default=str))
    print("\n".join(lineas))
    return 0


def baseline(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    campana = Path(args.campaign)
    muestras = seleccionar(cargar_manifest(campana), args.split, args.per_type)
    regs = registros_baseline(campana, muestras)
    res = resumen_por_tipo(regs)
    print(tabla_markdown(f"baseline {args.split} (n={len(regs)})", res))
    if args.campos:
        for t, d in res.items():
            if t != "TOTAL":
                print(f"\n{t}: " + ", ".join(f"{k}={100 * v[0]:.0f}%" for k, v in d["por_campo"].items()))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign", default=str(DEFAULT_CAMPAIGN))
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--split", default="dev")
    b.add_argument("--per-type", type=int, default=None)
    b.add_argument("--campos", action="store_true")
    b.set_defaults(fn=baseline)
    r = sub.add_parser("run")
    r.add_argument("--name", required=True)
    r.add_argument("--split", default="dev")
    r.add_argument("--per-type", type=int, default=8)
    r.add_argument("--preset", default=None, choices=["none", "default", "heavy"])
    r.add_argument("--mode", choices=["docling", "adapter"], default="docling")
    r.add_argument("--tipo", action="append", choices=sorted(GEN_TO_OCR.values()),
                   help="limitar a estos tipos OCR (repetible)")
    r.add_argument("--fallos", action="store_true", help="solo muestras que fallaron en la campana base")
    r.add_argument("--env", action="append")
    r.add_argument("--out", default=str(ROOT.parent / "bench_runs"))
    r.set_defaults(fn=correr)
    a = sub.add_parser("ab", help="A/B intercalado de flags de adaptador (tiempos comparables)")
    a.add_argument("--name", required=True)
    a.add_argument("--split", default="dev")
    a.add_argument("--per-type", type=int, default=None)
    a.add_argument("--preset", default=None, choices=["none", "default", "heavy"])
    a.add_argument("--tipo", action="append", choices=sorted(GEN_TO_OCR.values()))
    a.add_argument("--fallos", action="store_true")
    a.add_argument("--ab", action="append", required=True, help="nombre=FLAG:valor[,FLAG:valor]")
    a.add_argument("--out", default=str(ROOT.parent / "bench_runs"))
    a.set_defaults(fn=correr_ab)
    p = sub.add_parser("report")
    p.add_argument("runs", nargs="+")
    p.add_argument("--split", default="dev")
    p.add_argument("--per-type", type=int, default=None)
    p.add_argument("--json", default=None)
    p.add_argument("--variante", default=None, choices=["markdown", "traverse", "filas"])
    p.set_defaults(fn=informe)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
