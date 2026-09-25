"""
infrastructure/platform_config.py

Deteccion del sistema operativo y configuracion del OCR segun la plataforma.

El prototipo original se ajusto para macOS (Apple Silicon). Este modulo
centraliza todo lo que depende del SO para que el mismo codigo corra en:

  +-----------+------------------------------+---------------------------+-------------+
  | Plataforma| Motor OCR de Docling         | Tesseract / Poppler       | Acelerador  |
  +-----------+------------------------------+---------------------------+-------------+
  | macOS     | ocrmac (Vision de Apple)     | Homebrew (/opt/homebrew)  | MPS (Metal) |
  | Windows   | Tesseract CLI (o RapidOCR)   | C:\\Program Files\\...      | CUDA / CPU  |
  | Linux     | Tesseract CLI                | /usr/bin                  | CUDA / CPU  |
  | Docker    | Tesseract CLI                | /usr/bin (imagen)         | CPU         |
  +-----------+------------------------------+---------------------------+-------------+

Todo se puede forzar por variables de entorno (ver config/settings.py):
OCR_ENGINE, DOCLING_DEVICE, DOCLING_TABLES, TESSERACT_CMD, POPPLER_PATH.

Ademas expone `renderizar_pagina()` que convierte una pagina PDF a imagen PIL
usando pdf2image+Poppler y, si Poppler no existe (tipico en Windows),
cae a pypdfium2 (que se instala junto con Docling).
"""
from __future__ import annotations

import glob
import importlib.util
import logging
import os
import platform
import shutil
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from config import settings

logger = logging.getLogger(__name__)


# ── Deteccion ─────────────────────────────────────────────────────────────────

@dataclass
class PlataformaInfo:
    sistema: str                 # "macos" | "windows" | "linux"
    arquitectura: str            # "arm64" | "x86_64" | ...
    en_docker: bool
    apple_silicon: bool
    ram_gb: Optional[float]
    motor_ocr_docling: str       # ocrmac | tesseract | easyocr | rapidocr
    dispositivo: str             # mps | cuda | cpu | auto
    tablas_habilitadas: bool
    tesseract_cmd: Optional[str]
    poppler_path: Optional[str]
    renderizador_pdf: str        # poppler | pypdfium2 | ninguno
    notas: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _detectar_sistema() -> str:
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s.startswith("win"):
        return "windows"
    return "linux"


def _en_docker() -> bool:
    if os.environ.get("RUNNING_IN_DOCKER", "").lower() in {"1", "true", "yes"}:
        return True
    if Path("/.dockerenv").exists():
        return True
    try:
        return "docker" in Path("/proc/1/cgroup").read_text(errors="ignore")
    except Exception:
        return False


def _ram_gb() -> Optional[float]:
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    except (ValueError, OSError, AttributeError):
        pass
    if _detectar_sistema() == "windows":
        try:
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = _MemStatus()
            st.dwLength = ctypes.sizeof(_MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
            return round(st.ullTotalPhys / 1024**3, 1)
        except Exception:
            return None
    return None


# ── Busqueda de binarios ──────────────────────────────────────────────────────

_TESSERACT_CANDIDATOS = {
    "macos": ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"],
    "windows": [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
        r"C:\ProgramData\chocolatey\bin\tesseract.exe",
        os.path.expandvars(r"%USERPROFILE%\scoop\shims\tesseract.exe"),
    ],
    "linux": ["/usr/bin/tesseract", "/usr/local/bin/tesseract"],
}

_POPPLER_CANDIDATOS = {
    "macos": ["/opt/homebrew/bin", "/usr/local/bin"],
    "windows": [
        r"C:\Program Files\poppler\Library\bin",
        r"C:\Program Files\poppler\bin",
        r"C:\poppler\Library\bin",
        r"C:\poppler\bin",
        r"C:\ProgramData\chocolatey\lib\poppler\tools\Library\bin",
        os.path.expandvars(r"%USERPROFILE%\scoop\apps\poppler\current\Library\bin"),
        os.path.expandvars(r"%USERPROFILE%\scoop\apps\poppler\current\bin"),
    ],
    "linux": ["/usr/bin", "/usr/local/bin"],
}


def _buscar_tesseract(sistema: str) -> Optional[str]:
    if settings.TESSERACT_CMD and Path(settings.TESSERACT_CMD).exists():
        return settings.TESSERACT_CMD
    en_path = shutil.which("tesseract")
    if en_path:
        return en_path
    for cand in _TESSERACT_CANDIDATOS.get(sistema, []):
        if cand and Path(cand).exists():
            return cand
    return None


def _buscar_poppler(sistema: str) -> Optional[str]:
    """Devuelve el directorio que contiene pdftoppm (o None si esta en PATH / no existe)."""
    exe = "pdftoppm.exe" if sistema == "windows" else "pdftoppm"
    if settings.POPPLER_PATH and (Path(settings.POPPLER_PATH) / exe).exists():
        return settings.POPPLER_PATH
    if shutil.which("pdftoppm"):
        return None  # en PATH: pdf2image lo encuentra solo
    candidatos = list(_POPPLER_CANDIDATOS.get(sistema, []))
    if sistema == "windows":
        # instalaciones descomprimidas tipo C:\Program Files\poppler-24.08.0\Library\bin
        for patron in (r"C:\Program Files\poppler-*\Library\bin", r"C:\poppler-*\Library\bin",
                       r"C:\Program Files\poppler-*\bin"):
            candidatos.extend(sorted(glob.glob(patron), reverse=True))
    for cand in candidatos:
        if cand and (Path(cand) / exe).exists():
            return cand
    return None


def _poppler_disponible(sistema: str, poppler_path: Optional[str]) -> bool:
    return bool(poppler_path) or shutil.which("pdftoppm") is not None


# ── Eleccion de motor / dispositivo ──────────────────────────────────────────

def _elegir_motor(sistema: str, tesseract_cmd: Optional[str], notas: list[str]) -> str:
    forzado = settings.OCR_ENGINE
    if forzado and forzado != "auto":
        return forzado

    if sistema == "macos" and importlib.util.find_spec("ocrmac") is not None:
        return "ocrmac"
    if sistema == "macos":
        notas.append("ocrmac no instalado (pip install ocrmac); se usa Tesseract para Docling")
    if tesseract_cmd:
        return "tesseract"
    if importlib.util.find_spec("rapidocr") is not None:
        notas.append("Tesseract no encontrado; Docling usara RapidOCR")
        return "rapidocr"
    if importlib.util.find_spec("easyocr") is not None:
        return "easyocr"
    notas.append("Sin motor OCR para Docling; solo se leera texto nativo del PDF")
    return "ninguno"


def _elegir_dispositivo(sistema: str, apple_silicon: bool) -> str:
    forzado = settings.DOCLING_DEVICE
    if forzado and forzado != "auto":
        return forzado
    if sistema == "macos" and apple_silicon:
        return "mps"
    try:
        if importlib.util.find_spec("torch") is not None:
            import torch  # noqa: WPS433  (solo si ya esta instalado)

            if torch.cuda.is_available():
                return "cuda"
    except Exception:
        pass
    return "cpu"


def _tablas_habilitadas(sistema: str, ram_gb: Optional[float]) -> bool:
    valor = settings.DOCLING_TABLES
    if valor in {"true", "1", "si", "yes"}:
        return True
    if valor in {"false", "0", "no"}:
        return False
    # auto: TableFormer en M1 con 8 GB de memoria compartida provoca OOM
    if sistema == "macos" and ram_gb is not None and ram_gb <= 8.5:
        return False
    return True


@lru_cache(maxsize=1)
def detectar_plataforma() -> PlataformaInfo:
    sistema = _detectar_sistema()
    arquitectura = platform.machine().lower() or "desconocida"
    apple_silicon = sistema == "macos" and arquitectura in {"arm64", "aarch64"}
    ram = _ram_gb()
    notas: list[str] = []

    tesseract_cmd = _buscar_tesseract(sistema)
    if not tesseract_cmd:
        notas.append("Tesseract no encontrado: el OCR de respaldo por zonas no estara disponible")

    poppler_path = _buscar_poppler(sistema)
    if _poppler_disponible(sistema, poppler_path):
        renderizador = "poppler"
    elif importlib.util.find_spec("pypdfium2") is not None:
        renderizador = "pypdfium2"
        notas.append("Poppler no encontrado; se renderiza con pypdfium2")
    else:
        renderizador = "ninguno"
        notas.append("Sin Poppler ni pypdfium2: no se pueden recortar zonas del PDF")

    info = PlataformaInfo(
        sistema=sistema,
        arquitectura=arquitectura,
        en_docker=_en_docker(),
        apple_silicon=apple_silicon,
        ram_gb=ram,
        motor_ocr_docling=_elegir_motor(sistema, tesseract_cmd, notas),
        dispositivo=_elegir_dispositivo(sistema, apple_silicon),
        tablas_habilitadas=_tablas_habilitadas(sistema, ram),
        tesseract_cmd=tesseract_cmd,
        poppler_path=poppler_path,
        renderizador_pdf=renderizador,
        notas=notas,
    )

    # pytesseract necesita la ruta explicita en Windows (no suele estar en PATH)
    if tesseract_cmd and importlib.util.find_spec("pytesseract") is not None:
        try:
            import pytesseract

            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        except Exception:  # pragma: no cover
            pass
        # tessdata junto al ejecutable en Windows
        if sistema == "windows" and not os.environ.get("TESSDATA_PREFIX"):
            tessdata = Path(tesseract_cmd).parent / "tessdata"
            if tessdata.exists():
                os.environ["TESSDATA_PREFIX"] = str(tessdata)

    logger.info("plataforma_detectada", extra=info.to_dict())
    return info


# ── Docling: construccion del conversor segun plataforma ─────────────────────

def _idiomas_tesseract() -> list[str]:
    """Idiomas instalados que nos interesan (spa/eng). Evita error si falta 'spa'."""
    info = detectar_plataforma()
    if not info.tesseract_cmd:
        return ["eng"]
    try:
        import subprocess

        salida = subprocess.run(
            [info.tesseract_cmd, "--list-langs"], capture_output=True, text=True, timeout=15
        )
        disponibles = set((salida.stdout + salida.stderr).split())
        elegidos = [l for l in ("spa", "eng") if l in disponibles]
        return elegidos or ["eng"]
    except Exception:
        return ["spa", "eng"]


def idiomas_pytesseract() -> str:
    return "+".join(_idiomas_tesseract())


def crear_conversor_docling() -> Any:
    """
    Crea un DocumentConverter configurado para la plataforma actual.
    Si la configuracion avanzada falla (version vieja de docling, motor no
    instalado...) regresa el conversor por defecto para no romper el flujo.
    """
    from docling.document_converter import DocumentConverter

    info = detectar_plataforma()
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import PdfFormatOption

        try:
            from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        except ImportError:  # docling < 2.40
            from docling.datamodel.pipeline_options import AcceleratorDevice, AcceleratorOptions

        opciones = PdfPipelineOptions()
        opciones.do_table_structure = info.tablas_habilitadas
        opciones.do_ocr = info.motor_ocr_docling != "ninguno"

        ocr_opts = _crear_opciones_ocr(info)
        if ocr_opts is not None:
            opciones.ocr_options = ocr_opts

        dispositivo = {
            "mps": AcceleratorDevice.MPS,
            "cuda": AcceleratorDevice.CUDA,
            "cpu": AcceleratorDevice.CPU,
        }.get(info.dispositivo, AcceleratorDevice.AUTO)
        opciones.accelerator_options = AcceleratorOptions(
            num_threads=max(1, min(8, os.cpu_count() or 4)), device=dispositivo
        )

        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opciones)}
        )
    except Exception as e:  # pragma: no cover - depende de la version de docling
        logger.warning("docling_config_plataforma_fallo_usando_default", extra={"error": str(e)})
        return DocumentConverter()


def _crear_opciones_ocr(info: PlataformaInfo) -> Any:
    try:
        from docling.datamodel import pipeline_options as po
    except ImportError:  # pragma: no cover
        return None

    motor = info.motor_ocr_docling
    try:
        if motor == "ocrmac" and hasattr(po, "OcrMacOptions"):
            return po.OcrMacOptions(lang=["es-ES", "en-US"])
        if motor == "tesseract" and hasattr(po, "TesseractCliOcrOptions"):
            kwargs: dict[str, Any] = {"lang": _idiomas_tesseract()}
            if info.tesseract_cmd:
                kwargs["tesseract_cmd"] = info.tesseract_cmd
            return po.TesseractCliOcrOptions(**kwargs)
        if motor == "easyocr" and hasattr(po, "EasyOcrOptions"):
            return po.EasyOcrOptions(lang=["es", "en"])
        if motor == "rapidocr" and hasattr(po, "RapidOcrOptions"):
            return po.RapidOcrOptions()
    except Exception as e:
        logger.warning("ocr_options_invalidas", extra={"motor": motor, "error": str(e)})
    return None


# ── Renderizado de paginas PDF a imagen ───────────────────────────────────────

def renderizar_paginas(ruta_pdf: Path | str, primera: int = 1, ultima: int = 1,
                       dpi: Optional[int] = None) -> list:
    """
    Convierte paginas [primera, ultima] (1-indexadas) a imagenes PIL.
    Orden de intento: pdf2image+Poppler -> pypdfium2. Regresa [] si ninguno funciona.
    """
    dpi = dpi or settings.OCR_DPI
    info = detectar_plataforma()

    if info.renderizador_pdf == "poppler":
        try:
            from pdf2image import convert_from_path

            kwargs: dict[str, Any] = {"first_page": primera, "last_page": ultima, "dpi": dpi}
            if info.poppler_path:
                kwargs["poppler_path"] = info.poppler_path
            return convert_from_path(str(ruta_pdf), **kwargs)
        except Exception as e:
            logger.warning("pdf2image_fallo_intentando_pypdfium2", extra={"error": str(e)})

    if importlib.util.find_spec("pypdfium2") is not None:
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(ruta_pdf))
            imagenes = []
            try:
                for i in range(primera - 1, min(ultima, len(pdf))):
                    pagina = pdf[i]
                    imagenes.append(pagina.render(scale=dpi / 72).to_pil())
            finally:
                pdf.close()
            return imagenes
        except Exception as e:
            logger.warning("pypdfium2_fallo", extra={"error": str(e)})

    return []
