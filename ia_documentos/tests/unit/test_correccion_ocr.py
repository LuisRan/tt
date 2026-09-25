"""
Pruebas de la corrección de OCR por validación cruzada.
El texto de 'fixtures/ine_ocr_ruidoso.md' es la salida REAL del OCR de una INE
fotografiada (ine.pdf) en macOS: con errores como 'SACRT60818HDFUNLOS'.
"""
from pathlib import Path

import pytest

from domain.entities.documento import TipoDocumento
from infrastructure.regex.correccion_ocr import (
    aparece_con_tolerancia,
    corregir_clave_elector,
    corregir_curp,
    curp_valida,
    digito_verificador_curp,
    limpiar_linea_nombre,
    normalizar_curp,
    pistas_curp,
)
from infrastructure.regex.regex_heuristic_adapter import RegexHeuristicAdapter

TEXTO_RUIDOSO = (Path(__file__).parent.parent / "fixtures" / "ine_ocr_ruidoso.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("curp", [
    "SACR760818HDFVNL08", "BACP940622MQTRRL08", "TEGT140816HGTLRDA4", "MAOG720808MDFCCB02",
])
def test_digito_verificador_curps_reales(curp):
    assert digito_verificador_curp(curp) == curp[17]
    assert curp_valida(curp)


def test_curp_con_digito_incorrecto_no_es_valida():
    assert not curp_valida("SACR760818HDFVNL07")


def test_normaliza_homoclave_numerica_antes_de_2000():
    assert normalizar_curp("BACP940622MQTRRLO8") == "BACP940622MQTRRL08"


def test_pistas_curp_desde_nombre():
    p = pistas_curp("RAUL CARLOS", "SAAVEDRA", "CINTA")
    assert (p[0], p[1], p[2], p[3], p[13], p[14], p[15]) == ("S", "A", "C", "R", "V", "N", "L")
    # nombres compuestos con JOSE/MARIA usan el segundo nombre
    assert pistas_curp("JOSE TADEO", "TELLEZ", "GARCIA")[3] == "T"
    # partículas del apellido se ignoran
    assert pistas_curp("PAULINA", "BARAJAS", "DE LA CRUZ")[2] == "C"


def test_corrige_curp_ruidosa():
    curp, corregida = corregir_curp(TEXTO_RUIDOSO, "RAUL CARLOS", "SAAVEDRA", "CINTA", "18/08/1976", "H")
    assert curp == "SACR760818HDFVNL08" and corregida


def test_corrige_clave_elector_ruidosa():
    clave, corregida = corregir_clave_elector(
        TEXTO_RUIDOSO, "RAUL CARLOS", "SAAVEDRA", "CINTA", "18/08/1976", "H", "SACR760818HDFVNL08"
    )
    assert clave == "SVCNRL76081809H301" and corregida


def test_no_inventa_curp_sin_evidencia():
    assert corregir_curp("NOMBRE JUAN PEREZ\nDOMICILIO CALLE 1", "JUAN", "PEREZ", "LOPEZ", "01/01/1990", "H") == (None, False)


def test_tolerancia_anti_alucinacion():
    assert aparece_con_tolerancia("SACR760818HDFVNL08", TEXTO_RUIDOSO)
    assert not aparece_con_tolerancia("GOMA850101HDFRRN03", TEXTO_RUIDOSO)


@pytest.mark.parametrize("linea,esperado", [
    ("SAAVEDRA 18/08/1976", "SAAVEDRA"),
    ("CINTA wo H", "CINTA"),
    ("DE LA CRUZ 'aexo M", "DE LA CRUZ"),
    ("PECAR OE MACIMIEA TO", None),   # etiqueta "FECHA DE NACIMIENTO" mal leída
])
def test_limpia_lineas_de_nombre(linea, esperado):
    assert limpiar_linea_nombre(linea) == esperado


def test_ine_ruidosa_completa():
    c = RegexHeuristicAdapter().extraer_campos(TEXTO_RUIDOSO, TipoDocumento.INE)
    assert c["primer_apellido"] == "SAAVEDRA"
    assert c["segundo_apellido"] == "CINTA"
    assert c["nombres"] == "RAUL CARLOS"
    assert c["curp"] == "SACR760818HDFVNL08"
    assert c["clave_elector"] == "SVCNRL76081809H301"
    assert (c["estado"], c["municipio"], c["seccion"], c["localidad"]) == ("09", "014", "4373", "0001")
    assert c["año_registro"] == "2016 01"
    assert (c["año_emision"], c["año_vigencia"]) == ("2017", "2027")
    assert set(c["_corregidos"]) == {"curp", "clave_elector"}


def test_ine_sin_etiqueta_nombre_usa_bloque_previo_a_domicilio():
    texto = """CREDENCIAL PARA VOTAR
FEOMA DE NACIMIENTO: :
BARAJAS 22/06/1994
DE LA CRUZ 'exo M
PAULINA
DOMICILIO
COL LAZARO CARDENAS 76087
QUERETARO, QRO
CLAVE DE ELECTOR BRCRPL94062222M400
curp BACP940622MQTRRLO8
"""
    c = RegexHeuristicAdapter().extraer_campos(texto, TipoDocumento.INE)
    assert (c["primer_apellido"], c["segundo_apellido"], c["nombres"]) == ("BARAJAS", "DE LA CRUZ", "PAULINA")
    assert c["curp"] == "BACP940622MQTRRL08"


def test_agrupa_cajas_de_apple_vision_en_lineas():
    from infrastructure.ocr.docling_adapter import DoclingAdapter
    # (texto, confianza, (x, y, w, h)) con origen abajo-izquierda
    anotaciones = [
        ("18/08/1976", 0.9, (0.70, 0.80, 0.2, 0.03)),
        ("SAAVEDRA", 0.9, (0.10, 0.805, 0.2, 0.03)),
        ("CINTA", 0.9, (0.10, 0.75, 0.1, 0.03)),
    ]
    assert DoclingAdapter._agrupar_lineas(anotaciones) == "SAAVEDRA 18/08/1976\nCINTA"


def test_llm_no_puede_contradecir_curp_validada():
    from application.pipeline import DocumentPipeline
    regex = {"curp": "SACR760818HDFVNL08", "primer_apellido": "SAAVEDRA",
             "segundo_apellido": "CINTA", "nombres": "RAUL CARLOS"}
    assert not DocumentPipeline._consistente_con_curp("primer_apellido", "PECAR OE MACIMIEA", regex)
    assert DocumentPipeline._consistente_con_curp("primer_apellido", "SAAVEDRA", regex)
