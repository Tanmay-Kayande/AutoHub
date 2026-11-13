"""The guarded write rule: good data is never silently overwritten."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.db.models import SpecDefinition, Variant, VariantSpec, VariantSpecHistory
from autohub.specs.service import SpecValueInput, apply_spec_values
from tests.factories import make_brand, make_model, make_variant

D = Decimal


def _variant(db: Session) -> Variant:
    return make_variant(db, make_model(db, make_brand(db)), "LX")


def _spec(db: Session, variant: Variant, key: str) -> VariantSpec | None:
    return db.execute(
        select(VariantSpec)
        .join(SpecDefinition, SpecDefinition.id == VariantSpec.spec_definition_id)
        .where(VariantSpec.variant_id == variant.id, SpecDefinition.key == key)
    ).scalar_one_or_none()


def _history(db: Session) -> list[VariantSpecHistory]:
    return list(db.execute(select(VariantSpecHistory).order_by(VariantSpecHistory.id)).scalars())


def _clearance(value: str | None, source: str = "official_brochure", **kwargs: object) -> SpecValueInput:
    return SpecValueInput(
        key="ground_clearance",
        source_type=source,
        value_num=D(value) if value is not None else None,
        unit="mm" if value is not None else None,
        original_value=f"{value} mm" if value is not None else None,
        **kwargs,  # type: ignore[arg-type]
    )


def test_insert_then_identical_value_is_unchanged(db: Session) -> None:
    variant = _variant(db)
    first = apply_spec_values(
        db, variant.id, [_clearance("220", confidence=D("0.90"), source_page=4)], run_id=None
    )
    assert (first.written, first.unchanged) == (1, 0)

    again = apply_spec_values(db, variant.id, [_clearance("220.0", confidence=D("0.95"), source_page=7)])
    assert (again.written, again.unchanged, again.conflicts_skipped) == (0, 1, 0)
    row = _spec(db, variant, "ground_clearance")
    assert row is not None and row.value_num == D("220")
    assert (row.confidence, row.source_page) == (D("0.95"), 7)  # provenance refreshed by an equal-rank source
    assert _history(db) == []


def test_better_source_replaces_legacy_value_and_logs_history(db: Session) -> None:
    variant = _variant(db)
    apply_spec_values(db, variant.id, [_clearance("200", source="legacy_v1")])
    result = apply_spec_values(db, variant.id, [_clearance("220")], reason="re-extraction")
    assert result.written == 1

    row = _spec(db, variant, "ground_clearance")
    assert row is not None and (row.value_num, row.source_type) == (D("220"), "official_brochure")
    history = _history(db)
    assert len(history) == 1
    assert (history[0].value_num, history[0].source_type, history[0].original_value, history[0].reason) == (
        D("200"), "legacy_v1", "200 mm", "re-extraction",
    )  # fmt: skip


def test_weaker_source_cannot_overwrite(db: Session) -> None:
    variant = _variant(db)
    apply_spec_values(db, variant.id, [_clearance("220")])
    for weaker in ("legacy_v1", "third_party"):
        result = apply_spec_values(db, variant.id, [_clearance("180", source=weaker)])
        assert (result.written, result.conflicts_skipped) == (0, 1)
    row = _spec(db, variant, "ground_clearance")
    assert row is not None and (row.value_num, row.source_type) == (D("220"), "official_brochure")
    assert _history(db) == []


def test_manual_correction_outranks_brochure_and_survives_reextraction(db: Session) -> None:
    variant = _variant(db)
    apply_spec_values(db, variant.id, [_clearance("220")])
    assert apply_spec_values(db, variant.id, [_clearance("225", source="manual")]).written == 1
    later = apply_spec_values(db, variant.id, [_clearance("220")])  # a later brochure run
    assert later.conflicts_skipped == 1
    row = _spec(db, variant, "ground_clearance")
    assert row is not None and (row.value_num, row.source_type) == (D("225"), "manual")


def test_empty_incoming_value_never_blanks_existing_data(db: Session) -> None:
    variant = _variant(db)
    apply_spec_values(db, variant.id, [_clearance("220")])
    result = apply_spec_values(db, variant.id, [_clearance(None)])
    assert (result.written, result.empty_skipped) == (0, 1)
    row = _spec(db, variant, "ground_clearance")
    assert row is not None and row.value_num == D("220")


def test_same_rank_value_that_lost_its_number_is_rejected(db: Session) -> None:
    """A later, poorer extraction ('approx. 220' with no parsed number) must not replace a number."""
    variant = _variant(db)
    apply_spec_values(db, variant.id, [_clearance("220")])
    poorer = SpecValueInput(
        key="ground_clearance",
        source_type="official_brochure",
        value_text="approx. 220",
        original_value="approx. 220",
    )
    result = apply_spec_values(db, variant.id, [poorer])
    assert (result.written, result.conflicts_skipped) == (0, 1)
    row = _spec(db, variant, "ground_clearance")
    assert row is not None and row.value_num == D("220") and row.value_text is None


def test_same_rank_new_number_replaces_with_history(db: Session) -> None:
    """A newer brochure legitimately changes a figure."""
    variant = _variant(db)
    apply_spec_values(db, variant.id, [_clearance("220", brochure_version_id=None)])
    assert apply_spec_values(db, variant.id, [_clearance("226")]).written == 1
    assert [h.value_num for h in _history(db)] == [D("220")]


def test_status_change_is_a_real_change(db: Session) -> None:
    variant = _variant(db)
    seats = SpecValueInput(
        key="ventilated_seats", source_type="official_brochure", value_bool=True, original_value="✓"
    )
    apply_spec_values(db, variant.id, [seats])
    optional = SpecValueInput(
        key="ventilated_seats", source_type="official_brochure", status="optional", original_value="O"
    )
    assert apply_spec_values(db, variant.id, [optional]).written == 1
    row = _spec(db, variant, "ventilated_seats")
    assert row is not None and (row.status, row.value_bool) == ("optional", None)
    assert [(h.status, h.value_bool) for h in _history(db)] == [("present", True)]


def test_unknown_keys_are_reported_not_written(db: Session) -> None:
    variant = _variant(db)
    result = apply_spec_values(
        db, variant.id,
        [SpecValueInput(key="flux_capacitor", source_type="official_brochure", value_text="1.21 GW"), _clearance("220")],
    )  # fmt: skip
    assert result.unknown_keys == ["flux_capacitor"] and result.written == 1


def test_values_absent_from_a_run_are_left_alone(db: Session) -> None:
    """An extraction that returns fewer specs deletes nothing."""
    variant = _variant(db)
    apply_spec_values(
        db,
        variant.id,
        [_clearance("220"), SpecValueInput(key="airbags", source_type="official_brochure", value_num=D(6))],
    )
    apply_spec_values(db, variant.id, [_clearance("220")])  # airbags not mentioned this time
    assert _spec(db, variant, "airbags") is not None
