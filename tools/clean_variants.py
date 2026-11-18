"""Repair variant data that earlier code left incomplete. No LLM call is made.

1. **Codes:** fill a missing ``fuel_type`` / ``transmission_type`` / ``drivetrain``
   from the stored raw text and the variant name with the current normalisers
   (for example "7DCT" and Hyundai's "iVT" were not recognised before). Codes
   that are already set are never changed.
2. **Superseded legacy variants:** a variant whose specs all come from the V1
   migration (``legacy_v1``) is deactivated when its generation already has
   variants extracted from an official brochure. V1 names rarely match the
   brochure's (V1 "LXT Diesel (D117 CRDe)" next to the brochure's "LXT Diesel
   AT"), so these rows show up as duplicates. Deactivated, not deleted: rows,
   specs and history stay; the catalogue, search and assistant skip inactive
   variants, and the next ``tools.reindex`` drops their search documents.

    python -m tools.clean_variants            # dry run: prints what would change
    python -m tools.clean_variants --commit
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session, aliased

from autohub.catalog.vocab import normalize_drivetrain, normalize_fuel_type, normalize_transmission_type
from autohub.db.models import ModelGeneration, Variant, VariantSpec, VehicleModel
from autohub.db.session import get_session_factory

LEGACY = "legacy_v1"
OFFICIAL = "official_brochure"


@dataclass
class CleanResult:
    reclassified: list[str] = field(default_factory=list)  # "Model / Variant: field=value"
    retired: list[str] = field(default_factory=list)  # "Model / Variant"


def reclassify(db: Session, result: CleanResult) -> None:
    rows = db.execute(
        select(Variant, VehicleModel.name)
        .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
        .join(VehicleModel, VehicleModel.id == ModelGeneration.model_id)
        .where(
            Variant.is_active.is_(True),
            (Variant.fuel_type.is_(None))
            | (Variant.transmission_type.is_(None))
            | (Variant.drivetrain.is_(None)),
        )
        .order_by(Variant.id)
    ).all()
    for variant, model_name in rows:
        changes: list[str] = []
        if variant.fuel_type is None:
            code = normalize_fuel_type(variant.fuel_type_raw) or normalize_fuel_type(variant.name)
            if code:
                variant.fuel_type = code
                changes.append(f"fuel_type={code}")
        if variant.transmission_type is None:
            code = normalize_transmission_type(variant.transmission_raw) or normalize_transmission_type(
                variant.name
            )
            if code is None and variant.fuel_type == "electric" and not variant.transmission_raw:
                code = "single_speed"  # same rule as the extraction writer
            if code:
                variant.transmission_type = code
                changes.append(f"transmission_type={code}")
        if variant.drivetrain is None:
            code = normalize_drivetrain(variant.transmission_raw, variant.name)
            if code:
                variant.drivetrain = code
                changes.append(f"drivetrain={code}")
        if changes:
            result.reclassified.append(f"{model_name} / {variant.name}: {', '.join(changes)}")
    db.flush()


def retire_superseded_legacy(db: Session, result: CleanResult) -> None:
    sibling = aliased(Variant)
    has_legacy = exists().where(VariantSpec.variant_id == Variant.id, VariantSpec.source_type == LEGACY)
    has_other = exists().where(VariantSpec.variant_id == Variant.id, VariantSpec.source_type != LEGACY)
    generation_has_official = exists().where(
        and_(
            sibling.generation_id == Variant.generation_id,
            sibling.id != Variant.id,
            sibling.is_active.is_(True),
            exists().where(VariantSpec.variant_id == sibling.id, VariantSpec.source_type == OFFICIAL),
        )
    )
    rows = db.execute(
        select(Variant, VehicleModel.name)
        .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
        .join(VehicleModel, VehicleModel.id == ModelGeneration.model_id)
        .where(Variant.is_active.is_(True), has_legacy, ~has_other, generation_has_official)
        .order_by(VehicleModel.name, Variant.id)
    ).all()
    for variant, model_name in rows:
        variant.is_active = False
        result.retired.append(f"{model_name} / {variant.name}")
    db.flush()


def clean(db: Session, *, commit: bool) -> CleanResult:
    """Apply both repairs in one transaction; roll back unless ``commit``."""
    result = CleanResult()
    reclassify(db, result)
    retire_superseded_legacy(db, result)
    if commit:
        db.commit()
    else:
        db.rollback()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--commit", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args(argv)
    with get_session_factory()() as db:
        result = clean(db, commit=args.commit)
    verb = "" if args.commit else "would be "
    print(f"Codes {verb}filled on {len(result.reclassified)} variants:")
    for line in result.reclassified:
        print(f"  {line}")
    print(f"Legacy variants {verb}deactivated: {len(result.retired)}")
    for line in result.retired:
        print(f"  {line}")
    if args.commit and result.retired:
        print("Run `python -m tools.reindex` to drop their search documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
