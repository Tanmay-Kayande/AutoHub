"""Honda adapter against an offline fixture.

``tests/fixtures/honda/brochures.html`` is a trimmed copy of ``www.hondacarindia.com/download-brochure``
as served on 2026-11-22: the brochures sit in the ``__NEXT_DATA__`` JSON (``carBrochureData``),
next to accessory brochures and owner's manuals that must be ignored, plus a duplicate, an
off-domain entry and a non-PDF entry. ``expected.json`` maps brochure URL -> [model hint, title,
is primary layout]. ``home.html`` copies the home page's price-card markup of the same day.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autohub.ingestion.adapters import registry
from autohub.ingestion.adapters.base import AdapterError, DiscoveryResult
from autohub.ingestion.adapters.honda import (
    HondaAdapter,
    brochure_url,
    layout_group_from_url,
    model_hint_from_name,
    next_data,
    prices_from_home_page,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "honda"
LISTING = "https://www.hondacarindia.com/download-brochure"
HOME = "https://www.hondacarindia.com/"
MEDIA = "https://www.hondacarindia.com/web-data/"
EXPECTED: dict[str, list[object]] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


class FixtureFetcher:
    """Serves the brochure page and the home page; anything else is a 404."""

    def __init__(self, pages: dict[str, str | AdapterError] | None = None) -> None:
        self.plain: list[str] = []
        self._pages: dict[str, str | AdapterError] = (
            pages
            if pages is not None
            else {
                LISTING: (FIXTURES / "brochures.html").read_text(encoding="utf-8"),
                HOME: (FIXTURES / "home.html").read_text(encoding="utf-8"),
            }
        )

    @property
    def pages_visited(self) -> int:
        return len(self.plain)

    def get_html(self, url: str) -> str:
        self.plain.append(url)
        page = self._pages.get(url)
        if isinstance(page, AdapterError):
            raise page
        if page is None:
            raise AdapterError("fetch", url, "HTTP 404")
        return page

    def get_rendered_html(self, url: str) -> str:
        raise AssertionError("the Honda brochure page is statically generated; no browser is needed")


@pytest.fixture()
def result() -> DiscoveryResult:
    return HondaAdapter().discover(FixtureFetcher())


def test_returns_every_car_brochure(result: DiscoveryResult) -> None:
    assert len(EXPECTED) == 5
    assert [b.source_url for b in result.brochures] == sorted(EXPECTED)


def test_model_hint_title_and_primary_layout(result: DiscoveryResult) -> None:
    assert {b.source_url: [b.model_hint, b.title, b.is_primary_layout] for b in result.brochures} == EXPECTED
    assert all(b.brand == "Honda" and b.source_page_url == LISTING for b in result.brochures)
    assert len({b.layout_group for b in result.brochures}) == 5


def test_manuals_accessories_html_links_and_duplicates_are_ignored(result: DiscoveryResult) -> None:
    urls = [b.source_url for b in result.brochures]
    assert not any(
        w in u.lower() for u in urls for w in ("user-manual", "accessories", "emergency", "ownersmanual")
    )
    # Listed twice, once already percent-encoded: still one brochure.
    assert sum("Elevate" in u for u in urls) == 1
    assert all(" " not in u for u in urls)


def test_off_domain_brochure_is_reported_not_returned(result: DiscoveryResult) -> None:
    assert [(e.stage, e.url.split("/")[2]) for e in result.errors] == [("scope", "cdn.example.net")]


def test_plain_http_only_listing_then_home_page() -> None:
    fetcher = FixtureFetcher()
    result = HondaAdapter().discover(fetcher)
    assert fetcher.plain == [LISTING, HOME]
    assert result.pages_visited == 2


def test_starting_prices_skip_the_card_exempt_from_the_ex_showroom_disclaimer(
    result: DiscoveryResult,
) -> None:
    prices = {p.model_hint: (int(p.price_min), p.price_max) for p in result.prices}
    assert prices == {
        "City": (1_199_900, None),
        "Amaze": (765_400, None),
        "ZR-V": (4_999_900, None),
        "Elevate": (1_180_000, None),
    }
    assert all(p.brand == "Honda" and p.source_url == HOME for p in result.prices)


def test_listing_failure_is_data_and_prices_are_still_read() -> None:
    pages: dict[str, str | AdapterError] = {
        LISTING: AdapterError("tls", LISTING, "bad certificate"),
        HOME: (FIXTURES / "home.html").read_text(encoding="utf-8"),
    }
    result = HondaAdapter().discover(FixtureFetcher(pages))
    assert result.brochures == [] and [e.stage for e in result.errors] == ["tls"]
    assert len(result.prices) == 4 and result.pages_visited == 2


def test_home_page_failure_is_data_and_brochures_are_still_returned() -> None:
    pages: dict[str, str | AdapterError] = {
        LISTING: (FIXTURES / "brochures.html").read_text(encoding="utf-8")
    }
    result = HondaAdapter().discover(FixtureFetcher(pages))
    assert len(result.brochures) == 5 and result.prices == []
    assert ("fetch", HOME) in [(e.stage, e.url) for e in result.errors]


@pytest.mark.parametrize(
    "html",
    [
        "<html><body><h1>Download Brochure</h1></body></html>",  # no __NEXT_DATA__
        '<script id="__NEXT_DATA__" type="application/json">{not json</script>',
        '<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"brochureData":[]}}}</script>',
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"carBrochureData":[{"carName":"City","mediaUrl":"/x/","fileType":"link"}]}}}</script>',
    ],
)
def test_page_without_brochures_is_flagged_as_a_layout_change(html: str) -> None:
    result = HondaAdapter().discover(FixtureFetcher({LISTING: html, HOME: ""}))
    assert result.brochures == []
    assert [e.stage for e in result.errors] == ["parse"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("New City", "City"),
        ("All New ZR-V", "ZR-V"),
        ("All-New Elevate", "Elevate"),
        ("Amaze - 2nd Gen", "Amaze 2nd Gen"),
        ("Honda  Amaze", "Amaze"),
        ("New", None),
        ("", None),
        (None, None),
    ],
)
def test_model_hint_from_name(name: str | None, expected: str | None) -> None:
    assert model_hint_from_name(name) == expected


@pytest.mark.parametrize(
    ("media_url", "expected"),
    [
        ("/brochures/The New City Brochure_21.pdf", f"{MEDIA}brochures/The%20New%20City%20Brochure_21.pdf"),
        (
            "/brochures/pdfs/ZR-V%20Brochure_25July26.pdf",
            f"{MEDIA}brochures/pdfs/ZR-V%20Brochure_25July26.pdf",
        ),
        (
            "https://WWW.hondacarindia.com/web-data/brochures/pdfs/X.PDF",
            "https://www.hondacarindia.com/web-data/brochures/pdfs/X.PDF",
        ),
        ("https://www.hondacarindia.com/ownersmanual/webom/eng/jazz/2016/", None),
        ("/brochures/pdfs/readme.txt", None),
    ],
)
def test_brochure_url(media_url: str, expected: str | None) -> None:
    assert brochure_url(media_url, MEDIA) == expected


def test_layout_group_and_next_data_helpers() -> None:
    assert (
        layout_group_from_url(f"{MEDIA}brochures/The%20New%20City%20Brochure_21.pdf")
        == "the-new-city-brochure-21"
    )
    assert next_data('<script id="__NEXT_DATA__">{"props":{"pageProps":{"a":1}}}</script>') == {"a": 1}
    assert next_data('<script id="__NEXT_DATA__">[1]</script>') is None
    assert next_data("<html></html>") is None


def test_prices_from_home_page_ignores_cards_without_a_price() -> None:
    card = (
        '<h3 class="cpProdCard_prod-title__A"><span>{name}</span></h3>'
        '<p class="cpProdCard_desc__B">{desc}</p>'
    )
    html = card.format(name="New City", desc="Starts from ₹ on request") + card.format(
        name="New Elevate", desc="Coming soon"
    )
    assert prices_from_home_page(html, HOME) == []


def test_adapter_registers_with_official_scope() -> None:
    assert "honda" in registry.available_keys()
    adapter = registry.get_adapter("honda")
    assert isinstance(adapter, HondaAdapter) and not adapter.requires_browser
    assert adapter.is_official(LISTING) and adapter.is_official(next(iter(EXPECTED)))
    assert not adapter.is_official("https://nothondacarindia.com/x.pdf")
    assert not adapter.is_official("https://hondacarindia.example.net/x.pdf")
