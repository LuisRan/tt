"""
tests/unit/test_validator.py
Pruebas del validador de campos contra schemas Pydantic.
"""
import pytest
from domain.entities.documento import TipoDocumento
from application.validator import validar_campos


class TestValidarCampos:
    def test_campos_validos_ine(self):
        campos = {
            "nombre_completo": "JUAN PÉREZ",
            "curp": "PEGJ850312HDFRMN09",
            "fecha_nacimiento": "12/03/1985",
            "sexo": "H",
        }
        resultado, errores = validar_campos(campos, TipoDocumento.INE)
        assert errores == []
        assert resultado["nombre_completo"] == "JUAN PÉREZ"
        assert resultado["curp"] == "PEGJ850312HDFRMN09"

    def test_curp_invalida_genera_error(self):
        campos = {
            "nombre_completo": "ANA GÓMEZ",
            "curp": "INVALIDA_CURP",
        }
        resultado, errores = validar_campos(campos, TipoDocumento.INE)
        assert len(errores) > 0
        # El campo inválido debe quedar como None
        assert resultado["curp"] is None

    def test_campos_todos_none_no_genera_errores(self):
        campos = {}
        resultado, errores = validar_campos(campos, TipoDocumento.PASAPORTE)
        assert errores == []
        # Todos los campos deben estar presentes pero con None
        assert "nombre_completo" in resultado
        assert resultado["nombre_completo"] is None

    def test_campos_extra_ignorados(self):
        """Campos que no existen en el schema deben ignorarse sin error."""
        campos = {
            "nombre_completo": "PEDRO LUNA",
            "campo_inventado": "valor_raro",
        }
        resultado, errores = validar_campos(campos, TipoDocumento.INE)
        assert "campo_inventado" not in resultado

    def test_resultado_es_serializable(self):
        """El dict resultante debe ser JSON-serializable."""
        import json
        campos = {"nombre_completo": "LUCÍA FERNÁNDEZ", "sexo": "M"}
        resultado, _ = validar_campos(campos, TipoDocumento.ACTA_NACIMIENTO)
        # No debe lanzar excepción
        json.dumps(resultado)

    def test_tipo_desconocido_usa_schema_base(self):
        campos = {"nombre_completo": "TEST"}
        resultado, errores = validar_campos(campos, TipoDocumento.DESCONOCIDO)
        # Debe retornar algo sin explotar
        assert isinstance(resultado, dict)
