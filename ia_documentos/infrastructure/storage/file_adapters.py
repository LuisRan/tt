"""
infrastructure/storage/file_adapters.py
Adaptadores de almacenamiento en sistema de archivos local.

- MarkdownFileAdapter  → persiste texto crudo en .md  (implementa ITextStoragePort)
- JsonFileAdapter      → persiste resultados en .json (implementa IDocumentoRepository)
"""
from __future__ import annotations
import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from domain.entities.documento import TextoExtraido
from domain.ports.interfaces import IDocumentoRepository, ITextStoragePort

logger = logging.getLogger(__name__)

try:  # fcntl no existe en Windows: allí el lock de archivo es no-op
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

_LOCKS_PROCESO: dict[str, threading.Lock] = {}
_LOCKS_GUARDA = threading.Lock()


def escribir_privado(ruta: Path, contenido: str) -> None:
    """Escribe con permisos 0600 desde la creación y reemplazo atómico."""
    tmp = ruta.with_name(f".{ruta.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contenido)
        os.replace(tmp, ruta)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


@contextmanager
def _bloqueo_archivo(ruta: Path) -> Iterator[None]:
    """Lock exclusivo (hilos del proceso + flock entre procesos en posix)."""
    with _LOCKS_GUARDA:
        lock = _LOCKS_PROCESO.setdefault(str(ruta), threading.Lock())
    with lock:
        if fcntl is None:  # pragma: no cover
            yield
            return
        fd = os.open(str(ruta) + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


# ── Markdown ──────────────────────────────────────────────────────────────────

class MarkdownFileAdapter(ITextStoragePort):
    """
    Guarda el texto crudo extraído en un archivo .md con metadatos en cabecera.
    Permisos 600 (solo lectura/escritura del propietario).
    """

    def __init__(self, directorio: Path):
        self._dir = directorio
        self._dir.mkdir(parents=True, exist_ok=True)

    def guardar_texto(self, texto_extraido: TextoExtraido) -> Path:
        nombre_archivo = f"{texto_extraido.doc_id}.md"
        ruta = self._dir / nombre_archivo

        cabecera = (
            f"---\n"
            f"doc_id: {texto_extraido.doc_id}\n"
            f"tipo_documento: {texto_extraido.tipo_detectado.value}\n"
            f"timestamp: {texto_extraido.timestamp.isoformat()}\n"
            f"---\n\n"
        )

        contenido = cabecera + texto_extraido.texto_crudo

        escribir_privado(ruta, contenido)

        logger.info(
            "texto_guardado",
            extra={"doc_id": texto_extraido.doc_id, "ruta": str(ruta)},
        )
        return ruta

    def recuperar_texto(self, doc_id: str) -> Optional[str]:
        ruta = self._dir / f"{doc_id}.md"
        if not ruta.exists():
            logger.warning("texto_no_encontrado", extra={"doc_id": doc_id})
            return None

        contenido = ruta.read_text(encoding="utf-8")
        # Eliminar cabecera YAML (entre los dos ---)
        partes = contenido.split("---\n", 2)
        if len(partes) >= 3:
            return partes[2].strip()
        return contenido


# ── JSON ──────────────────────────────────────────────────────────────────────

class JsonFileAdapter(IDocumentoRepository):
    """
    Persiste el resultado final en un archivo .json por documento.
    También mantiene un índice por usuario para soporte multi-documento.
    Permisos 600.
    """

    def __init__(self, directorio: Path):
        self._dir = directorio
        self._dir.mkdir(parents=True, exist_ok=True)
        self._indice_dir = directorio / "_indices"
        self._indice_dir.mkdir(parents=True, exist_ok=True)

    # ── Escritura ─────────────────────────────────────────────────────────────

    def guardar_resultado(self, resultado: dict, doc_id: str, user_id: str) -> Path:
        # Archivo por documento
        ruta_doc = self._dir / f"{doc_id}.json"
        escribir_privado(ruta_doc, json.dumps(resultado, ensure_ascii=False, indent=2))

        # Actualizar índice del usuario
        self._actualizar_indice_usuario(user_id, doc_id, resultado)

        logger.info(
            "resultado_guardado",
            extra={"doc_id": doc_id, "user_id": user_id, "ruta": str(ruta_doc)},
        )
        return ruta_doc

    def _actualizar_indice_usuario(
        self, user_id: str, doc_id: str, resultado: dict
    ) -> None:
        ruta_idx = self._indice_dir / f"{user_id}.json"
        with _bloqueo_archivo(ruta_idx):
            self._actualizar_indice_locked(ruta_idx, user_id, doc_id, resultado)

    def _actualizar_indice_locked(
        self, ruta_idx: Path, user_id: str, doc_id: str, resultado: dict
    ) -> None:
        if ruta_idx.exists():
            indice = json.loads(ruta_idx.read_text(encoding="utf-8"))
        else:
            indice = {"user_id": user_id, "documentos": []}

        # Evitar duplicados: reemplazar si doc_id ya existe
        indice["documentos"] = [
            d for d in indice["documentos"] if d.get("doc_id") != doc_id
        ]
        indice["documentos"].append(
            {
                "doc_id": doc_id,
                "tipo_documento": resultado.get("tipo_documento"),
                "timestamp": resultado.get("metadatos", {}).get(
                    "timestamp_procesamiento", datetime.utcnow().isoformat()
                ),
            }
        )

        escribir_privado(ruta_idx, json.dumps(indice, ensure_ascii=False, indent=2))

    # ── Lectura ───────────────────────────────────────────────────────────────

    def obtener_resultado(self, doc_id: str) -> Optional[dict]:
        ruta = self._dir / f"{doc_id}.json"
        if not ruta.exists():
            logger.warning("resultado_no_encontrado", extra={"doc_id": doc_id})
            return None
        return json.loads(ruta.read_text(encoding="utf-8"))

    def listar_documentos_usuario(self, user_id: str) -> list[dict]:
        ruta_idx = self._indice_dir / f"{user_id}.json"
        if not ruta_idx.exists():
            return []
        indice = json.loads(ruta_idx.read_text(encoding="utf-8"))
        return indice.get("documentos", [])
