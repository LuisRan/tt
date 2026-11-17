"""Cifrado autenticado de las copias OCR. La llave vive fuera del volumen de datos."""
from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"TTIA1\x00"


class StorageIntegrityError(RuntimeError):
    pass


def storage_key() -> bytes:
    archivo = os.environ.get("IA_STORAGE_KEY_FILE", "").strip()
    valor = Path(archivo).read_text().strip() if archivo else os.environ.get("IA_STORAGE_KEY", "").strip()
    try:
        clave = base64.b64decode(valor, validate=True)
    except Exception:
        raise StorageIntegrityError("Llave de almacenamiento IA inválida") from None
    if len(clave) != 32:
        raise StorageIntegrityError("Configura IA_STORAGE_KEY_FILE o IA_STORAGE_KEY con 32 bytes en base64")
    return clave


def _aad(ruta: Path) -> bytes:
    # El identificador se conserva al mover el volumen; cambiar el nombre invalida el paquete.
    return b"tt-ia-storage-v1:" + ruta.name.encode("utf-8")


def decode_bytes(ruta: Path, paquete: bytes) -> bytes:
    if not paquete.startswith(MAGIC) or len(paquete) < len(MAGIC) + 28:
        raise StorageIntegrityError("Copia IA sin cifrado o con formato inválido: ejecuta la migración")
    inicio = len(MAGIC)
    try:
        plano = AESGCM(storage_key()).decrypt(paquete[inicio:inicio + 12], paquete[inicio + 12:], _aad(ruta))
        return plano
    except StorageIntegrityError:
        raise
    except Exception:
        raise StorageIntegrityError("No se pudo autenticar la copia IA") from None


def read_private(ruta: Path) -> str:
    return decode(ruta, ruta.read_bytes())


def decode(ruta: Path, paquete: bytes) -> str:
    return decode_bytes(ruta, paquete).decode("utf-8")


def write_private(ruta: Path, contenido: str | bytes, *, decrypt_for_rollback: bool = False) -> None:
    plano = contenido.encode("utf-8") if isinstance(contenido, str) else contenido
    nonce = os.urandom(12)
    paquete = plano if decrypt_for_rollback else MAGIC + nonce + AESGCM(storage_key()).encrypt(nonce, plano, _aad(ruta))
    fd, nombre = tempfile.mkstemp(prefix=".tt-", suffix=".tmp", dir=ruta.parent)
    temporal = Path(nombre)
    try:
        with os.fdopen(fd, "wb") as archivo:
            archivo.write(paquete)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, ruta)
        os.chmod(ruta, 0o600)
    finally:
        temporal.unlink(missing_ok=True)
