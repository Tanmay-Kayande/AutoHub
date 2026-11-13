"""Brochure downloader: conditional GET, validation, content-addressed store, versions."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pymupdf
import pytest
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.db.models import Brochure, BrochureVersion
from autohub.ingestion import downloader as downloader_module
from autohub.ingestion import retry as retry_module
from autohub.ingestion import store as store_module
from autohub.ingestion.downloader import (
    BrochureDownloader,
    DownloadStatus,
    PdfRejected,
    inspect_pdf,
)
from autohub.ingestion.fetcher import CrawlPolicy
from autohub.ingestion.store import BrochureStore
from tests.factories import make_brand
from tests.unit.test_fetcher import HOST, FakeClock, FakeResponse, FakeSession

URL = f"{HOST}/brochures/model-q.pdf"
LONG_TEXT = "Testa Model Q specifications: length 4300 mm, width 1800 mm, wheelbase 2650 mm. "


def make_pdf(text: str | None = LONG_TEXT, *, pages: int = 1) -> bytes:
    """A real PDF; ``text=None`` gives pages without a text layer."""
    document = pymupdf.open()
    for _ in range(pages):
        page = document.new_page()
        if text:
            page.insert_textbox(pymupdf.Rect(72, 72, 520, 760), text * 2)
    data: bytes = document.tobytes()
    document.close()
    return data


PDF_A = make_pdf(LONG_TEXT + "Edition A. ")
PDF_B = make_pdf(LONG_TEXT + "Edition B. ", pages=2)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_response(body: bytes, **headers: str) -> FakeResponse:
    return FakeResponse(200, body, headers={"Content-Type": "application/pdf", **headers})


class BrokenBodyResponse(FakeResponse):
    """Sends some bytes, then the connection drops."""

    def iter_content(self, chunk_size: int = 65536) -> Any:
        yield self._body[:100]
        raise requests.exceptions.ChunkedEncodingError("connection broken")


class Env:
    def __init__(
        self,
        db: Session,
        tmp_path: Path,
        routes: dict[str, list[Any]],
        brochure: Brochure | None = None,
        **kwargs: Any,
    ) -> None:
        self.db = db
        self.session = FakeSession(routes)
        self.clock = FakeClock()
        self.store = BrochureStore(tmp_path / "brochures")
        policy = CrawlPolicy(
            session=self.session,
            user_agent="AutoHubBot/test",
            delay_seconds=0.0,
            timeout_seconds=5.0,
            allowed_domains=("testa.example",),
            sleep=self.clock.sleep,
            clock=self.clock.clock,
        )
        self.downloader = BrochureDownloader(
            policy=policy, store=self.store, sleep=self.clock.sleep, clock=self.clock.clock, **kwargs
        )
        if brochure is not None:
            self.brochure = brochure
            return
        brand = make_brand(db)
        self.brochure = Brochure(
            brand_id=brand.id, adapter="testa", source_url=URL, layout_group="model-q", model_hint="Model Q"
        )
        db.add(self.brochure)
        db.flush()

    def download(self) -> Any:
        return self.downloader.download(self.db, self.brochure)

    def versions(self) -> list[BrochureVersion]:
        self.db.expire_all()
        return list(
            self.db.execute(
                select(BrochureVersion)
                .where(BrochureVersion.brochure_id == self.brochure.id)
                .order_by(BrochureVersion.id)
            ).scalars()
        )

    def stored_files(self) -> list[str]:
        return sorted(p.name for p in self.store.root.iterdir()) if self.store.root.exists() else []

    def requests_for(self, url: str = URL) -> list[dict[str, Any]]:
        return [kwargs for called, kwargs in self.session.calls if called == url]


# ---------------------------------------------------------------- acceptance criteria
def test_first_download_stores_file_and_records_version(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        {URL: [pdf_response(PDF_A, ETag='"v1"', **{"Last-Modified": "Mon, 02 Nov 2026 10:00:00 GMT"})]},
    )

    result = env.download()

    assert result.status is DownloadStatus.NEW_VERSION and result.reason is None
    (version,) = env.versions()
    assert result.version is not None and result.version.id == version.id
    assert version.sha256 == sha(PDF_A)
    assert version.file_size == len(PDF_A)
    assert version.file_path == f"{sha(PDF_A)}.pdf"
    assert version.page_count == 1 and version.has_text_layer is True
    assert version.http_etag == '"v1"'
    assert version.http_last_modified == "Mon, 02 Nov 2026 10:00:00 GMT"
    assert env.store.path_for(version.sha256).read_bytes() == PDF_A
    assert env.stored_files() == [f"{sha(PDF_A)}.pdf"]  # no temporary file left behind

    (request,) = env.requests_for()
    assert request["headers"]["User-Agent"] == "AutoHubBot/test"
    assert "If-None-Match" not in request["headers"] and "If-Modified-Since" not in request["headers"]
    assert request["stream"] is True and request["allow_redirects"] is False
    assert "verify" not in request


def test_same_bytes_twice_gives_one_version(db: Session, tmp_path: Path) -> None:
    env = Env(
        db, tmp_path, {URL: [pdf_response(PDF_A, ETag='"v1"'), pdf_response(PDF_A, ETag='"v1-renamed"')]}
    )

    first = env.download()
    second = env.download()

    assert first.status is DownloadStatus.NEW_VERSION
    assert second.status is DownloadStatus.UNCHANGED
    (version,) = env.versions()
    assert second.version is not None and second.version.id == version.id
    assert version.http_etag == '"v1-renamed"'  # validators follow the latest response
    assert env.stored_files() == [f"{sha(PDF_A)}.pdf"]


def test_changed_bytes_give_second_version_and_first_file_is_kept(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, {URL: [pdf_response(PDF_A, ETag='"v1"'), pdf_response(PDF_B, ETag='"v2"')]})

    env.download()
    second = env.download()

    assert second.status is DownloadStatus.NEW_VERSION
    first_version, second_version = env.versions()
    assert (first_version.sha256, second_version.sha256) == (sha(PDF_A), sha(PDF_B))
    assert second_version.page_count == 2
    assert env.store.path_for(sha(PDF_A)).read_bytes() == PDF_A  # history is retained
    assert env.store.path_for(sha(PDF_B)).read_bytes() == PDF_B
    assert env.requests_for()[1]["headers"]["If-None-Match"] == '"v1"'


def test_304_writes_nothing(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        {
            URL: [
                pdf_response(PDF_A, ETag='"v1"', **{"Last-Modified": "Mon, 02 Nov 2026 10:00:00 GMT"}),
                FakeResponse(304),
            ]
        },
    )
    first = env.download()
    assert first.version is not None
    downloaded_at = first.version.downloaded_at
    stored = env.store.path_for(sha(PDF_A))
    modified_ns = stored.stat().st_mtime_ns

    second = env.download()

    assert second.status is DownloadStatus.NOT_MODIFIED
    assert second.version is not None and second.version.id == first.version.id
    (version,) = env.versions()
    assert version.downloaded_at == downloaded_at and version.http_etag == '"v1"'
    assert env.stored_files() == [stored.name] and stored.stat().st_mtime_ns == modified_ns
    headers = env.requests_for()[1]["headers"]
    assert headers["If-None-Match"] == '"v1"'
    assert headers["If-Modified-Since"] == "Mon, 02 Nov 2026 10:00:00 GMT"


def test_last_modified_alone_is_enough_for_a_conditional_request(db: Session, tmp_path: Path) -> None:
    """Mahindra sends ``Last-Modified`` but no ``ETag``."""
    stamp = "Mon, 02 Nov 2026 10:00:00 GMT"
    env = Env(db, tmp_path, {URL: [pdf_response(PDF_A, **{"Last-Modified": stamp}), FakeResponse(304)]})
    env.download()

    assert env.download().status is DownloadStatus.NOT_MODIFIED
    headers = env.requests_for()[1]["headers"]
    assert headers["If-Modified-Since"] == stamp and "If-None-Match" not in headers


@pytest.mark.parametrize(
    ("body", "content_type", "expected"),
    [
        (b"<!DOCTYPE html><html>Not found</html>", "text/html", "not a PDF: the body starts with b'<!DOC'"),
        (b"", "application/pdf", "the body is empty"),
        (b"%PD", "application/pdf", "not a PDF"),
        (b"%PDF-1.7\nthis is not really a pdf\n", "application/pdf", "PDF"),
    ],
)
def test_non_pdf_is_rejected_with_reason(
    db: Session, tmp_path: Path, body: bytes, content_type: str, expected: str
) -> None:
    env = Env(db, tmp_path, {URL: [FakeResponse(200, body, headers={"Content-Type": content_type})]})

    result = env.download()

    assert result.status is DownloadStatus.REJECTED and result.stage == "validate"
    assert result.reason is not None and expected in result.reason
    assert result.version is None
    assert env.versions() == [] and env.stored_files() == []
    assert len(env.requests_for()) == 1  # a rejection is not retried


def test_html_error_page_reason_names_the_content_type(db: Session, tmp_path: Path) -> None:
    env = Env(
        db, tmp_path, {URL: [FakeResponse(200, b"<html>login</html>", headers={"Content-Type": "text/html"})]}
    )
    reason = env.download().reason
    assert reason is not None and "Content-Type: text/html" in reason


# ------------------------------------------------------------------------ edge cases
def test_content_reverting_to_an_earlier_version_reuses_that_row(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        {
            URL: [
                pdf_response(PDF_A, ETag='"a"'),
                pdf_response(PDF_B, ETag='"b"'),
                pdf_response(PDF_A, ETag='"a2"'),
                FakeResponse(304),
            ]
        },
    )
    env.download()
    env.download()

    reverted = env.download()
    assert reverted.status is DownloadStatus.UNCHANGED
    version_a, version_b = env.versions()
    assert reverted.version is not None and reverted.version.id == version_a.id
    assert version_a.downloaded_at > version_b.downloaded_at

    # The reverted content is now current: its validators are the ones sent next.
    last = env.download()
    assert last.status is DownloadStatus.NOT_MODIFIED
    assert last.version is not None and last.version.id == version_a.id
    assert env.requests_for()[3]["headers"]["If-None-Match"] == '"a2"'


def test_missing_stored_file_forces_a_full_download(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, {URL: [pdf_response(PDF_A, ETag='"v1"')]})
    env.download()
    env.store.path_for(sha(PDF_A)).unlink()

    result = env.download()

    assert result.status is DownloadStatus.UNCHANGED
    assert "If-None-Match" not in env.requests_for()[1]["headers"]
    assert env.store.path_for(sha(PDF_A)).read_bytes() == PDF_A
    assert len(env.versions()) == 1


def test_pdf_without_text_layer_is_stored_and_flagged(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, {URL: [pdf_response(make_pdf(None, pages=3))]})
    result = env.download()
    assert result.status is DownloadStatus.NEW_VERSION
    (version,) = env.versions()
    assert version.page_count == 3 and version.has_text_layer is False


def test_oversized_file_is_rejected(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, {URL: [pdf_response(PDF_A)]}, max_bytes=len(PDF_A) - 1)
    result = env.download()
    assert result.status is DownloadStatus.REJECTED
    assert result.reason is not None and "limit" in result.reason
    assert env.versions() == [] and env.stored_files() == []


def test_oversized_content_length_is_rejected_before_reading(db: Session, tmp_path: Path) -> None:
    response = pdf_response(PDF_A, **{"Content-Length": "999999999999"})
    env = Env(db, tmp_path, {URL: [response]})
    result = env.download()
    assert result.status is DownloadStatus.REJECTED
    assert result.reason is not None and "Content-Length" in result.reason
    assert env.stored_files() == []


@pytest.mark.parametrize(("status", "message"), [(404, "HTTP 404"), (403, "HTTP 403"), (304, "HTTP 304")])
def test_error_status_is_a_failure_without_retry(
    db: Session, tmp_path: Path, status: int, message: str
) -> None:
    env = Env(db, tmp_path, {URL: [FakeResponse(status)]})
    result = env.download()
    assert result.status is DownloadStatus.FAILED and result.stage == "fetch"
    assert result.reason is not None and message in result.reason
    assert len(env.requests_for()) == 1 and env.versions() == []


def test_transient_status_is_retried_with_backoff(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, {URL: [FakeResponse(503), pdf_response(PDF_A)]})
    assert env.download().status is DownloadStatus.NEW_VERSION
    assert len(env.requests_for()) == 2 and len(env.clock.sleeps) == 1


def test_connection_dropped_mid_body_is_retried(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, {URL: [BrokenBodyResponse(200, PDF_A), pdf_response(PDF_A)]})
    result = env.download()
    assert result.status is DownloadStatus.NEW_VERSION
    assert env.store.path_for(sha(PDF_A)).read_bytes() == PDF_A
    assert env.stored_files() == [f"{sha(PDF_A)}.pdf"]  # the partial file was removed


def test_persistently_broken_body_fails_after_bounded_attempts(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, {URL: [BrokenBodyResponse(200, PDF_A)]})
    result = env.download()
    assert result.status is DownloadStatus.FAILED and result.stage == "fetch"
    assert result.reason is not None and "gave up after 3 attempts" in result.reason
    assert len(env.requests_for()) == 3 and env.stored_files() == [] and env.versions() == []


def test_stalled_download_hits_the_deadline(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(downloader_module, "DOWNLOAD_DEADLINE_SECONDS", -1.0)
    env = Env(db, tmp_path, {URL: [pdf_response(PDF_A)]})
    result = env.download()
    assert result.status is DownloadStatus.FAILED
    assert result.reason is not None and "not finished" in result.reason
    assert env.stored_files() == []


def test_tls_failure_is_reported_not_bypassed(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, {URL: [requests.exceptions.SSLError("certificate has expired")]})
    result = env.download()
    assert result.status is DownloadStatus.FAILED and result.stage == "tls"
    assert all("verify" not in kwargs for _, kwargs in env.session.calls)


def test_no_module_can_disable_certificate_verification() -> None:
    for module in (downloader_module, retry_module, store_module):
        source = Path(str(module.__file__)).read_text(encoding="utf-8")
        assert "verify=" not in source


@pytest.mark.parametrize(
    ("url", "stage"),
    [
        ("https://evil.example/brochure.pdf", "scope"),
        ("http://www.testa.example/brochure.pdf", "scope"),
        (f"{HOST}/private/brochure.pdf", "robots"),
    ],
)
def test_out_of_policy_urls_are_never_requested(db: Session, tmp_path: Path, url: str, stage: str) -> None:
    env = Env(db, tmp_path, {url: [pdf_response(PDF_A)]})
    env.brochure.source_url = url
    result = env.download()
    assert result.status is DownloadStatus.FAILED and result.stage == stage
    assert url not in env.session.urls() and env.stored_files() == []


def test_redirect_is_followed_only_inside_the_official_domains(db: Session, tmp_path: Path) -> None:
    moved = f"{HOST}/cdn/model-q.pdf"
    env = Env(
        db, tmp_path, {URL: [FakeResponse(302, headers={"Location": moved})], moved: [pdf_response(PDF_A)]}
    )
    assert env.download().status is DownloadStatus.NEW_VERSION

    other = Env(
        db,
        tmp_path / "other",
        {URL: [FakeResponse(302, headers={"Location": "https://evil.example/x.pdf"})]},
        brochure=env.brochure,
    )
    result = other.download()
    assert result.status is DownloadStatus.FAILED and result.stage == "scope"
    assert all("evil.example" not in url for url in other.session.urls())


def test_requests_to_one_host_are_spaced(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, {URL: [pdf_response(PDF_A, ETag='"v1"'), FakeResponse(304)]})
    env.downloader._policy.delay_seconds = 2.0
    env.download()
    env.download()
    assert sum(env.clock.sleeps) >= 4.0  # robots.txt, first GET, second GET


def test_two_brochures_with_the_same_file_share_one_stored_copy(db: Session, tmp_path: Path) -> None:
    other_url = f"{HOST}/brochures/model-q-copy.pdf"
    env = Env(db, tmp_path, {URL: [pdf_response(PDF_A)], other_url: [pdf_response(PDF_A)]})
    second = Brochure(
        brand_id=env.brochure.brand_id, adapter="testa", source_url=other_url, layout_group="model-q-copy"
    )
    db.add(second)
    db.flush()

    assert env.download().status is DownloadStatus.NEW_VERSION
    assert env.downloader.download(db, second).status is DownloadStatus.NEW_VERSION
    assert env.stored_files() == [f"{sha(PDF_A)}.pdf"]
    assert (
        db.execute(select(BrochureVersion).where(BrochureVersion.sha256 == sha(PDF_A)))
        .scalars()
        .all()
        .__len__()
        == 2
    )


# ------------------------------------------------------------------- store / inspect
def test_store_rejects_keys_that_are_not_sha256(tmp_path: Path) -> None:
    store = BrochureStore(tmp_path)
    for bad in ("../../etc/passwd", "abc", sha(b"x").upper(), sha(b"x") + "/x"):
        with pytest.raises(ValueError):
            store.path_for(bad)


def test_store_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    store = BrochureStore(tmp_path / "s")
    digest = sha(b"original")
    first = store.new_temp_path()
    first.write_bytes(b"original")
    assert store.adopt(first, digest) is True

    second = store.new_temp_path()
    second.write_bytes(b"something else")
    assert store.adopt(second, digest) is False
    assert store.path_for(digest).read_bytes() == b"original"
    assert not second.exists() and [p.name for p in store.root.iterdir()] == [f"{digest}.pdf"]


def test_inspect_pdf_rejects_password_protected_files(tmp_path: Path) -> None:
    path = tmp_path / "locked.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), LONG_TEXT)
    document.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    document.close()
    with pytest.raises(PdfRejected, match="password"):
        inspect_pdf(path)
