"""R1/R2: pixel-bomb, PDFs cifrados y contenido activo."""
from pathlib import Path

import pypdf
import pytest

from infrastructure.ocr import pdf_validator
from infrastructure.ocr.pdf_validator import PdfValidationError, validar_pdf


def _pdf(ruta: Path, ancho: float = 595, alto: float = 842, *, js=False,
         cifrar=False, user_pwd="clave", adjunto=False, launch=False) -> Path:
    w = pypdf.PdfWriter()
    w.add_blank_page(width=ancho, height=alto)
    if js:
        w.add_js("app.alert('x');")
    if adjunto:
        w.add_attachment("a.txt", b"hola")
    if launch:
        from pypdf.generic import DictionaryObject, NameObject, TextStringObject
        w._root_object[pypdf.generic.NameObject("/OpenAction")] = DictionaryObject({
            NameObject("/S"): NameObject("/Launch"),
            NameObject("/F"): TextStringObject("calc.exe"),
        })
    if cifrar:
        w.encrypt(user_password=user_pwd, owner_password="x")
    with open(ruta, "wb") as f:
        w.write(f)
    return ruta


def test_a4_normal_pasa(tmp_path):
    h, n = validar_pdf(_pdf(tmp_path / "a4.pdf"))
    assert n == 1 and len(h) == 64


def test_pixel_bomb_rechazada_sin_rasterizar(tmp_path, monkeypatch):
    llamadas = []
    monkeypatch.setattr("infrastructure.platform_config.renderizar_paginas",
                        lambda *a, **k: llamadas.append(1) or [])
    with pytest.raises(PdfValidationError, match="exceden"):
        validar_pdf(_pdf(tmp_path / "bomba.pdf", 14400, 14400))
    assert llamadas == []


def test_tope_pixeles_configurable(tmp_path, monkeypatch):
    ruta = _pdf(tmp_path / "a4.pdf")
    monkeypatch.setattr(pdf_validator, "_tope_pixeles", lambda: 1_000_000)
    with pytest.raises(PdfValidationError, match="MP"):
        validar_pdf(ruta)


def test_a4_a_300dpi_cabe_en_tope_por_defecto(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_validator, "_dpi_render", lambda: 300)
    validar_pdf(_pdf(tmp_path / "a4.pdf"))


def test_pdf_cifrado_con_contrasena_rechazado(tmp_path):
    with pytest.raises(PdfValidationError, match="cifrado"):
        validar_pdf(_pdf(tmp_path / "c.pdf", cifrar=True))


def test_pdf_cifrado_con_clave_vacia_aceptado(tmp_path):
    ruta = _pdf(tmp_path / "v.pdf", cifrar=True, user_pwd="")
    assert pypdf.PdfReader(str(ruta)).is_encrypted
    h, n = validar_pdf(ruta)
    assert n == 1 and len(h) == 64


def test_cifrado_vacio_con_js_sigue_rechazado(tmp_path):
    with pytest.raises(PdfValidationError):
        validar_pdf(_pdf(tmp_path / "vj.pdf", cifrar=True, user_pwd="", js=True))


def test_javascript_rechazado(tmp_path):
    with pytest.raises(PdfValidationError, match="activo"):
        validar_pdf(_pdf(tmp_path / "js.pdf", js=True))


def test_adjunto_rechazado(tmp_path):
    with pytest.raises(PdfValidationError, match="adjuntos"):
        validar_pdf(_pdf(tmp_path / "adj.pdf", adjunto=True))


def test_launch_rechazado(tmp_path):
    with pytest.raises(PdfValidationError):
        validar_pdf(_pdf(tmp_path / "l.pdf", launch=True))
