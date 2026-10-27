"""Guardrails estaticos de la imagen Docker (sin Docker, rapidos).

Directriz critica: `formatos/` (referencias oficiales) NUNCA entra a la imagen ni
al contexto de build; la imagen es reproducible (base fijada, deps con ==/hashes)
y corre sin privilegios. Cada test se omite solo si el archivo que revisa no existe.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
COMPOSE = ROOT / "docker-compose.yml"
LOCK = ROOT / "requirements.lock"
PYPROJECT = ROOT / "pyproject.toml"

REQUIRED_IGNORED = ["formatos", ".venv", ".git", ".claude", "data"]
EXTRA_IGNORED = ["out", "__pycache__", ".pytest_cache"]


def _need(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path.name} no existe")
    return path.read_text(encoding="utf-8")


def _dockerfile_instructions() -> list[tuple[str, str]]:
    """(INSTRUCCION, resto) uniendo continuaciones con '\\' y sin comentarios."""
    text = _need(DOCKERFILE)
    logical: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not buf and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if buf and line.lstrip().startswith("#"):
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        logical.append((buf + line).strip())
        buf = ""
    if buf:
        logical.append(buf.strip())
    out = []
    for item in logical:
        name, _, rest = item.partition(" ")
        out.append((name.upper(), rest.strip()))
    return out


def _ignore_patterns() -> list[str]:
    text = _need(DOCKERIGNORE)
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock_requirements() -> dict[str, tuple[str, list[str]]]:
    """nombre normalizado -> (version, hashes)."""
    text = _need(LOCK)
    joined: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("#") or not line.strip():
            assert not buf, "comentario o linea vacia dentro de una continuacion del lock"
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        joined.append((buf + line).strip())
        buf = ""
    assert not buf
    reqs: dict[str, tuple[str, list[str]]] = {}
    for item in joined:
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9._+!-]+)((?:\s+--hash=sha256:[0-9a-f]{64})*)$", item)
        assert m, f"linea del lock no fijada con == (y hashes sha256): {item!r}"
        name = _norm_name(m.group(1))
        assert name not in reqs, f"{name} duplicado en el lock"
        reqs[name] = (m.group(2), re.findall(r"--hash=sha256:([0-9a-f]{64})", m.group(3)))
    return reqs


# ------------------------------------------------------------ .dockerignore
def test_dockerignore_excludes_sensitive_dirs():
    pats = [p.rstrip("/") for p in _ignore_patterns()]
    for d in REQUIRED_IGNORED:
        assert d in pats, f".dockerignore debe excluir {d}/"


def test_dockerignore_excludes_caches_and_outputs():
    pats = [p.rstrip("/") for p in _ignore_patterns()]
    for d in EXTRA_IGNORED:
        assert d in pats or f"**/{d}" in pats, f".dockerignore deberia excluir {d}/"


def test_dockerignore_never_reincludes_references():
    for p in _ignore_patterns():
        if p.startswith("!"):
            assert "formatos" not in p and ".venv" not in p, f"re-inclusion peligrosa: {p}"


# --------------------------------------------------------------- Dockerfile
def test_dockerfile_never_copies_formatos_or_whole_context():
    for name, rest in _dockerfile_instructions():
        if name not in {"COPY", "ADD"}:
            continue
        assert "formatos" not in rest.lower(), f"{name} de formatos/: {rest}"
        args = [a for a in rest.split() if not a.startswith("--")]
        sources = args[:-1]
        for src in sources:
            assert src not in {".", "./", "*", "./*", "/", ".*"}, (
                f"{name} copia el contexto completo ({src!r}); listar rutas explicitas"
            )
        if name == "ADD":
            assert not any(s.startswith(("http://", "https://")) for s in sources), "ADD de URL remota"


def test_dockerfile_copies_only_allowed_sources():
    allowed = {"contracts/", "src/", "tests/", "pyproject.toml", "PLAN.md", "README.md", ".gitignore", "requirements.lock"}
    for name, rest in _dockerfile_instructions():
        if name != "COPY":
            continue
        args = [a for a in rest.split() if not a.startswith("--")]
        for src in args[:-1]:
            assert src in allowed, f"COPY de {src!r} no esta en la lista permitida {sorted(allowed)}"


def test_dockerfile_base_is_pinned_and_not_latest():
    froms = [rest for name, rest in _dockerfile_instructions() if name == "FROM"]
    assert froms, "falta FROM"
    for rest in froms:
        image = rest.split()[0]
        ref, _, digest = image.partition("@")
        assert ":" in ref, f"la base debe llevar tag explicito: {image}"
        tag = ref.split(":", 1)[1]
        assert tag != "latest" and "latest" not in tag
        assert re.match(r"^\d+\.\d+\.\d+", tag), f"el tag debe fijar version exacta X.Y.Z: {tag}"
        assert "slim" in tag and "bookworm" in tag
        assert re.match(r"^sha256:[0-9a-f]{64}$", digest), f"la base debe fijar digest sha256: {image}"
    text = _need(DOCKERFILE)
    assert not re.search(r"(?im)^\s*(FROM|ARG).*:latest\b", text)


def test_dockerfile_installs_only_from_lock_with_hashes():
    runs = [rest for name, rest in _dockerfile_instructions() if name == "RUN" and "pip" in rest]
    assert runs, "falta el pip install"
    joined = " ".join(runs)
    assert "requirements.lock" in joined
    assert "--require-hashes" in joined
    assert "--no-deps" in joined
    assert not re.search(r"pip\s+install\s+(?!.*-r\s)[^-\s]", joined), "pip install de paquetes sueltos"
    assert "pip install -e" not in joined and "pip install ." not in joined


def test_dockerfile_runs_as_non_root():
    users = [rest.split(":")[0].strip() for name, rest in _dockerfile_instructions() if name == "USER"]
    assert users, "falta USER"
    assert users[-1] not in {"root", "0"}, "el contenedor no debe correr como root"


def test_dockerfile_entrypoint_and_workdir():
    ins = _dockerfile_instructions()
    entry = [rest for name, rest in ins if name == "ENTRYPOINT"]
    assert entry and entry[-1].replace(" ", "") == '["python","-m","src"]'
    assert ("WORKDIR", "/app") in ins


def test_dockerfile_determinism_env():
    text = _need(DOCKERFILE)
    for var in ("PYTHONHASHSEED=0", "PYTHONDONTWRITEBYTECODE=1", "TZ=UTC", "LANG=C.UTF-8", "SOURCE_DATE_EPOCH=0", "OCR_DOCKER=1"):
        assert var in text, f"falta {var} en el Dockerfile"


# ------------------------------------------------------------------- lock
def test_lock_pins_everything_with_hashes():
    reqs = _lock_requirements()
    assert reqs
    for name, (_, hashes) in reqs.items():
        assert hashes, f"{name} sin hashes sha256"


def test_lock_covers_pyproject_dependencies():
    if not PYPROJECT.is_file():
        pytest.skip("pyproject.toml no existe")
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = list(data["project"].get("dependencies", []))
    for extra in data["project"].get("optional-dependencies", {}).values():
        declared += extra
    lock = _lock_requirements()
    assert declared
    for dep in declared:
        name = _norm_name(re.split(r"[<>=!~\[;\s]", dep, maxsplit=1)[0])
        assert name in lock, f"{name} (pyproject) no esta en requirements.lock"
    # El propio paquete no debe aparecer en el lock.
    assert _norm_name(data["project"]["name"]) not in lock


def test_lock_versions_satisfy_pyproject_minimums():
    if not PYPROJECT.is_file():
        pytest.skip("pyproject.toml no existe")
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = list(data["project"].get("dependencies", []))
    for extra in data["project"].get("optional-dependencies", {}).values():
        declared += extra
    lock = _lock_requirements()

    def vt(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", v)[:4])

    for dep in declared:
        m = re.match(r"^([A-Za-z0-9._-]+)\s*>=\s*([0-9][0-9.]*)", dep)
        if m:
            assert vt(lock[_norm_name(m.group(1))][0]) >= vt(m.group(2)), f"{dep}: el lock queda por debajo del minimo"


# ---------------------------------------------------------------- compose
def _compose_services() -> dict[str, str]:
    text = _need(COMPOSE)
    m = re.search(r"(?m)^services:\s*$", text)
    assert m, "falta la seccion services"
    body = text[m.end():]
    blocks: dict[str, str] = {}
    current = None
    for line in body.splitlines():
        h = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if h:
            current = h.group(1)
            blocks[current] = ""
        elif current is not None:
            blocks[current] += line + "\n"
    return blocks


def test_compose_has_three_services_sharing_pinned_platform_and_user():
    text = _need(COMPOSE)
    services = _compose_services()
    assert {"menu", "generate", "tests"} <= set(services)
    # El ancla comun fija plataforma y usuario; cada servicio debe heredarla.
    assert "platform: ${OCR_PLATFORM:-linux/amd64}" in text
    anchor = re.search(r"(?s)x-ocr-common:.*?\n\nservices:", text)
    assert anchor
    assert re.search(r'user:\s*"\$\{UID:-1000\}:\$\{GID:-1000\}"', anchor.group(0))
    for name in ("menu", "generate", "tests"):
        block = services[name]
        inherits = "<<: *ocr-common" in block
        own_platform = re.search(r"platform:\s*\$\{OCR_PLATFORM:-linux/amd64\}", block)
        assert inherits or own_platform, f"{name} no fija platform"
        assert not re.search(r"user:\s*(root|0)\b", block)
        assert not re.search(r"(?m)^\s*privileged:\s*true", block)


def test_compose_services_behaviour():
    s = _compose_services()
    assert "stdin_open: true" in s["menu"] and "tty: true" in s["menu"]
    for name in ("menu", "generate"):
        assert "./data:/app/data" in s[name], f"{name} debe montar ./data:/app/data"
    assert "pytest" in s["tests"]
    assert "./data" not in s["tests"]
    assert "generate" in s["generate"]


def test_compose_never_mounts_references_or_uses_latest():
    text = _need(COMPOSE)
    assert "formatos" not in text
    assert not re.search(r"image:\s*\S+:latest\b", text)
    assert not re.search(r"(?m)^\s*-\s*\.:/", text), "no montar el repo completo (arrastraria formatos/)"
