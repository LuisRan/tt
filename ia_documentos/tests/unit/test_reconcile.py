"""Pruebas de application/reconcile.py con textos sintéticos escritos a mano."""
from types import SimpleNamespace

import pytest

from application import pipeline as pipeline_mod
from application.reconcile import reconcile
from domain.entities.documento import TextoExtraido, TipoDocumento
from infrastructure.regex.correccion_ocr import curp_valida, digito_verificador_curp


def _curp(prefijo17: str) -> str:
    return prefijo17 + digito_verificador_curp(prefijo17)


# CURPs sintéticas con dígito verificador calculado
CURP_PERSONA = _curp("ROMT570701MHGDZN0")      # 01/07/1957, mujer
CURP_PADRE = _curp("VAVG150124HTCLRR1")        # hombre
CURP_MADRE = _curp("CUIA150205MHGRRD7")        # mujer


def test_curp_sintetica_valida():
    for c in (CURP_PERSONA, CURP_PADRE, CURP_MADRE):
        assert curp_valida(c)


# ── Acta de nacimiento ────────────────────────────────────────────────────────

ACTA = f"""
Clave Única de Registro de Población {CURP_PERSONA}
Entidad de Registro
ZACATECAS
| MEZA Segundo Apellido ZACATECAS Lugar de Nacimiento TANIA Nombre(s) MUJER Sexo RODRIQUEZ Primer Apellido 01/07/1957 Fecha de Nacimiento Datos de Filiación de la Persona Registrada |
| GREGORIO Nombre(s) ADELA Nombre(s) DEL VALLE Primer Apellido CURIEL Primer Apellido VARGAS Segundo Apellido IRIZARRY Segundo Apellido MEXICANA Nacionalidad MEXICANA Nacionalidad {CURP_PADRE} CURP {CURP_MADRE} CURP |
Anotaciones Marginales: ninguna
Identificador Electrónico 32036007219427516258
"""


def test_acta_reasigna_por_etiqueta_y_curp_de_progenitor():
    datos = {
        "nombres": "TANIA", "primer_apellido": "RODRIQUEZ", "segundo_apellido": "ZACATECAS",
        "nombre_completo": "TANIA RODRIQUEZ", "curp": CURP_PADRE, "fecha_nacimiento": "01/07/1957",
        "sexo": "M", "lugar_nacimiento": "Código QR", "identificador_electronico": CURP_PERSONA,
        "nombre_madre": "ADELA", "curp_madre": CURP_PADRE, "curp_padre": None,
    }
    original = dict(datos)
    res = reconcile("acta_nacimiento", ACTA, datos)
    assert datos == original  # no muta la entrada
    d = res.datos
    assert d["segundo_apellido"] == "MEZA"
    assert d["lugar_nacimiento"] == "ZACATECAS"
    assert d["curp"] == CURP_PERSONA
    assert d["curp_padre"] == CURP_PADRE and d["curp_madre"] == CURP_MADRE
    assert d["nombre_madre"] == "ADELA CURIEL IRIZARRY"
    assert d["nombre_padre"] == "GREGORIO DEL VALLE VARGAS"
    assert d["nombre_completo"] == "TANIA RODRIQUEZ MEZA"
    assert d["identificador_electronico"] == "32036007219427516258"
    assert {c.campo for c in res.cambios} >= {"segundo_apellido", "curp", "curp_madre"}
    assert all(c.regla for c in res.cambios)


def test_acta_no_toca_valores_correctos():
    datos = {"nombres": "TANIA", "primer_apellido": "RODRIQUEZ", "segundo_apellido": "MEZA",
             "curp": CURP_PERSONA, "lugar_nacimiento": "ZACATECAS", "sexo": "M",
             "fecha_nacimiento": "01/07/1957"}
    res = reconcile("acta_nacimiento", ACTA, datos)
    for k, v in datos.items():
        assert res.datos[k] == v


def test_acta_valores_fuera_del_texto_no_se_inventan():
    texto = "Acta de Nacimiento\nDatos de la Persona Registrada\nDatos de Filiación\nAnotaciones Marginales"
    res = reconcile("acta_nacimiento", texto, {"segundo_apellido": "GARCIA"})
    assert res.datos["segundo_apellido"] == "GARCIA"
    assert res.cambios == []


# ── INE ───────────────────────────────────────────────────────────────────────

def test_ine_nombre_completo_se_ensambla_y_apellido_etiqueta_se_anula():
    raw = f"CREDENCIAL PARA VOTAR\nNOMBRE RODRIQUEZ MEZA TANIA\nCURP {CURP_PERSONA}\n"
    datos = {"nombres": "TANIA", "primer_apellido": "RODRIQUEZ", "segundo_apellido": "MEZA",
             "nombre_completo": "RODRIQUEZ MEZA TANIA", "curp": CURP_PERSONA}
    assert reconcile("ine", raw, datos).datos["nombre_completo"] == "TANIA RODRIQUEZ MEZA"
    sucio = dict(datos, primer_apellido="ESTADO MUNICIPIO SECCION LOCALIDAD", nombre_completo=None)
    res = reconcile("ine", raw, sucio)
    assert res.datos["primer_apellido"] == "RODRIQUEZ"  # recuperado del texto con las letras de la CURP
    sin_curp = reconcile("ine", "NOMBRE", {"primer_apellido": "ESTADO MUNICIPIO SECCION", "nombres": "ANA"})
    assert sin_curp.datos["primer_apellido"] is None
    assert any(c.regla == "apellido_es_etiqueta_o_entidad" for c in sin_curp.cambios)


def test_ine_apellido_entidad_federativa_es_basura():
    res = reconcile("ine", "texto", {"primer_apellido": "ROMO", "segundo_apellido": "ZACATECAS"})
    assert res.datos["segundo_apellido"] is None


def test_ine_curp_faltante_se_recupera_con_confusion_ocr():
    # el OCR leyó 'O' en lugar del '0' del dígito final: se repara con el dígito verificador
    ruidosa = CURP_PERSONA[:-1] + ("O" if CURP_PERSONA[-1] == "0" else CURP_PERSONA[-1])
    raw = f"CURP {ruidosa} AÑO DE REGISTRO"
    res = reconcile("ine", raw, {"curp": None, "fecha_nacimiento": "01/07/1957", "sexo": "M"})
    assert res.datos["curp"] == CURP_PERSONA


def test_ine_curp_inconsistente_con_la_fecha_no_se_acepta():
    raw = f"CURP {CURP_PERSONA}"
    res = reconcile("ine", raw, {"curp": None, "fecha_nacimiento": "02/02/1990"})
    assert res.datos["curp"] is None


def test_ine_clave_elector_se_corrige_con_el_texto_y_la_curp():
    buena = "RDMZTN57070113M281"
    mala = "RDMZTN67070113M281"
    raw = f"CLAVE DE ELECTOR RDMZTNS7070113M281 CURP {CURP_PERSONA}"
    res = reconcile("ine", raw, {"curp": CURP_PERSONA, "clave_elector": mala})
    assert res.datos["clave_elector"] == buena  # fecha y sexo coinciden con la CURP


def test_ine_domicilio_ensamblado():
    res = reconcile("ine", "x", {"domicilio": None, "domicilio_calle": "CALLE 1", "domicilio_colonia": "COL. A 123",
                                 "domicilio_municipio_ciudad": "PUEBLA, PUE."})
    assert res.datos["domicilio"] == "CALLE 1 COL. A 123 PUEBLA, PUE."


def test_ine_componente_nombre_por_pool_y_curp():
    raw = f"CREDENCIAL\nHOMERE\nMEZA\nRODRIQUEZ\nTANIA\nCURP {CURP_PERSONA}\n"
    datos = {"curp": CURP_PERSONA, "primer_apellido": "HOMERE", "segundo_apellido": "MEZA", "nombres": "TANIA"}
    res = reconcile("ine", raw, datos)
    assert res.datos["primer_apellido"] == "RODRIQUEZ"


# ── CURP (constancia) ─────────────────────────────────────────────────────────

def test_curp_folio_unico_junto_a_la_etiqueta():
    raw = "Fecha de inscripción Folio Entidad de registro\n06/01/2008 31905506 PUEBLA\n371284599189948"
    res = reconcile("curp", raw, {"folio": None})
    assert res.datos["folio"] == "31905506"
    ambiguo = reconcile("curp", raw + "\nFolio 11112222", {"folio": None})
    assert ambiguo.datos["folio"] is None


# ── Pasaporte ─────────────────────────────────────────────────────────────────

def _mrz2() -> str:
    from application.reconcile import _dv_mrz
    pre = "G44270498" + _dv_mrz("G44270498") + "MEX" + "570701" + _dv_mrz("570701") + "F" + "290605"
    pre += _dv_mrz("290605") + "<" * 14
    pre += _dv_mrz("<" * 14)
    full = pre + _dv_mrz(pre[0:10] + pre[13:20] + pre[21:43])
    assert len(full) == 44
    return full


def test_pasaporte_mrz_sexo_y_nombre_completo():
    l2 = _mrz2()
    l2_sucia = l2[:28] + "<<<<C<<<<<<<<<" + l2[-2:]
    l1_sucia = "P<MEXRODRIQUEZ<MEZA<<TANIA<<<<<<<<<< < < < <<<<"
    datos = {"primer_apellido": "RODRIQUEZ", "segundo_apellido": "MEZA", "nombres": "TANIA", "curp": CURP_PERSONA,
             "nombre_completo": "RODRIQUEZ MEZA MEX", "sexo": "M", "mrz_linea1": l1_sucia, "mrz_linea2": l2_sucia}
    res = reconcile("pasaporte", "texto", datos, permitir_derivados=True)
    assert res.datos["mrz_linea2"] == l2
    assert res.datos["mrz_linea1"] == "P<MEXRODRIQUEZ<MEZA<<TANIA".ljust(44, "<")
    assert res.datos["sexo"] == "F"
    assert res.datos["nombre_completo"] == "TANIA RODRIQUEZ MEZA"


def test_pasaporte_mrz_irreparable_no_se_toca():
    datos = {"mrz_linea2": "G442704986MEX5504204M2906058<<<<C<<<<<<<<<99"}  # dígito de control erróneo
    res = reconcile("pasaporte", "texto", datos, permitir_derivados=True)
    assert res.datos["mrz_linea2"] == datos["mrz_linea2"]


def test_tipo_desconocido_o_vacio_no_cambia_nada():
    datos = {"a": 1}
    assert reconcile("otro", "", datos).datos == datos
    assert reconcile("ine", "", {}).datos == {}
    assert reconcile("ine", None, {"curp": "X"}).datos == {"curp": "X"}


# ── Integración mínima con el pipeline (flag) ────────────────────────────────

class _Almacen:
    def __init__(self):
        self.guardado = None

    def guardar_texto(self, t):
        return None

    def guardar_resultado(self, r, doc_id, user_id):
        self.guardado = r


def _pipeline_falso(campos, texto):
    p = object.__new__(pipeline_mod.DocumentPipeline)
    p._ocr = SimpleNamespace(plataforma={}, docling_disponible=False)
    p._md_storage = _Almacen()
    p._json_storage = _Almacen()
    p._etapa_ocr = lambda doc: TextoExtraido("d", texto, None, TipoDocumento.INE)
    p._etapa_extraccion = lambda texto, tipo: (dict(campos), "regex")
    return p


@pytest.mark.parametrize("flag", [False, True])
def test_pipeline_flag_reconcile(monkeypatch, tmp_path, flag):
    monkeypatch.setattr(pipeline_mod, "POSTPROCESS_RECONCILE", flag)
    raw = f"NOMBRE RODRIQUEZ MEZA TANIA CURP {CURP_PERSONA}"
    campos = {"nombres": "TANIA", "primer_apellido": "RODRIQUEZ", "segundo_apellido": "MEZA",
              "nombre_completo": "RODRIQUEZ MEZA TANIA", "curp": CURP_PERSONA}
    p = _pipeline_falso(campos, raw)
    out = p._procesar(tmp_path / "x.pdf", "u", "d", "h", 1, 0.0)
    if flag:
        assert out["datos"]["nombre_completo"] == "TANIA RODRIQUEZ MEZA"
        cr = out["metadatos"]["campos_reconciliados"]
        assert {"campo": "nombre_completo", "regla": "nombre_completo_ensamblado"} in cr
        assert all(set(x) == {"campo", "regla"} for x in cr)
        assert "TANIA" not in str(cr)  # solo nombres de campo y regla, sin valores
    else:
        assert out["datos"]["nombre_completo"] == "RODRIQUEZ MEZA TANIA"
        assert "campos_reconciliados" not in out["metadatos"]


def test_sexo_y_fecha_solo_se_rellenan_nunca_se_sustituyen():
    vacio = reconcile("curp", "x", {"curp": CURP_PERSONA, "sexo": None, "fecha_nacimiento": None}, permitir_derivados=True).datos
    assert vacio["sexo"] == "M" and vacio["fecha_nacimiento"] == "01/07/1957"
    previo = reconcile("curp", "x", {"curp": CURP_PERSONA, "sexo": "H", "fecha_nacimiento": "02/02/1990"}, permitir_derivados=True).datos
    assert previo["sexo"] == "H" and previo["fecha_nacimiento"] == "02/02/1990"


def test_sin_derivados_no_se_escribe_ningun_valor_no_literal():
    l2 = _mrz2()
    l2_sucia = l2[:28] + "<<<<C<<<<<<<<<" + l2[-2:]
    datos = {"primer_apellido": "RODRIQUEZ", "segundo_apellido": "MEZA", "nombres": "TANIA", "curp": CURP_PERSONA,
             "sexo": None, "fecha_nacimiento": None, "mrz_linea1": "P<MEXRODRIQUEZ<MEZA<<TANIA<<< <<",
             "mrz_linea2": l2_sucia}
    res = reconcile("pasaporte", "texto", datos)
    for k in ("mrz_linea1", "mrz_linea2", "sexo", "fecha_nacimiento"):
        assert res.datos[k] == datos[k]
    assert not any(c.derivado for c in res.cambios)
    acta = reconcile("acta_nacimiento", ACTA, {"sexo": None, "curp": CURP_PERSONA, "fecha_nacimiento": None})
    assert acta.datos["sexo"] is None and acta.datos["fecha_nacimiento"] is None
    con = reconcile("pasaporte", "texto", datos, permitir_derivados=True)
    assert con.datos["mrz_linea2"] == l2 and any(c.derivado for c in con.cambios)


def test_cambios_sin_derivados_son_literales_en_el_texto():
    res = reconcile("acta_nacimiento", ACTA, {"nombres": "TANIA", "primer_apellido": "RODRIQUEZ",
                    "segundo_apellido": "ZACATECAS", "curp": CURP_PADRE, "curp_madre": CURP_PADRE,
                    "lugar_nacimiento": "Código QR"})
    campos_literales = {"segundo_apellido", "lugar_nacimiento", "curp", "curp_padre", "curp_madre"}
    for c in res.cambios:
        if c.campo in campos_literales:
            assert str(c.nuevo) in ACTA


def test_pipeline_marca_derivado(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline_mod, "POSTPROCESS_RECONCILE", True)
    monkeypatch.setattr(pipeline_mod, "RECONCILE_ALLOW_DERIVED", True)
    p = _pipeline_falso({"curp": CURP_PERSONA, "sexo": None}, f"CURP {CURP_PERSONA}")
    out = p._procesar(tmp_path / "x.pdf", "u", "d", "h", 1, 0.0)
    assert {"campo": "sexo", "regla": "sexo_desde_curp", "derivado": True} in out["metadatos"]["campos_reconciliados"]
    monkeypatch.setattr(pipeline_mod, "RECONCILE_ALLOW_DERIVED", False)
    out = p._procesar(tmp_path / "x.pdf", "u", "d", "h", 1, 0.0)
    assert out["datos"]["sexo"] is None
