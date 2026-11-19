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
3. **Edition names:** a variant whose brochure specs all come from one edition
   brochure ("Sierra #DARK Brochure") gets the edition appended to its name
   ("Pure L 1.5L Revotron MT #DARK") and slug, as the extraction writer now does.
4. **Duplicates worded differently:** two active variants of a generation that
   describe the same car ("Pure+ 1.2L Revotron 6MT" and "Pure + CNG 6MT", see
   ``ingestion.writer.same_car``) are merged one-to-one only: the variant with more
   specs is kept, the other one's specs that the kept variant lacks move to it
   (their history follows), and the other one is deactivated.

    python -m tools.clean_variants            # dry run: prints what would change
    python -m tools.clean_variants --commit
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.orm import Session, aliased

from autohub.catalog.slugs import unique_slug
from autohub.catalog.vocab import normalize_drivetrain, normalize_fuel_type, normalize_transmission_type
from autohub.db.models import (
    Brochure,
    BrochureVersion,
    ModelGeneration,
    Variant,
    VariantSpec,
    VehicleImage,
    VehicleModel,
)
from autohub.db.session import get_session_factory
from autohub.ingestion.writer import edition_label, same_car, with_edition

LEGACY = "legacy_v1"
OFFICIAL = "official_brochure"


@dataclass
class CleanResult:
    reclassified: list[str] = field(default_factory=list)  # "Model / Variant: field=value"
    retired: list[str] = field(default_factory=list)  # "Model / Variant"
    renamed: list[str] = field(default_factory=list)  # "Model / Old -> New"
    merged: list[str] = field(default_factory=list)  # "Model / Duplicate -> Kept (n specs moved)"


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


def name_editions(db: Session, result: CleanResult) -> None:
    rows = db.execute(
        select(Variant, VehicleModel.name, Brochure.title, Brochure.model_hint)
        .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
        .join(VehicleModel, VehicleModel.id == ModelGeneration.model_id)
        .join(VariantSpec, VariantSpec.variant_id == Variant.id)
        .join(BrochureVersion, BrochureVersion.id == VariantSpec.brochure_version_id)
        .join(Brochure, Brochure.id == BrochureVersion.brochure_id)
        .where(Variant.is_active.is_(True), VariantSpec.source_type == OFFICIAL)
        .distinct()
    ).all()
    sources: dict[int, set[tuple[str | None, str | None]]] = defaultdict(set)
    found: dict[int, tuple[Variant, str]] = {}
    for variant, model_name, title, hint in rows:
        sources[variant.id].add((title, hint))
        found[variant.id] = (variant, model_name)
    for variant_id in sorted(found):
        if len(sources[variant_id]) != 1:
            continue  # specs from several brochures: not an edition-only variant
        variant, model_name = found[variant_id]
        ((title, hint),) = sources[variant_id]
        new_name = with_edition(variant.name, edition_label(title, [model_name, *([hint] if hint else [])]))
        if new_name == variant.name:
            continue
        taken = set(
            db.execute(
                select(Variant.slug).where(
                    Variant.generation_id == variant.generation_id, Variant.id != variant.id
                )
            ).scalars()
        )
        result.renamed.append(f"{model_name} / {variant.name} -> {new_name}")
        variant.name = new_name
        variant.slug = unique_slug(new_name, taken)
        db.flush()


Entry = tuple[Variant, str, int]  # variant, model name, spec count


def _same(a: Variant, b: Variant) -> bool:
    return same_car(a.name, (a.fuel_type, a.transmission_type), b.name, (b.fuel_type, b.transmission_type))


def merge_duplicates(db: Session, result: CleanResult) -> None:
    spec_count = (
        select(func.count()).where(VariantSpec.variant_id == Variant.id).correlate(Variant).scalar_subquery()
    )
    rows = db.execute(
        select(Variant, VehicleModel.name, spec_count)
        .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
        .join(VehicleModel, VehicleModel.id == ModelGeneration.model_id)
        .where(Variant.is_active.is_(True))
        .order_by(VehicleModel.name, Variant.id)
    ).all()
    groups: dict[int, list[Entry]] = defaultdict(list)
    for variant, model_name, count in rows:
        groups[variant.generation_id].append((variant, model_name, int(count)))

    for group in groups.values():
        partners = {e[0].id: [o for o in group if o[0].id != e[0].id and _same(e[0], o[0])] for e in group}
        done: set[int] = set()
        for entry in group:
            mates = partners[entry[0].id]
            # One-to-one pairs only: anything ambiguous is left as it is.
            if entry[0].id in done or len(mates) != 1 or len(partners[mates[0][0].id]) != 1:
                continue
            keep, drop = sorted([entry, mates[0]], key=lambda e: (-e[2], e[0].id))
            kept_definitions = select(VariantSpec.spec_definition_id).where(
                VariantSpec.variant_id == keep[0].id
            )
            outcome = db.execute(
                update(VariantSpec)
                .where(
                    VariantSpec.variant_id == drop[0].id,
                    VariantSpec.spec_definition_id.not_in(kept_definitions),
                )
                .values(variant_id=keep[0].id)
                .execution_options(synchronize_session=False)
            )
            moved = int(getattr(outcome, "rowcount", 0) or 0)
            db.execute(
                update(VehicleImage)
                .where(VehicleImage.variant_id == drop[0].id)
                .values(variant_id=keep[0].id)
                .execution_options(synchronize_session=False)
            )
            drop[0].is_active = False
            done |= {keep[0].id, drop[0].id}
            result.merged.append(f"{keep[1]} / {drop[0].name} -> {keep[0].name} ({moved} specs moved)")
    db.flush()


def clean(db: Session, *, commit: bool) -> CleanResult:
    """Apply all repairs in one transaction; roll back unless ``commit``."""
    result = CleanResult()
    reclassify(db, result)
    retire_superseded_legacy(db, result)
    name_editions(db, result)
    merge_duplicates(db, result)
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
    print(f"Edition variants {verb}renamed: {len(result.renamed)}")
    for line in result.renamed:
        print(f"  {line}")
    print(f"Duplicate variants {verb}merged: {len(result.merged)}")
    for line in result.merged:
        print(f"  {line}")
    if args.commit and (result.retired or result.renamed or result.merged):
        print("Run `python -m tools.reindex` to bring the search documents up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
