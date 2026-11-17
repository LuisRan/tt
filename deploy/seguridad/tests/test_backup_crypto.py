import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backup"))
from archivo_seguro import encrypt_stream, decrypt_file, MAGIC, CHUNK

KEY = "fixture-key-never-use-for-production-0123456789"


def test_streaming_binario_y_nonce_aleatorio(tmp_path):
    datos = (b"%PDF-sintetico\x00\xff" * (CHUNK // 10)) + b"final"
    a, b, plano = [tmp_path / n for n in ["a.enc", "b.enc", "plain"]]
    encrypt_stream(io.BytesIO(datos), a, KEY)
    encrypt_stream(io.BytesIO(datos), b, KEY)
    assert a.read_bytes() != b.read_bytes()
    assert a.read_bytes().startswith(MAGIC)
    assert b"%PDF-sintetico" not in a.read_bytes()
    decrypt_file(a, plano, KEY)
    assert plano.read_bytes() == datos
    assert plano.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("cambio", ["tag", "cabecera", "cuerpo", "truncado", "clave"])
def test_no_publica_descifrado_sin_autenticidad(tmp_path, cambio):
    p, destino = tmp_path / "respaldo", tmp_path / "salida"
    encrypt_stream(io.BytesIO(b"datos sinteticos"), p, KEY)
    paquete = bytearray(p.read_bytes())
    if cambio == "tag": paquete[-1] ^= 1
    elif cambio == "cabecera": paquete[len(MAGIC) + 2] ^= 1
    elif cambio == "cuerpo": paquete[-18] ^= 1
    elif cambio == "truncado": paquete = paquete[:15]
    p.write_bytes(paquete)
    destino.write_bytes(b"destino anterior")
    with pytest.raises(Exception):
        decrypt_file(p, destino, KEY + ("otra" if cambio == "clave" else ""))
    assert destino.read_bytes() == b"destino anterior"
    assert not list(tmp_path.glob(".restauracion-*"))


def test_legado_requiere_eleccion_explicita(tmp_path):
    origen = tmp_path / "origen"
    origen.write_bytes(b"respaldo sintetico antiguo")
    cifrado, salida = tmp_path / "legado.enc", tmp_path / "salida"
    subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                    "-salt", "-pass", "env:BACKUP_ENCRYPTION_KEY",
                    "-in", str(origen), "-out", str(cifrado)],
                   env=dict(os.environ, BACKUP_ENCRYPTION_KEY=KEY), check=True, capture_output=True)
    with pytest.raises(ValueError): decrypt_file(cifrado, salida, KEY)
    assert not salida.exists()
    decrypt_file(cifrado, salida, KEY, legacy=True)
    assert salida.read_bytes() == origen.read_bytes()


def test_clave_corta_no_crea_archivo(tmp_path):
    destino = tmp_path / "destino"
    with pytest.raises(ValueError): encrypt_stream(io.BytesIO(b"datos"), destino, "corta")
    assert not destino.exists()
