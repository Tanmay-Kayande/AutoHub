"""Published model prices: the amount parser and what each brand adapter reads."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from autohub.ingestion.adapters.hyundai import HyundaiAdapter
from autohub.ingestion.adapters.mahindra import model_prices
from autohub.ingestion.prices import parse_price_range

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("₹ 10.99 - 18.41", (1_099_000, 1_841_000)),  # Hyundai card, lakh
        ("₹7.79L - ₹15.04L", (779_000, 1_504_000)),  # Mahindra menu
        ("₹13.69L – ₹25.49L", (1_369_000, 2_549_000)),  # en dash
        ("₹ 6.40", (640_000, None)),  # single figure: a starting price
        ("₹17.49L", (1_749_000, None)),
        ("₹7.39 L*", (739_000, None)),  # Tata title
        ("₹ 5.74 Lakh*", (574_000, None)),
        ("₹ 55 70 600", (5_570_600, None)),  # rupees, Indian grouping
        ("₹ 55,70,600", (5_570_600, None)),
        ("Rs. 12.5 lakh to 14 lakh", (1_250_000, 1_400_000)),
    ],
)
def test_parse_price_range(text: str, expected: tuple[int, int | None]) -> None:
    parsed = parse_price_range(text)
    assert parsed is not None
    assert (int(parsed[0]), None if parsed[1] is None else int(parsed[1])) == expected
    assert isinstance(parsed[0], Decimal)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Price on request",
        "₹ 18.41 - 10.99",  # max below min
        "₹ 0.20",  # 20,000 rupees: not a car
        "₹ 5000",  # 50 crore lakh: not a car
        "₹ 5 - 10 - 15",
        "₹ 10.999",
    ],
)
def test_parse_price_range_rejects(text: str) -> None:
    assert parse_price_range(text) is None


def test_hyundai_reads_the_price_attribute_of_each_card() -> None:
    html = (FIXTURES / "hyundai" / "find_a_car.html").read_text(encoding="utf-8")
    prices = {p.model_hint: p for p in HyundaiAdapter().prices(html)}
    assert {name: (int(p.price_min), p.price_max and int(p.price_max)) for name, p in prices.items()} == {
        "Verna": (1_099_000, 1_841_000),
        "IONIQ 5": (5_570_600, None),
        "Prime-HB": (640_000, None),
    }
    assert all(p.brand == "Hyundai" and p.source_url == HyundaiAdapter.listing_url for p in prices.values())


def test_hyundai_cards_without_a_readable_price_are_skipped() -> None:
    html = (
        '<div class="car-card col-md-4" data-price="On request"><h3>Alpha</h3></div>'
        '<div class="car-card col-md-4"><h3>Beta</h3></div>'
        '<div class="car-card col-md-4" data-price="&#8377; 7.99 - 15.82"><div><h3> <b>Gamma</b> </h3></div></div>'
        '<div class="car-card col-md-4" data-price="₹ 9.99 - 12.00"><h3>Gamma</h3></div>'
    )
    prices = HyundaiAdapter().prices(html)
    # The first card of a name wins, entities and inner tags are handled.
    assert [(p.model_hint, int(p.price_min), int(p.price_max or 0)) for p in prices] == [
        ("Gamma", 799_000, 1_582_000)
    ]


def test_mahindra_reads_the_model_menu() -> None:
    html = (FIXTURES / "mahindra" / "suv_listing.html").read_text(encoding="utf-8")
    page = "https://auto.mahindra.com/suv/"
    prices = {p.model_hint: p for p in model_prices(html, page)}
    assert {name: (int(p.price_min), p.price_max and int(p.price_max)) for name, p in prices.items()} == {
        "XUV 3XO": (779_000, 1_504_000),
        "Thar": (1_032_000, 1_899_000),  # menu label "THAR OG"
        "SCORPIO-N": (1_369_000, 2_549_000),
        "XUV400": (1_749_000, None),
    }
    assert all(p.brand == "Mahindra" and p.source_url == page for p in prices.values())
    assert model_prices("<html><body>no menu</body></html>", page) == []
