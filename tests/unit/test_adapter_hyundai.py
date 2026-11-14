"""Hyundai adapter against an offline fixture.

``tests/fixtures/hyundai/find_a_car.html`` is a small synthetic page with the
card structure, model names and brochure file names the live site served on
2026-11-13 (``expected.json``: file stem -> card title), plus links that must
be ignored.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autohub.ingestion.adapters import registry
from autohub.ingestion.adapters.base import AdapterError, DiscoveryResult
from autohub.ingestion.adapters.hyundai import HyundaiAdapter, brochure_slug, model_hint_from_slug
from autohub.ingestion.html import extract_links

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hyundai"
LISTING = "https://www.hyundai.com/in/en/find-a-car"
BROCHURES = "https://www.hyundai.com/content/dam/hyundai/in/en/data/brochure"
EXPECTED: dict[str, str] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


class FixtureFetcher:
    def __init__(self, html: str | None = None, error: AdapterError | None = None) -> None:
        self.plain: list[str] = []
        self._html = html if html is not None else (FIXTURES / "find_a_car.html").read_text(encoding="utf-8")
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
        raise AssertionError("the Hyundai page is server-rendered; no browser is needed")


@pytest.fixture()
def result() -> DiscoveryResult:
    return HyundaiAdapter().discover(FixtureFetcher())


def test_returns_the_brochure_set(result: DiscoveryResult) -> None:
    assert len(EXPECTED) == 16
    assert [b.source_url for b in result.brochures] == sorted(f"{BROCHURES}/{slug}.pdf" for slug in EXPECTED)


def test_model_hint_is_the_card_title(result: DiscoveryResult) -> None:
    hints = {b.layout_group: b.model_hint for b in result.brochures}
    assert hints == EXPECTED
    # Names the file name alone would get wrong.
    assert hints["grand-i10-nios"] == "Grand i10 NIOS"
    assert hints["creta-ev"] == "Creta Electric"
    assert hints["nios-prime-hb"] == "Prime-HB"


def test_each_brochure_is_its_own_primary_layout_with_source_tracking(result: DiscoveryResult) -> None:
    assert len({b.layout_group for b in result.brochures}) == len(result.brochures)
    for brochure in result.brochures:
        assert brochure.brand == "Hyundai" and brochure.is_primary_layout
        assert brochure.source_page_url == LISTING and brochure.title == "Brochure"


def test_one_plain_http_request_and_no_browser() -> None:
    fetcher = FixtureFetcher()
    result = HyundaiAdapter().discover(fetcher)
    assert fetcher.plain == [LISTING] and result.pages_visited == 1


def test_duplicates_and_non_brochure_links_are_ignored(result: DiscoveryResult) -> None:
    urls = [b.source_url for b in result.brochures]
    # creta.pdf is linked again with a cache-busting query string: still one brochure, first card's title.
    assert urls.count(f"{BROCHURES}/creta.pdf") == 1
    assert next(b for b in result.brochures if b.layout_group == "creta").model_hint == "Creta"
    assert not any("price-list" in u or "accessories" in u or u.endswith(".html") for u in urls)
    assert all("?" not in u for u in urls)


def test_off_domain_brochure_link_is_reported_not_returned(result: DiscoveryResult) -> None:
    assert [(e.stage, e.url) for e in result.errors] == [
        ("scope", "https://cdn.example.net/content/dam/hyundai/in/en/data/brochure/creta.pdf")
    ]
    assert all(b.source_url.startswith("https://www.hyundai.com/") for b in result.brochures)


def test_fetch_failure_is_returned_as_data() -> None:
    fetcher = FixtureFetcher(error=AdapterError("tls", LISTING, "certificate verification failed"))
    result = HyundaiAdapter().discover(fetcher)
    assert result.brochures == [] and [e.stage for e in result.errors] == ["tls"]
    assert result.pages_visited == 1


def test_page_without_brochure_links_is_flagged_as_a_layout_change() -> None:
    result = HyundaiAdapter().discover(
        FixtureFetcher(html="<html><body><h3>Creta</h3><a href='/in/en'>Home</a>")
    )
    assert result.brochures == []
    assert [(e.stage, "no brochure links" in e.message) for e in result.errors] == [("parse", True)]


def test_file_name_is_the_fallback_hint_when_a_card_has_no_title() -> None:
    html = '<a href="/content/dam/hyundai/in/en/data/brochure/creta-ev.pdf">Brochure</a>'
    result = HyundaiAdapter().discover(FixtureFetcher(html=html))
    assert [b.model_hint for b in result.brochures] == ["Creta EV"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{BROCHURES}/creta.pdf", "creta"),
        (f"{BROCHURES}/Grand-i10-NIOS.PDF", "grand-i10-nios"),
        (f"{BROCHURES}/creta.pdf?v=2", "creta"),
        (f"{BROCHURES}/accessories/creta-accessories.pdf", None),  # sub-folder: not a model brochure
        ("https://www.hyundai.com/content/dam/hyundai/in/en/data/price-list/creta.pdf", None),
        ("https://www.hyundai.com/content/dam/hyundai/kr/ko/data/brochure/creta.pdf", None),  # other market
        (f"{BROCHURES}/creta.html", None),
    ],
)
def test_brochure_slug(url: str, expected: str | None) -> None:
    assert brochure_slug(url) == expected


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("creta-ev", "Creta EV"),
        ("grand-i10-nios", "Grand I10 NIOS"),
        ("ioniq-5", "Ioniq 5"),
        ("venue-n-line", "Venue N Line"),
    ],
)
def test_model_hint_from_slug(slug: str, expected: str) -> None:
    assert model_hint_from_slug(slug) == expected


def test_adapter_is_registered() -> None:
    adapter = registry.get_adapter("hyundai")
    assert isinstance(adapter, HyundaiAdapter) and not adapter.requires_browser
    assert {"hyundai", "mahindra"} <= set(registry.available_keys())


def test_links_carry_the_nearest_preceding_heading() -> None:
    html = """
      <a href="/before">no heading yet</a>
      <h3> Grand   i10 <b>NIOS</b> </h3><p>text</p><a href="/a">A</a><a href="/b">B</a>
      <h2>Ignored level</h2>
      <h3>Creta</h3><a href="/c">C</a>
      <h3></h3><a href="/d">D</a>
    """
    links = extract_links(html, "https://www.hyundai.com/", heading_tags=("h3",))
    assert [(link.url.rsplit("/", 1)[-1], link.heading) for link in links] == [
        ("before", None),
        ("a", "Grand i10 NIOS"),
        ("b", "Grand i10 NIOS"),
        ("c", "Creta"),
        ("d", None),
    ]
    assert all(link.heading is None for link in extract_links(html, "https://www.hyundai.com/"))
