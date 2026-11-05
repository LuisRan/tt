"""Regresiones OCR sobre datos ficticios; no requiere Docling ni documentos reales."""
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from application.pipeline import _valor_aparece_en_texto
from domain.entities.documento import DocumentoIdentidad, TipoDocumento
from infrastructure.ocr import docling_adapter as da
from infrastructure.ocr.mejora_imagen import lectura_debil, puntuacion_lectura, variantes_rescate


def test_puntuacion_no_premia_ruido_largo():
    limpio = "INSTITUTO NACIONAL ELECTORAL CREDENCIAL PARA VOTAR NOMBRE PRUEBA"
    ruido = ">#$()????  " * 30
    assert puntuacion_lectura(limpio) > puntuacion_lectura(ruido)
    assert lectura_debil(ruido)
    assert not lectura_debil(limpio, minimo=30)


def test_variantes_no_modifican_original_y_son_acotadas():
    imagen = Image.new("RGB", (260, 120), (211, 211, 211))
    ImageDraw.Draw(imagen).text((12, 36), "CURP DOCUMENTO EJEMPLO", fill=(85, 85, 85))
    pixel_original = imagen.getpixel((12, 36))
    variantes = list(variantes_rescate(imagen))
    assert 1 <= len(variantes) <= 2
    assert imagen.getpixel((12, 36)) == pixel_original
    assert all(v.mode == "L" and v.width * v.height <= 12_000_000 for v in variantes)


def test_variantes_limite_de_pixeles():
    imagen = Image.new("L", (400, 300), 210)
    assert all(v.width * v.height <= 60000 for v in variantes_rescate(imagen, max_pixeles=60000))


def test_tesseract_no_hace_rescate_si_el_texto_es_usable(monkeypatch):
    monkeypatch.setattr(da, "OCR_RESCATE_ADAPTATIVO", True)
    ocr = da.DoclingAdapter()
    llamadas = []
    texto = "INSTITUTO NACIONAL ELECTORAL CREDENCIAL PARA VOTAR CLAVE DE ELECTOR " * 5
    ocr._pytesseract = SimpleNamespace(image_to_string=lambda *args, **kw: llamadas.append(kw["config"]) or texto)
    assert ocr._tesseract(Image.new("RGB", (120, 70), "white")) == texto
    assert llamadas == ["--psm 3"]


def test_tesseract_rescate_prefiere_texto_legible_y_limita_pasadas(monkeypatch):
    monkeypatch.setattr(da, "OCR_RESCATE_ADAPTATIVO", True)
    ocr = da.DoclingAdapter()
    respuestas = iter(("?!", "?", "INSTITUTO NACIONAL ELECTORAL CREDENCIAL PARA VOTAR", "#"))
    llamadas = []
    def fake_ocr(*args, **kw):
        llamadas.append(kw["config"])
        return next(respuestas)
    ocr._pytesseract = SimpleNamespace(image_to_string=fake_ocr)
    texto = ocr._tesseract(Image.new("RGB", (180, 80), "white"))
    assert "INSTITUTO NACIONAL" in texto
    assert len(llamadas) <= 4


def test_combinar_texto_conserva_primero_y_elimina_lineas_duplicadas():
    resultado = da.DoclingAdapter._combinar_textos(
        "INSTITUTO NACIONAL ELECTORAL\nNOMBRE: PERSONA FICTICIA\n",
        "nombre: persona ficticia\nCLAVE DE ELECTOR: PRUEBA123\n",
    )
    assert resultado.count("PERSONA FICTICIA") == 1
    assert "CLAVE DE ELECTOR: PRUEBA123" in resultado
    assert resultado.startswith("INSTITUTO")


def test_ine_dos_paginas_lee_reverso(monkeypatch):
    ad = da.DoclingAdapter()
    monkeypatch.setattr(ad, "_extraer_con_docling", lambda _p: "INSTITUTO NACIONAL ELECTORAL\nCREDENCIAL PARA VOTAR")
    monkeypatch.setattr(ad, "_ocr_pagina_completa", lambda *a, **k: None)
    monkeypatch.setattr(ad, "_extraer_multizona", lambda *a, **k: None)
    monkeypatch.setattr(ad, "_ocrmac_disponible", lambda: False)
    monkeypatch.setattr(ad, "_pytesseract_available", False)
    monkeypatch.setattr(ad, "_docling_available", True)
    llamadas = []
    def zona(_p, **kwargs):
        llamadas.append(kwargs.get("pagina", 1))
        return "DATOS REVERSO EJEMPLO" if kwargs.get("pagina") == 2 else ""
    monkeypatch.setattr(ad, "_ocr_zona", zona)
    doc = DocumentoIdentidad(Path("ficticio.pdf"), "usuario", "doc", num_paginas=2)
    resultado = ad.extraer_texto(doc)
    assert resultado.tipo_detectado == TipoDocumento.INE
    assert "DATOS REVERSO EJEMPLO" in resultado.texto_crudo
    assert llamadas.count(2) == 1


def test_fechas_requieren_anio_y_soportan_formatos():
    assert _valor_aparece_en_texto("2024-11-03", "EXPEDICION 03/11/2024", "fecha_expedicion")
    assert _valor_aparece_en_texto("03/11/2024", "3 de noviembre de 2024", "fecha_registro")
    assert not _valor_aparece_en_texto("03/11/2024", "EXPEDICION 03/11/2022", "fecha_expedicion")
    assert not _valor_aparece_en_texto("03/11/2024", "EXPEDICION 03/11", "fecha_expedicion")
    assert not _valor_aparece_en_texto("99/19/2024", "99/19/2024", "fecha_expedicion")


def test_tesseract_si_falla_opencv_conserva_lectura_base(monkeypatch):
    monkeypatch.setattr(da, "OCR_RESCATE_ADAPTATIVO", True)
    def preprocesado_fallido(_img):
        raise RuntimeError("filtro no disponible")
    monkeypatch.setattr(da, "variantes_rescate", preprocesado_fallido)
    ad = da.DoclingAdapter()
    ad._pytesseract = SimpleNamespace(image_to_string=lambda *a, **k: "NOMBRE PRUEBA")
    assert ad._tesseract(Image.new("RGB", (180, 60), "white")) == "NOMBRE PRUEBA"
