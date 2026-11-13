"""Brochure downloader: conditional GET, validation, content-addressed storage, versions.

For one ``brochures`` row :meth:`BrochureDownloader.download`:

1. sends a conditional GET (``If-None-Match`` / ``If-Modified-Since``) when the
   current version's file is still in the store, so an unchanged brochure costs
   one small request;
2. streams the body to a temporary file while hashing it, and rejects anything
   that is not a readable PDF or exceeds the size cap;
3. moves the file into the :class:`~autohub.ingestion.store.BrochureStore` and
   inserts a ``brochure_versions`` row if this content is new for the brochure.

Requests obey the same :class:`~autohub.ingestion.fetcher.CrawlPolicy` as page
fetches (official domains, ``robots.txt``, per-host spacing, verified TLS).

Problems are returned as data (:class:`DownloadResult`), never raised, so one
bad brochure cannot stop a pipeline run. The session is flushed, not committed:
the caller owns the per-brochure transaction. A file stored by a transaction
that is later rolled back is harmless — the next run finds it already present.

The *current* version of a brochure is the one with the latest
``downloaded_at``. If a site reverts to content seen before, that older row's
``downloaded_at`` and HTTP validators are refreshed instead of adding a row.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import pymupdf
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.db.models import Brochure, BrochureVersion
from autohub.ingestion.adapters.base import AdapterError
from autohub.ingestion.fetcher import CrawlPolicy
from autohub.ingestion.retry import MAX_ATTEMPTS, backoff_seconds, get_following_redirects
from autohub.ingestion.store import BrochureStore

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
MAX_PDF_BYTES = 100 * 1024 * 1024  # the largest brochure seen so far is ~19 MB
DOWNLOAD_DEADLINE_SECONDS = 600.0  # whole body; the per-read timeout alone allows a slow drip
TEXT_LAYER_MIN_CHARS = 100  # non-whitespace characters across the document
_CHUNK_BYTES = 65536


class DownloadStatus(StrEnum):
    NEW_VERSION = "new_version"  # new content stored and recorded
    UNCHANGED = "unchanged"  # full response, but the bytes match a known version
    NOT_MODIFIED = "not_modified"  # HTTP 304; nothing read or written
    REJECTED = "rejected"  # the response is not an acceptable PDF
    FAILED = "failed"  # the file could not be fetched


@dataclass(frozen=True)
class DownloadResult:
    status: DownloadStatus
    version: BrochureVersion | None = None  # set for the first three statuses
    stage: str | None = None  # scope, robots, tls, fetch, validate or store
    reason: str | None = None


class PdfRejected(ValueError):
    """The file is not a PDF this system can use; ``str(exc)`` is the reason."""


@dataclass(frozen=True)
class PdfInfo:
    page_count: int
    has_text_layer: bool


def inspect_pdf(path: Path) -> PdfInfo:
    """Open a PDF and report its page count and whether it has a text layer.

    Raises :class:`PdfRejected` for files that cannot be opened, are
    password-protected or have no pages.
    """
    try:
        # Opened from memory: on Windows a failed open by path can leave the file
        # locked, and the caller must be able to delete a rejected download.
        data = path.read_bytes()
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass:
                raise PdfRejected("PDF is password-protected")
            page_count = int(document.page_count)
            if page_count < 1:
                raise PdfRejected("PDF has no pages")
            chars = 0
            for page in document:
                chars += len("".join(str(page.get_text()).split()))
                if chars >= TEXT_LAYER_MIN_CHARS:
                    break
    except PdfRejected:
        raise
    except Exception as exc:  # PyMuPDF raises several unrelated types for damaged files
        raise PdfRejected(f"not a readable PDF ({type(exc).__name__}: {exc})") from exc
    return PdfInfo(page_count=page_count, has_text_layer=chars >= TEXT_LAYER_MIN_CHARS)


@dataclass(frozen=True)
class _Received:
    temp_path: Path
    sha256: str
    size: int
    info: PdfInfo
    etag: str | None
    last_modified: str | None


class BrochureDownloader:
    """Downloads brochures for one brand in one pipeline run (shares the run's policy)."""

    def __init__(
        self,
        *,
        policy: CrawlPolicy,
        store: BrochureStore,
        max_bytes: int = MAX_PDF_BYTES,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._store = store
        self._max_bytes = max_bytes
        self._sleep = sleep
        self._clock = clock

    # ------------------------------------------------------------------ public
    def download(self, db: Session, brochure: Brochure) -> DownloadResult:
        url = brochure.source_url
        current = self._current_version(db, brochure.id)
        conditional: dict[str, str] = {}
        # Validators are only sent while the file they describe is still held.
        if current is not None and self._store.exists(current.sha256):
            if current.http_etag:
                conditional["If-None-Match"] = current.http_etag
            if current.http_last_modified:
                conditional["If-Modified-Since"] = current.http_last_modified

        try:
            self._policy.authorize(url)
            received = self._fetch(url, conditional)
        except AdapterError as exc:
            return self._problem(brochure, DownloadStatus.FAILED, exc.stage, exc.message)
        except PdfRejected as exc:
            return self._problem(brochure, DownloadStatus.REJECTED, "validate", str(exc))

        if received is None:
            logger.info("brochure_not_modified brochure_id=%s", brochure.id)
            return DownloadResult(DownloadStatus.NOT_MODIFIED, version=current)

        try:
            self._store.adopt(received.temp_path, received.sha256)
        except OSError as exc:
            received.temp_path.unlink(missing_ok=True)
            return self._problem(brochure, DownloadStatus.FAILED, "store", f"could not store the file: {exc}")

        version = db.execute(
            select(BrochureVersion).where(
                BrochureVersion.brochure_id == brochure.id, BrochureVersion.sha256 == received.sha256
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if version is None:
            version = BrochureVersion(
                brochure_id=brochure.id,
                sha256=received.sha256,
                file_path=self._store.key_for(received.sha256),
                file_size=received.size,
                page_count=received.info.page_count,
                has_text_layer=received.info.has_text_layer,
                http_etag=received.etag,
                http_last_modified=received.last_modified,
                downloaded_at=now,
            )
            db.add(version)
            status = DownloadStatus.NEW_VERSION
        else:
            version.http_etag = received.etag
            version.http_last_modified = received.last_modified
            version.downloaded_at = now
            status = DownloadStatus.UNCHANGED
        db.flush()
        logger.info(
            "brochure_downloaded brochure_id=%s status=%s sha256=%s bytes=%s pages=%s text_layer=%s",
            brochure.id,
            status.value,
            received.sha256,
            received.size,
            received.info.page_count,
            received.info.has_text_layer,
        )
        return DownloadResult(status, version=version)

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _current_version(db: Session, brochure_id: int) -> BrochureVersion | None:
        return (
            db.execute(
                select(BrochureVersion)
                .where(BrochureVersion.brochure_id == brochure_id)
                .order_by(BrochureVersion.downloaded_at.desc(), BrochureVersion.id.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )

    @staticmethod
    def _problem(brochure: Brochure, status: DownloadStatus, stage: str, reason: str) -> DownloadResult:
        logger.warning(
            "brochure_download_%s brochure_id=%s url=%s stage=%s reason=%s",
            status.value,
            brochure.id,
            brochure.source_url,
            stage,
            reason,
        )
        return DownloadResult(status, stage=stage, reason=reason)

    def _fetch(self, url: str, conditional: dict[str, str]) -> _Received | None:
        """Return the validated download, or ``None`` for HTTP 304.

        A connection that breaks while the body is being read is retried from
        the start, with backoff, up to ``MAX_ATTEMPTS`` times.
        """
        last_error = "no attempt made"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            final_url, response = get_following_redirects(
                self._policy, url, headers={"Accept": "application/pdf", **conditional}, sleep=self._sleep
            )
            try:
                status = int(response.status_code)
                if status == 304:
                    if not conditional:
                        raise AdapterError(
                            "fetch", final_url, "HTTP 304 in reply to an unconditional request"
                        )
                    return None
                if status != 200:
                    raise AdapterError("fetch", final_url, f"HTTP {status}")
                try:
                    return self._receive(final_url, response)
                except requests.exceptions.RequestException as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
            finally:
                response.close()
            if attempt < MAX_ATTEMPTS:
                logger.warning("download_retry url=%s attempt=%s reason=%s", url, attempt, last_error)
                self._sleep(backoff_seconds(attempt))
        raise AdapterError("fetch", url, f"gave up after {MAX_ATTEMPTS} attempts: {last_error}")

    def _receive(self, url: str, response: Any) -> _Received:
        """Stream the body to a temporary file in the store, hashing and validating it."""
        headers = response.headers
        content_type = str(headers.get("Content-Type", "")) or "not stated"
        declared = str(headers.get("Content-Length", ""))
        if declared.isdigit() and int(declared) > self._max_bytes:
            raise PdfRejected(f"Content-Length {declared} exceeds the {self._max_bytes}-byte limit")

        deadline = self._clock() + DOWNLOAD_DEADLINE_SECONDS
        digest = hashlib.sha256()
        size = 0
        head = b""
        temp_path = self._store.new_temp_path()
        try:
            with temp_path.open("wb") as out:
                for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                    if not chunk:
                        continue
                    if len(head) < len(PDF_MAGIC):
                        head += chunk[: len(PDF_MAGIC) - len(head)]
                        if len(head) == len(PDF_MAGIC) and head != PDF_MAGIC:
                            break  # not a PDF: stop reading the rest
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise PdfRejected(f"file exceeds the {self._max_bytes}-byte limit")
                    if self._clock() > deadline:
                        raise AdapterError(
                            "fetch", url, f"download not finished after {DOWNLOAD_DEADLINE_SECONDS:.0f} s"
                        )
                    digest.update(chunk)
                    out.write(chunk)
            if head != PDF_MAGIC:
                raise PdfRejected(
                    f"not a PDF: the body starts with {head!r} (Content-Type: {content_type})"
                    if head
                    else f"not a PDF: the body is empty (Content-Type: {content_type})"
                )
            info = inspect_pdf(temp_path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        return _Received(
            temp_path=temp_path,
            sha256=digest.hexdigest(),
            size=size,
            info=info,
            etag=headers.get("ETag") or None,
            last_modified=headers.get("Last-Modified") or None,
        )
