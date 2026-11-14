"""Structured search: a validated query contract turned into one parameterised SQL statement.

Exact answers ("highest ground clearance", "petrol automatics with 6 airbags")
come from ``variant_specs``, never from embeddings or the LLM. Every key and
operator is checked against the taxonomy before SQL is built; values are bound
parameters. Variants with no value for a filtered or sorted spec are excluded,
and the result says how many were excluded per key, so rankings are honest
about coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import ColumnElement, and_, exists, false, func, or_, select
from sqlalchemy.orm import Session, aliased

from autohub.db.models import (
    BODY_TYPES,
    DRIVETRAINS,
    FUEL_TYPES,
    TRANSMISSION_TYPES,
    Brand,
    ModelGeneration,
    SpecDefinition,
    Variant,
    VariantSpec,
    VehicleModel,
)
from autohub.specs.formatting import format_spec_value
from autohub.specs.taxonomy import get_spec

NumberOp = Literal["eq", "ne", "gt", "gte", "lt", "lte", "between"]
SpecOp = Literal["eq", "ne", "gt", "gte", "lt", "lte", "between", "is_true", "is_false", "contains"]
_OPS_BY_TYPE: dict[str, set[str]] = {
    "number": {"eq", "ne", "gt", "gte", "lt", "lte", "between"},
    "boolean": {"is_true", "is_false"},
    "text": {"contains", "eq"},
}
MAX_ROWS = 5000


class QueryValidationError(ValueError):
    pass


class Filters(BaseModel):
    brand: list[str] = Field(default_factory=list, description="brand names or slugs")
    model: list[str] = Field(default_factory=list, description="model names or slugs")
    body_type: list[str] = Field(default_factory=list)
    fuel_type: list[str] = Field(default_factory=list)
    transmission_type: list[str] = Field(default_factory=list)
    drivetrain: list[str] = Field(default_factory=list)
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    model_ids: list[int] = Field(default_factory=list)
    variant_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _codes(self) -> Filters:
        for name, allowed in (
            ("body_type", BODY_TYPES),
            ("fuel_type", FUEL_TYPES),
            ("transmission_type", TRANSMISSION_TYPES),
            ("drivetrain", DRIVETRAINS),
        ):
            bad = [v for v in getattr(self, name) if v not in allowed]
            if bad:
                raise ValueError(f"{name}: unknown value(s) {bad}; allowed {list(allowed)}")
        return self


class SpecFilter(BaseModel):
    key: str
    op: SpecOp
    value: Decimal | str | None = None
    value_max: Decimal | None = None

    @field_validator("key")
    @classmethod
    def _known(cls, key: str) -> str:
        if get_spec(key) is None:
            raise ValueError(f"unknown spec key {key!r}")
        return key

    @model_validator(mode="after")
    def _op_fits(self) -> SpecFilter:
        spec = get_spec(self.key)
        assert spec is not None
        if self.op not in _OPS_BY_TYPE[spec.data_type]:
            raise ValueError(f"operator {self.op!r} does not apply to {spec.data_type} spec {self.key!r}")
        if spec.data_type == "number":
            if not isinstance(self.value, Decimal):
                raise ValueError(f"{self.key}: a numeric value (in {spec.unit or 'units'}) is required")
            if self.op == "between" and self.value_max is None:
                raise ValueError(f"{self.key}: 'between' needs value_max")
        if spec.data_type == "text" and not (isinstance(self.value, str) and self.value.strip()):
            raise ValueError(f"{self.key}: a text value is required")
        return self


class Sort(BaseModel):
    key: str = Field(description="a numeric spec key, or 'price'")
    direction: Literal["asc", "desc"] = "desc"

    @field_validator("key")
    @classmethod
    def _sortable(cls, key: str) -> str:
        if key == "price":
            return key
        spec = get_spec(key)
        if spec is None or spec.data_type != "number":
            raise ValueError(f"cannot sort by {key!r}: only numeric spec keys or 'price'")
        return key


class StructuredQuery(BaseModel):
    filters: Filters = Field(default_factory=Filters)
    spec_filters: list[SpecFilter] = Field(default_factory=list, max_length=12)
    sort: Sort | None = None
    group_by: Literal["variant", "model"] = "variant"
    limit: int = Field(default=10, ge=1, le=50)


@dataclass
class StructuredRow:
    variant_id: int
    variant: str
    model_id: int
    model: str
    model_slug: str
    brand: str
    brand_slug: str
    fuel_type: str | None
    transmission_type: str | None
    price_ex_showroom: Decimal | None
    values: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class StructuredResult:
    rows: list[StructuredRow]
    total_matched: int
    missing_value_counts: dict[str, int]


def _name_match(column: Any, slug_column: Any, names: list[str]) -> ColumnElement[bool]:
    lowered = [n.strip().lower() for n in names if n.strip()]
    if not lowered:
        return false()
    return or_(func.lower(column).in_(lowered), slug_column.in_(lowered))


def _spec_condition(spec_filter: SpecFilter, definition_id: int) -> ColumnElement[bool]:
    vs = aliased(VariantSpec)
    base = and_(vs.variant_id == Variant.id, vs.spec_definition_id == definition_id)
    op = spec_filter.op
    if op == "is_true":
        return exists().where(base, vs.status == "present", vs.value_bool.is_(True))
    if op == "is_false":
        return exists().where(
            base, or_(vs.status == "not_available", and_(vs.status == "present", vs.value_bool.is_(False)))
        )
    if op == "contains":
        assert isinstance(spec_filter.value, str)
        pattern = "%" + spec_filter.value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return exists().where(base, vs.status == "present", vs.value_text.ilike(pattern, escape="\\"))
    if isinstance(spec_filter.value, str):  # text eq
        return exists().where(
            base, vs.status == "present", func.lower(vs.value_text) == spec_filter.value.lower()
        )
    value = spec_filter.value
    column = vs.value_num
    comparisons: dict[str, ColumnElement[bool]] = {
        "eq": column == value,
        "ne": column != value,
        "gt": column > value,
        "gte": column >= value,
        "lt": column < value,
        "lte": column <= value,
        "between": column.between(value, spec_filter.value_max),
    }
    return exists().where(base, vs.status == "present", vs.value_num.is_not(None), comparisons[op])


def run_structured(db: Session, query: StructuredQuery) -> StructuredResult:
    keys = {f.key for f in query.spec_filters}
    if query.sort and query.sort.key != "price":
        keys.add(query.sort.key)
    definitions = {
        d.key: d for d in db.execute(select(SpecDefinition).where(SpecDefinition.key.in_(keys))).scalars()
    }
    missing_keys = keys - set(definitions)
    if missing_keys:
        raise QueryValidationError(f"spec keys not in the database: {sorted(missing_keys)}")

    f = query.filters
    base_conditions: list[ColumnElement[bool]] = [
        Variant.is_active.is_(True),
        VehicleModel.is_active.is_(True),
        Brand.is_active.is_(True),
        ModelGeneration.is_current.is_(True),
    ]
    if f.brand:
        base_conditions.append(_name_match(Brand.name, Brand.slug, f.brand))
    if f.model:
        base_conditions.append(_name_match(VehicleModel.name, VehicleModel.slug, f.model))
    if f.model_ids:
        base_conditions.append(VehicleModel.id.in_(f.model_ids))
    if f.variant_ids:
        base_conditions.append(Variant.id.in_(f.variant_ids))
    if f.body_type:
        base_conditions.append(VehicleModel.body_type.in_(f.body_type))
    if f.fuel_type:
        base_conditions.append(Variant.fuel_type.in_(f.fuel_type))
    if f.transmission_type:
        base_conditions.append(Variant.transmission_type.in_(f.transmission_type))
    if f.drivetrain:
        base_conditions.append(Variant.drivetrain.in_(f.drivetrain))
    if f.price_min is not None:
        base_conditions.append(Variant.price_ex_showroom >= f.price_min)
    if f.price_max is not None:
        base_conditions.append(Variant.price_ex_showroom <= f.price_max)

    def frame() -> Any:
        return (
            select(Variant, VehicleModel, Brand)
            .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
            .join(VehicleModel, VehicleModel.id == ModelGeneration.model_id)
            .join(Brand, Brand.id == VehicleModel.brand_id)
            .where(*base_conditions)
        )

    # Coverage: how many otherwise-eligible variants have no usable value per key.
    missing: dict[str, int] = {}
    for key in sorted(keys):
        definition = definitions[key]
        vs = aliased(VariantSpec)
        has_value = exists().where(
            vs.variant_id == Variant.id,
            vs.spec_definition_id == definition.id,
            or_(
                vs.status != "present",
                vs.value_num.is_not(None),
                vs.value_bool.is_not(None),
                vs.value_text.is_not(None),
            ),
        )
        missing[key] = int(
            db.execute(select(func.count()).select_from(frame().where(~has_value).subquery())).scalar_one()
        )

    stmt = frame()
    for spec_filter in query.spec_filters:
        stmt = stmt.where(_spec_condition(spec_filter, definitions[spec_filter.key].id))
    if query.sort is not None:
        if query.sort.key == "price":
            order_col: Any = Variant.price_ex_showroom
            stmt = stmt.where(Variant.price_ex_showroom.is_not(None))
        else:
            sort_spec = aliased(VariantSpec)
            stmt = stmt.join(
                sort_spec,
                and_(
                    sort_spec.variant_id == Variant.id,
                    sort_spec.spec_definition_id == definitions[query.sort.key].id,
                    sort_spec.status == "present",
                    sort_spec.value_num.is_not(None),
                ),
            )
            order_col = sort_spec.value_num
        stmt = stmt.order_by(order_col.desc() if query.sort.direction == "desc" else order_col.asc())
    stmt = stmt.order_by(Brand.name, VehicleModel.name, Variant.name).limit(MAX_ROWS)

    matched = list(db.execute(stmt).tuples())
    total = len(matched)
    if query.group_by == "model":
        seen: set[int] = set()
        grouped = []
        for variant, model, brand in matched:
            if model.id not in seen:
                seen.add(model.id)
                grouped.append((variant, model, brand))
        matched = grouped
    chosen = matched[: query.limit]

    values = _values_for(db, [v.id for v, _, _ in chosen], [definitions[k] for k in sorted(keys)])
    rows = [
        StructuredRow(
            variant_id=variant.id,
            variant=variant.name,
            model_id=model.id,
            model=model.name,
            model_slug=model.slug,
            brand=brand.name,
            brand_slug=brand.slug,
            fuel_type=variant.fuel_type,
            transmission_type=variant.transmission_type,
            price_ex_showroom=variant.price_ex_showroom,
            values=values.get(variant.id, {}),
        )
        for variant, model, brand in chosen
    ]
    return StructuredResult(rows=rows, total_matched=total, missing_value_counts=missing)


def _values_for(
    db: Session, variant_ids: list[int], definitions: list[SpecDefinition]
) -> dict[int, dict[str, dict[str, Any]]]:
    if not variant_ids or not definitions:
        return {}
    by_id = {d.id: d for d in definitions}
    out: dict[int, dict[str, dict[str, Any]]] = {}
    for row in db.execute(
        select(VariantSpec).where(
            VariantSpec.variant_id.in_(variant_ids), VariantSpec.spec_definition_id.in_(by_id)
        )
    ).scalars():
        definition = by_id[row.spec_definition_id]
        out.setdefault(row.variant_id, {})[definition.key] = {
            "display": format_spec_value(
                data_type=definition.data_type,
                status=row.status,
                value_num=row.value_num,
                value_num_max=row.value_num_max,
                value_text=row.value_text,
                value_bool=row.value_bool,
                unit=row.unit,
            ),
            "value_num": row.value_num,
            "value_bool": row.value_bool,
            "status": row.status,
            "source_type": row.source_type,
            "brochure_version_id": row.brochure_version_id,
            "source_page": row.source_page,
            "variant_spec_id": row.id,
        }
    return out
