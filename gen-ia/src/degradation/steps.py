"""Implementación de cada paso de degradación.

Cada función recibe (imagen RGB uint8, StepState, params muestreados, options,
rng) y devuelve (imagen, matriz de borde 3x3 | None, params extra a registrar).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import cv2
import numpy as np

from contracts.degradation import Range, StepKind

from . import geometry as G

MM_PER_INCH = 25.4

# Defaults usados si el parámetro falta en la config (documentados en README).
DEFAULT_PARAMS: dict[StepKind, dict[str, Range]] = {
    StepKind.DPI_RESAMPLE: {"target_dpi": Range(min=150, max=300)},
    StepKind.CANVAS_PLACEMENT: {"margin_mm": Range(min=5, max=30), "background": Range(min=235, max=250)},
    StepKind.ROTATION: {"angle_deg": Range(min=-3, max=3)},
    StepKind.SKEW: {"shear": Range(min=-0.03, max=0.03)},
    StepKind.JPEG: {"quality": Range(min=30, max=80)},
    StepKind.GAUSSIAN_NOISE: {"sigma": Range(min=3, max=12)},
    StepKind.SALT_PEPPER: {"amount": Range(min=0.001, max=0.005)},
    StepKind.CONTRAST_BRIGHTNESS: {"contrast": Range(min=0.8, max=1.2), "brightness": Range(min=-20, max=20)},
    StepKind.ILLUMINATION_SHADOW: {"strength": Range(min=0.1, max=0.4)},
    StepKind.BLUR: {"sigma": Range(min=0.3, max=1.0)},
}
# Defaults del modo perspectiva de `skew` (sustituyen a `shear`).
PERSPECTIVE_DEFAULT = {"corner_jitter": Range(min=-0.03, max=0.03)}
# Parámetros opcionales (solo se muestrean si están en la config).
OPTIONAL_PARAMS: dict[StepKind, tuple[str, ...]] = {
    StepKind.SKEW: ("shear_y",),
    StepKind.CANVAS_PLACEMENT: ("margin_y_mm",),
}

CANVAS_SIZES_MM = {"letter": (215.9, 279.4), "a4": (210.0, 297.0), "legal": (215.9, 355.6)}
DEFAULT_FILL = (245, 245, 245)


@dataclass
class StepState:
    dpi: float                       # DPI efectivo actual (px por pulgada de la cara)
    on_canvas: bool = False  # True tras canvas_placement
    extra: dict[str, Any] = field(default_factory=dict)


Result = tuple[np.ndarray, "np.ndarray | None", dict[str, Any]]


def _fill_color(img: np.ndarray, st: StepState) -> tuple[int, int, int]:
    """Color de las zonas expuestas por rotación/skew. Sobre lienzo (cama de
    escáner) = mediana del borde exterior de la imagen actual (sigue a los
    cambios fotométricos previos); sin lienzo = casi blanco fijo."""
    if not st.on_canvas:
        return DEFAULT_FILL
    border = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]], axis=0)
    med = np.median(border, axis=0)
    return (int(med[0]), int(med[1]), int(med[2]))


# --------------------------------------------------------------- geométricos
def dpi_resample(img, st: StepState, p, opts, rng) -> Result:
    h, w = img.shape[:2]
    target = float(p["target_dpi"])
    s = target / st.dpi
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
    out = cv2.resize(img, (nw, nh), interpolation=interp)
    sx, sy = nw / w, nh / h
    extra = {"dpi_in": float(st.dpi), "scale_x": sx, "scale_y": sy}
    st.dpi = target
    return out, G.scale(sx, sy), extra


def canvas_placement(img, st: StepState, p, opts, rng) -> Result:
    h, w = img.shape[:2]
    canvas = opts.get("canvas", "letter")
    if isinstance(canvas, str):
        cw_mm, ch_mm = CANVAS_SIZES_MM[canvas.lower()]
    else:
        cw_mm, ch_mm = float(canvas[0]), float(canvas[1])
    px_per_mm = st.dpi / MM_PER_INCH
    mx_mm = float(p["margin_mm"])
    my_mm = float(p["margin_y_mm"])
    position = opts.get("position", "margin")
    if position not in ("margin", "random"):
        raise ValueError(f"canvas_placement.options.position desconocido: {position!r}")
    mx, my = int(round(mx_mm * px_per_mm)), int(round(my_mm * px_per_mm))
    cw = max(int(round(cw_mm * px_per_mm)), w + 2 * mx)
    ch = max(int(round(ch_mm * px_per_mm)), h + 2 * my)
    if position == "random":
        # Posición uniforme (entera, sin remuestrear) con margen mínimo mx/my:
        # la cara siempre cabe entera. Se consumen exactamente 2 enteros del rng.
        ox = int(rng.integers(mx, cw - w - mx + 1))
        oy = int(rng.integers(my, ch - h - my + 1))
    else:
        ox, oy = mx, my
    bg = int(round(float(p["background"])))
    out = np.full((ch, cw, 3), bg, dtype=np.uint8)
    out[oy:oy + h, ox:ox + w] = img
    st.on_canvas = True
    extra = {"offset_px": [ox, oy], "offset_mm": [ox / px_per_mm, oy / px_per_mm], "position": position,
             "canvas_mm": [cw_mm, ch_mm], "dpi": float(st.dpi)}
    return out, G.translation(ox, oy), extra


def rotation(img, st: StepState, p, opts, rng) -> Result:
    h, w = img.shape[:2]
    a = math.radians(float(p["angle_deg"]))
    c, s = math.cos(a), math.sin(a)
    # Positivo = antihorario en pantalla (convención OpenCV), alrededor del centro.
    r = np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])
    m = G.translation(w / 2, h / 2) @ r @ G.translation(-w / 2, -h / 2)
    if opts.get("expand", True):
        m, nw, nh = G.fit_expand(m, w, h)
    else:
        nw, nh = w, h
    return G.warp(img, m, nw, nh, _fill_color(img, st)), m, {}


def skew(img, st: StepState, p, opts, rng) -> Result:
    h, w = img.shape[:2]
    mode = opts.get("mode", "affine")
    extra: dict[str, Any] = {"mode": mode}
    if mode == "perspective":
        src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
        d = np.array([[p[f"dx_{k}"] * w, p[f"dy_{k}"] * h] for k in ("tl", "tr", "br", "bl")])
        dst = (src + d).astype(np.float32)
        m = cv2.getPerspectiveTransform(src, dst).astype(np.float64)
        m /= m[2, 2]
    elif mode == "affine":
        shx = float(p["shear"])
        shy = float(p.get("shear_y", 0.0))
        sh = np.array([[1.0, shx, 0.0], [shy, 1.0, 0.0], [0.0, 0.0, 1.0]])
        m = G.translation(w / 2, h / 2) @ sh @ G.translation(-w / 2, -h / 2)
    else:
        raise ValueError(f"skew.options.mode desconocido: {mode!r}")
    if opts.get("expand", True):
        m, nw, nh = G.fit_expand(m, w, h)
    else:
        nw, nh = w, h
    return G.warp(img, m, nw, nh, _fill_color(img, st)), m, extra


# --------------------------------------------------------------- fotométricos
def jpeg(img, st, p, opts, rng) -> Result:
    q = int(p["quality"])
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
    if not ok:
        raise RuntimeError("fallo al codificar JPEG")
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB), None, {}


def gaussian_noise(img, st, p, opts, rng) -> Result:
    noise = rng.normal(0.0, float(p["sigma"]), img.shape).astype(np.float32)
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8), None, {}


def salt_pepper(img, st, p, opts, rng) -> Result:
    h, w = img.shape[:2]
    out = img.copy()
    mask = rng.random((h, w)) < float(p["amount"])
    salt = rng.random((h, w)) < 0.5
    out[mask & salt] = 255
    out[mask & ~salt] = 0
    return out, None, {}


def contrast_brightness(img, st, p, opts, rng) -> Result:
    c, b = float(p["contrast"]), float(p["brightness"])
    out = (img.astype(np.float32) - 128.0) * c + 128.0 + b
    return np.clip(np.rint(out), 0, 255).astype(np.uint8), None, {}


def illumination_shadow(img, st, p, opts, rng) -> Result:
    h, w = img.shape[:2]
    k = float(p["strength"])
    ang = float(rng.uniform(0, 360))
    cx, cy = float(rng.uniform(0, 1)), float(rng.uniform(0, 1))
    rad = float(rng.uniform(0.15, 0.5))
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    xs = (xs + 0.5) / w
    ys = (ys + 0.5) / h
    # Gradiente lineal de iluminación en dirección `ang` (0 = claro, 1 = oscuro).
    dx, dy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    proj = xs * dx + ys * dy
    proj = (proj - proj.min()) / max(float(proj.max() - proj.min()), 1e-6)
    illum = 1.0 - 0.5 * k * proj
    # Sombra suave: gaussiana circular (radio relativo al lado mayor).
    ar_x, ar_y = w / max(w, h), h / max(w, h)
    d2 = ((xs - cx) * ar_x) ** 2 + ((ys - cy) * ar_y) ** 2
    illum *= 1.0 - 0.5 * k * np.exp(-d2 / (2 * rad * rad))
    out = img.astype(np.float32) * illum[..., None]
    extra = {"gradient_angle_deg": ang, "shadow_center": [cx, cy], "shadow_radius": rad}
    return np.clip(np.rint(out), 0, 255).astype(np.uint8), None, extra


def blur(img, st, p, opts, rng) -> Result:
    s = float(p["sigma"])
    return cv2.GaussianBlur(img, (0, 0), sigmaX=s, sigmaY=s), None, {}


STEP_FUNCS: dict[StepKind, Callable[..., Result]] = {
    StepKind.DPI_RESAMPLE: dpi_resample,
    StepKind.CANVAS_PLACEMENT: canvas_placement,
    StepKind.ROTATION: rotation,
    StepKind.SKEW: skew,
    StepKind.JPEG: jpeg,
    StepKind.GAUSSIAN_NOISE: gaussian_noise,
    StepKind.SALT_PEPPER: salt_pepper,
    StepKind.CONTRAST_BRIGHTNESS: contrast_brightness,
    StepKind.ILLUMINATION_SHADOW: illumination_shadow,
    StepKind.BLUR: blur,
}

INT_PARAMS = {(StepKind.JPEG, "quality")}


def sample_params(kind: StepKind, cfg_params: dict[str, Range], opts: dict, rng: np.random.Generator) -> dict[str, Any]:
    """Muestrea uniforme cada parámetro (orden: nombre de parámetro) usando los
    rangos de la config y, si faltan, los defaults documentados."""
    if kind == StepKind.SKEW and opts.get("mode", "affine") == "perspective":
        jit = cfg_params.get("corner_jitter", PERSPECTIVE_DEFAULT["corner_jitter"])
        ranges = {f"{a}_{k}": jit for k in ("tl", "tr", "br", "bl") for a in ("dx", "dy")}
        for name, r in cfg_params.items():  # rangos por esquina explícitos ganan
            if name in ranges:
                ranges[name] = r
    else:
        ranges = dict(DEFAULT_PARAMS.get(kind, {}))
        for name in OPTIONAL_PARAMS.get(kind, ()):
            ranges.pop(name, None)
        ranges.update(cfg_params)
    out: dict[str, Any] = {}
    for name in sorted(ranges):
        r = ranges[name]
        v = float(rng.uniform(r.min, r.max)) if r.max > r.min else float(r.min)
        if (kind, name) in INT_PARAMS:
            v = int(round(v))
        out[name] = v
    if kind == StepKind.CANVAS_PLACEMENT and "margin_y_mm" not in out:
        # sin margin_y_mm explícito: segundo muestreo independiente del rango margin_mm
        r = ranges["margin_mm"]
        out["margin_y_mm"] = float(rng.uniform(r.min, r.max)) if r.max > r.min else float(r.min)
    return out
