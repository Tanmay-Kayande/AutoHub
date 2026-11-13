"""Assistant endpoints: ask (JSON and SSE), suggestions, conversation history."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from autohub.api.schemas import AskIn, AskOut, ConversationDetail, ConversationOut
from autohub.api.v1.search import query_embedder
from autohub.assistant.llm import LLMError, LLMProvider, LLMQuotaError, get_llm_provider
from autohub.assistant.schemas import AssistantContext
from autohub.assistant.service import AssistantReply, ask
from autohub.auth.deps import current_user, optional_user
from autohub.core.errors import NotFoundError, UpstreamError, UpstreamUnavailableError
from autohub.core.ratelimit import rate_limit
from autohub.db.models import Brand, Conversation, Message, User, VehicleModel
from autohub.db.session import get_db
from autohub.search.embedder import EmbeddingProvider

router = APIRouter(prefix="/assistant", tags=["assistant"])
_limit = Depends(rate_limit("assistant", limit=20))
HISTORY_MESSAGES = 6


def assistant_provider() -> LLMProvider:
    try:
        return get_llm_provider()
    except Exception as exc:
        raise UpstreamUnavailableError("The assistant is not configured.") from exc


def _conversation(
    db: Session, conversation_id: uuid.UUID | None, user: User | None, data: AskIn
) -> Conversation:
    if conversation_id is not None:
        conversation = db.get(Conversation, conversation_id)
        # Someone else's conversation is reported as missing, never shown.
        if conversation is None or (
            conversation.user_id is not None and (user is None or conversation.user_id != user.id)
        ):
            raise NotFoundError("Conversation not found.")
        return conversation
    conversation = Conversation(
        user_id=user.id if user else None,
        context_model_id=data.model_id,
        context_variant_ids=data.variant_ids or None,
        title=data.question[:80],
    )
    db.add(conversation)
    db.flush()
    return conversation


def _history(db: Session, conversation: Conversation) -> list[tuple[str, str]]:
    rows = db.execute(
        select(Message.role, Message.content)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.id.desc())
        .limit(HISTORY_MESSAGES)
    ).all()
    return [(r[0], r[1]) for r in reversed(rows)]


def _run(
    db: Session, data: AskIn, user: User | None, provider: LLMProvider, embedder: EmbeddingProvider | None
) -> tuple[Conversation, Message, AssistantReply]:
    conversation = _conversation(db, data.conversation_id, user, data)
    context = AssistantContext(
        model_id=data.model_id if data.model_id is not None else conversation.context_model_id,
        variant_ids=data.variant_ids or list(conversation.context_variant_ids or []),
        history=_history(db, conversation),
    )
    try:
        reply = ask(db, data.question, context=context, provider=provider, embedder=embedder)
    except LLMQuotaError as exc:
        db.rollback()
        raise UpstreamUnavailableError(
            "The assistant has reached its usage limit. Please try again later."
        ) from exc
    except LLMError as exc:
        db.rollback()
        raise UpstreamError("The assistant could not answer right now. Please try again.") from exc
    db.add(Message(conversation_id=conversation.id, role="user", content=data.question))
    answer = reply.answer.model_dump()
    message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=" ".join(s.text for s in reply.answer.segments),
        intent=reply.intent,
        answer={**answer, "flags": reply.flags},
        evidence=[e.as_dict() for e in reply.evidence],
        llm_model=reply.model,
        latency_ms=reply.latency_ms,
        input_tokens=reply.usage.get("input_tokens"),
        output_tokens=reply.usage.get("output_tokens"),
    )
    db.add(message)
    db.commit()
    return conversation, message, reply


def _out(conversation: Conversation, message: Message, reply: AssistantReply) -> AskOut:
    return AskOut.model_validate(
        {
            "conversation_id": conversation.id,
            "message_id": message.id,
            "intent": reply.intent,
            "answer": reply.answer.model_dump(),
            "evidence": [e.as_dict() for e in reply.evidence],
            "flags": reply.flags,
            "plan": reply.plan,
            "usage": reply.usage,
            "latency_ms": reply.latency_ms,
        }
    )


@router.post("/ask", response_model=AskOut, dependencies=[_limit])
def ask_question(
    data: AskIn,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
    provider: LLMProvider = Depends(assistant_provider),
    embedder: EmbeddingProvider | None = Depends(query_embedder),
) -> AskOut:
    return _out(*_run(db, data, user, provider, embedder))


def _event(name: str, payload: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n"


@router.post("/ask/stream", dependencies=[_limit])
def ask_stream(
    data: AskIn,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
    provider: LLMProvider = Depends(assistant_provider),
    embedder: EmbeddingProvider | None = Depends(query_embedder),
) -> StreamingResponse:
    """Server-Sent Events: ``plan``, ``evidence``, one ``segment`` per answer part, then ``done`` or ``error``."""

    def events() -> Iterator[str]:
        try:
            out = _out(*_run(db, data, user, provider, embedder))
        except (UpstreamError, UpstreamUnavailableError, NotFoundError) as exc:
            yield _event("error", {"code": exc.code, "message": exc.message})
            return
        yield _event("plan", {"intent": out.intent, "plan": out.plan})
        yield _event("evidence", [e.model_dump() for e in out.evidence])
        for segment in out.answer.segments:
            yield _event("segment", segment.model_dump())
        yield _event(
            "done",
            {
                "conversation_id": str(out.conversation_id),
                "message_id": out.message_id,
                "missing": out.answer.missing,
                "flags": out.flags,
                "latency_ms": out.latency_ms,
            },
        )

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


GENERIC_SUGGESTIONS = [
    "Which SUV has the highest ground clearance?",
    "Show petrol automatic SUVs with 6 airbags",
    "Which cars have ventilated seats?",
    "Compare the dimensions of two models",
]


@router.get("/suggestions", response_model=list[str])
def suggestions(model_id: int | None = Query(None), db: Session = Depends(get_db)) -> list[str]:
    if model_id is None:
        return GENERIC_SUGGESTIONS
    row = db.execute(
        select(Brand.name, VehicleModel.name)
        .join(Brand, Brand.id == VehicleModel.brand_id)
        .where(VehicleModel.id == model_id)
    ).first()
    if row is None:
        raise NotFoundError(f"Model {model_id} was not found.")
    name = f"{row[0]} {row[1]}"
    return [
        f"Which {name} variants have a sunroof?",
        f"What is the ground clearance of the {name}?",
        f"Is the {name} good for long highway drives?",
        f"How many airbags does the {name} have?",
    ]


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[Conversation]:
    return list(
        db.execute(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.updated_at.desc())
            .limit(50)
        ).scalars()
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Conversation:
    conversation = db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .options(selectinload(Conversation.messages))
    ).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("Conversation not found.")
    return conversation
