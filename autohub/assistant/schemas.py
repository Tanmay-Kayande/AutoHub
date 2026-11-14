"""Contracts of the assistant: the LLM-facing plan and answer schemas, and evidence.

LLM-facing models have only required (nullable) fields and no defaults, which
the provider's JSON-schema subset handles reliably; code converts them to the
validated internal types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

Intent = Literal["spec_lookup", "ranking", "filter", "comparison", "descriptive", "general", "out_of_scope"]
FACTUAL_INTENTS: frozenset[str] = frozenset({"spec_lookup", "ranking", "filter", "comparison", "descriptive"})


class PlanSpecFilter(BaseModel):
    key: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "between", "is_true", "is_false", "contains"]
    value: float | None = Field(description="number in the spec's canonical unit, else null")
    value_max: float | None = Field(description="upper bound for 'between', else null")
    text: str | None = Field(description="text for 'contains'/'eq' on text specs, else null")


class PlanFilters(BaseModel):
    body_type: list[str]
    fuel_type: list[str]
    transmission_type: list[str]
    drivetrain: list[str]
    price_max: float | None


class QueryPlan(BaseModel):
    intent: Intent
    brands: list[str] = Field(description="brand names mentioned or implied")
    models: list[str] = Field(description="model names mentioned or implied")
    variants: list[str] = Field(description="variant/trim names mentioned")
    spec_keys: list[str] = Field(description="taxonomy keys the question is about")
    filters: PlanFilters
    spec_filters: list[PlanSpecFilter]
    sort_key: str | None = Field(description="numeric spec key or 'price' for rankings, else null")
    sort_direction: Literal["asc", "desc"] | None
    group_by_model: bool = Field(description="true when the user asks about models/cars rather than variants")
    semantic_query: str | None = Field(description="short search phrase for descriptive context, else null")


SegmentKind = Literal["verified_fact", "sourced_info", "explanation"]


class AnswerSegment(BaseModel):
    kind: SegmentKind
    text: str
    evidence_ids: list[str]


class AnswerOut(BaseModel):
    segments: list[AnswerSegment]
    missing: list[str] = Field(description="what the question asked that the evidence does not answer")


EvidenceKind = Literal["brochure_fact", "sourced", "catalogue", "note"]


@dataclass
class Evidence:
    id: str
    kind: EvidenceKind
    text: str
    source: dict[str, Any] = field(default_factory=dict)
    refs: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "source": self.source,
            "refs": self.refs,
            "confidence": self.confidence,
        }


@dataclass
class AssistantContext:
    model_id: int | None = None
    variant_ids: list[int] = field(default_factory=list)
    history: list[tuple[str, str]] = field(default_factory=list)  # (role, content), oldest first
