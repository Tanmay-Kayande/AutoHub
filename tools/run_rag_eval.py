"""Measure the assistant against ground truth taken from the database at run time.

Classes (docs/V2/RAG_DESIGN.md §5): ranking, spec lookup, missing spec,
unknown vehicle, out of scope. Each item records the expected intent and the
checkable property; the runner scores plan accuracy, answer correctness,
citation validity, abstention, latency and tokens, and writes a Markdown report
with a breakdown per class.

The set (about 44 items) is built from the live catalogue:

- ranking: 5 superlatives; the top (or bottom) value comes from SQL and any model
  tied at that value counts as correct;
- spec lookup: each of ``LOOKUP_KEYS`` for each brand, on a random active variant
  that has the value from an official brochure;
- missing spec: variants without 0-100 km/h, top speed or NCAP data;
- unknown vehicle and out of scope: fixed questions.

Random picks use a seeded database RNG (``--seed``), so a run is repeatable
against the same data.

Uses the live LLM (2 calls per item; the free tier allows 15 requests a minute,
retries wait for it):

    python -m tools.run_rag_eval
    python -m tools.run_rag_eval --limit 10 --model gemini-2.5-flash-lite
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from autohub.assistant.llm import LLMError, LLMProvider
from autohub.assistant.schemas import AssistantContext
from autohub.assistant.service import ask
from autohub.core.settings import REPO_ROOT
from autohub.db.models import Brand, ModelGeneration, SpecDefinition, Variant, VariantSpec, VehicleModel
from autohub.search.embedder import EmbeddingProvider
from autohub.specs.formatting import format_number

LOOKUP_KEYS = (
    "airbags",
    "length",
    "width",
    "height",
    "wheelbase",
    "max_power",
    "max_torque",
    "fuel_tank_capacity",
)
RANKINGS = (
    ("max_power", "desc", "Which car in the catalogue has the most powerful engine?"),
    ("length", "desc", "Which is the longest car in the catalogue?"),
    ("length", "asc", "Which is the shortest car in the catalogue?"),
    ("wheelbase", "desc", "Which car in the catalogue has the longest wheelbase?"),
    ("fuel_tank_capacity", "desc", "Which car in the catalogue has the biggest fuel tank?"),
)
MISSING = (
    ("acceleration_0_100", "How fast does the {car} go from 0 to 100 km/h?"),
    ("top_speed", "What is the top speed of the {car}?"),
    ("ncap_rating", "What NCAP safety rating did the {car} get?"),
)
UNKNOWN_VEHICLES = (
    "What is the boot space of the Zorvex Q9 Hyperline?",
    "How many airbags does the Tata Nebula EV have?",
    "What is the mileage of the Hyundai Solara 2.0 Turbo?",
    "Compare the Mahindra XUV 900 with the Hyundai Venue.",
    "What is the ground clearance of the Kestrel Motors Ridgeback 4x4?",
)
OUT_OF_SCOPE = (
    "Write me a short poem about the monsoon.",
    "What is the share price of Tata Motors today?",
    "How do I cook paneer butter masala?",
    "Will it rain in Mumbai tomorrow?",
    "Write a Python function that reverses a string.",
)


@dataclass
class EvalItem:
    kind: str
    question: str
    expected_intent: str
    expect_value: str | None = None  # text that must appear in a fact segment
    expect_abstain: bool = False
    alternatives: tuple[str, ...] = ()  # other texts that also count (ties in a ranking)

    @property
    def accepted(self) -> tuple[str, ...]:
        return ((self.expect_value,) if self.expect_value else ()) + self.alternatives


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


def _active_specs(key: str) -> Select[tuple[str, str, str, Decimal | None]]:
    return (
        select(Brand.name, VehicleModel.name, Variant.name, VariantSpec.value_num)
        .join(SpecDefinition, SpecDefinition.id == VariantSpec.spec_definition_id)
        .join(Variant, Variant.id == VariantSpec.variant_id)
        .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
        .join(VehicleModel, VehicleModel.id == ModelGeneration.model_id)
        .join(Brand, Brand.id == VehicleModel.brand_id)
        .where(SpecDefinition.key == key, Variant.is_active.is_(True), VariantSpec.value_num.is_not(None))
    )


def _rankings(db: Session) -> list[EvalItem]:
    items: list[EvalItem] = []
    for key, direction, question in RANKINGS:
        rows = db.execute(_active_specs(key)).all()
        if not rows:
            continue
        values = [r[3] for r in rows if r[3] is not None]
        best = max(values) if direction == "desc" else min(values)
        models = sorted({r[1] for r in rows if r[3] == best})
        items.append(
            EvalItem("ranking", question, "ranking", expect_value=models[0], alternatives=tuple(models[1:]))
        )
    return items


def _lookups(db: Session) -> list[EvalItem]:
    items: list[EvalItem] = []
    brands = db.execute(select(Brand.id).order_by(Brand.name)).scalars().all()
    for key in LOOKUP_KEYS:
        name = db.execute(select(SpecDefinition.name).where(SpecDefinition.key == key)).scalar_one_or_none()
        if name is None:
            continue
        for brand_id in brands:
            found = db.execute(
                _active_specs(key)
                .where(Brand.id == brand_id, VariantSpec.source_type == "official_brochure")
                .order_by(func.random())
                .limit(1)
            ).first()
            if found and found[3] is not None:
                brand, model, variant, value = found
                items.append(
                    EvalItem(
                        "spec_lookup",
                        f"What is the {name.lower()} of the {brand} {model} {variant}?",
                        "spec_lookup",
                        expect_value=format_number(value),
                    )
                )
    return items


def _missing(db: Session) -> list[EvalItem]:
    items: list[EvalItem] = []
    brands = db.execute(select(Brand.id).order_by(Brand.name)).scalars().all()
    # 0-100 once per brand, then top speed and NCAP on any brand: five items.
    plan = [(MISSING[0], b) for b in brands] + [(MISSING[1], None), (MISSING[2], None)]
    for (key, template), brand_id in plan:
        has_value = (
            select(VariantSpec.id)
            .join(SpecDefinition, SpecDefinition.id == VariantSpec.spec_definition_id)
            .where(VariantSpec.variant_id == Variant.id, SpecDefinition.key == key)
            .exists()
        )
        stmt = (
            select(Brand.name, VehicleModel.name, Variant.name)
            .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
            .join(VehicleModel, VehicleModel.id == ModelGeneration.model_id)
            .join(Brand, Brand.id == VehicleModel.brand_id)
            .where(Variant.is_active.is_(True), ~has_value)
            .order_by(func.random())
            .limit(1)
        )
        if brand_id is not None:
            stmt = stmt.where(Brand.id == brand_id)
        found = db.execute(stmt).first()
        if found:
            car = " ".join(found)
            items.append(EvalItem("missing", template.format(car=car), "spec_lookup", expect_abstain=True))
    return items


def build_items(db: Session, limit: int | None = None, *, seed: float = 0.2026) -> list[EvalItem]:
    db.execute(select(func.setseed(seed)))  # repeatable random picks on the same data
    items = _rankings(db) + _lookups(db) + _missing(db)
    items += [EvalItem("unknown_vehicle", q, "spec_lookup", expect_abstain=True) for q in UNKNOWN_VEHICLES]
    items += [EvalItem("out_of_scope", q, "out_of_scope", expect_abstain=True) for q in OUT_OF_SCOPE]
    return items if limit is None else items[:limit]


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
    elif item.accepted:
        result.correct = any(v.lower() in s.text.lower() for s in facts for v in item.accepted)
    return result


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def report(results: Sequence[EvalResult], model: str) -> str:
    ran = [r for r in results if r.error is None]
    answered = [r for r in ran if not r.abstained]
    unanswerable = [r for r in ran if r.item.expect_abstain]
    answerable = [r for r in ran if not r.item.expect_abstain]
    latencies = sorted(r.latency_ms for r in ran)
    p90 = latencies[min(len(latencies) - 1, int(0.9 * len(latencies)))] if latencies else 0
    lines = [
        f"# RAG evaluation — {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        f"Model: `{model}` · items: {len(results)} · errors: {len(results) - len(ran)}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Plan intent accuracy | {_pct(sum(r.intent == r.item.expected_intent for r in ran), len(ran))} |",
        f"| Answer correctness | {_pct(sum(r.correct for r in ran), len(ran))} |",
        f"| Correct on answerable questions | {_pct(sum(r.correct for r in answerable), len(answerable))} |",
        f"| Citation validity (answers with facts) | {_pct(sum(r.citations_valid for r in answered), len(answered))} |",
        f"| Abstention on unanswerable | {_pct(sum(r.correct for r in unanswerable), len(unanswerable))} |",
        f"| Median / p90 latency | {statistics.median(latencies) if latencies else 0:.0f} / {p90} ms |",
        f"| Mean tokens per question | {statistics.mean([r.tokens for r in ran]) if ran else 0:.0f} |",
        "",
        "Citation validity counts only answers that state facts; a refusal has nothing to cite.",
        "",
        "## By class",
        "",
        "| Class | Items | Correct | Intent as expected |",
        "|---|---|---|---|",
    ]
    for kind in ("ranking", "spec_lookup", "missing", "unknown_vehicle", "out_of_scope"):
        group = [r for r in ran if r.item.kind == kind]
        if group:
            lines.append(
                f"| {kind} | {len(group)} | {_pct(sum(r.correct for r in group), len(group))} | "
                f"{_pct(sum(r.intent == r.item.expected_intent for r in group), len(group))} |"
            )
    lines += ["", "## Items", ""]
    for r in results:
        status = "ERROR" if r.error else ("PASS" if r.correct and r.citations_valid else "FAIL")
        lines.append(f"### {status} · {r.item.kind} · {r.item.question}")
        expected = " / ".join(r.item.accepted) or "—"
        lines.append(
            f"- expected intent `{r.item.expected_intent}`, got `{r.intent}`; expected value: {expected}"
        )
        if r.error:
            lines.append(f"- error: {r.error}")
        lines.extend(f"- {a}" for a in r.answer)
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, help="run only the first N items")
    parser.add_argument("--seed", type=float, default=0.2026, help="database RNG seed in [-1, 1]")
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
        items = build_items(db, args.limit, seed=args.seed)
        results = []
        for number, item in enumerate(items, 1):
            results.append(score(db, item, provider, embedder))
            print(
                f"[{number}/{len(items)}] {item.kind}: {'ok' if results[-1].correct else 'miss'}", flush=True
            )
    finally:
        db.close()
    text = report(results, provider.model)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"rag_eval_{datetime.now(UTC):%Y%m%dT%H%M%S}.md"
    path.write_text(text, encoding="utf-8")
    (args.out / (path.stem + ".json")).write_text(
        json.dumps(
            [{**r.__dict__, "item": {**r.item.__dict__, "accepted": r.item.accepted}} for r in results],
            indent=2,
        ),
        encoding="utf-8",
    )
    print(text.split("## Items")[0])
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
