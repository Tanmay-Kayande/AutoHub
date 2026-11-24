"""Maruti Suzuki adapter against offline fixtures.

``tests/fixtures/maruti/{arena,nexa}.html`` are small synthetic copies of the rendered
``car-brochure`` block of ``www.marutisuzuki.com/arena/car-brochure`` and
``www.nexaexperience.com/e-brochure`` as served on 2026-11-22: real card structure, model
names, prices and brochure URLs, plus links that must be ignored (navigation, footer, an
off-domain mirror, a cache-busted duplicate). ``expected.json``: brochure URL ->
[model hint, title, is primary layout].
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autohub.ingestion.adapters import registry
from autohub.ingestion.adapters.base import AdapterError, DiscoveryResult
from autohub.ingestion.adapters.maruti import (
    BrochureCard,
    MarutiAdapter,
    brochure_stem,
    canonical_url,
    is_accessories,
    is_hindi,
    layout_group_from_stem,
    model_hint,
    model_hint_from_slug,
    parse_cards,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "maruti"
ARENA = "https://www.marutisuzuki.com/arena/car-brochure"
NEXA = "https://www.nexaexperience.com/e-brochure"
S7 = "https://marutisuzuki.scene7.com/is/content/maruti"
EXPECTED: dict[str, list[object]] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
PAGES = {
    ARENA: (FIXTURES / "arena.html").read_text(encoding="utf-8"),
    NEXA: (FIXTURES / "nexa.html").read_text(encoding="utf-8"),
}


class FixtureFetcher:
    """Serves the two rendered brochure pages; anything else is a 404."""

    def __init__(
        self, pages: dict[str, str] | None = None, errors: dict[str, AdapterError] | None = None
    ) -> None:
        self.rendered: list[str] = []
        self._pages = pages if pages is not None else PAGES
        self._errors = errors or {}

    @property
    def pages_visited(self) -> int:
        return len(self.rendered)

    def get_html(self, url: str) -> str:
        raise AssertionError("the brochure cards are built by JavaScript; plain HTML has none")

    def get_rendered_html(self, url: str) -> str:
        self.rendered.append(url)
        if url in self._errors:
            raise self._errors[url]
        if url in self._pages:
            return self._pages[url]
        raise AdapterError("fetch", url, "HTTP 404")


def _card(code: str, alt: str, explore: str, *files: tuple[str, str]) -> str:
    """A minimal rendered card: logo, explore link, the file options and the default button."""
    options = "".join(f'<custom-option value="{u}">{label}</custom-option>' for u, label in files)
    return (
        f'<div id="car-card-content-{code}"><img alt="{alt} car"><img alt="{alt}">'
        f'<a class="explore-link" href="{explore}">Explore</a>'
        f'<div class="car-price-label">Ex-showroom</div><span class="price-span">₹ 6.49 Lakh</span>'
        f'<custom-select><div class="selected-option">{files[0][1]}</div>{options}</custom-select>'
        f'<a class="download-btn" href="{files[0][0]}">Download</a></div>'
    )


@pytest.fixture()
def result() -> DiscoveryResult:
    return MarutiAdapter().discover(FixtureFetcher())


def test_returns_every_brochure_of_both_channels(result: DiscoveryResult) -> None:
    assert len(EXPECTED) == 25
    assert [b.source_url for b in result.brochures] == sorted(EXPECTED)
    by_page = {page: sum(b.source_page_url == page for b in result.brochures) for page in PAGES}
    assert by_page == {ARENA: 17, NEXA: 8}


def test_model_hint_title_and_primary_layout(result: DiscoveryResult) -> None:
    assert {b.source_url: [b.model_hint, b.title, b.is_primary_layout] for b in result.brochures} == EXPECTED


def test_hindi_brochure_is_the_alternative_layout_of_the_default_file(result: DiscoveryResult) -> None:
    alternatives = [b for b in result.brochures if not b.is_primary_layout]
    assert sorted(b.model_hint or "" for b in alternatives) == ["Alto K10", "Eeco", "S-Presso", "WagonR"]
    groups = {b.layout_group: b for b in result.brochures if b.is_primary_layout}
    for alt in alternatives:
        primary = groups[alt.layout_group]
        assert primary.model_hint == alt.model_hint and "hindi" not in primary.source_url.lower()
    assert len(groups) == 21  # every primary brochure has its own group
    assert all(b.brand == "Maruti Suzuki" for b in result.brochures)


def test_extra_brochures_of_a_model_are_primary_on_their_own(result: DiscoveryResult) -> None:
    titles = {b.title for b in result.brochures if b.is_primary_layout}
    assert {"CNG BROCHURE", "Eeco Ambulance Brochure", "WagonR Swivel Seat", "EECO Star Brochure"} <= titles


def test_accessories_tour_range_noise_and_duplicates_are_ignored(result: DiscoveryResult) -> None:
    urls = [b.source_url for b in result.brochures]
    assert not any("ccessor" in u for u in urls)  # includes the "ACCESOORIES" spelling
    assert not any("tour" in u.lower() for u in urls)  # Tour cards link to marutisuzukicommercial.com
    assert not any(w in u.lower() for u in urls for w in ("privacy", "footer", "manual-image"))
    swift = f"{S7}/Swift%20Brochure%20-%20Vertical%20-%205th%20Octpdf"
    assert urls.count(swift) == 1  # also listed with "?v=2"
    assert all("?" not in u and " " not in u for u in urls)


def test_off_domain_brochure_option_is_reported_not_returned(result: DiscoveryResult) -> None:
    assert [(e.stage, e.url.split("/")[2]) for e in result.errors] == [("scope", "cdn.example.net")]


def test_renders_each_channel_page_once() -> None:
    fetcher = FixtureFetcher()
    result = MarutiAdapter().discover(fetcher)
    assert fetcher.rendered == [ARENA, NEXA]
    assert result.pages_visited == 2


def test_prices_from_the_cards_skip_the_battery_subscription_price(result: DiscoveryResult) -> None:
    prices = {p.model_hint: (int(p.price_min), p.price_max and int(p.price_max)) for p in result.prices}
    assert prices == {
        "S-Presso": (350_000, None),
        "Alto K10": (370_000, None),
        "Celerio": (470_000, None),
        "WagonR": (499_000, None),
        "Eeco": (528_000, None),
        "Swift": (584_000, None),
        "Dzire": (631_000, None),
        "Brezza": (750_000, None),
        "Ertiga": (890_000, None),
        "Victoris": (1_050_000, None),
        "Baleno": (599_000, 999_000),
        "Invicto": (2_502_000, 2_880_000),
        "Jimny": (1_259_000, 1_467_000),
        "XL6": (1_162_000, 1_468_000),
        "Grand Vitara": (1_077_000, 1_977_000),
        "FRONX": (690_000, 1_203_000),
    }
    assert "e VITARA" not in prices  # "Introductory BaaS price"
    assert {p.source_url for p in result.prices} == set(PAGES)


def test_a_failing_page_is_an_error_and_the_other_channel_still_counts() -> None:
    fetcher = FixtureFetcher(errors={ARENA: AdapterError("render", ARENA, "timeout")})
    result = MarutiAdapter().discover(fetcher)
    assert [(e.stage, e.url) for e in result.errors] == [("render", ARENA)]
    assert len(result.brochures) == 8 and {b.source_page_url for b in result.brochures} == {NEXA}
    assert fetcher.rendered == [ARENA, NEXA]


def test_every_page_failing_returns_errors_not_an_exception() -> None:
    errors = {url: AdapterError("robots", url, "disallowed") for url in PAGES}
    result = MarutiAdapter().discover(FixtureFetcher(errors=errors))
    assert result.brochures == [] and result.prices == []
    assert [e.stage for e in result.errors] == ["robots", "robots"]


def test_unrendered_page_is_flagged_as_a_layout_change() -> None:
    plain = '<div class="car-brochure"><div>Download Brochures</div></div>'
    result = MarutiAdapter().discover(FixtureFetcher(pages={ARENA: plain, NEXA: PAGES[NEXA]}))
    assert [(e.stage, e.url, "no brochure links" in e.message) for e in result.errors] == [
        ("parse", ARENA, True)
    ]
    assert len(result.brochures) == 8


def test_only_hindi_file_is_primary_and_slug_is_the_fallback_hint() -> None:
    html = _card("XX", "undefined", "/arena/alto-k10", (f"{S7}/AltoK10-Brochure-Hindipdf-1", "हिंदी ब्रोशर"))
    result = MarutiAdapter().discover(FixtureFetcher(pages={ARENA: html, NEXA: PAGES[NEXA]}))
    arena = [b for b in result.brochures if b.source_page_url == ARENA]
    assert [(b.model_hint, b.is_primary_layout) for b in arena] == [("Alto K10", True)]


def test_parse_cards_reads_every_field() -> None:
    html = _card(
        "BZ",
        "Baleno",
        "/baleno",
        (f"{S7}/Baleno-Brochurepdf", "E-BROCHURE"),
        (f"{S7}/Accessories_Brochurepdf", "ACC"),
    )
    [card] = parse_cards(html, NEXA)
    assert card == BrochureCard(
        code="BZ",
        logo_alt="Baleno",
        explore_url="https://www.nexaexperience.com/baleno",
        price_label="Ex-showroom",
        price="₹ 6.49 Lakh",
        selected="E-BROCHURE",
        options=[(f"{S7}/Baleno-Brochurepdf", "E-BROCHURE"), (f"{S7}/Accessories_Brochurepdf", "ACC")],
        download_url=f"{S7}/Baleno-Brochurepdf",
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{S7}/Arena-Alto-K10-Brochurepdf", "Arena-Alto-K10-Brochurepdf"),
        (f"{S7}/Swift Brochure - Vertical - 5th Octpdf", "Swift Brochure - Vertical - 5th Octpdf"),
        (f"{S7}/Brezza%20Brochurepdf?v=1", "Brezza Brochurepdf"),
        ("http://marutisuzuki.scene7.com/is/content/maruti/X-Brochurepdf", None),  # not https
        ("https://marutisuzuki.scene7.com/is/image/maruti/Alto_logo", None),  # an image
        ("https://www.marutisuzuki.com/arena/swift", None),
    ],
)  # fmt: skip
def test_brochure_stem(url: str, expected: str | None) -> None:
    assert brochure_stem(url) == expected


def test_canonical_url_and_layout_group() -> None:
    assert canonical_url(f"{S7}/Victoris Brochurepdf?v=3") == f"{S7}/Victoris%20Brochurepdf"
    assert canonical_url(f"{S7}/Victoris%20Brochurepdf") == f"{S7}/Victoris%20Brochurepdf"
    assert canonical_url("https://MarutiSuzuki.Scene7.com/is/content/maruti/A") == f"{S7}/A"
    assert layout_group_from_stem("e VITARA Web Brochure Sep_26pdf") == "e-vitara-web-brochure-sep-26pdf"


def test_label_classifiers() -> None:
    assert is_hindi("हिंदी ब्रोशर", f"{S7}/S-presso-BrochureHindipdf-1")
    assert is_hindi("Brochure", f"{S7}/Hindi-WagonR-Brochurepdf-1")
    assert not is_hindi("E-brochure S-Presso", f"{S7}/S-presso-Brochurepdf-2")
    assert is_accessories("ACCESOORIES BROCHURE", f"{S7}/x")
    assert is_accessories("E-BROCHURE", f"{S7}/NEXA-Fronx-Accessories-Brochurepdf")
    assert not is_accessories("CNG BROCHURE", f"{S7}/NEXA-Grand-Vitara-CNG-Brochurepdf")


def test_model_hint_helpers() -> None:
    assert model_hint_from_slug("https://www.marutisuzuki.com/arena/alto-k10") == "Alto K10"
    assert model_hint_from_slug("https://www.nexaexperience.com/grand-vitara/") == "Grand Vitara"
    assert model_hint_from_slug(None) is None
    assert model_hint(BrochureCard(code="FR", logo_alt="FRONX")) == "FRONX"
    assert model_hint(BrochureCard(code="X", logo_alt="undefined", explore_url=f"{NEXA[:-11]}/xl6")) == "XL6"


def test_adapter_registers_with_official_scope() -> None:
    assert "maruti" in registry.available_keys()
    adapter = registry.get_adapter("maruti")
    assert isinstance(adapter, MarutiAdapter) and adapter.requires_browser
    adapter = MarutiAdapter()
    assert all(adapter.is_official(u) for u in (ARENA, NEXA, f"{S7}/NEXA-Jimny-Brochurepdf"))
    assert not adapter.is_official("https://other.scene7.com/is/content/maruti/x")
    assert not adapter.is_official("https://www.marutisuzukicommercial.com/dzire-tour-s")
    assert not adapter.is_official("https://marutisuzuki.example.net/x")
