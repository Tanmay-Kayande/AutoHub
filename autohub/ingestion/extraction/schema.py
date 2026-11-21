"""Structured output the extraction model must return for one brochure.

Values are kept **verbatim** (``"1999 cc"``, ``"110 kW @ 5000 rpm"``,
``"Standard"``); unit conversion happens afterwards in deterministic code
(:mod:`autohub.ingestion.normalize`), never in the model.

Every field is required (nullable where a value may be absent) and no field has
a default: the provider's JSON-schema subset handles that shape reliably, and a
missing field is then a validation error rather than a silent default.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

ItemStatus = Literal["present", "not_available", "optional"]


class ExtractedSpec(BaseModel):
    key: str = Field(description="A key from the allowed specification list. Never invent keys.")
    value: str = Field(
        description="Value exactly as printed, including its unit, e.g. '1999 cc' or 'Standard'."
    )
    unit: str | None = Field(description="The unit as printed when it is separate from the value, else null.")
    status: ItemStatus = Field(
        description="present: the variant has it; not_available: brochure marks it absent; optional: an option/pack."
    )
    page: int | None = Field(description="1-based PDF page the value was read from, if known.")


class ExtractedVariant(BaseModel):
    name: str = Field(description="Variant/trim name as printed, e.g. 'AX7 L Diesel AT'.")
    fuel_type: str | None = Field(description="Fuel as printed (petrol, diesel, CNG, electric, ...).")
    transmission: str | None = Field(description="Transmission as printed (6MT, 7DCT, AMT, ...).")
    drivetrain: str | None = Field(description="Drivetrain as printed (4WD, AWD, 2WD, ...), else null.")
    specs: list[ExtractedSpec] = Field(description="Values that apply to this variant only.")


class BrochureExtraction(BaseModel):
    model_name: str | None = Field(description="Vehicle model name as printed on the brochure.")
    body_type: str | None = Field(description="suv, hatchback, sedan, mpv, pickup, ... if stated.")
    contains_specifications: bool = Field(
        description="false when the document has no specification or feature table."
    )
    common_specs: list[ExtractedSpec] = Field(
        description="Values identical for every variant (typically dimensions). Listed once here."
    )
    variants: list[ExtractedVariant]


class VariantOutline(BaseModel):
    name: str = Field(description="Full configuration name, as for a variant entry.")
    fuel_type: str | None = Field(description="Fuel as printed (petrol, diesel, CNG, electric, ...).")
    transmission: str | None = Field(description="Transmission as printed (6MT, 7DCT, AMT, ...).")
    drivetrain: str | None = Field(description="Drivetrain as printed (4WD, AWD, 2WD, ...), else null.")


class VariantIndex(BaseModel):
    """First pass for a brochure too large to extract in one response: the variants only."""

    model_name: str | None = Field(description="Vehicle model name as printed on the brochure.")
    body_type: str | None = Field(description="suv, hatchback, sedan, mpv, pickup, ... if stated.")
    contains_specifications: bool = Field(
        description="false when the document has no specification or feature table."
    )
    variants: list[VariantOutline]


class SpecGroup(BaseModel):
    label: str = Field(
        description="Powertrain label (e.g. '1.2 Petrol 5MT') or trim name as printed (e.g. 'HX 5')."
    )
    specs: list[ExtractedSpec] = Field(
        description="Values that apply to every configuration with this label."
    )


class GroupedVariant(BaseModel):
    name: str = Field(description="Full configuration name, e.g. 'HX 5 1.2 Petrol 5MT'.")
    fuel_type: str | None = Field(description="Fuel as printed (petrol, diesel, CNG, electric, ...).")
    transmission: str | None = Field(description="Transmission as printed (6MT, 7DCT, AMT, ...).")
    drivetrain: str | None = Field(description="Drivetrain as printed (4WD, AWD, 2WD, ...), else null.")
    trim: str | None = Field(description="Label of this configuration's entry in trims, else null.")
    powertrain: str | None = Field(
        description="Label of this configuration's entry in powertrains, else null."
    )
    specs: list[ExtractedSpec] = Field(
        description="Only values specific to this exact configuration that its trim and powertrain do not give."
    )


class GroupedExtraction(BaseModel):
    """One brochure with each value stated once: per powertrain, per trim, or per configuration.

    Brochures print engine data per powertrain and features per trim, so this is several
    times shorter than repeating both under every configuration (output tokens are the
    cost). :meth:`expand` rebuilds the per-configuration :class:`BrochureExtraction`.
    """

    model_name: str | None = Field(description="Vehicle model name as printed on the brochure.")
    body_type: str | None = Field(description="suv, hatchback, sedan, mpv, pickup, ... if stated.")
    contains_specifications: bool = Field(
        description="false when the document has no specification or feature table."
    )
    common_specs: list[ExtractedSpec] = Field(
        description="Values identical for every variant (typically dimensions). Listed once here."
    )
    powertrains: list[SpecGroup] = Field(
        description="One entry per engine/powertrain with the values that depend on it (engine, power, economy)."
    )
    trims: list[SpecGroup] = Field(description="One entry per trim with the values that depend on the trim.")
    variants: list[GroupedVariant]

    def expand(self) -> BrochureExtraction:
        """Each configuration gets its own values, then its trim's, then its powertrain's;
        when a key repeats, the first (most specific) one is kept by the cleaner."""
        powertrains = {_label(g.label): g.specs for g in self.powertrains}
        trims = {_label(g.label): g.specs for g in self.trims}
        variants = [
            ExtractedVariant(
                name=v.name,
                fuel_type=v.fuel_type,
                transmission=v.transmission,
                drivetrain=v.drivetrain,
                specs=[
                    *v.specs,
                    *trims.get(_label(v.trim), []),
                    *powertrains.get(_label(v.powertrain), []),
                ],
            )
            for v in self.variants
        ]
        return BrochureExtraction(
            model_name=self.model_name,
            body_type=self.body_type,
            contains_specifications=self.contains_specifications,
            common_specs=self.common_specs,
            variants=variants,
        )


def _label(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


def extraction_from_raw(raw: Mapping[str, Any]) -> BrochureExtraction:
    """A stored run's output in either format, as per-configuration values."""
    if "powertrains" in raw or "trims" in raw:
        return GroupedExtraction.model_validate(raw).expand()
    return BrochureExtraction.model_validate(raw)
