"""Specification reads and the guarded write rule.

``apply_spec_values`` is the only code path that writes ``variant_specs``.
It enforces "never silently overwrite good data" (docs/V2/DATABASE_DESIGN.md §3):
a value is replaced only by a non-empty value from an equal-or-better source,
and the replaced value is copied to ``variant_spec_history`` first.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from autohub.db.models import (
    Brochure,
    BrochureVersion,
    SpecCategory,
    SpecDefinition,
    Variant,
    VariantSpec,
    VariantSpecHistory,
)

SOURCE_RANK: dict[str, int] = {
    "manual": 4,
    "official_brochure": 3,
    "official_website": 3,
    "third_party": 2,
    "legacy_v1": 1,
}


@dataclass
class SpecValueInput:
    key: str
    source_type: str
    value_num: Decimal | None = None
    value_num_max: Decimal | None = None
    value_text: str | None = None
    value_bool: bool | None = None
    unit: str | None = None
    original_value: str | None = None
    original_unit: str | None = None
    status: str = "present"
    brochure_version_id: int | None = None
    source_page: int | None = None
    source_url: str | None = None
    confidence: Decimal | None = None
    flags: tuple[str, ...] = ()

    @property
    def has_value(self) -> bool:
        return self.value_num is not None or self.value_bool is not None or bool(self.value_text)

    def comparable(self) -> tuple[object, ...]:
        return (self.status, self.value_num, self.value_num_max, self.value_bool, (self.value_text or None))


@dataclass
class ApplyResult:
    written: int = 0  # inserted or replaced
    unchanged: int = 0  # identical value already stored
    conflicts_skipped: int = 0  # different value from a weaker / emptier source: kept existing
    empty_skipped: int = 0  # incoming 'present' row carried no value
    unknown_keys: list[str] = field(default_factory=list)

    def merge(self, other: ApplyResult) -> None:
        self.written += other.written
        self.unchanged += other.unchanged
        self.conflicts_skipped += other.conflicts_skipped
        self.empty_skipped += other.empty_skipped
        self.unknown_keys.extend(other.unknown_keys)


def _norm(value: Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(value).quantize(Decimal("0.0001"))


def _existing_comparable(row: VariantSpec) -> tuple[object, ...]:
    return (
        row.status,
        _norm(row.value_num),
        _norm(row.value_num_max),
        row.value_bool,
        (row.value_text or None),
    )


def _assign(row: VariantSpec, incoming: SpecValueInput, run_id: int | None, now: datetime) -> None:
    row.value_num = incoming.value_num
    row.value_num_max = incoming.value_num_max
    row.value_text = incoming.value_text
    row.value_bool = incoming.value_bool
    row.unit = incoming.unit
    row.original_value = incoming.original_value
    row.original_unit = incoming.original_unit
    row.status = incoming.status
    _assign_provenance(row, incoming, run_id, now)


def _assign_provenance(row: VariantSpec, incoming: SpecValueInput, run_id: int | None, now: datetime) -> None:
    row.source_type = incoming.source_type
    row.brochure_version_id = incoming.brochure_version_id
    row.source_page = incoming.source_page
    row.source_url = incoming.source_url
    row.confidence = incoming.confidence
    row.flags = list(incoming.flags) or None
    row.extraction_run_id = run_id
    row.extracted_at = now


def apply_spec_values(
    db: Session,
    variant_id: int,
    values: Iterable[SpecValueInput],
    *,
    run_id: int | None = None,
    reason: str | None = None,
) -> ApplyResult:
    """Upsert spec values for one variant under the guarded write rule. Does not commit."""
    result = ApplyResult()
    incoming_list = list(values)
    if not incoming_list:
        return result

    definitions = {
        d.key: d
        for d in db.execute(
            select(SpecDefinition).where(SpecDefinition.key.in_({v.key for v in incoming_list}))
        ).scalars()
    }
    existing = {
        row.spec_definition_id: row
        for row in db.execute(select(VariantSpec).where(VariantSpec.variant_id == variant_id)).scalars()
    }
    now = datetime.now(UTC)

    for incoming in incoming_list:
        definition = definitions.get(incoming.key)
        if definition is None:
            result.unknown_keys.append(incoming.key)
            continue
        incoming.value_num = _norm(incoming.value_num)
        incoming.value_num_max = _norm(incoming.value_num_max)
        if incoming.status == "present" and not incoming.has_value:
            result.empty_skipped += 1
            continue

        current = existing.get(definition.id)
        if current is None:
            row = VariantSpec(variant_id=variant_id, spec_definition_id=definition.id)
            _assign(row, incoming, run_id, now)
            db.add(row)
            existing[definition.id] = row
            result.written += 1
            continue

        incoming_rank = SOURCE_RANK.get(incoming.source_type, 0)
        current_rank = SOURCE_RANK.get(current.source_type, 0)

        if incoming.comparable() == _existing_comparable(current):
            if incoming_rank >= current_rank:
                _assign_provenance(current, incoming, run_id, now)
            result.unchanged += 1
            continue

        # A number already on file is not replaced by a same-rank value that lost its number
        # (typically a parse failure in a later, poorer extraction).
        loses_number = (
            definition.data_type == "number"
            and current.value_num is not None
            and incoming.value_num is None
            and incoming.status == "present"
            and incoming_rank <= current_rank
        )
        if incoming_rank < current_rank or loses_number:
            result.conflicts_skipped += 1
            continue

        db.add(
            VariantSpecHistory(
                variant_spec_id=current.id,
                value_num=current.value_num,
                value_num_max=current.value_num_max,
                value_text=current.value_text,
                value_bool=current.value_bool,
                unit=current.unit,
                original_value=current.original_value,
                status=current.status,
                source_type=current.source_type,
                brochure_version_id=current.brochure_version_id,
                source_page=current.source_page,
                confidence=current.confidence,
                extraction_run_id=current.extraction_run_id,
                superseded_by_run_id=run_id,
                reason=reason,
            )
        )
        _assign(current, incoming, run_id, now)
        result.written += 1

    db.flush()
    return result


# ---------------------------------------------------------------------------- reads
def load_variant_specs(db: Session, variant_ids: Sequence[int]) -> dict[int, list[VariantSpec]]:
    """Spec rows (with definition, category and brochure provenance) for the given variants."""
    by_variant: dict[int, list[VariantSpec]] = {variant_id: [] for variant_id in variant_ids}
    if not variant_ids:
        return by_variant
    rows = db.execute(
        select(VariantSpec)
        .where(VariantSpec.variant_id.in_(variant_ids))
        .options(
            joinedload(VariantSpec.definition).joinedload(SpecDefinition.category),
            joinedload(VariantSpec.brochure_version).joinedload(BrochureVersion.brochure),
        )
    ).scalars()
    for row in rows:
        by_variant[row.variant_id].append(row)
    return by_variant


def list_categories(db: Session) -> list[SpecCategory]:
    return list(
        db.execute(
            select(SpecCategory)
            .options(joinedload(SpecCategory.definitions))
            .order_by(SpecCategory.sort_order)
        )
        .unique()
        .scalars()
    )


def spec_coverage(db: Session) -> list[tuple[str, int]]:
    """How many variants have a value for each definition."""
    rows = db.execute(
        select(SpecDefinition.key, func.count(VariantSpec.id))
        .outerjoin(VariantSpec, VariantSpec.spec_definition_id == SpecDefinition.id)
        .group_by(SpecDefinition.key)
        .order_by(func.count(VariantSpec.id).desc(), SpecDefinition.key)
    ).all()
    return [(key, int(count)) for key, count in rows]


def total_variants(db: Session) -> int:
    return int(db.execute(select(func.count()).select_from(Variant)).scalar_one())


def brochure_of(row: VariantSpec) -> Brochure | None:
    return row.brochure_version.brochure if row.brochure_version is not None else None
