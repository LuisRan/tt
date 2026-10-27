"""Guardrails de la directriz crítica. Si alguno falla, el pipeline NO es válido.

1. Ningún asset oficial en el repo (hash/nombre de formatos/ o imágenes raster).
2. Ningún elemento de seguridad dibujado (identificadores/specs con esos términos).
3. Marca de agua "MOCK DATA" presente en cada página generada (ver test_pipeline_watermark).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "formatos"
RASTER_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
BINARY_DOC_EXT = {".pdf", ".svg", ".ttf", ".otf", ".woff", ".woff2"}
# Archivos binarios permitidos explícitamente (ruta relativa). Vacío a propósito.
ALLOWLIST: set[str] = set()

SECURITY_TERMS = [
    "guilloche", "guilloché", "microtext", "microtexto", "ovi", "ovd", "uv_ink", "tinta_uv",
    "rainbow", "arcoiris", "hologram", "holograma", "relieve", "emboss", "tactil", "táctil",
    "patron_debilitado", "ghost_image", "foto_fantasma",
]


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no es un repo git")
    return [ROOT / p for p in out.splitlines() if p and (ROOT / p).is_file()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reference_folder_is_gitignored():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "formatos/" in gitignore
    for f in _tracked_files():
        assert REFERENCE_DIR not in f.parents, f"archivo de referencia en el repo: {f}"


def test_no_official_asset_copies():
    if not REFERENCE_DIR.is_dir():
        pytest.skip("formatos/ no disponible localmente")
    ref_hashes = {_sha256(p) for p in REFERENCE_DIR.rglob("*") if p.is_file() and p.name != ".DS_Store"}
    ref_names = {p.name.lower() for p in REFERENCE_DIR.rglob("*") if p.is_file()}
    for f in _tracked_files():
        assert _sha256(f) not in ref_hashes, f"copia de asset oficial: {f}"
        assert f.name.lower() not in ref_names or f.name == ".DS_Store", f"nombre de asset oficial: {f}"


def test_no_binary_assets_in_repo():
    for f in _tracked_files():
        rel = f.relative_to(ROOT).as_posix()
        if rel in ALLOWLIST:
            continue
        assert f.suffix.lower() not in RASTER_EXT | BINARY_DOC_EXT, f"asset binario no permitido: {rel}"


def _identifier_pattern() -> re.Pattern[str]:
    terms = "|".join(re.escape(t) for t in SECURITY_TERMS)
    # identificadores de Python (def/class/variables) que contengan el término
    return re.compile(rf"\b(def|class)\s+\w*({terms})\w*|\b\w*_({terms})\w*\s*=", re.IGNORECASE)


def test_no_security_element_code():
    pat = _identifier_pattern()
    for f in _tracked_files():
        if f.suffix != ".py" or "tests" in f.parts:
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            assert not pat.search(line), f"posible elemento de seguridad en {f}:{n}: {line.strip()}"


def test_specs_have_no_security_elements():
    specs = list((ROOT / "src" / "layouts" / "specs").glob("*.json"))
    if not specs:
        pytest.skip("specs de layout aún no disponibles")
    terms = re.compile("|".join(re.escape(t) for t in SECURITY_TERMS), re.IGNORECASE)
    for spec in specs:
        data = json.loads(spec.read_text(encoding="utf-8"))
        for face in data["faces"]:
            for field in face["fields"]:
                for key in ("name", "static_value", "label"):
                    val = field.get(key) or ""
                    # 'static_value' de SHAPE describe la figura sustituta; no debe nombrar elementos de seguridad
                    assert not re.search(rf"\b({terms.pattern})\b", val, re.IGNORECASE), (
                        f"{spec.name}:{field['name']}.{key} menciona elemento de seguridad: {val!r}"
                    )


# --- Datos personales de los especímenes de formatos/ --------------------------
# Solo se guardan (longitud, sha256) de cadenas normalizadas (minúsculas, sin
# acentos, no alfanumérico -> espacio) para no introducir esos datos en el repo.
SPECIMEN_HASHES = {
    "7698c656013dff18a0604c50f91426c7a4933999a140535d7ffc7e30d1468a3f",
    "a09ef2459a9fc8d2a2d45dd01bbc1b1bd4ae189d237e5c494fe396ca52f6b4e8",
    "a71fd6b92929777c3f11560d8eb3536ec0c34cb35946a167391c3b2e3489a9f6",
    "051a437ae7f27f3c38a5957b2b09a7a436416c26990e84f65576f1397fcfb6f2",
    "6e262b481b5281cca687b8b474f93e38976648a514f6cef0ac3ff8c65e247cab",
    "7c29efb64c9aa15ded718694bcf2cfdd4bafff323955af7a2291de39c6fd47c6",
    "aa39995c5638f54debadbe2286eae9f4fc58722f85a3b9a593fb440d9659f654",
    "e89e5542e7d1c8881f90acce0a74fb83aff8e166e675a3279a0a87d2f7297959",
    "de9e4751bfb3a071c15a1da187e490cc18d289d87b18f405a33b736d64991575",
    "0cacc8be729489c1c2082d9cd1bfa978dddcf7d904eceab1f9c1081e6afcc645",
    "3b97687917aa852db4c005d6e34e3aca1ac7434c6d92f27a9028ef87e2a958d0",
    "d8cc232ffb015350a5006c09c63a806f80abf06d2ce286cda7537ecb67c7053e",
    "687dcda946437a428f423ac84033c59ccaee1a84d1d227d18474c22329bc7f54",
    "8007c33642ec4b9b768ed5b6e08390bf3344dc753d72d359cd74eac6eea350a9",
    "7ed4fb4f74859722b586bd725c7038651e5b099f550f105158553c5ddeb14946",
}
SPECIMEN_LENGTHS = (6, 8, 9, 13, 15, 18, 19, 22, 25, 41)


def _normalize(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text)


def find_specimen_data(text: str) -> bool:
    norm = _normalize(text)
    for n in set(SPECIMEN_LENGTHS):
        for i in range(len(norm) - n + 1):
            if hashlib.sha256(norm[i : i + n].encode()).hexdigest() in SPECIMEN_HASHES:
                return True
    return False


def test_specimen_detector_works(monkeypatch):
    fake = "zafiro tlacuache"  # cadena inventada; nunca escribir aquí datos reales
    hashes = SPECIMEN_HASHES | {hashlib.sha256(fake.encode()).hexdigest()}
    monkeypatch.setitem(globals(), "SPECIMEN_HASHES", hashes)
    monkeypatch.setitem(globals(), "SPECIMEN_LENGTHS", SPECIMEN_LENGTHS + (len(fake),))
    assert find_specimen_data("Texto: ZAFIRO  Tlacuaché, otro")
    assert not find_specimen_data("ROSALBA QUINTERO LEDESMA")


def test_no_specimen_personal_data():
    for f in _tracked_files():
        if f.suffix.lower() not in {".py", ".md", ".json", ".txt", ".toml", ".yaml", ".yml"}:
            continue
        if f.name == "proyecto.md":
            continue
        assert not find_specimen_data(f.read_text(encoding="utf-8", errors="ignore")), (
            f"datos personales de un espécimen de referencia en {f.relative_to(ROOT)}"
        )
