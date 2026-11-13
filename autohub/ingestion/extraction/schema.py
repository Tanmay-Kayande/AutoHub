"""Structured output the extraction model must return for one brochure.

Values are kept **verbatim** (``"1999 cc"``, ``"110 kW @ 5000 rpm"``,
``"Standard"``); unit conversion happens afterwards in deterministic code
(:mod:`autohub.ingestion.normalize`), never in the model.

Every field is required (nullable where a value may be absent) and no field has
a default: the provider's JSON-schema subset handles that shape reliably, and a
missing field is then a validation error rather than a silent default.
"""

from __future__ import annotations

from typing import Literal

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
