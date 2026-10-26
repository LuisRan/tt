"""Pruebas de application/reconcile_pasaporte.py (datos 100 % sinteticos escritos a mano)."""
import pytest

import application.reconcile as R
from application.reconcile import _dv_mrz, _mrz2_valida, reconcile


def _l2(num="G12345678", nac="900101", sexo="F", cad="300101"):
    c = num + _dv_mrz(num) + "MEX" + nac + _dv_mrz(nac) + sexo + cad + _dv_mrz(cad) + "<" * 14 + "0"
    return c + _dv_mrz(c[0:10] + c[13:20] + c[21:43])


L2 = _l2()
L1 = ("P<MEXPEREZ<LOPEZ<<ANA<MARIA").ljust(44, "<")
TEXTO = f"""
Apellidos / Surname / Nom
PEREZ LOPEZ
Nombres / Given names / Prenoms Observaciones / Remarks / Observations
ANA MARIA NINGUNA
Sexo / Sex / Sexe Lugar de nacimiento / Place of birth
F JALISCO
{L1}
{L2}
"""
BASE = {"primer_apellido": "PEREZ LOPEZ", "segundo_apellido": None, "nombres": "ANA MARIA NINGUNA",
        "nombre_completo": "ANA MARIA NINGUNA PEREZ LOPEZ", "sexo": "M", "numero_pasaporte": "G12345678",
        "fecha_nacimiento": "01/01/1990", "fecha_caducidad": "01/01/2030",
        "mrz_linea1": L1, "mrz_linea2": L2}


@pytest.fixture(autouse=True)
def _flag():
    R.PASAPORTE_RULES_ON = True
    yield
    R.PASAPORTE_RULES_ON = None


def _rec(texto=TEXTO, derivados=False, **cambios):
    d = dict(BASE, **cambios)
    return reconcile("pasaporte", texto, d, permitir_derivados=derivados)


def test_l2_sintetica_valida():
    assert _mrz2_valida(L2)


def test_flag_apagado_no_cambia_nada():
    R.PASAPORTE_RULES_ON = False
    assert _rec().datos == BASE


def test_sexo_por_etiqueta_literal():
    r = _rec()
    assert r.datos["sexo"] == "F"
    assert not any(c.derivado for c in r.cambios)


def test_sexo_sin_evidencia_no_se_inventa():
    texto = TEXTO.replace("F JALISCO", "JALISCO").replace(L2, "")
    assert _rec(texto).datos["sexo"] == "M"


def test_nombres_sin_observaciones_y_nombre_completo():
    d = _rec().datos
    assert d["nombres"] == "ANA MARIA"
    assert d["nombre_completo"] == "ANA MARIA PEREZ LOPEZ"


def test_nombres_no_se_toca_sin_etiqueta_observaciones():
    texto = TEXTO.replace(" Observaciones / Remarks / Observations", "")
    assert _rec(texto).datos["nombres"] == "ANA MARIA NINGUNA"


def test_apellidos_separados():
    d = _rec().datos
    assert (d["primer_apellido"], d["segundo_apellido"]) == ("PEREZ", "LOPEZ")


def test_mrz2_una_edicion_es_literal():
    ruido = L2[:30] + "X" + L2[31:]
    r = _rec(TEXTO.replace(L2, ruido), mrz_linea2=ruido)
    assert r.datos["mrz_linea2"] == L2
    assert all(not c.derivado for c in r.cambios if c.campo == "mrz_linea2")


def test_mrz2_espacio_intercalado_es_literal():
    ruido = L2[:30] + " " + L2[30:]
    r = _rec(TEXTO.replace(L2, ruido), mrz_linea2=ruido)
    assert r.datos["mrz_linea2"] == L2


def test_mrz2_varios_cambios_solo_con_derivados():
    ruido = L2[:30] + "X" + L2[31:33] + "Q" + L2[34:]
    texto = TEXTO.replace(L2, ruido)
    sin = _rec(texto, mrz_linea2=ruido)
    assert sin.datos["mrz_linea2"] == ruido
    con = _rec(texto, derivados=True, mrz_linea2=ruido)
    assert con.datos["mrz_linea2"] == L2
    assert any(c.derivado and c.campo == "mrz_linea2" for c in con.cambios)


def test_mrz2_truncada_se_reconstruye_solo_con_derivados():
    parcial = L2[:32]
    texto = TEXTO.replace(L2, parcial)
    assert _rec(texto, mrz_linea2=parcial).datos["mrz_linea2"] == parcial
    con = _rec(texto, derivados=True, mrz_linea2=parcial)
    assert con.datos["mrz_linea2"] == L2


def test_mrz2_no_se_inventa_sin_evidencia_en_texto():
    texto = TEXTO.replace(L2, "")
    con = _rec(texto, derivados=True, mrz_linea2="BASURA")
    assert con.datos["mrz_linea2"] == "BASURA"


def test_mrz1_ruidosa_solo_con_derivados():
    ruido = "P<MEXPEREZ<LOPEZ<<ANA<MARIA<<<<<<<<<‹‹• <<<<<"
    texto = TEXTO.replace(L1, ruido)
    sin = _rec(texto, mrz_linea1=ruido, nombres="ANA MARIA", nombre_completo="ANA MARIA PEREZ LOPEZ")
    assert sin.datos["mrz_linea1"] != L1
    con = _rec(texto, derivados=True, mrz_linea1=ruido, nombres="ANA MARIA",
               nombre_completo="ANA MARIA PEREZ LOPEZ")
    assert con.datos["mrz_linea1"] == L1


def test_mrz1_exacta_en_texto_es_literal():
    r = _rec(mrz_linea1="P<MEXPEREZ")
    assert r.datos["mrz_linea1"] == L1
    assert all(not c.derivado for c in r.cambios if c.campo == "mrz_linea1")


def test_curp_con_espacios_validada_por_dv():
    from infrastructure.regex.correccion_ocr import digito_verificador_curp
    base = "PELA900101MJCRPN0"
    curp = base + digito_verificador_curp(base)
    texto = TEXTO + f"\nMEXICANA {curp[:5]} {curp[5:9]} {curp[9:]}\n"
    r = _rec(texto, curp=None, sexo="F")
    assert r.datos["curp"] == curp


def test_no_lanza_con_entrada_basura():
    assert reconcile("pasaporte", None, {"sexo": None}).datos == {"sexo": None}
