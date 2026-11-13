"""Per-intent retrieval into numbered, attributed evidence.

Facts come from SQL (``variant_specs``) with their provenance; descriptive
context comes from hybrid search. A spec the question asks about but the
database does not hold becomes an explicit ``note`` evidence item, so the
answer reports it as missing instead of guessing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.assistant.intent import ResolvedPlan
from autohub.assistant.schemas import Evidence, EvidenceKind
from autohub.db.models import (
    Brand,
    Brochure,
    BrochureVersion,
    ModelGeneration,
    SpecDefinition,
    Variant,
    VariantSpec,
    VehicleModel,
)
from autohub.search.embedder import EmbeddingProvider
from autohub.search.service import SearchScope, hybrid_search
from autohub.search.structured import run_structured
from autohub.specs.formatting import format_number, format_spec_value

logger = logging.getLogger(__name__)

MAX_EVIDENCE = 40
MAX_CHARS = 14000
MAX_VARIANTS = 8
CONTEXT_HITS = 6


def evidence_kind(source_type: str) -> EvidenceKind:
    return "brochure_fact" if source_type in ("official_brochure", "official_website") else "sourced"


@dataclass
class EvidenceBuilder:
    items: list[Evidence] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)
    _chars: int = 0

    def add(
        self,
        kind: EvidenceKind,
        text: str,
        *,
        source: dict[str, Any] | None = None,
        refs: dict[str, Any] | None = None,
        confidence: float | None = None,
    ) -> None:
        if text in self._seen or len(self.items) >= MAX_EVIDENCE or self._chars + len(text) > MAX_CHARS:
            return
        self._seen.add(text)
        self._chars += len(text)
        self.items.append(
            Evidence(f"E{len(self.items) + 1}", kind, text, source or {}, refs or {}, confidence)
        )


@dataclass
class VariantLabel:
    variant_id: int
    model_id: int
    label: str


def _labels(db: Session, variant_ids: Iterable[int]) -> dict[int, VariantLabel]:
    rows = db.execute(
        select(Variant.id, VehicleModel.id, Brand.name, VehicleModel.name, Variant.name)
        .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
        .join(VehicleModel, VehicleModel.id == ModelGeneration.model_id)
        .join(Brand, Brand.id == VehicleModel.brand_id)
        .where(Variant.id.in_(list(variant_ids)))
    ).all()
    return {r[0]: VariantLabel(r[0], r[1], f"{r[2]} {r[3]} {r[4]}") for r in rows}


def _model_variants(db: Session, model_ids: Sequence[int], limit: int = MAX_VARIANTS) -> list[int]:
    out: list[int] = []
    for model_id in model_ids:
        out.extend(
            db.execute(
                select(Variant.id)
                .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
                .where(
                    ModelGeneration.model_id == model_id,
                    ModelGeneration.is_current.is_(True),
                    Variant.is_active.is_(True),
                )
                .order_by(Variant.id)
                .limit(limit)
            ).scalars()
        )
    return out


def _source(db: Session, row: VariantSpec) -> dict[str, Any]:
    source: dict[str, Any] = {"type": row.source_type}
    if row.brochure_version_id is not None:
        found = db.execute(
            select(Brochure.id, Brochure.source_url, Brochure.title)
            .join(BrochureVersion, BrochureVersion.brochure_id == Brochure.id)
            .where(BrochureVersion.id == row.brochure_version_id)
        ).first()
        if found:
            source.update(
                brochure_id=found[0],
                url=found[1],
                brochure_title=found[2],
                brochure_version_id=row.brochure_version_id,
            )
        if row.source_page:
            source["page"] = row.source_page
    elif row.source_url:
        source["url"] = row.source_url
    return source


def add_spec_evidence(
    db: Session,
    builder: EvidenceBuilder,
    variant_ids: Sequence[int],
    keys: Sequence[str],
    *,
    note_missing: bool = True,
) -> None:
    if not variant_ids or not keys:
        return
    definitions = {
        d.key: d for d in db.execute(select(SpecDefinition).where(SpecDefinition.key.in_(keys))).scalars()
    }
    labels = _labels(db, variant_ids)
    rows = {
        (r.variant_id, r.spec_definition_id): r
        for r in db.execute(
            select(VariantSpec).where(
                VariantSpec.variant_id.in_(list(variant_ids)),
                VariantSpec.spec_definition_id.in_([d.id for d in definitions.values()]),
            )
        ).scalars()
    }
    for variant_id in variant_ids:
        label = labels.get(variant_id)
        if label is None:
            continue
        for key in keys:
            definition = definitions.get(key)
            if definition is None:
                continue
            row = rows.get((variant_id, definition.id))
            if row is None:
                if note_missing:
                    builder.add(
                        "note",
                        f"{definition.name} is not recorded for {label.label}.",
                        refs={"variant_id": variant_id, "spec_key": key},
                    )
                continue
            display = format_spec_value(
                data_type=definition.data_type,
                status=row.status,
                value_num=row.value_num,
                value_num_max=row.value_num_max,
                value_text=row.value_text,
                value_bool=row.value_bool,
                unit=row.unit,
            )
            builder.add(
                evidence_kind(row.source_type),
                f"{label.label} — {definition.name}: {display}",
                source=_source(db, row),
                refs={
                    "variant_id": variant_id,
                    "model_id": label.model_id,
                    "spec_key": key,
                    "variant_spec_id": row.id,
                },
                confidence=float(row.confidence) if row.confidence is not None else None,
            )


def _key_spec_keys(db: Session) -> list[str]:
    return list(
        db.execute(
            select(SpecDefinition.key)
            .where(SpecDefinition.is_key_spec.is_(True))
            .order_by(SpecDefinition.sort_order)
        ).scalars()
    )


def _catalogue_evidence(db: Session, builder: EvidenceBuilder, variant_ids: Sequence[int]) -> None:
    labels = _labels(db, variant_ids)
    for variant in db.execute(select(Variant).where(Variant.id.in_(list(variant_ids)))).scalars():
        label = labels[variant.id]
        parts = [
            p for p in (variant.fuel_type, variant.transmission_type, (variant.drivetrain or "").upper()) if p
        ]
        text = f"{label.label} — {', '.join(parts)}" if parts else label.label
        if variant.price_ex_showroom is not None:
            text += f"; ex-showroom {variant.price_currency or 'INR'} {format_number(variant.price_ex_showroom)} ({variant.price_source})"
        builder.add("catalogue", text, refs={"variant_id": variant.id, "model_id": label.model_id})


def _context_evidence(
    db: Session,
    builder: EvidenceBuilder,
    embedder: EmbeddingProvider | None,
    query: str,
    model_ids: Sequence[int],
) -> None:
    scope = SearchScope(model_ids=list(model_ids), entity_types=["brochure_page", "spec_group", "model"])
    try:
        hits, _ = hybrid_search(db, embedder, query, scope=scope, limit=CONTEXT_HITS)
    except Exception as exc:  # search is supporting context; the SQL facts still stand
        logger.warning("assistant_context_search_failed error=%s", exc)
        return
    for hit in hits:
        meta = hit.meta
        if hit.entity_type == "brochure_page":
            source = {
                "type": "official_brochure",
                "brochure_id": meta.get("brochure_id"),
                "brochure_version_id": meta.get("brochure_version_id"),
                "page": meta.get("page"),
                "url": meta.get("url"),
            }
            builder.add(
                "brochure_fact",
                hit.content[:1500],
                source=source,
                refs={"model_id": hit.model_id, "embedding_id": hit.id},
            )
        else:
            builder.add(
                "catalogue",
                hit.content[:1200],
                refs={"model_id": hit.model_id, "variant_id": hit.variant_id, "embedding_id": hit.id},
            )


def retrieve(
    db: Session, resolved: ResolvedPlan, question: str, embedder: EmbeddingProvider | None
) -> list[Evidence]:
    builder = EvidenceBuilder()
    intent = resolved.intent
    for name in resolved.unresolved:
        close = resolved.suggestions.get(name) or []
        hint = f" Close matches: {', '.join(close)}." if close else ""
        builder.add("note", f'"{name}" does not match any vehicle in AutoHub.{hint}')

    if intent in ("ranking", "filter") and resolved.structured is not None:
        result = run_structured(db, resolved.structured)
        keys = sorted(
            {f.key for f in resolved.structured.spec_filters}
            | (
                {resolved.structured.sort.key}
                if resolved.structured.sort and resolved.structured.sort.key != "price"
                else set()
            )
        )
        for position, row in enumerate(result.rows, start=1):
            values = "; ".join(f"{k}: {row.values[k]['display']}" for k in keys if k in row.values)
            price = (
                f"; ex-showroom INR {format_number(row.price_ex_showroom)}"
                if row.price_ex_showroom is not None
                else ""
            )
            sources = [row.values[k] for k in keys if k in row.values]
            kind: EvidenceKind = (
                "brochure_fact"
                if sources and all(evidence_kind(v["source_type"]) == "brochure_fact" for v in sources)
                else ("sourced" if sources else "catalogue")
            )
            source = {"type": sources[0]["source_type"], "page": sources[0]["source_page"]} if sources else {}
            builder.add(
                kind,
                f"#{position} {row.brand} {row.model} {row.variant} ({row.fuel_type or '?'}, {row.transmission_type or '?'}){': ' + values if values else ''}{price}",
                source=source,
                refs={"variant_id": row.variant_id, "model_id": row.model_id},
            )
        builder.add("catalogue", f"{result.total_matched} variants matched these conditions in AutoHub.")
        for key, missing in result.missing_value_counts.items():
            if missing:
                builder.add(
                    "note",
                    f"{missing} otherwise matching variants have no recorded {key} and were not ranked.",
                )
        return builder.items

    variant_ids = list(resolved.variant_ids) or _model_variants(db, resolved.model_ids)
    if intent == "spec_lookup":
        _catalogue_evidence(db, builder, variant_ids[:MAX_VARIANTS])
        add_spec_evidence(db, builder, variant_ids[:MAX_VARIANTS], resolved.spec_keys or _key_spec_keys(db))
    elif intent == "comparison":
        per_model = (
            [_model_variants(db, [m], 3) for m in resolved.model_ids]
            if not resolved.variant_ids
            else [resolved.variant_ids]
        )
        chosen = [v for group in per_model for v in group][:MAX_VARIANTS]
        _catalogue_evidence(db, builder, chosen)
        add_spec_evidence(db, builder, chosen, resolved.spec_keys or _key_spec_keys(db))
    elif intent == "descriptive":
        _catalogue_evidence(db, builder, variant_ids[:4])
        add_spec_evidence(
            db, builder, variant_ids[:4], resolved.spec_keys or _key_spec_keys(db), note_missing=False
        )
        _context_evidence(db, builder, embedder, resolved.plan.semantic_query or question, resolved.model_ids)
    elif intent == "general" and resolved.model_ids:
        _context_evidence(db, builder, embedder, resolved.plan.semantic_query or question, resolved.model_ids)
    return builder.items
