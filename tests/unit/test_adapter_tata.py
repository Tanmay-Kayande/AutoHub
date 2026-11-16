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
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tata"
LISTING = "https://tata.cars/support/brochures.html"
PRODUCTS = "https://tata.cars/content/dam/tml/pv/products"
EXPECTED: dict[str, list[str]] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


class FixtureFetcher:
    def __init__(self, html: str | None = None, error: AdapterError | None = None) -> None:
        self.plain: list[str] = []
        self._html = html if html is not None else (FIXTURES / "brochures.html").read_text(encoding="utf-8")
        self._error = error

    @property
    def pages_visited(self) -> int:
        return len(self.plain)

    def get_html(self, url: str) -> str:
        self.plain.append(url)
        if self._error is not None:
            raise self._error
        return self._html

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


def test_one_plain_http_request_and_no_browser() -> None:
    fetcher = FixtureFetcher()
    result = TataAdapter().discover(fetcher)
    assert fetcher.plain == [LISTING] and result.pages_visited == 1


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
