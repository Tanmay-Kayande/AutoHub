"""Turn spec rows into API shapes: grouped spec sheets and comparison tables."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy.orm import Session

from autohub.api.schemas import (
    CompareCell,
    CompareGroup,
    CompareOut,
    CompareRow,
    SpecGroupOut,
    SpecSource,
    SpecValueOut,
    VariantDetail,
    VariantSpecsOut,
)
from autohub.db.models import SpecDefinition, VariantSpec
from autohub.specs.formatting import format_spec_value
from autohub.specs.service import list_categories, load_variant_specs


def display_of(row: VariantSpec) -> str:
    return format_spec_value(
        data_type=row.definition.data_type,
        status=row.status,
        value_num=row.value_num,
        value_num_max=row.value_num_max,
        value_text=row.value_text,
        value_bool=row.value_bool,
        unit=row.unit,
    )


def source_of(row: VariantSpec) -> SpecSource:
    version = row.brochure_version
    brochure = version.brochure if version is not None else None
    return SpecSource(
        type=row.source_type,
        brochure_id=brochure.id if brochure else None,
        brochure_version_id=version.id if version else None,
        brochure_title=(brochure.title or brochure.model_hint) if brochure else None,
        page=row.source_page,
        url=(brochure.source_url if brochure else None) or row.source_url,
    )


def to_spec_value(row: VariantSpec) -> SpecValueOut:
    definition = row.definition
    return SpecValueOut(
        key=definition.key,
        name=definition.name,
        data_type=definition.data_type,
        unit=row.unit or definition.canonical_unit,
        value_num=row.value_num,
        value_num_max=row.value_num_max,
        value_text=row.value_text,
        value_bool=row.value_bool,
        display=display_of(row),
        status=row.status,
        original_value=row.original_value,
        source=source_of(row),
        confidence=row.confidence,
        flags=list(row.flags or []),
        extracted_at=row.extracted_at,
    )


def variant_spec_sheet(db: Session, variant_id: int, *, category: str | None = None) -> VariantSpecsOut:
    """Stored specs of one variant grouped by category, in taxonomy order."""
    rows = load_variant_specs(db, [variant_id])[variant_id]
    by_definition = {row.spec_definition_id: row for row in rows}
    groups: list[SpecGroupOut] = []
    total = 0
    for cat in list_categories(db):
        if category and cat.key != category:
            continue
        specs = [to_spec_value(by_definition[d.id]) for d in cat.definitions if d.id in by_definition]
        if specs:
            groups.append(SpecGroupOut(key=cat.key, name=cat.name, specs=specs))
            total += len(specs)
    return VariantSpecsOut(variant_id=variant_id, groups=groups, total=total)


def _cell(variant_id: int, row: VariantSpec | None) -> CompareCell:
    if row is None:
        return CompareCell(
            variant_id=variant_id, status="missing", display=None, value_num=None, value_bool=None,
            value_text=None, source_type=None,
        )  # fmt: skip
    return CompareCell(
        variant_id=variant_id,
        status=row.status,
        display=display_of(row),
        value_num=row.value_num,
        value_bool=row.value_bool,
        value_text=row.value_text,
        source_type=row.source_type,
    )


def _signature(cell: CompareCell) -> tuple[object, ...]:
    """What makes two cells 'the same' for difference highlighting."""
    if cell.status == "missing":
        return ("missing",)
    if cell.value_num is not None:
        return (cell.status, Decimal(cell.value_num).normalize())
    if cell.value_bool is not None:
        return (cell.status, cell.value_bool)
    return (cell.status, (cell.value_text or "").strip().lower())


def _best(definition: SpecDefinition, cells: Sequence[CompareCell]) -> list[int]:
    """Variants holding the best value, when 'better' is defined and at least two can be compared."""
    if definition.higher_is_better is None:
        return []
    numeric = [
        (c.variant_id, Decimal(c.value_num))
        for c in cells
        if c.status == "present" and c.value_num is not None
    ]
    if len(numeric) < 2:
        return []
    values = [value for _, value in numeric]
    if min(values) == max(values):
        return []
    target = max(values) if definition.higher_is_better else min(values)
    return [variant_id for variant_id, value in numeric if value == target]


def compare_variants(db: Session, variants: Sequence[VariantDetail]) -> CompareOut:
    """Side-by-side spec matrix. Rows appear for every spec at least one variant has."""
    variant_ids = [v.id for v in variants]
    specs = load_variant_specs(db, variant_ids)
    lookup = {(row.variant_id, row.spec_definition_id): row for rows in specs.values() for row in rows}

    groups: list[CompareGroup] = []
    rows_total = rows_differing = 0
    for category in list_categories(db):
        rows: list[CompareRow] = []
        for definition in category.definitions:
            if not any((variant_id, definition.id) in lookup for variant_id in variant_ids):
                continue
            cells = [_cell(variant_id, lookup.get((variant_id, definition.id))) for variant_id in variant_ids]
            best = _best(definition, cells)
            for cell in cells:
                cell.is_best = cell.variant_id in best
            differs = len({_signature(cell) for cell in cells}) > 1
            rows.append(
                CompareRow(
                    key=definition.key,
                    name=definition.name,
                    data_type=definition.data_type,
                    unit=definition.canonical_unit,
                    higher_is_better=definition.higher_is_better,
                    differs=differs,
                    best_variant_ids=best,
                    cells=cells,
                )
            )
            rows_total += 1
            rows_differing += differs
        if rows:
            groups.append(CompareGroup(key=category.key, name=category.name, rows=rows))
    return CompareOut(
        variants=list(variants), groups=groups, rows_total=rows_total, rows_differing=rows_differing
    )
