"""Pruebas de application/reconcile_acta.py (datos 100 % sinteticos escritos a mano)."""
import pytest

import application.reconcile as R
from application import reconcile_acta as A
from application.reconcile import reconcile
from infrastructure.regex.correccion_ocr import curp_valida, digito_verificador_curp


def _curp(pref="PEPA", fecha="800101", sexo="H", estado="DF", cons="RRN", hom="0"):
    c17 = pref + fecha + sexo + estado + cons + hom
    return c17 + digito_verificador_curp(c17)


CP, CM = _curp("PEPA", "800101", "H", cons="RRN"), _curp("LOMA", "820304", "M", "JC", "PPR")
CPERSONA = _curp("SAAM", "430713", "H", "DG", "LRG")
TEXTO = f"""
Municipio de Registro
VIEJA BELICE
Ofoalia Fecha de Hagaira Libro Nimero de Acta
20 24/11/1943 33 53729
Datos de la Persona Registrada
MIGUEL ANGEL SALDIVAR ARREDONDO
Nombre(s) Primer Apellido Segundo Apellido
HOMBRE 13/07/1943 DURANGO
Sexo Fecha de Nacimiento Lugar de Nacimiento
Datos de Filiación de la Persona Registrada
MAURICIO SALDIVAR ZAYAS MEXICANA {CP}
Nombre(s) Primer Apellido Segundo Apellido Nacionalidad CURP
HELENA DE LA TORRE CORREA MEXICANA {CM}
Nombre(s) Primer Apellido Segundo Apellido Nacionalidad CURP
Anotaciones Marginales: Certificación:
"""
BASE = {"curp": CPERSONA, "sexo": "H", "nombres": "MIGUEL ANGEL", "primer_apellido": "SALDIVAR", "segundo_apellido": "ARREDONDO",
        "fecha_nacimiento": "13/07/1943", "oficialia": "53729", "libro": "20", "numero_acta": None,
        "fecha_registro": "13/07/1943", "nombre_padre": "MAURICIO SALDIVAR ZAYAS MEXICANA",
        "nombre_madre": "HELENA DE LA TORRE", "curp_padre": None, "curp_madre": CP}


@pytest.fixture(autouse=True)
def _flag():
    R.ACTA_RULES_ON = True
    yield
    R.ACTA_RULES_ON = None


def _rec(texto=TEXTO, derivados=False, **cambios):
    return reconcile("acta_nacimiento", texto, dict(BASE, **cambios), permitir_derivados=derivados)


def test_flag_apagado_no_hace_nada():
    R.ACTA_RULES_ON = False
    d = _rec().datos
    assert d["oficialia"] == "53729" and d["nombre_madre"] == "HELENA DE LA TORRE"


def test_fila_registro_por_posicion():
    d = _rec().datos
    assert (d["oficialia"], d["fecha_registro"], d["libro"], d["numero_acta"]) == ("20", "24/11/1943", "33", "53729")


def test_fila_registro_repara_fecha_ocr_y_cabecera_partida():
    t = TEXTO.replace("20 24/11/1943 33 53729", "91 de Acta\n05/1D/1986 11 369")
    d = _rec(t, fecha_nacimiento="01/01/1950").datos
    assert (d["oficialia"], d["fecha_registro"], d["libro"], d["numero_acta"]) == ("91", "05/10/1986", "11", "369")


def test_fila_registro_sin_libro_no_inventa():
    t = TEXTO.replace("20 24/11/1943 33 53729", "8 26/02/2006 95521")
    d = _rec(t, libro="95521").datos
    assert d["oficialia"] == "8" and d["numero_acta"] == "95521" and d["libro"] == "95521"  # libro no se toca


def test_fila_registro_dos_fechas_ambiguas_no_cambia():
    t = TEXTO.replace("20 24/11/1943 33 53729", "20 24/11/1943 12/12/1950 33 53729")
    assert _rec(t).datos["oficialia"] == "53729"


def test_filiacion_nombre_completo_sin_nacionalidad_ni_truncado():
    d = _rec().datos
    assert d["nombre_padre"] == "MAURICIO SALDIVAR ZAYAS"
    assert d["nombre_madre"] == "HELENA DE LA TORRE CORREA"
    assert d["curp_padre"] == CP and d["curp_madre"] == CM  # la CURP del padre no queda como curp de otro rol


def test_filiacion_etiquetas_en_linea_y_diacriticos():
    t = (TEXTO.split("Datos de Filiación")[0] + "Datos de Filiación de la Persona Registrada\n"
         f"Nombre(s) AGUSTÍN Primer Apellido RASCON Segundo Apellido ALONZO Nacionalidad MEXICANA {CP} CURP\n"
         f"Nombre(s) ANA Primer Apellido PEREZ Nacionalidad MEXICANA {CM} CURP\nAnotaciones Marginales:\n")
    d = _rec(t).datos
    assert d["nombre_padre"] == "AGUSTIN RASCON ALONZO" and d["nombre_madre"] == "ANA PEREZ"


def test_filiacion_duplicada_o_dudosa_no_cambia():
    t = TEXTO.replace("MAURICIO SALDIVAR ZAYAS", "MAURICIO MAURICIO SALDIVAR SALDIVAR ZAYAS")
    assert _rec(t).datos["nombre_padre"] == "MAURICIO SALDIVAR ZAYAS MEXICANA"


def test_filiacion_roles_por_sexo_de_la_curp():
    t = TEXTO.replace(f"MEXICANA {CP}", "MEXICANA @@").replace(f"MEXICANA {CM}", f"MEXICANA {CP}").replace("@@", CM)
    d = _rec(t, nombre_padre=None, nombre_madre=None, curp_padre=None, curp_madre=None).datos
    # la 1a fila lleva la CURP de mujer y la 2a la de hombre: los roles se invierten
    assert d["nombre_padre"] == "HELENA DE LA TORRE CORREA" and d["nombre_madre"] == "MAURICIO SALDIVAR ZAYAS"
    assert d["curp_padre"] == CP and d["curp_madre"] == CM


def test_nacionalidad_con_ruido_ocr():
    t = TEXTO.replace(f"MEXICANA {CM}", f"MEXIGANA {CM}")
    assert _rec(t).datos["nombre_madre"] == "HELENA DE LA TORRE CORREA"


def test_segundo_apellido_por_fila_de_persona():
    d = _rec(segundo_apellido="ZAYAS").datos
    assert d["segundo_apellido"] == "ARREDONDO"


def test_diacriticos_conserva_enie():
    d = _rec(lugar_nacimiento="MONTAÑA ÁLAMO", entidad_registro="MEXIÇO").datos
    assert d["lugar_nacimiento"] == "MONTAÑA ALAMO" and d["entidad_registro"] == "MEXICO"


def _corrupta(c):
    """Una CURP con un digito cambiado que la reparacion por estructura recupera de forma unica."""
    for i in range(4, 10):
        for ch in "0123456789":
            m = c[:i] + ch + c[i + 1:]
            if ch != c[i] and not curp_valida(m) and A.reparar_curp_estructura(m, "", "M") == c:
                return m
    raise AssertionError("sin corrupcion reparable")


def test_curp_progenitor_dv_solo_con_derivados():
    t = TEXTO.replace(CM, _corrupta(CM))
    assert _rec(t, derivados=False).datos["curp_madre"] != CM
    r = _rec(t, derivados=True)
    assert r.datos["curp_madre"] == CM
    assert any(c.regla == "acta_curp_progenitor_dv" and c.derivado for c in r.cambios)


def test_reparar_curp_estructura_ambigua_devuelve_none():
    assert A.reparar_curp_estructura("OITB470817HHGRRN58", "", "H") is None  # varias soluciones al minimo
    assert A.reparar_curp_estructura(CP, "", "H") == CP


def test_quitar_diacriticos():
    assert A.quitar_diacriticos("CITLALİ ÁÉÍÓÚÜ ñ Ç") == "CITLALI AEIOUU ñ C"


def test_otro_tipo_no_afectado():
    r = reconcile("ine", TEXTO, {"nombre_padre": "X"})
    assert r.datos["nombre_padre"] == "X"
