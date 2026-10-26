"""
tests/unit/test_docling_tuning.py
Ajustes opcionales de Docling (flags apagados por defecto), exportacion de texto, reintento
de Docling y metricas del harness scripts/docling_bench.py. Sin cargar Docling real.
"""
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain.entities.documento import DocumentoIdentidad, TipoDocumento
from infrastructure import platform_config as pc
from infrastructure.ocr import docling_adapter as da
from infrastructure.ocr.docling_text import exportar, texto_por_filas


# ── docling_text ─────────────────────────────────────────────────────────────

class _Doc:
    def __init__(self, texts=(), md="MD"):
        self.texts = list(texts)
        self._md = md
        self.llamadas = []
        self.pages = {1: SimpleNamespace(size=SimpleNamespace(height=100.0))}

    def export_to_markdown(self, **kw):
        self.llamadas.append(kw)
        return self._md


def _item(txt, l, t, r, b, page=1, origen="TOPLEFT"):
    bb = SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=SimpleNamespace(value=origen))
    bb.to_top_left_origin = lambda page_height: SimpleNamespace(l=l, t=page_height - t, r=r, b=page_height - b)
    return SimpleNamespace(text=txt, prov=[SimpleNamespace(page_no=page, bbox=bb)])


def test_exportar_defecto_es_markdown_sin_argumentos():
    d = _Doc()
    assert exportar(d) == "MD"
    assert d.llamadas == [{}]  # export_to_markdown() sin kwargs: identico al comportamiento historico


def test_exportar_traverse_pictures():
    d = _Doc()
    exportar(d, traverse_pictures=True)
    assert d.llamadas == [{"traverse_pictures": True}]


def test_filas_agrupa_por_renglon_y_ordena_izquierda_derecha():
    d = _Doc([
        _item("valor", 50, 10, 90, 18), _item("etiqueta", 0, 11, 40, 19),
        _item("otra", 0, 40, 40, 48),
    ])
    assert exportar(d, modo="filas") == "etiqueta valor\notra"


def test_filas_origen_abajo_izquierda_se_convierte():
    # En BOTTOMLEFT un t mayor esta MAS arriba: "arriba" debe salir primero
    d = _Doc([_item("abajo", 0, 20, 30, 12, origen="BOTTOMLEFT"), _item("arriba", 0, 90, 30, 82, origen="BOTTOMLEFT")])
    assert texto_por_filas(d).split("\n") == ["arriba", "abajo"]


def test_filas_incluye_items_sin_posicion_y_omite_vacios():
    d = _Doc([_item("a", 0, 0, 5, 5), SimpleNamespace(text="sin pos", prov=[]), _item("  ", 0, 9, 5, 12)])
    assert texto_por_filas(d).split("\n")[-1] == "sin pos"
    assert "  " not in texto_por_filas(d)


# ── platform_config: ajustes opcionales ──────────────────────────────────────

class _Modo:
    FULL_PAGE = "FP"
    LAYOUT_REGIONS = "LR"


@pytest.fixture
def po_falso(monkeypatch):
    mod = types.ModuleType("docling.datamodel.pipeline_options")
    mod.OcrMode = _Modo
    for nombre, valor in {"docling": types.ModuleType("docling"),
                          "docling.datamodel": types.ModuleType("docling.datamodel")}.items():
        monkeypatch.setitem(sys.modules, nombre, valor)
    monkeypatch.setitem(sys.modules, "docling.datamodel.pipeline_options", mod)


def _ajustes(monkeypatch, **kw):
    base = dict(DOCLING_OCR_MODE="", DOCLING_OCR_SCALE=0.0, DOCLING_OCR_RECOGNITION="", DOCLING_OCR_LANGS="",
                DOCLING_IMAGES_SCALE=0.0, DOCLING_RETRY_OCR_MODE="full_page", DOCLING_RETRY_OCR_SCALE=0.0)
    base.update(kw)
    for k, v in base.items():
        monkeypatch.setattr(pc.settings, k, v)


def test_ajustes_por_defecto_no_tocan_nada(monkeypatch, po_falso):
    _ajustes(monkeypatch)
    ocr = SimpleNamespace(mode="orig", scale=3.0, recognition="accurate", lang=["es-ES"])
    opc = SimpleNamespace(images_scale=1.0)
    assert pc._aplicar_ajustes_docling(opc, ocr) == []
    assert (ocr.mode, ocr.scale, ocr.recognition, ocr.lang, opc.images_scale) == ("orig", 3.0, "accurate", ["es-ES"], 1.0)


def test_ajustes_aplican_cada_flag(monkeypatch, po_falso):
    _ajustes(monkeypatch, DOCLING_OCR_MODE="full_page", DOCLING_OCR_SCALE=4.0, DOCLING_OCR_RECOGNITION="fast",
             DOCLING_OCR_LANGS="en-US, es-ES", DOCLING_IMAGES_SCALE=2.0)
    ocr = SimpleNamespace(mode="orig", scale=3.0, recognition="accurate", lang=["es-ES"])
    opc = SimpleNamespace(images_scale=1.0)
    aplicados = pc._aplicar_ajustes_docling(opc, ocr)
    assert (ocr.mode, ocr.scale, ocr.recognition, ocr.lang, opc.images_scale) == ("FP", 4.0, "fast", ["en-US", "es-ES"], 2.0)
    assert len(aplicados) == 5


def test_ajustes_retry_usa_modo_de_reintento(monkeypatch, po_falso):
    _ajustes(monkeypatch, DOCLING_OCR_MODE="layout_regions", DOCLING_RETRY_OCR_MODE="full_page")
    ocr = SimpleNamespace(mode="orig")
    pc._aplicar_ajustes_docling(SimpleNamespace(), ocr, retry=True)
    assert ocr.mode == "FP"


def test_ajustes_valor_invalido_se_ignora(monkeypatch, po_falso):
    _ajustes(monkeypatch, DOCLING_OCR_MODE="loquesea", DOCLING_OCR_RECOGNITION="turbo")
    ocr = SimpleNamespace(mode="orig", recognition="accurate")
    assert pc._aplicar_ajustes_docling(SimpleNamespace(), ocr) == []


# ── adaptador: reintento de Docling ──────────────────────────────────────────

def _adaptador(monkeypatch, textos, tipo_por_texto):
    ad = da.DoclingAdapter()
    monkeypatch.setattr(ad, "_extraer_con_docling", lambda ruta: textos["primero"])
    monkeypatch.setattr(ad, "_ocr_pagina_completa", lambda *a, **k: None)
    monkeypatch.setattr(ad, "_ocr_zona", lambda *a, **k: None)
    monkeypatch.setattr(ad, "_ocrmac_disponible", lambda: False)
    monkeypatch.setattr(ad, "_pytesseract_available", False)
    monkeypatch.setattr(ad, "_docling_available", True)
    llamadas = []

    def retry(ruta, t, notas):
        llamadas.append(1)
        notas.append("docling_reintento:0.1s")
        return textos.get("retry", "")
    monkeypatch.setattr(ad, "_reintentar_docling", retry)
    monkeypatch.setattr(ad, "detectar_tipo", lambda t: tipo_por_texto(t))
    return ad, llamadas


def _doc():
    return DocumentoIdentidad(ruta_pdf=Path("x.pdf"), user_id="u", doc_id="d")


def _tipo(t):
    return TipoDocumento.PASAPORTE if "PASAPORTE" in t else TipoDocumento.DESCONOCIDO


def test_reintento_apagado_no_se_invoca(monkeypatch):
    monkeypatch.setattr(da, "DOCLING_RETRY_ON_UNKNOWN", False)
    ad, llamadas = _adaptador(monkeypatch, {"primero": "x" * 600, "retry": "PASAPORTE " * 80}, _tipo)
    r = ad.extraer_texto(_doc())
    assert llamadas == [] and r.tipo_detectado == TipoDocumento.DESCONOCIDO and r.notas_ocr == []


def test_reintento_recupera_el_tipo(monkeypatch):
    monkeypatch.setattr(da, "DOCLING_RETRY_ON_UNKNOWN", True)
    ad, llamadas = _adaptador(monkeypatch, {"primero": "x" * 600, "retry": "PASAPORTE " * 80}, _tipo)
    r = ad.extraer_texto(_doc())
    assert llamadas == [1] and r.tipo_detectado == TipoDocumento.PASAPORTE
    assert "docling_reintento:tipo_recuperado" in r.notas_ocr
    assert r.texto_crudo.startswith("PASAPORTE")


def test_reintento_sin_efecto_conserva_texto_original(monkeypatch):
    monkeypatch.setattr(da, "DOCLING_RETRY_ON_UNKNOWN", True)
    ad, llamadas = _adaptador(monkeypatch, {"primero": "y" * 600, "retry": "z" * 600}, _tipo)
    r = ad.extraer_texto(_doc())
    assert r.texto_crudo == "y" * 600 and "docling_reintento:sin_efecto" in r.notas_ocr


def test_reintento_no_se_lanza_si_el_tipo_ya_se_detecto(monkeypatch):
    monkeypatch.setattr(da, "DOCLING_RETRY_ON_UNKNOWN", True)
    ad, llamadas = _adaptador(monkeypatch, {"primero": "PASAPORTE " * 80}, _tipo)
    ad.extraer_texto(_doc())
    assert llamadas == []


def test_reintentar_docling_respeta_tope_de_tiempo(monkeypatch):
    monkeypatch.setattr(da, "DOCLING_RETRY_MAX_SECONDS", 5.0)
    ad = da.DoclingAdapter()
    notas: list[str] = []
    assert ad._reintentar_docling(Path("x.pdf"), 6.0, notas) == ""
    assert notas == ["docling_reintento:omitido_por_tiempo"]


def test_reintentar_docling_convierte_con_segundo_conversor(monkeypatch):
    monkeypatch.setattr(da, "DOCLING_RETRY_MAX_SECONDS", 40.0)
    ad = da.DoclingAdapter()
    doc = _Doc(md="Hola   mundo")
    ad._converter_retry = SimpleNamespace(convert=lambda p: SimpleNamespace(document=doc))
    notas: list[str] = []
    assert ad._reintentar_docling(Path("x.pdf"), 1.0, notas) == "Hola mundo"
    assert doc.llamadas == [{"traverse_pictures": True}]  # DOCLING_RETRY_TRAVERSE_PICTURES=True por defecto


# ── harness docling_bench ────────────────────────────────────────────────────

def _bench():
    ruta = Path(__file__).resolve().parents[2] / "scripts" / "docling_bench.py"
    spec = importlib.util.spec_from_file_location("docling_bench_t", ruta)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["docling_bench_t"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_bench_limpiar_texto_quita_frontmatter_y_entidades():
    b = _bench()
    assert b.limpiar_texto("---\ndoc_id: x\n---\n\nP&lt;MEX") == "\n\nP<MEX"


def test_bench_id_exacto_sin_espacios_y_sin_confundir_caracteres():
    b = _bench()
    e = b.Esperado("curp", "id", ["POZA710906HJCRLL67"], True)
    assert b.puntuar(e, "CURP POZA7109 06HJCRLL67 fin", {}) == 1.0
    assert b.puntuar(e, "CURP P0ZA710906HJCRLL67", {}) == 0.0  # O vs 0 no se corrige


def test_bench_texto_sin_acentos_y_mayusculas():
    b = _bench()
    e = b.Esperado("lugar", "text", ["MÉXICO CITY"], False)
    assert b.puntuar(e, "mexico   city", {}) == 1.0
    assert b.puntuar(e, "mexico", {}) == 0.0


def test_bench_code_requiere_token_completo():
    b = _bench()
    e = b.Esperado("seccion", "code", ["4441"], False)
    assert b.puntuar(e, "SECCION 4441", {}) == 1.0
    assert b.puntuar(e, "SECCION 14441", {}) == 0.0


def test_bench_fecha_acepta_separadores_pero_no_otra_fecha():
    b = _bench()
    e = b.Esperado("f", "date", ["06 09 1971"], False)
    assert b.puntuar(e, "nac 06/09/1971", {}) == 1.0
    assert b.puntuar(e, "nac 06/09/1972", {}) == 0.0
    assert b.puntuar(e, "x106 09 1971", {}) == 0.0


def test_bench_lines_es_fraccion():
    b = _bench()
    e = b.Esperado("domicilio", "lines", ["CALLE UNO 1", "COLONIA DOS"], False)
    assert b.puntuar(e, "calle uno 1", {}) == 0.5


def test_bench_mrz_contiguo():
    b = _bench()
    gt = {"pages": [{"fields": [{"name": "mrz", "value": ["P<MEXAAA<<B", "123456<<"]}]}]}
    assert b.mrz_contiguo(gt, "P<MEXAAA<<B\n123456<<") is True
    assert b.mrz_contiguo(gt, "P<MEXAAA<<B\nruido\n123456<<") is False


def test_bench_esperados_ine_lineas_y_vacios():
    b = _bench()
    gt = {"pages": [{"fields": [{"name": "nombre", "value": ["A1", "A2", "N"]}, {"name": "curp", "value": "X"},
                                {"name": "domicilio", "value": ["L1", "L2"]}, {"name": "clave_elector", "value": ""}]}]}
    nombres = {e.nombre: e for e in b.esperados(gt, "ine")}
    assert nombres["nombres"].valores == ["N"] and nombres["curp"].critico
    assert "clave_elector" not in nombres and nombres["domicilio"].valores == ["L1", "L2"]


def test_bench_seleccion_balanceada_y_determinista():
    b = _bench()
    man = [{"sample_id": f"{p}{i}", "doc_type": "curp_2019", "split": "dev", "degradation_preset": p}
           for p in ("none", "default", "heavy") for i in range(3)]
    s1 = b.seleccionar(man, "dev", 4)
    assert [m["degradation_preset"] for m in s1] == ["none", "default", "heavy", "none"]
    assert s1 == b.seleccionar(man, "dev", 4)


def test_bench_p95_y_resumen():
    b = _bench()
    assert b.p95([1, 2, 3, 4, 100]) == 100 and b.p95([]) is None
    r = b.Registro("s", "curp", "none", "dev", "t", "curp", "curp", 2.0, {"curp": 1.0, "folio": 0.0})
    r2 = b.Registro("s2", "curp", "none", "dev", "", None, None, 4.0, {"curp": 0.0, "folio": 0.0})
    res = b.resumen_por_tipo([r, r2])["curp"]
    assert res["det_exacta"] == 1 and res["tipo_equivocado"] == 0
    assert res["recall"] == 0.25 and res["recall_critico"] == 0.25 and res["t_medio"] == 3.0
