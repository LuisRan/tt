import base64
import json
import os
import importlib.util
from pathlib import Path
import pytest
from infrastructure.storage.crypto_file import MAGIC, StorageIntegrityError, read_private, write_private, decode_bytes
from infrastructure.storage.file_adapters import JsonFileAdapter, MarkdownFileAdapter


def cargar_migracion():
    ruta = Path(__file__).resolve().parents[2] / "scripts/migrar_almacenamiento.py"
    spec = importlib.util.spec_from_file_location("migracion_prueba", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_roundtrip_nonce_y_ausencia_de_texto(tmp_path):
    p = tmp_path / "doc.json"
    secreto = '{"dato":"persona sintética ñ", "numero": 123}'
    write_private(p, secreto)
    primero = p.read_bytes()
    write_private(p, secreto)
    assert primero != p.read_bytes()
    assert p.read_bytes().startswith(MAGIC)
    assert b"persona" not in p.read_bytes()
    assert read_private(p) == secreto
    assert p.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("cambio", ["tag", "nonce", "nombre", "truncado", "llave", "plano"])
def test_no_entrega_datos_alterados(tmp_path, monkeypatch, cambio):
    p = tmp_path / "doc.md"
    write_private(p, "dato sintético")
    if cambio in ["tag", "nonce"]:
        b = bytearray(p.read_bytes())
        b[-1 if cambio == "tag" else len(MAGIC)] ^= 1
        p.write_bytes(b)
    elif cambio == "nombre":
        otro = tmp_path / "otro.md"
        p.rename(otro)
        p = otro
    elif cambio == "truncado":
        p.write_bytes(p.read_bytes()[:10])
    elif cambio == "llave":
        monkeypatch.setenv("IA_STORAGE_KEY", base64.b64encode(b"X" * 32).decode())
    else:
        p.write_text("texto sin cifrar")
    with pytest.raises(StorageIntegrityError):
        read_private(p)


def test_sin_llave_no_vuelve_a_texto_plano(tmp_path, monkeypatch):
    monkeypatch.delenv("IA_STORAGE_KEY")
    with pytest.raises(StorageIntegrityError):
        JsonFileAdapter(tmp_path)
    assert not list(tmp_path.iterdir())


def test_rechaza_recorrido_de_ruta(tmp_path):
    r = JsonFileAdapter(tmp_path)
    with pytest.raises(ValueError):
        r.guardar_resultado({}, "../fuera", "u1")


def test_borrado_actualiza_indice_sin_afectar_otro_documento(tmp_path):
    r = JsonFileAdapter(tmp_path)
    for doc in ["d1", "d2"]:
        r.guardar_resultado({"user_id": "u1", "dato": doc}, doc, "u1")
    assert r.eliminar("d1")
    assert r.obtener_resultado("d1") is None
    assert r.obtener_resultado("d2")["dato"] == "d2"
    assert [d["doc_id"] for d in r.listar_documentos_usuario("u1")] == ["d2"]
    assert r.eliminar("d1") is False


def test_migracion_idempotente_y_reversion_binaria(tmp_path, monkeypatch):
    m = cargar_migracion()
    entrada, raw, resultados = [tmp_path / x for x in ["input", "raw", "json"]]
    for p in [entrada, raw, resultados]: p.mkdir()
    (entrada / "ejemplo.pdf").write_bytes(b"%PDF-1.4\x00\xffdatos sinteticos")
    (raw / "d1.md").write_text("texto sintético")
    (resultados / "d1.json").write_text('{"user_id":"u1","datos":{"dato":"sintético"}}')
    for k, v in [("INPUT_DIR", entrada), ("RAW_TEXT_DIR", raw), ("JSON_RESULTS_DIR", resultados), ("DATA_DIR", tmp_path)]:
        monkeypatch.setattr(m, k, v)
    originales = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert m.migrate()["pendientes"] == 3
    assert m.migrate(True)["convertidos"] == 3
    assert m.migrate(True)["convertidos"] == 0
    for p, original in originales.items():
        assert decode_bytes(p, p.read_bytes()) == original
    assert m.migrate(True, True)["convertidos"] == 3
    assert {p: p.read_bytes() for p in originales} == originales


def test_valida_todo_antes_de_escribir(tmp_path, monkeypatch):
    m = cargar_migracion()
    for k in ["RAW_TEXT_DIR", "JSON_RESULTS_DIR", "INPUT_DIR", "DATA_DIR"]:
        monkeypatch.setattr(m, k, tmp_path)
    p = tmp_path / "a.md"
    p.write_text("texto anterior")
    (tmp_path / "z.json").write_text("{no es json")
    with pytest.raises(json.JSONDecodeError): m.migrate(True)
    assert p.read_text() == "texto anterior"
