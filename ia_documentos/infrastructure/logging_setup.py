"""
infrastructure/logging_setup.py
Configura structlog para logging estructurado en JSON.
Un único registro por ejecución del pipeline incluye:
doc_id, tipo, adaptador, tiempo_ms, resultado_validacion.
"""
from __future__ import annotations
import logging
import logging.handlers
import sys
from pathlib import Path

import structlog


def configurar_logging(logs_dir: Path, nivel: str = "INFO") -> None:
    """
    Llama esta función una vez al iniciar el proceso.
    Produce logs JSON en archivo + texto legible en consola.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)

    nivel_int = getattr(logging, nivel.upper(), logging.INFO)

    # Handler consola (texto legible)
    consola = logging.StreamHandler(sys.stdout)
    consola.setLevel(nivel_int)

    # Handler archivo rotativo (JSON)
    archivo = logging.handlers.RotatingFileHandler(
        logs_dir / "pipeline.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    archivo.setLevel(nivel_int)

    logging.basicConfig(
        level=nivel_int,
        handlers=[consola, archivo],
    )

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
