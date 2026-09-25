"""
tests/unit/test_entities.py
Pruebas unitarias del dominio — sin dependencias externas.
"""
import pytest
from datetime import datetime
from pathlib import Path

from domain.entities.documento import (
    DocumentoIdentidad,
    DocumentoNoSoportadoError,
    ResultadoProcesamiento,
    TextoExtraido,
    TipoDocumento,
)


class TestTipoDocumento:
    def test_valores_esperados(self):
        assert TipoDocumento.INE.value == "ine"
        assert TipoDocumento.PASAPORTE.value == "pasaporte"
        assert TipoDocumento.ACTA_NACIMIENTO.value == "acta_nacimiento"
        assert TipoDocumento.CURP.value == "curp"
        assert TipoDocumento.DESCONOCIDO.value == "desconocido"

    def test_comparacion_con_string(self):
        assert TipoDocumento("ine") == TipoDocumento.INE


class TestDocumentoIdentidad:
    def test_creacion_minima(self):
        doc = DocumentoIdentidad(
            ruta_pdf=Path("/tmp/test.pdf"),
            user_id="u_001",
            doc_id="abc-123",
        )
        assert doc.tipo_detectado == TipoDocumento.DESCONOCIDO
        assert doc.hash_sha256 is None
        assert isinstance(doc.timestamp_ingesta, datetime)

    def test_creacion_completa(self):
        doc = DocumentoIdentidad(
            ruta_pdf=Path("/tmp/ine.pdf"),
            user_id="u_002",
            doc_id="def-456",
            tipo_detectado=TipoDocumento.INE,
            hash_sha256="abc123",
            num_paginas=2,
        )
        assert doc.tipo_detectado == TipoDocumento.INE
        assert doc.num_paginas == 2


class TestTextoExtraido:
    def test_creacion(self):
        t = TextoExtraido(
            doc_id="doc-001",
            texto_crudo="CREDENCIAL PARA VOTAR\nNOMBRE: JUAN PEREZ",
            tipo_detectado=TipoDocumento.INE,
        )
        assert t.doc_id == "doc-001"
        assert "JUAN" in t.texto_crudo


class TestResultadoProcesamiento:
    def _make_resultado(self, **kwargs):
        defaults = dict(
            doc_id="doc-001",
            user_id="u_001",
            tipo_documento=TipoDocumento.INE,
            datos={"nombre_completo": "JUAN PÉREZ"},
            ruta_texto_crudo="/data/raw/doc-001.md",
            adaptador_usado="ollama_qwen3_1.7b",
            pipeline_version="0.1.0",
            timestamp_procesamiento=datetime.utcnow().isoformat(),
        )
        defaults.update(kwargs)
        return ResultadoProcesamiento(**defaults)

    def test_to_dict_estructura(self):
        r = self._make_resultado()
        d = r.to_dict()

        assert d["doc_id"] == "doc-001"
        assert d["tipo_documento"] == "ine"
        assert "datos" in d
        assert "metadatos" in d
        assert d["metadatos"]["adaptador_usado"] == "ollama_qwen3_1.7b"
        assert d["metadatos"]["pipeline_version"] == "0.1.0"

    def test_datos_adicionales_vacio_por_defecto(self):
        r = self._make_resultado()
        assert r.datos_adicionales == {}

    def test_to_dict_tipo_es_string(self):
        """El tipo_documento en el dict debe ser string, no el enum."""
        r = self._make_resultado()
        d = r.to_dict()
        assert isinstance(d["tipo_documento"], str)


class TestDocumentoNoSoportadoError:
    def test_mensaje_por_defecto(self):
        e = DocumentoNoSoportadoError()
        assert "INE" in e.mensaje
        assert "Pasaporte" in e.mensaje

    def test_mensaje_personalizado(self):
        e = DocumentoNoSoportadoError("Archivo no válido")
        assert e.mensaje == "Archivo no válido"

    def test_es_exception(self):
        with pytest.raises(DocumentoNoSoportadoError):
            raise DocumentoNoSoportadoError()
