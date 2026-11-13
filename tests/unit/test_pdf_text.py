"""Per-page text extraction from a generated PDF."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from autohub.ingestion.pdf_text import clean_text, extract_pages


def test_pages_keep_numbers_and_skip_blank_pages(tmp_path: Path) -> None:
    document = pymupdf.open()
    first = document.new_page()
    first.insert_textbox(pymupdf.Rect(72, 72, 520, 760), "Ground clearance 209 mm. Wheelbase 2750 mm. " * 3)
    document.new_page()  # image-only / blank page
    third = document.new_page()
    third.insert_textbox(pymupdf.Rect(72, 72, 520, 760), "Six airbags as standard across all variants.")
    path = tmp_path / "b.pdf"
    document.save(str(path))
    document.close()

    pages = extract_pages(path)
    assert [n for n, _ in pages] == [1, 3]
    assert "Ground clearance 209 mm" in pages[0][1]


def test_clean_text_collapses_whitespace() -> None:
    assert clean_text("A    B\r\n\n\n\n C ") == "A B\n\nC"
