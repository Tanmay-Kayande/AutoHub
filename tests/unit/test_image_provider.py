"""SerpApi provider (HTTP mocked), filters and the URL validator."""

from __future__ import annotations

from typing import Any

import pytest
import requests

from autohub.images.filters import rejection_reason
from autohub.images.provider import ImageCandidate, ImageQuotaError, ImageSearchError, SerpApiImageProvider
from autohub.images.validator import ImageValidator

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 20000


class Resp:
    def __init__(
        self, status: int, payload: Any = None, *, headers: dict[str, str] | None = None, body: bytes = b""
    ):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self._body = body

    def json(self) -> Any:
        return self._payload

    def iter_content(self, chunk_size: int = 16) -> Any:
        yield self._body[:chunk_size]

    def close(self) -> None:
        pass


class Session:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


RESULTS = {
    "images_results": [
        {
            "position": 1,
            "original": "https://img.example.com/q.jpg",
            "thumbnail": "https://t.example/q",
            "link": "https://www.testa.example/model-q",
            "title": "Testa Model Q exterior",
            "original_width": 1600,
            "original_height": 900,
        },
        {"position": 2, "original": "data:image/png;base64,xx"},
        {"position": 3, "original": "https://img.example.com/r.jpg", "original_width": "bad"},
    ]
}


def provider(responses: list[Any]) -> tuple[SerpApiImageProvider, Session, list[float]]:
    session = Session(responses)
    sleeps: list[float] = []
    return SerpApiImageProvider(api_key="k-secret", session=session, sleep=sleeps.append), session, sleeps


def test_results_keep_metadata_and_skip_bad_urls() -> None:
    p, session, _ = provider([Resp(200, RESULTS)])
    results = p.search("Testa Model Q exterior", limit=5)
    assert [r.image_url for r in results] == [
        "https://img.example.com/q.jpg",
        "https://img.example.com/r.jpg",
    ]
    first = results[0]
    assert (
        first.source_domain == "testa.example"
        and first.width == 1600
        and first.title == "Testa Model Q exterior"
    )
    assert results[1].width is None
    params = session.calls[0][1]["params"]
    assert params["engine"] == "google_images" and params["api_key"] == "k-secret"


def test_retries_then_quota_error() -> None:
    p, session, sleeps = provider([Resp(429), Resp(503), Resp(429)])
    with pytest.raises(ImageQuotaError):
        p.search("q", limit=5)
    assert len(session.calls) == 3 and len(sleeps) == 2


def test_out_of_searches_and_bad_key() -> None:
    p, _, _ = provider([Resp(200, {"error": "Your account has run out of searches."})])
    with pytest.raises(ImageQuotaError):
        p.search("q", limit=5)
    p, _, _ = provider([Resp(401)])
    with pytest.raises(ImageSearchError, match="401"):
        p.search("q", limit=5)
    p, _, _ = provider([Resp(200, {"error": "Google hasn't returned any results for this query."})])
    assert p.search("q", limit=5) == []


def test_connection_error_message_never_contains_the_key() -> None:
    p, _, _ = provider([requests.exceptions.ConnectionError("https://serpapi.com/?api_key=k-secret")] * 3)
    with pytest.raises(ImageSearchError) as info:
        p.search("q", limit=5)
    assert "k-secret" not in str(info.value)


def cand(url: str = "https://img.example.com/a.jpg", **kw: Any) -> ImageCandidate:
    base: dict[str, Any] = dict(
        thumbnail_url=None,
        source_page_url=None,
        source_domain=None,
        title=None,
        width=1200,
        height=800,
        position=1,
    )
    base.update(kw)
    return ImageCandidate(image_url=url, **base)


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (cand(), None),
        (cand("https://i.ytimg.com/vi/x/maxres.jpg"), "blocked host"),
        (cand("https://lookaside.fbsbx.com/x.jpg"), "blocked host"),
        (cand("https://img.example.com/logo.svg"), "not a photo format"),
        (cand(width=300), "too narrow (300px)"),
        (cand(height=200), "too low (200px)"),
        (cand(title="Testa Model R review"), "title names another model (Model R)"),
        (cand(title="Testa Model Q vs Model R"), None),
        (cand(title="Testa Model Q EV interior"), None),
        (cand("ftp://img.example.com/a.jpg"), "not an http(s) URL"),
    ],
)
def test_filters(candidate: ImageCandidate, reason: str | None) -> None:
    assert rejection_reason(candidate, model_name="Model Q", other_models=["Model R", "Model Q"]) == reason


def validator(responses: list[Any], addresses: list[str] | None = None) -> ImageValidator:
    return ImageValidator(session=Session(responses), resolve=lambda host: addresses or ["93.184.216.34"])


@pytest.mark.parametrize(
    ("responses", "status", "note"),
    [
        ([Resp(200, headers={"Content-Type": "image/jpeg"}, body=JPEG)], "valid", None),
        ([Resp(404)], "broken", "HTTP 404"),
        (
            [Resp(200, headers={"Content-Type": "text/html"}, body=b"<html>")],
            "rejected",
            "content type text/html",
        ),
        (
            [Resp(200, headers={"Content-Type": "image/jpeg", "Content-Length": "500"}, body=JPEG)],
            "rejected",
            "only 500 bytes",
        ),
        (
            [Resp(200, headers={"Content-Type": "image/jpeg"}, body=b"<html>nope")],
            "rejected",
            "body is not a recognised image",
        ),
        ([requests.exceptions.ConnectTimeout()], "broken", "ConnectTimeout"),
        (
            [
                Resp(302, headers={"Location": "/real.jpg"}),
                Resp(200, headers={"Content-Type": "image/png"}, body=b"\x89PNG...."),
            ],
            "valid",
            None,
        ),
    ],
)
def test_validator(responses: list[Any], status: str, note: str | None) -> None:
    result = validator(responses).validate("https://img.example.com/a.jpg")
    assert (result.status, result.note) == (status, note)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1"])
def test_validator_refuses_private_addresses(address: str) -> None:
    v = validator([], [address])
    assert v.validate("https://internal.example/a.jpg").status == "rejected"
