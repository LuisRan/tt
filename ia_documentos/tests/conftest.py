"""Llave sintética exclusiva de pruebas; nunca se reutiliza para datos reales."""
import base64
import pytest


@pytest.fixture(autouse=True)
def llave_almacenamiento_sintetica(monkeypatch):
    monkeypatch.delenv("IA_STORAGE_KEY_FILE", raising=False)
    monkeypatch.setenv("IA_STORAGE_KEY", base64.b64encode(b"T" * 32).decode())
