"""
infrastructure/storage/file_adapters.py
Adaptadores de almacenamiento en sistema de archivos local.

- MarkdownFileAdapter  → persiste texto crudo en .md  (implementa ITextStoragePort)
- JsonFileAdapter      → persiste resultados en .json (implementa IDocumentoRepository)
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from domain.entities.documento import TextoExtraido
from domain.ports.interfaces import IDocumentoRepository, ITextStoragePort

logger = logging.getLogger(__name__)


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

        ruta.write_text(contenido, encoding="utf-8")
        ruta.chmod(0o600)

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
        ruta_doc.write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ruta_doc.chmod(0o600)

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

        ruta_idx.write_text(
            json.dumps(indice, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ruta_idx.chmod(0o600)

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
