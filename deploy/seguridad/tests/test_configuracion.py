import importlib.util
import sys
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "deploy/seguridad"))
from configurar import secure_uri, ErrorDespliegue


def test_uri_postgres_escapa_password_y_exige_certificado():
    u = urlsplit(secure_uri("postgresql://admin:p%40ss%24%27@postgres:5432/tt_ocr?sslmode=disable",
                            user="runtime", password="x@:$'"))
    assert unquote(u.password) == "x@:$'"
    assert u.username == "runtime"
    assert parse_qs(u.query)["sslmode"] == ["verify-full"]
    assert parse_qs(u.query)["sslrootcert"] == ["/run/tt/ca.crt"]


def test_mongo_no_conserva_bypass_y_separa_auth_source():
    u = urlsplit(secure_uri("mongodb://admin:p@mongo:27017/?authSource=admin&tlsInsecure=true",
                            mongo=True, user="app", password="nueva", database="tt_ocr"))
    q = parse_qs(u.query)
    assert u.path == "/tt_ocr"
    assert q["authSource"] == ["tt_ocr"]
    assert q["tls"] == ["true"]
    assert "tlsInsecure" not in q


@pytest.mark.parametrize("uri", ["postgresql://x:y@otro/tt_ocr", "postgresql://x:y@147.93.133.251/tt_ocr"])
def test_instalador_no_reconfigura_bases_remotas(uri):
    with pytest.raises(ErrorDespliegue): secure_uri(uri)


def test_plantilla_https_publico_conserva_verificacion_al_backend():
    spec = importlib.util.spec_from_file_location("https_publico", ROOT / "deploy/seguridad/https_publico.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    source = (ROOT / "nginx/templates/default.conf.template").read_text()
    source = "proxy_ssl_verify on;\nproxy_ssl_name backend;\nproxy_ssl_protocols TLSv1.2 TLSv1.3;\nproxy_ssl_trusted_certificate /run/tt/ca.crt;\n" + source.replace("proxy_pass http://backend_node;", "proxy_pass https://backend_node;")
    nuevo = modulo.public_template(source, "ejemplo.invalid")
    assert "proxy_pass https://backend_node;" in nuevo
    assert "proxy_ssl_verify on;" in nuevo
    assert "proxy_ssl_protocols TLSv1.2 TLSv1.3;" in nuevo
    assert "/etc/letsencrypt/live/ejemplo.invalid/fullchain.pem" in nuevo
