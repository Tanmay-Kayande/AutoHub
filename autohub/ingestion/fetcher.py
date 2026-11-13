"""Polite page access for brand adapters (docs/V2/MULTI_BRAND_STRATEGY.md §4).

:class:`CrawlPolicy` holds the rules shared by the fetcher and the brochure
downloader: official-domain scope, ``robots.txt``, and per-host spacing.
:class:`PolitePageFetcher` adds the per-run page cap, bounded retries and the
optional browser renderer.

TLS certificates are always verified. There is deliberately no switch to turn
verification off (DECISIONS D-007): a host with an invalid certificate is
reported as an :class:`AdapterError` with ``stage="tls"`` and skipped.

Not thread-safe: the pipeline makes no parallel requests to one host.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, Protocol, cast
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

from autohub.core.settings import Settings, get_settings
from autohub.ingestion.adapters.base import AdapterError, host_in_domains

logger = logging.getLogger(__name__)

MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_ROBOTS_BYTES = 512 * 1024
MAX_ATTEMPTS = 3
MAX_REDIRECTS = 5
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


class HttpSession(Protocol):
    """The part of ``requests.Session`` the fetcher uses (faked in tests)."""

    def get(self, url: str, **kwargs: Any) -> Any: ...


class Renderer(Protocol):
    """Renders a page in a real browser and returns ``(final_url, html)``."""

    def render(self, url: str) -> tuple[str, str]: ...

    def close(self) -> None: ...


def _host_key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{(parts.netloc or '').lower()}"


def _read_capped(response: Any, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > limit:
            raise ValueError(f"response larger than {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


_CHARSET = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)


def _decode_html(body: bytes, content_type: str) -> str:
    """Decode a page using the charset the server declared, else UTF-8.

    ``requests`` assumes ISO-8859-1 for ``text/*`` responses without a charset,
    which corrupts the UTF-8 pages most sites actually serve, so its guess is
    not used. Bytes that are not valid UTF-8 fall back to Windows-1252.
    """
    declared = _CHARSET.search(content_type)
    if declared:
        try:
            return body.decode(declared.group(1), errors="replace")
        except LookupError:
            pass  # unknown charset name: treat as undeclared
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


class CrawlPolicy:
    """Scope, ``robots.txt`` and per-host rate limit for one pipeline run."""

    def __init__(
        self,
        *,
        session: HttpSession,
        user_agent: str,
        delay_seconds: float,
        timeout_seconds: float,
        allowed_domains: tuple[str, ...],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not allowed_domains:
            raise ValueError("allowed_domains must not be empty")
        self._session = session
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.allowed_domains = allowed_domains
        self._sleep = sleep
        self._clock = clock
        self._next_request_at: dict[str, float] = {}
        # host -> parsed rules, or True (allow everything) / False (disallow everything)
        self._robots: dict[str, RobotFileParser | bool] = {}

    # ------------------------------------------------------------------ rules
    def check_scope(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise AdapterError("scope", url, "only https URLs are fetched")
        if parts.username or parts.password:
            raise AdapterError("scope", url, "URLs with credentials are not fetched")
        if not host_in_domains(url, self.allowed_domains):
            raise AdapterError("scope", url, "host is not one of the brand's official domains")

    def wait_for_host(self, url: str) -> None:
        """Block until this host may be contacted again, then reserve the slot."""
        host = _host_key(url)
        ready_at = self._next_request_at.get(host)
        now = self._clock()
        if ready_at is not None and now < ready_at:
            self._sleep(ready_at - now)
        self._next_request_at[host] = self._clock() + self.delay_seconds

    def check_robots(self, url: str) -> None:
        host = _host_key(url)
        rules = self._robots.get(host)
        if rules is None:
            rules = self._load_robots(host)
            self._robots[host] = rules
        allowed = rules if isinstance(rules, bool) else rules.can_fetch(self.user_agent, url)
        if not allowed:
            raise AdapterError("robots", url, "disallowed by robots.txt; skipped")

    def authorize(self, url: str) -> None:
        """All checks that must pass before a URL is requested."""
        self.check_scope(url)
        self.check_robots(url)

    # ------------------------------------------------------------------ robots
    def _load_robots(self, host: str) -> RobotFileParser | bool:
        robots_url = f"{host}/robots.txt"
        self.wait_for_host(robots_url)
        try:
            response = self._session.get(
                robots_url,
                timeout=self.timeout_seconds,
                headers={"User-Agent": self.user_agent},
                stream=True,
                allow_redirects=True,
            )
        except requests.exceptions.SSLError as exc:
            raise AdapterError("tls", robots_url, f"certificate verification failed: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            # Unreachable robots.txt: assume disallowed (RFC 9309 §2.3.1.4).
            logger.warning("robots_unreachable host=%s error=%s", host, type(exc).__name__)
            return False
        try:
            status = int(response.status_code)
            if 400 <= status < 500:
                return True  # no robots.txt (or not available to us): no restrictions stated
            if status != 200:
                logger.warning("robots_unavailable host=%s status=%s", host, status)
                return False
            try:
                body = _read_capped(response, MAX_ROBOTS_BYTES)
            except (ValueError, requests.exceptions.RequestException):
                logger.warning("robots_unreadable host=%s", host)
                return False
        finally:
            response.close()
        parser = RobotFileParser()
        parser.parse(body.decode("utf-8", errors="replace").splitlines())
        return parser


class PolitePageFetcher:
    """`PageFetcher` implementation used by the pipeline. One instance per adapter per run."""

    def __init__(
        self,
        *,
        allowed_domains: tuple[str, ...],
        settings: Settings | None = None,
        session: HttpSession | None = None,
        renderer: Renderer | None = None,
        max_pages: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        settings = settings or get_settings()
        self._owns_session = session is None
        # requests.Session satisfies HttpSession at runtime; its stub spells out keyword
        # arguments instead of **kwargs, hence the cast.
        self._session: HttpSession = session if session is not None else cast(HttpSession, requests.Session())
        self._renderer = renderer
        self._sleep = sleep
        self._timeout = settings.http_timeout_seconds
        self._max_pages = max_pages if max_pages is not None else settings.crawler_max_pages_per_brand
        self._pages_visited = 0
        self.policy = CrawlPolicy(
            session=self._session,
            user_agent=settings.crawler_user_agent,
            delay_seconds=settings.crawler_delay_seconds,
            timeout_seconds=settings.http_timeout_seconds,
            allowed_domains=allowed_domains,
            sleep=sleep,
            clock=clock,
        )

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self) -> PolitePageFetcher:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._renderer is not None:
            try:
                self._renderer.close()
            except Exception:
                logger.exception("renderer_close_failed")
        if self._owns_session:
            close = getattr(self._session, "close", None)
            if callable(close):
                close()

    @property
    def pages_visited(self) -> int:
        return self._pages_visited

    # ------------------------------------------------------------------ PageFetcher
    def get_html(self, url: str) -> str:
        self._admit(url)
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            response = self._get_with_retry(current)
            try:
                status = int(response.status_code)
                if status in _REDIRECT_STATUS:
                    location = response.headers.get("Location")
                    if not location:
                        raise AdapterError("fetch", current, f"HTTP {status} without a Location header")
                    target = urljoin(current, location)
                    # Every hop is held to the same scope and robots rules as the first URL.
                    try:
                        self.policy.authorize(target)
                    except AdapterError as exc:
                        raise AdapterError(
                            exc.stage, url, f"redirect to {target} refused: {exc.message}"
                        ) from exc
                    current = target
                    continue
                if status != 200:
                    raise AdapterError("fetch", current, f"HTTP {status}")
                return self._decode(current, response)
            finally:
                response.close()
        raise AdapterError("fetch", url, f"more than {MAX_REDIRECTS} redirects")

    def _get_with_retry(self, url: str) -> Any:
        """One GET without following redirects; retries transient failures with backoff."""
        last_error = "no attempt made"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.policy.wait_for_host(url)
            try:
                response = self._session.get(
                    url,
                    timeout=self._timeout,
                    headers={
                        "User-Agent": self.policy.user_agent,
                        "Accept": "text/html,application/xhtml+xml",
                    },
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
                if int(response.status_code) not in _RETRYABLE_STATUS:
                    return response
                last_error = f"HTTP {response.status_code}"
                response.close()
            if attempt < MAX_ATTEMPTS:
                logger.warning("fetch_retry url=%s attempt=%s reason=%s", url, attempt, last_error)
                self._sleep((2 ** (attempt - 1)) + random.uniform(0, 0.5))
        raise AdapterError("fetch", url, f"gave up after {MAX_ATTEMPTS} attempts: {last_error}")

    def get_rendered_html(self, url: str) -> str:
        self._admit(url)
        if self._renderer is None:
            self._renderer = PlaywrightRenderer(
                user_agent=self.policy.user_agent, timeout_seconds=self._timeout * 2
            )
        self.policy.wait_for_host(url)
        try:
            final_url, html = self._renderer.render(url)
        except AdapterError:
            raise
        except Exception as exc:
            text = str(exc)
            stage = "tls" if "ERR_CERT" in text or "SSL" in text.upper() else "render"
            raise AdapterError(
                stage, url, f"{type(exc).__name__}: {text.splitlines()[0] if text else ''}"
            ) from exc
        self._check_final_url(url, final_url)
        return html

    # ------------------------------------------------------------------ internals
    def _admit(self, url: str) -> None:
        """Scope and robots first (free), then spend one unit of the page budget."""
        self.policy.authorize(url)
        if self._pages_visited >= self._max_pages:
            raise AdapterError("budget", url, f"page cap of {self._max_pages} per run reached; skipped")
        self._pages_visited += 1

    def _check_final_url(self, url: str, final_url: str) -> None:
        if final_url and final_url != url:
            try:
                self.policy.check_scope(final_url)
            except AdapterError as exc:
                raise AdapterError(
                    "scope", url, f"redirected outside the official domains to {final_url}"
                ) from exc

    def _decode(self, url: str, response: Any) -> str:
        try:
            body = _read_capped(response, MAX_HTML_BYTES)
        except ValueError as exc:
            raise AdapterError("fetch", url, str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise AdapterError("fetch", url, f"{type(exc).__name__}: {exc}") from exc
        return _decode_html(body, str((getattr(response, "headers", None) or {}).get("Content-Type", "")))


class PlaywrightRenderer:
    """Chromium renderer, started lazily on the first page and reused for the run.

    The browser context keeps certificate checking on (``ignore_https_errors`` is
    never set).
    """

    SETTLE_MS = 3000
    SCROLL_SETTLE_MS = 2000

    def __init__(self, *, user_agent: str, timeout_seconds: float) -> None:
        self._user_agent = user_agent
        self._timeout_ms = timeout_seconds * 1000
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    def _ensure_started(self) -> None:
        if self._context is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=self._user_agent)

    def render(self, url: str) -> tuple[str, str]:
        self._ensure_started()
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            page.wait_for_timeout(self.SETTLE_MS)
            # Scroll so lazily loaded links (model tiles, brochure buttons) are in the DOM.
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(self.SCROLL_SETTLE_MS)
            return str(page.url), str(page.content())
        finally:
            page.close()

    def close(self) -> None:
        for resource, method in (
            (self._context, "close"),
            (self._browser, "close"),
            (self._playwright, "stop"),
        ):
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception:
                    logger.exception("playwright_shutdown_failed step=%s", method)
        self._context = self._browser = self._playwright = None
