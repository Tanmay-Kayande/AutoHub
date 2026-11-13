"""The evaluation runner builds items from the database and scores replies (fake LLM; no live calls)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from autohub.assistant.llm import FakeLLMProvider
from tests.factories import make_brand, make_model, make_variant
from tests.integration.test_assistant import answer, plan
from tools.run_rag_eval import EvalItem, build_items, report, score


def test_items_come_from_catalogue_and_scoring_works(db: Session) -> None:
    model = make_model(db, make_brand(db, "Mahindra"), "XUV 3XO")
    make_variant(db, model, "AX7", specs={"ground_clearance": 201, "airbags": 6})
    items = build_items(db, 20)
    kinds = [i.kind for i in items]
    assert kinds[0] == "ranking" and items[0].expect_value == "XUV 3XO"
    assert {"spec_lookup", "missing", "unknown_vehicle", "out_of_scope"} <= set(kinds)

    item = EvalItem("spec_lookup", "Airbags of the Mahindra XUV 3XO AX7?", "spec_lookup", expect_value="6")
    provider = FakeLLMProvider([
        plan(models=["XUV 3XO"], variants=["AX7"], spec_keys=["airbags"]),
        answer(("verified_fact", "It has 6 airbags.", ["E2"])),
    ])  # fmt: skip
    result = score(db, item, provider, None)
    assert result.intent == "spec_lookup" and result.correct and result.citations_valid

    out = score(
        db,
        EvalItem("out_of_scope", "poem", "out_of_scope", expect_abstain=True),
        FakeLLMProvider([plan("out_of_scope")]),
        None,
    )
    assert out.correct and out.abstained
    text = report([result, out], "fake-model")
    assert "| Answer correctness | 2/2 (100%) |" in text
