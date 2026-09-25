"""
application/pipeline.py
Orquestador principal del pipeline de procesamiento.

Etapas:
  1. Validacion + correccion de orientacion
  2. OCR con Docling (con fallbacks multi-zona y pytesseract)
  3. Extraccion hibrida: regex -> LLM enriquecido -> fusion
  4. VALIDACION ANTI-ALUCINACION: cada valor del LLM debe aparecer en el texto crudo
  5. Validacion Pydantic + envelope final
"""
from __future__ import annotations
import logging
import re
import tempfile
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from application.validator import validar_campos
from config.settings import (
    ANTI_HALLUCINATION,
    EXTRACTION_STRATEGY,
    JSON_RESULTS_DIR,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    LLM_NUM_CTX,
    LLM_NUM_PREDICT,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
    OLLAMA_KEEP_ALIVE,
    PDF_MAX_PAGES,
    PDF_MAX_SIZE_MB,
    PIPELINE_VERSION,
    RAW_TEXT_DIR,
)
from domain.entities.documento import (
    DocumentoNoSoportadoError,
    DocumentoIdentidad,
    ResultadoProcesamiento,
    TipoDocumento,
)
from infrastructure.llm.ollama_qwen_adapter import OllamaQwenAdapter
from infrastructure.ocr.docling_adapter import DoclingAdapter
from infrastructure.ocr.pdf_validator import PdfValidationError, corregir_orientacion, validar_pdf
from infrastructure.regex.correccion_ocr import aparece_con_tolerancia, curp_valida, pistas_curp
from infrastructure.regex.regex_heuristic_adapter import RegexHeuristicAdapter
from infrastructure.storage.file_adapters import JsonFileAdapter, MarkdownFileAdapter

logger = logging.getLogger(__name__)


# Campos que NO pueden alucinarse: deben aparecer literalmente en el texto crudo
# Si el LLM da un valor que no esta en el texto -> se reemplaza por null
_CAMPOS_PROTEGIDOS_CONTRA_ALUCINACION = {
    # INE
    "curp", "clave_elector", "numero_folio", "estado", "municipio",
    "seccion", "localidad", "año_registro", "año_emision", "año_vigencia",
    # Pasaporte
    "numero_pasaporte", "fecha_expedicion", "fecha_caducidad",
    # Acta
    "folio_impresion", "identificador_electronico", "numero_certificado_nac",
    "numero_acta", "fecha_registro", "oficialia", "libro",
    "curp_padre", "curp_madre",
    # CURP
    "folio", "codigo_verificacion",
}

# Campos que el regex obtiene de forma determinista a partir de un patron
# textual ("a 12 de mayo de 2022" -> 12/05/2022). Si el regex los encontro,
# su valor prevalece sobre el del LLM.
_CAMPOS_PRIORIDAD_REGEX = {"fecha_emision"}


def _normalizar_para_comparacion(texto: str) -> str:
    """Normaliza texto para comparar valor vs texto crudo (case/acentos/espacios/fechas).
    Elimina separadores para que '03/11/2011' y '03 11 2011' sean equivalentes.
    """
    if not texto:
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\s\-\.\,\/\|]+", "", s)
    return s.upper()


def _valor_aparece_en_texto(valor: Any, texto_crudo: str, campo: str = "") -> bool:
    """
    True si el valor aparece (normalizado) en el texto crudo.
    Permite tolerancia a separadores, acentos y mayusculas.
    Para fechas (fecha_expedicion, fecha_caducidad), también acepta
    que solo dd/mm aparezca en el texto (cuando el OCR truncó el año).
    """
    if valor is None or not str(valor).strip():
        return True  # null no es alucinacion
    valor_norm = _normalizar_para_comparacion(valor)
    texto_norm = _normalizar_para_comparacion(texto_crudo)
    if valor_norm in texto_norm:
        return True

    # Tolerancia especial para fechas: si el OCR truncó el año,
    # verificar que al menos dd/mm aparezca en el texto
    if campo in ("fecha_expedicion", "fecha_caducidad", "fecha_registro"):
        partes = re.split(r"[/\-\s]", str(valor).strip())
        if len(partes) >= 2:
            dd_mm = _normalizar_para_comparacion(f"{partes[0]}/{partes[1]}")
            if dd_mm and dd_mm in texto_norm:
                return True

    return False


class DocumentPipeline:
    """
    Pipeline principal. Instanciar una vez y reutilizar.
    """

    def __init__(self):
        self._ocr          = DoclingAdapter()
        self._llm          = OllamaQwenAdapter(
            host=OLLAMA_HOST,
            model=OLLAMA_MODEL,
            timeout=LLM_TIMEOUT,
            temperature=LLM_TEMPERATURE,
            keep_alive=OLLAMA_KEEP_ALIVE,
            num_predict=LLM_NUM_PREDICT,
            num_ctx=LLM_NUM_CTX,
        )
        self._regex        = RegexHeuristicAdapter()
        self._md_storage   = MarkdownFileAdapter(RAW_TEXT_DIR)
        self._json_storage = JsonFileAdapter(JSON_RESULTS_DIR)

        logger.info("pipeline_inicializado", extra={
            "ocr_disponible":         self._ocr.disponible,
            "llm_disponible":         self._llm.disponible,
            "modelo":                 OLLAMA_MODEL,
            "estrategia":             EXTRACTION_STRATEGY,
            "anti_hallucination":     ANTI_HALLUCINATION,
            "version":                PIPELINE_VERSION,
        })

    # ── Punto de entrada publico ──────────────────────────────────────────────

    def process(
        self,
        ruta_pdf: str | Path,
        user_id: str,
        doc_id: Optional[str] = None,
    ) -> dict:
        inicio   = time.monotonic()
        doc_id   = doc_id or str(uuid.uuid4())
        ruta_pdf = Path(ruta_pdf)

        logger.info("pipeline_inicio", extra={
            "doc_id": doc_id, "user_id": user_id, "archivo": ruta_pdf.name
        })

        # Etapa 1: Validacion + correccion de orientacion
        hash_sha256, num_paginas = self._etapa_validacion(ruta_pdf, doc_id)
        ruta_procesada = self._corregir_orientacion_si_necesario(ruta_pdf, doc_id)
        try:
            return self._procesar(ruta_procesada, user_id, doc_id, hash_sha256,
                                  num_paginas, inicio)
        finally:
            # La copia corregida vive en /tmp: no dejar PDFs con datos personales
            if ruta_procesada != ruta_pdf:
                try:
                    ruta_procesada.unlink(missing_ok=True)
                except OSError:
                    pass

    def _procesar(self, ruta_procesada: Path, user_id: str, doc_id: str,
                  hash_sha256: str, num_paginas: int, inicio: float) -> dict:

        documento = DocumentoIdentidad(
            ruta_pdf=ruta_procesada,
            user_id=user_id,
            doc_id=doc_id,
            hash_sha256=hash_sha256,
            num_paginas=num_paginas,
        )

        # Etapa 2: OCR
        t_ocr = time.monotonic()
        texto_extraido = self._etapa_ocr(documento)
        tiempos = {"ocr_ms": round((time.monotonic() - t_ocr) * 1000)}

        if texto_extraido.tipo_detectado == TipoDocumento.DESCONOCIDO:
            raise DocumentoNoSoportadoError()

        ruta_md = self._md_storage.guardar_texto(texto_extraido)
        texto_extraido.ruta_md = ruta_md

        # Etapa 3: Extraccion de campos
        t_ext = time.monotonic()
        campos, adaptador_usado = self._etapa_extraccion(
            texto=texto_extraido.texto_crudo,
            tipo=texto_extraido.tipo_detectado,
        )
        tiempos["extraccion_ms"] = round((time.monotonic() - t_ext) * 1000)
        # Campos que el regex corrigió por validación cruzada (dígito verificador
        # de la CURP, consistencia nombre/fecha/sexo...)
        corregidos_ocr = [c for c in campos.pop("_corregidos", []) or [] if campos.get(c)]

        # Etapa 4: ANTI-ALUCINACION
        # Si un campo protegido tiene un valor que NO esta en el texto crudo,
        # lo descartamos. Esto evita que el LLM invente CURPs, claves de elector,
        # numeros de seccion, etc.
        campos_filtrados = []
        if ANTI_HALLUCINATION:
            campos, campos_filtrados = self._filtrar_alucinaciones(
                campos, texto_extraido.texto_crudo, set(corregidos_ocr)
            )
            if campos_filtrados:
                logger.warning("alucinaciones_detectadas", extra={
                    "doc_id": doc_id, "campos_descartados": campos_filtrados,
                })

        # Etapa 5: Validacion Pydantic + envelope
        campos_validados, errores_validacion = validar_campos(
            campos, texto_extraido.tipo_detectado
        )
        tiempo_ms = round((time.monotonic() - inicio) * 1000)

        resultado = ResultadoProcesamiento(
            doc_id=doc_id,
            user_id=user_id,
            tipo_documento=texto_extraido.tipo_detectado,
            datos=campos_validados,
            ruta_texto_crudo=str(ruta_md),
            adaptador_usado=adaptador_usado,
            pipeline_version=PIPELINE_VERSION,
            timestamp_procesamiento=datetime.utcnow().isoformat(),
            hash_sha256=hash_sha256,
        )

        resultado_dict = resultado.to_dict()
        meta = resultado_dict["metadatos"]
        meta["tiempo_procesamiento_ms"] = tiempo_ms
        meta["tiempos_etapas_ms"] = tiempos  # OCR vs. extracción (regex + LLM)
        meta["errores_validacion"] = errores_validacion
        meta["num_paginas"] = num_paginas
        meta["modelo_llm"] = OLLAMA_MODEL if adaptador_usado.startswith("ollama") else None
        meta["confianza"] = self._calcular_confianza(
            campos_validados, texto_extraido.tipo_detectado,
            len(errores_validacion), len(campos_filtrados),
        )
        meta["campos_detectados"] = sum(1 for v in campos_validados.values() if v not in (None, ""))
        meta["plataforma"] = {
            "sistema": self._ocr.plataforma.get("sistema"),
            "en_docker": self._ocr.plataforma.get("en_docker"),
            "motor_ocr_docling": self._ocr.plataforma.get("motor_ocr_docling"),
            "docling": self._ocr.docling_disponible,
        }
        if campos_filtrados:
            resultado_dict["metadatos"]["campos_descartados_por_alucinacion"] = campos_filtrados
        corregidos_finales = [c for c in corregidos_ocr if campos_validados.get(c)]
        if corregidos_finales:
            resultado_dict["metadatos"]["campos_corregidos_ocr"] = corregidos_finales

        self._json_storage.guardar_resultado(resultado_dict, doc_id, user_id)

        logger.info("pipeline_tiempos", extra={"doc_id": doc_id, **tiempos, "total_ms": tiempo_ms})
        logger.info("pipeline_completado", extra={
            "doc_id":              doc_id,
            "tipo":                texto_extraido.tipo_detectado.value,
            "adaptador":           adaptador_usado,
            "tiempo_ms":           tiempo_ms,
            "errores_validacion":  len(errores_validacion),
            "campos_alucinados":   len(campos_filtrados),
        })

        return resultado_dict

    # ── Confianza ─────────────────────────────────────────────────────────────

    # Campos clave por tipo: sirven para estimar que tan completo es el resultado
    _CAMPOS_CLAVE = {
        TipoDocumento.INE: ["primer_apellido", "nombres", "curp", "clave_elector",
                            "fecha_nacimiento", "sexo", "domicilio", "año_vigencia"],
        TipoDocumento.CURP: ["curp", "nombre_completo", "fecha_inscripcion", "folio",
                             "entidad_registro", "fecha_emision"],
        TipoDocumento.ACTA_NACIMIENTO: ["curp", "nombres", "primer_apellido", "fecha_nacimiento",
                                        "identificador_electronico", "numero_acta",
                                        "entidad_registro", "fecha_registro"],
        TipoDocumento.PASAPORTE: ["numero_pasaporte", "primer_apellido", "nombres", "curp",
                                  "fecha_nacimiento", "fecha_expedicion", "fecha_caducidad"],
    }

    def _calcular_confianza(self, campos: dict, tipo: TipoDocumento,
                            n_errores: int, n_alucinados: int) -> float:
        """
        Heuristica 0..1: proporcion de campos clave encontrados, penalizada por
        errores de validacion y por valores descartados como alucinacion.
        """
        clave = self._CAMPOS_CLAVE.get(tipo, [])
        if not clave:
            return 0.0
        encontrados = sum(1 for c in clave if campos.get(c) not in (None, ""))
        base = encontrados / len(clave)
        penalizacion = 0.05 * n_errores + 0.05 * n_alucinados
        return round(max(0.0, min(1.0, base - penalizacion)), 2)

    # ── Anti-alucinacion ──────────────────────────────────────────────────────

    def _filtrar_alucinaciones(
        self, campos: dict, texto_crudo: str, corregidos: Optional[set] = None
    ) -> tuple[dict, list[str]]:
        """
        Para cada campo PROTEGIDO con valor no nulo, verifica que aparezca
        literalmente en el texto crudo. Si no aparece -> se reemplaza por null.
        Retorna (campos_filtrados, lista_de_campos_descartados).
        """
        descartados = []
        resultado = dict(campos)

        for campo, valor in campos.items():
            if campo not in _CAMPOS_PROTEGIDOS_CONTRA_ALUCINACION:
                continue
            if valor is None or not str(valor).strip():
                continue
            if campo in (corregidos or set()):
                # Valor corregido por validación cruzada: debe parecerse a lo que
                # el OCR leyó (tolerando confusiones O/0, S/5, B/8...), no ser inventado
                if aparece_con_tolerancia(str(valor), texto_crudo):
                    continue
            if not _valor_aparece_en_texto(valor, texto_crudo, campo):
                resultado[campo] = None
                descartados.append(f"{campo}={valor}")

        return resultado, descartados

    # ── Etapas internas ───────────────────────────────────────────────────────

    def _etapa_validacion(self, ruta: Path, doc_id: str):
        try:
            return validar_pdf(ruta, max_mb=PDF_MAX_SIZE_MB, max_paginas=PDF_MAX_PAGES)
        except PdfValidationError as e:
            logger.error("pipeline_validacion_fallida", extra={
                "doc_id": doc_id, "error": str(e)
            })
            raise

    def _corregir_orientacion_si_necesario(self, ruta_original: Path, doc_id: str) -> Path:
        ruta_corregida = Path(tempfile.gettempdir()) / f"{doc_id}_corregido.pdf"
        try:
            corregida = corregir_orientacion(ruta_original, ruta_corregida)
            if corregida:
                logger.info("orientacion_corregida_silenciosamente",
                            extra={"doc_id": doc_id})
            return ruta_corregida
        except Exception as e:
            logger.warning("orientacion_correccion_fallida",
                           extra={"doc_id": doc_id, "error": str(e)})
            return ruta_original

    def _etapa_ocr(self, documento: DocumentoIdentidad):
        if not self._ocr.disponible:
            raise RuntimeError(
                "No hay motor OCR disponible: instala Docling (pip install docling) "
                "y/o Tesseract."
            )
        return self._ocr.extraer_texto(documento)

    def _etapa_extraccion(self, texto: str, tipo: TipoDocumento) -> tuple[dict, str]:
        if EXTRACTION_STRATEGY == "regex_first":
            return self._extraer_con_regex(texto, tipo)
        return self._extraer_hibrido(texto, tipo)

    # ── Estrategia hibrida ────────────────────────────────────────────────────

    def _extraer_hibrido(self, texto: str, tipo: TipoDocumento) -> tuple[dict, str]:
        """
        1. Regex extrae lo que puede (determinista, siempre funciona).
        2. Se enriquece el texto con esos campos como anotaciones.
        3. LLM completa y corrige sobre el texto enriquecido.
        4. Fusion: LLM tiene prioridad PERO se rechazan valores claramente rotos.
        """
        campos_regex, _ = self._extraer_con_regex(texto, tipo)

        if not self._llm.disponible:
            logger.warning("llm_no_disponible_usando_regex")
            return campos_regex, self._regex.nombre_adaptador

        if not self._llm.verificar_conexion():
            logger.warning("ollama_no_conectado_usando_regex")
            return campos_regex, self._regex.nombre_adaptador

        texto_enriquecido = self._enriquecer_texto(texto, campos_regex)

        try:
            campos_llm = self._llm.extraer_campos(texto_enriquecido, tipo)

            campos_final = dict(campos_regex)
            for k, v in campos_llm.items():
                if v is None or not str(v).strip():
                    continue
                # Si el regex tiene un valor mejor para nombres, no lo sobreescribas
                # con basura del LLM
                if k in _CAMPOS_PRIORIDAD_REGEX and campos_regex.get(k):
                    continue
                if k in (campos_regex.get("_corregidos") or []) and campos_regex.get(k):
                    continue  # corregido y validado por el regex: más confiable que el LLM
                if k == "curp" and curp_valida(campos_regex.get("curp")) and not curp_valida(str(v)):
                    continue  # la CURP del regex pasa el dígito verificador; la del LLM no
                if not self._consistente_con_curp(k, v, campos_regex):
                    logger.info("llm_valor_inconsistente_con_curp", extra={"campo": k})
                    continue
                if self._llm_valor_es_basura(k, v):
                    logger.debug("llm_valor_rechazado_basura", extra={
                        "campo": k, "valor": str(v)[:60]
                    })
                    continue
                campos_final[k] = v

            logger.info("llm_hibrido_ok", extra={
                "regex_campos": sum(1 for v in campos_regex.values() if v),
                "llm_campos":   sum(1 for v in campos_llm.values() if v),
                "final_campos": sum(1 for v in campos_final.values() if v),
            })
            return campos_final, self._llm.nombre_adaptador

        except Exception as e:
            logger.warning("llm_fallo_usando_regex", extra={"error": str(e)})
            return campos_regex, self._regex.nombre_adaptador

    @staticmethod
    def _consistente_con_curp(campo: str, valor: Any, campos_regex: dict) -> bool:
        """
        Si el regex obtuvo una CURP con dígito verificador válido, sus iniciales
        y consonantes internas CONFIRMAN apellidos y nombre. Un valor del LLM que
        las contradiga se descarta y se conserva el del regex.
        """
        curp = campos_regex.get("curp")
        if campo not in ("primer_apellido", "segundo_apellido", "nombres") or not curp_valida(curp):
            return True
        if not campos_regex.get(campo):
            return True
        args = {
            "nombres": campos_regex.get("nombres"),
            "ap1": campos_regex.get("primer_apellido"),
            "ap2": campos_regex.get("segundo_apellido"),
        }
        clave = {"primer_apellido": "ap1", "segundo_apellido": "ap2", "nombres": "nombres"}[campo]
        args[clave] = str(valor)
        posiciones = {"ap1": (0, 13), "ap2": (2, 14), "nombres": (3, 15)}[clave]
        pistas = pistas_curp(args["nombres"], args["ap1"], args["ap2"])
        return all(pistas.get(p) in (None, curp[p]) for p in posiciones)

    @staticmethod
    def _llm_valor_es_basura(campo: str, valor: Any) -> bool:
        """
        Detecta si el valor del LLM es claramente basura/etiqueta capturada por error.
        Estos casos suelen pasar cuando el LLM lee mal una tabla y captura la etiqueta
        en vez del valor (ej. 'Nombre(s): Segundo Apellido: MUJER').
        """
        if valor is None:
            return False
        s = str(valor).strip()
        if not s:
            return True
        # Contiene palabras-etiqueta que NUNCA deberian ser un valor
        ETIQUETAS_BASURA = [
            "Nombre(s)", "Primer Apellido", "Segundo Apellido",
            "Sexo:", "Fecha de Nacimiento", "Lugar de Nacimiento",
            "Nacionalidad:", "CURP:", "MUJER", "HOMBRE",
        ]
        # Si el valor TIENE etiquetas + datos mezclados (no es solo el dato), es basura
        for etiqueta in ETIQUETAS_BASURA:
            if etiqueta in s and len(s) < len(etiqueta) + 30:
                # tiene etiqueta y poco contenido extra -> claramente captura mala
                return True
        # Empieza con caracteres raros tipo "(s)" o ":"
        if re.match(r"^[\(\):,\.\-]", s):
            return True
        return False

    def _enriquecer_texto(self, texto: str, campos_regex: dict) -> str:
        """Anade al texto las anotaciones del regex como contexto para el LLM."""
        conocidos = {k: v for k, v in campos_regex.items() if v is not None and not k.startswith("_")}
        if not conocidos:
            return texto

        lineas = [
            "",
            "=== CAMPOS YA IDENTIFICADOS POR ANALISIS PREVIO ===",
            "(Usa estos valores como referencia)",
        ]
        for k, v in conocidos.items():
            lineas.append(f"{k}: {v}")

        return texto + "\n".join(lineas)

    def _extraer_con_regex(self, texto: str, tipo: TipoDocumento) -> tuple[dict, str]:
        campos = self._regex.extraer_campos(texto, tipo)
        # El regex tambien puede capturar etiquetas como valores cuando el OCR
        # desordena una tabla (p.ej. "(s): Primer Apellido:"): se limpian igual
        # que los valores del LLM.
        campos = {k: (None if self._llm_valor_es_basura(k, v) else v) for k, v in campos.items()}
        return campos, self._regex.nombre_adaptador

    # ── Utilidades publicas ───────────────────────────────────────────────────

    def health_check(self) -> dict:
        llm_ok = self._llm.disponible and self._llm.verificar_conexion(usar_cache=False)
        ocr_ok = self._ocr.disponible
        return {
            # "degradado": funciona, pero solo con regex (sin Ollama)
            "status":                "ok" if (ocr_ok and llm_ok) else ("degradado" if ocr_ok else "error"),
            "pipeline_version":      PIPELINE_VERSION,
            "ocr_disponible":        ocr_ok,
            "docling_disponible":    self._ocr.docling_disponible,
            "tesseract_disponible":  self._ocr.tesseract_disponible,
            "llm_disponible":        llm_ok,
            "ollama_host":           OLLAMA_HOST,
            "modelo_llm":            OLLAMA_MODEL,
            "estrategia_extraccion": EXTRACTION_STRATEGY,
            "anti_hallucination":    ANTI_HALLUCINATION,
            "plataforma":            self._ocr.plataforma,
        }

    def recuperar_texto_crudo(self, doc_id: str) -> Optional[str]:
        return self._md_storage.recuperar_texto(doc_id)

    def obtener_resultado(self, doc_id: str) -> Optional[dict]:
        return self._json_storage.obtener_resultado(doc_id)

    def listar_documentos_usuario(self, user_id: str) -> list[dict]:
        return self._json_storage.listar_documentos_usuario(user_id)
