"""Mahindra adapter against offline fixtures.

``tests/fixtures/mahindra`` holds synthetic pages built from the 13 brochure
URLs V1 discovered on 2026-03-07 (``v1_discovered_urls.json``), plus links that
the V1 rules rejected. Page URL shapes and the repeated link label follow what
the live site served on 2026-11-13.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from autohub.ingestion.adapters import registry
from autohub.ingestion.adapters.base import AdapterError, DiscoveryResult
from autohub.ingestion.adapters.mahindra import (
    MahindraAdapter,
    canonical_brochure_url,
    is_brochure_link,
    is_vehicle_page,
    layout_group_for,
    model_hint_from_file,
    model_hint_from_page,
)
from autohub.ingestion.html import extract_links

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mahindra"
SITE = "https://auto.mahindra.com"
LIBRARY = f"{SITE}/on/demandware.static/-/Sites-amc-Library/default"
V1_URLS: list[str] = json.loads((FIXTURES / "v1_discovered_urls.json").read_text(encoding="utf-8"))
# V1 stored URLs with the site's rotating cache fingerprint; V2 stores them without it.
EXPECTED_URLS = sorted(canonical_brochure_url(u) for u in V1_URLS)


class FixtureFetcher:
    """Serves saved pages; anything else is a 404, as on the live site."""

    def __init__(self, *, fail: dict[str, AdapterError] | None = None) -> None:
        self.rendered: list[str] = []
        self.plain: list[str] = []
        self._fail = fail or {}

    @property
    def pages_visited(self) -> int:
        return len(self.rendered) + len(self.plain)

    def get_html(self, url: str) -> str:
        self.plain.append(url)
        raise AssertionError("the Mahindra site is client-rendered; plain HTTP must not be used")

    def get_rendered_html(self, url: str) -> str:
        self.rendered.append(url)
        if url in self._fail:
            raise self._fail[url]
        name = "suv_listing.html" if url == f"{SITE}/suv/" else url.rsplit("/", 1)[-1]
        path = FIXTURES / name
        if not url.startswith(f"{SITE}/suv/") or not path.is_file():
            raise AdapterError("fetch", url, "HTTP 404")
        return path.read_text(encoding="utf-8")


@pytest.fixture()
def result_and_fetcher() -> tuple[DiscoveryResult, FixtureFetcher]:
    fetcher = FixtureFetcher()
    return MahindraAdapter().discover(fetcher), fetcher


def test_returns_exactly_the_brochures_v1_discovered(
    result_and_fetcher: tuple[DiscoveryResult, FixtureFetcher],
) -> None:
    result, _ = result_and_fetcher
    assert len(V1_URLS) == len(set(EXPECTED_URLS)) == 13
    assert sorted(b.source_url for b in result.brochures) == EXPECTED_URLS


def test_groups_into_ten_layout_groups_with_one_primary_each(
    result_and_fetcher: tuple[DiscoveryResult, FixtureFetcher],
) -> None:
    result, _ = result_and_fetcher
    sizes = Counter(b.layout_group for b in result.brochures)
    assert len(sizes) == 10
    assert {g for g, n in sizes.items() if n == 2} == {"thar-roxx", "scorpio-classic", "scorpio-big-daddy"}
    primaries = Counter(b.layout_group for b in result.brochures if b.is_primary_layout)
    assert primaries == Counter(dict.fromkeys(sizes, 1))


def test_primary_layout_choice_is_deterministic(
    result_and_fetcher: tuple[DiscoveryResult, FixtureFetcher],
) -> None:
    result, _ = result_and_fetcher
    primary = {
        b.layout_group: b.source_url.rsplit("/", 1)[-1] for b in result.brochures if b.is_primary_layout
    }
    assert primary["thar-roxx"] == "THAR-ROXX-Brochure.pdf"  # default layout beats Horizontal
    assert (
        primary["scorpio-big-daddy"] == "Scorpio_Big_Daddy_Vertical_Brochure.pdf"
    )  # Vertical beats Horizontal
    assert primary["scorpio-classic"] == "Scorpio-Classic-Vertical-Brochure.pdf"
    assert MahindraAdapter().discover(FixtureFetcher()).brochures == result.brochures


def test_two_different_thar_documents_are_not_merged(
    result_and_fetcher: tuple[DiscoveryResult, FixtureFetcher],
) -> None:
    result, _ = result_and_fetcher
    groups = {b.layout_group for b in result.brochures if b.source_page_url.endswith("/suv/thar.html")}
    assert groups == {"thar", "3-door-thar"}


def test_source_tracking_and_hints(result_and_fetcher: tuple[DiscoveryResult, FixtureFetcher]) -> None:
    result, _ = result_and_fetcher
    by_file = {b.source_url.rsplit("/", 1)[-1]: b for b in result.brochures}
    roxx = by_file["THAR-ROXX-Brochure-Horizontal.pdf"]
    assert roxx.brand == "Mahindra" and roxx.source_page_url == f"{SITE}/suv/thar-roxx/TH5D.html"
    assert roxx.source_url == f"{LIBRARY}/thar-roxx/THAR-ROXX-Brochure-Horizontal.pdf"  # no fingerprint
    assert roxx.title == "Download Brochure"
    # Hints come from the page's model slug, not from the file name.
    hints = {name: b.model_hint for name, b in by_file.items()}
    assert hints["THAR-ROXX-Brochure-Horizontal.pdf"] == hints["THAR-ROXX-Brochure.pdf"] == "Thar Roxx"
    assert hints["Scorpio_Big_Daddy_Vertical_Brochure.pdf"] == "Scorpio N"
    assert hints["XUV3XOProductBrochure-01-07-24.pdf"] == "XUV 3XO"
    assert hints["XUV-3XO-EV-Brochure.pdf"] == "XUV 3XO EV"
    assert hints["BoleroNeo-Brochure-19-Dec.pdf"] == "Bolero Neo"
    # Both Thar documents point at the one Thar model (V1 created duplicate variants here).
    assert (
        hints["Thar-Brochure-2025-NEW.pdf"]
        == hints["Mahindra-3-Door-Thar-Brochure-Vertical-Low-Res.pdf"]
        == "Thar"
    )
    assert all(b.source_url.startswith(f"{SITE}/") and "/dw" not in b.source_url for b in result.brochures)


def test_same_file_under_a_new_cache_fingerprint_is_one_brochure(
    result_and_fetcher: tuple[DiscoveryResult, FixtureFetcher],
) -> None:
    """bolero-neo.html links the PDF twice, under two fingerprints (as V1 and the live site saw it)."""
    result, _ = result_and_fetcher
    neo = [b for b in result.brochures if b.source_url.endswith("/BoleroNeo-Brochure-19-Dec.pdf")]
    assert len(neo) == 1 and neo[0].source_url == f"{LIBRARY}/NEO/BoleroNeo-Brochure-19-Dec.pdf"
    old = f"{LIBRARY}/dw27dcbbbe/NEO/BoleroNeo-Brochure-19-Dec.pdf"
    new = f"{LIBRARY}/dw702a971a/NEO/BoleroNeo-Brochure-19-Dec.pdf"
    assert canonical_brochure_url(old) == canonical_brochure_url(new) == neo[0].source_url
    # Only a fingerprint-shaped segment is removed.
    assert canonical_brochure_url(f"{LIBRARY}/dwarf/x.pdf?v=1") == f"{LIBRARY}/dwarf/x.pdf?v=1"


def test_only_official_suv_pages_are_visited_through_the_browser(
    result_and_fetcher: tuple[DiscoveryResult, FixtureFetcher],
) -> None:
    result, fetcher = result_and_fetcher
    assert fetcher.plain == []
    assert fetcher.rendered[0] == f"{SITE}/suv/"
    visited = fetcher.rendered[1:]
    assert visited == sorted(set(visited)) and len(visited) == 10  # 9 models + 1 dead link, each once
    assert all(u.startswith(f"{SITE}/suv/") and u.endswith(".html") for u in visited)
    assert result.pages_visited == 11


def test_problems_are_reported_without_stopping_discovery(
    result_and_fetcher: tuple[DiscoveryResult, FixtureFetcher],
) -> None:
    result, _ = result_and_fetcher
    errors = {(e.stage, e.url) for e in result.errors}
    assert errors == {
        ("fetch", f"{SITE}/suv/retired-model.html"),  # dead vehicle page
        ("scope", "https://cdn.example.net/files/Fake-Brochure.pdf"),  # brochure link off the official domain
    }


def test_one_failing_vehicle_page_does_not_lose_the_others() -> None:
    broken = f"{SITE}/suv/thar-roxx/TH5D.html"
    fetcher = FixtureFetcher(fail={broken: AdapterError("tls", broken, "certificate verification failed")})
    result = MahindraAdapter().discover(fetcher)
    assert len(result.brochures) == 11
    assert ("tls", broken) in {(e.stage, e.url) for e in result.errors}


def test_listing_failure_is_returned_as_data() -> None:
    listing = f"{SITE}/suv/"
    fetcher = FixtureFetcher(
        fail={listing: AdapterError("robots", listing, "disallowed by robots.txt; skipped")}
    )
    result = MahindraAdapter().discover(fetcher)
    assert result.brochures == [] and [e.stage for e in result.errors] == ["robots"]
    assert fetcher.rendered == [listing]


def test_empty_listing_is_flagged_as_a_layout_change() -> None:
    class EmptyListing(FixtureFetcher):
        def get_rendered_html(self, url: str) -> str:
            self.rendered.append(url)
            return "<html><body><a href='/'>Home</a></body></html>"

    result = MahindraAdapter().discover(EmptyListing())
    assert result.brochures == []
    assert [(e.stage, "no vehicle pages" in e.message) for e in result.errors] == [("parse", True)]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{LIBRARY}/dw1/XUV-7XO/brochures/XUV-7XO-Brochure.pdf", True),
        (f"{LIBRARY}/dw1/x/xuv-7xo-brochure.PDF", True),  # V1 matched 'Brochure' only; case no longer matters
        (f"{LIBRARY}/dw1/x/XUV%207XO%20Brochure.pdf", True),
        (f"{LIBRARY}/dw1/x/XUV-7XO-Brochure.pdf?v=3", True),
        (f"{LIBRARY}/dw1/x/XUV-7XO-Accessories-Brochure.pdf", False),
        (f"{LIBRARY}/dw1/x/Press-Release-Brochure.pdf", False),
        (f"{LIBRARY}/dw1/x/SUV-Range-Brochure.pdf", False),
        (f"{LIBRARY}/dw1/x/Special-Edition-Brochure.pdf", False),
        (f"{LIBRARY}/dw1/x/owners-manual.pdf", False),
        (f"{SITE}/suv/brochure.html", False),
        (f"{SITE}/suv/thar.html?file=Brochure.pdf", False),  # ".pdf" must be the path's extension
    ],
)
def test_brochure_link_rules(url: str, expected: bool) -> None:
    assert is_brochure_link(url) is expected


def test_vehicle_page_rule() -> None:
    assert is_vehicle_page(f"{SITE}/suv/thar-roxx.html")
    assert is_vehicle_page(f"{SITE}/suv/thar-roxx.html?ref=tile")
    assert not is_vehicle_page(f"{SITE}/suv/")
    assert not is_vehicle_page(f"{SITE}/electric/be-6.html")


def test_layout_group_and_hint_helpers() -> None:
    a = f"{LIBRARY}/dw1/SCN/Scorpio_Big_Daddy_Horizontal_Brochure.pdf"
    b = f"{LIBRARY}/dw2/SCN/Scorpio_Big_Daddy_Vertical_Brochure.pdf"
    assert layout_group_for(a) == layout_group_for(b) == "scorpio-big-daddy"
    assert model_hint_from_file(a) == "Scorpio Big Daddy"
    # "3" is part of the model identity; two-digit numbers and months are dates.
    assert (
        layout_group_for(f"{LIBRARY}/x/Mahindra-3-Door-Thar-Brochure-Vertical-Low-Res.pdf") == "3-door-thar"
    )
    assert layout_group_for(f"{LIBRARY}/x/BoleroNeo-Brochure-19-Dec.pdf") == "boleroneo"
    # A name made only of noise words still yields a stable, non-empty group.
    assert layout_group_for(f"{LIBRARY}/x/Brochure.pdf") == "brochure"
    assert model_hint_from_file(f"{LIBRARY}/x/Brochure.pdf") is None
    # Revision markers do not split a document into separate groups.
    assert (
        layout_group_for(f"{LIBRARY}/x/THAR-ROXX-Brochure-3rd.pdf")
        == layout_group_for(f"{LIBRARY}/x/THAR-ROXX-Brochure-Vertical-3rd.pdf")
        == "thar-roxx"
    )
    assert layout_group_for(f"{LIBRARY}/x/29-06-MM-3XO-EV-BROCHURE_R3.pdf") == "mm-3xo-ev"


def test_model_hint_from_page() -> None:
    assert model_hint_from_page(f"{SITE}/suv/scorpio-n/SCN.html") == "Scorpio N"
    assert model_hint_from_page(f"{SITE}/suv/xuv3xo-ev.html") == "XUV3XO EV"
    assert model_hint_from_page(f"{SITE}/suv/xuv-7xo.html") == "XUV 7XO"
    assert model_hint_from_page(f"{SITE}/suv/") is None
    assert model_hint_from_page(f"{SITE}/electric/be-6.html") is None


def test_adapter_is_registered() -> None:
    adapter = registry.get_adapter("mahindra")
    assert isinstance(adapter, MahindraAdapter) and adapter.requires_browser
    assert adapter.is_official(f"{LIBRARY}/x.pdf") and not adapter.is_official(
        "https://www.mahindra.com/x.pdf"
    )


def test_extract_links_resolves_and_filters() -> None:
    html = """
      <a href="/suv/thar.html#specs"> Thar\n   SUV </a>
      <a href="brochure.pdf"><span>Download</span> <b>PDF</b></a>
      <a href="javascript:void(0)">x</a><a href="mailto:a@b.co">mail</a><a href="#top">top</a>
      <a>no href</a><a href="  ">blank</a>
      <a href="https://other.example/a?b=1&amp;c=2">Other
    """
    links = extract_links(html, "https://auto.mahindra.com/suv/")
    assert [(link.url, link.text) for link in links] == [
        ("https://auto.mahindra.com/suv/thar.html", "Thar SUV"),
        ("https://auto.mahindra.com/suv/brochure.pdf", "Download PDF"),
        ("https://other.example/a?b=1&c=2", "Other"),
    ]
