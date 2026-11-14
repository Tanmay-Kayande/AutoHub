"""Prompts for the two assistant LLM calls: understanding (plan) and answering."""

from __future__ import annotations

from collections.abc import Sequence

from autohub.ingestion.extraction.prompt import spec_catalogue

PLAN_RULES = """\
You turn a car buyer's question into a query plan for AutoHub, a vehicle catalogue. You never answer the question.

Intents:
- spec_lookup: a specific value or feature of named/current vehicle(s) ("does it have ventilated seats?")
- ranking: best/highest/lowest by a measurable spec or price ("highest ground clearance SUV")
- filter: list vehicles matching conditions ("petrol automatic SUVs with 6 airbags")
- comparison: compare two or more named vehicles
- descriptive: suitability, character, what the brochure says ("good for long drives?")
- general: general automotive knowledge not tied to catalogue data ("what is ADAS?")
- out_of_scope: anything not about cars

Rules:
- Use only spec keys from the list below; numeric filter values are in the unit shown for that key (kW, Nm, mm, l ...).
  Convert the user's units (e.g. 150 PS -> 110 kW, 1.5 litres engine -> 1500 cc).
- Codes: body_type in suv, hatchback, sedan, mpv, pickup, coupe, convertible, wagon, van; fuel_type in petrol, diesel, cng,
  hybrid, phev, electric, lpg, flex_fuel; transmission_type in manual, automatic, amt, cvt, dct, single_speed;
  drivetrain in fwd, rwd, awd, 4wd. "Automatic" in everyday speech means [automatic, amt, cvt, dct].
- Prices are in Indian rupees (1 lakh = 100000).
- Names: copy brand/model/variant names as the user wrote them; prefer names from the catalogue list when they match.
- "it", "this car", "the diesel" refer to the vehicle in the page context or conversation; leave models empty to use the context.
"""

ANSWER_RULES = """\
You are AutoHub's car assistant. Answer the question using ONLY the numbered evidence supplied.

Output segments:
- verified_fact: a statement taken from evidence of kind brochure_fact; cite those ids.
- sourced_info: a statement taken from evidence of kind sourced or catalogue; cite those ids.
- explanation: general explanation or advice, no numbers or specification claims, cites nothing.

Rules:
1. Every number, unit, feature claim and ranking must come from cited evidence, copied exactly. Never compute new figures.
2. If the evidence does not answer part of the question, say so in missing. Never guess or use outside knowledge for facts.
3. Evidence of kind note states what is NOT recorded; report those in missing.
4. Be concise: at most 6 segments, plain sentences, no markdown tables. Indian English, rupees as given.
"""


def plan_system_prompt(brand_names: Sequence[str], model_names: Sequence[str]) -> str:
    catalogue = ", ".join(model_names[:300])
    return (
        f"{PLAN_RULES}\nBrands: {', '.join(brand_names)}\nModels: {catalogue}\n\n"
        f"Specification keys:\n{spec_catalogue()}\n"
    )


def plan_user_prompt(
    question: str, *, context_vehicle: str | None, history: Sequence[tuple[str, str]]
) -> str:
    lines = []
    if history:
        lines.append("Conversation so far (oldest first):")
        lines.extend(f"{role}: {content[:500]}" for role, content in history[-6:])
    if context_vehicle:
        lines.append(f"Page context: the user is looking at {context_vehicle}.")
    lines.append(f"Question: {question}")
    return "\n".join(lines)


def answer_user_prompt(question: str, evidence_lines: Sequence[str]) -> str:
    body = "\n".join(evidence_lines) if evidence_lines else "(no evidence)"
    return f"Question: {question}\n\nEvidence:\n{body}"
