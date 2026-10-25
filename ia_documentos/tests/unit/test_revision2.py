"""Segunda revision: robustez y falsos positivos de reconcile, clasificador tolerante y OCR de franja del acta."""
import random
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from application import pipeline as pipeline_mod
from application.reconcile import reconcile
from domain.entities.documento import DocumentoIdentidad, TipoDocumento
from infrastructure.ocr import docling_adapter as da
from infrastructure.ocr.clasificador_tolerante import clasificar_tolerante
from infrastructure.regex.correccion_ocr import digito_verificador_curp


def _curp(p17: str) -> str:
    return p17 + digito_verificador_curp(p17)


# ── Robustez: reconcile jamas lanza ──────────────────────────────────────────

@pytest.mark.parametrize("tipo", ["ine", "curp", "pasaporte", "acta_nacimiento", None, "", 5, "otro"])
@pytest.mark.parametrize("raw", [None, "", b"bytes \xff", 123, [], "x" * 50])
@pytest.mark.parametrize("datos", [None, {}, "str", {"curp": 123, "mrz_linea2": ["x"], "sexo": {}}])
def test_reconcile_nunca_lanza(tipo, raw, datos):
    r = reconcile(tipo, raw, datos, permitir_derivados=True)
    assert isinstance(r.datos, dict) and isinstance(r.cambios, list)


@pytest.mark.parametrize("tipo", ["ine", "curp", "pasaporte", "acta_nacimiento"])
def test_reconcile_texto_largo_acotado(tipo):
    rng = random.Random(3)
    ruido = "".join(rng.choice("OIOSB0123 \n<ABCDHM") for _ in range(200_000))
    t0 = time.time()
    r = reconcile(tipo, ruido, {"curp": "x", "nombres": "A", "primer_apellido": "B"}, permitir_derivados=True)
    assert time.time() - t0 < 10
    assert isinstance(r.datos, dict)


def test_reconcile_regla_que_falla_se_registra_y_no_rompe(monkeypatch, caplog):
    from application import reconcile as rec

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(rec, "_regla_acta", boom)
    with caplog.at_level("WARNING"):
        r = reconcile("acta_nacimiento", "texto", {"curp": None})
    assert r.datos == {"curp": None}
    assert any(rec_.msg == "reconcile_regla_fallo" for rec_ in caplog.records)


# ── Falsos positivos corregidos ──────────────────────────────────────────────

CURP_GUERRERO = _curp("GUHD900101HDFRDS0")   # GUERRERO HIDALGO ... (pos. 13 = R, pos. 14 = D)


def test_apellido_homonimo_de_entidad_se_conserva_si_la_curp_lo_confirma():
    d = {"curp": CURP_GUERRERO, "nombres": "ESTEBAN", "primer_apellido": "GUERRERO",
         "segundo_apellido": "HIDALGO", "nombre_completo": "ESTEBAN GUERRERO HIDALGO"}
    r = reconcile("ine", f"GUERRERO\nHIDALGO\nESTEBAN\n{CURP_GUERRERO}", d)
    assert r.datos["primer_apellido"] == "GUERRERO" and r.datos["segundo_apellido"] == "HIDALGO"


def test_entidad_como_apellido_sin_confirmacion_de_curp_sigue_siendo_basura():
    d = {"curp": CURP_GUERRERO, "nombres": "ESTEBAN", "primer_apellido": "GUERRERO", "segundo_apellido": "PUEBLA"}
    r = reconcile("ine", "texto", d)
    assert r.datos["segundo_apellido"] is None


def test_nombre_completo_con_segundo_nombre_no_se_trunca():
    curp = _curp("GAJL900101MDFRPR0")
    d = {"curp": curp, "nombres": "JOSE", "primer_apellido": "GARCIA", "segundo_apellido": "LOPEZ",
         "nombre_completo": "JOSE LUIS GARCIA LOPEZ"}
    assert reconcile("ine", "x", d).datos["nombre_completo"] == "JOSE LUIS GARCIA LOPEZ"


def test_nombre_completo_con_basura_se_sigue_corrigiendo():
    curp = _curp("COCD630101MDFRRL0")
    d = {"curp": curp, "nombres": "DULCE", "primer_apellido": "CORTES", "segundo_apellido": "CORREA",
         "nombre_completo": "MINARE CORTES CORREA DULCE"}
    assert reconcile("ine", "x", d).datos["nombre_completo"] == "DULCE CORTES CORREA"


def test_acta_dos_madres_no_anula_curp_de_progenitor():
    c1, c2 = _curp("PELA700101MDFRPN0"), _curp("LODA700101MDFPZN0")
    raw = (f"Datos de Filiación\nANA Nombre(s)\nPEREZ Primer Apellido\nLOPEZ Segundo Apellido\n"
           f"MEXICANA Nacionalidad\n{c1} CURP\nSOFIA Nombre(s)\nLOPEZ Primer Apellido\nDIAZ Segundo Apellido\n"
           f"MEXICANA Nacionalidad\n{c2} CURP\nAnotaciones Marginales")
    r = reconcile("acta_nacimiento", raw, {"curp_padre": c1, "curp_madre": c2})
    assert r.datos["curp_padre"] == c1 and r.datos["curp_madre"] == c2


def test_acta_diseno_etiqueta_primero_no_toma_el_valor_de_otro_campo():
    raw = ("Acta de Nacimiento\nDatos de la Persona Registrada\nNombre(s): ROSA MARIA\nPrimer Apellido: PEREZ\n"
           "Segundo Apellido: LOPEZ\nSexo: MUJER\nDatos de Filiación\nNombre(s): JUAN\nPrimer Apellido: PEREZ\n"
           "Segundo Apellido: RUIZ\nNacionalidad: MEXICANA\nNombre(s): ANA\nPrimer Apellido: LOPEZ\n"
           "Segundo Apellido: DIAZ\nNacionalidad: MEXICANA\nAnotaciones Marginales")
    d = {"nombres": "ROSA MARIA", "primer_apellido": "PEREZ", "segundo_apellido": "LOPEZ"}
    r = reconcile("acta_nacimiento", raw, d, permitir_derivados=True)
    assert (r.datos["nombres"], r.datos["primer_apellido"], r.datos["segundo_apellido"]) == ("ROSA MARIA", "PEREZ", "LOPEZ")
    assert r.datos.get("nacionalidad_padre") in (None, "")


def test_folio_impresion_por_etiqueta_solo_con_candidato_unico():
    raw = "Folio de Impresión\n70345840\nIdentificador Electrónico\n32036007219427516258"
    assert reconcile("acta_nacimiento", raw, {}).datos["folio_impresion"] == "70345840"
    assert reconcile("acta_nacimiento", raw, {"folio_impresion": "12345678"}).datos["folio_impresion"] == "12345678"
    ambiguo = "Folio de Impresión 70345840 11112222"
    assert reconcile("acta_nacimiento", ambiguo, {}).datos.get("folio_impresion") is None


# ── Pipeline: guardas de reconcile ───────────────────────────────────────────

def test_pipeline_reconciliar_revierte_valor_no_literal_en_campo_protegido(monkeypatch):
    from application.reconcile import Cambio, ReconcileResult
    monkeypatch.setattr(pipeline_mod, "ANTI_HALLUCINATION", True)
    fake = lambda *a, **k: ReconcileResult({"folio": "99999999"}, [Cambio("folio", None, "99999999", "r")])
    monkeypatch.setattr(pipeline_mod, "reconcile", fake)
    datos, meta = pipeline_mod.DocumentPipeline._reconciliar("curp", "texto sin ese numero", {"folio": None}, "d")
    assert datos["folio"] is None and meta == []


def test_pipeline_reconciliar_acepta_valor_literal_y_no_expone_valores(monkeypatch):
    from application.reconcile import Cambio, ReconcileResult
    fake = lambda *a, **k: ReconcileResult({"folio": "12345678"}, [Cambio("folio", None, "12345678", "r")])
    monkeypatch.setattr(pipeline_mod, "reconcile", fake)
    datos, meta = pipeline_mod.DocumentPipeline._reconciliar("curp", "Folio 12345678", {"folio": None}, "d")
    assert datos["folio"] == "12345678" and meta == [{"campo": "folio", "regla": "r"}]


def test_pipeline_reconciliar_ante_excepcion_devuelve_originales(monkeypatch):
    def boom(*a, **k):
        raise ValueError("x")
    monkeypatch.setattr(pipeline_mod, "reconcile", boom)
    campos = {"curp": "A"}
    datos, meta = pipeline_mod.DocumentPipeline._reconciliar("ine", "t", campos, "d")
    assert datos is campos and meta == []


# ── Clasificador tolerante ───────────────────────────────────────────────────

def test_tolerante_clasifica_texto_con_ruido():
    t = "CREDENC1AL PARA V0TAR\nINSTITUT0 NAC10NAL ELECT0RAL\nCLAVE DE ELECT0R GRSRCL73073132M500"
    assert clasificar_tolerante(t) == TipoDocumento.INE
    mrz = "P<MEXTAMAYO<RAEL<<AMADOR<<<<<<<<<<<<<<<<<<<<\nG442704986MEX5504204M2906058<<<<<<<<<<<<<<08"
    assert clasificar_tolerante(mrz) == TipoDocumento.PASAPORTE
    assert clasificar_tolerante("AC TA DE NAC1MIENTO\nDATOS DE FILIACI0N\nAN0TACIONES MARGINALES") == TipoDocumento.ACTA_NACIMIENTO


@pytest.mark.parametrize("texto", [
    "", "x", "Lorem ipsum dolor sit amet " * 40, "<<<<<<<<<<<<<<<<<<<<<<<<",
    "ESTADOS UNIDOS MEXICANOS " * 3,
    "CERTIFICADO DE ESTUDIOS SECRETARIA DE EDUCACION PUBLICA LOCALIDAD SECCION DOMICILIO",
    "LICENCIA DE CONDUCIR VIGENCIA EMISION DOMICILIO",
    "PASAPORTE ACTA DE NACIMIENTO",  # empate: ni uno ni otro
])
def test_tolerante_prefiere_no_clasificar_a_equivocarse(texto):
    assert clasificar_tolerante(texto) is None


def test_detectar_tipo_flag_apagado_conserva_comportamiento(monkeypatch):
    monkeypatch.setattr(da, "CLASSIFIER_TOLERANT", False)
    ad = da.DoclingAdapter()
    assert ad.detectar_tipo("CREDENC1AL PARA V0TAR INSTITUT0 NAC10NAL ELECT0RAL") == TipoDocumento.DESCONOCIDO


def test_detectar_tipo_flag_encendido_recupera_y_no_altera_lo_exacto(monkeypatch):
    monkeypatch.setattr(da, "CLASSIFIER_TOLERANT", True)
    ad = da.DoclingAdapter()
    assert ad.detectar_tipo("CREDENC1AL PARA V0TAR INSTITUT0 NAC10NAL ELECT0RAL CLAVE DE ELECT0R") == TipoDocumento.INE
    assert ad.detectar_tipo("INSTITUTO NACIONAL ELECTORAL\nCREDENCIAL PARA VOTAR") == TipoDocumento.INE
    assert ad.detectar_tipo("texto cualquiera sin nada") == TipoDocumento.DESCONOCIDO


# ── OCR de la franja superior del acta ───────────────────────────────────────

def _extraer(monkeypatch, flag, zona):
    monkeypatch.setattr(da, "OCR_ACTA_ZONA_SUPERIOR", flag)
    ad = da.DoclingAdapter()
    cuerpo = "ACTA DE NACIMIENTO REGISTRO CIVIL " + "texto de relleno " * 30
    monkeypatch.setattr(ad, "_extraer_con_docling", lambda ruta: cuerpo)
    llamadas = []
    monkeypatch.setattr(ad, "_ocr_zona", lambda *a, **k: (llamadas.append(k), zona)[1])
    doc = DocumentoIdentidad(ruta_pdf=Path("x.pdf"), user_id="u", doc_id="d")
    return ad.extraer_texto(doc), llamadas


def test_acta_franja_superior_se_anade_solo_con_flag(monkeypatch):
    t, llamadas = _extraer(monkeypatch, True, "Folio de Impresión\n70345840")
    assert "70345840" in t.texto_crudo and t.tipo_detectado == TipoDocumento.ACTA_NACIMIENTO
    assert llamadas and llamadas[0]["bottom_pct"] == 0.12
    t2, llamadas2 = _extraer(monkeypatch, False, "Folio de Impresión\n70345840")
    assert "70345840" not in t2.texto_crudo and not llamadas2
