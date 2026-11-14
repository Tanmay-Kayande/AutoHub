"""Assistant orchestration: understand → retrieve → generate → verify.

The generator writes from numbered evidence only, and code re-checks its work:

- citations to evidence ids that were never supplied are removed;
- a ``verified_fact`` must cite brochure evidence, a ``sourced_info`` any real
  evidence; otherwise the segment is downgraded (and flagged);
- a number in an answer must appear in the evidence that segment cites; an
  explanation may not contain specification numbers at all. Offending segments
  are dropped, so a missing spec can never surface as a number.

When a factual question has no evidence, the model is not asked to improvise:
a fixed answer says the data is not in AutoHub.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from autohub.assistant.intent import ResolvedPlan, understand
from autohub.assistant.llm import LLMProvider
from autohub.assistant.prompts import ANSWER_RULES, answer_user_prompt
from autohub.assistant.retrieval import retrieve
from autohub.assistant.schemas import (
    FACTUAL_INTENTS,
    AnswerOut,
    AnswerSegment,
    AssistantContext,
    Evidence,
)
from autohub.search.embedder import EmbeddingProvider

logger = logging.getLogger(__name__)

# A standalone number: not part of a name like "AX7", "XUV 3XO" or "i20".
_NUMBER = re.compile(r"(?<![A-Za-z\d.])\d+(?:[.,]\d+)*(?![A-Za-z\d])")
OUT_OF_SCOPE_TEXT = (
    "I can help with questions about the cars in AutoHub's catalogue: specifications, features, "
    "comparisons and which models fit your needs."
)
NO_DATA_TEXT = "AutoHub's data does not contain the information needed to answer this."


@dataclass
class AssistantReply:
    intent: str
    answer: AnswerOut
    evidence: list[Evidence]
    flags: list[str] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    model: str | None = None


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def verify(answer: AnswerOut, evidence: list[Evidence]) -> tuple[AnswerOut, list[str]]:
    by_id = {e.id: e for e in evidence}
    flags: list[str] = []
    segments: list[AnswerSegment] = []
    for index, segment in enumerate(answer.segments):
        cited = [i for i in dict.fromkeys(segment.evidence_ids) if i in by_id and by_id[i].kind != "note"]
        fabricated = [i for i in segment.evidence_ids if i not in by_id]
        if fabricated:
            flags.append(f"segment {index}: removed unknown citation(s) {', '.join(fabricated)}")
        kind = segment.kind
        kinds = {by_id[i].kind for i in cited}
        if kind == "verified_fact" and "brochure_fact" not in kinds:
            new_kind = "sourced_info" if cited else "explanation"
            flags.append(f"segment {index}: verified_fact without brochure evidence -> {new_kind}")
            kind = new_kind  # type: ignore[assignment]
        if kind == "sourced_info" and not cited:
            flags.append(f"segment {index}: sourced_info without evidence -> explanation")
            kind = "explanation"
        if kind == "explanation":
            cited = []
        numbers = _numbers(segment.text)
        allowed = set().union(*(_numbers(by_id[i].text) for i in cited)) if cited else set()
        unsupported = numbers - allowed
        if unsupported:
            flags.append(f"segment {index}: dropped, number(s) {sorted(unsupported)} not in cited evidence")
            continue
        segments.append(AnswerSegment(kind=kind, text=segment.text.strip(), evidence_ids=cited))
    missing = [m for m in answer.missing if m.strip()]
    return AnswerOut(segments=segments, missing=missing), flags


def _fixed(intent: str, text: str, evidence: list[Evidence], resolved: ResolvedPlan | None) -> AssistantReply:
    missing = [e.text for e in evidence if e.kind == "note"]
    return AssistantReply(
        intent=intent,
        answer=AnswerOut(
            segments=[AnswerSegment(kind="explanation", text=text, evidence_ids=[])], missing=missing
        ),
        evidence=evidence,
        flags=["fixed_answer"],
        plan=resolved.plan.model_dump() if resolved else {},
        usage=dict(resolved.usage) if resolved else {},
    )


def ask(
    db: Session,
    question: str,
    *,
    context: AssistantContext,
    provider: LLMProvider,
    embedder: EmbeddingProvider | None,
) -> AssistantReply:
    started = time.monotonic()
    resolved = understand(db, provider, question, context)
    if resolved.intent == "out_of_scope":
        reply = _fixed("out_of_scope", OUT_OF_SCOPE_TEXT, [], resolved)
    else:
        evidence = retrieve(db, resolved, question, embedder)
        facts = [e for e in evidence if e.kind != "note"]
        if resolved.intent in FACTUAL_INTENTS and not facts:
            reply = _fixed(resolved.intent, NO_DATA_TEXT, evidence, resolved)
        else:
            lines = [f"[{e.id}] ({e.kind}) {e.text}" for e in evidence]
            result = provider.generate_structured(
                system=ANSWER_RULES,
                prompt=answer_user_prompt(question, lines),
                schema=AnswerOut,
                max_output_tokens=2048,
            )
            answer, flags = verify(result.value, evidence)
            if not answer.segments:
                answer = AnswerOut(
                    segments=[AnswerSegment(kind="explanation", text=NO_DATA_TEXT, evidence_ids=[])],
                    missing=answer.missing,
                )
                flags.append("all segments failed verification")
            usage = {
                "input_tokens": resolved.usage.get("input_tokens", 0) + result.usage.input_tokens,
                "output_tokens": resolved.usage.get("output_tokens", 0) + result.usage.output_tokens,
            }
            reply = AssistantReply(
                intent=resolved.intent,
                answer=answer,
                evidence=evidence,
                flags=flags,
                plan=resolved.plan.model_dump(),
                usage=usage,
            )
    reply.latency_ms = int((time.monotonic() - started) * 1000)
    reply.model = provider.model
    if resolved.unresolved:
        reply.flags.append(f"unresolved names: {', '.join(resolved.unresolved)}")
    if resolved.dropped:
        reply.flags.append(f"plan items dropped: {', '.join(resolved.dropped)}")
    logger.info(
        "assistant_answer intent=%s evidence=%s segments=%s flags=%s latency_ms=%s",
        reply.intent,
        len(reply.evidence),
        len(reply.answer.segments),
        len(reply.flags),
        reply.latency_ms,
    )
    return reply
