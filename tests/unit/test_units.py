from decimal import Decimal

import pytest

from autohub.specs.units import convert, parse_at_rpm, parse_feature, parse_quantity

D = Decimal


# --- strings taken verbatim from the V1 database (docs/V2/AUDIT.md §4.2) ---------------
@pytest.mark.parametrize(
    ("text", "unit", "expected"),
    [
        ("97 kW @ 3750 r/min", "kW", D("97")),
        ("149.2kW @5000 r/min", "kW", D("149.2")),
        ("73.5 kW @ 3750 rpm", "kW", D("73.5")),
        ("300 Nm @ 1600-2800 r/min", "Nm", D("300")),
        ("260 Nm @ 1750 - 2250 rpm", "Nm", D("260")),
        ("310 Nm", "Nm", D("310")),
    ],
)
def test_v1_power_and_torque_strings(text: str, unit: str, expected: Decimal) -> None:
    parsed = parse_quantity(text, unit)
    assert parsed.value == expected
    assert parsed.unit == unit
    assert "multi_value" not in parsed.flags


def test_equivalent_figures_in_two_units_resolve_to_first() -> None:
    # 150 PS is 110.3 kW: the same output stated twice, not two values.
    parsed = parse_quantity("110 kW | 150 PS", "kW")
    assert parsed.value == D("110")
    assert parsed.original_unit == "kW"


def test_conflicting_figures_yield_no_number() -> None:
    parsed = parse_quantity("300 Nm @ 1250-3000 (with MT) / 320 Nm @ 1500-3000 (with AT)", "Nm")
    assert parsed.value is None
    assert parsed.flags == ("multi_value",)


def test_rpm_qualifier_is_not_mistaken_for_the_quantity() -> None:
    assert parse_quantity("97 kW @ 3750 r/min", "kW").value == D("97")
    assert parse_at_rpm("97 kW @ 3750 r/min").value == D("3750")


def test_rpm_range() -> None:
    parsed = parse_at_rpm("300 Nm @ 1600-2800 r/min")
    assert (parsed.value, parsed.value_max) == (D("1600"), D("2800"))
    assert "range" in parsed.flags


def test_conflicting_rpm_qualifiers() -> None:
    assert parse_at_rpm("300 Nm @ 1250-3000 (MT) / 320 Nm @ 1500-3000 (AT)").flags == ("multi_value",)


# --- conversions -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "unit", "expected"),
    [
        ("150 PS", "kW", D("110.3248")),
        ("100 bhp", "kW", D("74.5700")),
        ("2.2 L", "cc", D("2200")),
        ("1,997 cc", "cc", D("1997")),
        ("1497cc", "cc", D("1497")),
        ("4.7 m", "mm", D("4700")),
        ("2 750 mm".replace(" ", ""), "mm", D("2750")),
        ('10.25"', "inch", D("10.25")),
        ("26.03 cm", "inch", D("10.2480")),
        ("5.3 m", "m", D("5.3")),
        ("57 litres", "l", D("57")),
        ("39.4 kWh", "kWh", D("39.4")),
        ("456 km", "km", D("456")),
    ],
)
def test_conversions(text: str, unit: str, expected: Decimal) -> None:
    assert parse_quantity(text, unit).value == expected


def test_bare_number_is_not_guessed() -> None:
    # The V1 root cause: "2.0" and "2184.0" stored in one column with no unit.
    parsed = parse_quantity("2184.0", "cc")
    assert parsed.value is None
    assert parsed.flags == ("unit_missing",)


def test_bare_number_with_caller_supplied_unit_is_flagged() -> None:
    parsed = parse_quantity("2184.0", "cc", default_unit="cc")
    assert parsed.value == D("2184")
    assert "unit_assumed" in parsed.flags


def test_unitless_spec_accepts_bare_number() -> None:
    assert parse_quantity("6 airbags", None).value == D("6")
    assert parse_quantity("7", None).value == D("7")


def test_range_is_kept_as_bounds() -> None:
    parsed = parse_quantity("15.5-17 kmpl", "km/l")
    assert (parsed.value, parsed.value_max) == (D("15.5"), D("17"))
    assert "range" in parsed.flags


def test_cng_mileage_is_flagged_not_silently_treated_as_km_per_litre() -> None:
    parsed = parse_quantity("26.6 km/kg", "km/l")
    assert parsed.value == D("26.6")
    assert "unit_km_per_kg" in parsed.flags


@pytest.mark.parametrize("text", [None, "", "   ", "N/A", "Not available"])
def test_empty_and_textual_values(text: str | None) -> None:
    assert parse_quantity(text, "mm").value is None


def test_unrecognised_unit_is_not_converted() -> None:
    assert parse_quantity("220 furlongs", "mm").value is None


def test_convert_separated_value_and_unit() -> None:
    assert convert("220", "mm", "mm").value == D("220")
    assert convert(1.5, "L", "cc").value == D("1500")
    assert convert("abc", "mm", "mm").flags == ("not_a_number",)
    assert convert("220", None, "mm").flags == ("unit_missing",)
    assert convert("220", "parsecs", "mm").flags == ("unit_unrecognised",)
    assert convert("6", None, None).value == D("6")


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("Yes", (True, "present")),
        ("✓", (True, "present")),
        ("Standard", (True, "present")),
        ("No", (False, "not_available")),
        ("-", (False, "not_available")),
        ("Optional", (None, "optional")),
        ("O", (None, "optional")),
        ("Leatherette", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_feature(cell: str | None, expected: tuple[bool | None, str | None]) -> None:
    assert parse_feature(cell) == expected
