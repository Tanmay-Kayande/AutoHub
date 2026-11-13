"""Per-page brochure text into ``brochure_pages`` (PyMuPDF), for search with page citations.

Pages with no text layer (scanned or image-only brochures) are skipped; the
version's ``has_text_layer`` flag (set by the downloader) already records that.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from autohub.db.models import BrochurePage, BrochureVersion
from autohub.ingestion.store import BrochureStore

logger = logging.getLogger(__name__)

MIN_PAGE_CHARS = 40
_SPACES = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    lines = [_SPACES.sub(" ", line).strip() for line in text.replace("\r", "\n").split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def extract_pages(path: Path) -> list[tuple[int, str]]:
    """``[(1-based page number, cleaned text)]`` for pages with meaningful text."""
    pages: list[tuple[int, str]] = []
    with pymupdf.open(stream=path.read_bytes(), filetype="pdf") as document:
        for index, page in enumerate(document):
            text = clean_text(str(page.get_text()))
            if len(text) >= MIN_PAGE_CHARS:
                pages.append((index + 1, text))
    return pages


def store_pages(db: Session, version: BrochureVersion, store: BrochureStore) -> int:
    """Insert page text for ``version`` once. Returns the number of pages added."""
    already = db.execute(select(exists().where(BrochurePage.brochure_version_id == version.id))).scalar()
    if already:
        return 0
    path = store.path_for(version.sha256)
    if not path.is_file():
        logger.warning("pdf_text_missing_file version_id=%s", version.id)
        return 0
    try:
        pages = extract_pages(path)
    except Exception as exc:  # a damaged file must not stop the run
        logger.warning("pdf_text_failed version_id=%s error=%s", version.id, exc)
        return 0
    for number, text in pages:
        db.add(
            BrochurePage(
                brochure_version_id=version.id, page_number=number, content=text, char_count=len(text)
            )
        )
    db.flush()
    return len(pages)


def backfill_pages(db: Session, store: BrochureStore) -> int:
    """Extract text for every version that has none yet (text-layer versions only)."""
    versions = db.execute(
        select(BrochureVersion).where(
            BrochureVersion.has_text_layer.is_not(False),
            ~exists().where(BrochurePage.brochure_version_id == BrochureVersion.id),
        )
    ).scalars()
    added = 0
    for version in list(versions):
        added += store_pages(db, version, store)
        db.commit()
    return added
