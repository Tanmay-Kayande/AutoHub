"""Extraction prompt, generated from the taxonomy.

Changing the wording or the taxonomy changes the extraction contract, so bump
``PROMPT_VERSION``: the pipeline re-extracts every brochure version whose last
successful run used a different prompt version or model.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache

from autohub.specs.taxonomy import TAXONOMY, SpecDef

PROMPT_BASE_VERSION = "v2.4"
# Earlier versions whose runs stay current: v2.4 asks for the same values under the same
# rules, only grouped by powertrain and trim (fewer output tokens). Their runs are neither
# re-extracted nor skipped by the writer.
EQUIVALENT_PROMPT_VERSIONS = ("v2.3-d97f8d97",)


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
6. Finding the configurations: brochures usually print technical specifications in one column per engine or powertrain, and features in one column per trim. Engine columns are NOT the variant list. Take the trims from the feature table, the variant line-up or the availability matrix, then list every trim × powertrain combination the brochure says is offered. Every value goes under the configurations it applies to (see rule 9 for how).
7. List every configuration, even when the list is long and entries repeat values. Never reduce it to examples, one per engine, the top trim only, or "and others".
8. Editions: when a trim carries an edition badge or label (e.g. "#DARK", "RED #DARK", "Knight", "CAMO"), the edition is offered in addition to the standard trim. List the edition configurations too, with the edition in the name (e.g. "Creative + S #DARK Petrol 6AMT"), for the powertrains the brochure gives that edition, or the trim's powertrains if it gives none.
9. Where each value goes. A value identical for every variant goes once in common_specs. When the answer has powertrains and trims lists: put each value that depends on the engine or powertrain once under that powertrain (label it e.g. "1.2 Petrol 5MT"); put each value that depends on the trim once under that trim (label: the trim name as printed; an edition with its own feature values, e.g. "HX 5 Knight", is its own trim entry with all its values); give every variant its trim and powertrain label; and list under the variant only values specific to that exact configuration (e.g. a feature only the automatic has). When the answer has no powertrains or trims lists (a brochure done in parts), put every value under each variant it applies to.
10. page is the 1-based PDF page number where you read the value.
11. If the document has no specification or feature table, return contains_specifications=false and empty lists.
"""


@lru_cache(maxsize=1)
def system_prompt() -> str:
    return f"{SYSTEM_RULES}\nAllowed specification keys:\n{spec_catalogue()}\n"


def user_prompt(*, brand: str, model_hint: str | None) -> str:
    hint = f' The brochure is expected to cover the model "{model_hint}".' if model_hint else ""
    return f"Extract the specifications from this {brand} brochure.{hint} Return JSON only."


def current_prompt_versions() -> tuple[str, ...]:
    """The current version and the earlier ones whose runs are still current."""
    return (prompt_version(), *EQUIVALENT_PROMPT_VERSIONS)


@lru_cache(maxsize=1)
def prompt_version() -> str:
    """Base version plus a digest of the generated text, so taxonomy edits re-trigger extraction."""
    digest = hashlib.sha256(system_prompt().encode("utf-8")).hexdigest()[:8]
    return f"{PROMPT_BASE_VERSION}-{digest}"
