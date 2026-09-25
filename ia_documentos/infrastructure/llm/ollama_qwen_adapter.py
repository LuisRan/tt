"""
infrastructure/llm/ollama_qwen_adapter.py

Mejoras criticas para mayor precision:
- Prompts EXTREMADAMENTE estrictos contra alucinacion
- Sin placeholders ambiguos (XXXX, NNNN) en el prompt
- Reglas explicitas: "si no esta en el texto, usa null. NUNCA inventes."
- Post-validacion local (placeholders, duplicados, campos numericos)
- La validacion principal anti-alucinacion vive en pipeline.py donde
  se compara cada valor contra el texto crudo OCR
- Limpieza robusta de respuesta JSON (markdown, <think>, etc.)
"""
from __future__ import annotations
import json
import logging
import re
import time
from typing import Any

from domain.entities.documento import TipoDocumento
from domain.ports.interfaces import IFieldExtractorPort
from domain.schemas.document_schemas import get_field_names

logger = logging.getLogger(__name__)


# ── Sistema: instrucciones absolutas contra alucinacion ───────────────────────

_SISTEMA = """Eres un extractor de datos de documentos de identidad oficiales mexicanos.
Lees texto EXTRAIDO POR OCR y devuelves JSON con los campos pedidos.

REGLAS ABSOLUTAS QUE NUNCA DEBES VIOLAR:
1. Responde SOLO con un objeto JSON valido. Sin texto extra, sin markdown.
2. Si un campo no aparece LITERALMENTE en el texto, usa null. NUNCA inventes.
3. NUNCA copies placeholders genericos como "XXXX", "NNNN", "AAAA", "------".
4. NUNCA repitas el mismo valor en campos distintos.
5. NUNCA uses datos que recuerdes de tu entrenamiento. SOLO el texto dado.
6. Fechas siempre en formato dd/mm/yyyy.
7. Codigos numericos (estado, municipio, seccion, localidad) deben ser SOLO digitos.
8. Si tienes duda sobre un campo, usa null. Es preferible null que un valor incorrecto.

Si el texto OCR esta dañado o incompleto, devuelve null en los campos afectados."""


# ── Contextos especificos por tipo de documento ───────────────────────────────

_CONTEXTO_INE = """
DOCUMENTO: Credencial para Votar (INE) mexicana.

ESTRUCTURA TIPICA DEL OCR:
Tras la palabra "NOMBRE" aparecen 3 lineas en este orden EXACTO:
  linea 1 -> PRIMER APELLIDO (paterno)
  linea 2 -> SEGUNDO APELLIDO (materno)
  linea 3 -> NOMBRE(S) DE PILA

Tras la palabra "DOMICILIO" hay hasta 3 lineas:
  linea 1 -> calle y numero
  linea 2 -> colonia y codigo postal
  linea 3 -> municipio o delegacion + ciudad

Otros campos vienen como "ETIQUETA VALOR" en la misma linea:
- CLAVE DE ELECTOR: 18 caracteres (6 letras + 8 digitos + 1 letra + 3 digitos)
- CURP: 18 caracteres (4 letras + 6 digitos + H/M + 5 letras + 2 alfanumericos)
- ESTADO: 2 digitos (ej. 09)
- MUNICIPIO: 3 digitos (ej. 014)
- SECCION: 4 digitos (ej. 4373)
- LOCALIDAD: 4 digitos (ej. 0001)
- EMISION: 4 digitos del año
- VIGENCIA: 4 digitos del año
- SEXO: solo "H" o "M"

CRITICO: Si NO ves estos campos LITERALMENTE en el texto, usa null.
No inventes claves, codigos ni fechas.
"""

_CONTEXTO_PASAPORTE = """
DOCUMENTO: Pasaporte Mexicano.

EL OCR DE PASAPORTES SUELE VENIR DESORDENADO POR EL LAYOUT EN COLUMNAS.
Hay etiquetas en español, ingles y frances mezcladas. Ignora etiquetas
sin sentido como "Agorie Code" o "Sumame" - son ruido OCR.

CAMPOS A BUSCAR:
- "Pasaporte No." o "Passport No." -> letra + 8 digitos (ej. G08103919, E1234567)
- "Apellidos" o "Surname" -> linea siguiente con apellido(s)
- "Nombres" o "Given names" -> linea siguiente con el/los nombre(s)
- "Nacionalidad" -> generalmente "MEXICANA"
- "CURP" -> 18 caracteres (4 letras + 6 digitos + H/M + 5 letras + 2)
- "Fecha de nacimiento" -> formato "dd mm yyyy" o "dd/mm/yyyy"
- "Sexo" -> "M" (femenino) o "H" (masculino) - OJO: en pasaporte M=Mujer
  Convierte a tu output como: F (femenino) -> "M", H (hombre) -> "H"
- "Lugar de nacimiento" -> ciudad, estado, pais
- "Fecha de expedicion" -> formato "dd mm yyyy"
- "Fecha de caducidad" -> formato "dd mm yyyy"
- MRZ: las 2 ultimas lineas largas con caracter "<", la primera empieza "P<MEX"

Si no aparece, usa null. Las etiquetas ruidosas tipo "Agorie", "Sumame",
"Code de pega" NO son valores reales.
"""

_CONTEXTO_ACTA = """
DOCUMENTO: Acta de Nacimiento (Formato Unico Nacional 2015).

ATENCION: en el OCR, los VALORES suelen aparecer ANTES que las ETIQUETAS.
Ejemplo real:

    GABRIELA ROMANA
    MACIAS
    OCHOA
    Nombre(s):
    Primer Apellido:
    Segundo Apellido:
    MUJER
    8 DE AGOSTO DE 1972
    DISTRITO FEDERAL
    Sexo:
    Fecha de Nacimiento:
    Lugar de Nacimiento:

Esto significa:
    nombres = "GABRIELA ROMANA"
    primer_apellido = "MACIAS"
    segundo_apellido = "OCHOA"
    sexo = "M" (de MUJER)
    fecha_nacimiento = "08/08/1972"
    lugar_nacimiento = "DISTRITO FEDERAL"

REGLA ABSOLUTA: NUNCA pongas "Nombre(s)", "Primer Apellido", "Segundo Apellido",
"Sexo:", "MUJER", "HOMBRE" como VALORES. Esos son ETIQUETAS, no datos.

DATOS DE FILIACION (padre Y madre, NO los confundas):
- Aparecen en 2 bloques de 5 lineas cada uno, en este orden:
  Nombre(s), Primer Apellido, Segundo Apellido, Nacionalidad, CURP
- El primer bloque es UNO, el segundo bloque es OTRO. Determina padre/madre por:
  * El CURP en posicion 11 indica genero: H=hombre (padre), M=mujer (madre)
  * Si no hay CURPs, deja los 2 en null (no adivines)
- NUNCA pongas el mismo valor en padre y madre.

DATOS REGISTRALES (en tabla):
- "Identificador Electronico" -> codigo numerico largo
- "Folio de Impresion" -> 8 digitos
- "Numero de Acta" -> codigo
- "Entidad de Registro" -> nombre del estado
- "Municipio de Registro" -> nombre del municipio
- "Fecha de Registro" -> fecha
- "Oficialia" / "Libro" -> codigos cortos

CONVERSION DE FECHAS EN TEXTO:
"8 DE AGOSTO DE 1972" -> "08/08/1972"
Meses: ENERO=01, FEBRERO=02, MARZO=03, ABRIL=04, MAYO=05, JUNIO=06,
       JULIO=07, AGOSTO=08, SEPTIEMBRE=09, OCTUBRE=10, NOVIEMBRE=11, DICIEMBRE=12

CONVERSION DE SEXO:
MUJER/FEMENINO/F -> "M"
HOMBRE/MASCULINO/H -> "H"

Si "----" o "---" aparece, eso significa "sin dato": usa null, no copies los guiones.
"""

_CONTEXTO_CURP = """
DOCUMENTO: Constancia de CURP (SEGOB/RENAPO).

EL TEXTO COMBINA: la zona util superior (con OCR forzado) + el cuerpo del
documento (texto plano de la carta).

CAMPOS A BUSCAR EN LA ZONA SUPERIOR:
- "Clave:" seguido de la CURP en linea siguiente (18 caracteres)
- "Nombre:" seguido del nombre completo en linea siguiente
- "Fecha de inscripcion" -> dd/mm/yyyy
- "Folio" -> numerico, 7-12 digitos
- "Entidad de registro" -> nombre del estado mexicano
- "Codigo de Verificacion" -> codigo numerico largo

EL NOMBRE TAMBIEN PUEDE APARECER EN MAYUSCULAS EN LA CARTA inmediatamente
despues del header. Ejemplo: "JOSE TADEO TELLEZ GARCIA" como linea separada.
Si lo ves asi, ese es el nombre_completo.

IMPORTANTE: el cuerpo de la carta (sobre el derecho a la identidad, etc.)
NO contiene datos del titular. NO extraigas nada de ahi.

Si un campo no aparece, usa null.
"""

_CONTEXTO_MAP = {
    TipoDocumento.INE:             _CONTEXTO_INE,
    TipoDocumento.PASAPORTE:       _CONTEXTO_PASAPORTE,
    TipoDocumento.ACTA_NACIMIENTO: _CONTEXTO_ACTA,
    TipoDocumento.CURP:            _CONTEXTO_CURP,
}

_USUARIO = """{contexto}

Campos a extraer (devuelve TODOS, usa null si no aparece):
{campos_lista}

Texto del documento (extraido por OCR):
---
{texto}
---

Responde SOLO con el JSON. Recuerda: si un valor NO aparece literalmente, usa null."""


# ── Filtros de basura ─────────────────────────────────────────────────────────

_PATRONES_BASURA = [
    re.compile(r"^X{2,}$"),
    re.compile(r"^N{2,}$"),
    re.compile(r"^A{4,}$"),
    re.compile(r"^Y{4,}$"),
    re.compile(r"^[XN]+$", re.I),
    re.compile(r"^\?+$"),
    re.compile(r"^-+$"),
    re.compile(r"^_+$"),
    re.compile(r"^null$", re.I),
    re.compile(r"^none$", re.I),
    re.compile(r"^n/?a$", re.I),
    re.compile(r"^sin\s+dato$", re.I),
    re.compile(r"^vacio$", re.I),
]

_CAMPOS_SOLO_DIGITOS = {
    "estado", "municipio", "seccion", "localidad",
    "año_emision", "año_vigencia",
}


def _es_basura(valor: Any) -> bool:
    if valor is None:
        return True
    s = str(valor).strip()
    if not s:
        return True
    for patron in _PATRONES_BASURA:
        if patron.match(s):
            return True
    return False


def _validar_campo_numerico(campo: str, valor: Any) -> Any:
    if campo not in _CAMPOS_SOLO_DIGITOS or valor is None:
        return valor
    s = str(valor).strip()
    if not re.fullmatch(r"\d+", s):
        return None
    return s


def _filtrar_campos(campos: dict) -> dict:
    """
    Limpia el dict del LLM:
    - Reemplaza placeholders por None
    - Valida campos numericos
    - Detecta valores duplicados sospechosos
    """
    limpios = {}
    for k, v in campos.items():
        if _es_basura(v):
            limpios[k] = None
        else:
            limpios[k] = v

    for k in list(limpios.keys()):
        limpios[k] = _validar_campo_numerico(k, limpios[k])

    # Si un mismo valor aparece en > 2 campos, mantener solo el primero
    valores_count: dict[str, list[str]] = {}
    for k, v in limpios.items():
        if v is None or not str(v).strip():
            continue
        sv = str(v).strip()
        valores_count.setdefault(sv, []).append(k)

    for valor, campos_con_valor in valores_count.items():
        if len(campos_con_valor) > 2:
            for c in campos_con_valor[1:]:
                logger.debug("filtrando_duplicado", extra={
                    "campo": c, "valor": valor[:40]
                })
                limpios[c] = None

    return limpios


def _campos_lista(tipo: TipoDocumento) -> str:
    return "\n".join(f"- {c}" for c in get_field_names(tipo))


def _limpiar_json(texto: str) -> str:
    """Quita markdown fences, bloques <think>, y extrae el primer JSON."""
    texto = re.sub(r"```(?:json)?", "", texto)
    texto = re.sub(r"```", "", texto)
    texto = re.sub(r"<think>[\s\S]*?</think>", "", texto, flags=re.I)
    m = re.search(r"\{[\s\S]*\}", texto)
    return m.group(0) if m else texto.strip()


# ── Adaptador ─────────────────────────────────────────────────────────────────

class OllamaQwenAdapter(IFieldExtractorPort):
    """Extractor LLM con Ollama. Compatible con qwen3:1.7b/4b, qwen2.5:7b, gemma3."""

    def __init__(self, host: str, model: str, timeout: int = 120, temperature: float = 0,
                 keep_alive: str = "30m", num_predict: int = 1024, num_ctx: int = 8192):
        self._keep_alive  = keep_alive
        self._num_predict = num_predict
        self._num_ctx     = num_ctx
        self._host        = host
        self._model       = model
        self._timeout     = timeout
        self._temperature = temperature

        self._cache_conexion: tuple[float, bool] | None = None

        try:
            import ollama
            # timeout explicito: sin el, una peticion a un host caido se queda colgada
            self._client       = ollama.Client(host=host, timeout=timeout)
            self._client_check = ollama.Client(host=host, timeout=5)
            self._available    = True
        except ImportError:
            logger.warning("libreria ollama no instalada")
            self._available = False

    @property
    def disponible(self) -> bool:
        return self._available

    @property
    def nombre_adaptador(self) -> str:
        return f"ollama_{self._model.replace(':', '_')}"

    @property
    def modelo(self) -> str:
        return self._model

    @property
    def host(self) -> str:
        return self._host

    def verificar_conexion(self, usar_cache: bool = True) -> bool:
        """True si Ollama responde y el modelo esta descargado (cache de 30 s)."""
        if not self._available:
            return False
        ahora = time.monotonic()
        if usar_cache and self._cache_conexion and ahora - self._cache_conexion[0] < 30:
            return self._cache_conexion[1]
        try:
            modelos    = self._client_check.list()
            nombres    = [m.model for m in modelos.models]
            disponible = any(self._model == n or n.startswith(self._model) for n in nombres)
            if not disponible:
                logger.warning("modelo_no_descargado", extra={
                    "modelo": self._model, "disponibles": nombres,
                    "hint": f"ollama pull {self._model}",
                })
        except Exception as e:
            logger.error("ollama_no_disponible", extra={"error": str(e), "host": self._host})
            disponible = False
        self._cache_conexion = (ahora, disponible)
        return disponible

    def extraer_campos(self, texto: str, tipo_documento: TipoDocumento) -> dict:
        if not self._available:
            raise RuntimeError("Libreria ollama no instalada")

        contexto = _CONTEXTO_MAP.get(tipo_documento, "")
        prompt = _USUARIO.format(
            contexto=contexto,
            campos_lista=_campos_lista(tipo_documento),
            texto=texto[:8000],   # mas contexto para qwen2.5:7b
        )

        logger.info("llm_request", extra={
            "modelo": self._model, "tipo": tipo_documento.value
        })

        ultimo_error: Exception | None = None
        for intento in (1, 2):  # reintento automatico si el JSON sale invalido
            try:
                return self._llamar_llm(prompt, tipo_documento)
            except json.JSONDecodeError as e:
                ultimo_error = e
                logger.warning("llm_json_invalido_reintentando", extra={"intento": intento})
        raise ultimo_error  # type: ignore[misc]

    def _llamar_llm(self, prompt: str, tipo_documento: TipoDocumento) -> dict:
        contenido = ""
        try:
            response = self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SISTEMA},
                    {"role": "user",   "content": prompt},
                ],
                format="json",
                keep_alive=self._keep_alive,
                options={
                    "temperature": self._temperature,
                    "num_predict": self._num_predict,
                    "num_ctx": self._num_ctx,
                    "top_p": 0.1,        # mas determinista
                    "repeat_penalty": 1.1,
                },
            )

            contenido = response.message.content
            contenido_limpio = _limpiar_json(contenido)
            campos_brutos = json.loads(contenido_limpio)

            campos = _filtrar_campos(campos_brutos)

            no_nulos_brutos = sum(1 for v in campos_brutos.values() if v is not None)
            no_nulos_limpios = sum(1 for v in campos.values() if v is not None)

            logger.info("llm_response_ok", extra={
                "tipo": tipo_documento.value,
                "campos_no_nulos": no_nulos_limpios,
                "filtrados_basura": no_nulos_brutos - no_nulos_limpios,
            })
            return campos

        except json.JSONDecodeError as e:
            logger.error("llm_json_invalido", extra={
                "error": str(e),
                "respuesta_inicio": contenido[:300],
            })
            raise
        except Exception as e:
            logger.error("llm_error", extra={"error": str(e)})
            raise
