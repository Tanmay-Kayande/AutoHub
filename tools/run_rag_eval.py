"""Measure the assistant against ground truth taken from the database at run time.

Classes (docs/V2/RAG_DESIGN.md §5): ranking, spec lookup, missing spec,
unknown vehicle, out of scope. Each item records the expected intent and the
checkable property; the runner scores plan accuracy, answer correctness,
citation validity, abstention, latency and tokens, and writes a Markdown report.

Uses the live LLM (2 calls per item):

    python -m tools.run_rag_eval --limit 10
    python -m tools.run_rag_eval --limit 10 --model gemini-2.5-flash-lite
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autohub.assistant.llm import LLMError, LLMProvider
from autohub.assistant.schemas import AssistantContext
from autohub.assistant.service import ask
from autohub.core.settings import REPO_ROOT
from autohub.db.models import Brand, ModelGeneration, SpecDefinition, Variant, VariantSpec, VehicleModel
from autohub.search.embedder import EmbeddingProvider
from autohub.search.structured import StructuredQuery, run_structured
from autohub.specs.formatting import format_number

LOOKUP_KEYS = ("airbags", "length", "ground_clearance", "max_power", "seating_capacity")


@dataclass
class EvalItem:
    kind: str
    question: str
    expected_intent: str
    expect_value: str | None = None  # text that must appear in a fact segment
    expect_abstain: bool = False


@dataclass
class EvalResult:
    item: EvalItem
    intent: str | None = None
    correct: bool = False
    citations_valid: bool = True
    abstained: bool = False
    latency_ms: int = 0
    tokens: int = 0
    error: str | None = None
    answer: list[str] = field(default_factory=list)


def build_items(db: Session, limit: int) -> list[EvalItem]:
    items: list[EvalItem] = []
    top = run_structured(
        db,
        StructuredQuery.model_validate(
            {
                "filters": {"body_type": ["suv"]},
                "sort": {"key": "ground_clearance"},
                "group_by": "model",
                "limit": 1,
            }
        ),
    )
    if top.rows:
        row = top.rows[0]
        items.append(
            EvalItem(
                "ranking", "Which SUV has the highest ground clearance?", "ranking", expect_value=row.model
            )
        )

    for key in LOOKUP_KEYS:
        found = db.execute(
            select(Brand.name, VehicleModel.name, Variant.name, VariantSpec.value_num, SpecDefinition.name)
            .join(SpecDefinition, SpecDefinition.id == VariantSpec.spec_definition_id)
            .join(Variant, Variant.id == VariantSpec.variant_id)
            .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
            .join(VehicleModel, VehicleModel.id == ModelGeneration.model_id)
            .join(Brand, Brand.id == VehicleModel.brand_id)
            .where(
                SpecDefinition.key == key,
                VariantSpec.source_type == "official_brochure",
                VariantSpec.value_num.is_not(None),
            )
            .order_by(func.random())
            .limit(1)
        ).first()
        if found:
            brand, model, variant, value, name = found
            items.append(
                EvalItem(
                    "spec_lookup",
                    f"What is the {name.lower()} of the {brand} {model} {variant}?",
                    "spec_lookup",
                    expect_value=format_number(value),
                )
            )

    lacking = db.execute(
        select(Brand.name, VehicleModel.name, Variant.name)
        .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
        .join(VehicleModel, VehicleModel.id == ModelGeneration.model_id)
        .join(Brand, Brand.id == VehicleModel.brand_id)
        .where(
            ~select(VariantSpec.id)
            .join(SpecDefinition, SpecDefinition.id == VariantSpec.spec_definition_id)
            .where(VariantSpec.variant_id == Variant.id, SpecDefinition.key == "acceleration_0_100")
            .exists()
        )
        .limit(1)
    ).first()
    if lacking:
        items.append(
            EvalItem(
                "missing",
                f"How fast does the {lacking[0]} {lacking[1]} {lacking[2]} go from 0 to 100 km/h?",
                "spec_lookup",
                expect_abstain=True,
            )
        )
    items.append(
        EvalItem(
            "unknown_vehicle",
            "What is the boot space of the Zorvex Q9 Hyperline?",
            "spec_lookup",
            expect_abstain=True,
        )
    )
    items.append(
        EvalItem(
            "out_of_scope", "Write me a short poem about the monsoon.", "out_of_scope", expect_abstain=True
        )
    )
    return items[:limit]


def score(
    db: Session, item: EvalItem, provider: LLMProvider, embedder: EmbeddingProvider | None
) -> EvalResult:
    result = EvalResult(item)
    try:
        reply = ask(db, item.question, context=AssistantContext(), provider=provider, embedder=embedder)
    except LLMError as exc:
        result.error = f"{type(exc).__name__}: {exc}"[:300]
        return result
    db.rollback()
    result.intent = reply.intent
    result.latency_ms = reply.latency_ms
    result.tokens = sum(reply.usage.values())
    result.answer = [f"[{s.kind}] {s.text}" for s in reply.answer.segments]
    by_id = {e.id: e for e in reply.evidence}
    facts = [s for s in reply.answer.segments if s.kind != "explanation"]
    # Citation validity: every fact segment cites at least one supplied evidence item.
    result.citations_valid = all(s.evidence_ids and all(i in by_id for i in s.evidence_ids) for s in facts)
    result.abstained = not facts
    if item.expect_abstain:
        result.correct = result.abstained or bool(reply.answer.missing)
    elif item.expect_value is not None:
        result.correct = any(item.expect_value.lower() in s.text.lower() for s in facts)
    return result


def report(results: list[EvalResult], model: str) -> str:
    def pct(n: int, d: int) -> str:
        return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"

    ran = [r for r in results if r.error is None]
    lines = [
        f"# RAG evaluation — {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        f"Model: `{model}` · items: {len(results)} · errors: {len(results) - len(ran)}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Plan intent accuracy | {pct(sum(r.intent == r.item.expected_intent for r in ran), len(ran))} |",
        f"| Answer correctness | {pct(sum(r.correct for r in ran), len(ran))} |",
        f"| Citation validity | {pct(sum(r.citations_valid for r in ran), len(ran))} |",
        f"| Abstention on unanswerable | {pct(sum(r.abstained or r.correct for r in ran if r.item.expect_abstain), sum(r.item.expect_abstain for r in ran))} |",
        f"| Median latency | {statistics.median([r.latency_ms for r in ran]) if ran else 0} ms |",
        f"| Mean tokens per question | {statistics.mean([r.tokens for r in ran]) if ran else 0:.0f} |",
        "",
        "## Items",
        "",
    ]
    for r in results:
        status = "ERROR" if r.error else ("PASS" if r.correct and r.citations_valid else "FAIL")
        lines.append(f"### {status} · {r.item.kind} · {r.item.question}")
        lines.append(
            f"- expected intent `{r.item.expected_intent}`, got `{r.intent}`; expected value: {r.item.expect_value or '—'}"
        )
        if r.error:
            lines.append(f"- error: {r.error}")
        lines.extend(f"- {a}" for a in r.answer)
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--model", help="LLM model override (default LLM_MODEL)")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "V2" / "reports")
    args = parser.parse_args(argv)

    from autohub.assistant.llm import get_llm_provider
    from autohub.db.session import get_session_factory
    from autohub.search.embedder import get_embedder

    provider = get_llm_provider(model=args.model)
    try:
        embedder: EmbeddingProvider | None = get_embedder()
    except Exception:
        embedder = None
    db = get_session_factory()()
    try:
        items = build_items(db, args.limit)
        results = [score(db, item, provider, embedder) for item in items]
    finally:
        db.close()
    text = report(results, provider.model)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"rag_eval_{datetime.now(UTC):%Y%m%dT%H%M%S}.md"
    path.write_text(text, encoding="utf-8")
    (args.out / (path.stem + ".json")).write_text(
        json.dumps([{**r.__dict__, "item": r.item.__dict__} for r in results], indent=2), encoding="utf-8"
    )
    print(text.split("## Items")[0])
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
