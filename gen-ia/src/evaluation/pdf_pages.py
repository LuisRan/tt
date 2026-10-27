"""Selección de páginas del PDF que se envía al OCR (INE: solo anverso)."""
from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

from contracts import DocType
from contracts.common import Face
from contracts.ground_truth import GroundTruth


def front_page_indexes(gt: GroundTruth) -> list[int]:
    return sorted(p.page_index for p in gt.pages if p.face == Face.ANVERSO)


def prepare_submitted_pdf(gt: GroundTruth, source_pdf: Path, dest_pdf: Path, ine_pages: str) -> bool:
    """Escribe `dest_pdf` solo si hay que recortar páginas. Devuelve True si lo escribió
    (INE con ine_pages='front' y reverso presente); False si se envía el PDF tal cual."""
    if gt.doc_type != DocType.INE_2016 or ine_pages != "front":
        return False
    keep = front_page_indexes(gt)
    if not keep:
        raise ValueError(f"{gt.sample_id}: el GT no tiene página de anverso")
    if len(keep) == len(gt.pages):
        return False
    src = pdfium.PdfDocument(str(source_pdf))
    try:
        if max(keep) >= len(src):
            raise ValueError(f"{gt.sample_id}: page_index fuera de rango en el PDF")
        out = pdfium.PdfDocument.new()
        try:
            out.import_pages(src, keep)
            dest_pdf.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest_pdf.with_suffix(".tmp")
            out.save(str(tmp))
        finally:
            out.close()
        tmp.replace(dest_pdf)
    finally:
        src.close()
    return True
