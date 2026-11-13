"""Image search seam: protocol, SerpApi (Google Images) implementation and a fake.

V1 kept only the image URL; this keeps what is needed to judge a result later:
source page, domain, size, title and thumbnail.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from autohub.core.settings import Settings, get_settings
from autohub.images.urls import domain_of

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"
_RETRYABLE = frozenset({429, 500, 502, 503, 504})


class ImageSearchError(Exception):
    pass


class ImageQuotaError(ImageSearchError):
    """The search account is out of credits or rate-limited after retries."""


@dataclass(frozen=True)
class ImageCandidate:
    image_url: str
    thumbnail_url: str | None
    source_page_url: str | None
    source_domain: str | None
    title: str | None
    width: int | None
    height: int | None
    position: int


class ImageSearchProvider(Protocol):
    name: str

    def search(self, query: str, *, limit: int) -> list[ImageCandidate]: ...


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_serpapi_results(payload: dict[str, Any], limit: int) -> list[ImageCandidate]:
    candidates: list[ImageCandidate] = []
    for index, item in enumerate(payload.get("images_results") or []):
        if not isinstance(item, dict):
            continue
        url = item.get("original")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        page = item.get("link") if isinstance(item.get("link"), str) else None
        candidates.append(
            ImageCandidate(
                image_url=url,
                thumbnail_url=item.get("thumbnail") if isinstance(item.get("thumbnail"), str) else None,
                source_page_url=page,
                source_domain=domain_of(page) if page else domain_of(url),
                title=item.get("title") if isinstance(item.get("title"), str) else None,
                width=_int(item.get("original_width")),
                height=_int(item.get("original_height")),
                position=_int(item.get("position")) or index + 1,
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


class SerpApiImageProvider:
    name = "serpapi"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        session: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._session = session or requests.Session()
        self._sleep = sleep

    def search(self, query: str, *, limit: int) -> list[ImageCandidate]:
        params = {"engine": "google_images", "q": query, "gl": "in", "hl": "en", "safe": "active"}
        last = "no attempt"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._session.get(
                    SERPAPI_URL, params={**params, "api_key": self._api_key}, timeout=self._timeout
                )
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last = type(exc).__name__  # the message can echo the URL, which holds the key
            else:
                status = int(response.status_code)
                if status == 200:
                    payload = response.json()
                    if payload.get("error"):
                        message = str(payload["error"])
                        if "run out of searches" in message.lower():
                            raise ImageQuotaError(message)
                        if "hasn't returned any results" in message.lower():
                            return []
                        raise ImageSearchError(message)
                    return parse_serpapi_results(payload, limit)
                if status == 401:
                    raise ImageSearchError("SerpApi rejected the API key (HTTP 401)")
                if status not in _RETRYABLE:
                    raise ImageSearchError(f"SerpApi HTTP {status}")
                last = f"HTTP {status}"
            if attempt < self._max_attempts:
                logger.warning("image_search_retry attempt=%s reason=%s", attempt, last)
                self._sleep(2 ** (attempt - 1) + random.uniform(0, 0.5))
        if last == "HTTP 429":
            raise ImageQuotaError("SerpApi rate limit persisted after retries")
        raise ImageSearchError(f"SerpApi unavailable after {self._max_attempts} attempts: {last}")


class FakeImageSearchProvider:
    name = "fake"

    def __init__(self, results: dict[str, Sequence[ImageCandidate]] | None = None) -> None:
        self.results = dict(results or {})
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int) -> list[ImageCandidate]:
        self.queries.append(query)
        return list(self.results.get(query, []))[:limit]


def get_image_provider(settings: Settings | None = None) -> ImageSearchProvider:
    settings = settings or get_settings()
    return SerpApiImageProvider(
        api_key=settings.require_serpapi_key(), timeout_seconds=settings.http_timeout_seconds
    )
