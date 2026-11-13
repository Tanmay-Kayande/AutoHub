"""Bounded-retry HTTP GET shared by the page fetcher and the brochure downloader.

Both functions enforce the :class:`~autohub.ingestion.fetcher.CrawlPolicy`
per-host spacing before every attempt, and every redirect hop is held to the
same scope and ``robots.txt`` rules as the first URL. Certificate verification
is never overridden (DECISIONS D-007).
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import requests

from autohub.ingestion.adapters.base import AdapterError

if TYPE_CHECKING:
    from autohub.ingestion.fetcher import CrawlPolicy

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_REDIRECTS = 5
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


def backoff_seconds(attempt: int) -> float:
    """Delay before the attempt after ``attempt`` (1-based): 1 s, 2 s, 4 s … plus jitter."""
    return float(2 ** (attempt - 1)) + random.uniform(0, 0.5)


def get_with_retry(
    policy: CrawlPolicy,
    url: str,
    *,
    headers: Mapping[str, str],
    sleep: Callable[[float], None],
) -> Any:
    """One streamed GET without following redirects; transient failures are retried.

    Returns the open response for any non-retryable status (the caller closes
    it). Raises :class:`AdapterError` with stage ``tls`` or ``fetch``.
    """
    last_error = "no attempt made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        policy.wait_for_host(url)
        try:
            response = policy.session.get(
                url,
                timeout=policy.timeout_seconds,
                headers={"User-Agent": policy.user_agent, **headers},
                stream=True,
                allow_redirects=False,
            )
        except requests.exceptions.SSLError as exc:
            raise AdapterError("tls", url, f"certificate verification failed: {exc}") from exc
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        except requests.exceptions.RequestException as exc:
            raise AdapterError("fetch", url, f"{type(exc).__name__}: {exc}") from exc
        else:
            if int(response.status_code) not in RETRYABLE_STATUS:
                return response
            last_error = f"HTTP {response.status_code}"
            response.close()
        if attempt < MAX_ATTEMPTS:
            logger.warning("fetch_retry url=%s attempt=%s reason=%s", url, attempt, last_error)
            sleep(backoff_seconds(attempt))
    raise AdapterError("fetch", url, f"gave up after {MAX_ATTEMPTS} attempts: {last_error}")


def get_following_redirects(
    policy: CrawlPolicy,
    url: str,
    *,
    headers: Mapping[str, str],
    sleep: Callable[[float], None],
) -> tuple[str, Any]:
    """GET ``url``, following redirects by hand so each hop passes ``policy.authorize``.

    The caller must already have authorised ``url`` itself. Returns
    ``(final_url, open_response)`` for the first non-redirect response, whatever
    its status; the caller checks the status and closes the response.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        response = get_with_retry(policy, current, headers=headers, sleep=sleep)
        status = int(response.status_code)
        if status not in REDIRECT_STATUS:
            return current, response
        location = response.headers.get("Location")
        response.close()
        if not location:
            raise AdapterError("fetch", current, f"HTTP {status} without a Location header")
        target = urljoin(current, location)
        try:
            policy.authorize(target)
        except AdapterError as exc:
            raise AdapterError(exc.stage, url, f"redirect to {target} refused: {exc.message}") from exc
        current = target
    raise AdapterError("fetch", url, f"more than {MAX_REDIRECTS} redirects")
