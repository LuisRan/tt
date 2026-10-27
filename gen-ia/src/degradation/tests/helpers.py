"""Generadores de páginas de prueba con marcadores y detector de centroides."""
from __future__ import annotations

import io

import cv2
import numpy as np
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from contracts.common import Face

INE_MM = (85.60, 53.98)
LETTER_MM = (215.9, 279.4)

# Posiciones asimétricas (mm, origen arriba-izquierda) para atrapar errores de
# signo / espejo / medio píxel.
MARKERS_INE = [(6.0, 6.0), (25.0, 9.0), (72.0, 5.5), (80.0, 30.0), (11.0, 47.0),
               (47.0, 27.0), (63.0, 44.0), (35.0, 40.0)]
MARKERS_LETTER = [(15.0, 15.0), (100.0, 30.0), (200.0, 12.0), (190.0, 150.0),
                  (20.0, 260.0), (110.0, 140.0), (150.0, 250.0), (60.0, 90.0)]
R_INE_MM = 1.2
R_LETTER_MM = 3.0


def marker_pdf(pages: list[tuple[tuple[float, float], list[tuple[float, float]], float]]) -> bytes:
    """PDF (reportlab) con discos negros en posiciones mm conocidas y la marca
    MOCK DATA en gris claro. pages = [(size_mm, markers_mm, radius_mm)]."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    for (w, h), markers, r in pages:
        c.setPageSize((w * mm, h * mm))
        c.setFillGray(0.85)
        c.setFont("Helvetica-Bold", max(4.0, h * 0.08) * 2.83)
        c.drawCentredString(w / 2 * mm, h * 0.06 * mm, "MOCK DATA")
        c.setFillGray(0.0)
        for x, y in markers:
            c.circle(x * mm, (h - y) * mm, r * mm, stroke=0, fill=1)
        c.showPage()
    c.save()
    return buf.getvalue()


def ine_faces():
    return [(Face.ANVERSO, INE_MM), (Face.REVERSO, INE_MM)]


def detect_markers(img: np.ndarray, r_px: float) -> np.ndarray:
    """Centroides (coordenadas de BORDE: centro del píxel j = j+0.5) de los
    discos oscuros. Normaliza iluminación con un cierre morfológico grande."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    gray = cv2.medianBlur(gray, 3)
    k = int(max(7, round(r_px * 5))) | 1
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    dark = np.clip(1.0 - gray.astype(np.float32) / np.maximum(bg.astype(np.float32), 1.0), 0, 1)
    mask = (dark > 0.35).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    area_exp = np.pi * r_px * r_px
    pts = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if not (0.3 * area_exp <= a <= 3.0 * area_exp):
            continue
        pad = 2
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(img.shape[1], x + w + pad), min(img.shape[0], y + h + pad)
        win = dark[y0:y1, x0:x1].copy()
        reg = cv2.dilate((lab[y0:y1, x0:x1] == i).astype(np.uint8), np.ones((3, 3), np.uint8))
        win *= reg
        win[win < 0.1] = 0
        ys, xs = np.mgrid[y0:y1, x0:x1]
        s = win.sum()
        pts.append(((xs * win).sum() / s + 0.5, (ys * win).sum() / s + 0.5))
    return np.array(pts, dtype=np.float64).reshape(-1, 2)


def marker_errors(img: np.ndarray, composed, markers_mm, r_mm: float, dpi: float) -> np.ndarray:
    """Distancia (px) de cada marcador predicho (composed·p) al centroide
    detectado más cercano. Cada predicción debe tener una detección propia
    (asignación única); se toleran detecciones espurias extra (p.ej. artefactos
    de ruido en las esquinas expuestas), pero no faltantes."""
    m = np.asarray(composed, dtype=np.float64)
    pred = []
    for x, y in markers_mm:
        v = m @ np.array([x, y, 1.0])
        pred.append(v[:2] / v[2])
    pred = np.array(pred)
    det = detect_markers(img, r_mm * dpi / 25.4)
    assert len(det) >= len(markers_mm), f"detectados {len(det)} marcadores, esperados {len(markers_mm)}"
    d = np.linalg.norm(pred[:, None, :] - det[None, :, :], axis=2)
    nearest = d.argmin(axis=1)
    assert len(set(nearest.tolist())) == len(markers_mm), "dos predicciones comparten detección"
    return d.min(axis=1)
