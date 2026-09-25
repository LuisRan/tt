"""
tests/unit/test_docling_adapter.py
Pruebas de la lógica de detección de tipo y normalización de texto.
No requieren Docling instalado (solo prueban métodos que no dependen de él).
"""
import pytest
from domain.entities.documento import TipoDocumento
from infrastructure.ocr.docling_adapter import DoclingAdapter


@pytest.fixture
def adapter():
    # Instanciamos sin que falle aunque Docling no esté instalado
    return DoclingAdapter()


class TestDetectarTipo:
    def test_detecta_ine(self, adapter):
        texto = "INSTITUTO NACIONAL ELECTORAL\nCREDENCIAL PARA VOTAR\nCLAVE DE ELECTOR ABC123"
        assert adapter.detectar_tipo(texto) == TipoDocumento.INE

    def test_detecta_pasaporte(self, adapter):
        texto = "SECRETARÍA DE RELACIONES EXTERIORES\nPASAPORTE MEXICANO"
        assert adapter.detectar_tipo(texto) == TipoDocumento.PASAPORTE

    def test_detecta_acta_nacimiento(self, adapter):
        texto = "REGISTRO CIVIL DEL ESTADO\nACTA DE NACIMIENTO\nFECHA:"
        assert adapter.detectar_tipo(texto) == TipoDocumento.ACTA_NACIMIENTO

    def test_detecta_curp(self, adapter):
        texto = "CLAVE ÚNICA DE REGISTRO DE POBLACIÓN\nSECRETARÍA DE GOBERNACIÓN"
        assert adapter.detectar_tipo(texto) == TipoDocumento.CURP

    def test_texto_ambiguo_devuelve_desconocido(self, adapter):
        texto = "Lorem ipsum dolor sit amet consectetur"
        assert adapter.detectar_tipo(texto) == TipoDocumento.DESCONOCIDO

    def test_es_case_insensitive(self, adapter):
        texto = "credencial para votar instituto nacional electoral"
        assert adapter.detectar_tipo(texto) == TipoDocumento.INE


class TestNormalizar:
    def test_elimina_espacios_multiples(self, adapter):
        texto = "NOMBRE:    JUAN    PÉREZ"
        resultado = adapter._normalizar(texto)
        assert "    " not in resultado

    def test_normaliza_unicode_nfc(self, adapter):
        # café con NFD (e + combining acute) → NFC (é precompuesto)
        import unicodedata
        texto_nfd = unicodedata.normalize("NFD", "café")
        resultado = adapter._normalizar(texto_nfd)
        assert unicodedata.is_normalized("NFC", resultado)

    def test_no_colapsa_saltos_linea(self, adapter):
        texto = "linea1\nlinea2\nlinea3"
        resultado = adapter._normalizar(texto)
        assert "\n" in resultado

    def test_max_dos_lineas_vacias_consecutivas(self, adapter):
        texto = "A\n\n\n\n\nB"
        resultado = adapter._normalizar(texto)
        # No debe haber más de una línea vacía seguida (máx \n\n)
        assert "\n\n\n" not in resultado
        assert "A" in resultado and "B" in resultado
