"""Fixture exclusivamente local invocada por integracion_real.mjs."""
import os
import secrets
import subprocess
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backup"))
from archivo_seguro import decrypt_file
from respaldo import pg_env, mongo_config, respaldar


def main():
    root = Path(os.environ["TEST_FOLDER"])
    if Path.cwd() != root or not root.name.startswith("tt-security-test-"):
        raise RuntimeError("Solo se ejecuta dentro de la prueba efímera")
    for k in ["DATABASE_URL", "MONGO_URL", "RESTORE_OWNER_MONGO"]:
        if urlsplit(os.environ[k]).hostname != "127.0.0.1":
            raise RuntimeError("La prueba requiere bases locales")
    if os.environ["RESTORE_DATABASE"] != "tt_restore_fixture":
        raise RuntimeError("Destino de prueba incorrecto")
    backups = root / "respaldos"
    password = secrets.token_hex(40)
    os.environ.update(BACKUP_DIR=str(backups), BACKUP_ENCRYPTION_KEY=password)
    fuentes = [root / "archivos", root / "ia"]
    for ruta in fuentes:
        ruta.mkdir()
        (ruta / "fixture.bin").write_bytes(b"solo-dato-sintetico")
    env_pg = dict(pg_env(), PGDATABASE="tt_restore_fixture", PGUSER="postgres")
    resultado = respaldar(storage=fuentes[0], iadata=fuentes[1])
    assert len(resultado["respaldos"]) == 4
    os.environ["MONGO_URL"] = os.environ["RESTORE_OWNER_MONGO"]
    conf = mongo_config(root)
    for nombre in resultado["respaldos"]:
        tipo = nombre.split("_")[0]
        nuevo = root / ("validar-" + tipo)
        decrypt_file(backups / nombre, nuevo, password)
        if tipo == "pg":
            subprocess.run(["pg_restore", "--exit-on-error", "--clean", "--if-exists", "--no-owner", "--no-acl",
                            "-d", "tt_restore_fixture", str(nuevo)], env=env_pg, check=True)
        elif tipo == "mongo":
            # Omitir la base en la URI para que --nsFrom/--nsTo seleccione el destino.
            from urllib.parse import urlunsplit
            u = urlsplit(os.environ["MONGO_URL"])
            os.environ["MONGO_URL"] = urlunsplit((u.scheme, u.netloc, "/", u.query, ""))
            conf.unlink()
            conf = mongo_config(root)
            subprocess.run(["mongorestore", "--config", str(conf), "--gzip", "--archive=" + str(nuevo),
                            "--quiet", "--nsFrom=tt_security_fixture.*", "--nsTo=tt_restore_fixture.*"], check=True)
        else:
            with tarfile.open(nuevo, "r:gz") as tar:
                assert tar.extractfile("./fixture.bin").read() == b"solo-dato-sintetico"


if __name__ == "__main__":
    main()
