"""Extraction prompt, generated from the taxonomy.

Changing the wording or the taxonomy changes the extraction contract, so bump
``PROMPT_VERSION``: the pipeline re-extracts every brochure version whose last
successful run used a different prompt version or model.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache

from autohub.specs.taxonomy import TAXONOMY, SpecDef

PROMPT_BASE_VERSION = "v2.2"


def _describe(spec: SpecDef) -> str:
    if spec.data_type == "boolean":
        kind = "feature: value Yes/No/Optional"
    elif spec.data_type == "number":
        kind = f"number in {spec.unit}" if spec.unit else "number"
    else:
        kind = "text"
    return f"- {spec.key}: {spec.name} ({kind})"


@lru_cache(maxsize=1)
def spec_catalogue() -> str:
    lines: list[str] = []
    for category in TAXONOMY:
        lines.append(f"{category.name}:")
        lines.extend(_describe(spec) for spec in category.specs)
    return "\n".join(lines)


SYSTEM_RULES = """You extract vehicle specifications from an official manufacturer brochure (PDF) for a car catalogue.

Rules:
1. Use only what the brochure states. Never use outside knowledge, never estimate, never fill gaps.
2. Use only the keys in the allowed list, and only for exactly that specification. If the brochure item is a different (even related) feature, skip it. Example: "Rear wiper & washer" is NOT rain_sensing_wipers.
3. Copy numeric and text values verbatim with their unit as printed (e.g. "1197 cc", "61 kW (83 PS) @ 6000 r/min"). Do not convert units.
4. Feature keys: value must be exactly "Yes", "No" or "Optional", with status present / not_available / optional.
5. variants: one entry per purchasable configuration. When a trim is offered with several powertrains (fuel, transmission, drivetrain) or seating layouts, output one entry for each, and make name the full configuration name including those parts, e.g. "AX7 TECH 6 STR Diesel AWD AT". No two entries may have the same name. Always fill fuel_type and transmission.
6. A value identical for every variant goes once in common_specs. Values that differ by variant go under each variant they apply to.
7. page is the 1-based PDF page number where you read the value.
8. If the document has no specification or feature table, return contains_specifications=false and empty lists.
"""


@lru_cache(maxsize=1)
def system_prompt() -> str:
    return f"{SYSTEM_RULES}\nAllowed specification keys:\n{spec_catalogue()}\n"


def user_prompt(*, brand: str, model_hint: str | None) -> str:
    hint = f' The brochure is expected to cover the model "{model_hint}".' if model_hint else ""
    return f"Extract the specifications from this {brand} brochure.{hint} Return JSON only."


@lru_cache(maxsize=1)
def prompt_version() -> str:
    """Base version plus a digest of the generated text, so taxonomy edits re-trigger extraction."""
    digest = hashlib.sha256(system_prompt().encode("utf-8")).hexdigest()[:8]
    return f"{PROMPT_BASE_VERSION}-{digest}"
