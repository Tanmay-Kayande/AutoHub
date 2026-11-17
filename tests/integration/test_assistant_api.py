"""Assistant HTTP API: ask, SSE stream, persistence, ownership, provider failures."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.api.v1.assistant import anonymous_questions, assistant_provider
from autohub.api.v1.search import query_embedder
from autohub.assistant.llm import FakeLLMProvider, LLMQuotaError
from autohub.core.settings import get_settings
from autohub.db.models import Message
from tests.factories import PASSWORD, make_brand, make_model, make_user, make_variant
from tests.integration.test_assistant import answer, plan


@pytest.fixture(autouse=True)
def _fresh_anonymous_allowance() -> Iterator[None]:
    anonymous_questions.reset()  # the test client always has the same address
    yield
    anonymous_questions.reset()


def setup(client: Any, db: Session, answers: list[Any]) -> tuple[FakeLLMProvider, Any]:
    model = make_model(db, make_brand(db, "Mahindra"), "XUV 3XO")
    make_variant(db, model, "AX7 Diesel AT", specs={"ventilated_seats": True})
    provider = FakeLLMProvider(answers)
    client.app.dependency_overrides[assistant_provider] = lambda: provider
    client.app.dependency_overrides[query_embedder] = lambda: None
    return provider, model


def login(client: Any, db: Session, email: str) -> dict[str, str]:
    make_user(db, email)
    token = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


GOOD = [
    plan(models=["XUV 3XO"], spec_keys=["ventilated_seats"]),
    answer(("verified_fact", "Yes, it has ventilated seats.", ["E2"])),
]


def test_ask_answers_persists_and_continues_conversation(client: Any, db: Session) -> None:
    provider, model = setup(client, db, [*GOOD, *GOOD])
    response = client.post(
        "/api/v1/assistant/ask", json={"question": "Ventilated seats?", "model_id": model.id}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "spec_lookup" and body["answer"]["segments"][0]["kind"] == "verified_fact"
    assert any(e["id"] == "E2" and e["kind"] == "brochure_fact" for e in body["evidence"])
    conversation_id = body["conversation_id"]

    again = client.post(
        "/api/v1/assistant/ask", json={"question": "And the AX7?", "conversation_id": conversation_id}
    )
    assert again.status_code == 200 and again.json()["conversation_id"] == conversation_id
    assert "user: Ventilated seats?" in provider.calls[2].prompt  # history reaches the planner
    roles = db.execute(select(Message.role).order_by(Message.id)).scalars().all()
    assert roles == ["user", "assistant", "user", "assistant"]


def test_stream_emits_events_in_order(client: Any, db: Session) -> None:
    _, model = setup(client, db, GOOD)
    with client.stream(
        "POST", "/api/v1/assistant/ask/stream", json={"question": "Ventilated seats?", "model_id": model.id}
    ) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    names = [line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("event: ")]
    assert names == ["plan", "evidence", "segment", "done"]
    done = json.loads(text.split("event: done\ndata: ", 1)[1].split("\n", 1)[0])
    assert done["conversation_id"] and done["message_id"]


def test_quota_error_is_a_503_and_nothing_is_saved(client: Any, db: Session) -> None:
    setup(client, db, [LLMQuotaError("daily quota")])
    response = client.post("/api/v1/assistant/ask", json={"question": "Ventilated seats?"})
    assert response.status_code == 503 and response.json()["error"]["code"] == "upstream_unavailable"
    assert db.execute(select(Message)).scalars().all() == []


def test_conversations_are_private(client: Any, db: Session) -> None:
    setup(client, db, GOOD)
    alice = login(client, db, "alice@example.com")
    bob = login(client, db, "bob@example.com")
    conversation_id = client.post(
        "/api/v1/assistant/ask", json={"question": "Ventilated seats?"}, headers=alice
    ).json()["conversation_id"]

    mine = client.get("/api/v1/assistant/conversations", headers=alice).json()
    assert [c["id"] for c in mine] == [conversation_id]
    detail = client.get(f"/api/v1/assistant/conversations/{conversation_id}", headers=alice).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]

    assert client.get("/api/v1/assistant/conversations", headers=bob).json() == []
    assert client.get(f"/api/v1/assistant/conversations/{conversation_id}", headers=bob).status_code == 404
    hijack = client.post(
        "/api/v1/assistant/ask",
        json={"question": "hi there", "conversation_id": conversation_id},
        headers=bob,
    )
    assert hijack.status_code == 404
    assert client.get("/api/v1/assistant/conversations").status_code == 401


def test_suggestions(client: Any, db: Session) -> None:
    _, model = setup(client, db, [])
    assert len(client.get("/api/v1/assistant/suggestions").json()) == 4
    assert "Mahindra XUV 3XO" in client.get(f"/api/v1/assistant/suggestions?model_id={model.id}").json()[0]
    assert client.get("/api/v1/assistant/suggestions?model_id=999999").status_code == 404


def test_question_validation(client: Any, db: Session) -> None:
    setup(client, db, [])
    assert client.post("/api/v1/assistant/ask", json={"question": "x"}).status_code == 422
    assert client.post("/api/v1/assistant/ask", json={"question": "y" * 1001}).status_code == 422


def ask(client: Any, headers: dict[str, str] | None = None) -> Any:
    return client.post("/api/v1/assistant/ask", json={"question": "Ventilated seats?"}, headers=headers or {})


def test_two_questions_without_login_then_login_is_required(client: Any, db: Session) -> None:
    provider, _ = setup(client, db, [*GOOD, *GOOD, *GOOD])
    assert ask(client).status_code == 200
    assert ask(client).status_code == 200
    blocked = ask(client)
    assert blocked.status_code == 401 and blocked.json()["error"]["code"] == "login_required"
    assert len(provider.calls) == 4  # the third question never reached the LLM
    with client.stream("POST", "/api/v1/assistant/ask/stream", json={"question": "Ventilated seats?"}) as r:
        assert r.status_code == 401  # a plain error, not an event stream

    signed_in = login(client, db, "carol@example.com")
    assert ask(client, signed_in).status_code == 200  # an account lifts the limit


def test_failed_question_does_not_use_up_the_allowance(client: Any, db: Session) -> None:
    setup(client, db, [LLMQuotaError("daily quota"), *GOOD, *GOOD])
    db.commit()  # the 503 path rolls back the request session; keep the test catalogue
    assert ask(client).status_code == 503
    assert ask(client).status_code == 200
    assert ask(client).status_code == 200
    assert ask(client).status_code == 401


def test_zero_allowance_requires_login(client: Any, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "assistant_anonymous_questions", 0)
    setup(client, db, GOOD)
    blocked = ask(client)
    assert blocked.status_code == 401 and "sign up" in blocked.json()["error"]["message"]
