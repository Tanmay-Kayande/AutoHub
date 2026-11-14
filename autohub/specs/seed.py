"""Idempotent seeding of ``spec_categories`` / ``spec_definitions`` from the taxonomy.

Upserts by key, so it is safe to run on every deploy. Definitions that have
been removed from the taxonomy are left in the database (their values may still
be referenced); they are only reported.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, text

from autohub.specs.taxonomy import TAXONOMY


@dataclass(frozen=True)
class SeedResult:
    categories: int
    definitions: int
    orphaned_keys: tuple[str, ...]


_UPSERT_CATEGORY = text(
    """
    INSERT INTO spec_categories (key, name, sort_order)
    VALUES (:key, :name, :sort_order)
    ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order
    RETURNING id
    """
)

_UPSERT_DEFINITION = text(
    """
    INSERT INTO spec_definitions
        (category_id, key, name, data_type, canonical_unit, description, sort_order,
         higher_is_better, is_filterable, is_key_spec, applies_to_fuel)
    VALUES
        (:category_id, :key, :name, :data_type, :canonical_unit, :description, :sort_order,
         :higher_is_better, :is_filterable, :is_key_spec, :applies_to_fuel)
    ON CONFLICT (key) DO UPDATE SET
        category_id = EXCLUDED.category_id,
        name = EXCLUDED.name,
        data_type = EXCLUDED.data_type,
        canonical_unit = EXCLUDED.canonical_unit,
        description = EXCLUDED.description,
        sort_order = EXCLUDED.sort_order,
        higher_is_better = EXCLUDED.higher_is_better,
        is_filterable = EXCLUDED.is_filterable,
        is_key_spec = EXCLUDED.is_key_spec,
        applies_to_fuel = EXCLUDED.applies_to_fuel
    """
)


def seed_taxonomy(connection: Connection) -> SeedResult:
    """Upsert the taxonomy on ``connection`` (the caller owns the transaction)."""
    definition_count = 0
    keys: list[str] = []
    for category_order, category in enumerate(TAXONOMY, start=1):
        category_id = connection.execute(
            _UPSERT_CATEGORY,
            {"key": category.key, "name": category.name, "sort_order": category_order * 10},
        ).scalar_one()
        for spec_order, spec in enumerate(category.specs, start=1):
            connection.execute(
                _UPSERT_DEFINITION,
                {
                    "category_id": category_id,
                    "key": spec.key,
                    "name": spec.name,
                    "data_type": spec.data_type,
                    "canonical_unit": spec.unit,
                    "description": spec.description,
                    "sort_order": spec_order * 10,
                    "higher_is_better": spec.higher_is_better,
                    "is_filterable": spec.filterable,
                    "is_key_spec": spec.key_spec,
                    "applies_to_fuel": list(spec.applies_to_fuel) if spec.applies_to_fuel else None,
                },
            )
            definition_count += 1
            keys.append(spec.key)

    orphaned = connection.execute(
        text("SELECT key FROM spec_definitions WHERE NOT (key = ANY(:keys)) ORDER BY key"),
        {"keys": keys},
    ).scalars()
    return SeedResult(len(TAXONOMY), definition_count, tuple(orphaned))
