"""The evaluation runner builds items from the database and scores replies (fake LLM; no live calls)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from autohub.assistant.llm import FakeLLMProvider
from tests.factories import make_brand, make_model, make_variant
from tests.integration.test_assistant import answer, plan
from tools.run_rag_eval import EvalItem, build_items, report, score


def test_items_come_from_catalogue_and_scoring_works(db: Session) -> None:
    model = make_model(db, make_brand(db, "Mahindra"), "XUV 3XO")
    make_variant(db, model, "AX7", specs={"max_power": 96, "airbags": 6})
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


def test_set_covers_every_brand_and_class_and_is_repeatable(db: Session) -> None:
    for brand_name, model_name, power in (
        ("Mahindra", "Thar", 97),
        ("Hyundai", "Creta", 103),
        ("Tata", "Sierra", 103),
    ):
        model = make_model(db, make_brand(db, brand_name), model_name)
        make_variant(db, model, "Base", specs={"max_power": power, "length": 4000, "airbags": 6})

    items = build_items(db)
    again = build_items(db)

    assert [i.question for i in items] == [i.question for i in again]  # seeded
    ranking = next(
        i for i in items if i.question.startswith("Which car in the catalogue has the most powerful")
    )
    assert set(ranking.accepted) == {"Creta", "Sierra"}  # a tie: either model is correct
    lookups = [i for i in items if i.kind == "spec_lookup"]
    assert {q.split(" of the ")[1].split()[0] for q in (i.question for i in lookups)} == {
        "Mahindra",
        "Hyundai",
        "Tata",
    }
    kinds = {i.kind for i in items}
    assert kinds == {"ranking", "spec_lookup", "missing", "unknown_vehicle", "out_of_scope"}
    assert sum(i.kind == "missing" for i in items) == 5
    assert build_items(db, 3) == items[:3]


def test_refusals_do_not_count_toward_citation_validity() -> None:
    from tools.run_rag_eval import EvalResult

    refusal = EvalResult(EvalItem("out_of_scope", "poem", "out_of_scope", expect_abstain=True), correct=True)
    refusal.abstained = True
    refusal.intent = "out_of_scope"
    uncited = EvalResult(EvalItem("spec_lookup", "q", "spec_lookup", expect_value="6"), correct=True)
    uncited.citations_valid = False
    text = report([refusal, uncited], "fake-model")
    assert "| Citation validity (answers with facts) | 0/1 (0%) |" in text
    assert "| Abstention on unanswerable | 1/1 (100%) |" in text
    assert "| out_of_scope | 1 | 1/1 (100%) | 1/1 (100%) |" in text
