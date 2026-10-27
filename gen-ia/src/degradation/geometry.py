"""Utilidades de matrices y warps.

Convención de píxeles (coordenadas de BORDE): el píxel (fila i, col j) cubre
[j, j+1) x [i, i+1). Así mm 0 -> px 0.0 y mm W -> px ancho. Todas las matrices
registradas en el TransformLog usan esta convención.

OpenCV (`warpAffine`/`warpPerspective`) trabaja con coordenadas de CENTRO de
píxel (el centro del píxel j está en x=j). Para llamar a OpenCV con una matriz
de borde M se usa  M_cv = T(-0.5) · M · T(+0.5).
`cv2.resize` ya mapea bordes con bordes (x_out = x_in · w_out / w_in en
coordenadas de borde), por lo que su matriz es diag(w_out/w_in, h_out/h_in).
"""
from __future__ import annotations

import math

import cv2
import numpy as np

_HALF = 0.5


def translation(tx: float, ty: float) -> np.ndarray:
    return np.array([[1.0, 0.0, tx], [0.0, 1.0, ty], [0.0, 0.0, 1.0]])


def scale(sx: float, sy: float | None = None) -> np.ndarray:
    sy = sx if sy is None else sy
    return np.array([[sx, 0.0, 0.0], [0.0, sy, 0.0], [0.0, 0.0, 1.0]])


def to_opencv(m: np.ndarray) -> np.ndarray:
    """Matriz de borde -> matriz de centro de píxel para OpenCV."""
    return translation(-_HALF, -_HALF) @ m @ translation(_HALF, _HALF)


def apply(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Aplica una homografía 3x3 a puntos Nx2."""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    h = np.hstack([pts, np.ones((len(pts), 1))]) @ m.T
    return h[:, :2] / h[:, 2:3]


def fit_expand(m: np.ndarray, w: int, h: int) -> tuple[np.ndarray, int, int]:
    """Añade una traslación a `m` para que la imagen w x h completa quepa en un
    lienzo nuevo sin recorte. Devuelve (M_final, new_w, new_h). El contenido se
    centra en el lienzo entero (redondeado hacia arriba)."""
    corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)
    tc = apply(m, corners)
    minx, miny = tc.min(axis=0)
    maxx, maxy = tc.max(axis=0)
    bw, bh = maxx - minx, maxy - miny
    new_w = max(1, int(math.ceil(bw - 1e-6)))
    new_h = max(1, int(math.ceil(bh - 1e-6)))
    tx = -minx + (new_w - bw) / 2.0
    ty = -miny + (new_h - bh) / 2.0
    return translation(tx, ty) @ m, new_w, new_h


def warp(img: np.ndarray, m: np.ndarray, out_w: int, out_h: int, fill: tuple[int, int, int]) -> np.ndarray:
    """Warp con matriz de BORDE `m` (entrada -> salida)."""
    mcv = to_opencv(m)
    border = tuple(float(c) for c in fill)
    if abs(m[2, 0]) < 1e-15 and abs(m[2, 1]) < 1e-15 and abs(m[2, 2] - 1.0) < 1e-15:
        return cv2.warpAffine(
            img, mcv[:2], (out_w, out_h), flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=border,
        )
    return cv2.warpPerspective(
        img, mcv, (out_w, out_h), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=border,
    )


def to_list(m: np.ndarray) -> list[list[float]]:
    return [[float(v) for v in row] for row in np.asarray(m, dtype=np.float64)]
