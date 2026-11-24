"""MG adapter against an offline fixture.

``tests/fixtures/mg/brochures.html`` is a trimmed copy of the block structure and brochure
URLs that ``www.mgmotor.co.in/tools/brochures`` served on 2026-11-22 (``expected.json``:
brochure URL -> [model hint, title, is primary layout]), plus accessory catalogues, feature
leaflets, footer documents and two synthetic off-scope links that must be ignored.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autohub.ingestion.adapters import registry
from autohub.ingestion.adapters.base import AdapterError, DiscoveryResult
from autohub.ingestion.adapters.mg import (
    MGAdapter,
    anchor_titles,
    brochure_stem,
    canonical_url,
    layout_group_from_stem,
    model_hint_from_stem,
    model_hint_from_title,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mg"
LISTING = "https://www.mgmotor.co.in/tools/brochures"
SHARED = "https://s7ap1.scene7.com/is/content/mgmotor/mgmotor/documents"
OWN = "https://mgmotor.scene7.com/is/content/mgmotor/mgmotor/documents"
EXPECTED: dict[str, list[object]] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


class FixtureFetcher:
    """Serves the brochure page; anything else is a 404."""

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
        if url == LISTING:
            return self._html
        raise AdapterError("fetch", url, "HTTP 404")

    def get_rendered_html(self, url: str) -> str:
        raise AssertionError("the MG brochure page is server-rendered; no browser is needed")


@pytest.fixture()
def result() -> DiscoveryResult:
    return MGAdapter().discover(FixtureFetcher())


def test_returns_the_main_brochure_of_every_model(result: DiscoveryResult) -> None:
    assert len(EXPECTED) == 8
    assert [b.source_url for b in result.brochures] == sorted(EXPECTED)


def test_model_hint_title_and_primary_layout(result: DiscoveryResult) -> None:
    assert {b.source_url: [b.model_hint, b.title, b.is_primary_layout] for b in result.brochures} == EXPECTED


def test_one_layout_per_brochure_from_the_listing_page(result: DiscoveryResult) -> None:
    groups = [b.layout_group for b in result.brochures]
    assert len(set(groups)) == 8 and "mg-hector-tomahawk-phev-brochure" in groups
    assert all(b.brand == "MG" and b.source_page_url == LISTING for b in result.brochures)


def test_pdfs_move_from_the_shared_scene7_host_to_mgs_own(result: DiscoveryResult) -> None:
    assert all(
        b.source_url.startswith("https://mgmotor.scene7.com/is/content/mgmotor/") for b in result.brochures
    )


def test_accessories_leaflets_footer_documents_and_duplicates_are_ignored(result: DiscoveryResult) -> None:
    urls = [b.source_url.lower() for b in result.brochures]
    noise = ("accessor", "shield", "ismart", "i-smart", "adas", "charger", "scrapper", "depreciation")
    assert not any(w in u for u in urls for w in noise)
    # Linked twice, once with a cache-busting query string: still one brochure.
    assert urls.count(f"{OWN}/MG%20Hector%20-%20Brochure.pdf".lower()) == 1
    assert all("?" not in u for u in urls)


def test_off_scope_brochure_links_are_reported_not_returned(result: DiscoveryResult) -> None:
    assert [(e.stage, e.url) for e in result.errors] == [
        ("scope", "https://s7ap1.scene7.com/is/content/othercorp/docs/Roadster%20-%20Brochure.pdf"),
        ("scope", "https://cdn.example.net/mg/MG%20Gloster%20-%20Brochure.pdf"),
    ]


def test_one_plain_http_request() -> None:
    fetcher = FixtureFetcher()
    result = MGAdapter().discover(fetcher)
    assert fetcher.plain == [LISTING] and result.pages_visited == 1
    assert result.prices == []  # the site prints no ex-showroom prices in HTML


def test_fetch_failure_is_returned_as_data() -> None:
    result = MGAdapter().discover(FixtureFetcher(error=AdapterError("robots", LISTING, "disallowed")))
    assert result.brochures == [] and [e.stage for e in result.errors] == ["robots"]
    assert result.pages_visited == 1


def test_page_without_brochure_links_is_flagged_as_a_layout_change() -> None:
    html = f'<h2>MG HECTOR</h2><a href="{SHARED}/MG%20Hector%20Accessories%20-%20Brochure.pdf">x</a>'
    result = MGAdapter().discover(FixtureFetcher(html=html))
    assert result.brochures == []
    assert [(e.stage, "no brochure links" in e.message) for e in result.errors] == [("parse", True)]


def test_heading_then_file_name_are_the_fallback_model_hints() -> None:
    html = (
        f'<h2>MG GLOSTER</h2><a href="{SHARED}/MG%20Gloster%20-%20Brochure.pdf" title="Download Brochure">x</a>'
        f'<a href="{OWN}/MG%20Cyberster%20-%20Brochure.pdf"></a>'
    )
    hints = {
        b.source_url: (b.model_hint, b.title)
        for b in MGAdapter().discover(FixtureFetcher(html=html)).brochures
    }
    assert hints == {
        f"{OWN}/MG%20Gloster%20-%20Brochure.pdf": ("GLOSTER", "Download Brochure"),
        # Same heading still open, but no title: the heading wins over the file name.
        f"{OWN}/MG%20Cyberster%20-%20Brochure.pdf": ("GLOSTER", "MG GLOSTER"),
    }


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{SHARED}/MG%20Hector%20-%20Brochure.pdf", "MG Hector - Brochure"),
        (f"{SHARED}/MG%20ZSEV%20-%20Brochure.pdf?v=3", "MG ZSEV - Brochure"),
        (f"{OWN}/MG%20Astor%20-%20Brochure-v2.pdf", "MG Astor - Brochure-v2"),
        (f"{SHARED}/MG%20Hector%20Shield%20-%20Brochure.pdf", None),
        (f"{SHARED}/MG%20ZS%20EV%20iSmart2%20-%20Brochure.pdf", None),
        (f"{SHARED}/MG%20Hector%20Tomahawk%20EV%20Accessory%20-%20Brochure.pdf", None),
        (f"{SHARED}/Majestor%20Accessory%20-%20Brochure.pdf", None),
        (f"{SHARED}/i-Smart%20Brochure-v2.pdf", None),
        (f"{SHARED}/Hector%20ADAS%20Brochure.pdf", None),
        (f"{SHARED}/majestor-owners-manual.pdf", None),
        ("https://www.mgmotor.co.in/vehicles/mghector", None),
    ],
)  # fmt: skip
def test_brochure_stem(url: str, expected: str | None) -> None:
    assert brochure_stem(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{SHARED}/MG%20Hector%20-%20Brochure.pdf?v=2", f"{OWN}/MG%20Hector%20-%20Brochure.pdf"),
        ("https://S7D1.scene7.com/is/content/mgmotor/x.pdf", "https://mgmotor.scene7.com/is/content/mgmotor/x.pdf"),
        (f"{OWN}/a.pdf", f"{OWN}/a.pdf"),
        # Another Scene7 customer's asset stays on the shared host, which is not official.
        ("https://s7ap1.scene7.com/is/content/othercorp/a.pdf", "https://s7ap1.scene7.com/is/content/othercorp/a.pdf"),
        ("https://www.mgmotor.co.in/content/dam/mgmotor/documents/a.pdf", "https://www.mgmotor.co.in/content/dam/mgmotor/documents/a.pdf"),
    ],
)  # fmt: skip
def test_canonical_url(url: str, expected: str) -> None:
    assert canonical_url(url) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("MG Windsor EV Main Brochure", "Windsor EV"),
        ("Majestor Main Brochure", "Majestor"),
        ("MG Hector Brochure", "Hector"),
        ("MG HECTOR", "HECTOR"),
        ("JSW MG Comet EV", "Comet EV"),
        ("Download Brochure", None),
        ("MG", None),
        ("", None),
        (None, None),
    ],
)
def test_model_hint_from_title(title: str | None, expected: str | None) -> None:
    assert model_hint_from_title(title) == expected


def test_stem_helpers_and_anchor_titles() -> None:
    assert model_hint_from_stem("MG ZSEV - Brochure") == "ZSEV"
    assert model_hint_from_stem("MG Hector Tomahawk PHEV - Brochure") == "Hector Tomahawk PHEV"
    assert layout_group_from_stem("MG Hector Tomahawk EV - Brochure") == "mg-hector-tomahawk-ev-brochure"
    html = '<a href="/a.pdf#p2" title="  MG  Astor Brochure "></a><a href="/b.pdf"></a>'
    assert anchor_titles(html, LISTING) == {"https://www.mgmotor.co.in/a.pdf": "MG Astor Brochure"}


def test_adapter_registers_with_official_scope() -> None:
    assert "mg" in registry.available_keys()
    adapter = registry.get_adapter("mg")
    assert isinstance(adapter, MGAdapter) and not adapter.requires_browser
    assert adapter.is_official(LISTING) and adapter.is_official(f"{OWN}/x.pdf")
    assert not adapter.is_official(f"{SHARED}/x.pdf")  # shared Adobe host
    assert not adapter.is_official("https://notmgmotor.co.in/x.pdf")
    assert not adapter.is_official("https://mgmotor.co.in.example.net/x.pdf")
