"""T1 Layouts: LayoutSpec reales de los 4 documentos + motor de render (reportlab).

API pública:
    load_spec(doc_type) -> LayoutSpec
    render_document(spec, values=None, variant=None, watermark=True) -> bytes (PDF)
    render_template(spec, face, values=None, variant=None, watermark=True) -> bytes (PDF de una cara)
    draw_avatar(canvas, x, y, w, h, avatar_id, faded=False)
    draw_signature(canvas, x, y, w, h, signature_id)
    draw_shape(canvas, x, y, w, h, desc)
    draw_watermark(canvas, width_mm, height_mm)
    placeholder_value(doc_type, field)

`watermark=False` existe sólo para pruebas internas: PROHIBIDO en producción.
"""
from .render import (
    FONT_MAP,
    SPECS_DIR,
    draw_watermark,
    load_spec,
    placeholder_value,
    render_document,
    render_template,
)
from .shapes import draw_avatar, draw_shape, draw_signature

__all__ = [
    "FONT_MAP",
    "SPECS_DIR",
    "draw_avatar",
    "draw_shape",
    "draw_signature",
    "draw_watermark",
    "load_spec",
    "placeholder_value",
    "render_document",
    "render_template",
]
