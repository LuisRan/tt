"""Pruebas de la deteccion de sistema operativo y eleccion de motor OCR."""
import pytest

from config import settings
from infrastructure import platform_config as pc


@pytest.fixture(autouse=True)
def limpiar_cache():
    pc.detectar_plataforma.cache_clear()
    yield
    pc.detectar_plataforma.cache_clear()


@pytest.mark.parametrize("so,esperado", [("Darwin", "macos"), ("Windows", "windows"), ("Linux", "linux")])
def test_detecta_sistema(monkeypatch, so, esperado):
    monkeypatch.setattr(pc.platform, "system", lambda: so)
    assert pc._detectar_sistema() == esperado


def test_mac_usa_ocrmac_si_esta_instalado(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENGINE", "auto")
    monkeypatch.setattr(pc.importlib.util, "find_spec", lambda n: object() if n == "ocrmac" else None)
    assert pc._elegir_motor("macos", "/opt/homebrew/bin/tesseract", []) == "ocrmac"


def test_windows_usa_tesseract(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENGINE", "auto")
    monkeypatch.setattr(pc.importlib.util, "find_spec", lambda n: None)
    assert pc._elegir_motor("windows", r"C:\Program Files\Tesseract-OCR\tesseract.exe", []) == "tesseract"


def test_sin_tesseract_cae_a_rapidocr(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENGINE", "auto")
    monkeypatch.setattr(pc.importlib.util, "find_spec", lambda n: object() if n == "rapidocr" else None)
    assert pc._elegir_motor("windows", None, []) == "rapidocr"


def test_motor_forzado_por_env(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENGINE", "easyocr")
    assert pc._elegir_motor("macos", None, []) == "easyocr"


def test_apple_silicon_usa_mps(monkeypatch):
    monkeypatch.setattr(settings, "DOCLING_DEVICE", "auto")
    assert pc._elegir_dispositivo("macos", True) == "mps"


def test_tablas_desactivadas_en_mac_8gb(monkeypatch):
    monkeypatch.setattr(settings, "DOCLING_TABLES", "auto")
    assert pc._tablas_habilitadas("macos", 8.0) is False
    assert pc._tablas_habilitadas("windows", 16.0) is True
    monkeypatch.setattr(settings, "DOCLING_TABLES", "true")
    assert pc._tablas_habilitadas("macos", 8.0) is True


def test_detectar_plataforma_devuelve_info():
    info = pc.detectar_plataforma()
    assert info.sistema in {"macos", "windows", "linux"}
    assert info.renderizador_pdf in {"poppler", "pypdfium2", "ninguno"}
    assert isinstance(info.to_dict(), dict)
