#!/usr/bin/env python3
"""Exporta directamente a cifrado GCM; no crea dumps intermedios legibles."""
from __future__ import annotations
import argparse
import datetime
import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs
from archivo_seguro import encrypt_stream


def pg_env() -> dict:
    uri = urlparse(os.environ["DATABASE_URL"])
    args = parse_qs(uri.query)
    env = dict(os.environ, PGHOST=uri.hostname or "postgres", PGPORT=str(uri.port or 5432),
               PGDATABASE=unquote(uri.path.lstrip("/")), PGUSER=unquote(uri.username or ""),
               PGPASSWORD=unquote(uri.password or ""))
    for k in ["sslmode", "sslrootcert"]:
        if k in args:
            env["PG" + k.upper()] = args[k][0]
    return env


def mongo_config(carpeta: Path) -> Path:
    ruta = carpeta / "mongo.yml"
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as archivo:
        archivo.write("uri: " + json.dumps(os.environ["MONGO_URL"]) + "\n")
    return ruta


def exportar(comando: list[str], destino: Path, env: dict) -> None:
    temporal = destino.with_name("." + destino.name + ".pending")
    proceso = subprocess.Popen(comando, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    try:
        encrypt_stream(proceso.stdout, temporal, os.environ["BACKUP_ENCRYPTION_KEY"])
        if proceso.wait() != 0:
            raise RuntimeError("Falló la exportación")
        os.replace(temporal, destino)
    finally:
        if proceso.poll() is None:
            proceso.kill()
        proceso.wait()
        proceso.stdout.close()
        temporal.unlink(missing_ok=True)


def respaldar(*, storage=Path("/storage"), iadata=Path("/iadata")) -> dict:
    os.umask(0o077)
    destino = Path(os.environ.get("BACKUP_DIR", "/backups"))
    destino.mkdir(parents=True, exist_ok=True, mode=0o700)
    fecha = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    retencion = max(1, int(os.environ.get("BACKUP_RETENCION", "7")))
    pg = pg_env()
    salidas = []
    with (destino / ".respaldo.lock").open("a") as lock, tempfile.TemporaryDirectory(prefix="tt-respaldo-") as carpeta:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = mongo_config(Path(carpeta))
        trabajos = [
            ("pg", ["pg_dump", "-Fc", "--no-owner"], pg),
            ("mongo", ["mongodump", "--config", str(config), "--archive", "--gzip", "--quiet"], dict(os.environ)),
            ("archivos", ["tar", "-czf", "-", "-C", str(storage), "."], dict(os.environ)),
        ]
        if iadata.is_dir():
            trabajos.append(("ia", ["tar", "-czf", "-", "-C", str(iadata), "."], dict(os.environ)))
        filas = []
        try:
            for tipo, comando, entorno in trabajos:
                archivo = destino / (tipo + "_" + fecha + ".enc")
                exportar(comando, archivo, entorno)
                salidas.append(archivo.name)
                filas.append("('" + tipo + "','" + archivo.name + "'," + str(archivo.stat().st_size) + ",'completado')")
            sentencia = "INSERT INTO respaldos(tipo,ubicacion,tamano,estado) VALUES " + ",".join(filas)
            subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-q", "-c", sentencia],
                           env=pg, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except Exception:
            for nombre in salidas:
                (destino / nombre).unlink(missing_ok=True)
            raise
        for tipo, _, _ in trabajos:
            completos = [p for p in destino.glob(tipo + "_*.enc")
                         if re.fullmatch(tipo + r"_\d{8}_\d{6}_\d{6}\.enc", p.name)]
            for viejo in sorted(completos, reverse=True)[retencion:]:
                viejo.unlink()
    return {"respaldos": salidas, "formato": "AES-256-GCM-v2"}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--export-recovery", action="store_true")
    a = p.parse_args()
    if a.export_recovery:
        destino = Path(os.environ.get("BACKUP_DIR", "/backups"))
        if not Path("/recovery").is_dir():
            raise SystemExit("Falta el montaje privado de recuperación")
        nombre = "claves_" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S") + ".enc"
        exportar(["tar", "-czf", "-", "-C", "/recovery", "."], destino / nombre, dict(os.environ))
        print(json.dumps({"recuperacion": nombre, "requiere": "BACKUP_ENCRYPTION_KEY conservada por separado"}))
        raise SystemExit(0)
    if not a.once:
        time.sleep(max(0, int(os.environ.get("BACKUP_RETRASO_INICIAL", "120"))))
    while True:
        try:
            print(json.dumps(respaldar()), flush=True)
        except Exception as e:
            print(json.dumps({"respaldo": "fallido", "tipo_error": type(e).__name__}), flush=True)
            if a.once:
                raise SystemExit(1)
        if a.once:
            break
        time.sleep(max(1, int(os.environ.get("BACKUP_INTERVALO_HORAS", "24"))) * 3600)
