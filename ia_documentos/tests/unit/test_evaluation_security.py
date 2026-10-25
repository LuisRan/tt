"""Seguridad del modo evaluacion: sample_id, traversal, symlinks, tamano, formato."""
import os

import pytest

from application.evaluation import (
    AlgorithmInfo, EvaluationRequest, EvaluationService, EvaluationRejected, resolver_pdf,
)

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


class NoDebeLlamarse:
    def process(self, *a, **k):  # pragma: no cover
        raise AssertionError("no debia llegar al pipeline")


def evaluar(base, sid, ruta, max_mb=10):
    s = EvaluationService(NoDebeLlamarse(), "R", AlgorithmInfo("v", None, "h"), base, max_size_mb=max_mb)
    return s.evaluate(EvaluationRequest(sid, ruta)).error


@pytest.mark.parametrize("sid", ["../x", "a/b", "", "a" * 65, "con espacio", "ñ", "a\nb"])
def test_sample_id_invalido(tmp_path, sid):
    assert evaluar(tmp_path, sid, "x.pdf").code == "ID_INVALIDO"


def test_sample_id_no_str(tmp_path):
    assert evaluar(tmp_path, 5, "x.pdf").code == "ID_INVALIDO"


def test_inexistente(tmp_path):
    assert evaluar(tmp_path, "s", "nada.pdf").code == "PDF_INVALIDO"


@pytest.mark.parametrize("ruta", ["", "  ", "a\x00b.pdf"])
def test_ruta_vacia(tmp_path, ruta):
    assert evaluar(tmp_path, "s", ruta).code == "PDF_INVALIDO"


def test_relativa_con_traversal(tmp_path):
    base = tmp_path / "m"
    base.mkdir()
    (tmp_path / "fuera.pdf").write_bytes(PDF)
    assert evaluar(base, "s", "../fuera.pdf").code == "RUTA_NO_PERMITIDA"


def test_symlink_fuera_rechazado(tmp_path):
    base = tmp_path / "m"
    base.mkdir()
    real = tmp_path / "fuera.pdf"
    real.write_bytes(PDF)
    os.symlink(real, base / "enlace.pdf")
    assert evaluar(base, "s", "enlace.pdf").code == "RUTA_NO_PERMITIDA"
    assert evaluar(base, "s", str(base / "enlace.pdf")).code == "RUTA_NO_PERMITIDA"


def test_directorio_symlink_fuera_rechazado(tmp_path):
    base = tmp_path / "m"
    base.mkdir()
    fuera = tmp_path / "fuera"
    fuera.mkdir()
    (fuera / "a.pdf").write_bytes(PDF)
    os.symlink(fuera, base / "dir")
    assert evaluar(base, "s", "dir/a.pdf").code == "RUTA_NO_PERMITIDA"


def test_symlink_dentro_permitido(tmp_path):
    (tmp_path / "a.pdf").write_bytes(PDF)
    os.symlink(tmp_path / "a.pdf", tmp_path / "b.pdf")
    assert resolver_pdf("b.pdf", tmp_path, 10) == (tmp_path / "a.pdf").resolve()


def test_absoluta_fuera_sin_symlink_permitida(tmp_path):
    base = tmp_path / "m"
    base.mkdir()
    (tmp_path / "x.pdf").write_bytes(PDF)
    assert resolver_pdf(str(tmp_path / "x.pdf"), base, 10).name == "x.pdf"


def test_no_regular(tmp_path):
    (tmp_path / "d.pdf").mkdir()
    assert evaluar(tmp_path, "s", "d.pdf").code == "PDF_INVALIDO"


def test_demasiado_grande(tmp_path):
    (tmp_path / "g.pdf").write_bytes(PDF + b"0" * 2 * 1024 * 1024)
    assert evaluar(tmp_path, "s", "g.pdf", max_mb=1).code == "PDF_INVALIDO"


def test_no_es_pdf(tmp_path):
    (tmp_path / "t.pdf").write_bytes(b"hola mundo")
    assert evaluar(tmp_path, "s", "t.pdf").code == "FORMATO_INVALIDO"


def test_rejected_atributos():
    e = EvaluationRejected("X", "m")
    assert (e.code, e.message, e.retryable) == ("X", "m", False)
