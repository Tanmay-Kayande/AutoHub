from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import requests

from autohub.core.settings import get_settings
from autohub.ingestion import fetcher as fetcher_module
from autohub.ingestion.adapters.base import AdapterError
from autohub.ingestion.fetcher import PolitePageFetcher

HOST = "https://www.testa.example"
ROBOTS = "User-agent: *\nDisallow: /private/\n"


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        body: str | bytes = "",
        *,
        headers: dict[str, str] | None = None,
        encoding: str | None = "utf-8",
    ) -> None:
        self.status_code = status
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.headers = headers or {}
        self.encoding = encoding
        self.closed = False

    def iter_content(self, chunk_size: int = 65536) -> Any:
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Serves queued responses (or raises queued exceptions) per URL and records every call."""

    def __init__(self, routes: dict[str, list[Any]] | None = None) -> None:
        self.routes: dict[str, list[Any]] = {f"{HOST}/robots.txt": [FakeResponse(200, ROBOTS)]}
        self.routes.update(routes or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs))
        queue = self.routes.get(url)
        if not queue:
            return FakeResponse(404)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


class FakeClock:
    """Monotonic clock advanced only by ``sleep``; records each sleep."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_fetcher(
    session: FakeSession, *, delay: float = 2.0, max_pages: int | None = None, renderer: Any = None
) -> tuple[PolitePageFetcher, FakeClock]:
    clock = FakeClock()
    settings = get_settings().model_copy(update={"crawler_delay_seconds": delay})
    fetcher = PolitePageFetcher(
        allowed_domains=("testa.example",),
        settings=settings,
        session=session,
        renderer=renderer,
        max_pages=max_pages,
        sleep=clock.sleep,
        clock=clock.clock,
    )
    return fetcher, clock


def test_fetches_html_with_honest_user_agent_and_verified_tls() -> None:
    session = FakeSession({f"{HOST}/cars": [FakeResponse(200, "<html>ok</html>")]})
    fetcher, _ = make_fetcher(session)

    assert fetcher.get_html(f"{HOST}/cars") == "<html>ok</html>"
    assert fetcher.pages_visited == 1
    assert session.urls() == [f"{HOST}/robots.txt", f"{HOST}/cars"]
    for _, kwargs in session.calls:
        assert kwargs["headers"]["User-Agent"] == get_settings().crawler_user_agent
        assert kwargs["timeout"] > 0
        assert "verify" not in kwargs  # certificate verification is never overridden


def test_robots_disallowed_url_is_skipped_and_reported() -> None:
    session = FakeSession({f"{HOST}/private/x": [FakeResponse(200, "secret")]})
    fetcher, _ = make_fetcher(session)

    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_html(f"{HOST}/private/x")

    assert excinfo.value.stage == "robots" and excinfo.value.url == f"{HOST}/private/x"
    assert f"{HOST}/private/x" not in session.urls()  # never requested
    assert fetcher.pages_visited == 0  # a skipped page does not spend budget


def test_robots_is_fetched_once_per_host() -> None:
    session = FakeSession({f"{HOST}/a": [FakeResponse(200, "a")], f"{HOST}/b": [FakeResponse(200, "b")]})
    fetcher, _ = make_fetcher(session)
    fetcher.get_html(f"{HOST}/a")
    fetcher.get_html(f"{HOST}/b")
    assert session.urls().count(f"{HOST}/robots.txt") == 1


@pytest.mark.parametrize(
    ("robots_response", "allowed"),
    [
        (FakeResponse(404), True),  # no robots.txt: nothing is disallowed
        (FakeResponse(503), False),  # server error: assume disallowed
        (requests.exceptions.ConnectionError("down"), False),  # unreachable: assume disallowed
    ],
)
def test_robots_unavailable_handling(robots_response: Any, allowed: bool) -> None:
    session = FakeSession({f"{HOST}/robots.txt": [robots_response], f"{HOST}/a": [FakeResponse(200, "a")]})
    fetcher, _ = make_fetcher(session)
    if allowed:
        assert fetcher.get_html(f"{HOST}/a") == "a"
    else:
        with pytest.raises(AdapterError) as excinfo:
            fetcher.get_html(f"{HOST}/a")
        assert excinfo.value.stage == "robots"


def test_requests_to_one_host_are_spaced_by_the_configured_delay() -> None:
    session = FakeSession({f"{HOST}/a": [FakeResponse(200, "a")], f"{HOST}/b": [FakeResponse(200, "b")]})
    fetcher, clock = make_fetcher(session, delay=2.0)

    fetcher.get_html(f"{HOST}/a")  # robots.txt, then /a
    fetcher.get_html(f"{HOST}/b")

    # Three requests to the host -> two full waits; the clock only moves by sleeping.
    assert clock.sleeps == [2.0, 2.0]


def test_different_hosts_do_not_wait_for_each_other() -> None:
    other = "https://cdn.testa.example"
    session = FakeSession(
        {
            f"{HOST}/a": [FakeResponse(200, "a")],
            f"{other}/robots.txt": [FakeResponse(404)],
            f"{other}/b": [FakeResponse(200, "b")],
        }
    )
    fetcher, clock = make_fetcher(session, delay=2.0)
    fetcher.get_html(f"{HOST}/a")
    sleeps_before = list(clock.sleeps)
    fetcher.get_html(f"{other}/b")
    # Only the second host's own robots.txt -> page gap is waited for.
    assert clock.sleeps == [*sleeps_before, 2.0]


def test_tls_error_surfaces_as_adapter_error_and_is_not_retried() -> None:
    session = FakeSession({f"{HOST}/a": [requests.exceptions.SSLError("certificate has expired")]})
    fetcher, _ = make_fetcher(session)

    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_html(f"{HOST}/a")

    assert excinfo.value.stage == "tls"
    assert session.urls().count(f"{HOST}/a") == 1
    assert all("verify" not in kwargs for _, kwargs in session.calls)


def test_tls_error_on_robots_surfaces_as_adapter_error() -> None:
    session = FakeSession({f"{HOST}/robots.txt": [requests.exceptions.SSLError("expired")]})
    fetcher, _ = make_fetcher(session)
    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_html(f"{HOST}/a")
    assert excinfo.value.stage == "tls"


def test_there_is_no_switch_to_disable_certificate_verification() -> None:
    source = Path(fetcher_module.__file__).read_text(encoding="utf-8")
    assert "verify=" not in source and "ignore_https_errors=" not in source


@pytest.mark.parametrize(
    "url",
    [
        "http://www.testa.example/a",  # not https
        "https://evil.example/a",  # other domain
        "https://testa.example.evil.example/a",  # look-alike suffix
        "https://user:pw@www.testa.example/a",  # credentials in URL
        "file:///etc/passwd",
    ],
)
def test_out_of_scope_urls_are_refused_without_any_request(url: str) -> None:
    session = FakeSession()
    fetcher, _ = make_fetcher(session)
    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_html(url)
    assert excinfo.value.stage == "scope"
    assert session.calls == []


def test_page_cap_is_enforced() -> None:
    session = FakeSession({f"{HOST}/{n}": [FakeResponse(200, str(n))] for n in range(3)})
    fetcher, _ = make_fetcher(session, max_pages=2)
    fetcher.get_html(f"{HOST}/0")
    fetcher.get_html(f"{HOST}/1")
    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_html(f"{HOST}/2")
    assert excinfo.value.stage == "budget"
    assert f"{HOST}/2" not in session.urls() and fetcher.pages_visited == 2


def test_transient_errors_are_retried_with_backoff() -> None:
    session = FakeSession(
        {
            f"{HOST}/a": [
                FakeResponse(503),
                requests.exceptions.ConnectionError("reset"),
                FakeResponse(200, "finally"),
            ]
        }
    )
    fetcher, clock = make_fetcher(session, delay=0.0)
    assert fetcher.get_html(f"{HOST}/a") == "finally"
    assert session.urls().count(f"{HOST}/a") == 3
    assert len(clock.sleeps) == 2 and 1.0 <= clock.sleeps[0] <= 1.5 and 2.0 <= clock.sleeps[1] <= 2.5
    assert fetcher.pages_visited == 1  # retries do not spend page budget


def test_gives_up_after_bounded_attempts() -> None:
    session = FakeSession({f"{HOST}/a": [FakeResponse(503)]})
    fetcher, _ = make_fetcher(session, delay=0.0)
    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_html(f"{HOST}/a")
    assert excinfo.value.stage == "fetch" and "gave up after 3 attempts" in excinfo.value.message
    assert session.urls().count(f"{HOST}/a") == 3


def test_client_errors_are_not_retried() -> None:
    session = FakeSession({f"{HOST}/a": [FakeResponse(404)]})
    fetcher, _ = make_fetcher(session, delay=0.0)
    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_html(f"{HOST}/a")
    assert excinfo.value.message == "HTTP 404" and session.urls().count(f"{HOST}/a") == 1


def test_redirect_within_scope_is_followed_and_checked_against_robots() -> None:
    session = FakeSession(
        {
            f"{HOST}/old": [FakeResponse(301, headers={"Location": "/new"})],
            f"{HOST}/new": [FakeResponse(200, "moved here")],
            f"{HOST}/sneaky": [FakeResponse(302, headers={"Location": "/private/x"})],
        }
    )
    fetcher, _ = make_fetcher(session, delay=0.0)
    assert fetcher.get_html(f"{HOST}/old") == "moved here"
    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_html(f"{HOST}/sneaky")
    assert excinfo.value.stage == "robots" and f"{HOST}/private/x" not in session.urls()


def test_redirect_outside_official_domains_is_refused() -> None:
    session = FakeSession({f"{HOST}/a": [FakeResponse(302, headers={"Location": "https://evil.example/x"})]})
    fetcher, _ = make_fetcher(session, delay=0.0)
    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_html(f"{HOST}/a")
    assert excinfo.value.stage == "scope"
    assert all("evil.example" not in url for url in session.urls())
    assert all(kwargs["allow_redirects"] is False for url, kwargs in session.calls if url == f"{HOST}/a")


def test_redirect_loop_is_bounded() -> None:
    session = FakeSession({f"{HOST}/loop": [FakeResponse(302, headers={"Location": "/loop"})]})
    fetcher, _ = make_fetcher(session, delay=0.0)
    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_html(f"{HOST}/loop")
    assert "redirects" in excinfo.value.message


def test_oversized_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher_module, "MAX_HTML_BYTES", 10)
    session = FakeSession({f"{HOST}/big": [FakeResponse(200, "x" * 11)]})
    fetcher, _ = make_fetcher(session, delay=0.0)
    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_html(f"{HOST}/big")
    assert excinfo.value.stage == "fetch" and "larger than" in excinfo.value.message


class FakeRenderer:
    def __init__(self, result: tuple[str, str] | Exception) -> None:
        self.result = result
        self.rendered: list[str] = []
        self.closed = False

    def render(self, url: str) -> tuple[str, str]:
        self.rendered.append(url)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self) -> None:
        self.closed = True


def test_rendered_fetch_applies_the_same_policy_and_closes_the_renderer() -> None:
    renderer = FakeRenderer((f"{HOST}/suv", "<html>rendered</html>"))
    session = FakeSession()
    with make_fetcher(session, renderer=renderer)[0] as fetcher:
        assert fetcher.get_rendered_html(f"{HOST}/suv") == "<html>rendered</html>"
        with pytest.raises(AdapterError) as excinfo:
            fetcher.get_rendered_html(f"{HOST}/private/page")
        assert excinfo.value.stage == "robots"
        assert fetcher.pages_visited == 1
    assert renderer.rendered == [f"{HOST}/suv"] and renderer.closed


def test_rendered_page_that_leaves_the_official_domains_is_refused() -> None:
    renderer = FakeRenderer(("https://evil.example/landing", "<html></html>"))
    fetcher, _ = make_fetcher(FakeSession(), renderer=renderer)
    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_rendered_html(f"{HOST}/suv")
    assert excinfo.value.stage == "scope"


def test_browser_certificate_error_is_reported_as_tls() -> None:
    renderer = FakeRenderer(RuntimeError("net::ERR_CERT_DATE_INVALID at https://www.testa.example/suv"))
    fetcher, _ = make_fetcher(FakeSession(), renderer=renderer)
    with pytest.raises(AdapterError) as excinfo:
        fetcher.get_rendered_html(f"{HOST}/suv")
    assert excinfo.value.stage == "tls"


@pytest.mark.parametrize(
    ("content_type", "body", "expected"),
    [
        (
            "text/html",
            "Price : ₹ 5.59 Lakh".encode(),
            "Price : ₹ 5.59 Lakh",
        ),  # no charset: UTF-8, not Latin-1
        ("text/html; charset=UTF-8", "café".encode(), "café"),
        ('text/html; charset="iso-8859-1"', "café".encode("latin-1"), "café"),  # declared charset wins
        ("text/html", "café".encode("cp1252"), "café"),  # undeclared and not UTF-8: Windows-1252
        ("text/html; charset=no-such-charset", "₹".encode(), "₹"),  # unknown name: as undeclared
        ("", b"plain", "plain"),
    ],
)
def test_html_is_decoded_with_declared_charset_else_utf8(
    content_type: str, body: bytes, expected: str
) -> None:
    # requests would report ISO-8859-1 for a bare "text/html"; that guess must not be used.
    response = FakeResponse(200, body, headers={"Content-Type": content_type}, encoding="ISO-8859-1")
    fetcher, _ = make_fetcher(FakeSession({f"{HOST}/a": [response]}), delay=0.0)
    assert fetcher.get_html(f"{HOST}/a") == expected
