#!/usr/bin/env python3
"""Lanza servidores efímeros locales; nunca usa DATABASE_URL del entorno del operador."""
from __future__ import annotations
import argparse
import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pg-root", required=True, type=Path)
    p.add_argument("--mongo-bin", required=True, type=Path)
    p.add_argument("--backup-tools", action="store_true", help="Prueba pg_dump, pg_restore, mongodump y mongorestore de PATH")
    a = p.parse_args()
    entorno = dict(os.environ)
    entorno["LD_LIBRARY_PATH"] = str(a.pg_root / "lib")
    with tempfile.TemporaryDirectory(prefix="tt-security-test-") as carpeta:
        carpeta = Path(carpeta)
        os.chmod(carpeta, 0o700)
        def run(args, **kw):
            r = subprocess.run([str(x) for x in args], capture_output=True, env=entorno, **kw)
            if r.returncode:
                print(r.stderr.decode(errors="replace") + r.stdout.decode(errors="replace"))
                if (carpeta / "postgres.log").exists():
                    print((carpeta / "postgres.log").read_text()[-3000:])
                raise RuntimeError("Falló el servidor sintético de prueba")
            return r
        ca = carpeta / "ca.crt"
        run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
             "-subj", "/CN=TT CA SOLO PRUEBAS", "-keyout", carpeta / "ca.key", "-out", ca])
        for nombre in ["postgres", "mongo", "backend"]:
            run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=" + nombre,
                 "-keyout", carpeta / (nombre + ".key"), "-out", carpeta / (nombre + ".csr")])
            (carpeta / "ext").write_text("subjectAltName=DNS:localhost,IP:127.0.0.1,DNS:" + nombre + "\nextendedKeyUsage=serverAuth\n")
            run(["openssl", "x509", "-req", "-days", "2", "-in", carpeta / (nombre + ".csr"),
                 "-CA", ca, "-CAkey", carpeta / "ca.key", "-set_serial", str(secrets.randbits(64)),
                 "-extfile", carpeta / "ext", "-out", carpeta / (nombre + ".crt")])
            os.chmod(carpeta / (nombre + ".key"), 0o600)
        (carpeta / "mongo.pem").write_bytes((carpeta / "mongo.crt").read_bytes() + (carpeta / "mongo.key").read_bytes())
        os.chmod(carpeta / "mongo.pem", 0o600)
        pg_dir, mongo_dir = carpeta / "pgdata", carpeta / "mongodata"
        mongo_dir.mkdir()
        pg_port, mongo_port, backend_port = libre(), libre(), libre()
        run([a.pg_root / "bin/initdb", "-D", pg_dir, "-U", "postgres", "--auth=trust", "--encoding=UTF8", "--locale=C"])
        with (pg_dir / "postgresql.conf").open("a") as f:
            f.write("\nlisten_addresses='127.0.0.1'\nport=" + str(pg_port) + "\nssl=on\nssl_cert_file='" +
                    str(carpeta / "postgres.crt") + "'\nssl_key_file='" + str(carpeta / "postgres.key") +
                    "'\nunix_socket_directories=''\nshared_buffers='32MB'\nmax_connections=30\n")
        (pg_dir / "pg_hba.conf").write_text(
            "local all all trust\nhostssl all postgres 127.0.0.1/32 trust\n"
            "hostssl all all 127.0.0.1/32 scram-sha-256\nhostnossl all all 127.0.0.1/32 reject\n")
        pg_activo = mongo_activo = False
        try:
            run([a.pg_root / "bin/pg_ctl", "-D", pg_dir, "-l", carpeta / "postgres.log", "-w", "start"])
            pg_activo = True
            run([a.mongo_bin, "--dbpath", mongo_dir, "--port", str(mongo_port), "--bind_ip", "127.0.0.1",
                 "--auth", "--nounixsocket", "--tlsMode", "requireTLS", "--tlsCAFile", ca,
                 "--tlsCertificateKeyFile", carpeta / "mongo.pem", "--tlsAllowConnectionsWithoutCertificates",
                 "--wiredTigerCacheSizeGB", "0.25", "--fork", "--logpath", carpeta / "mongo.log",
                 "--pidfilepath", carpeta / "mongo.pid"])
            mongo_activo = True
            entorno.update(
                TEST_PG_PORT=str(pg_port), TEST_MONGO_PORT=str(mongo_port), TEST_BACKEND_PORT=str(backend_port),
                TEST_CA=str(ca), TEST_PG_DATA_DIR=str(pg_dir), TEST_FOLDER=str(carpeta),
                TEST_MONGO_PASSWORD=secrets.token_hex(32),
                NODE_ENV="production", NODE_EXTRA_CA_CERTS=str(ca),
                JWT_SECRET="fixture-not-for-production-" + secrets.token_hex(24),
                DATA_ENCRYPTION_KEY=base64.b64encode(b"T" * 32).decode(),
                MAIL_PROVIDER="resend", RESEND_API_KEY="fixture-no-correo-real",
                ADMIN_EMAIL="", ADMIN_PASSWORD="", ALLOW_LEGACY_PLAINTEXT="true",
                TEST_PYTHON=sys.executable, TEST_BACKUP_TOOLS="1" if a.backup_tools else "0",
            )
            prueba = subprocess.run(["node", str(ROOT / "deploy/seguridad/tests/integracion_real.mjs")],
                                   cwd=ROOT / "backend", env=entorno, capture_output=True, timeout=180)
            if prueba.returncode:
                print(prueba.stderr.decode(errors="replace"))
                raise RuntimeError("Falló la prueba real de bases")
            print(prueba.stdout.decode())
        finally:
            if mongo_activo:
                os.kill(int((carpeta / "mongo.pid").read_text()), 15)
            if pg_activo:
                run([a.pg_root / "bin/pg_ctl", "-D", pg_dir, "-m", "fast", "-w", "stop"])


if __name__ == "__main__":
    main()
