"""Assistant: plan resolution, evidence, grounded answers and citation enforcement (fake LLM)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from autohub.assistant.intent import resolve
from autohub.assistant.llm import FakeLLMProvider
from autohub.assistant.retrieval import retrieve
from autohub.assistant.schemas import AnswerOut, AnswerSegment, AssistantContext, Evidence, QueryPlan
from autohub.assistant.service import NO_DATA_TEXT, OUT_OF_SCOPE_TEXT, ask, verify
from tests.factories import make_brand, make_model, make_variant


def plan(intent: str = "spec_lookup", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent": intent,
        "brands": [],
        "models": [],
        "variants": [],
        "spec_keys": [],
        "filters": {
            "body_type": [],
            "fuel_type": [],
            "transmission_type": [],
            "drivetrain": [],
            "price_max": None,
        },
        "spec_filters": [],
        "sort_key": None,
        "sort_direction": None,
        "group_by_model": False,
        "semantic_query": None,
    }
    base.update(kw)
    return base


def answer(*segments: tuple[str, str, list[str]], missing: list[str] | None = None) -> dict[str, Any]:
    return {
        "segments": [{"kind": k, "text": t, "evidence_ids": e} for k, t, e in segments],
        "missing": missing or [],
    }


@pytest.fixture()
def cat(db: Session) -> dict[str, Any]:
    mahi = make_brand(db, "Mahindra")
    hy = make_brand(db, "Hyundai")
    xuv = make_model(db, mahi, "XUV 3XO")
    creta = make_model(db, hy, "Creta")
    return {
        "mahi": mahi,
        "xuv": xuv,
        "creta": creta,
        "ax5": make_variant(db, xuv, "AX5 Diesel MT", fuel_type="diesel",
                            specs={"ground_clearance": 201, "ventilated_seats": False, "airbags": 6}),
        "ax7": make_variant(db, xuv, "AX7 Diesel AT", fuel_type="diesel", transmission_type="automatic",
                            specs={"ground_clearance": 201, "ventilated_seats": True, "airbags": 6}),
        "sx": make_variant(db, creta, "SX(O) Petrol AT", transmission_type="automatic",
                           specs={"ground_clearance": 190, "ventilated_seats": True, "airbags": 6}, source_type="legacy_v1"),
    }  # fmt: skip


def q(d: dict[str, Any]) -> QueryPlan:
    return QueryPlan.model_validate(d)


# ---------------------------------------------------------------- understanding
def test_names_resolve_by_spacing_and_typos(db: Session, cat: dict[str, Any]) -> None:
    r = resolve(db, q(plan(models=["XUV3XO", "Cretaa"], variants=["AX7 diesel"])), AssistantContext())
    assert r.model_ids == [cat["xuv"].id, cat["creta"].id]
    assert r.variant_ids == [cat["ax7"].id]


def test_unknown_names_and_keys_are_reported(db: Session, cat: dict[str, Any]) -> None:
    r = resolve(
        db,
        q(plan(models=["Nonexistent Zeta"], spec_keys=["ventilated_seats", "flux_capacitor"])),
        AssistantContext(),
    )
    assert r.unresolved == ["Nonexistent Zeta"] and r.spec_keys == ["ventilated_seats"]
    assert r.dropped == ["spec key flux_capacitor"]


def test_page_context_scopes_this_car_but_not_rankings(db: Session, cat: dict[str, Any]) -> None:
    ctx = AssistantContext(model_id=cat["creta"].id)
    assert resolve(db, q(plan()), ctx).model_ids == [cat["creta"].id]
    ranking = resolve(db, q(plan("ranking", sort_key="ground_clearance", sort_direction="desc")), ctx)
    assert ranking.model_ids == [] and ranking.structured is not None


def test_invalid_plan_filters_are_dropped_not_executed(db: Session, cat: dict[str, Any]) -> None:
    r = resolve(
        db,
        q(plan("filter", spec_filters=[{"key": "sunroof", "op": "gt", "value": 3, "value_max": None, "text": None},
                                       {"key": "airbags", "op": "gte", "value": 6, "value_max": None, "text": None}],
               filters={"body_type": ["spaceship"], "fuel_type": ["diesel"], "transmission_type": [], "drivetrain": [], "price_max": None})),
        AssistantContext(),
    )  # fmt: skip
    assert r.structured is not None
    assert [f.key for f in r.structured.spec_filters] == ["airbags"] and r.structured.filters.fuel_type == [
        "diesel"
    ]
    assert any("sunroof" in d for d in r.dropped) and any("body_type" in d for d in r.dropped)


# ---------------------------------------------------------------- retrieval
def test_spec_lookup_evidence_has_provenance_and_missing_notes(db: Session, cat: dict[str, Any]) -> None:
    r = resolve(
        db, q(plan(models=["XUV 3XO"], spec_keys=["ventilated_seats", "boot_capacity"])), AssistantContext()
    )
    evidence = retrieve(db, r, "q", None)
    texts = [e.text for e in evidence]
    assert "Mahindra XUV 3XO AX7 Diesel AT — Ventilated seats: Yes" in texts
    assert "Boot capacity is not recorded for Mahindra XUV 3XO AX5 Diesel MT." in texts
    fact = next(e for e in evidence if e.text.endswith("Ventilated seats: Yes"))
    assert (
        fact.kind == "brochure_fact"
        and fact.refs["spec_key"] == "ventilated_seats"
        and fact.refs["variant_spec_id"]
    )
    assert [e.id for e in evidence] == [f"E{i}" for i in range(1, len(evidence) + 1)]


def test_ranking_evidence_comes_from_sql_with_coverage(db: Session, cat: dict[str, Any]) -> None:
    make_variant(db, cat["creta"], "E Petrol MT")  # no ground clearance recorded
    r = resolve(
        db,
        q(plan("ranking", sort_key="ground_clearance", sort_direction="desc", group_by_model=True)),
        AssistantContext(),
    )
    evidence = retrieve(db, r, "q", None)
    assert (
        evidence[0].text.startswith("#1 Mahindra XUV 3XO") and "ground_clearance: 201 mm" in evidence[0].text
    )
    assert any(
        e.kind == "note" and "1 otherwise matching variants have no recorded ground_clearance" in e.text
        for e in evidence
    )
    sourced = next(e for e in evidence if "Creta" in e.text)
    assert sourced.kind == "sourced"  # legacy V1 data is not a brochure fact


# ---------------------------------------------------------------- generation and verification
EVIDENCE = [
    Evidence("E1", "brochure_fact", "XUV 3XO AX7 — Ground clearance: 201 mm"),
    Evidence("E2", "sourced", "Creta SX(O) — Ground clearance: 190 mm"),
    Evidence("E3", "note", "Boot capacity is not recorded for XUV 3XO AX7."),
]


def test_fabricated_citation_is_removed_and_segment_downgraded() -> None:
    out, flags = verify(
        AnswerOut(
            segments=[
                AnswerSegment(
                    kind="verified_fact", text="The Creta SX(O) has 190 mm.", evidence_ids=["E2", "E9"]
                )
            ],
            missing=[],
        ),
        EVIDENCE,
    )
    assert out.segments[0].kind == "sourced_info" and out.segments[0].evidence_ids == ["E2"]
    assert any("E9" in f for f in flags) and any("-> sourced_info" in f for f in flags)


def test_numbers_must_come_from_cited_evidence() -> None:
    out, flags = verify(
        AnswerOut(
            segments=[
                AnswerSegment(
                    kind="verified_fact", text="The AX7 has 201 mm of ground clearance.", evidence_ids=["E1"]
                ),
                AnswerSegment(kind="verified_fact", text="Its boot holds 364 litres.", evidence_ids=["E1"]),
                AnswerSegment(
                    kind="explanation",
                    text="The XUV 3XO and AX7 suit rough roads with 200+ mm.",
                    evidence_ids=[],
                ),
                AnswerSegment(
                    kind="explanation", text="Higher clearance helps on speed breakers.", evidence_ids=["E3"]
                ),
            ],
            missing=["Boot capacity is not recorded."],
        ),
        EVIDENCE,
    )
    assert [s.text for s in out.segments] == [
        "The AX7 has 201 mm of ground clearance.",
        "Higher clearance helps on speed breakers.",
    ]
    assert out.segments[1].evidence_ids == []
    assert sum("dropped" in f for f in flags) == 2


def test_full_flow_spec_lookup(db: Session, cat: dict[str, Any]) -> None:
    provider = FakeLLMProvider([
        plan(models=["XUV 3XO"], variants=["AX7"], spec_keys=["ventilated_seats"]),
        answer(("verified_fact", "Yes, the AX7 Diesel AT has ventilated seats.", ["E2"])),
    ])  # fmt: skip
    reply = ask(
        db,
        "Does the XUV 3XO AX7 have ventilated seats?",
        context=AssistantContext(),
        provider=provider,
        embedder=None,
    )
    assert reply.intent == "spec_lookup" and reply.flags == []
    assert reply.answer.segments[0].kind == "verified_fact"
    cited = next(e for e in reply.evidence if e.id == "E2")
    assert cited.text.endswith("Ventilated seats: Yes")
    assert "Evidence:" in provider.calls[1].prompt and "[E2] (brochure_fact)" in provider.calls[1].prompt


def test_out_of_scope_and_no_data_use_fixed_answers_without_generation(
    db: Session, cat: dict[str, Any]
) -> None:
    provider = FakeLLMProvider([plan("out_of_scope")])
    reply = ask(db, "Write me a poem", context=AssistantContext(), provider=provider, embedder=None)
    assert reply.answer.segments[0].text == OUT_OF_SCOPE_TEXT and len(provider.calls) == 1

    provider = FakeLLMProvider([plan(models=["Zeta 9000"], spec_keys=["boot_capacity"])])
    reply = ask(
        db, "Boot space of the Zeta 9000?", context=AssistantContext(), provider=provider, embedder=None
    )
    assert reply.answer.segments[0].text == NO_DATA_TEXT and len(provider.calls) == 1
    assert any("Zeta 9000" in m for m in reply.answer.missing)


def test_missing_spec_never_yields_a_number(db: Session, cat: dict[str, Any]) -> None:
    provider = FakeLLMProvider([
        plan(models=["XUV 3XO"], variants=["AX7"], spec_keys=["boot_capacity"]),
        answer(("verified_fact", "The AX7 has 364 litres of boot space.", ["E1"]), missing=[]),
    ])  # fmt: skip
    reply = ask(db, "Boot space of the AX7?", context=AssistantContext(), provider=provider, embedder=None)
    assert all("364" not in s.text for s in reply.answer.segments)
    assert any("not in cited evidence" in f for f in reply.flags)
