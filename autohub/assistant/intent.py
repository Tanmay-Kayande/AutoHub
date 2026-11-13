"""Question understanding: LLM plan → validated plan with names resolved to ids.

The model only proposes; this module decides. Unknown spec keys and invalid
filters are dropped (and reported), names are matched exactly, by slug, then by
trigram similarity, and anything unmatched is reported so the answer can say so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autohub.assistant.llm import LLMProvider, LLMResult
from autohub.assistant.prompts import plan_system_prompt, plan_user_prompt
from autohub.assistant.schemas import AssistantContext, QueryPlan
from autohub.catalog.slugs import slugify
from autohub.db.models import Brand, ModelGeneration, Variant, VehicleModel
from autohub.ingestion.writer import name_tokens
from autohub.search.structured import Filters, Sort, SpecFilter, StructuredQuery
from autohub.specs.taxonomy import get_spec

logger = logging.getLogger(__name__)

TRIGRAM_MIN = 0.4
MAX_VARIANTS_PER_MODEL = 40


@dataclass
class ResolvedPlan:
    plan: QueryPlan
    brand_ids: list[int] = field(default_factory=list)
    model_ids: list[int] = field(default_factory=list)
    variant_ids: list[int] = field(default_factory=list)
    spec_keys: list[str] = field(default_factory=list)
    structured: StructuredQuery | None = None
    unresolved: list[str] = field(default_factory=list)
    suggestions: dict[str, list[str]] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def intent(self) -> str:
        return self.plan.intent


def _match_name(db: Session, model: Any, name: str, extra: Any = None) -> tuple[Any | None, list[str]]:
    """Exact (case-insensitive) or slug match, then best trigram match. Returns (row, close names)."""
    conditions = [extra] if extra is not None else []
    exact = (
        db.execute(
            select(model).where(
                *conditions, (func.lower(model.name) == name.strip().lower()) | (model.slug == slugify(name))
            )
        )
        .scalars()
        .first()
    )
    if exact is not None:
        return exact, []
    compact = name.replace(" ", "").lower()
    score = func.greatest(
        func.similarity(model.name, name), func.similarity(func.replace(model.name, " ", ""), compact)
    )
    rows = db.execute(
        select(model, score.label("s")).where(*conditions).order_by(score.desc()).limit(3)
    ).all()
    if rows and float(rows[0][1]) >= TRIGRAM_MIN:
        return rows[0][0], []
    return None, [row[0].name for row in rows if float(row[1]) > 0.15]


def resolve(db: Session, plan: QueryPlan, context: AssistantContext) -> ResolvedPlan:
    resolved = ResolvedPlan(plan=plan)

    for name in plan.brands:
        brand, close = _match_name(db, Brand, name)
        if brand is not None:
            resolved.brand_ids.append(brand.id)
        else:
            resolved.unresolved.append(name)
            resolved.suggestions[name] = close
    brand_scope = VehicleModel.brand_id.in_(resolved.brand_ids) if resolved.brand_ids else None
    for name in plan.models:
        model, close = _match_name(db, VehicleModel, name, brand_scope)
        if model is None and brand_scope is not None:
            model, close = _match_name(db, VehicleModel, name)
        if model is not None:
            if model.id not in resolved.model_ids:
                resolved.model_ids.append(model.id)
        else:
            resolved.unresolved.append(name)
            resolved.suggestions[name] = close
    # Page context scopes questions about "this car", never catalogue-wide rankings or filters.
    use_context = plan.intent not in ("ranking", "filter", "general", "out_of_scope") and not plan.brands
    if not resolved.model_ids and not plan.models and context.model_id is not None and use_context:
        resolved.model_ids.append(context.model_id)

    if plan.variants:
        candidates = list(
            db.execute(
                select(Variant)
                .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
                .where(ModelGeneration.model_id.in_(resolved.model_ids or [-1]), Variant.is_active.is_(True))
            ).scalars()
        )
        for name in plan.variants:
            wanted = set(name_tokens(name))
            # A variant matches when its name contains every word the user gave ("AX7 diesel AT").
            matches = [v for v in candidates if wanted and wanted <= set(name_tokens(v.name))]
            if matches:
                resolved.variant_ids.extend(v.id for v in matches if v.id not in resolved.variant_ids)
            else:
                resolved.unresolved.append(name)
                resolved.suggestions[name] = [v.name for v in candidates[:5]]
    elif not plan.models and context.variant_ids and use_context:
        resolved.variant_ids = list(context.variant_ids)

    for key in plan.spec_keys:
        if get_spec(key) is not None:
            resolved.spec_keys.append(key)
        else:
            resolved.dropped.append(f"spec key {key}")

    if plan.intent in ("ranking", "filter"):
        resolved.structured = _structured(plan, resolved)
    return resolved


def _structured(plan: QueryPlan, resolved: ResolvedPlan) -> StructuredQuery:
    spec_filters: list[SpecFilter] = []
    for item in plan.spec_filters:
        try:
            value: Decimal | str | None = item.text if item.op in ("contains",) or item.text else None
            if value is None and item.value is not None:
                value = Decimal(str(item.value))
            spec_filters.append(
                SpecFilter(
                    key=item.key,
                    op=item.op,
                    value=value,
                    value_max=Decimal(str(item.value_max)) if item.value_max is not None else None,
                )
            )
        except (ValidationError, ArithmeticError) as exc:
            resolved.dropped.append(f"filter {item.key} {item.op}: {exc.__class__.__name__}")
    filters: dict[str, Any] = {}
    for name in ("body_type", "fuel_type", "transmission_type", "drivetrain"):
        values = getattr(plan.filters, name)
        try:
            Filters.model_validate({name: values})
            filters[name] = values
        except ValidationError:
            resolved.dropped.append(f"filter {name}={values}")
    if plan.filters.price_max is not None and plan.filters.price_max > 0:
        filters["price_max"] = Decimal(str(plan.filters.price_max))
    if resolved.model_ids:
        filters["model_ids"] = resolved.model_ids  # brand-only scope is added by understand()
    sort = None
    if plan.sort_key:
        try:
            sort = Sort(key=plan.sort_key, direction=plan.sort_direction or "desc")
        except ValidationError:
            resolved.dropped.append(f"sort {plan.sort_key}")
    query = StructuredQuery(
        filters=Filters(**filters),
        spec_filters=spec_filters,
        sort=sort,
        group_by="model" if plan.group_by_model else "variant",
        limit=10,
    )
    return query


def understand(db: Session, provider: LLMProvider, question: str, context: AssistantContext) -> ResolvedPlan:
    brands = list(
        db.execute(select(Brand.name).where(Brand.is_active.is_(True)).order_by(Brand.name)).scalars()
    )
    models = list(
        db.execute(
            select(Brand.name, VehicleModel.name)
            .join(Brand, Brand.id == VehicleModel.brand_id)
            .where(VehicleModel.is_active.is_(True))
            .order_by(Brand.name, VehicleModel.name)
        ).tuples()
    )
    context_vehicle = None
    if context.model_id is not None:
        row = db.execute(
            select(Brand.name, VehicleModel.name)
            .join(Brand, Brand.id == VehicleModel.brand_id)
            .where(VehicleModel.id == context.model_id)
        ).first()
        if row:
            context_vehicle = f"{row[0]} {row[1]}"
    result: LLMResult[QueryPlan] = provider.generate_structured(
        system=plan_system_prompt(brands, [f"{b} {m}" for b, m in models]),
        prompt=plan_user_prompt(question, context_vehicle=context_vehicle, history=context.history),
        schema=QueryPlan,
        max_output_tokens=2048,
    )
    resolved = resolve(db, result.value, context)
    if resolved.brand_ids and resolved.structured is not None and not resolved.model_ids:
        resolved.structured.filters.model_ids = list(
            db.execute(select(VehicleModel.id).where(VehicleModel.brand_id.in_(resolved.brand_ids))).scalars()
        ) or [-1]
    resolved.usage = {"input_tokens": result.usage.input_tokens, "output_tokens": result.usage.output_tokens}
    logger.info(
        "assistant_plan intent=%s models=%s variants=%s keys=%s unresolved=%s dropped=%s",
        resolved.intent,
        resolved.model_ids,
        len(resolved.variant_ids),
        resolved.spec_keys,
        resolved.unresolved,
        resolved.dropped,
    )
    return resolved
