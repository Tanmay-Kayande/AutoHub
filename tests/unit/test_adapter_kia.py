"""Kia adapter against an offline fixture.

``tests/fixtures/kia/brochures.html`` is a small synthetic page with the block structure
and brochure URLs that ``www.kia.com/in/buy/download-brochure.html`` served on 2026-11-20
(``expected.json``: brochure URL -> [model hint, title, is primary layout]), plus links
that must be ignored. Showroom snippets copy the price markup of the same day.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autohub.ingestion.adapters import registry
from autohub.ingestion.adapters.base import AdapterError, DiscoveryResult
from autohub.ingestion.adapters.kia import (
    KiaAdapter,
    brochure_stem,
    layout_group_from_stem,
    model_hint_from_heading,
    model_hint_from_slug,
    model_hint_from_stem,
    price_from_page,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "kia"
LISTING = "https://www.kia.com/in/buy/download-brochure.html"
SITEMAP = "https://www.kia.com/in/sitemap.xml"
DAM = "https://www.kia.com/content/dam/kia2/in/en"
EXPECTED: dict[str, list[object]] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


def _showroom(*pairs: tuple[str, str]) -> str:
    body = "".join(
        f'<div class="logo-text1"><p class="price-start">{label} </p>'
        f'<p class="number-text"><span class="text-span">₹</span>{amount}</p></div>'
        for label, amount in pairs
    )
    return f"<html><body>{body}</body></html>"


def _page(slug: str) -> str:
    return f"https://www.kia.com/in/our-vehicles/{slug}/showroom.html"


SHOWROOMS = {
    # Footnote marker inside the amount, as on the Sonet, Seltos, Syros and Sorento pages.
    _page("sonet"): _showroom(("Prices start at", '7 44 900<sup style="font-size: 17.0px;">#</sup>')),
    _page("seltos"): _showroom(("Range price starts at", "11 24 900<sup>#</sup>")),
    _page("carens-clavis"): _showroom(("Price starts at", "11,38,900")),
    # The EV page prints the battery-subscription (BaaS) price after the purchase price.
    _page("syros-ev"): _showroom(("Price starts at", "13 49 900"), ("BaaS price starts at", "7.99 Lakh")),
    _page("carens-clavis-ev"): _showroom(("Price starts at", "12.88 Lakh")),
    _page("ev9"): "<html><body><h1>EV9</h1></body></html>",  # no price printed
}
SITEMAP_XML = (
    '<?xml version="1.0"?><urlset>'
    + "".join(f"<url><loc>{u}</loc></url>" for u in SHOWROOMS)
    + "<url><loc>https://www.kia.com/in/our-vehicles/sonet/features.html</loc></url>"
    + "<url><loc>https://kia.example.net/in/our-vehicles/sonet/showroom.html</loc></url>"
    + f"<url><loc>{_page('sonet')}</loc></url>"  # listed twice
    + "</urlset>"
)


class FixtureFetcher:
    """Serves the brochure page, a sitemap and showroom pages; anything else is a 404."""

    def __init__(
        self,
        html: str | None = None,
        error: AdapterError | None = None,
        *,
        sitemap: str | None = None,
        pages: dict[str, str] | None = None,
    ) -> None:
        self.plain: list[str] = []
        full = html is None
        self._html = html if html is not None else (FIXTURES / "brochures.html").read_text(encoding="utf-8")
        self._error = error
        self._sitemap = sitemap if sitemap is not None else (SITEMAP_XML if full else "<urlset></urlset>")
        self._pages = pages if pages is not None else SHOWROOMS

    @property
    def pages_visited(self) -> int:
        return len(self.plain)

    def get_html(self, url: str) -> str:
        self.plain.append(url)
        if self._error is not None:
            raise self._error
        if url == LISTING:
            return self._html
        if url == SITEMAP:
            return self._sitemap
        if url in self._pages:
            return self._pages[url]
        raise AdapterError("fetch", url, "HTTP 404")

    def get_rendered_html(self, url: str) -> str:
        raise AssertionError("the Kia brochure page is server-rendered; no browser is needed")


@pytest.fixture()
def result() -> DiscoveryResult:
    return KiaAdapter().discover(FixtureFetcher())


def test_returns_both_layouts_of_every_brochure(result: DiscoveryResult) -> None:
    assert len(EXPECTED) == 22
    assert [b.source_url for b in result.brochures] == sorted(EXPECTED)


def test_model_hint_title_and_primary_layout(result: DiscoveryResult) -> None:
    assert {b.source_url: [b.model_hint, b.title, b.is_primary_layout] for b in result.brochures} == EXPECTED


def test_desktop_is_primary_and_shares_its_group_with_mobile(result: DiscoveryResult) -> None:
    groups: dict[str, list[bool]] = {}
    for brochure in result.brochures:
        groups.setdefault(brochure.layout_group, []).append(brochure.is_primary_layout)
        assert brochure.brand == "Kia" and brochure.source_page_url == LISTING
    assert len(groups) == 11
    assert all(sorted(flags) == [False, True] for flags in groups.values())
    primary = {b.source_url for b in result.brochures if b.is_primary_layout}
    assert all("Desktop" in u for u in primary)


def test_policies_fact_sheets_other_countries_and_duplicates_are_ignored(result: DiscoveryResult) -> None:
    urls = [b.source_url for b in result.brochures]
    assert not any(w in u.lower() for u in urls for w in ("csr", "fact-sheet", "/kr/"))
    sorento = f"{DAM}/images/our-vehicles/sorento/brochure/Kia_Sorento_Brochure_2026_Desktop.pdf"
    # Linked twice, once with a cache-busting query string: still one brochure.
    assert urls.count(sorento) == 1
    assert all("?" not in u for u in urls)


def test_off_domain_brochure_link_is_reported_not_returned(result: DiscoveryResult) -> None:
    assert [(e.stage, e.url.split("/")[2]) for e in result.errors] == [("scope", "cdn.example.net")]


def test_plain_http_only_listing_then_sitemap_then_showroom_pages() -> None:
    fetcher = FixtureFetcher()
    result = KiaAdapter().discover(fetcher)
    assert fetcher.plain[:2] == [LISTING, SITEMAP]
    # Only official showroom pages, each once, in a stable order.
    assert fetcher.plain[2:] == sorted(SHOWROOMS)
    assert result.pages_visited == len(fetcher.plain)


def test_starting_prices_skip_the_battery_subscription_price(result: DiscoveryResult) -> None:
    prices = {p.model_hint: (int(p.price_min), p.price_max) for p in result.prices}
    assert prices == {
        "Sonet": (744_900, None),
        "Seltos": (1_124_900, None),
        "Carens Clavis": (1_138_900, None),
        "Syros EV": (1_349_900, None),
        "Carens Clavis EV": (1_288_000, None),
    }
    assert all(p.brand == "Kia" and p.source_url.endswith("/showroom.html") for p in result.prices)


def test_a_failing_showroom_page_is_an_error_and_the_rest_still_count() -> None:
    pages = {u: h for u, h in SHOWROOMS.items() if "sonet" not in u}
    result = KiaAdapter().discover(FixtureFetcher(pages=pages))
    assert ("fetch", _page("sonet")) in [(e.stage, e.url) for e in result.errors]
    assert len(result.prices) == 4 and len(result.brochures) == 22


def test_sitemap_failure_is_reported_and_brochures_are_still_returned() -> None:
    class NoSitemap(FixtureFetcher):
        def get_html(self, url: str) -> str:
            if url == SITEMAP:
                self.plain.append(url)
                raise AdapterError("fetch", url, "HTTP 503")
            return super().get_html(url)

    result = KiaAdapter().discover(NoSitemap())
    assert len(result.brochures) == 22 and result.prices == []
    assert ("fetch", SITEMAP) in [(e.stage, e.url) for e in result.errors]


def test_fetch_failure_is_returned_as_data() -> None:
    result = KiaAdapter().discover(FixtureFetcher(error=AdapterError("tls", LISTING, "bad certificate")))
    assert result.brochures == [] and [e.stage for e in result.errors] == ["tls"]
    assert result.pages_visited == 1


def test_page_without_brochure_links_is_flagged_as_a_layout_change() -> None:
    html = '<h4>Legal</h4><a href="/content/dam/kia2/in/en/images/csr/CSR-Plan-FY25-26.pdf">CSR</a>'
    result = KiaAdapter().discover(FixtureFetcher(html=html))
    assert result.brochures == []
    assert [(e.stage, "no brochure links" in e.message) for e in result.errors] == [("parse", True)]


def test_file_name_is_the_fallback_model_hint() -> None:
    html = '<a href="/content/dam/kia2/in/en/x/Kia_Carens_Clavis_2026_Desktop.pdf">Download</a>'
    result = KiaAdapter().discover(FixtureFetcher(html=html))
    assert [(b.model_hint, b.title) for b in result.brochures] == [("Carens Clavis", None)]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{DAM}/our-vehicles/carnival/Kia_Carnival_Brochure_Desktop.pdf", "Kia_Carnival_Brochure_Desktop"),
        (f"{DAM}/images/buy/download-brochure/ev6/EV6_Mobile_2025.pdf?v=1", "EV6_Mobile_2025"),
        (f"{DAM}/images/csr/CSR-Plan-FY25-26.pdf", None),  # no layout word
        (f"{DAM}/our-vehicles/syros/FACT-SHEET-SYROS-1.pdf", None),
        ("https://www.kia.com/content/dam/kia2/kr/ko/K5_Brochure_Desktop.pdf", None),  # not India
        ("https://www.kia.com/in/our-vehicles/sonet/showroom.html", None),
    ],
)  # fmt: skip
def test_brochure_stem(url: str, expected: str | None) -> None:
    assert brochure_stem(url) == expected


@pytest.mark.parametrize(
    ("stem", "group"),
    [
        ("Kia_Syros_EV_Brochure_Desktop2026", "kia-syros-ev-brochure-2026"),
        ("Kia_Syros_EV_Brochure_Mobile2026", "kia-syros-ev-brochure-2026"),
        ("EV6_Desktop_2025", "ev6-2025"),
        ("EV6_Mobile_2025", "ev6-2025"),
    ],
)
def test_layout_group_from_stem(stem: str, group: str) -> None:
    assert layout_group_from_stem(stem) == group


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("The Kia Sorento", "Sorento"),
        ("The all-new Seltos", "Seltos"),
        ("The Carens Clavis EV", "Carens Clavis EV"),
        ("SONET", "SONET"),
        ("  EV9 ", "EV9"),
        ("The", None),
        ("", None),
        (None, None),
    ],
)
def test_model_hint_from_heading(heading: str | None, expected: str | None) -> None:
    assert model_hint_from_heading(heading) == expected


def test_stem_and_slug_helpers() -> None:
    assert model_hint_from_stem("Kia_Syros_EV_Brochure_Desktop2026") == "Syros EV"
    assert model_hint_from_stem("Sonet_Wild_Reborn_Brochure_2026_Desktop") == "Sonet Wild Reborn"
    assert model_hint_from_slug("carens-clavis-ev") == "Carens Clavis EV"
    assert model_hint_from_slug("ev9") == "EV9"


def test_price_from_page_needs_a_showroom_url_and_a_price() -> None:
    page = _showroom(("Prices start at", "27 99 000"))
    assert price_from_page(page, _page("sorento")) is not None
    assert price_from_page(page, "https://www.kia.com/in/our-vehicles/sorento/features.html") is None
    assert price_from_page(_showroom(("Price starts at", "on request")), _page("sorento")) is None
    only_baas = _showroom(("BaaS price starts at", "7.99 Lakh"))
    assert price_from_page(only_baas, _page("syros-ev")) is None


def test_adapter_is_registered_with_official_scope() -> None:
    assert "kia" in registry.available_keys()
    adapter = registry.get_adapter("kia")
    assert isinstance(adapter, KiaAdapter) and not adapter.requires_browser
    assert adapter.is_official(LISTING) and adapter.is_official(f"{DAM}/x.pdf")
    assert not adapter.is_official("https://notkia.com/x.pdf")
    assert not adapter.is_official("https://kia.example.net/in/x.pdf")
