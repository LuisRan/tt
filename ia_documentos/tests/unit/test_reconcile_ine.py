"""Reglas adicionales de INE (application/reconcile_ine.py). Textos sinteticos escritos a mano."""
import pytest

from application import reconcile_ine
from application.reconcile import reconcile
from config import settings
from infrastructure.regex.correccion_ocr import curp_valida, digito_verificador_curp


def _curp(prefijo17: str) -> str:
    return prefijo17 + digito_verificador_curp(prefijo17)


# VEGA LUNA CARLA: V E (ap1) L (ap2) C (nombre) ... G (ap1) N (ap2) R (nombre)
CURP_VLC = _curp("VELC850224MASGNR0")
# ROJAS YANEZ PEDRO con Ñ interna en el 2º apellido (YAÑEZ -> X en la posicion 14)
CURP_ENIE = _curp("ROYP900101HDFJXD0")


@pytest.fixture(autouse=True)
def flag_ine(monkeypatch):
    monkeypatch.setattr(settings, "RECONCILE_INE", True)


def _rec(raw, datos, derivados=False):
    return reconcile("ine", raw, datos, permitir_derivados=derivados)


def test_curps_sinteticas_validas():
    assert curp_valida(CURP_VLC) and curp_valida(CURP_ENIE)


# ── nombre ────────────────────────────────────────────────────────────────────

RAW_LINEAS = f"""
INSTITUTO NACIONAL ELECTORAL
CREDENCIAL PARA VOTAR
NOMBRE FECHA NACIMIENTO
VEGA 24/02/1985
LUNA SEXO M
CARLA
DOMICILIO
CALLE UNO 12
COL. CENTRO 01234
VILLA NUEVA, AGS.
CLAVE DE ELECTOR VGLNCR85022401M123
CURP {CURP_VLC} AÑO DE REGISTRO 2005 02
"""


def test_nombre_se_reparte_con_etiquetas_y_fecha_intercaladas():
    datos = {"curp": CURP_VLC, "nombres": "CARLA", "primer_apellido": "VEGA", "segundo_apellido": None,
             "nombre_completo": "CARLA VEGA"}
    r = _rec(RAW_LINEAS, datos)
    assert (r.datos["primer_apellido"], r.datos["segundo_apellido"], r.datos["nombres"]) == ("VEGA", "LUNA", "CARLA")
    assert r.datos["nombre_completo"] == "CARLA VEGA LUNA"
    assert any(c.regla == "ine_nombre_por_ventana_curp" for c in r.cambios)


def test_nombre_valores_son_palabras_literales_del_texto():
    r = _rec(RAW_LINEAS, {"curp": CURP_VLC, "nombres": "X", "primer_apellido": "Y", "segundo_apellido": None})
    for campo in ("primer_apellido", "segundo_apellido", "nombres"):
        for palabra in r.datos[campo].split():
            assert palabra in RAW_LINEAS


def test_nombre_ancla_en_linea_limpia_antes_de_domicilio():
    raw = f"""
NOMBRE VEGA LUNA CARLA MARIA
DOMICILIO ALGO
CARLA MARIA
DOMICILIO
CALLE UNO 12
CLAVE DE ELECTOR X
CURP {CURP_VLC}
"""
    datos = {"curp": CURP_VLC, "nombres": "CARLA", "primer_apellido": "VEGA", "segundo_apellido": "LUNA"}
    r = _rec(raw, datos)
    assert r.datos["nombres"] == "CARLA MARIA"
    assert r.datos["nombre_completo"] == "CARLA MARIA VEGA LUNA"


def test_nombre_sin_ancla_el_empate_de_longitud_conserva_el_actual_o_el_mas_corto():
    raw = f"VEGA LUNA CARLA RUIDO SUELTO\nCURP {CURP_VLC}\n"
    r = _rec(raw, {"curp": CURP_VLC, "nombres": "CARLA", "primer_apellido": "VEGA", "segundo_apellido": "LUNA"})
    assert r.datos["nombres"] == "CARLA"  # no se prolonga sobre palabras sueltas


def test_nombre_no_cambia_si_el_actual_ya_es_consistente_y_no_hay_ancla():
    raw = f"VEGA CARLA LUNA\nCURP {CURP_VLC}\n"  # orden raro (heavy): el texto no permite una ventana mejor
    datos = {"curp": CURP_VLC, "nombres": "CARLA", "primer_apellido": "VEGA", "segundo_apellido": "LUNA",
             "nombre_completo": "CARLA VEGA LUNA"}
    r = _rec(raw, datos)
    assert (r.datos["primer_apellido"], r.datos["segundo_apellido"], r.datos["nombres"]) == ("VEGA", "LUNA", "CARLA")


def test_nombre_sin_curp_valida_no_actua():
    raw = RAW_LINEAS.replace(CURP_VLC, "ILEGIBLE")
    r = _rec(raw, {"curp": "XXXX", "nombres": "CARLA", "primer_apellido": "VEGA", "segundo_apellido": None})
    assert not any(c.regla == "ine_nombre_por_ventana_curp" for c in r.cambios)


def test_nombre_tolera_una_letra_interna_de_la_curp_que_no_cuadra():
    # CURP de generador con la consonante interna del 2º apellido distinta (<=2 de 7 letras)
    curp = _curp("VELC850224MASGXR0")
    raw = RAW_LINEAS.replace(CURP_VLC, curp)
    r = _rec(raw, {"curp": curp, "nombres": "CARLA", "primer_apellido": "VEGA", "segundo_apellido": None})
    assert r.datos["segundo_apellido"] == "LUNA"


# ── Ñ por CURP (derivada) ─────────────────────────────────────────────────────

def test_enie_por_curp_solo_con_derivados():
    raw = f"ROJAS YANEZ PEDRO\nCURP {CURP_ENIE}\n"
    datos = {"curp": CURP_ENIE, "nombres": "PEDRO", "primer_apellido": "ROJAS", "segundo_apellido": "YANEZ",
             "nombre_completo": "PEDRO ROJAS YANEZ"}
    sin = _rec(raw, datos)
    assert sin.datos["segundo_apellido"] == "YANEZ"
    con = _rec(raw, datos, derivados=True)
    assert con.datos["segundo_apellido"] == "YAÑEZ"
    assert con.datos["nombre_completo"] == "PEDRO ROJAS YAÑEZ"
    assert all(c.derivado for c in con.cambios if c.regla == "ine_enie_por_curp")


def test_enie_no_se_inventa_si_la_curp_no_la_delata():
    raw = f"ROJAS YANEZ PEDRO\nCURP {CURP_VLC}\n"
    r = reconcile_ine._enie("YANEZ", "N")
    assert r is None


# ── domicilio ─────────────────────────────────────────────────────────────────

def test_domicilio_tres_lineas_limpias_corrige_puntuacion_y_orden():
    datos = {"curp": CURP_VLC, "domicilio": "CALLE UNO 12 COL: CENTRO 01234 VILLA NUEVA. AGS"}
    r = _rec(RAW_LINEAS, datos)
    assert r.datos["domicilio"] == "CALLE UNO 12 COL. CENTRO 01234 VILLA NUEVA, AGS."


def test_domicilio_lineas_revueltas_con_etiquetas_en_medio():
    raw = """
DOMICILIO
COL. NUEVA HUNGRIA 23768
FECHA NACIMENTO 05/08/1998
PASAJE SUR CARRERA 397
SAN EMILIANO DE LA MONTAÑA, TAMPS.
CLAVE DE ELECTOR X
"""
    r = _rec(raw, {"domicilio": "COL. NUEVA HUNGRIA 23768 FECHA NACIMENTO 05/08/1998 PASAJE SUR CARRERA 397"})
    assert r.datos["domicilio"] == "PASAJE SUR CARRERA 397 COL. NUEVA HUNGRIA 23768 SAN EMILIANO DE LA MONTAÑA, TAMPS."


def test_domicilio_recupera_la_enie_de_otra_zona():
    raw = """
CALLE UNO 12 COL. SAN PEDRO DE LA MONTAÑA 12345 VILLA, AGS.
DOMICILIO
CALLE UNO 12
COL. SAN PEDRO DE LA MONTANA 12345
VILLA, AGS.
CLAVE DE ELECTOR X
"""
    r = _rec(raw, {"domicilio": None})
    assert "MONTAÑA" in r.datos["domicilio"]


def test_domicilio_respeta_el_valor_actual_ante_ruido_de_lectura_minimo():
    raw = RAW_LINEAS.replace("CALLE UNO 12", "CALLE UNO 13")
    actual = "CALLE UNO 12 COL. CENTRO 01234 VILLA NUEVA, AGS."
    r = _rec(raw, {"domicilio": actual})
    assert r.datos["domicilio"] == actual


def test_domicilio_ignora_lineas_con_claves_alfanumericas():
    raw = """
VILLA NUEVA, AGS.
VGLNCR85022401M123
COL. CENTRO 01234
"""
    r = _rec(raw, {"domicilio": None})
    assert r.datos.get("domicilio") in (None, "")


# ── clave de elector ──────────────────────────────────────────────────────────

def test_clave_por_etiqueta_desempata_dos_lecturas_validas():
    raw = f"""
VGLNCR85022401M124
CLAVE DE ELECTOR VGLNCR85022401M123
CURP {CURP_VLC}
"""
    r = _rec(raw, {"curp": CURP_VLC, "clave_elector": None})
    assert r.datos["clave_elector"] == "VGLNCR85022401M123"


def test_clave_por_etiqueta_no_toca_una_clave_valida():
    raw = f"CLAVE DE ELECTOR VGLNCR85022401M123\nCURP {CURP_VLC}\n"
    r = _rec(raw, {"curp": CURP_VLC, "clave_elector": "VGLNCR85022401M999"})
    assert r.datos["clave_elector"] == "VGLNCR85022401M999"


# ── flag ──────────────────────────────────────────────────────────────────────

def test_flag_apagado_no_aplica_ninguna_regla_de_ine(monkeypatch):
    monkeypatch.setattr(settings, "RECONCILE_INE", False)
    datos = {"curp": CURP_VLC, "nombres": "CARLA", "primer_apellido": "VEGA", "segundo_apellido": None,
             "domicilio": "x"}
    r = _rec(RAW_LINEAS, datos)
    assert not any(c.regla.startswith("ine_") for c in r.cambios)


def test_default_del_flag_es_false(monkeypatch):
    monkeypatch.delenv("RECONCILE_INE", raising=False)
    assert settings._env_bool("RECONCILE_INE", False) is False
    assert 'RECONCILE_INE = _env_bool("RECONCILE_INE", False)' in open(settings.__file__, encoding="utf-8").read()


def test_reglas_derivadas_registradas():
    from application.reconcile import REGLAS_DERIVADAS
    assert reconcile_ine.REGLAS_DERIVADAS_INE <= REGLAS_DERIVADAS
