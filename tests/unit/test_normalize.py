"""Extracted verbatim values -> typed spec inputs in canonical units."""

from __future__ import annotations

from decimal import Decimal

import pytest

from autohub.ingestion.extraction.prompt import prompt_version, system_prompt
from autohub.ingestion.extraction.schema import ExtractedSpec
from autohub.ingestion.normalize import Provenance, normalize_spec, normalize_specs
from autohub.ingestion.writer import compact
from autohub.specs.taxonomy import TAXONOMY

P = Provenance(brochure_version_id=7)


def item(
    key: str, value: str, unit: str | None = None, status: str = "present", page: int | None = 3
) -> ExtractedSpec:
    return ExtractedSpec(key=key, value=value, unit=unit, status=status, page=page)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("key", "value", "unit", "expected", "canonical"),
    [
        ("length", "4,395 mm", None, Decimal("4395"), "mm"),
        ("length", "4395", "mm", Decimal("4395"), "mm"),
        ("max_power", "110 kW (150 PS) @ 5000 rpm", None, Decimal("110"), "kW"),
        ("max_power", "150 PS", None, Decimal("110.3248"), "kW"),
        ("max_torque", "300 Nm @ 1750-2800 rpm", None, Decimal("300"), "Nm"),
        ("engine_displacement", "1.5 l", None, Decimal("1500"), "cc"),
        ("boot_capacity", "433", "litres", Decimal("433"), "l"),
        ("seating_capacity", "7 Seater", None, Decimal("7"), None),
        ("touchscreen_size", "26.03 cm", None, Decimal("10.2480"), "inch"),
        ("touchscreen_size", "10.25", None, Decimal("10.25"), "inch"),
    ],
)
def test_numbers_are_converted_to_the_canonical_unit(
    key: str, value: str, unit: str | None, expected: Decimal, canonical: str | None
) -> None:
    result = normalize_spec(item(key, value, unit), P)
    assert result is not None
    assert result.value_num == expected and result.unit == canonical
    assert result.original_value == value and result.status == "present"
    assert (
        result.source_type == "official_brochure"
        and result.brochure_version_id == 7
        and result.source_page == 3
    )


def test_rpm_band_is_read_from_the_qualifier() -> None:
    result = normalize_spec(item("max_torque_rpm", "300 Nm @ 1750-2800 rpm"), P)
    assert (
        result is not None and result.value_num == Decimal("1750") and result.value_num_max == Decimal("2800")
    )


def test_number_without_a_known_unit_keeps_text_and_no_number() -> None:
    result = normalize_spec(item("ground_clearance", "209"), P)
    assert result is not None
    assert result.value_num is None and result.value_text == "209" and "unit_missing" in result.flags
    assert result.confidence == Decimal("0.60")


def test_conflicting_figures_yield_no_number() -> None:
    result = normalize_spec(item("max_torque", "300 Nm (MT) / 320 Nm (AT)"), P)
    assert result is not None and result.value_num is None and "multi_value" in result.flags


@pytest.mark.parametrize(
    ("value", "status", "expected_bool", "expected_status"),
    [
        ("Yes", "present", True, "present"),
        ("Standard", "present", True, "present"),
        ("No", "not_available", False, "not_available"),
        ("—", "present", False, "not_available"),
        ("Optional", "optional", None, "optional"),
        ("Pack", "optional", None, "optional"),
        ("Not offered", "not_available", False, "not_available"),
    ],
)
def test_features(value: str, status: str, expected_bool: bool | None, expected_status: str) -> None:
    result = normalize_spec(item("sunroof", value, status=status), P)
    assert result is not None and result.value_bool is expected_bool and result.status == expected_status


def test_descriptive_feature_text_counts_as_present_but_flagged() -> None:
    result = normalize_spec(item("led_headlamps", "LED Projector"), P)
    assert result is not None and result.value_bool is True and result.flags == ("feature_from_text",)


def test_text_spec_and_unknown_or_empty_items() -> None:
    text = normalize_spec(item("front_suspension", " MacPherson strut "), P)
    assert text is not None and text.value_text == "MacPherson strut"
    assert normalize_spec(item("warp_drive", "Yes"), P) is None
    assert normalize_spec(item("length", "  "), P) is None


def test_variant_values_override_common_values() -> None:
    values = normalize_specs(
        [item("length", "4395 mm"), item("width", "1800 mm")], [item("length", "4400 mm")], P
    )
    by_key = {v.key: v.value_num for v in values}
    assert by_key == {"length": Decimal("4400"), "width": Decimal("1800")}


def test_compact_names_reconcile_spacing_and_punctuation() -> None:
    assert compact("XUV3XO") == compact("XUV 3XO") == "xuv3xo"
    assert compact("Scorpio-N") == compact("Scorpio N")
    assert compact("M4+") != compact("M4")


def test_prompt_lists_every_taxonomy_key_and_versions_by_content() -> None:
    prompt = system_prompt()
    for category in TAXONOMY:
        for spec in category.specs:
            assert f"- {spec.key}:" in prompt
    assert prompt_version().startswith("v2.1-") and len(prompt_version()) == len("v2.1-") + 8
