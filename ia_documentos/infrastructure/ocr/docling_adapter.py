"""
infrastructure/ocr/docling_adapter.py

Estrategia de OCR mejorada por tipo de documento:

1. Docling normal -> texto base
2. Si es CURP -> SIEMPRE forzar OCR pytesseract sobre la zona superior
                 (la zona util es la imagen embebida en el tercio superior;
                  el resto es la carta del Secretario, basura para extraer)
3. Si es Pasaporte -> Docling produce columnas mezcladas, intenta tambien
                      pytesseract para tener un texto alternativo
4. Si Docling extrajo < 500 chars -> multi-zona
5. Si despues de todo aun < 300 chars -> pytesseract de toda la pagina
6. Lazy loading: Docling solo se importa cuando se procesa el primer PDF
"""
from __future__ import annotations
import importlib.util
import logging
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Optional

from config.settings import OCR_DPI, OCR_MIN_CHARS_FALLBACK, OCR_MIN_CHARS_NORMAL
from domain.entities.documento import DocumentoIdentidad, TextoExtraido, TipoDocumento
from domain.ports.interfaces import IOcrPort
from infrastructure.platform_config import (
    crear_conversor_docling,
    detectar_plataforma,
    idiomas_pytesseract,
    renderizar_paginas,
)

logger = logging.getLogger(__name__)


# Patrones textuales para clasificar tipo de documento
_PATRONES_TIPO: dict[TipoDocumento, list[str]] = {
    TipoDocumento.INE: [
        r"CREDENCIAL PARA VOTAR",
        r"INSTITUTO NACIONAL ELECTORAL",
        r"CLAVE DE ELECTOR",
        r"A[ÑN]O DE REGISTRO",
        r"\bLOCALIDAD\s+\d{4}\b",
    ],
    TipoDocumento.PASAPORTE: [
        r"PASAPORTE",
        r"PASSPORT",
        r"SECRETAR[IÍ]A DE RELACIONES EXTERIORES",
    ],
    TipoDocumento.ACTA_NACIMIENTO: [
        r"ACTA DE NACIMIENTO",
        r"REGISTRO CIVIL",
    ],
    TipoDocumento.CURP: [
        r"CLAVE [ÚU]NICA DE REGISTRO DE POBLACI[ÓO]N",
        r"CONSTANCIA DE LA CLAVE",
        r"TRAMITE GRATUITO",
        r"TR[ÁA]MITE GRATUITO",
        r"TELCURP",
        r"CURP CERTIFICADA",
        r"DERECHO A LA IDENTIDAD",
        r"DATOS PERSONALES.*BASE DE DATOS NACIONAL",
        r"RENAPO",
    ],
}


class DoclingAdapter(IOcrPort):
    """OCR con estrategia hibrida adaptada por tipo de documento."""

    def __init__(self):
        # Lazy loading - solo verificar si esta instalado
        self._DocumentConverter = None
        self._converter = None
        self._docling_available = importlib.util.find_spec("docling") is not None
        if not self._docling_available:
            logger.warning("docling no instalado: se usara Tesseract como motor principal")
        self._plataforma = detectar_plataforma()

        self._pytesseract = None
        self._pytesseract_available = importlib.util.find_spec("pytesseract") is not None

        try:
            from PIL import Image
            self._Image = Image
            self._pil_available = True
        except ImportError:
            self._pil_available = False

    @property
    def disponible(self) -> bool:
        # El modulo puede operar si hay Docling o, como respaldo, Tesseract
        return self._docling_available or self.tesseract_disponible

    @property
    def docling_disponible(self) -> bool:
        return self._docling_available

    @property
    def tesseract_disponible(self) -> bool:
        return self._pytesseract_available and bool(self._plataforma.tesseract_cmd)

    @property
    def plataforma(self) -> dict:
        return self._plataforma.to_dict()

    # ── Punto de entrada ──────────────────────────────────────────────────────

    def extraer_texto(self, documento: DocumentoIdentidad) -> TextoExtraido:
        if not self.disponible:
            raise RuntimeError(
                "No hay motor OCR disponible. Instala Docling (pip install docling) "
                "y/o Tesseract (brew install tesseract / choco install tesseract)."
            )

        logger.info("ocr_inicio", extra={
            "doc_id": documento.doc_id,
            "sistema": self._plataforma.sistema,
            "motor_docling": self._plataforma.motor_ocr_docling,
        })

        # Paso 1: Docling normal sobre todo el documento.
        # Si Docling falla por completo (p.ej. OOM de GPU en M1) o no esta
        # instalado, se invoca directamente Tesseract de pagina completa.
        texto_docling = self._extraer_con_docling(documento.ruta_pdf)
        texto_docling = self._normalizar(texto_docling)
        # Un escaneo/foto suele salir de Docling como "<!-- image -->": sin texto real
        texto_util = re.sub(r"<!--.*?-->", "", texto_docling).strip()
        if len(texto_util) < 80 and (self.tesseract_disponible or self._ocrmac_disponible()):
            logger.info("ocr_docling_sin_texto_ocr_pagina_completa", extra={
                "doc_id": documento.doc_id, "motor": "apple_vision" if self._ocrmac_disponible() else "tesseract",
            })
            texto_pagina = self._normalizar(self._ocr_pagina_completa(documento.ruta_pdf) or "")
            texto_docling = self._normalizar(texto_util + "\n\n" + texto_pagina)

        # Paso 2: detectar tipo con lo que tenemos
        tipo = self.detectar_tipo(texto_docling)

        # Si Docling no detecto tipo, intentar OCR de toda la primera pagina
        # (puede ser que Docling no haya extraido los headers identificadores)
        if tipo == TipoDocumento.DESCONOCIDO and (self.tesseract_disponible or self._ocrmac_disponible()):
            logger.info("ocr_tipo_no_detectado_intentando_pytesseract_inicial",
                        extra={"doc_id": documento.doc_id})
            texto_tess_inicial = self._ocr_pagina_completa(documento.ruta_pdf)
            if texto_tess_inicial and self.detectar_tipo(texto_docling + "\n" + texto_tess_inicial) \
                    == TipoDocumento.DESCONOCIDO:
                # Tarjetas con texto disperso (INE): modo "sparse text"
                disperso = self._ocr_pagina_completa(documento.ruta_pdf, disperso=True)
                if disperso:
                    texto_tess_inicial = texto_tess_inicial + "\n\n" + disperso
            if texto_tess_inicial:
                tipo = self.detectar_tipo(texto_docling + "\n" + texto_tess_inicial)
                if tipo != TipoDocumento.DESCONOCIDO:
                    texto_docling = self._normalizar(texto_docling + "\n\n" + texto_tess_inicial)
                    logger.info("ocr_tipo_recuperado_con_pytesseract", extra={
                        "doc_id": documento.doc_id, "tipo": tipo.value,
                    })

        # Paso 3: estrategia especifica por tipo
        texto_final = texto_docling

        if tipo == TipoDocumento.CURP:
            # Para CURP: la zona util (clave, nombre, folio, entidad) viene
            # como imagen embebida que Docling NO procesa. Estrategia:
            # 1. Intentar OCR de la zona superior con pdf2image+pytesseract
            # 2. Si falla (poppler no instalado), extraer imagen embebida del PDF
            texto_curp = self._ocr_zona(documento.ruta_pdf, top_pct=0.0, bottom_pct=0.40)
            if not texto_curp:
                # Fallback: extraer imagen embebida directamente del PDF con pypdf
                texto_curp = self._ocr_imagen_embebida_pdf(documento.ruta_pdf)
            if texto_curp:
                logger.info("ocr_curp_zona_superior_ok", extra={
                    "doc_id": documento.doc_id,
                    "chars_zona": len(texto_curp),
                })
                texto_final = self._normalizar(texto_curp + "\n\n" + texto_docling)

        elif tipo == TipoDocumento.INE:
            # Para INE: Docling extrae bien la zona superior (nombre, domicilio,
            # fecha nacimiento) pero a menudo OMITE la zona inferior donde estan
            # CURP, clave de elector, estado, municipio, seccion, vigencia, etc.
            # Forzamos OCR de la mitad inferior + tercio derecho (donde esta sexo).
            texto_ine_inf = self._ocr_zona(documento.ruta_pdf, top_pct=0.40, bottom_pct=1.00)
            texto_ine_der = self._ocr_zona(documento.ruta_pdf, top_pct=0.0, bottom_pct=0.50,
                                            left_pct=0.55, right_pct=1.0)
            extras = []
            if texto_ine_inf and len(texto_ine_inf) > 30:
                extras.append(texto_ine_inf)
            if texto_ine_der and len(texto_ine_der) > 10:
                extras.append(texto_ine_der)
            if extras:
                logger.info("ocr_ine_zonas_extra_ok", extra={
                    "doc_id": documento.doc_id,
                    "zonas_capturadas": len(extras),
                })
                texto_final = self._normalizar(texto_docling + "\n\n" + "\n\n".join(extras))

        elif tipo == TipoDocumento.PASAPORTE:
            # Para Pasaporte: Docling lia los layouts en columnas.
            # OCR clasico da una version mas lineal aunque tambien con ruido.
            # Combinar ambos textos da mas oportunidad al regex/LLM de encontrar
            # los datos correctos.
            texto_tess = self._ocr_pagina_completa(documento.ruta_pdf)
            if texto_tess and len(texto_tess) > 100:
                texto_final = self._normalizar(texto_docling + "\n\n=== OCR ALTERNATIVO ===\n" + texto_tess)
                logger.info("ocr_pasaporte_combinado", extra={
                    "doc_id": documento.doc_id,
                    "docling_chars": len(texto_docling),
                    "tesseract_chars": len(texto_tess),
                })

        # Paso 4: si TODAVIA es muy poco, multi-zona como ultima tentativa
        if len(texto_final) < OCR_MIN_CHARS_NORMAL and self._pil_available and self._docling_available:
            logger.info("ocr_intentando_multizona", extra={"doc_id": documento.doc_id})
            texto_mz = self._extraer_multizona(documento.ruta_pdf)
            if texto_mz and len(texto_mz) > len(texto_final):
                texto_final = self._normalizar(texto_mz)

        # Paso 5: ultima opcion - pytesseract toda la pagina
        if len(texto_final) < OCR_MIN_CHARS_FALLBACK and (self.tesseract_disponible or self._ocrmac_disponible()):
            logger.info("ocr_fallback_pytesseract", extra={"doc_id": documento.doc_id})
            texto_t = self._ocr_pagina_completa(documento.ruta_pdf)
            if texto_t and len(texto_t) > len(texto_final):
                texto_final = self._normalizar(texto_t)

        # Re-detectar tipo con el texto final (puede haber mejorado)
        tipo_final = self.detectar_tipo(texto_final) if tipo == TipoDocumento.DESCONOCIDO else tipo

        logger.info("ocr_completado", extra={
            "doc_id":         documento.doc_id,
            "tipo_detectado": tipo_final.value,
            "chars":          len(texto_final),
        })

        return TextoExtraido(
            doc_id=documento.doc_id,
            texto_crudo=texto_final,
            tipo_detectado=tipo_final,
        )

    # ── Lazy loaders ──────────────────────────────────────────────────────────

    def _cargar_docling(self) -> bool:
        if self._DocumentConverter is not None:
            return True
        if not self._docling_available:
            return False
        try:
            logger.info("cargando_docling_primera_vez")
            from docling.document_converter import DocumentConverter
            self._DocumentConverter = DocumentConverter
            # Conversor configurado segun el SO (ocrmac/MPS en Mac, Tesseract en
            # Windows/Linux/Docker). Se crea una vez y se reutiliza.
            self._converter = crear_conversor_docling()
            return True
        except Exception as e:
            logger.error("docling_carga_fallo", extra={"error": str(e)})
            return False

    def _cargar_pytesseract(self) -> bool:
        if self._pytesseract is not None:
            return True
        if not self.tesseract_disponible:
            return False
        try:
            import pytesseract
            if self._plataforma.tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = self._plataforma.tesseract_cmd
            self._pytesseract = pytesseract
            self._lang = idiomas_pytesseract()
            return True
        except Exception:
            return False

    # ── Estrategia 1: Docling normal ──────────────────────────────────────────

    def _extraer_con_docling(self, ruta_pdf: Path) -> str:
        if not self._cargar_docling():
            return ""
        try:
            converter = self._converter or self._DocumentConverter()
            result = converter.convert(str(ruta_pdf))
            return result.document.export_to_markdown()
        except Exception as e:
            logger.warning("docling_fallo", extra={"error": str(e)})
            return ""

    # ── Utilidades de imagen para Tesseract ───────────────────────────────────

    def _recortar_contenido(self, img):
        """
        Recorta los margenes blancos de la pagina. Muchos PDFs de INE/pasaporte
        son un escaneo pequeño centrado en una hoja A4: sin este recorte los
        porcentajes de zona apuntan a espacio vacio y Tesseract no detecta
        bloques de texto.
        """
        try:
            from PIL import ImageChops
            gris = img.convert("L")
            fondo = self._Image.new("L", gris.size, 255)
            mascara = ImageChops.difference(gris, fondo).point(lambda p: 255 if p > 30 else 0)
            bbox = mascara.getbbox()
            if not bbox:
                return img
            x0, y0, x1, y1 = bbox
            # Solo recortar si el contenido es claramente menor que la pagina
            if (x1 - x0) * (y1 - y0) < 0.85 * img.size[0] * img.size[1]:
                margen = 8
                return img.crop((max(0, x0 - margen), max(0, y0 - margen),
                                 min(img.size[0], x1 + margen), min(img.size[1], y1 + margen)))
        except Exception as e:
            logger.debug("recorte_contenido_fallo", extra={"error": str(e)})
        return img

    # ── Motor OCR de imágenes según SO: Apple Vision (macOS) o Tesseract ─────

    def _ocrmac_disponible(self) -> bool:
        if getattr(self, "_ocrmac_ok", None) is None:
            self._ocrmac_ok = (
                self._plataforma.sistema == "macos"
                and importlib.util.find_spec("ocrmac") is not None
                and os.environ.get("OCR_ZONAS", "auto").lower() != "tesseract"
            )
        return self._ocrmac_ok

    def _motor_imagen_disponible(self) -> bool:
        return self._ocrmac_disponible() or self._cargar_pytesseract()

    def _ocr_imagen(self, img, disperso: bool = False) -> str:
        """
        OCR de una imagen (página o zona recortada).
        En macOS usa Apple Vision (ocrmac): mucho más preciso que Tesseract en
        fotos y escaneos de credenciales. Si falla o no existe, usa Tesseract.
        """
        if self._ocrmac_disponible():
            try:
                texto = self._apple_vision(img)
                if texto and len(texto.strip()) >= 10:
                    return texto
            except Exception as e:  # pragma: no cover - solo macOS
                logger.warning("ocrmac_fallo_usando_tesseract", extra={"error": str(e)})
                self._ocrmac_ok = False
        if self._cargar_pytesseract():
            return self._tesseract(img, disperso=disperso)
        return ""

    @staticmethod
    def _agrupar_lineas(anotaciones: list) -> str:
        """
        Reconstruye líneas de texto a partir de cajas (texto, confianza, bbox)
        normalizadas con origen abajo-izquierda (formato de Apple Vision).
        """
        cajas = []
        for a in anotaciones:
            texto, bbox = a[0], a[2]
            x, y, w, h = bbox
            cajas.append((1 - (y + h / 2), x, h, texto))  # centro vertical desde arriba
        if not cajas:
            return ""
        cajas.sort()
        alturas = sorted(c[2] for c in cajas)
        tolerancia = max(alturas[len(alturas) // 2] * 0.6, 0.004)
        lineas: list[list] = []
        for c in cajas:
            if lineas and abs(c[0] - lineas[-1][0][0]) <= tolerancia:
                lineas[-1].append(c)
            else:
                lineas.append([c])
        return "\n".join(" ".join(t[3] for t in sorted(l, key=lambda t: t[1])) for l in lineas)

    def _apple_vision(self, img) -> str:  # pragma: no cover - solo macOS
        from ocrmac import ocrmac
        if img.width < 1600:  # Vision rinde mejor con texto de al menos ~20 px
            escala = 1600 / img.width
            img = img.resize((int(img.width * escala), int(img.height * escala)))
        anotaciones = ocrmac.OCR(
            img.convert("RGB"), recognition_level="accurate", language_preference=["es-ES", "en-US"]
        ).recognize()
        return self._agrupar_lineas(anotaciones)

    def _tesseract(self, img, disperso: bool = False) -> str:
        """Tesseract con preprocesado ligero y dos modos de segmentacion."""
        try:
            from PIL import ImageOps
            prep = ImageOps.autocontrast(img.convert("L"))
        except Exception:
            prep = img
        lang = getattr(self, "_lang", "spa+eng")
        if disperso:
            return self._pytesseract.image_to_string(prep, lang=lang, config="--psm 11")
        texto = self._pytesseract.image_to_string(prep, lang=lang, config="--psm 3")
        if len(texto.strip()) < 80:
            # Layout disperso (tarjetas): psm 11 = texto suelto sin orden
            alterno = self._pytesseract.image_to_string(prep, lang=lang, config="--psm 11")
            if len(alterno.strip()) > len(texto.strip()):
                texto = alterno
        return texto

    # ── Estrategia 2: OCR de zona arbitraria (CURP zona sup, INE zona inf) ────

    def _ocr_zona(
        self,
        ruta_pdf: Path,
        top_pct: float = 0.0,
        bottom_pct: float = 1.0,
        left_pct: float = 0.0,
        right_pct: float = 1.0,
    ) -> Optional[str]:
        """
        Renderiza la primera pagina como imagen, recorta la region indicada
        por porcentajes (0.0-1.0) del area con contenido y le aplica Tesseract.
        """
        if not (self._pil_available and self._motor_imagen_disponible()):
            return None
        try:
            # DPI=200 (no 300) para evitar OOM en Apple M1 con GPUs compartidas
            imagenes = renderizar_paginas(ruta_pdf, 1, 1, dpi=OCR_DPI)
            if not imagenes:
                return None
            img = self._recortar_contenido(imagenes[0])
            ancho, alto = img.size
            zona = img.crop((int(ancho * left_pct), int(alto * top_pct),
                             int(ancho * right_pct), int(alto * bottom_pct)))
            texto = self._ocr_imagen(zona)
            return texto if texto.strip() else None
        except Exception as e:
            logger.warning("ocr_zona_fallo", extra={"error": str(e)})
            return None

    # ── Estrategia 3: OCR pagina completa (pasaporte / respaldo) ──────────────

    def _ocr_pagina_completa(self, ruta_pdf: Path, disperso: bool = False) -> Optional[str]:
        """OCR (Apple Vision o Tesseract) sobre las primeras dos paginas."""
        if not self._motor_imagen_disponible():
            return None
        try:
            imagenes = renderizar_paginas(ruta_pdf, 1, 2, dpi=OCR_DPI)
            textos = []
            for img in imagenes[:2]:
                try:
                    t = self._ocr_imagen(self._recortar_contenido(img), disperso=disperso)
                    if t.strip():
                        textos.append(t)
                except Exception as e_tess:
                    logger.warning("pytesseract_imagen_fallo", extra={"error": str(e_tess)})
            return "\n\n".join(textos) if textos else None
        except Exception as e:
            logger.warning("pytesseract_pagina_completa_fallo", extra={
                "error": str(e),
                "hint": "macOS: brew install tesseract tesseract-lang poppler | "
                        "Windows: choco install tesseract poppler | Linux: apt-get install "
                        "tesseract-ocr tesseract-ocr-spa poppler-utils",
            })
            return None

    # ── Estrategia 3b: Extraer imagen embebida del PDF (fallback sin render) ──

    def _ocr_imagen_embebida_pdf(self, ruta_pdf: Path) -> Optional[str]:
        """
        Extrae las imagenes embebidas de la primera pagina con pypdf y les
        aplica Tesseract. Util para CURP cuando no se puede renderizar el PDF.
        """
        if not (self._pil_available and self._motor_imagen_disponible()):
            return None
        try:
            from pypdf import PdfReader
        except ImportError:
            logger.debug("pypdf no instalado, no se puede extraer imagen embebida")
            return None
        try:
            from io import BytesIO
            reader = PdfReader(str(ruta_pdf))
            textos = []
            for page in reader.pages[:1]:
                imagenes = []
                try:
                    imagenes = [im.image for im in page.images][:3]  # pypdf >= 3
                except Exception:
                    resources = page.get("/Resources", {})
                    xobjects = resources.get("/XObject", {})
                    if hasattr(xobjects, "get_object"):
                        xobjects = xobjects.get_object()
                    for name in list(xobjects.keys())[:3]:
                        obj = xobjects[name]
                        if hasattr(obj, "get_object"):
                            obj = obj.get_object()
                        if obj.get("/Subtype") == "/Image":
                            try:
                                imagenes.append(self._Image.open(BytesIO(obj.get_data())))
                            except Exception:
                                continue
                for img in imagenes:
                    try:
                        t = self._ocr_imagen(img.convert("RGB"))
                        if t.strip() and len(t.strip()) > 20:
                            textos.append(t)
                    except Exception as e_img:
                        logger.debug("imagen_embebida_ocr_fallo", extra={"error": str(e_img)})
            return "\n\n".join(textos) if textos else None
        except Exception as e:
            logger.warning("ocr_imagen_embebida_fallo", extra={"error": str(e)})
            return None

    # ── Estrategia 4: Multi-zona ──────────────────────────────────────────────

    def _extraer_multizona(self, ruta_pdf: Path) -> Optional[str]:
        if not (self._pil_available and self._cargar_docling()):
            return None
        try:
            imagenes = renderizar_paginas(ruta_pdf, 1, 1, dpi=OCR_DPI)
            if not imagenes:
                return None
            img = imagenes[0]
            ancho, alto = img.size
            superior = img.crop((0, 0, ancho, alto // 2))
            inferior = img.crop((0, alto // 2, ancho, alto))

            textos = []
            for zona in (superior, inferior):
                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        tmp_path = Path(tmp.name)
                    zona.save(tmp_path)
                    converter = self._converter or self._DocumentConverter()
                    result = converter.convert(str(tmp_path))
                    t = result.document.export_to_markdown()
                    if t.strip():
                        textos.append(t)
                except Exception:
                    continue
                finally:
                    if tmp_path is not None:
                        tmp_path.unlink(missing_ok=True)
            return "\n\n".join(textos) if textos else None
        except Exception as e:
            logger.warning("multizona_fallo", extra={"error": str(e)})
            return None

    # ── Deteccion de tipo ─────────────────────────────────────────────────────

    def detectar_tipo(self, texto: str) -> TipoDocumento:
        texto_upper = texto.upper()
        puntuacion: dict[TipoDocumento, int] = {t: 0 for t in TipoDocumento}

        for tipo, patrones in _PATRONES_TIPO.items():
            for patron in patrones:
                if re.search(patron, texto_upper):
                    puntuacion[tipo] += 1

        # Si tanto INE como CURP tienen puntos, gana CURP si tiene "CONSTANCIA"
        # o "TELCURP" (constancia de CURP, no INE)
        if (puntuacion[TipoDocumento.CURP] > 0 and
            re.search(r"CONSTANCIA|TELCURP|TRAMITE GRATUITO", texto_upper)):
            return TipoDocumento.CURP

        mejor = max(puntuacion, key=lambda t: puntuacion[t])
        if puntuacion[mejor] == 0:
            logger.warning("tipo_documento_no_detectado")
            return TipoDocumento.DESCONOCIDO
        return mejor

    # ── Normalizacion ─────────────────────────────────────────────────────────

    @staticmethod
    def _normalizar(texto: str) -> str:
        texto = unicodedata.normalize("NFC", texto)
        texto = "".join(
            c for c in texto
            if unicodedata.category(c)[0] != "C" or c in "\n\t"
        )
        lineas = [re.sub(r" {2,}", " ", linea).strip() for linea in texto.splitlines()]
        resultado = []
        vacias = 0
        for linea in lineas:
            if linea == "":
                vacias += 1
                if vacias <= 1:
                    resultado.append(linea)
            else:
                vacias = 0
                resultado.append(linea)
        return "\n".join(resultado)
