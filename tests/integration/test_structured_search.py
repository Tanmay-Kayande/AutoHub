"""Structured search answers exact questions from SQL, validates every key and reports coverage."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from autohub.search.structured import StructuredQuery, run_structured
from tests.factories import make_brand, make_model, make_variant


@pytest.fixture()
def catalogue(db: Session) -> dict[str, Any]:
    testa = make_brand(db)
    other = make_brand(db, "Otra")
    q = make_model(db, testa, "Model Q")
    r = make_model(db, testa, "Model R", body_type="hatchback")
    s = make_model(db, other, "Sierra")
    v = {
        "q_lx": make_variant(db, q, "LX", fuel_type="diesel", transmission_type="manual", price=Decimal(1500000),
                             specs={"ground_clearance": 209, "max_torque": 300, "airbags": 6, "ventilated_seats": False}),
        "q_zx": make_variant(db, q, "ZX", fuel_type="petrol", transmission_type="automatic", price=Decimal(1900000),
                             specs={"ground_clearance": 215, "max_torque": 250, "airbags": 6, "ventilated_seats": True}),
        "r_base": make_variant(db, r, "Base", fuel_type="petrol", transmission_type="amt", price=Decimal(700000),
                               specs={"ground_clearance": 165, "max_torque": 113, "airbags": 2}),
        "s_top": make_variant(db, s, "Top", fuel_type="petrol", transmission_type="automatic", price=Decimal(1400000),
                              specs={"ground_clearance": 220, "max_torque": 200, "airbags": 6, "ventilated_seats": True}),
        "s_nodata": make_variant(db, s, "Bare", fuel_type="petrol", transmission_type="manual"),
    }  # fmt: skip
    return v


def ids(result: Any) -> list[int]:
    return [row.variant_id for row in result.rows]


def q(**kwargs: Any) -> StructuredQuery:
    return StructuredQuery.model_validate(kwargs)


def test_highest_ground_clearance_suv_reports_coverage(db: Session, catalogue: dict[str, Any]) -> None:
    result = run_structured(
        db, q(filters={"body_type": ["suv"]}, sort={"key": "ground_clearance", "direction": "desc"}, limit=1)
    )
    assert ids(result) == [catalogue["s_top"].id]
    assert result.rows[0].values["ground_clearance"]["display"] == "220 mm"
    assert result.missing_value_counts == {"ground_clearance": 1}  # "Bare" has no value
    assert result.total_matched == 3


def test_petrol_automatic_suvs_with_six_airbags(db: Session, catalogue: dict[str, Any]) -> None:
    result = run_structured(
        db,
        q(
            filters={
                "body_type": ["suv"],
                "fuel_type": ["petrol"],
                "transmission_type": ["automatic", "amt"],
            },
            spec_filters=[{"key": "airbags", "op": "gte", "value": 6}],
        ),
    )
    assert set(ids(result)) == {catalogue["q_zx"].id, catalogue["s_top"].id}


def test_torque_above_200_nm_grouped_by_model(db: Session, catalogue: dict[str, Any]) -> None:
    result = run_structured(
        db,
        q(
            spec_filters=[{"key": "max_torque", "op": "gt", "value": 200}],
            sort={"key": "max_torque", "direction": "desc"},
            group_by="model",
        ),
    )
    assert ids(result) == [catalogue["q_lx"].id]  # best Model Q variant; Sierra's 200 is not > 200


def test_feature_yes_and_no(db: Session, catalogue: dict[str, Any]) -> None:
    has = run_structured(db, q(spec_filters=[{"key": "ventilated_seats", "op": "is_true"}]))
    lacks = run_structured(db, q(spec_filters=[{"key": "ventilated_seats", "op": "is_false"}]))
    assert set(ids(has)) == {catalogue["q_zx"].id, catalogue["s_top"].id}
    assert ids(lacks) == [catalogue["q_lx"].id]


def test_cheapest_under_budget_by_brand_slug(db: Session, catalogue: dict[str, Any]) -> None:
    result = run_structured(
        db, q(filters={"brand": ["testa"], "price_max": 1600000}, sort={"key": "price", "direction": "asc"})
    )
    assert ids(result) == [catalogue["r_base"].id, catalogue["q_lx"].id]


@pytest.mark.parametrize(
    "payload",
    [
        {"spec_filters": [{"key": "warp_factor", "op": "gt", "value": 9}]},
        {"spec_filters": [{"key": "sunroof", "op": "gt", "value": 1}]},
        {"spec_filters": [{"key": "max_torque", "op": "is_true"}]},
        {"spec_filters": [{"key": "max_torque", "op": "between", "value": 1}]},
        {"spec_filters": [{"key": "max_torque", "op": "drop table", "value": 1}]},
        {"sort": {"key": "front_suspension"}},
        {"filters": {"fuel_type": ["plutonium"]}},
        {"limit": 500},
    ],
)
def test_invalid_queries_are_rejected_before_sql(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        StructuredQuery.model_validate(payload)


def test_text_contains_escapes_wildcards(db: Session, catalogue: dict[str, Any]) -> None:
    result = run_structured(db, q(spec_filters=[{"key": "front_suspension", "op": "contains", "value": "%"}]))
    assert result.rows == []
