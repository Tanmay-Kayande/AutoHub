"""Tata adapter against an offline fixture.

``tests/fixtures/tata/brochures.html`` is a small synthetic page with the block
structure, titles and brochure URLs that ``tata.cars/support/brochures.html`` served
on 2026-11-16 (``expected.json``: brochure URL -> [model hint, title]), plus links
that must be ignored.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autohub.ingestion.adapters import registry
from autohub.ingestion.adapters.base import AdapterError, DiscoveryResult
from autohub.ingestion.adapters.tata import (
    TataAdapter,
    brochure_parts,
    layout_group_from_stem,
    model_hint_from_heading,
    model_hint_from_slug,
    price_from_page,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tata"
LISTING = "https://tata.cars/support/brochures.html"
PRODUCTS = "https://tata.cars/content/dam/tml/pv/products"
EXPECTED: dict[str, list[str]] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


SITEMAP = "https://tata.cars/sitemap.xml"
PRICE_PAGES = {
    "https://tata.cars/nexon/ice/price.html": "Tata Nexon Price in India – From ₹7.39 L* Onwards | TATA.CARS",
    "https://tata.cars/punch/ice/price.html": "Tata Punch Price - All New Tata Punch Price Starts at ₹ 5.74 Lakh*",
    "https://tata.cars/tigor/ice/price.html": "Tata Tigor | TATA.CARS",  # no price in the title
    # Not in the sitemap: found by trying the path of a model that has a brochure.
    "https://tata.cars/aeris/ice/price.html": "Tata Aeris Price Starts at ₹5.29 Lakh* Ex-showroom | TATA.CARS",
}
SITEMAP_XML = (
    '<?xml version="1.0"?><urlset>'
    "<url><loc>https://tata.cars/punch/ice/price.html</loc></url>"
    "<url><loc>https://tata.cars/nexon/ice/price.html</loc></url>"
    "<url><loc>https://tata.cars/tigor/ice/price.html</loc></url>"
    "<url><loc>https://tata.cars/nexon/ice.html</loc></url>"
    "<url><loc>https://tata.example.net/nexon/ice/price.html</loc></url>"
    "</urlset>"
)


class FixtureFetcher:
    """Serves the brochure page, a sitemap and price pages; anything else is a 404."""

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
        # A test that supplies its own brochure page gets an empty sitemap unless it asks for more.
        self._sitemap = sitemap if sitemap is not None else (SITEMAP_XML if full else "<urlset></urlset>")
        self._pages = pages if pages is not None else (PRICE_PAGES if full else {})

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
            return f"<html><head><title>{self._pages[url]}</title></head><body></body></html>"
        raise AdapterError("fetch", url, "HTTP 404")

    def get_rendered_html(self, url: str) -> str:
        raise AssertionError("the Tata brochure page is server-rendered; no browser is needed")


def _block(heading: str, title: str, href: str) -> str:
    return f'<h6>{heading}</h6><ul><li><p class="link-btn">{title}</p><a href="{href}" class="files"></a></li></ul>'


@pytest.fixture()
def result() -> DiscoveryResult:
    return TataAdapter().discover(FixtureFetcher())


def test_returns_the_brochure_set(result: DiscoveryResult) -> None:
    assert len(EXPECTED) == 16
    assert [b.source_url for b in result.brochures] == sorted(EXPECTED)


def test_model_hint_is_the_block_heading_and_title_is_the_file_label(result: DiscoveryResult) -> None:
    assert {b.source_url: [b.model_hint, b.title] for b in result.brochures} == EXPECTED
    by_stem = {b.layout_group: b for b in result.brochures}
    # The folder is "xpres-t"; the site calls the model "XPRES".
    assert by_stem["xpres-brochure"].model_hint == "XPRES"
    # An edition's own label does not name the model; the block heading does.
    assert by_stem["harrier-red-dark-brochure"].model_hint == "Harrier"
    assert by_stem["harrier-red-dark-brochure"].title == "Red #DARK"


def test_each_brochure_is_its_own_primary_layout_with_source_tracking(result: DiscoveryResult) -> None:
    assert len({b.layout_group for b in result.brochures}) == len(result.brochures)
    for brochure in result.brochures:
        assert brochure.brand == "Tata" and brochure.is_primary_layout
        assert brochure.source_page_url == LISTING


def test_plain_http_only_brochure_page_first_then_sitemap_and_price_pages() -> None:
    fetcher = FixtureFetcher()
    result = TataAdapter().discover(fetcher)
    assert fetcher.plain[:2] == [LISTING, SITEMAP]
    # Three price pages from the sitemap, then one guess per brochure model not listed there.
    assert fetcher.plain[2:5] == sorted(u for u in PRICE_PAGES if "aeris" not in u)
    assert "https://tata.cars/aeris/ice/price.html" in fetcher.plain[5:]
    assert all(u.endswith("/ice/price.html") for u in fetcher.plain[2:])
    assert result.pages_visited == len(fetcher.plain) == len(set(fetcher.plain))


def test_a_model_served_at_two_paths_is_reported_once_from_its_own_path() -> None:
    aeris_title = PRICE_PAGES["https://tata.cars/aeris/ice/price.html"]
    pages = {
        **PRICE_PAGES,
        "https://tata.cars/tigor/ice/price.html": aeris_title,
    }  # retired path, successor's page
    result = TataAdapter().discover(FixtureFetcher(pages=pages))
    aeris = [p for p in result.prices if p.model_hint == "Aeris"]
    assert [p.source_url for p in aeris] == ["https://tata.cars/aeris/ice/price.html"]
    assert len(result.prices) == 3


def test_starting_prices_come_from_price_page_titles(result: DiscoveryResult) -> None:
    prices = {p.model_hint: p for p in result.prices}
    assert {name: (int(p.price_min), p.price_max) for name, p in prices.items()} == {
        "Nexon": (739_000, None),
        "Punch": (574_000, None),
        "Aeris": (529_000, None),
    }
    assert prices["Nexon"].source_url == "https://tata.cars/nexon/ice/price.html"
    assert all(p.brand == "Tata" for p in result.prices)


def test_missing_guessed_price_pages_are_not_errors_but_listed_ones_are() -> None:
    # Every model with a brochure but no sitemap entry 404s here: silent.
    quiet = TataAdapter().discover(
        FixtureFetcher(pages={u: t for u, t in PRICE_PAGES.items() if "aeris" not in u})
    )
    assert [e.stage for e in quiet.errors] == ["scope"]  # only the fixture's off-domain brochure link
    # A page the sitemap lists is expected to exist.
    broken = TataAdapter().discover(FixtureFetcher(pages={}))
    assert sorted(e.url for e in broken.errors if e.stage == "fetch") == sorted(
        u for u in PRICE_PAGES if "aeris" not in u
    )
    assert broken.prices == [] and len(broken.brochures) == 16


def test_sitemap_failure_is_reported_and_brochures_are_still_returned() -> None:
    class NoSitemap(FixtureFetcher):
        def get_html(self, url: str) -> str:
            if url == SITEMAP:
                self.plain.append(url)
                raise AdapterError("fetch", url, "HTTP 503")
            return super().get_html(url)

    result = TataAdapter().discover(NoSitemap())
    assert len(result.brochures) == 16
    assert ("fetch", SITEMAP) in [(e.stage, e.url) for e in result.errors]
    # The guessed path still finds the one model whose page exists outside the sitemap list.
    assert [p.model_hint for p in result.prices] == ["Aeris", "Nexon", "Punch"]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Tata Nexon Price in India – From ₹7.39 L* Onwards | TATA.CARS", ("Nexon", 739_000)),
        ("Tata Punch Price - All New Tata Punch Price Starts at ₹ 5.74 Lakh*", ("Punch", 574_000)),
        ("Tata Aeris Price Starts at ₹5.29 Lakh* Ex-showroom | TATA.CARS", ("Aeris", 529_000)),
        ("Tata Tigor | TATA.CARS", None),
        ("Price list from ₹7.39 L*", None),  # no model name
        ("Tata Nexon Price in India", None),
    ],
)
def test_price_from_page(title: str, expected: tuple[str, int] | None) -> None:
    price = price_from_page(f"<html><head><title>\n {title}\n</title></head></html>", "https://tata.cars/x")
    assert (None if price is None else (price.model_hint, int(price.price_min))) == expected
    assert price_from_page("<html><body>no title</body></html>", "https://tata.cars/x") is None


def test_accessory_catalogues_duplicates_and_other_links_are_ignored(result: DiscoveryResult) -> None:
    urls = [b.source_url for b in result.brochures]
    assert not any("TMGA" in u or "terms-and-conditions" in u or u.endswith(".html") for u in urls)
    # The Harrier brochure is linked again with a cache-busting query string: still one brochure.
    harrier = f"{PRODUCTS}/harrier/year-2026/ice/promoting-vc/brouchers/june/harrier-master-brochure.pdf"
    assert urls.count(harrier) == 1
    assert next(b for b in result.brochures if b.source_url == harrier).title == "Harrier Brochure"
    assert all("?" not in u for u in urls)


def test_off_domain_brochure_link_is_reported_not_returned(result: DiscoveryResult) -> None:
    assert [(e.stage, urlhost(e.url)) for e in result.errors] == [("scope", "cdn.example.net")]
    assert all(b.source_url.startswith("https://tata.cars/") for b in result.brochures)


def urlhost(url: str) -> str:
    return url.split("/")[2]


def test_fetch_failure_is_returned_as_data() -> None:
    fetcher = FixtureFetcher(error=AdapterError("tls", LISTING, "certificate verification failed"))
    result = TataAdapter().discover(fetcher)
    assert result.brochures == [] and [e.stage for e in result.errors] == ["tls"]
    assert result.pages_visited == 1


def test_page_without_brochure_links_is_flagged_as_a_layout_change() -> None:
    result = TataAdapter().discover(FixtureFetcher(html="<html><body><h6>Nexon</h6><a href='/'>Home</a>"))
    assert result.brochures == []
    assert [(e.stage, "no brochure links" in e.message) for e in result.errors] == [("parse", True)]


def test_accessory_label_excludes_a_file_whose_name_does_not_say_so() -> None:
    html = _block(
        "Punch Brochures", "Punch Accessories", "/content/dam/tml/pv/products/punch/x/punch-kit.pdf"
    )
    result = TataAdapter().discover(FixtureFetcher(html=html))
    # Nothing usable on the page is reported the same way as a layout change.
    assert result.brochures == [] and [e.stage for e in result.errors] == ["parse"]


def test_folder_name_is_the_fallback_hint_and_anchor_text_the_fallback_title() -> None:
    html = '<a href="/content/dam/tml/pv/products/xpres-t/year-2026/xpres-brochure.pdf">Download</a>'
    result = TataAdapter().discover(FixtureFetcher(html=html))
    assert [(b.model_hint, b.title) for b in result.brochures] == [("Xpres T", "Download")]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{PRODUCTS}/nexon/year-2026/ice/promoting-vc/brochures/may/nexon-brochure-may.pdf", ("nexon", "nexon-brochure-may")),
        (f"{PRODUCTS}/curvv/year-2026/CURVV_Series-X_Brochure.PDF", ("curvv", "curvv_series-x_brochure")),
        (f"{PRODUCTS}/punch/punch.pdf?v=3", ("punch", "punch")),
        (f"{PRODUCTS}/punch.pdf", None),  # no model folder
        ("https://tata.cars/content/dam/tml/pv/common/terms-and-conditions.pdf", None),
        ("https://tata.cars/content/dam/tml/cv/products/ace/ace-brochure.pdf", None),  # commercial vehicles
        (f"{PRODUCTS}/nexon/ice.html", None),
    ],
)  # fmt: skip
def test_brochure_parts(url: str, expected: tuple[str, str] | None) -> None:
    assert brochure_parts(url) == expected


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Harrier Brochures", "Harrier"),
        ("Sierra Brochure", "Sierra"),
        ("XPRES brochure", "XPRES"),
        ("Curvv", "Curvv"),
        ("Brochures", None),
        ("", None),
        (None, None),
    ],
)
def test_model_hint_from_heading(heading: str | None, expected: str | None) -> None:
    assert model_hint_from_heading(heading) == expected


def test_slug_and_layout_group_helpers() -> None:
    assert model_hint_from_slug("xpres-t") == "Xpres T"
    assert layout_group_from_stem("CURVV_Series-X_Brochure") == "curvv-series-x-brochure"
    assert layout_group_from_stem("altroz-brochure-may-2026-new") == "altroz-brochure-may-2026-new"


def test_adapter_is_registered_with_official_scope() -> None:
    assert "tata" in registry.available_keys()
    adapter = registry.get_adapter("tata")
    assert isinstance(adapter, TataAdapter) and not adapter.requires_browser
    assert adapter.is_official(LISTING) and adapter.is_official("https://www.tata.cars/x.pdf")
    # The old hosts with the expired certificate are out of scope, as are look-alikes.
    assert not adapter.is_official("https://cars.tatamotors.com/x.pdf")
    assert not adapter.is_official("https://nottata.cars/x.pdf")
