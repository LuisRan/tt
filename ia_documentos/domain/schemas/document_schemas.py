"""
domain/schemas/document_schemas.py
Esquemas Pydantic v2 con TODOS los campos relevantes por tipo de documento,
basados en los formatos oficiales vigentes de cada documento mexicano.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


# ── Base compartida ───────────────────────────────────────────────────────────

class CamposBase(BaseModel):
    """Campos presentes en todos los documentos soportados."""
    nombre_completo:  Optional[str] = None   # nombre(s) + apellidos ensamblados
    primer_apellido:  Optional[str] = None
    segundo_apellido: Optional[str] = None
    nombres:          Optional[str] = None   # solo el/los nombre(s) de pila
    curp:             Optional[str] = Field(
        None,
        pattern=r"^[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$",
        description="Clave Única de Registro de Población (18 caracteres)"
    )
    fecha_nacimiento: Optional[str] = None   # dd/mm/yyyy
    sexo:             Optional[str] = None   # H / M


# ── INE / Credencial para Votar ───────────────────────────────────────────────
# Fuente: modelo vigente desde 2016 (imagen de referencia INE)
# Campos: nombre (3 líneas), domicilio (3 líneas), clave elector,
#         CURP, año registro, estado, municipio, sección,
#         localidad, emisión, vigencia, fecha nac, sexo

class CamposINE(CamposBase):
    # Identidad
    clave_elector:    Optional[str] = Field(None, description="18 caracteres alfanuméricos")

    # Domicilio (puede venir en hasta 3 líneas de Docling)
    domicilio_calle:  Optional[str] = None   # ej. "C PITAGORAS 1253 INT. 4"
    domicilio_colonia: Optional[str] = None  # ej. "COL. MORELOS 04800"
    domicilio_municipio_ciudad: Optional[str] = None  # ej. "CUAJIMALPA DE MORELOS, D.F."
    domicilio:        Optional[str] = None   # domicilio completo concatenado

    # Datos registrales
    estado:           Optional[str] = None   # código 2 dígitos, ej. "09"
    municipio:        Optional[str] = None   # código 3 dígitos, ej. "014"
    localidad:        Optional[str] = None   # código 4 dígitos, ej. "0001"
    seccion:          Optional[str] = None   # 4 dígitos, ej. "0747"
    año_registro:     Optional[str] = None   # ej. "2008 02"

    # Vigencia
    año_emision:      Optional[str] = None   # año 4 dígitos, ej. "2014"
    año_vigencia:     Optional[str] = None   # año 4 dígitos, ej. "2024"


# ── Acta de Nacimiento ────────────────────────────────────────────────────────
# Fuente: Formato Único Nacional desde 2015 (SEGOB/RENAPO)
# Campos: folio impresión, identificador electrónico, CURP,
#         cert. nacimiento, entidad/municipio registro, fecha registro,
#         oficialía, libro, número acta, nombre(s)/apellidos,
#         sexo, fecha/lugar nac, datos padre, datos madre

class CamposActaNacimiento(CamposBase):
    # Identificadores del acta
    folio_impresion:           Optional[str] = None  # "00000000" campo A
    identificador_electronico: Optional[str] = None  # campo C, ej. "04002000120160002965"
    numero_certificado_nac:    Optional[str] = None  # campo 02
    numero_acta:               Optional[str] = None  # campo 04.5 Número de Acta

    # Registro
    entidad_registro:          Optional[str] = None  # campo 03 Entidad de Registro
    municipio_registro:        Optional[str] = None  # campo 04 Municipio de Registro
    fecha_registro:            Optional[str] = None  # campo 04.2 Fecha de Registro
    oficialia:                 Optional[str] = None  # campo 04.1 Oficialía
    libro:                     Optional[str] = None  # campo 04.3 Libro

    # Datos del registrado
    lugar_nacimiento:          Optional[str] = None  # campo 07.6

    # Datos de filiación — padre (campos 08.x)
    nombre_padre:              Optional[str] = None
    primer_apellido_padre:     Optional[str] = None
    segundo_apellido_padre:    Optional[str] = None
    nacionalidad_padre:        Optional[str] = None
    curp_padre:                Optional[str] = None

    # Datos de filiación — madre (campos 08.6–08.10)
    nombre_madre:              Optional[str] = None
    primer_apellido_madre:     Optional[str] = None
    segundo_apellido_madre:    Optional[str] = None
    nacionalidad_madre:        Optional[str] = None
    curp_madre:                Optional[str] = None


# ── Pasaporte Mexicano ────────────────────────────────────────────────────────
# Fuente: modelo tipo "E" (vigente desde 2021)
# Campos: número pasaporte, tipo, país expedición, apellidos,
#         nombres, nacionalidad, CURP, fecha nac, sexo,
#         lugar nac, fecha expedición, fecha caducidad, MRZ

class CamposPasaporte(CamposBase):
    # Identificación
    numero_pasaporte:   Optional[str] = Field(
        None, description="Letra + 8 dígitos, ej. E1234567"
    )
    tipo_pasaporte:     Optional[str] = None   # "P"
    pais_expedicion:    Optional[str] = None   # "MEX"

    # Persona
    nacionalidad:       Optional[str] = None   # "MEXICANA"
    lugar_nacimiento:   Optional[str] = None   # ej. "IRAPUATO, GTO., MEX"

    # Vigencia
    fecha_expedicion:   Optional[str] = None   # dd mm yyyy → normalizado dd/mm/yyyy
    fecha_caducidad:    Optional[str] = None   # fecha de vencimiento

    # MRZ (Machine Readable Zone) — 2 líneas de 44 caracteres
    mrz_linea1:         Optional[str] = None   # ej. "P<MEXNUNES<DE<LOS<MONTEROS<<..."
    mrz_linea2:         Optional[str] = None   # ej. "E087469774MEX8010166F1810161..."

    # Observaciones (campo adicional en pasaportes consulares)
    observaciones:      Optional[str] = None


# ── Constancia de CURP ────────────────────────────────────────────────────────
# Fuente: nueva constancia SEGOB/RENAPO (formato 2019)
# Campos: clave CURP, nombre completo, fecha inscripción,
#         folio, entidad registro, CURPs asociadas por corrección

class CamposCURP(CamposBase):
    # Registro
    fecha_inscripcion:         Optional[str] = None  # ej. "14/05/2001"
    folio:                     Optional[str] = None  # ej. "80496894"
    entidad_registro:          Optional[str] = None  # ej. "HIDALGO"

    # CURPs asociadas por corrección (puede haber varias)
    curps_asociadas:           Optional[str] = None  # separadas por coma si hay varias

    # Código de verificación (código de barras)
    codigo_verificacion:       Optional[str] = None  # ej. "104002000120080033670"

    # Fecha de impresión de la constancia ("Ciudad de México, a 12 de mayo de 2022").
    # Necesaria para la regla de negocio RN-06 (antigüedad máxima de 3 meses).
    fecha_emision:             Optional[str] = None  # dd/mm/yyyy


# ── Mapa tipo → schema ────────────────────────────────────────────────────────

from domain.entities.documento import TipoDocumento

SCHEMA_MAP: dict[TipoDocumento, type[CamposBase]] = {
    TipoDocumento.INE:             CamposINE,
    TipoDocumento.ACTA_NACIMIENTO: CamposActaNacimiento,
    TipoDocumento.PASAPORTE:       CamposPasaporte,
    TipoDocumento.CURP:            CamposCURP,
}


def get_schema(tipo: TipoDocumento) -> type[CamposBase]:
    return SCHEMA_MAP.get(tipo, CamposBase)


def get_json_schema(tipo: TipoDocumento) -> dict:
    return get_schema(tipo).model_json_schema()


def get_field_names(tipo: TipoDocumento) -> list[str]:
    """Retorna la lista de nombres de campo del schema para usar en prompts."""
    return list(get_schema(tipo).model_fields.keys())
