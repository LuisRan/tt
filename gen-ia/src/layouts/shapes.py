"""Primitivas de dibujo procedurales y deterministas (reportlab).

Todas las funciones públicas reciben coordenadas en PUNTOS con el origen
abajo-izquierda de reportlab (x, y = esquina inferior izquierda del recuadro).

- `draw_avatar`: silueta genérica de alto contraste (círculo + trapecio).
- `draw_signature`: trazo bezier sembrado con el id.
- `draw_shape`: figuras sustitutas / decoración plana descritas en el
  `static_value` de un campo SHAPE: "<figura> [clave=valor ...] | descripción".

Nada aquí reproduce elementos de seguridad: sólo figuras geométricas lisas y
tintes planos.
"""
from __future__ import annotations

import hashlib
import math
import random

from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas


def seeded_rng(key: str) -> random.Random:
    """RNG determinista derivado de un id textual (independiente de PYTHONHASHSEED)."""
    return random.Random(int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big"))


def _rgb(c: tuple[int, int, int]) -> tuple[float, float, float]:
    return (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)


def _clip(c: Canvas, x: float, y: float, w: float, h: float) -> None:
    p = c.beginPath()
    p.rect(x, y, w, h)
    c.clipPath(p, stroke=0, fill=0)


# ------------------------------------------------------------------ avatar

_AVATAR_BG = [(228, 228, 228), (214, 226, 240), (240, 232, 214), (226, 240, 226), (240, 222, 222)]
_AVATAR_FG = [(22, 22, 22), (30, 40, 70), (55, 30, 25), (20, 50, 35)]


def draw_avatar(c: Canvas, x: float, y: float, w: float, h: float, avatar_id: str, faded: bool = False) -> None:
    """Avatar genérico procedural (nunca una foto) en el recuadro (x, y, w, h) en pt.

    Mismo `avatar_id` => mismo dibujo. `faded=True` produce una versión clara en
    gris (para la imagen secundaria del documento).
    """
    rng = seeded_rng("avatar|" + avatar_id)
    bg = rng.choice(_AVATAR_BG)
    fg = rng.choice(_AVATAR_FG)
    hair = tuple(min(255, v + 70) for v in fg)
    if rng.random() < 0.35:  # variante invertida (fondo oscuro, silueta clara)
        bg, fg, hair = fg, (245, 245, 245), (190, 190, 190)
    if faded:
        bg, fg, hair = (238, 238, 238), (170, 170, 170), (205, 205, 205)
    c.saveState()
    _clip(c, x, y, w, h)
    c.setFillColorRGB(*_rgb(bg))
    c.rect(x, y, w, h, stroke=0, fill=1)
    s = min(w, h * 0.75)
    cx = x + w * (0.5 + rng.uniform(-0.04, 0.04))
    head_r = s * rng.uniform(0.21, 0.26)
    head_cy = y + h * rng.uniform(0.58, 0.64)
    # hombros: trapecio
    top_y = head_cy - head_r * rng.uniform(1.25, 1.45)
    top_half = s * rng.uniform(0.20, 0.27)
    bot_half = w * rng.uniform(0.44, 0.52)
    c.setFillColorRGB(*_rgb(fg))
    p = c.beginPath()
    p.moveTo(cx - bot_half, y - 1)
    p.lineTo(cx + bot_half, y - 1)
    p.lineTo(cx + top_half, top_y)
    p.lineTo(cx - top_half, top_y)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    # cuello
    c.rect(cx - head_r * 0.42, top_y - 1, head_r * 0.84, head_cy - top_y, stroke=0, fill=1)
    # cabello (casquete) en tono secundario, opcional
    if rng.random() < 0.7:
        c.setFillColorRGB(*_rgb(hair))
        c.circle(cx, head_cy + head_r * 0.12, head_r * 1.12, stroke=0, fill=1)
    c.setFillColorRGB(*_rgb(fg))
    c.circle(cx, head_cy, head_r, stroke=0, fill=1)
    c.restoreState()


# ------------------------------------------------------------------ firma

def draw_signature(c: Canvas, x: float, y: float, w: float, h: float, signature_id: str) -> None:
    """Garabato sintético: cadena de curvas bezier sembradas con `signature_id`."""
    rng = seeded_rng("firma|" + signature_id)
    color = rng.choice([(15, 15, 15), (20, 30, 95), (10, 20, 60)])
    c.saveState()
    _clip(c, x, y, w, h)
    c.setStrokeColorRGB(*_rgb(color))
    c.setLineCap(1)
    c.setLineJoin(1)
    c.setLineWidth(max(0.35, h * rng.uniform(0.018, 0.03)))
    n_strokes = rng.randint(1, 2)
    left = x + w * rng.uniform(0.05, 0.15)
    right = x + w * rng.uniform(0.75, 0.95)
    for k in range(n_strokes):
        seg = rng.randint(3, 6)
        sx = left + (right - left) * (k / n_strokes)
        ex = left + (right - left) * ((k + 1) / n_strokes)
        p = c.beginPath()
        px, py = sx, y + h * rng.uniform(0.3, 0.7)
        p.moveTo(px, py)
        for i in range(seg):
            nx = sx + (ex - sx) * (i + 1) / seg
            ny = y + h * rng.uniform(0.2, 0.8)
            c1 = (px + rng.uniform(-0.1, 0.4) * w / seg, y + h * rng.uniform(0.05, 0.95))
            c2 = (nx - rng.uniform(-0.1, 0.4) * w / seg, y + h * rng.uniform(0.05, 0.95))
            p.curveTo(c1[0], c1[1], c2[0], c2[1], nx, ny)
            px, py = nx, ny
        c.drawPath(p, stroke=1, fill=0)
    if rng.random() < 0.5:  # rúbrica inferior
        p = c.beginPath()
        yy = y + h * rng.uniform(0.15, 0.3)
        p.moveTo(left, yy)
        p.curveTo(left + w * 0.3, yy - h * 0.1, right - w * 0.3, yy + h * 0.15, right, yy + h * rng.uniform(-0.05, 0.1))
        c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


# ------------------------------------------------------------------ figuras

def parse_shape(desc: str) -> tuple[str, dict[str, str]]:
    """'circle stroke=1,2,3 | texto' -> ('circle', {'stroke': '1,2,3'})."""
    head = desc.split("|", 1)[0].strip()
    tokens = head.split()
    if not tokens:
        return "rect", {}
    params = {}
    for t in tokens[1:]:
        if "=" in t:
            k, v = t.split("=", 1)
            params[k] = v
    return tokens[0], params


def _color(params: dict[str, str], key: str, default):
    if key not in params:
        return default
    return tuple(int(v) for v in params[key].split(","))


FLAG_COLORS = [(0, 104, 71), (255, 255, 255), (206, 17, 38)]


def draw_shape(c: Canvas, x: float, y: float, w: float, h: float, desc: str) -> None:
    """Dibuja la figura descrita por `desc` en el recuadro (x, y, w, h) en pt."""
    kind, prm = parse_shape(desc)
    stroke = _color(prm, "stroke", (90, 90, 90))
    fill = _color(prm, "fill", None)
    c.saveState()
    c.setStrokeColorRGB(*_rgb(stroke))
    c.setLineWidth(float(prm["width"]) * mm if "width" in prm else min(1.0, max(0.4, min(w, h) * 0.025)))
    if kind == "circle":
        r = min(w, h) / 2
        cx, cy = x + w / 2, y + h / 2
        c.circle(cx, cy, r * 0.97, stroke=1, fill=0)
        c.circle(cx, cy, r * 0.75, stroke=1, fill=0)
    elif kind == "hexagon":
        cx, cy = x + w / 2, y + h / 2
        p = c.beginPath()
        for i in range(6):
            a = math.pi / 6 + i * math.pi / 3
            px, py = cx + (w / 2) * math.cos(a), cy + (h / 2) * math.sin(a)
            (p.moveTo if i == 0 else p.lineTo)(px, py)
        p.close()
        c.drawPath(p, stroke=1, fill=0)
    elif kind == "rect":
        if fill is not None:
            c.setFillColorRGB(*_rgb(fill))
        c.rect(x, y, w, h, stroke=1, fill=1 if fill is not None else 0)
    elif kind == "fill":
        c.setFillColorRGB(*_rgb(_color(prm, "color", (230, 230, 230))))
        c.rect(x, y, w, h, stroke=0, fill=1)
    elif kind == "header_band":
        # Banda superior: bordes laterales a 2/3 de la altura, borde inferior curvo
        # cuyo punto más bajo (centro) toca el fondo del recuadro.
        c.setFillColorRGB(*_rgb(_color(prm, "color", (160, 150, 140))))
        side_y = y + h * 0.34
        bottom = y
        cp_y = (4 * bottom - side_y) / 3
        p = c.beginPath()
        p.moveTo(x, y + h)
        p.lineTo(x + w, y + h)
        p.lineTo(x + w, side_y)
        p.curveTo(x + w * 2 / 3, cp_y, x + w / 3, cp_y, x, side_y)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
    elif kind == "flag3":
        sw = w / 3
        for i, col in enumerate(FLAG_COLORS):
            c.setFillColorRGB(*_rgb(col))
            c.rect(x + i * sw, y, sw, h, stroke=0, fill=1)
        c.setLineWidth(0.3)
        c.setStrokeColorRGB(0.6, 0.6, 0.6)
        c.rect(x, y, w, h, stroke=1, fill=0)
    elif kind == "boxes":
        n = int(prm.get("n", 2))
        split = float(prm.get("split", 1.0 / n))
        widths = [w * split] + [w * (1 - split) / (n - 1)] * (n - 1) if n > 1 else [w]
        c.setLineWidth(0.4)
        cx = x
        for bw in widths:
            c.rect(cx + 0.4 * mm, y + 0.3 * mm, bw - 0.8 * mm, h * 0.62, stroke=1, fill=0)
            cx += bw
    elif kind == "oval":
        c.ellipse(x + w * 0.05, y + h * 0.03, x + w * 0.95, y + h * 0.97, stroke=1, fill=0)
    elif kind == "chip":
        c.rect(x, y, w, h, stroke=1, fill=0)
        c.circle(x + w / 2, y + h / 2, min(w, h) * 0.28, stroke=1, fill=0)
    elif kind == "frame":
        inset = float(prm.get("inset", 1.5)) * mm
        c.rect(x, y, w, h, stroke=1, fill=0)
        c.rect(x + inset, y + inset, w - 2 * inset, h - 2 * inset, stroke=1, fill=0)
    elif kind == "hline":
        c.setLineWidth(max(0.4, h * 0.6))
        c.line(x, y + h / 2, x + w, y + h / 2)
    elif kind == "grid":
        rows, cols = int(prm.get("rows", 2)), int(prm.get("cols", 2))
        c.setLineWidth(0.5)
        c.rect(x, y, w, h, stroke=1, fill=0)
        for i in range(1, rows):
            c.line(x, y + h * i / rows, x + w, y + h * i / rows)
        for j in range(1, cols):
            c.line(x + w * j / cols, y, x + w * j / cols, y + h)
    elif kind == "stripes":
        rng = seeded_rng("stripes|" + prm.get("seed", "0"))
        c.setFillColorRGB(0.1, 0.1, 0.1)
        cx = x
        while cx < x + w:
            bw = rng.uniform(0.15, 0.55) * mm
            if cx + bw > x + w:
                break
            c.rect(cx, y, bw, h, stroke=0, fill=1)
            cx += bw + rng.uniform(0.15, 0.5) * mm
    else:
        c.rect(x, y, w, h, stroke=1, fill=0)
    c.restoreState()
