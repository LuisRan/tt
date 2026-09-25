"""
tests/unit/test_regex_adapter.py
Pruebas unitarias del adaptador regex/heurístico.
No requiere Docling ni Ollama.
"""
import pytest
from domain.entities.documento import TipoDocumento
from infrastructure.regex.regex_heuristic_adapter import RegexHeuristicAdapter


@pytest.fixture
def adapter():
    return RegexHeuristicAdapter()


# ── Textos de prueba simulados ─────────────────────────────────────────────────
# (Simulan lo que Docling extraería de un doc real)

TEXTO_INE = """
INSTITUTO NACIONAL ELECTORAL
CREDENCIAL PARA VOTAR

NOMBRE: JUAN CARLOS PÉREZ GÓMEZ
FECHA DE NACIMIENTO: 12/03/1985
SEXO: MASCULINO
CURP PEGJ850312HDFRMN09
CLAVE DE ELECTOR PRGMJN85031210H800
SECCIÓN 1234
DOMICILIO: CALLE REFORMA 123 COL CENTRO CDMX
FECHA DE EXPEDICIÓN: 15/05/2020
FECHA DE VENCIMIENTO: 15/05/2030
FOLIO: A1B2C3D4
"""

TEXTO_PASAPORTE = """
SECRETARÍA DE RELACIONES EXTERIORES
PASAPORTE MEXICANO

NOMBRE: MARÍA ELENA GUTIÉRREZ SOTO
FECHA DE NACIMIENTO: 05/07/1990
SEXO: FEMENINO
CURP GUSM900705MDFTRR08
NÚM. PASAPORTE G98765432
FECHA DE EXPEDICIÓN: 01/02/2022
FECHA DE VENCIMIENTO: 01/02/2032
P<MEXGUTIERREZ<<SOTO<<MARIA<<<<<<<<<<<<<<
G987654329MEX9007053F3202011<<<<<<<<<<<<4
"""

TEXTO_ACTA = """
ESTADOS UNIDOS MEXICANOS
ACTA DE NACIMIENTO

Folio de Impresión
12345678

Entidad de Registro
ESTADO DE MÉXICO

Municipio de Registro
ECATEPEC DE MORELOS

Número de Acta
2000/3456/EDOMEX

Nombre(s)
CARLOS ALBERTO

Primer Apellido
MARTÍNEZ

Segundo Apellido
RUIZ

Sexo
MASCULINO

Fecha de Nacimiento
20/11/2000

CURP MARC001120HMCRLS09
"""

TEXTO_CURP = """
ESTADOS UNIDOS MEXICANOS
CONSTANCIA DE LA CLAVE ÚNICA DE REGISTRO DE POBLACIÓN

Clave:
HEMJ950810HDFRNS06

Nombre:
JESÚS HERNÁNDEZ MENDOZA

Fecha de inscripción  Folio       Entidad de registro
10/08/1995            12345678    CIUDAD DE MÉXICO

Código de Verificación
104002000120080033670
"""


class TestRegexAdapterINE:
    def test_extrae_curp(self, adapter):
        campos = adapter.extraer_campos(TEXTO_INE, TipoDocumento.INE)
        assert campos["curp"] == "PEGJ850312HDFRMN09"

    def test_extrae_fecha_nacimiento(self, adapter):
        campos = adapter.extraer_campos(TEXTO_INE, TipoDocumento.INE)
        assert campos["fecha_nacimiento"] == "12/03/1985"

    def test_extrae_sexo_masculino(self, adapter):
        campos = adapter.extraer_campos(TEXTO_INE, TipoDocumento.INE)
        assert campos["sexo"] == "H"

    def test_extrae_domicilio(self, adapter):
        campos = adapter.extraer_campos(TEXTO_INE, TipoDocumento.INE)
        assert campos["domicilio"] is not None
        assert "REFORMA" in campos["domicilio"]

    def test_extrae_seccion(self, adapter):
        campos = adapter.extraer_campos(TEXTO_INE, TipoDocumento.INE)
        assert campos["seccion"] == "1234"

    def test_nombre_adaptador(self, adapter):
        assert adapter.nombre_adaptador == "regex_heuristic"


class TestRegexAdapterPasaporte:
    def test_extrae_numero_pasaporte(self, adapter):
        campos = adapter.extraer_campos(TEXTO_PASAPORTE, TipoDocumento.PASAPORTE)
        assert campos["numero_pasaporte"] == "G98765432"

    def test_extrae_curp(self, adapter):
        campos = adapter.extraer_campos(TEXTO_PASAPORTE, TipoDocumento.PASAPORTE)
        assert campos["curp"] == "GUSM900705MDFTRR08"

    def test_extrae_sexo_femenino(self, adapter):
        campos = adapter.extraer_campos(TEXTO_PASAPORTE, TipoDocumento.PASAPORTE)
        assert campos["sexo"] == "M"

    def test_extrae_mrz(self, adapter):
        campos = adapter.extraer_campos(TEXTO_PASAPORTE, TipoDocumento.PASAPORTE)
        assert campos["mrz_linea1"] is not None
        assert campos["mrz_linea1"].startswith("P<MEX")


class TestRegexAdapterActa:
    def test_extrae_curp(self, adapter):
        campos = adapter.extraer_campos(TEXTO_ACTA, TipoDocumento.ACTA_NACIMIENTO)
        assert campos["curp"] == "MARC001120HMCRLS09"

    def test_extrae_municipio(self, adapter):
        campos = adapter.extraer_campos(TEXTO_ACTA, TipoDocumento.ACTA_NACIMIENTO)
        assert campos["municipio_registro"] is not None

    def test_extrae_numero_acta(self, adapter):
        campos = adapter.extraer_campos(TEXTO_ACTA, TipoDocumento.ACTA_NACIMIENTO)
        assert campos["numero_acta"] is not None


class TestRegexAdapterCURP:
    def test_extrae_curp(self, adapter):
        campos = adapter.extraer_campos(TEXTO_CURP, TipoDocumento.CURP)
        assert campos["curp"] == "HEMJ950810HDFRNS06"

    def test_extrae_sexo_h(self, adapter):
        campos = adapter.extraer_campos(TEXTO_CURP, TipoDocumento.CURP)
        assert campos["sexo"] == "H"

    def test_extrae_folio_y_entidad(self, adapter):
        campos = adapter.extraer_campos(TEXTO_CURP, TipoDocumento.CURP)
        assert campos["folio"] == "12345678"
        assert campos["entidad_registro"] == "CIUDAD DE MÉXICO"

    def test_extrae_fecha_inscripcion(self, adapter):
        campos = adapter.extraer_campos(TEXTO_CURP, TipoDocumento.CURP)
        assert campos["fecha_inscripcion"] == "10/08/1995"


class TestRegexAdapterTipoDesconocido:
    def test_tipo_desconocido_devuelve_dict_vacio(self, adapter):
        campos = adapter.extraer_campos("texto cualquiera", TipoDocumento.DESCONOCIDO)
        assert campos == {}
