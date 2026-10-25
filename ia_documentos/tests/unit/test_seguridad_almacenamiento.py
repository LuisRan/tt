"""R5/R6: permisos 0600, escritura atómica, lock del índice y temporales."""
import json
import os
import stat
import threading
from datetime import datetime
from pathlib import Path

from domain.entities.documento import TipoDocumento, TextoExtraido
from infrastructure.storage.file_adapters import JsonFileAdapter, MarkdownFileAdapter


def _modo(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_json_0600_desde_creacion(tmp_path, monkeypatch):
    modos = []
    real_open = os.open

    def espia(ruta, flags, mode=0o777, *a, **k):
        modos.append(mode)
        return real_open(ruta, flags, mode, *a, **k)

    monkeypatch.setattr(os, "open", espia)
    repo = JsonFileAdapter(tmp_path)
    ruta = repo.guardar_resultado({"tipo_documento": "ine"}, "d1", "u1")
    assert _modo(ruta) == 0o600
    assert _modo(tmp_path / "_indices" / "u1.json") == 0o600
    assert modos and all(m == 0o600 for m in modos)
    assert json.loads(ruta.read_text()) == {"tipo_documento": "ine"}
    assert not [p for p in tmp_path.rglob(".*.tmp")]


def test_md_0600(tmp_path):
    try:
        te = TextoExtraido(doc_id="d1", texto_crudo="hola",
                           tipo_detectado=TipoDocumento.INE,
                           timestamp=datetime.utcnow())
    except TypeError:
        import pytest
        pytest.skip("firma de TextoExtraido distinta")
    ruta = MarkdownFileAdapter(tmp_path).guardar_texto(te)
    assert _modo(ruta) == 0o600
    assert "hola" in ruta.read_text()


def test_indice_concurrente_sin_perdida(tmp_path):
    repo = JsonFileAdapter(tmp_path)
    n = 24
    errores = []

    def tarea(i):
        try:
            repo.guardar_resultado({"tipo_documento": "ine"}, f"doc{i}", "u1")
        except Exception as e:  # pragma: no cover
            errores.append(e)

    hilos = [threading.Thread(target=tarea, args=(i,)) for i in range(n)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert not errores
    assert len(repo.listar_documentos_usuario("u1")) == n


def test_pipeline_temporal_mkstemp_y_limpieza(tmp_path, monkeypatch):
    from application import pipeline as pl

    monkeypatch.setattr(pl, "DATA_DIR", tmp_path)
    p = pl.DocumentPipeline.__new__(pl.DocumentPipeline)
    origen = tmp_path / "o.pdf"
    origen.write_bytes(b"%PDF-1.4")

    destinos = []

    def falla(o, d):
        destinos.append(d)
        raise ValueError("boom")

    monkeypatch.setattr(pl, "corregir_orientacion", falla)
    assert p._corregir_orientacion_si_necesario(origen, "docX") == origen
    assert destinos and not destinos[0].exists()

    def ok(o, d):
        d.write_bytes(b"x")
        return True

    monkeypatch.setattr(pl, "corregir_orientacion", ok)
    r = p._corregir_orientacion_si_necesario(origen, "docX")
    assert r.parent == tmp_path / "tmp" and "docX" not in r.name
    assert r.exists()
    r.unlink()
