"""Turn extracted, verbatim values into typed spec values in canonical units.

The guiding rule of :mod:`autohub.specs.units` applies: no number is better
than a wrong number. A value whose unit cannot be established is kept as
``original_value`` with a flag and no ``value_num``; the guarded writer then
refuses to let it replace a good number already on file.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from autohub.ingestion.extraction.schema import ExtractedSpec
from autohub.specs.service import SpecValueInput
from autohub.specs.taxonomy import SpecDef, get_spec
from autohub.specs.units import parse_at_rpm, parse_feature, parse_quantity

CONFIDENCE_CLEAN = Decimal("0.90")
CONFIDENCE_FLAGGED = Decimal("0.60")
TEXT_LIMIT = 500
_RPM_KEYS = frozenset({"max_power_rpm", "max_torque_rpm"})
# Unitless counts where a bare number is the expected form.
_DEFAULT_UNITS: dict[str, str] = {
    "touchscreen_size": "inch",
    "instrument_cluster_size": "inch",
    "wheel_size": "inch",
}


@dataclass(frozen=True)
class Provenance:
    brochure_version_id: int
    source_type: str = "official_brochure"


def _text_with_unit(spec: ExtractedSpec) -> str:
    value = spec.value.strip()
    unit = (spec.unit or "").strip()
    if unit and unit.lower() not in value.lower():
        return f"{value} {unit}"
    return value


def normalize_spec(spec: ExtractedSpec, provenance: Provenance) -> SpecValueInput | None:
    """One extracted item to a writer input; ``None`` when the key is unknown or the item is empty."""
    definition: SpecDef | None = get_spec(spec.key)
    if definition is None or not spec.value.strip():
        return None
    original = spec.value.strip()[:TEXT_LIMIT]
    base = SpecValueInput(
        key=definition.key,
        source_type=provenance.source_type,
        original_value=original,
        original_unit=(spec.unit or None),
        brochure_version_id=provenance.brochure_version_id,
        source_page=spec.page if spec.page and spec.page > 0 else None,
        confidence=CONFIDENCE_CLEAN,
    )

    if definition.data_type == "boolean":
        value, status = parse_feature(original)
        if status is None and spec.status != "present":
            status = spec.status
            value = False if spec.status == "not_available" else None
        if status is None:
            # Descriptive text instead of Yes/No ("LED Projector"): the feature is there.
            if spec.status == "present":
                base.value_bool = True
                base.value_text = original
                base.flags = ("feature_from_text",)
                base.confidence = CONFIDENCE_FLAGGED
                return base
            return None
        base.status = status
        base.value_bool = value
        return base

    if definition.data_type == "text":
        if spec.status != "present":
            base.status = spec.status
            return base
        base.value_text = original
        return base

    # number
    if spec.status != "present":
        base.status = spec.status
        return base
    text = _text_with_unit(spec)
    if definition.key in _RPM_KEYS and "@" in text:
        parsed = parse_at_rpm(text)
    else:
        parsed = parse_quantity(text, definition.unit, default_unit=_DEFAULT_UNITS.get(definition.key))
    base.value_num = parsed.value
    base.value_num_max = parsed.value_max
    base.unit = parsed.unit if parsed.value is not None else None
    base.original_unit = parsed.original_unit or base.original_unit
    base.flags = parsed.flags
    if parsed.value is None:
        # Keep the wording for display; the writer never lets this replace a number.
        base.value_text = original
        base.confidence = CONFIDENCE_FLAGGED
    elif set(parsed.flags) - {"range"}:
        base.confidence = CONFIDENCE_FLAGGED
    return base


def normalize_specs(
    common: list[ExtractedSpec], own: list[ExtractedSpec], provenance: Provenance
) -> list[SpecValueInput]:
    """Common values overlaid by the variant's own values (same key: the variant wins)."""
    merged: dict[str, ExtractedSpec] = {s.key: s for s in common}
    merged.update({s.key: s for s in own})
    values = [normalize_spec(spec, provenance) for spec in merged.values()]
    return [v for v in values if v is not None]
