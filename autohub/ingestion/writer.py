"""Write one successful extraction into the catalogue.

Resolves the brochure to a model (matching existing names loosely, creating the
model only when nothing matches), then each extracted variant to a variant of
the model's current generation, then hands the normalised values to
:func:`autohub.specs.service.apply_spec_values`, which owns the guarded write
rule and history.

Nothing here deletes: a variant missing from a newer brochure stays as it is,
and an empty value never blanks a stored one. The caller owns the transaction
(one brochure = one transaction).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.catalog.slugs import slugify, unique_slug
from autohub.catalog.vocab import (
    normalize_body_type,
    normalize_drivetrain,
    normalize_fuel_type,
    normalize_transmission_type,
)
from autohub.db.models import (
    Brochure,
    BrochureVersion,
    ExtractionRun,
    ModelGeneration,
    Variant,
    VehicleModel,
)
from autohub.ingestion.extraction.schema import BrochureExtraction, ExtractedVariant
from autohub.ingestion.normalize import Provenance, normalize_specs
from autohub.specs.service import ApplyResult, SpecValueInput, apply_spec_values
from autohub.specs.taxonomy import get_spec

logger = logging.getLogger(__name__)

MODEL_MATCH_RATIO = 0.9  # "XUV 3XO EV" vs "XUV 3XO" is 0.86 and must not match
_NON_ALNUM = re.compile(r"[^a-z0-9+]+")


def compact(name: str) -> str:
    """``"XUV 3XO"`` and ``"XUV3XO"`` -> ``"xuv3xo"``; ``"Scorpio-N"`` -> ``"scorpion"``."""
    return _NON_ALNUM.sub("", name.lower())


def match_model(candidates: list[str], models: list[VehicleModel]) -> VehicleModel | None:
    """Exact compact-name match first, then the closest name above ``MODEL_MATCH_RATIO``."""
    keys = [compact(c) for c in candidates if c and compact(c)]
    by_key = {compact(m.name): m for m in models}
    for key in keys:
        if key in by_key:
            return by_key[key]
    best: tuple[float, VehicleModel | None] = (0.0, None)
    for key in keys:
        for model_key, model in by_key.items():
            ratio = SequenceMatcher(None, key, model_key).ratio()
            if ratio > best[0]:
                best = (ratio, model)
    return best[1] if best[0] >= MODEL_MATCH_RATIO else None


@dataclass
class WriteSummary:
    model_id: int
    model_created: bool = False
    variants_created: int = 0
    variants_matched: int = 0
    not_applicable_skipped: int = 0
    result: ApplyResult = field(default_factory=ApplyResult)


def _resolve_model(
    db: Session, brochure: Brochure, extraction: BrochureExtraction
) -> tuple[VehicleModel, bool]:
    if brochure.model_id is not None:
        model = db.get(VehicleModel, brochure.model_id)
        if model is not None:
            return model, False
    models = list(
        db.execute(select(VehicleModel).where(VehicleModel.brand_id == brochure.brand_id)).scalars()
    )
    names = [n for n in (brochure.model_hint, extraction.model_name) if n]
    found = match_model(names, models)
    if found is not None:
        brochure.model_id = found.id
        return found, False
    if not names:
        raise ValueError(f"brochure {brochure.id} has no model hint and the extraction names no model")
    name = names[0].strip()
    model = VehicleModel(
        brand_id=brochure.brand_id,
        name=name,
        slug=unique_slug(name, {m.slug for m in models}),
        body_type=normalize_body_type(extraction.body_type),
    )
    db.add(model)
    db.flush()
    brochure.model_id = model.id
    logger.info("model_created brand_id=%s model=%s brochure_id=%s", brochure.brand_id, name, brochure.id)
    return model, True


def _current_generation(db: Session, model: VehicleModel) -> ModelGeneration:
    generation = db.execute(
        select(ModelGeneration).where(
            ModelGeneration.model_id == model.id, ModelGeneration.is_current.is_(True)
        )
    ).scalar_one_or_none()
    if generation is None:
        generation = ModelGeneration(model_id=model.id, name="Current", is_current=True)
        db.add(generation)
        db.flush()
    return generation


def _resolve_variant(
    db: Session, generation: ModelGeneration, existing: list[Variant], extracted: ExtractedVariant
) -> tuple[Variant, bool]:
    key = compact(extracted.name)
    for variant in existing:
        if variant.slug == slugify(extracted.name) or compact(variant.name) == key:
            _fill_codes(variant, extracted)
            return variant, False
    variant = Variant(
        generation_id=generation.id,
        name=extracted.name,
        slug=unique_slug(extracted.name, {v.slug for v in existing}),
    )
    _fill_codes(variant, extracted)
    db.add(variant)
    db.flush()
    existing.append(variant)
    return variant, True


def _fill_codes(variant: Variant, extracted: ExtractedVariant) -> None:
    """Set fuel/transmission/drivetrain only where still unknown; never overwrite."""
    if variant.fuel_type is None:
        variant.fuel_type = normalize_fuel_type(extracted.fuel_type) or normalize_fuel_type(extracted.name)
        variant.fuel_type_raw = variant.fuel_type_raw or extracted.fuel_type
    if variant.transmission_type is None:
        variant.transmission_type = normalize_transmission_type(
            extracted.transmission
        ) or normalize_transmission_type(extracted.name)
        variant.transmission_raw = variant.transmission_raw or extracted.transmission
    if variant.drivetrain is None:
        variant.drivetrain = normalize_drivetrain(
            extracted.drivetrain, extracted.transmission, extracted.name
        )


def _applicable(values: list[SpecValueInput], fuel_type: str | None) -> tuple[list[SpecValueInput], int]:
    """Drop values for specs that cannot apply to this fuel (e.g. fuel tank on an EV)."""
    if fuel_type is None:
        return values, 0
    kept = []
    for value in values:
        spec = get_spec(value.key)
        if spec is not None and spec.applies_to_fuel is not None and fuel_type not in spec.applies_to_fuel:
            continue
        kept.append(value)
    return kept, len(values) - len(kept)


def write_extraction(
    db: Session,
    *,
    version: BrochureVersion,
    run: ExtractionRun,
    extraction: BrochureExtraction,
) -> WriteSummary:
    brochure = db.get(Brochure, version.brochure_id)
    assert brochure is not None
    model, created = _resolve_model(db, brochure, extraction)
    if model.body_type is None and extraction.body_type:
        model.body_type = normalize_body_type(extraction.body_type)
    generation = _current_generation(db, model)
    existing = list(db.execute(select(Variant).where(Variant.generation_id == generation.id)).scalars())
    summary = WriteSummary(model_id=model.id, model_created=created)
    provenance = Provenance(brochure_version_id=version.id)

    variants = extraction.variants
    if not variants:
        # Only common values: they describe every variant already on file.
        targets = [(v, False) for v in existing]
        common_only = True
    else:
        targets = [_resolve_variant(db, generation, existing, ev) for ev in variants]
        common_only = False

    for index, (variant, was_created) in enumerate(targets):
        if was_created:
            summary.variants_created += 1
        else:
            summary.variants_matched += 1
        own = [] if common_only else variants[index].specs
        values = normalize_specs(extraction.common_specs, own, provenance)
        values, skipped = _applicable(values, variant.fuel_type)
        summary.not_applicable_skipped += skipped
        summary.result.merge(
            apply_spec_values(db, variant.id, values, run_id=run.id, reason=f"brochure version {version.id}")
        )

    run.specs_written = summary.result.written
    run.specs_unchanged = summary.result.unchanged
    run.conflicts_skipped = summary.result.conflicts_skipped
    run.finished_at = datetime.now(UTC)
    db.flush()
    logger.info(
        "extraction_written run=%s model_id=%s variants_created=%s matched=%s written=%s unchanged=%s conflicts=%s",
        run.id,
        model.id,
        summary.variants_created,
        summary.variants_matched,
        summary.result.written,
        summary.result.unchanged,
        summary.result.conflicts_skipped,
    )
    return summary
