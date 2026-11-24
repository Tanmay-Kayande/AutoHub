from __future__ import annotations

import os
from pathlib import Path

import pymupdf

from autohub.ingestion.pdf_split import split_pdf


def noisy_pdf(path: Path, pages: int, side: int = 60) -> Path:
    """Each page carries its own random (incompressible) image, about side*side*3 bytes."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, side, side, os.urandom(side * side * 3), False)
        page.insert_image(pymupdf.Rect(0, 0, 200, 200), pixmap=pix)
    doc.save(path)
    doc.close()
    return path


def page_size(path: Path) -> int:
    parts = split_pdf(path, 10**9, path.parent / "probe")
    assert parts is not None
    return parts[0].path.stat().st_size


def test_pages_are_packed_into_consecutive_ranges_under_the_limit(tmp_path: Path) -> None:
    source = noisy_pdf(tmp_path / "b.pdf", 5)
    one = noisy_pdf(tmp_path / "one.pdf", 1)
    (tmp_path / "probe").mkdir()
    limit = int(page_size(one) * 2.5)  # two pages fit, three do not
    out = tmp_path / "out"
    out.mkdir()

    parts = split_pdf(source, limit, out)

    assert parts is not None
    assert [(p.first_page, p.last_page) for p in parts] == [(1, 2), (3, 4), (5, 5)]
    for part in parts:
        assert part.path.parent == out and part.path.stat().st_size <= limit
        with pymupdf.open(part.path) as doc:
            assert doc.page_count == part.page_count


def test_a_page_over_the_limit_cannot_be_split(tmp_path: Path) -> None:
    source = noisy_pdf(tmp_path / "b.pdf", 2)
    assert split_pdf(source, 1000, tmp_path) is None


def test_a_file_that_is_not_a_pdf_cannot_be_split(tmp_path: Path) -> None:
    source = tmp_path / "b.pdf"
    source.write_bytes(b"%PDF-1.7 fake brochure bytes")
    assert split_pdf(source, 10**6, tmp_path) is None
