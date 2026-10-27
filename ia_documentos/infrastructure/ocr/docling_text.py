"""
infrastructure/ocr/docling_text.py

Exporta un `DoclingDocument` a texto plano. Por defecto es EXACTAMENTE
`document.export_to_markdown()` (comportamiento historico). Con flags:

  traverse_pictures=True -> `export_to_markdown(traverse_pictures=True)`: el texto que el OCR
      leyo DENTRO de regiones que el layout clasifico como figura deja de perderse (en
      pasaportes/INE/CURP el layout a 72 dpi suele ver toda la tarjeta como una sola
      "picture" y el markdown normal solo emite `<!-- image -->`).
  modo="filas" -> ignora el markdown y reconstruye el texto a partir de los items de texto
      (incluidos los hijos de pictures) ordenados por posicion: un renglon por fila visual
      (agrupando por solape vertical de los bbox) y de izquierda a derecha.

Sin dependencias de Docling en el modulo: trabaja con el objeto recibido (los tests usan
dobles simples).
"""
from __future__ import annotations

from typing import Any, Optional


def _bbox_arriba(item: Any, doc: Any) -> Optional[tuple[int, float, float, float, float]]:
    """(pagina, izq, arriba, der, abajo) con origen arriba-izquierda, o None sin posicion."""
    prov = getattr(item, "prov", None)
    if not prov:
        return None
    p = prov[0]
    bb = p.bbox
    try:
        origen = getattr(getattr(bb, "coord_origin", None), "value", str(getattr(bb, "coord_origin", "")))
        if "BOTTOM" in str(origen).upper():
            alto = doc.pages[p.page_no].size.height
            bb = bb.to_top_left_origin(page_height=alto)
        izq, arr, der, aba = bb.l, bb.t, bb.r, bb.b
    except Exception:
        return None
    return (p.page_no, float(izq), float(min(arr, aba)), float(der), float(max(arr, aba)))


def _cajas_de_tablas(doc: Any) -> list[tuple[int, float, float, float, float, str]]:
    """Celdas de las tablas de Docling como cajas (pagina, izq, arriba, der, abajo, texto).

    Con el OCR Tesseract el layout suele clasificar bloques de formulario (p. ej. la filiacion del acta)
    como tabla: su texto vive en las celdas y no en `doc.texts`, y `texto_por_filas` lo perderia.
    """
    cajas: list[tuple[int, float, float, float, float, str]] = []
    for tabla in getattr(doc, "tables", []) or []:
        prov = getattr(tabla, "prov", None)
        data = getattr(tabla, "data", None)
        if not prov or data is None:
            continue
        pagina = prov[0].page_no
        for celda in getattr(data, "table_cells", []) or []:
            txt = (getattr(celda, "text", "") or "").strip()
            bb = getattr(celda, "bbox", None)
            if not txt or bb is None:
                continue
            try:
                origen = getattr(getattr(bb, "coord_origin", None), "value", str(getattr(bb, "coord_origin", "")))
                if "BOTTOM" in str(origen).upper():
                    bb = bb.to_top_left_origin(page_height=doc.pages[pagina].size.height)
                cajas.append((pagina, float(bb.l), float(min(bb.t, bb.b)), float(bb.r), float(max(bb.t, bb.b)), txt))
            except Exception:
                continue
    return cajas


def texto_por_filas(doc: Any, incluir_tablas: bool = False) -> str:
    """Texto de todos los items de texto, un renglon por fila visual, por pagina.

    `incluir_tablas` agrega tambien el texto de las celdas de las tablas (ver `_cajas_de_tablas`).
    """
    cajas: list[tuple[int, float, float, float, float, str]] = []
    sin_pos: list[str] = []
    for item in getattr(doc, "texts", []):
        txt = (getattr(item, "text", "") or "").strip()
        if not txt:
            continue
        b = _bbox_arriba(item, doc)
        if b is None:
            sin_pos.append(txt)
        else:
            cajas.append((*b, txt))
    if incluir_tablas:
        cajas.extend(_cajas_de_tablas(doc))
    paginas: dict[int, list] = {}
    for c in cajas:
        paginas.setdefault(c[0], []).append(c)
    salida: list[str] = []
    for pg in sorted(paginas):
        cs = sorted(paginas[pg], key=lambda c: ((c[2] + c[4]) / 2, c[1]))
        filas: list[list] = []
        for c in cs:
            centro = (c[2] + c[4]) / 2
            if filas:
                ult = filas[-1]
                top = min(x[2] for x in ult)
                bot = max(x[4] for x in ult)
                alto_c = max(c[4] - c[2], 1e-6)
                # misma fila si el centro cae dentro de la banda vertical de la fila
                if top - 0.25 * alto_c <= centro <= bot + 0.25 * alto_c:
                    ult.append(c)
                    continue
            filas.append([c])
        salida.extend(" ".join(x[5] for x in sorted(f, key=lambda x: x[1])) for f in filas)
        salida.append("")
    salida.extend(sin_pos)
    return "\n".join(salida).strip("\n")


def exportar(doc: Any, traverse_pictures: bool = False, modo: str = "markdown", incluir_tablas: bool = False) -> str:
    if modo == "filas":
        return texto_por_filas(doc, incluir_tablas)
    if traverse_pictures:
        return doc.export_to_markdown(traverse_pictures=True)
    return doc.export_to_markdown()
