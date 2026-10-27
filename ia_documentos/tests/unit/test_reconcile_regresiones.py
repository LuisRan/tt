"""Regresiones de la reconciliación detectadas en una campaña limpia (textos sintéticos escritos a mano)."""
from application.reconcile import reconcile
from infrastructure.regex.correccion_ocr import curp_valida, digito_verificador_curp


def _curp(prefijo17: str) -> str:
    return prefijo17 + digito_verificador_curp(prefijo17)


CURP_COLON = _curp("COMG030522HNLLNL0")   # GILBERTO COLON MONTOYA
CURP_PAD = _curp("COFF810707HCMLNR4")
CURP_MAD = _curp("MOQB860125MHGNSN0")
CURP_CASAREZ = _curp("CAIM840520HGTSGR6")  # MAURO CASAREZ IGLESIAS
CURP_MAD2 = _curp("IEIS670126MCMGRN5")
CURP_AGUA = _curp("MARA700905MASSDM0")     # persona M
CURP_MAD_AGUA = _curp("ROMB430528MQRDDT7")
CURP_PAD_AGUA = _curp("MABC340531HNLSCL5")


def test_curps_sinteticas_validas():
    for c in (CURP_COLON, CURP_PAD, CURP_MAD, CURP_CASAREZ, CURP_MAD2, CURP_AGUA, CURP_MAD_AGUA, CURP_PAD_AGUA):
        assert curp_valida(c)


def test_nombres_no_se_amplian_con_el_apellido_de_la_celda_contigua():
    """'GILBERTO COLON / Nombre(s) MONTOYA': el valor previo a la etiqueta incluye el apellido."""
    raw = f"""
Clave Única de Registro de Población
{CURP_COLON}
Datos de la Persona Registrada
GILBERTO COLON
Nombre(s) MONTOYA
Primer Apellido Segundo Apellido
HOMBRE
22/05/2003 NUEVO LEON
Sexo Fecha de Nacimiento
Lugar de Nacimiento
Datos de Filiación de la Persona Registrada
FRANCISCO COLON FUENTES
Nombre(s) MEXICANA {CURP_PAD}
Primer Apellido Segundo Apellido
Nacionalidad CURP
Anotaciones Marginales:
"""
    datos = {"nombre_completo": "GILBERTO COLON MONTOYA", "nombres": "GILBERTO", "primer_apellido": "COLON",
             "segundo_apellido": "MONTOYA", "curp": CURP_COLON, "fecha_nacimiento": "22/05/2003", "sexo": "H"}
    r = reconcile("acta_nacimiento", raw, dict(datos), permitir_derivados=True).datos
    assert r["nombres"] == "GILBERTO"
    assert r["nombre_completo"] == "GILBERTO COLON MONTOYA"


def test_nombre_completo_no_duplica_apellido_que_el_actual_no_repite():
    datos = {"nombres": "GILBERTO", "primer_apellido": "COLON COLON", "segundo_apellido": "MONTOYA",
             "nombre_completo": "GILBERTO COLON MONTOYA"}
    r = reconcile("acta_nacimiento", "", dict(datos)).datos
    assert r["nombre_completo"] == "GILBERTO COLON MONTOYA"


def test_pasaporte_nombre_completo_no_duplica_apellido():
    datos = {"nombres": "CRISTOBAL WILFRIDO", "primer_apellido": "ALBA BARELA", "segundo_apellido": "BARELA",
             "nombre_completo": "CRISTOBAL WILFRIDO ALBA BARELA"}
    r = reconcile("pasaporte", "", dict(datos)).datos
    assert r["nombre_completo"] == "CRISTOBAL WILFRIDO ALBA BARELA"


def test_etiqueta_curp_no_se_pega_al_nombre_del_progenitor():
    raw = f"""
Datos de la Persona Registrada
Datos de Filiación de la Persona Registrada
| ALFREDO Nombre(s) CORONA Primer Apellido TIJERINA Segundo Apellido MEXICANA Nacionalidad {CURP_PAD} CURP ANEL Nombre(s) ALMANZA Primer Apellido VILLALPANDO Segundo Apellido MEXICANA Nacionalidad {CURP_MAD} CURP |
Anotaciones Marginales:
"""
    for previo in ("ANEL ALMANZA VILLALPANDO", "ANEL", None):
        datos = {"nombre_madre": previo, "curp_padre": CURP_PAD, "curp_madre": CURP_MAD}
        r = reconcile("acta_nacimiento", raw, datos).datos
        assert r["nombre_madre"] == "ANEL ALMANZA VILLALPANDO", previo
        assert "CURP" not in str(r["nombre_madre"])


def test_apellido_correcto_segun_curp_no_se_reemplaza_por_el_de_otra_celda():
    """'Nombre(s) IGLESIAS / Primer Apellido': IGLESIAS es el segundo apellido, no el primero."""
    raw = f"""
Clave Única de Registro de Población
{CURP_CASAREZ}
Datos de la Persona Registrada
MAURO
CASAREZ
Nombre(s) IGLESIAS
Primer Apellido
Segundo Apellido
HOMBRE
20/05/1984
Sexo
Fecha de Nacimiento
Datos de Filiación de la Persona Registrada
SONIA IGLESIAS
Nombre(s) IRIZARRY MEXICANA {CURP_MAD2}
Primer Apellido Segundo Apellido
Anotaciones Marginales:
"""
    datos = {"nombres": "MAURO", "primer_apellido": "CASAREZ", "segundo_apellido": "IGLESIAS",
             "curp": CURP_CASAREZ, "fecha_nacimiento": "20/05/1984", "sexo": "H"}
    r = reconcile("acta_nacimiento", raw, dict(datos)).datos
    assert (r["nombres"], r["primer_apellido"], r["segundo_apellido"]) == ("MAURO", "CASAREZ", "IGLESIAS")


def test_curp_de_la_persona_no_sustituye_la_curp_valida_del_progenitor():
    """La CURP de la persona está en la región de filiación; la de la madre, tras las anotaciones."""
    raw = f"""
Datos de la Persona Registrada
Datos de Filiación de la Persona Registrada
Clave Única de Registro de Población {CURP_AGUA}
| CLEMENTE Nombre(s) MAESTAS Primer Apellido BACA Segundo Apellido Anotaciones Marginales: ninguna {CURP_PAD_AGUA} CURP {CURP_MAD_AGUA} CURP |
"""
    datos = {"curp": CURP_AGUA, "fecha_nacimiento": "05/09/1970", "sexo": "M",
             "curp_padre": CURP_PAD_AGUA, "curp_madre": CURP_MAD_AGUA}
    r = reconcile("acta_nacimiento", raw, dict(datos)).datos
    assert r["curp_madre"] == CURP_MAD_AGUA
    assert r["curp_padre"] == CURP_PAD_AGUA


def test_curp_progenitor_valida_con_sexo_correcto_presente_en_texto_se_conserva():
    raw = f"""
Datos de Filiación de la Persona Registrada
ANA Nombre(s) PEREZ Primer Apellido LOPEZ Segundo Apellido {CURP_MAD} CURP
LUIS Nombre(s) PEREZ Primer Apellido LOPEZ Segundo Apellido {CURP_MAD2} CURP
Anotaciones Marginales:
"""
    datos = {"curp_madre": CURP_MAD2, "curp_padre": None}
    r = reconcile("acta_nacimiento", raw, dict(datos)).datos
    assert r["curp_madre"] == CURP_MAD2
