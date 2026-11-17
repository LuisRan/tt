#!/usr/bin/env python3
"""Endurecimiento de una instalación existente. Docker local, sin cambiar puertos web."""
from __future__ import annotations
import argparse
import base64
import fcntl
import json
import os
import re
import secrets
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, unquote, quote, parse_qsl, urlencode


class ErrorDespliegue(RuntimeError):
    pass


def write_private(ruta: Path, texto: str | bytes, mode=0o600, gid=0, uid=0):
    ruta.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, nombre = tempfile.mkstemp(prefix=".tt-", dir=ruta.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(texto.encode() if isinstance(texto, str) else texto)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(nombre, mode)
        os.chown(nombre, uid, gid)
        os.replace(nombre, ruta)
    finally:
        Path(nombre).unlink(missing_ok=True)


def secure_uri(uri: str, *, mongo=False, user=None, password=None, database=None) -> str:
    u = urlsplit(uri)
    if u.hostname not in (["mongo"] if mongo else ["postgres"]):
        raise ErrorDespliegue("Se requiere la base del Compose local, no un servidor remoto")
    usuario = user if user is not None else unquote(u.username or "")
    clave = password if password is not None else unquote(u.password or "")
    db = database if database is not None else unquote(u.path.lstrip("/"))
    q = dict(parse_qsl(u.query))
    if mongo:
        q.update(tls="true", tlsCAFile="/run/tt/ca.crt")
        if user is not None:
            q["authSource"] = db
        for k in ["tlsInsecure", "tlsAllowInvalidCertificates", "tlsAllowInvalidHostnames"]:
            q.pop(k, None)
    else:
        for k in list(q):
            if k.lower().startswith("ssl"):
                del q[k]
        q.update(sslmode="verify-full", sslrootcert="/run/tt/ca.crt")
    autoridad = quote(usuario, safe="") + ":" + quote(clave, safe="") + "@" + u.hostname + ":" + str(u.port or (27017 if mongo else 5432))
    return urlunsplit((u.scheme, autoridad, "/" + quote(db, safe=""), urlencode(q), ""))


class Installer:
    def __init__(self, proyecto: Path):
        self.root = proyecto.resolve()
        self.private = self.root / "deploy/seguridad/private"
        self.state_path = self.private / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.secretos = []
        self.base = ["docker", "compose", "--env-file", ".env"]
        self.unit = "tt-security-" + hashlib.sha256(str(self.root).encode()).hexdigest()[:10]

    def lock(self):
        if os.geteuid() != 0:
            raise ErrorDespliegue("Ejecuta este instalador en la VPS con sudo python3")
        path = Path("/run/lock") / (self.unit + ".lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise ErrorDespliegue("Hay otra operación de seguridad en curso")
        # Leer la versión más reciente del estado una vez que tenemos exclusividad.
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        return fd

    def save(self):
        write_private(self.state_path, json.dumps(self.state, indent=2))

    def run(self, args, *, paso="comando", data=None, stdin_file=None, timeout=120, env=None, check=True):
        r = subprocess.run(args, cwd=self.root, input=data, stdin=stdin_file, capture_output=True, timeout=timeout, env=env)
        if check and r.returncode:
            detalle = (r.stderr or r.stdout).decode(errors="replace")[-2500:]
            for secreto in self.secretos:
                if secreto:
                    detalle = detalle.replace(secreto, "[oculto]").replace(quote(secreto, safe=""), "[oculto]")
            # URI con contraseña, incluso si no pertenecía a nuestra configuración.
            detalle = re.sub(r"(mongodb(?:\+srv)?|postgres(?:ql)?):\/\/[^\s\"']+", "[URI oculta]", detalle)
            raise ErrorDespliegue(paso + " falló (" + str(r.returncode) + "): " + detalle)
        return r

    def compose(self, args, **kwargs):
        return self.run(self.base + args, **kwargs)

    def config(self):
        return json.loads(self.compose(["config", "--format", "json"], paso="validar Compose").stdout)

    def env_values(self):
        raw = self.compose(["config", "--environment"], paso="resolver entorno").stdout.decode()
        return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)

    def check(self):
        if os.name != "posix" or os.geteuid() != 0:
            raise ErrorDespliegue("Ejecuta este instalador en la VPS con sudo python3")
        if any(ord(c) < 32 for c in str(self.root)):
            raise ErrorDespliegue("Ruta de proyecto no admitida")
        for ejecutable in ["docker", "openssl", "systemctl"]:
            if not shutil.which(ejecutable):
                raise ErrorDespliegue("Falta " + ejecutable)
        if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
            raise ErrorDespliegue("Retira DOCKER_HOST/DOCKER_CONTEXT; se requiere Docker local")
        if not (self.root / ".env").is_file() or (self.root / ".env").is_symlink():
            raise ErrorDespliegue("Falta .env regular en el proyecto")
        if (self.root / ".env").stat().st_mode & 0o077:
            raise ErrorDespliegue(".env debe tener permisos 600")
        endpoint = self.run(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]).stdout.decode().strip()
        if not endpoint.startswith("unix://"):
            raise ErrorDespliegue("El contexto Docker no es local")
        version = self.run(["docker", "compose", "version", "--short"]).stdout.decode()
        numeros = tuple(int(x) for x in re.findall(r"\d+", version)[:3])
        if numeros < (2, 24, 4):
            raise ErrorDespliegue("Se requiere Docker Compose >= 2.24.4")
        cfg = self.config()
        for servicio in ["postgres", "mongo", "ia", "backend", "web", "backup", "ollama"]:
            if servicio not in cfg["services"]:
                raise ErrorDespliegue("Falta el servicio " + servicio)
        e = cfg["services"]["backend"]["environment"]
        b = cfg["services"]["backup"]["environment"]
        for k in ["DATABASE_URL", "MONGO_URL", "DATA_ENCRYPTION_KEY", "JWT_SECRET", "IA_API_KEY", "RESEND_API_KEY"]:
            if not e.get(k):
                raise ErrorDespliegue("Falta la configuración " + k)
            self.secretos.append(str(e[k]))
        for k in ["DATABASE_URL", "MONGO_URL"]:
            self.secretos.append(unquote(urlsplit(e[k]).password or ""))
        self.secretos.append(b.get("BACKUP_ENCRYPTION_KEY", ""))
        if len(b.get("BACKUP_ENCRYPTION_KEY", "")) < 32:
            raise ErrorDespliegue("La clave de respaldo existente requiere al menos 32 caracteres")
        if e.get("MAIL_PROVIDER") != "resend":
            raise ErrorDespliegue("Configura Resend real antes de endurecer el backend")
        if len(base64.b64decode(e["DATA_ENCRYPTION_KEY"], validate=True)) != 32:
            raise ErrorDespliegue("La llave de datos existente no es válida")
        # Nunca genera ni reemplaza DATA_ENCRYPTION_KEY, JWT_SECRET o BACKUP_ENCRYPTION_KEY.
        for servicio in ["postgres", "mongo", "backend", "ia", "ollama", "backup"]:
            if cfg["services"][servicio].get("ports"):
                raise ErrorDespliegue("Publicación inesperada del servicio interno " + servicio)
        ids = {}
        for servicio in ["postgres", "mongo", "ia", "backend", "web", "backup", "ollama"]:
            cid = self.compose(["ps", "-q", servicio]).stdout.decode().strip()
            if not cid or "\n" in cid:
                raise ErrorDespliegue("Se requiere una instancia activa de " + servicio)
            info = json.loads(self.run(["docker", "inspect", cid]).stdout)[0]
            if not info["State"]["Running"] or info["State"].get("Health", {}).get("Status", "healthy") != "healthy":
                raise ErrorDespliegue("El servicio aún no está listo: " + servicio)
            ids[servicio] = {"id": info["Image"], "tag": info["Config"]["Image"]}
        espacio = shutil.disk_usage(self.root).free
        if espacio < 5 * 1024 ** 3:
            raise ErrorDespliegue("Se requieren al menos 5 GiB libres")
        print("Prevalidación correcta: Docker local, bases privadas y llaves existentes conservadas.", flush=True)
        return cfg, ids

    def certificados(self, gids, uids, *, renovar=False):
        self.private.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.private, 0o700)
        ca_dir = self.private / "autoridad"
        ca_dir.mkdir(exist_ok=True, mode=0o700)
        ca_key, ca_cert = ca_dir / "ca.key", self.private / "ca.crt"
        if not ca_key.exists():
            if ca_cert.exists():
                raise ErrorDespliegue("La CA existente no tiene llave; no se reemplaza automáticamente")
            self.run(["openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-sha256",
                      "-days", "3650", "-subj", "/CN=TT autoridad interna",
                      "-addext", "basicConstraints=critical,CA:TRUE",
                      "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                      "-keyout", str(ca_key), "-out", str(ca_cert)], paso="crear CA")
            os.chmod(ca_key, 0o600)
        os.chmod(ca_cert, 0o644)
        self.run(["openssl", "x509", "-checkend", "2592000", "-noout", "-in", str(ca_cert)], paso="vigencia de CA")
        cambios = False
        for servicio in ["postgres", "mongo", "ia", "ollama", "backend"]:
            carpeta = self.private / servicio
            carpeta.mkdir(exist_ok=True, mode=0o755)
            os.chmod(carpeta, 0o755)
            cert = carpeta / "cert.pem"
            if cert.exists() and not renovar:
                continue
            if cert.exists() and self.run(["openssl", "x509", "-checkend", "2592000", "-noout", "-in", str(cert)], check=False).returncode == 0:
                continue
            with tempfile.TemporaryDirectory(dir=self.private) as tmp:
                tmp = Path(tmp)
                self.run(["openssl", "req", "-new", "-newkey", "rsa:3072", "-nodes",
                          "-subj", "/CN=" + servicio, "-keyout", str(tmp / "key.pem"),
                          "-out", str(tmp / "request.csr")], paso="crear llave " + servicio)
                (tmp / "ext.cnf").write_text(
                    "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                    "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:" + servicio + ",DNS:localhost,IP:127.0.0.1\n")
                self.run(["openssl", "x509", "-req", "-sha256", "-days", "365", "-in", str(tmp / "request.csr"),
                          "-CA", str(ca_cert), "-CAkey", str(ca_key), "-set_serial", str(secrets.randbits(128)),
                          "-extfile", str(tmp / "ext.cnf"), "-out", str(tmp / "cert.pem")], paso="firmar " + servicio)
                write_private(carpeta / "key.pem", (tmp / "key.pem").read_bytes(), 0o600, gids[servicio], uids[servicio])
                write_private(cert, (tmp / "cert.pem").read_bytes(), 0o644)
                if servicio == "mongo":
                    write_private(carpeta / "server.pem", (tmp / "cert.pem").read_bytes() + (tmp / "key.pem").read_bytes(), 0o600, gids[servicio], uids[servicio])
            cambios = True
            self.state["certs_pending_reload"] = True
            self.save()
        return cambios

    def prepare(self, cfg, ids):
        if not self.state:
            entorno = self.env_values()
            files = entorno.get("COMPOSE_FILE", "docker-compose.yml").split(entorno.get("COMPOSE_PATH_SEPARATOR", ":"))
            if any("security" in f for f in files):
                raise ErrorDespliegue("Hay un overlay de seguridad sin su estado privado")
            e, b = cfg["services"]["backend"]["environment"], cfg["services"]["backup"]["environment"]
            pg_db = unquote(urlsplit(e["DATABASE_URL"]).path.lstrip("/"))
            mongo_db = e.get("MONGO_DB", "tt_ocr")
            for nombre in [pg_db, mongo_db]:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", nombre):
                    raise ErrorDespliegue("Nombre de base no admitido por el instalador")
            sufijo = secrets.token_hex(4)
            self.state = {"version": 9, "old_files": files, "old_images": ids,
                          "pg_database": pg_db, "mongo_database": mongo_db,
                          "runtime": {"pg_user": "tt_runtime_" + sufijo, "pg_password": secrets.token_hex(32),
                                      "mongo_user": "tt_runtime_" + sufijo, "mongo_password": secrets.token_hex(32)},
                          "backup": {"pg_user": "tt_backup_" + sufijo, "pg_password": secrets.token_hex(32),
                                     "mongo_user": "tt_backup_" + sufijo, "mongo_password": secrets.token_hex(32)},
                          "admin_pg": e["DATABASE_URL"], "admin_mongo": e["MONGO_URL"],
                          "data_key": e["DATA_ENCRYPTION_KEY"], "backup_key": b["BACKUP_ENCRYPTION_KEY"],
                          "env_uid": (self.root / ".env").stat().st_uid,
                          "env_gid": (self.root / ".env").stat().st_gid}
            self.private.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_private(self.private / "env.anterior", (self.root / ".env").read_bytes())
            self.save()
        gids, uids = {}, {}
        for servicio, usuario in [("postgres", "postgres"), ("mongo", "mongodb"), ("ia", "ia"), ("backend", "node")]:
            gids[servicio] = int(self.compose(["exec", "-T", servicio, "id", "-g", usuario]).stdout)
            uids[servicio] = int(self.compose(["exec", "-T", servicio, "id", "-u", usuario]).stdout)
        gids["ollama"] = uids["ollama"] = 0
        self.state["uids"] = uids
        self.state["gids"] = gids
        backups = self.root / "backups"
        if backups.is_symlink():
            raise ErrorDespliegue("El directorio de respaldos no puede ser un enlace")
        backups.mkdir(exist_ok=True, mode=0o700)
        os.chmod(backups, 0o700)
        os.chown(backups, self.state["env_uid"], self.state["env_gid"])
        self.certificados(gids, uids)
        plantilla = self.compose(["exec", "-T", "web", "cat", "/etc/nginx/templates/default.conf.template"]).stdout.decode()
        if "proxy_pass http://backend_node;" not in plantilla and "proxy_pass https://backend_node;" not in plantilla:
            raise ErrorDespliegue("No se identificó el proxy existente al backend")
        plantilla = plantilla.replace("proxy_pass http://backend_node;", "proxy_pass https://backend_node;")
        if "proxy_ssl_verify on;" not in plantilla:
            plantilla = ("proxy_ssl_server_name on;\nproxy_ssl_name backend;\n"
                         "proxy_ssl_trusted_certificate /run/tt/ca.crt;\nproxy_ssl_verify on;\n"
                         "proxy_ssl_protocols TLSv1.2 TLSv1.3;\n" + plantilla)
        if "log_format tt_minimo " not in plantilla:
            plantilla = re.sub(r"(?m)^(\s*server\s*\{)", r"\1\n    access_log /dev/stdout tt_minimo;", plantilla)
            plantilla = "log_format tt_minimo '$request_method $uri $status $request_time';\n" + plantilla
        carpeta_web = self.private / "web-templates"
        carpeta_web.mkdir(exist_ok=True, mode=0o755)
        os.chmod(carpeta_web, 0o755)
        write_private(carpeta_web / "default.conf.template", plantilla, 0o644)
        bundle = self.compose(["exec", "-T", "ia", "cat", "/etc/ssl/certs/ca-certificates.crt"]).stdout
        write_private(self.private / "ca-bundle.crt", bundle + b"\n" + (self.private / "ca.crt").read_bytes(), 0o644)
        clave = self.private / "ia-storage.key"
        if not clave.exists():
            ia_env = cfg["services"]["ia"].get("environment", {})
            if ia_env.get("IA_STORAGE_KEY_FILE"):
                raise ErrorDespliegue("Ya hay una llave IA montada. Integra esa llave antes de una primera instalación v09.")
            anterior_ia = ia_env.get("IA_STORAGE_KEY", "")
            if anterior_ia and len(base64.b64decode(anterior_ia, validate=True)) != 32:
                raise ErrorDespliegue("La llave IA anterior no es válida")
            valor = anterior_ia.encode() if anterior_ia else base64.b64encode(secrets.token_bytes(32))
            self.state["previous_ia_encryption"] = bool(anterior_ia)
            self.save()
            write_private(clave, valor + b"\n", 0o600, gids["ia"], uids["ia"])
        if len(base64.b64decode(clave.read_text().strip(), validate=True)) != 32:
            raise ErrorDespliegue("La llave IA existente no es válida")
        write_private(self.private / "pg_hba.conf",
                      "local all all scram-sha-256\nhostnossl all all 0.0.0.0/0 reject\nhostnossl all all ::/0 reject\n"
                      "hostssl all all 0.0.0.0/0 scram-sha-256\nhostssl all all ::/0 scram-sha-256\n", 0o644)
        valores = {
            "COMPOSE_FILE": ":".join(self.state["old_files"] + ["docker-compose.security.yml"]),
            "TT_SECURITY_DIR": str(self.private),
            "TT_WEB_IMAGE": self.state["old_images"]["web"]["tag"],
            "TT_BACKEND_IMAGE": self.state["old_images"]["backend"]["tag"],
            "TT_IA_IMAGE": self.state["old_images"]["ia"]["tag"],
            "TT_IA_UID": str(uids["ia"]),
            "TT_IA_GID": str(gids["ia"]),
            "TT_ADMIN_DATABASE_URL": secure_uri(self.state["admin_pg"]),
            "TT_ADMIN_MONGO_URL": secure_uri(self.state["admin_mongo"], mongo=True, database=self.state["mongo_database"]),
            "TT_IA_RETENTION_HOURS": "24",
            "COMPOSE_PROFILES": ",".join(sorted(set(filter(None, self.env_values().get("COMPOSE_PROFILES", "").split(","))) | {"ia", "ollama"})),
        }
        for tipo in ["runtime", "backup"]:
            cuenta = self.state[tipo]
            valores["TT_" + tipo.upper() + "_DATABASE_URL"] = secure_uri(self.state["admin_pg"], user=cuenta["pg_user"], password=cuenta["pg_password"])
            valores["TT_" + tipo.upper() + "_MONGO_URL"] = secure_uri(self.state["admin_mongo"], mongo=True, user=cuenta["mongo_user"], password=cuenta["mongo_password"], database=self.state["mongo_database"])
        anterior = (self.private / "env.anterior").read_text()
        lineas = [l for l in anterior.splitlines() if not re.match(r"^\s*(?:export\s+)?(" + "|".join(valores) + r")\s*=", l)]
        contenido = "\n".join(lineas) + "\n\n# Configuración administrada por TT seguridad v09\n"
        contenido += "\n".join(k + "='" + v.replace("'", "\\'") + "'" for k, v in valores.items()) + "\n"
        write_private(self.root / ".env", contenido)
        os.chown(self.root / ".env", self.state["env_uid"], self.state["env_gid"])
        self.state["managed_env_sha256"] = hashlib.sha256((self.root / ".env").read_bytes()).hexdigest()
        self.state["managed_env"] = valores
        self.save()
        self.compose(["--profile", "seguridad", "config", "--quiet"], paso="validar configuración de seguridad")

    def tool(self, accion, *, stdin=None, timeout=180):
        return self.compose(["--profile", "seguridad", "run", "--rm", "--no-deps", "-T",
                             "migrate", "/app/tt-security-tools.mjs", accion],
                            data=stdin, timeout=timeout, paso="herramienta " + accion)

    def state_input(self):
        return json.dumps({k: self.state[k] for k in ["runtime", "backup", "pg_database", "mongo_database"]}).encode()

    def ia_migration(self, *, rollback=False):
        args = ["--profile", "seguridad", "run", "--rm", "--no-deps", "-T", "ia-storage",
                "scripts/migrar_almacenamiento.py", "--include-logs", "--apply"]
        if rollback:
            args.append("--decrypt-for-rollback")
        return self.compose(args, paso="convertir almacenamiento IA", timeout=900)

    def install_timer(self):
        script = json.dumps(str(self.root / "deploy/seguridad/configurar.py"))
        proyecto = json.dumps(str(self.root))
        servicio = "[Unit]\nDescription=Comprobar cifrado y renovar TLS interno TT\nAfter=docker.service\nRequires=docker.service\n\n[Service]\nType=oneshot\nUser=root\nUMask=0077\nNoNewPrivileges=true\nExecStart=/usr/bin/python3 " + script + " --project " + proyecto + " --maintenance\n"
        timer = "[Unit]\nDescription=Mantenimiento diario de seguridad TT\n\n[Timer]\nOnCalendar=daily\nRandomizedDelaySec=30m\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n"
        write_private(Path("/etc/systemd/system") / (self.unit + ".service"), servicio, 0o644)
        write_private(Path("/etc/systemd/system") / (self.unit + ".timer"), timer, 0o644)
        self.run(["systemctl", "daemon-reload"], paso="recargar mantenimiento")
        self.run(["systemctl", "enable", "--now", self.unit + ".timer"], paso="activar mantenimiento")

    def apply(self):
        if self.state.get("offline"):
            raise ErrorDespliegue("Quedó una activación interrumpida. Ejecuta --rollback antes de reintentar.")
        if self.state and not self.state.get("applied") and (self.private / "env.anterior").exists():
            original = (self.private / "env.anterior").read_bytes()
            if (self.root / ".env").read_bytes() != original:
                raise ErrorDespliegue("La configuración difiere de la anterior guardada. Revísala antes de reintentar la activación.")
        cfg, ids = self.check()
        if self.state.get("applied"):
            self.verify()
            print("Seguridad ya aplicada; no se regeneraron llaves ni cuentas.", flush=True)
            return
        try:
            self.prepare(cfg, ids)
            for servicio in ["web", "backend", "ia", "backup"]:
                datos = self.state["old_images"][servicio]
                self.run(["docker", "tag", datos["id"], self.unit + "-anterior-" + servicio + ":v09"],
                         paso="conservar imagen para reversión")
            print("Construyendo imágenes; los servicios existentes siguen activos durante esta fase.", flush=True)
            self.compose(["build", "backend", "ia", "web", "backup"], paso="construir imágenes", timeout=3600)
            self.state["new_images"] = {}
            nuevo = self.config()
            for servicio in ["backend", "ia", "web", "backup"]:
                tag = nuevo["services"][servicio].get("image") or nuevo["name"] + "-" + servicio
                self.state["new_images"][servicio] = self.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag]).stdout.decode().strip()
            self.save()
            print("Iniciando ventana de mantenimiento y conexiones TLS.", flush=True)
            # No cambia las bases mientras haya un OCR en curso.
            self.require_idle()
            self.state["offline"] = True
            self.save()
            self.compose(["stop", "-t", "60", "web"], paso="cerrar ingreso de documentos", timeout=90)
            self.require_idle()
            self.compose(["stop", "-t", "60", "backend", "backup", "ia"], paso="detener aplicación", timeout=180)
            self.compose(["up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "180", "postgres", "mongo"],
                         paso="activar TLS de bases", timeout=240)
            self.tool("provision", stdin=self.state_input())
            print("Creando respaldo autenticado anterior a la conversión.", flush=True)
            self.snapshot("antes-v09")
            self.restore_test()
            self.compose(["--profile", "seguridad", "run", "--rm", "--no-deps", "-T", "migrate", "dist/db/cli.js"],
                         paso="migrar esquema", timeout=240)
            self.tool("migrate-check")
            self.state["data_migration_started"] = True
            self.save()
            self.tool("migrate-apply", timeout=900)
            self.state["ia_migration_started"] = True
            self.save()
            self.ia_migration()
            self.compose(["up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "240", "ollama"],
                         paso="limitar Ollama a loopback", timeout=300)
            self.compose(["up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "240", "ollama-tls", "ia"],
                         paso="activar HTTPS de IA y Ollama", timeout=360)
            self.compose(["up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "180", "backend", "web", "backup"],
                         paso="arrancar aplicación con permisos limitados", timeout=240)
            self.verify()
            self.export_recovery()
            self.state["applied"] = True
            self.state["offline"] = False
            self.state["certs_pending_reload"] = False
            self.save()
            self.install_timer()
            print("Seguridad aplicada y restauración aislada verificada. Los puertos web no cambiaron.", flush=True)
        except Exception:
            if self.state:
                print("La aplicación del cambio se detuvo; recuperando el estado anterior.", flush=True)
                try:
                    self.rollback()
                except Exception as e:
                    print("Recuperación incompleta: conserva deploy/seguridad/private y ejecuta --rollback. Tipo: " + type(e).__name__, file=sys.stderr)
            raise

    def verify(self):
        self.check()
        salida = self.compose(["exec", "-T", "backend", "node", "/app/tt-security-tools.mjs", "audit"],
                              paso="auditar cuentas, cifrado y referencias", timeout=180)
        reporte = json.loads(salida.stdout.decode().splitlines()[-1])
        print(json.dumps(reporte, ensure_ascii=False), flush=True)
        estado_ia = self.compose(["--profile", "seguridad", "run", "--rm", "--no-deps", "-T", "ia-storage", "scripts/migrar_almacenamiento.py", "--include-logs"],
                     paso="verificar copias IA cifradas", timeout=180)
        if json.loads(estado_ia.stdout.decode().splitlines()[-1])["pendientes"]:
            raise ErrorDespliegue("Quedan copias IA sin cifrar")
        ia = self.compose(["exec", "-T", "ia", "python", "-c",
                           "import ssl,urllib.request; c=ssl.create_default_context(); r=urllib.request.urlopen('https://ollama:11443/api/tags',context=c,timeout=15); assert r.status==200; print('TLS Ollama verificado')"],
                          paso="verificar TLS y certificado de Ollama")
        print(ia.stdout.decode().strip(), flush=True)
        self.compose(["exec", "-T", "backend", "node", "-e",
                      "fetch(process.env.IA_URL+'/health',{signal:AbortSignal.timeout(10000)}).then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"],
                     paso="verificar HTTPS del backend al servicio IA")
        self.compose(["exec", "-T", "web", "nginx", "-t"], paso="validar Nginx")
        salud_web = self.compose(["exec", "-T", "web", "wget", "--no-check-certificate", "-qO-", "https://localhost/api/salud"], paso="comprobar proxy HTTPS al backend")
        salud = json.loads(salud_web.stdout)
        if salud.get("postgres") is not True or salud.get("mongo") is not True:
            raise ErrorDespliegue("La ruta HTTPS del proxy no confirmó ambas bases")

    def rollback(self):
        if not self.state or not (self.private / "env.anterior").exists():
            raise ErrorDespliegue("No existe un estado anterior recuperable")
        if self.state.get("managed_env_sha256") and hashlib.sha256((self.root / ".env").read_bytes()).hexdigest() != self.state["managed_env_sha256"]:
            raise ErrorDespliegue(".env cambió después del parche; se conserva y requiere revisar la recuperación")
        if self.state.get("offline") or self.state.get("applied"):
            self.compose(["stop", "-t", "60", "web", "backend", "backup", "ia"], timeout=180, check=False)
            if self.state.get("data_migration_started"):
                self.tool("migrate-rollback", timeout=900)
            if self.state.get("ia_migration_started") and not self.state.get("previous_ia_encryption"):
                self.ia_migration(rollback=True)
            self.compose(["rm", "-s", "-f", "ollama-tls"], check=False)
        write_private(self.root / ".env", (self.private / "env.anterior").read_bytes())
        os.chown(self.root / ".env", self.state["env_uid"], self.state["env_gid"])
        for datos in self.state["old_images"].values():
            self.run(["docker", "tag", datos["id"], datos["tag"]], paso="recuperar imagen anterior")
        if self.state.get("offline") or self.state.get("applied"):
            self.compose(["--profile", "ia", "--profile", "ollama", "up", "-d", "--force-recreate", "--wait",
                          "--wait-timeout", "240", "postgres", "mongo", "ollama", "ia", "backend", "web", "backup"],
                         paso="recuperar servicios", timeout=360)
        self.run(["systemctl", "disable", "--now", self.unit + ".timer"], check=False)
        self.state.update(applied=False, offline=False, ia_migration_started=False, data_migration_started=False)
        self.state.pop("managed_env_sha256", None)
        self.save()
        print("Estado anterior recuperado. Sus riesgos anteriores vuelven a existir; no publiques el sistema así.", flush=True)

    def maintenance(self):
        if not self.state.get("applied"):
            raise ErrorDespliegue("Primero completa --apply")
        self.check()
        renovados = self.certificados(self.state["gids"], self.state["uids"], renovar=True)
        if renovados or self.state.get("certs_pending_reload"):
            self.tool("rotate")
            self.compose(["kill", "-s", "HUP", "backend", "ia"], paso="recargar TLS IA")
            self.compose(["exec", "-T", "ollama-tls", "nginx", "-s", "reload"], paso="recargar TLS Ollama")
            self.state["certs_pending_reload"] = False
            self.save()
        horas = self.env_values().get("TT_IA_RETENTION_HOURS", "24")
        if not horas.isdigit() or int(horas) < 1:
            raise ErrorDespliegue("Retención IA inválida")
        self.compose(["exec", "-T", "ia", "python", "scripts/limpiar_copias.py", "--hours", horas, "--apply"],
                     paso="retención de copias IA", timeout=240)
        self.verify()

    def require_idle(self):
        r = self.compose(["exec", "-T", "postgres", "sh", "-ec",
                          'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
                         data=b"SELECT count(*) FROM documentos WHERE estado IN ('cargado','procesando');\n")
        if r.stdout.strip() != b"0":
            raise ErrorDespliegue("Hay documentos en cola o en proceso. Espera antes de abrir el mantenimiento.")

    def snapshot(self, carpeta):
        if carpeta not in {"antes-v09", "prueba-v09"}:
            raise ErrorDespliegue("Destino de instantánea no admitido")
        counts = json.loads(self.tool("counts").stdout.decode().splitlines()[-1])
        salida = self.compose(["run", "--rm", "--no-deps", "-T", "-e", "BACKUP_DIR=/backups/" + carpeta,
                               "backup", "python3", "/usr/local/bin/respaldo.py", "--once"], paso="respaldo coherente", timeout=900)
        self.state.update(counts=counts, snapshot_dir=carpeta,
                          snapshot=json.loads(salida.stdout.decode().splitlines()[-1])["respaldos"])
        self.save()

    def restore_test(self, fresh=False):
        from restaurar_prueba import ejecutar
        if fresh:
            if not self.state.get("applied"):
                raise ErrorDespliegue("Primero completa --apply")
            self.require_idle()
            try:
                self.compose(["stop", "-t", "60", "web"], timeout=120)
                self.require_idle()
                self.compose(["stop", "-t", "60", "backend", "ia", "backup"], timeout=180)
                self.snapshot("prueba-v09")
            finally:
                self.compose(["up", "-d", "--no-build", "--no-deps", "--wait", "--wait-timeout", "240",
                              "ia", "backend", "web", "backup"], timeout=360)
        ejecutar(self)

    def export_recovery(self):
        salida = self.compose(["run", "--rm", "--no-deps", "-T", "-v", str(self.private) + ":/recovery:ro",
                               "backup", "python3", "/usr/local/bin/respaldo.py", "--export-recovery"],
                              paso="exportar recuperación cifrada", timeout=180)
        nombre = json.loads(salida.stdout.decode().splitlines()[-1])["recuperacion"]
        ruta = self.root / "backups" / nombre
        if ruta.parent != self.root / "backups" or ruta.is_symlink():
            raise ErrorDespliegue("Ruta de recuperación no admitida")
        os.chmod(ruta, 0o600)
        os.chown(ruta, self.state["env_uid"], self.state["env_gid"])
        print("Recuperación cifrada: backups/" + nombre + ". Conserva BACKUP_ENCRYPTION_KEY fuera de la VPS.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    grupo = parser.add_mutually_exclusive_group(required=True)
    for opcion in ["check", "apply", "verify", "rollback", "maintenance", "restore-test", "export-recovery"]:
        grupo.add_argument("--" + opcion, action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    lock_fd = None
    try:
        app = Installer(args.project)
        lock_fd = app.lock()
        if args.check: app.check()
        elif args.apply: app.apply()
        elif args.verify: app.verify()
        elif args.rollback:
            if os.geteuid() != 0: raise ErrorDespliegue("Se requiere sudo")
            if not app.state.get("offline") and not app.state.get("applied") and not app.state.get("managed_env_sha256"):
                print("No hay una activación pendiente de revertir.")
                return 0
            app.rollback()
        elif args.maintenance: app.maintenance()
        elif args.restore_test:
            app.check()
            app.restore_test(fresh=True)
        elif args.export_recovery:
            app.check()
            app.export_recovery()
    except Exception as e:
        if isinstance(e, ErrorDespliegue):
            print(str(e), file=sys.stderr)
        else:
            print("Operación detenida: " + type(e).__name__, file=sys.stderr)
        return 1
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
