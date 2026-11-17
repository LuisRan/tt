"""Respaldos autenticados y en streaming. Nunca entrega texto antes de verificar GCM."""
from __future__ import annotations
import argparse
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"TTBACKUP2\x00"
CHUNK = 1024 * 1024


def _key(password: str, salt: bytes) -> bytes:
    if len(password) < 32:
        raise ValueError("La clave de respaldo debe tener al menos 32 caracteres")
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000, 32)


def encrypt_stream(origen, destino: Path, password: str) -> None:
    salt, nonce = os.urandom(16), os.urandom(12)
    cabecera = MAGIC + salt + nonce
    cipher = Cipher(algorithms.AES(_key(password, salt)), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(cabecera)
    fd, tmp = tempfile.mkstemp(prefix=".cifrado-", dir=destino.parent)
    try:
        with os.fdopen(fd, "wb") as salida:
            salida.write(cabecera)
            while bloque := origen.read(CHUNK):
                salida.write(cipher.update(bloque))
            salida.write(cipher.finalize())
            salida.write(cipher.tag)
            salida.flush()
            os.fsync(salida.fileno())
        os.replace(tmp, destino)
    finally:
        Path(tmp).unlink(missing_ok=True)


def decrypt_file(origen: Path, destino: Path, password: str, *, legacy: bool = False) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".restauracion-", dir=destino.parent)
    try:
        with origen.open("rb") as entrada, os.fdopen(fd, "wb") as salida:
            cabecera = entrada.read(len(MAGIC) + 28)
            if not cabecera.startswith(MAGIC):
                if not legacy:
                    raise ValueError("Respaldo antiguo sin autenticación; requiere --allow-legacy")
                entorno = dict(os.environ, BACKUP_ENCRYPTION_KEY=password)
                proceso = subprocess.run([
                    "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                    "-pass", "env:BACKUP_ENCRYPTION_KEY", "-in", str(origen)
                ], stdout=salida, stderr=subprocess.DEVNULL, env=entorno)
                if proceso.returncode:
                    raise ValueError("No se pudo descifrar el respaldo antiguo")
            else:
                if origen.stat().st_size < len(cabecera) + 16:
                    raise ValueError("Respaldo incompleto")
                salt, nonce = cabecera[len(MAGIC):len(MAGIC) + 16], cabecera[-12:]
                entrada.seek(-16, 2)
                tag = entrada.read(16)
                cipher = Cipher(algorithms.AES(_key(password, salt)), modes.GCM(nonce, tag)).decryptor()
                cipher.authenticate_additional_data(cabecera)
                entrada.seek(len(cabecera))
                restantes = origen.stat().st_size - len(cabecera) - 16
                while restantes:
                    bloque = entrada.read(min(CHUNK, restantes))
                    if not bloque:
                        raise ValueError("Respaldo incompleto")
                    restantes -= len(bloque)
                    salida.write(cipher.update(bloque))
                salida.write(cipher.finalize())
            salida.flush()
            os.fsync(salida.fileno())
        # Solo publica el archivo si se verificó la etiqueta.
        os.replace(tmp, destino)
    finally:
        Path(tmp).unlink(missing_ok=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("accion", choices=["verify", "decrypt"])
    p.add_argument("archivo", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--allow-legacy", action="store_true")
    a = p.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="tt-verificar-") as carpeta:
            destino = a.output if a.accion == "decrypt" else Path(carpeta) / "verificado"
            if destino is None or destino.exists():
                raise ValueError("Destino nuevo requerido")
            decrypt_file(a.archivo, destino, os.environ["BACKUP_ENCRYPTION_KEY"], legacy=a.allow_legacy)
        with a.archivo.open("rb") as ver_formato:
            es_legado = ver_formato.read(len(MAGIC)) != MAGIC
        if a.allow_legacy and es_legado:
            print("Respaldo legado legible; no aporta autenticación criptográfica")
        else:
            print("Respaldo verificado" if a.accion == "verify" else "Descifrado completado en destino privado")
    except Exception as e:
        print("Operación detenida: " + type(e).__name__)
        raise SystemExit(1)
