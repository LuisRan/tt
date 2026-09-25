"""
tests/unit/test_schemas.py
Pruebas unitarias de los schemas Pydantic y su validación.
"""
import pytest
from pydantic import ValidationError

from domain.entities.documento import TipoDocumento
from domain.schemas.document_schemas import (
    CamposINE,
    CamposPasaporte,
    CamposActaNacimiento,
    CamposCURP,
    get_schema,
    get_json_schema,
    SCHEMA_MAP,
)


class TestSchemaMap:
    def test_todos_los_tipos_mapeados(self):
        tipos_con_schema = [
            TipoDocumento.INE,
            TipoDocumento.PASAPORTE,
            TipoDocumento.ACTA_NACIMIENTO,
            TipoDocumento.CURP,
        ]
        for tipo in tipos_con_schema:
            assert tipo in SCHEMA_MAP, f"{tipo} sin schema"

    def test_get_schema_devuelve_clase_correcta(self):
        assert get_schema(TipoDocumento.INE) is CamposINE
        assert get_schema(TipoDocumento.PASAPORTE) is CamposPasaporte
        assert get_schema(TipoDocumento.ACTA_NACIMIENTO) is CamposActaNacimiento
        assert get_schema(TipoDocumento.CURP) is CamposCURP

    def test_get_json_schema_es_dict(self):
        schema = get_json_schema(TipoDocumento.INE)
        assert isinstance(schema, dict)
        assert "properties" in schema


class TestCamposINE:
    def test_todos_none_es_valido(self):
        """Un INE con todos los campos None es válido (doc incompleto)."""
        ine = CamposINE()
        assert ine.nombre_completo is None

    def test_curp_valida(self):
        ine = CamposINE(curp="GOMC850312HDFNRL09")
        assert ine.curp == "GOMC850312HDFNRL09"

    def test_curp_invalida_lanza_error(self):
        with pytest.raises(ValidationError):
            CamposINE(curp="INVALIDA")

    def test_campos_completos(self):
        ine = CamposINE(
            nombre_completo="JUAN CARLOS PÉREZ GÓMEZ",
            primer_apellido="PÉREZ",
            segundo_apellido="GÓMEZ",
            nombres="JUAN CARLOS",
            curp="PEGJ850312HDFRMN09",
            fecha_nacimiento="12/03/1985",
            sexo="H",
            clave_elector="PRGMJN85031210H800",
            domicilio="CALLE REFORMA 123 COL CENTRO",
            año_vigencia="2030",
            estado="09",
            municipio="014",
            seccion="4373",
            año_emision="2020",
        )
        assert ine.nombre_completo == "JUAN CARLOS PÉREZ GÓMEZ"
        assert ine.sexo == "H"
        assert ine.año_vigencia == "2030"
        assert ine.estado == "09"


class TestCamposPasaporte:
    def test_numero_pasaporte(self):
        p = CamposPasaporte(numero_pasaporte="G12345678")
        assert p.numero_pasaporte == "G12345678"

    def test_mrz_lines(self):
        p = CamposPasaporte(
            mrz_linea1="P<MEXPEREZ<<GOMEZ<<JUAN<<<<<<<<<<<<<<<<<<<<",
            mrz_linea2="G123456789MEX8503121M3001011<<<<<<<<<<<<<<<2",
        )
        assert p.mrz_linea1.startswith("P<MEX")


class TestCamposActaNacimiento:
    def test_campos_especificos(self):
        acta = CamposActaNacimiento(
            nombre_padre="PEDRO PÉREZ LUNA",
            nombre_madre="ANA GÓMEZ VILLA",
            municipio_registro="IZTAPALAPA",
        )
        assert acta.nombre_padre == "PEDRO PÉREZ LUNA"
        assert acta.municipio_registro == "IZTAPALAPA"


class TestModelDump:
    def test_model_dump_json_serializable(self):
        """model_dump(mode='json') no debe lanzar excepciones."""
        import json
        ine = CamposINE(nombre_completo="JUAN PÉREZ", sexo="H")
        d = ine.model_dump(mode="json")
        # Debe ser serializable
        json.dumps(d)
        assert isinstance(d, dict)
