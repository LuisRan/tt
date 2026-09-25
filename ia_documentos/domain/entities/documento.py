"""
domain/entities/documento.py
Entidades del dominio. No importan nada de infraestructura.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class TipoDocumento(str, Enum):
    INE             = "ine"
    ACTA_NACIMIENTO = "acta_nacimiento"
    PASAPORTE       = "pasaporte"
    CURP            = "curp"
    DESCONOCIDO     = "desconocido"


class DocumentoNoSoportadoError(Exception):
    """
    Se lanza cuando el documento recibido no corresponde a ninguno
    de los 4 tipos soportados. El pipeline detiene el proceso y
    retorna este error al usuario con un mensaje claro.
    """
    def __init__(self, mensaje: str = None):
        self.mensaje = mensaje or (
            "El documento no fue reconocido como uno de los tipos soportados: "
            "INE, Acta de Nacimiento, Pasaporte o CURP. "
            "Verifica que el PDF sea uno de estos documentos y que esté legible."
        )
        super().__init__(self.mensaje)


@dataclass
class DocumentoIdentidad:
    ruta_pdf: Path
    user_id: str
    doc_id: str
    timestamp_ingesta: datetime = field(default_factory=datetime.utcnow)
    tipo_detectado: TipoDocumento = TipoDocumento.DESCONOCIDO
    hash_sha256: Optional[str] = None
    num_paginas: Optional[int] = None


@dataclass
class TextoExtraido:
    doc_id: str
    texto_crudo: str
    ruta_md: Optional[Path] = None
    tipo_detectado: TipoDocumento = TipoDocumento.DESCONOCIDO
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ResultadoProcesamiento:
    doc_id: str
    user_id: str
    tipo_documento: TipoDocumento
    datos: dict
    ruta_texto_crudo: Optional[str]
    adaptador_usado: str
    pipeline_version: str
    timestamp_procesamiento: str
    hash_sha256: Optional[str] = None
    datos_adicionales: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "user_id": self.user_id,
            "tipo_documento": self.tipo_documento.value,
            "datos": self.datos,
            "metadatos": {
                "ruta_texto_crudo": self.ruta_texto_crudo,
                "adaptador_usado": self.adaptador_usado,
                "pipeline_version": self.pipeline_version,
                "timestamp_procesamiento": self.timestamp_procesamiento,
                "hash_sha256": self.hash_sha256,
            },
            "datos_adicionales": self.datos_adicionales,
        }
