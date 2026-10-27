"""R8/R9: IA_REQUIRE_API_KEY, aviso de modo abierto y 500 sin fugas."""
import io
import logging

import pytest

from config import settings
from integration.flask_app import create_app


class Explota:
    def process(self, *a, **k):
        raise ValueError("SECRETO-INTERNO /ruta/privada")


def test_require_api_key_sin_clave_falla(monkeypatch):
    monkeypatch.setattr(settings, "IA_API_KEY", "")
    monkeypatch.setattr(settings, "IA_REQUIRE_API_KEY", True)
    with pytest.raises(RuntimeError, match="IA_API_KEY"):
        create_app(Explota())


def test_require_api_key_con_clave_ok(monkeypatch):
    monkeypatch.setattr(settings, "IA_API_KEY", "k")
    monkeypatch.setattr(settings, "IA_REQUIRE_API_KEY", True)
    create_app(Explota())


def test_modo_abierto_emite_un_warning(monkeypatch, caplog):
    monkeypatch.setattr(settings, "IA_API_KEY", "")
    monkeypatch.setattr(settings, "IA_REQUIRE_API_KEY", False)
    with caplog.at_level(logging.WARNING, logger="ia.api"):
        create_app(Explota())
    avisos = [r for r in caplog.records if r.getMessage() == "api_sin_clave_modo_abierto"]
    assert len(avisos) == 1 and avisos[0].levelno == logging.WARNING


def test_500_no_filtra_excepcion(monkeypatch, caplog):
    monkeypatch.setattr(settings, "IA_API_KEY", "")
    monkeypatch.setattr(settings, "IA_REQUIRE_API_KEY", False)
    c = create_app(Explota()).test_client()
    data = {"archivo": (io.BytesIO(b"%PDF-1.4\n%%EOF"), "d.pdf")}
    with caplog.at_level(logging.ERROR, logger="ia.api"):
        r = c.post("/api/v1/documentos/procesa", data=data,
                   content_type="multipart/form-data")
    assert r.status_code == 500
    cuerpo = r.get_json()
    assert set(cuerpo) == {"error", "codigo", "detalle"}
    assert "SECRETO" not in r.get_data(as_text=True)
    cid = cuerpo["detalle"].split("=")[1]
    assert len(cid) == 12
    reg = [x for x in caplog.records if getattr(x, "correlacion", None) == cid]
    assert reg and reg[0].exc_info
