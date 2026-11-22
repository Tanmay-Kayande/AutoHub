"""Toyota adapter against an offline fixture.

``tests/fixtures/toyota/home.html`` copies the model navigation of ``www.toyotabharat.com``
and ``showroom/*.html`` the brochure links each showroom page served on 2026-11-22
(``expected.json``: brochure URL -> [model hint, title, is primary layout]), plus links
that must be ignored: accessory catalogues, flip-books, commented-out and off-domain links.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autohub.ingestion.adapters import registry
from autohub.ingestion.adapters.base import AdapterError, DiscoveryResult
from autohub.ingestion.adapters.toyota import (
    ToyotaAdapter,
    brochure_stem,
    is_spec_sheet,
    layout_group_from_stem,
    model_hint_from_slug,
    showroom_slug,
    spec_sheet_group,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "toyota"
HOME = "https://www.toyotabharat.com/"
SHOWROOM = "https://www.toyotabharat.com/showroom"
DOCS = "https://www.toyotabharat.com/documents/brochures"
EXPECTED: dict[str, list[object]] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


def _pages() -> dict[str, str]:
    pages = {}
    for path in (FIXTURES / "showroom").glob("*.html"):
        name = path.stem
        url = (
            f"{SHOWROOM}/fortuner/{name.removeprefix('fortuner-')}.html"
            if name.startswith("fortuner-index-")
            else f"{SHOWROOM}/{name}/"
        )
        pages[url] = path.read_text(encoding="utf-8")
    return pages


PAGES = _pages()


class FixtureFetcher:
    """Serves the home page and the showroom pages; anything else is a 404."""

    def __init__(self, home: str | None = None, pages: dict[str, str] | None = None) -> None:
        self.plain: list[str] = []
        self._home = home if home is not None else (FIXTURES / "home.html").read_text(encoding="utf-8")
        self._pages = pages if pages is not None else PAGES

    @property
    def pages_visited(self) -> int:
        return len(self.plain)

    def get_html(self, url: str) -> str:
        self.plain.append(url)
        if url == HOME:
            return self._home
        if url in self._pages:
            return self._pages[url]
        raise AdapterError("fetch", url, "HTTP 404")

    def get_rendered_html(self, url: str) -> str:
        raise AssertionError("the Toyota showroom pages are server-rendered; no browser is needed")


@pytest.fixture()
def result() -> DiscoveryResult:
    return ToyotaAdapter().discover(FixtureFetcher())


def test_returns_every_brochure_and_spec_sheet(result: DiscoveryResult) -> None:
    assert len(EXPECTED) == 16
    assert [b.source_url for b in result.brochures] == sorted(EXPECTED)


def test_model_hint_title_and_primary_layout(result: DiscoveryResult) -> None:
    assert {b.source_url: [b.model_hint, b.title, b.is_primary_layout] for b in result.brochures} == EXPECTED


def test_spec_sheets_are_alternatives_of_their_brochure(result: DiscoveryResult) -> None:
    by_url = {b.source_url: b for b in result.brochures}
    pairs = {  # spec sheet -> the brochure it belongs to
        f"{DOCS}/e-brochure-urbancruiser-hyryder-spec.pdf": f"{DOCS}/e-brochure-urbancruiser-hyryder.pdf",
        # The GR-S brochure comes first on the page; the spec sheet follows the main brochure.
        f"{DOCS}/fortuner-mmc/combined-specsheet.pdf": f"{DOCS}/fortuner-mmc/combined-brochure-mobile.pdf",
        f"{DOCS}/fortuner-mmc/legender-spec-brochure.pdf": f"{DOCS}/fortuner-mmc/legender-brochure-mobile.pdf",
    }
    for spec, brochure in pairs.items():
        assert not by_url[spec].is_primary_layout and by_url[brochure].is_primary_layout
        assert by_url[spec].layout_group == by_url[brochure].layout_group
    primaries = [b.layout_group for b in result.brochures if b.is_primary_layout]
    assert len(primaries) == 13 and len(set(primaries)) == 13  # one primary per group
    assert all(b.brand == "Toyota" for b in result.brochures)
    pages = {b.source_url: b.source_page_url for b in result.brochures}
    assert (
        pages[f"{DOCS}/fortuner-mmc/legender-spec-brochure.pdf"] == f"{SHOWROOM}/fortuner/index-legender.html"
    )


def test_accessories_flip_books_comments_and_duplicates_are_ignored(result: DiscoveryResult) -> None:
    urls = [b.source_url for b in result.brochures]
    assert not any(
        w in u for u in urls for w in ("accessor", "acc-brochure", "view-brochure", "inc-specsheet")
    )
    assert (
        "https://www.toyotabharat.com/documents/dealerships-restart-status/dealer-open-status.pdf" not in urls
    )
    # Hyryder links its brochure three times, once with a query string: still one entry.
    assert urls.count(f"{DOCS}/e-brochure-urbancruiser-hyryder.pdf") == 1
    assert all("?" not in u for u in urls)


def test_bare_host_link_is_kept_as_published(result: DiscoveryResult) -> None:
    rumion = "https://toyotabharat.com/documents/brochures/e-brochure-rumion.pdf"
    assert rumion in [b.source_url for b in result.brochures]


def test_off_domain_brochure_link_is_reported_not_returned(result: DiscoveryResult) -> None:
    assert [(e.stage, e.url.split("/")[2]) for e in result.errors] == [("scope", "cdn.example.net")]


def test_visits_home_then_each_official_showroom_page_once() -> None:
    fetcher = FixtureFetcher()
    result = ToyotaAdapter().discover(fetcher)
    assert fetcher.plain[0] == HOME
    assert sorted(fetcher.plain[1:]) == sorted(PAGES) and len(fetcher.plain) == 14
    assert result.pages_visited == 14


def test_a_failing_showroom_page_is_an_error_and_the_rest_still_count() -> None:
    pages = {u: h for u, h in PAGES.items() if "/glanza/" not in u}
    result = ToyotaAdapter().discover(FixtureFetcher(pages=pages))
    assert ("fetch", f"{SHOWROOM}/glanza/") in [(e.stage, e.url) for e in result.errors]
    assert len(result.brochures) == 15


def test_home_page_failure_is_returned_as_data() -> None:
    class Down(FixtureFetcher):
        def get_html(self, url: str) -> str:
            self.plain.append(url)
            raise AdapterError("tls", url, "bad certificate")

    result = ToyotaAdapter().discover(Down())
    assert result.brochures == [] and [e.stage for e in result.errors] == ["tls"]
    assert result.pages_visited == 1


def test_no_brochure_links_is_flagged_as_a_layout_change() -> None:
    home = '<a href="/showroom/vellfire/">Vellfire</a>'
    result = ToyotaAdapter().discover(FixtureFetcher(home=home))
    assert result.brochures == []
    assert [(e.stage, "no brochure links" in e.message) for e in result.errors] == [("parse", True)]


def test_link_without_a_name_falls_back_to_the_slug() -> None:
    home = '<a href="/showroom/lc300/"><img src="x.webp"></a><a href="/showroom/lc300/">Know More</a>'
    result = ToyotaAdapter().discover(FixtureFetcher(home=home))
    assert [(b.model_hint, b.title) for b in result.brochures] == [("LC300", "LC300 Brochure")]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{SHOWROOM}/glanza/", "glanza"),
        (f"{SHOWROOM}/urbancruiser-hyryder/#testDriveBlock", "urbancruiser-hyryder"),
        (f"{SHOWROOM}/fortuner/index-legender.html", "fortuner/index-legender"),
        (f"{SHOWROOM}/", None),
        (f"{SHOWROOM}/glanza/specs.html", None),
        ("https://www.toyotabharat.com/brochure/", None),
    ],
)
def test_showroom_slug(url: str, expected: str | None) -> None:
    assert showroom_slug(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{DOCS}/e-brochure-glanza.pdf", "e-brochure-glanza"),
        (f"{DOCS}/fortuner-mmc/combined-specsheet.pdf?v=1", "fortuner-mmc/combined-specsheet"),
        (f"{DOCS}/accessories-brochure-glanza.pdf", None),
        (f"{DOCS}/fortuner-mmc/accessory-brochure.pdf", None),
        (f"{DOCS}/urban-cruiser-taisor/acc-brochure-urbancruiser-taisor.pdf", None),
        (f"{DOCS}/camry/view-brochure/index.html", None),
        ("https://www.toyotabharat.com/documents/dealerships-restart-status/dealer-open-status.pdf", None),
    ],
)
def test_brochure_stem(url: str, expected: str | None) -> None:
    assert brochure_stem(url) == expected


def test_small_helpers() -> None:
    assert layout_group_from_stem("fortuner-mmc/gr_mobile-brochure_360x640pix") == (
        "fortuner-mmc-gr-mobile-brochure-360x640pix"
    )
    assert model_hint_from_slug("urbancruiser-hyryder") == "Urbancruiser Hyryder"
    assert model_hint_from_slug("fortuner/index-legender") == "Legender"
    assert model_hint_from_slug("lc300") == "LC300"
    assert is_spec_sheet("e-brochure-urbancruiser-hyryder-spec", "Download")
    assert is_spec_sheet("fortuner-mmc/combined-brochure", "SPECIFICATIONS")
    assert not is_spec_sheet("e-brochure-glanza", "Download Brochure")


def test_spec_sheet_group_prefers_the_brochure_before_it() -> None:
    found = [("u1", "gr", False), ("u2", "main", False), ("u3", "spec", True)]
    assert spec_sheet_group(found, 2) == "main"
    assert spec_sheet_group([("u1", "spec", True), ("u2", "main", False)], 0) == "main"
    # A page with only a spec sheet keeps it as its own primary (the caller uses its group).
    assert spec_sheet_group([("u1", "spec", True)], 0) is None


def test_adapter_registers_with_official_scope() -> None:
    assert "toyota" in registry.available_keys()
    adapter = registry.get_adapter("toyota")
    assert isinstance(adapter, ToyotaAdapter) and not adapter.requires_browser
    adapter = ToyotaAdapter()
    assert adapter.is_official(HOME) and adapter.is_official(f"{DOCS}/x.pdf")
    assert adapter.is_official("https://toyotabharat.com/documents/brochures/x.pdf")
    assert not adapter.is_official("https://nottoyotabharat.com/x.pdf")
    assert not adapter.is_official("https://toyotabharat.example.net/x.pdf")
