"""
application/validator.py
Valida el dict de campos extraídos contra el schema Pydantic del tipo de documento.
Retorna (campos_validados: dict, errores: list[str]).
"""
from __future__ import annotations
import logging
from typing import Tuple

from pydantic import ValidationError

from domain.entities.documento import TipoDocumento
from domain.schemas.document_schemas import get_schema

logger = logging.getLogger(__name__)


def validar_campos(
    campos: dict,
    tipo_documento: TipoDocumento,
) -> Tuple[dict, list[str]]:
    """
    Intenta construir el modelo Pydantic correspondiente con los campos recibidos.

    Returns:
        (campos_limpios, errores)
        - campos_limpios: dict serializable, campos inválidos convertidos a None
        - errores: lista de strings con los errores encontrados (vacía si todo OK)
    """
    SchemaClass = get_schema(tipo_documento)
    errores: list[str] = []

    try:
        instancia = SchemaClass(**campos)
        campos_limpios = instancia.model_dump(mode="json")
        logger.info(
            "validacion_ok",
            extra={"tipo": tipo_documento.value},
        )
        return campos_limpios, []

    except ValidationError as e:
        for err in e.errors():
            campo = " -> ".join(str(loc) for loc in err["loc"])
            mensaje = f"{campo}: {err['msg']}"
            errores.append(mensaje)
            logger.warning(
                "validacion_campo_invalido",
                extra={"campo": campo, "error": err["msg"]},
            )

        # Retornar lo que sea válido: crear instancia ignorando campos inválidos
        campos_filtrados = {
            k: v for k, v in campos.items()
            if k in SchemaClass.model_fields
        }
        # Forzar None en campos que fallaron
        campos_fallidos = {err.split(":")[0].strip() for err in errores}
        for campo in campos_fallidos:
            campos_filtrados[campo] = None

        try:
            instancia = SchemaClass(**campos_filtrados)
            campos_limpios = instancia.model_dump(mode="json")
        except ValidationError:
            # Si sigue fallando, devolver dict vacío con todos None
            campos_limpios = {k: None for k in SchemaClass.model_fields}

        return campos_limpios, errores
