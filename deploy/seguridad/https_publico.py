#!/usr/bin/env python3
"""HTTPS para el despliegue TT existente en Contabo. No crea una instalacion vacia.

Uso (en la VPS):
  sudo python3 tt-configurar-https.py --check
  sudo python3 tt-configurar-https.py --apply

Dominio predeterminado: redes-noc-alerts.xyz; proyecto: /home/eden/tt.
--apply instala Certbot desde Ubuntu, acepta los terminos de Let's Encrypt,
solicita el correo de contacto y publica Nginx en IPv4 TCP 80/443.
Necesita el registro A ya creado, Docker Compose >= 2.24.4 y el sistema
existente funcionando. Hay una interrupcion breve al recrear backend/web.
Las bases de datos, sus volumenes, los modelos y claves existentes se conservan.
Verifica Nginx y el certificado antes de publicar 443. Ante un error durante
la migracion intenta recuperar .env, backend y web automaticamente.
El registro A es publico; la emision registra el nombre en Certificate Transparency.
--rollback /root/tt-prehttps-... recupera la configuracion previa y retira
las reglas 80/443 solamente si este instalador las agrego.
"""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid


class SetupError(Exception):
    pass


def run(args, cwd=None, *, quiet=False, timeout=120, input_text=None):
    env = os.environ.copy()
    for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
        env.pop(key, None)
    p = subprocess.run(
        [str(a) for a in args], cwd=cwd, env=env, input=input_text,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if p.returncode:
        # Never include stdout: `compose config` stdout contains secrets.
        detail = p.stderr.strip()[-1800:]
        raise SetupError(f"Fallo {args[0]} {args[1] if len(args) > 1 else ''}: {detail}")
    if not quiet and p.stdout.strip():
        print(p.stdout.strip(), flush=True)
    return p.stdout


def require(ok, message):
    if not ok:
        raise SetupError(message)


def atomic_write(path, text, mode=0o600, owner=None):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=".tt-https-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, mode)
        if owner:
            os.chown(name, *owner)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def env_value(text, key):
    values = re.findall(r"(?m)^\s*(?:export\s+)?" + re.escape(key) + r"\s*=([^\r\n]*)", text)
    require(len(values) <= 1, f"{key} aparece varias veces en .env; corrige el duplicado primero.")
    if not values:
        return None
    parts = shlex.split(values[0], comments=True)
    require(len(parts) <= 1, f"Formato de {key} no compatible. Revisa esa variable.")
    return parts[0] if parts else ""


def update_env(text, updates):
    # Preserve every unrelated line, including encryption keys and passwords.
    newline = "\r\n" if "\r\n" in text else "\n"
    for key, value in updates.items():
        env_value(text, key)  # Reject ambiguous duplicate settings.
        pattern = r"(?m)^[ \t]*(?:export[ \t]+)?" + re.escape(key) + r"[ \t]*=[^\r\n]*"
        if re.search(pattern, text):
            text = re.sub(pattern, lambda _: f"{key}={value}", text)
        else:
            if text and not text.endswith(("\n", "\r")):
                text += newline
            text += f"{key}={value}" + newline
    return text


def public_template(source, domain):
    require(len(re.findall(r"(?m)^server\s*\{", source)) == 2,
            "La plantilla Nginx tiene una estructura distinta. Necesita revision manual.")
    require("/.well-known/acme-challenge/" not in source, "La plantilla ya tiene ACME; revisala antes de continuar.")
    require(re.search(r"server\s+backend:3000\s*;", source)
            and any(p in source for p in ("proxy_pass http://backend_node;", "proxy_pass https://backend_node;")),
            "No se identifico el reverse proxy existente hacia backend:3000.")
    source, n = re.subn(r"(?m)^(\s*)server_name\s+[^;]+;", lambda m: m[1] + f"server_name {domain};", source)
    require(n == 2, "No se identificaron los dos server_name de Nginx.")
    acme = (
        "listen 80;\n"
        "    location ^~ /.well-known/acme-challenge/ {\n"
        "        root /var/www/acme;\n"
        "        default_type text/plain;\n"
        "        try_files $uri =404;\n"
        "    }"
    )
    source, n = re.subn(r"listen\s+80;", lambda _: acme, source)
    require(n == 1, "No se identifico un unico listener HTTP 80.")
    source, n = re.subn(
        r"return\s+301\s+https://\$host(?::\$\{HTTPS_PORT\})?\$request_uri;",
        lambda _: f"return 301 https://{domain}$request_uri;", source,
    )
    require(n == 1, "La redireccion HTTP no coincide con la plantilla esperada.")
    for directive, filename in (("ssl_certificate", "fullchain.pem"), ("ssl_certificate_key", "privkey.pem")):
        source, n = re.subn(
            r"(?m)^(\s*)" + directive + r"\s+[^;]+;",
            lambda m: m[1] + f"{directive} /etc/letsencrypt/live/{domain}/{filename};", source,
        )
        require(n == 1, f"No se identifico {directive}.")
    source, n = re.subn(r"(?m)^([ \t]*)ssl_protocols\s+[^;]+;", r"\1ssl_protocols TLSv1.2 TLSv1.3;", source)
    require(n == 1, "No se identifico la configuracion TLS.")
    return source.replace("${HTTPS_PORT}", "443")


def validate_bindings(config, *, final=False):
    require(config.get("name") == "tt-ocr", "El proyecto Compose no se llama tt-ocr; no se modificara.")
    for name, service in config["services"].items():
        for port in service.get("ports", []):
            local = port.get("host_ip") in ("127.0.0.1", "::1")
            if name != "web":
                require(local, f"{name} ya tiene un puerto publico. Revisa su aislamiento primero.")
            elif not final:
                require(local, "Web ya tiene puertos publicos; revisa su configuracion antes de usar este instalador.")
    if final:
        ports = config["services"]["web"].get("ports", [])
        actual = {(p.get("host_ip"), str(p.get("published")), int(p["target"]), p.get("protocol", "tcp")) for p in ports}
        expected = {("0.0.0.0", "80", 80, "tcp"), ("0.0.0.0", "443", 443, "tcp")}
        require(len(ports) == 2 and actual == expected, "La fusion de Compose publico puertos inesperados.")
        backend_env = config["services"]["backend"]["environment"]
        require(str(backend_env.get("COOKIE_SECURE")).lower() == "true", "COOKIE_SECURE debe permanecer true.")


def compose(project, *args, files=None):
    command = ["docker", "compose", "--project-directory", project, "--env-file", project / ".env"]
    if files:
        for file in files:
            command += ["-f", project / file]
    return run(command + list(args), cwd=project, quiet=True, timeout=300)


def preflight(project, domain):
    require(re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain), "Dominio invalido.")
    require(project == Path("/home/eden/tt"), "Este instalador se preparo solo para /home/eden/tt.")
    require(os.geteuid() == 0, "Ejecuta este archivo con sudo python3.")
    for executable in ("docker", "ufw", "openssl", "ss", "systemctl", "apt-get"):
        require(shutil.which(executable), f"Falta {executable}; no se modifico el despliegue.")
    env_path = project / ".env"
    require(env_path.is_file() and not env_path.is_symlink(), "No se encontro un .env regular.")
    with env_path.open(newline="") as inp:
        env_text = inp.read()
    selected = env_value(env_text, "COMPOSE_FILE") or "docker-compose.yml"
    files = selected.split(":")
    require(files in (["docker-compose.yml"], ["docker-compose.yml", "docker-compose.contabo.yml"], ["docker-compose.yml", "docker-compose.security.yml"], ["docker-compose.yml", "docker-compose.contabo.yml", "docker-compose.security.yml"]),
            "COMPOSE_FILE no coincide con el despliegue conocido; revisalo antes de continuar.")
    for file in files:
        require((project / file).is_file(), f"Falta {file}.")
    version = run(["docker", "compose", "version", "--short"], quiet=True)
    nums = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    require(nums and tuple(map(int, nums.groups())) >= (2, 24, 4), "Compose necesita version >= 2.24.4 para !override.")
    config = json.loads(compose(project, "config", "--format", "json"))
    validate_bindings(config)
    ssh = run(["/usr/sbin/sshd", "-T"], quiet=True)
    for line in ("permitrootlogin no", "passwordauthentication no", "pubkeyauthentication yes", "kbdinteractiveauthentication no"):
        require(line in ssh.splitlines(), f"SSH no tiene '{line}'. Revisa el hardening existente.")
    firewall = run(["ufw", "status"], quiet=True)
    require("Status: active" in firewall or "Estado: activo" in firewall, "UFW no esta activo; revisalo antes de continuar.")
    web_id = compose(project, "ps", "-q", "web").strip()
    require(re.fullmatch(r"[0-9a-f]{12,64}", web_id), "No hay un unico contenedor web funcionando.")
    inspect = json.loads(run(["docker", "inspect", web_id], quiet=True))[0]
    require(inspect["State"]["Running"], "Web no esta corriendo.")
    require(inspect["Config"]["Labels"].get("com.docker.compose.project") == "tt-ocr", "Contenedor de otro proyecto.")
    image = inspect["Image"]
    source = run(["docker", "exec", web_id, "cat", "/etc/nginx/templates/default.conf.template"], quiet=True)
    template = public_template(source, domain)
    # Original entrypoint expects these bootstrap files even with LE mounted.
    run(["docker", "exec", web_id, "sh", "-c", "test -s /etc/nginx/certs/cert.pem && test -s /etc/nginx/certs/key.pem && nginx -t"], quiet=True)
    for port in (80, 443):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("0.0.0.0", port))
            except OSError as exc:
                raise SetupError(f"Puerto {port} ocupado; no se detendran servicios ajenos.") from exc
    try:
        ips = {i[4][0] for i in socket.getaddrinfo(domain, 80, socket.AF_INET, socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise SetupError(f"Aun no resuelve {domain}. Debe tener un registro A -> 147.93.133.251; para el dominio raiz, Host vacio en Porkbun.") from exc
    require(ips == {"147.93.133.251"}, f"DNS A inesperado: {sorted(ips)}. Debe ser solo 147.93.133.251.")
    paths = (project / "docker-compose.https-publico.yml", project / "deploy/https-publico", Path("/etc/letsencrypt/renewal-hooks/deploy/50-tt-nginx"))
    for path in paths:
        require(not path.exists(), f"Ya existe {path}; no se sobrescribira. Si hubo un intento anterior, revisalo.")
    require(not (Path("/etc/letsencrypt/live") / domain).exists(), "Ya existe un certificado para ese nombre; necesita revision.")
    print(f"Revision correcta: {domain} -> 147.93.133.251; SSH con llave; UFW activo; web privada; puertos 80/443 libres.", flush=True)
    return env_text, files, image, web_id, template, firewall


def dns_check(domain):
    # Check an external recursive resolver as well as the server's resolver.
    for resolver in ("1.1.1.1", "8.8.8.8"):
        a = run(["dig", "@" + resolver, domain, "A", "+short", "+time=5", "+tries=1"], quiet=True)
        require(set(a.split()) == {"147.93.133.251"}, f"El DNS publico {resolver} aun no devuelve solo la IP correcta.")
        aaaa = run(["dig", "@" + resolver, domain, "AAAA", "+short", "+time=5", "+tries=1"], quiet=True)
        require(not aaaa.strip(), f"Existe un AAAA para {domain}. Este procedimiento publica solo IPv4; revisa ese AAAA.")


def reload_hook(project, files, domain):
    command = [shutil.which("docker") or "/usr/bin/docker", "compose", "--project-directory", str(project), "--env-file", str(project / ".env")]
    for file in files:
        command += ["-f", str(project / file)]
    return (
        "#!/bin/sh\nset -eu\n"
        "unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES\n"
        f"case \" ${{RENEWED_DOMAINS:-}} \" in *' {domain} '*) ;; *) exit 0 ;; esac\n"
        + shlex.join(command + ["exec", "-T", "web", "nginx", "-t"]) + "\n"
        + shlex.join(command + ["exec", "-T", "web", "nginx", "-s", "reload"]) + "\n"
    )


def rollback(backup):
    backup = backup.resolve()
    require(backup.parent == Path("/root") and backup.name.startswith("tt-prehttps-"), "Respaldo fuera de /root/tt-prehttps-*.")
    require(backup.stat().st_uid == 0 and backup.stat().st_mode & 0o077 == 0, "Permisos inesperados en el respaldo.")
    state = json.loads((backup / "estado.json").read_text())
    project = Path(state["project"])
    require(project == Path("/home/eden/tt"), "Proyecto inesperado en el respaldo.")
    original = (backup / ".env").read_bytes().decode("utf-8")
    atomic_write(project / ".env", original, state["env_mode"], tuple(state["env_owner"]))
    hook = Path("/etc/letsencrypt/renewal-hooks/deploy/50-tt-nginx")
    if hook.is_file() and hook.read_text().startswith("#!/bin/sh\nset -eu\n"):
        hook.rename(backup / "hook-retirado")
    domain = state["domain"]
    require(re.fullmatch(r"[a-z0-9.-]+", domain), "Dominio inesperado en el respaldo.")
    renewal = Path("/etc/letsencrypt/renewal") / (domain + ".conf")
    if renewal.is_file():
        renewal.rename(backup / "renovacion-retirada.conf")
    print(compose(project, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", "--wait", "--wait-timeout", "120", "backend"))
    print(compose(project, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", "--wait", "--wait-timeout", "120", "web"))
    for port in state.get("ufw_added", []):
        run(["ufw", "--force", "delete", "allow", str(port) + "/tcp"], quiet=True)
    # New files/certificates are deliberately kept for inspection, not deleted.
    print("Configuracion anterior recuperada. Los archivos HTTPS nuevos se conservaron para revision.")


def apply(project, domain):
    env_text, files, image, web_id, template, firewall = preflight(project, domain)
    email = input("Correo de contacto para avisos de Let's Encrypt: ").strip()
    require(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email), "Correo invalido.")
    print("Instalando Certbot y herramientas desde Ubuntu...", flush=True)
    run(["apt-get", "update"], quiet=True, timeout=900)
    run(["apt-get", "install", "-y", "certbot", "dnsutils", "curl", "ca-certificates"], quiet=True, timeout=900)
    dns_check(domain)
    stat = (project / ".env").stat()
    backup = Path(tempfile.mkdtemp(prefix="tt-prehttps-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-", dir="/root"))
    state = {"project": str(project), "env_owner": [stat.st_uid, stat.st_gid], "env_mode": stat.st_mode & 0o777,
             "ufw_added": [], "domain": domain}
    atomic_write(backup / ".env", env_text)
    for file in files:
        shutil.copy2(project / file, backup / file)
    atomic_write(backup / "nginx-original.template", run(["docker", "exec", web_id, "cat", "/etc/nginx/templates/default.conf.template"], quiet=True))
    atomic_write(backup / "estado.json", json.dumps(state, indent=2))
    print(f"Respaldo privado creado: {backup}", flush=True)
    root = project / "deploy/https-publico"
    root.mkdir(parents=True, mode=0o755)
    root.chmod(0o755)
    (root / "templates").mkdir(mode=0o755)
    (root / "acme/.well-known/acme-challenge").mkdir(parents=True, mode=0o755)
    for folder in (root / "acme", root / "acme/.well-known", root / "acme/.well-known/acme-challenge"):
        folder.chmod(0o755)
    atomic_write(root / "templates/default.conf.template", template, 0o644)
    boot_conf = (
        "server_tokens off;\nserver {\nlisten 80;\n"
        f"server_name {domain};\n"
        "location ^~ /.well-known/acme-challenge/ { root /var/www/acme; default_type text/plain; try_files $uri =404; }\n"
        "location / { return 404; }\n}\n"
    )
    atomic_write(root / "acme-bootstrap.conf", boot_conf, 0o644)
    responder = "tt-acme-" + uuid.uuid4().hex[:12]
    responder_started = False
    env_changed = False
    committed = False
    hook = Path("/etc/letsencrypt/renewal-hooks/deploy/50-tt-nginx")
    try:
        for port in (80, 443):
            # Store only exact allow rules newly added by this run.
            exact = bool(re.search(r"(?m)^" + str(port) + r"/tcp\s+ALLOW(?: IN)?\s+Anywhere\s*$", firewall))
            if not exact:
                state["ufw_added"].append(port)
                atomic_write(backup / "estado.json", json.dumps(state, indent=2))
                run(["ufw", "allow", f"{port}/tcp", "comment", "TT HTTPS"], quiet=True)
        run(["docker", "run", "-d", "--name", responder, "--label", "tt.https.bootstrap=true",
             "--read-only", "--tmpfs", "/var/cache/nginx", "--tmpfs", "/var/run",
             "-p", "0.0.0.0:80:80", "--mount", f"type=bind,src={root / 'acme'},dst=/var/www/acme,readonly",
             "--mount", f"type=bind,src={root / 'acme-bootstrap.conf'},dst=/etc/nginx/conf.d/default.conf,readonly",
             "--entrypoint", "nginx", image, "-g", "daemon off;"], quiet=True)
        responder_started = True
        token = uuid.uuid4().hex
        probe = root / "acme/.well-known/acme-challenge" / token
        atomic_write(probe, token, 0o644)
        found = run(["curl", "--retry", "8", "--retry-connrefused", "--retry-delay", "1", "--max-time", "10",
                     "--fail", "--silent", "--show-error", "-H", f"Host: {domain}",
                     f"http://127.0.0.1/.well-known/acme-challenge/{token}"], quiet=True)
        require(found == token, "El servidor temporal no entrega el desafio ACME.")
        probe.unlink()
        print("Solicitando certificado de produccion a Let's Encrypt...", flush=True)
        run(["certbot", "certonly", "--non-interactive", "--agree-tos", "--no-eff-email",
             "--email", email, "--webroot", "-w", root / "acme", "-d", domain,
             "--cert-name", domain], timeout=300)
        cert = Path("/etc/letsencrypt/live") / domain / "fullchain.pem"
        run(["openssl", "x509", "-in", cert, "-noout", "-checkhost", domain], quiet=True)
        run(["openssl", "x509", "-in", cert, "-noout", "-checkend", "86400"], quiet=True)
        # Validate the exact new configuration using the existing image and Docker DNS.
        ca_mount = (["--mount", f"type=bind,src={project / 'deploy/seguridad/private/ca.crt'},dst=/run/tt/ca.crt,readonly"]
                    if "docker-compose.security.yml" in files else [])
        run(["docker", "run", "--rm", "--network", "container:" + web_id,
             "--mount", f"type=bind,src={root / 'templates/default.conf.template'},dst=/etc/nginx/conf.d/default.conf,readonly",
             "--mount", "type=bind,src=/etc/letsencrypt,dst=/etc/letsencrypt,readonly",
             *ca_mount, "--entrypoint", "nginx", image, "-t"], quiet=True)
        public_file = "docker-compose.https-publico.yml"
        override = (
            "# Generado por tt-configurar-https.py. Certificado gestionado por Certbot del host.\n"
            "services:\n  web:\n    environment:\n      HTTPS_PORT: '443'\n"
            "    ports: !override\n      - '0.0.0.0:80:80'\n      - '0.0.0.0:443:443'\n"
            "    volumes:\n"
            "      - ./deploy/https-publico/templates:/etc/nginx/templates:ro\n"
            "      - ./deploy/https-publico/acme:/var/www/acme:ro\n"
            "      - /etc/letsencrypt:/etc/letsencrypt:ro\n"
        )
        atomic_write(project / public_file, override, 0o644)
        final_files = files + [public_file]
        new_env = update_env(env_text, {"COMPOSE_FILE": ":".join(final_files), "PUBLIC_URL": "https://" + domain,
                                        "HTTP_PORT": "80", "HTTPS_PORT": "443"})
        # Arm rollback before replacing .env.
        env_changed = True
        atomic_write(project / ".env", new_env, stat.st_mode & 0o777, (stat.st_uid, stat.st_gid))
        merged = json.loads(compose(project, "config", "--format", "json"))
        validate_bindings(merged, final=True)
        require(merged["services"]["backend"]["environment"]["PUBLIC_URL"] == "https://" + domain,
                "PUBLIC_URL no se aplico al backend.")
        print("Certificado y Nginx validados. Recreando backend y web...", flush=True)
        print(compose(project, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", "--wait", "--wait-timeout", "120", "backend"))
        run(["docker", "stop", responder], quiet=True)
        run(["docker", "rm", responder], quiet=True)
        responder_started = False
        print(compose(project, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", "--wait", "--wait-timeout", "120", "web"))
        compose(project, "exec", "-T", "web", "nginx", "-t")
        for path in ("/", "/api/salud"):
            status = run(["curl", "--resolve", f"{domain}:443:127.0.0.1", "--max-time", "40", "--silent", "--show-error",
                          "--output", "/dev/null", "--write-out", "%{http_code}", "https://" + domain + path], quiet=True)
            require(status == "200", f"HTTPS {path} respondio {status}; se recuperara la configuracion previa.")
        headers = run(["curl", "--max-time", "10", "--silent", "--show-error", "--head", "-H", f"Host: {domain}", "http://127.0.0.1/"], quiet=True)
        require(f"location: https://{domain}/" in headers.lower(), "Fallo la redireccion HTTP -> HTTPS.")
        hook.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(hook, reload_hook(project, final_files, domain), 0o700)
        print("Probando renovacion ACME y recarga automatica de Nginx...", flush=True)
        run(["certbot", "renew", "--cert-name", domain, "--dry-run", "--run-deploy-hooks"], timeout=600)
        run(["systemctl", "enable", "--now", "certbot.timer"], quiet=True)
        committed = True
        print(compose(project, "ps"))
        run(["openssl", "x509", "-in", cert, "-noout", "-issuer", "-dates", "-ext", "subjectAltName"])
        run(["systemctl", "list-timers", "certbot.timer", "--no-pager"])
        print(f"\nHTTPS CONFIGURADO: https://{domain}\nRespaldo: {backup}\n"
              f"Reversion: sudo python3 {Path(__file__).resolve()} --rollback {backup}\n"
              "Comprueba ahora desde otra red, inicia sesion, prueba OCR y descarga/vincula de nuevo la extension.")
    except BaseException:
        if env_changed and not committed:
            print("Fallo la migracion; intentando recuperar el despliegue anterior...", flush=True)
            try:
                rollback(backup)
            except Exception as exc:
                print(f"La recuperacion necesita revision: {exc}\nRespaldo privado: {backup}", file=sys.stderr)
        elif not committed:
            for port in state["ufw_added"]:
                subprocess.run(["ufw", "--force", "delete", "allow", str(port) + "/tcp"], capture_output=True)
            print(f"El despliegue original sigue configurado. Archivos del intento conservados en {root}; respaldo: {backup}.", file=sys.stderr)
        raise
    finally:
        if responder_started:
            subprocess.run(["docker", "stop", responder], capture_output=True)
            subprocess.run(["docker", "rm", responder], capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", default="redes-noc-alerts.xyz")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Revision previa sin instalar ni cambiar archivos.")
    group.add_argument("--apply", action="store_true", help="Instala y configura HTTPS y la renovacion.")
    group.add_argument("--rollback", type=Path, help="Recupera el despliegue previo desde un respaldo de este instalador.")
    args = parser.parse_args()
    os.umask(0o077)
    project = Path("/home/eden/tt")
    try:
        if args.rollback:
            require(os.geteuid() == 0, "Usa sudo para recuperar el respaldo.")
            rollback(args.rollback)
        elif args.check:
            preflight(project, args.domain.lower())
        else:
            apply(project, args.domain.lower())
    except (SetupError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"DETENIDO: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrumpido. Revisa los mensajes de recuperacion.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
