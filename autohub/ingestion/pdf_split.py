"""Split a PDF that is too large to attach into consecutive page ranges that each fit.

Used for image-only brochures above the provider's upload limit (Maruti Brezza 53 MB,
Ertiga 38 MB): with no text layer there is no page text to send instead, so each range
is attached in its own request. Pages are copied as they are, never re-encoded.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass(frozen=True)
class PdfPart:
    path: Path
    first_page: int  # 1-based page of the whole document
    last_page: int

    @property
    def page_count(self) -> int:
        return self.last_page - self.first_page + 1


def _range_bytes(doc: pymupdf.Document, start: int, end: int) -> bytes:
    """Pages ``start``..``end`` (0-based, inclusive) saved as a standalone PDF."""
    part = pymupdf.open()
    try:
        part.insert_pdf(doc, from_page=start, to_page=end)
        return bytes(part.tobytes(garbage=4, deflate=True))
    finally:
        part.close()


def split_pdf(path: Path, limit: int, out_dir: Path) -> list[PdfPart] | None:
    """Consecutive page ranges, each saved in ``out_dir`` at no more than ``limit`` bytes.

    Each range takes as many pages as fit, measured on the saved part (pages share fonts
    and images, so their sizes do not simply add up). Returns None when the file is not a
    readable PDF or one page alone is over the limit.
    """
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError):
        return None
    ranges: list[tuple[int, int, bytes]] = []
    try:
        start = 0
        while start < doc.page_count:
            data = _range_bytes(doc, start, start)
            if len(data) > limit:
                return None
            end = start
            while end + 1 < doc.page_count:
                bigger = _range_bytes(doc, start, end + 1)
                if len(bigger) > limit:
                    break
                end, data = end + 1, bigger
            ranges.append((start, end, data))
            start = end + 1
    finally:
        doc.close()
    if not ranges:
        return None
    parts = []
    for number, (start, end, data) in enumerate(ranges, 1):
        part_path = out_dir / f"part{number:02d}_p{start + 1}-{end + 1}.pdf"
        part_path.write_bytes(data)
        parts.append(PdfPart(path=part_path, first_page=start + 1, last_page=end + 1))
    return parts
