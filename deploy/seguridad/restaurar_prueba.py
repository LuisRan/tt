"""Prueba de recuperación sin puertos, sin red y sin montar volúmenes de producción."""
from __future__ import annotations
import json
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path


def ejecutar(app):
    if not app.state.get("snapshot") or not app.state.get("counts"):
        raise RuntimeError("Primero crea el respaldo de seguridad mediante --apply")
    montajes = Path("/proc/mounts").read_text().splitlines()
    if not any(l.split()[1:3] == ["/dev/shm", "tmpfs"] for l in montajes):
        raise RuntimeError("La prueba requiere /dev/shm sobre tmpfs para no escribir dumps legibles a disco")
    archivos = {n.split("_", 1)[0]: n for n in app.state["snapshot"]}
    if not {"pg", "mongo", "archivos", "ia"}.issubset(archivos):
        raise RuntimeError("El respaldo está incompleto")
    subdir = app.state["snapshot_dir"]
    if subdir not in {"antes-v09", "prueba-v09"}:
        raise RuntimeError("Directorio de instantánea inesperado")
    carpeta_backup = app.root / "backups" / subdir
    for nombre in archivos.values():
        if Path(nombre).name != nombre or not (carpeta_backup / nombre).is_file():
            raise RuntimeError("Ruta de respaldo inválida")
    necesarios = sum((carpeta_backup / n).stat().st_size for n in archivos.values())
    if shutil.disk_usage("/dev/shm").free < necesarios * 2 + 64 * 1024 ** 2:
        raise RuntimeError("tmpfs no tiene espacio suficiente para la prueba")
    token = secrets.token_hex(8)
    prefijo = "tt-restore-" + token
    creados = []

    def contenedor(args, nombre):
        r = app.run(["docker", "run", "-d", "--name", nombre, "--network", "none",
                     "--label", "tt.restore=" + token] + args,
                    paso="crear contenedor de prueba")
        cid = r.stdout.decode().strip()
        creados.append(cid)
        return cid

    def ejecutar_en(cid, args, *, archivo=None):
        etiqueta = app.run(["docker", "inspect", "--format", '{{index .Config.Labels "tt.restore"}}', cid]).stdout.decode().strip()
        if etiqueta != token or cid not in creados:
            raise RuntimeError("Destino de restauración no autorizado")
        if archivo:
            with archivo.open("rb") as entrada:
                r = app.run(["docker", "exec", "-i", cid] + args, stdin_file=entrada, timeout=900, check=False)
        else:
            r = app.run(["docker", "exec", "-i", cid] + args, timeout=900, check=False)
        if r.returncode:
            raise RuntimeError("Falló una operación dentro del contenedor aislado")
        return r

    try:
        with tempfile.TemporaryDirectory(prefix=prefijo + "-", dir="/dev/shm") as tmp:
            tmp = Path(tmp)
            # Solo los procesos de esta prueba reciben estas credenciales efímeras.
            (tmp / "secrets.env").write_text("BACKUP_ENCRYPTION_KEY=" + app.state["backup_key"] + "\n")
            os.chmod(tmp / "secrets.env", 0o600)
            backup_image = app.state["new_images"]["backup"]
            for tipo, nombre in archivos.items():
                app.run(["docker", "run", "--rm", "--network", "none",
                         "--env-file", str(tmp / "secrets.env"),
                         "--mount", "type=bind,src=" + str(carpeta_backup / nombre) + ",dst=/entrada,readonly",
                         "--mount", "type=bind,src=" + str(tmp) + ",dst=/salida",
                         "--entrypoint", "python3", backup_image,
                         "/usr/local/bin/archivo_seguro.py", "decrypt", "/entrada", "--output", "/salida/" + tipo],
                        paso="autenticar respaldo " + tipo, timeout=900)
            # Verifica también los tar sin extraer rutas suministradas por un archivo.
            import tarfile
            for tipo in ["archivos", "ia"]:
                with tarfile.open(tmp / tipo, "r:gz") as tar:
                    for miembro in tar:
                        ruta = Path(miembro.name)
                        if ruta.is_absolute() or ".." in ruta.parts or miembro.issym() or miembro.islnk():
                            raise RuntimeError("Ruta no admitida en el respaldo")
                        if miembro.isfile():
                            with tar.extractfile(miembro) as f:
                                while f.read(1024 * 1024):
                                    pass
            cfg = app.config()
            (tmp / "pg.env").write_text("POSTGRES_PASSWORD=" + secrets.token_hex(32) + "\nPOSTGRES_DB=tt_restore\n")
            os.chmod(tmp / "pg.env", 0o600)
            pg = contenedor(["--env-file", str(tmp / "pg.env"), cfg["services"]["postgres"]["image"]], prefijo + "-pg")
            mongo = contenedor([cfg["services"]["mongo"]["image"], "mongod", "--bind_ip", "127.0.0.1"], prefijo + "-mongo")
            for cid, comando in [(pg, ["pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "tt_restore"]),
                                  (mongo, ["mongosh", "--quiet", "--eval", "db.adminCommand('ping').ok"])]:
                for intento in range(90):
                    listo = app.run(["docker", "exec", cid] + comando, check=False)
                    if listo.returncode == 0:
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("El contenedor de prueba no inició")
            ejecutar_en(pg, ["pg_restore", "--exit-on-error", "--clean", "--if-exists", "--no-owner", "--no-acl",
                             "-U", "postgres", "-d", "tt_restore"], archivo=tmp / "pg")
            ejecutar_en(mongo, ["mongorestore", "--archive", "--gzip", "--quiet"], archivo=tmp / "mongo")
            restaurado = {}
            for tabla in app.state["counts"]:
                if tabla == "resultados_ocr":
                    continue
                if not tabla.replace("_", "").isalnum():
                    raise RuntimeError("Tabla no admitida")
                r = ejecutar_en(pg, ["psql", "-X", "-At", "-U", "postgres", "-d", "tt_restore",
                                     "-c", 'SELECT count(*) FROM "' + tabla + '";'])
                restaurado[tabla] = int(r.stdout)
            consulta = "db.getSiblingDB(" + json.dumps(app.state["mongo_database"]) + ").resultados_ocr.countDocuments({})"
            restaurado["resultados_ocr"] = int(ejecutar_en(mongo, ["mongosh", "--quiet", "--eval", consulta]).stdout)
            if restaurado != app.state["counts"]:
                raise RuntimeError("Las cantidades restauradas no coinciden con la instantánea")
            print(json.dumps({"restauracion_aislada": "correcta", "conteos": restaurado,
                              "archivos_y_IA": "autenticados y archivos tar legibles"}), flush=True)
    finally:
        # Solo borra los contenedores creados por ESTA prueba y sus volúmenes anónimos.
        for cid in creados:
            etiqueta = app.run(["docker", "inspect", "--format", '{{index .Config.Labels "tt.restore"}}', cid], check=False).stdout.decode().strip()
            if etiqueta == token:
                app.run(["docker", "rm", "-f", "-v", cid], check=False)
