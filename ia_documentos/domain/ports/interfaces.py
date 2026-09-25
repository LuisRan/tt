"""
domain/ports/interfaces.py
Contratos (interfaces) que cada adaptador de infraestructura debe implementar.
El dominio y la capa de aplicación solo conocen estas interfaces, nunca las
implementaciones concretas (Docling, Ollama, archivos, etc.).
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from domain.entities.documento import (
    DocumentoIdentidad,
    TextoExtraido,
    TipoDocumento,
)


class IOcrPort(ABC):
    """Extrae texto de un PDF y detecta tipo de documento."""

    @abstractmethod
    def extraer_texto(self, documento: DocumentoIdentidad) -> TextoExtraido:
        """
        Procesa el PDF y devuelve el texto completo normalizado.
        Debe manejar PDFs en orientación incorrecta automáticamente.
        """
        ...

    @abstractmethod
    def detectar_tipo(self, texto: str) -> TipoDocumento:
        """Clasifica el documento a partir del texto extraído."""
        ...


class IFieldExtractorPort(ABC):
    """Extrae campos de dominio del texto crudo."""

    @abstractmethod
    def extraer_campos(
        self,
        texto: str,
        tipo_documento: TipoDocumento,
    ) -> dict:
        """
        Retorna un dict con los campos del esquema correspondiente.
        Si un campo no se encuentra, devuelve None para ese campo.
        """
        ...

    @property
    @abstractmethod
    def nombre_adaptador(self) -> str:
        """Identificador del adaptador para el envelope de salida."""
        ...


class ITextStoragePort(ABC):
    """Persiste y recupera el texto crudo en formato Markdown."""

    @abstractmethod
    def guardar_texto(self, texto_extraido: TextoExtraido) -> Path:
        """Guarda el texto y devuelve la ruta del archivo .md creado."""
        ...

    @abstractmethod
    def recuperar_texto(self, doc_id: str) -> Optional[str]:
        """Recupera el texto crudo dado el doc_id. None si no existe."""
        ...


class IDocumentoRepository(ABC):
    """Persiste y recupera el JSON de resultado por usuario/documento."""

    @abstractmethod
    def guardar_resultado(self, resultado: dict, doc_id: str, user_id: str) -> Path:
        ...

    @abstractmethod
    def obtener_resultado(self, doc_id: str) -> Optional[dict]:
        ...

    @abstractmethod
    def listar_documentos_usuario(self, user_id: str) -> list[dict]:
        ...
