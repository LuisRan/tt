"""degrade_page / degrade_pdf / rasterize_pdf / project_ground_truth."""
from __future__ import annotations

import math
from typing import Sequence

import cv2
import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from contracts.common import Face, project_bbox
from contracts.degradation import (
    GEOMETRIC_KINDS,
    AppliedStep,
    DegradationConfig,
    PageTransformLog,
    StepKind,
    TransformLog,
)
from contracts.ground_truth import GroundTruth, TransformedGeometry

from . import geometry as G
from .steps import MM_PER_INCH, STEP_FUNCS, StepState, sample_params

# Tolerancia (px) entre el tamaño rasterizado y el nominal face_size_mm·dpi/25.4.
# pdfium redondea hacia arriba el tamaño del bitmap (hasta +1 px).
_RASTER_SIZE_TOL_PX = 2


def rasterize_pdf(pdf_bytes: bytes, dpi: float) -> list[np.ndarray]:
    """Rasteriza cada página a `dpi` (RGB uint8, HxWx3). Fondo blanco.

    La esquina superior izquierda de la página queda en px (0, 0) y la escala es
    EXACTAMENTE dpi/72 px por punto (= dpi/25.4 px por mm). El bitmap mide
    ceil(lado·dpi/72); la fracción de píxel sobrante queda en blanco.
    Nota: `PdfPage.render(scale=...)` de pypdfium2 estira la página para llenar
    el bitmap redondeado hacia arriba (error de hasta ~1 px en el borde lejano),
    por eso se usa FPDF_RenderPageBitmapWithMatrix con la escala exacta.
    """
    s = float(dpi) / 72.0
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        out = []
        for i in range(len(doc)):
            page = doc[i]
            if page.get_rotation() != 0:
                raise ValueError(f"página {i}: /Rotate distinto de 0 no soportado")
            pw, ph = page.get_size()
            w = max(1, math.ceil(pw * s - 1e-6))
            h = max(1, math.ceil(ph * s - 1e-6))
            bmp = pdfium.PdfBitmap.new_native(w, h, pdfium_c.FPDFBitmap_BGR, rev_byteorder=True)
            bmp.fill_rect((255, 255, 255, 255), 0, 0, w, h)
            pdfium_c.FPDF_RenderPageBitmapWithMatrix(
                bmp, page, pdfium_c.FS_MATRIX(s, 0, 0, s, 0, 0), pdfium_c.FS_RECTF(0, 0, w, h),
                pdfium_c.FPDF_ANNOT)
            arr = np.asarray(bmp.to_pil().convert("RGB"), dtype=np.uint8)
            out.append(np.ascontiguousarray(arr))
            page.close()
        return out
    finally:
        doc.close()


def _ensure_rgb(image: np.ndarray) -> np.ndarray:
    img = np.asarray(image)
    if img.dtype != np.uint8:
        raise TypeError("se espera imagen uint8")
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    if img.ndim == 3 and img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
    if img.ndim == 3 and img.shape[2] == 3:
        return img.copy()
    raise ValueError(f"forma de imagen no soportada: {img.shape}")


def _wh(img: np.ndarray) -> tuple[int, int]:
    return (int(img.shape[1]), int(img.shape[0]))


def degrade_page(
    image: np.ndarray,
    cfg: DegradationConfig,
    rng: np.random.Generator,
    *,
    page_index: int,
    face: Face,
    face_size_mm: tuple[float, float],
    render_dpi: float,
) -> tuple[np.ndarray, PageTransformLog]:
    """Aplica `cfg.steps` en orden a una página ya rasterizada a `render_dpi`.

    Por cada paso se consume SIEMPRE un número u~U(0,1) del rng para decidir la
    activación (u < probability), de modo que la secuencia aleatoria es estable.
    """
    img = _ensure_rgb(image)
    k = render_dpi / MM_PER_INCH
    m_render = G.scale(k, k)
    size0 = _wh(img)
    steps = [AppliedStep(kind=StepKind.RENDER, applied=True, params={"dpi": float(render_dpi)},
                         matrix=G.to_list(m_render), input_size_px=size0, output_size_px=size0)]
    composed = m_render
    st = StepState(dpi=float(render_dpi))

    for sc in cfg.steps:
        if sc.kind == StepKind.RENDER:
            raise ValueError("'render' es implícito; no debe aparecer en cfg.steps")
        size_in = _wh(img)
        u = float(rng.random())
        if not u < sc.probability:
            steps.append(AppliedStep(kind=sc.kind, applied=False, input_size_px=size_in, output_size_px=size_in))
            continue
        params = sample_params(sc.kind, sc.params, sc.options, rng)
        img, m, extra = STEP_FUNCS[sc.kind](img, st, params, sc.options, rng)
        if sc.kind in GEOMETRIC_KINDS:
            assert m is not None
            composed = m @ composed
        rec = {**params, **extra}
        steps.append(AppliedStep(kind=sc.kind, applied=True, params=rec,
                                 matrix=G.to_list(m) if m is not None else None,
                                 input_size_px=size_in, output_size_px=_wh(img)))

    log = PageTransformLog(
        page_index=page_index, face=face,
        face_size_mm=(float(face_size_mm[0]), float(face_size_mm[1])),
        steps=steps, composed=G.to_list(composed), final_size_px=_wh(img),
    )
    return img, log


def effective_dpi(page_log: PageTransformLog) -> float:
    """DPI efectivo final de una página (render_dpi, o el último target_dpi)."""
    dpi = None
    for s in page_log.steps:
        if s.kind == StepKind.RENDER:
            dpi = float(s.params["dpi"])
        elif s.kind == StepKind.DPI_RESAMPLE and s.applied:
            dpi = float(s.params["target_dpi"])
    if dpi is None:
        raise ValueError("log sin paso render")
    return dpi


# ------------------------------------------------------------- PDF writer
def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def images_to_pdf(jpegs: Sequence[tuple[bytes, int, int, float]], title: str | None = None) -> bytes:
    """Escribe un PDF mínimo y determinista: una página por imagen JPEG
    (bytes, w_px, h_px, dpi). Tamaño de página = w_px/dpi pulgadas. Sin fechas
    ni /ID, así misma entrada => mismos bytes."""
    objs: list[bytes] = []  # objeto i+1

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    add(b"")  # 1 catalog (placeholder)
    add(b"")  # 2 pages (placeholder)
    kids = []
    for jpg, w, h, dpi in jpegs:
        wpt, hpt = w * 72.0 / dpi, h * 72.0 / dpi
        img_id = add(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
            b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" % (w, h, len(jpg))
            + jpg + b"\nendstream"
        )
        content = f"q {_fmt(wpt)} 0 0 {_fmt(hpt)} 0 0 cm /Im0 Do Q".encode()
        c_id = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        p_id = add(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_fmt(wpt)} {_fmt(hpt)}] "
            f"/Resources << /XObject << /Im0 {img_id} 0 R >> >> /Contents {c_id} 0 R >>".encode()
        )
        kids.append(p_id)
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    info = b"<< /Producer (ocr-synth degradation) /Subject (MOCK DATA - synthetic test sample)"
    if title:
        safe = title.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        info += b" /Title (" + safe.encode("latin-1", "replace") + b")"
    info_id = add(info + b" >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, info_id, xref)
    return bytes(out)


def encode_jpeg(img: np.ndarray, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise RuntimeError("fallo al codificar JPEG")
    return buf.tobytes()


def page_rng(degradation_seed: int, page_index: int) -> np.random.Generator:
    return np.random.default_rng([int(degradation_seed), int(page_index)])


def degrade_pdf_with_images(
    clean_pdf: bytes,
    cfg: DegradationConfig,
    *,
    sample_id: str,
    degradation_seed: int,
    faces: list[tuple[Face, tuple[float, float]]],
) -> tuple[bytes, TransformLog, list[np.ndarray]]:
    """Como `degrade_pdf` pero también devuelve las imágenes degradadas
    (antes de la compresión JPEG final del contenedor PDF)."""
    pages = rasterize_pdf(clean_pdf, cfg.render_dpi)
    if len(pages) != len(faces):
        raise ValueError(f"el PDF tiene {len(pages)} páginas pero faces tiene {len(faces)}")
    logs, imgs, jpegs = [], [], []
    for i, (img, (face, size_mm)) in enumerate(zip(pages, faces)):
        exp_w = size_mm[0] / MM_PER_INCH * cfg.render_dpi
        exp_h = size_mm[1] / MM_PER_INCH * cfg.render_dpi
        if abs(img.shape[1] - exp_w) > _RASTER_SIZE_TOL_PX or abs(img.shape[0] - exp_h) > _RASTER_SIZE_TOL_PX:
            raise ValueError(
                f"página {i}: tamaño rasterizado {img.shape[1]}x{img.shape[0]} px no corresponde a "
                f"{size_mm} mm a {cfg.render_dpi} dpi")
        out, log = degrade_page(img, cfg, page_rng(degradation_seed, i), page_index=i, face=Face(face),
                                face_size_mm=size_mm, render_dpi=cfg.render_dpi)
        logs.append(log)
        imgs.append(out)
        jpegs.append((encode_jpeg(out, cfg.output_jpeg_quality), out.shape[1], out.shape[0], effective_dpi(log)))
    tlog = TransformLog(sample_id=sample_id, degradation_seed=int(degradation_seed), config_name=cfg.name,
                        config=cfg.model_copy(deep=True), pages=logs)
    return images_to_pdf(jpegs, title=sample_id), tlog, imgs


def degrade_pdf(
    clean_pdf: bytes,
    cfg: DegradationConfig,
    *,
    sample_id: str,
    degradation_seed: int,
    faces: list[tuple[Face, tuple[float, float]]],
) -> tuple[bytes, TransformLog]:
    """Rasteriza `clean_pdf` a `cfg.render_dpi`, degrada cada página con
    rng = default_rng([degradation_seed, page_index]) y empaqueta el resultado
    en un PDF raster (JPEG calidad `cfg.output_jpeg_quality`). Cada página mide
    físicamente final_px / dpi_efectivo. Salida determinista byte a byte."""
    pdf, log, _ = degrade_pdf_with_images(clean_pdf, cfg, sample_id=sample_id,
                                          degradation_seed=degradation_seed, faces=faces)
    return pdf, log


def project_ground_truth(gt: GroundTruth, log: TransformLog) -> GroundTruth:
    """Devuelve una COPIA de `gt` con `transformed` rellenado en cada campo:
    quad_px = composed · esquinas(bbox_mm) (TL, TR, BR, BL), bbox_px = envolvente.
    No se recorta a la imagen (el quad es exacto; puede salir de la imagen solo si
    el paso no usó expand). `bbox_mm` queda intacto. También fija
    `degradation_seed` = log.degradation_seed."""
    by_index = {p.page_index: p for p in log.pages}
    out = gt.model_copy(deep=True)
    out.degradation_seed = log.degradation_seed
    for page in out.pages:
        pl = by_index.get(page.page_index)
        if pl is None:
            raise ValueError(f"transform_log sin página {page.page_index}")
        if pl.face != page.face:
            raise ValueError(f"página {page.page_index}: cara GT {page.face} != log {pl.face}")
        w, h = pl.final_size_px
        for f in page.fields:
            quad = project_bbox(pl.composed, f.bbox_mm)
            f.transformed = TransformedGeometry(quad_px=quad, bbox_px=quad.envelope(),
                                                image_width_px=int(w), image_height_px=int(h))
    return out


__all__ = [
    "rasterize_pdf", "degrade_page", "degrade_pdf", "degrade_pdf_with_images",
    "project_ground_truth", "effective_dpi", "images_to_pdf", "page_rng",
]
