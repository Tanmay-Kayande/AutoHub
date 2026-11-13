"""Normalisation of free-text catalogue attributes to controlled codes.

Each function returns ``None`` when the text is empty or ambiguous (for example
a transmission string that lists both a manual and an automatic). The raw text
is always stored separately, so nothing is lost by declining to guess.
"""

from __future__ import annotations

import re


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def normalize_fuel_type(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.lower()
    found: list[str] = []
    if _has(text, r"plug[\s-]*in", r"\bphev\b"):
        found.append("phev")
    elif _has(text, r"hybrid", r"\bhev\b"):
        found.append("hybrid")
    if _has(text, r"\belectric\b", r"\bev\b", r"\bbev\b") and not found:
        found.append("electric")
    if _has(text, r"\bcng\b"):
        found.append("cng")
    if _has(text, r"\blpg\b"):
        found.append("lpg")
    if _has(text, r"flex"):
        found.append("flex_fuel")
    if _has(text, r"diesel"):
        found.append("diesel")
    if _has(text, r"petrol", r"gasoline") and not {"phev", "hybrid", "cng", "lpg", "flex_fuel"} & set(found):
        found.append("petrol")
    return found[0] if len(found) == 1 else None


def normalize_transmission_type(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.lower()
    found: set[str] = set()
    if _has(text, r"\bamt\b", r"automated manual", r"\bags\b"):
        found.add("amt")
        text = re.sub(r"automated manual", " ", text)
    if _has(text, r"\bcvt\b", r"continuously variable"):
        found.add("cvt")
    if _has(text, r"\bdct\b", r"\bdca\b", r"dual[\s-]*clutch"):
        found.add("dct")
    if _has(text, r"single[\s-]*speed"):
        found.add("single_speed")
    if _has(text, r"\bautomatic\b", r"\bat\b", r"torque converter", r"\d\s*at\b") and not (
        found & {"cvt", "dct", "amt"}
    ):
        found.add("automatic")
    if _has(text, r"\bmanual\b", r"\bmt\b", r"\d\s*mt\b"):
        found.add("manual")
    return next(iter(found)) if len(found) == 1 else None


def normalize_drivetrain(*texts: str | None) -> str | None:
    """Drivetrain from any of the given texts (e.g. transmission wording, variant name)."""
    found: set[str] = set()
    for raw in texts:
        if not raw:
            continue
        text = raw.lower()
        if _has(text, r"\b4wd\b", r"\b4x4\b", r"four[\s-]*wheel drive", r"4xplor"):
            found.add("4wd")
        if _has(text, r"\bawd\b", r"all[\s-]*wheel drive"):
            found.add("awd")
        if _has(text, r"\brwd\b", r"rear[\s-]*wheel drive", r"\b4x2\b"):
            found.add("rwd")
        if _has(text, r"\bfwd\b", r"front[\s-]*wheel drive"):
            found.add("fwd")
    return next(iter(found)) if len(found) == 1 else None


_BODY_TYPES = {
    "suv": "suv", "sport utility": "suv", "compact suv": "suv", "crossover": "suv",
    "hatchback": "hatchback", "hatch": "hatchback",
    "sedan": "sedan", "saloon": "sedan",
    "mpv": "mpv", "muv": "mpv", "minivan": "mpv",
    "pickup": "pickup", "pick-up": "pickup", "truck": "pickup",
    "coupe": "coupe", "convertible": "convertible", "cabriolet": "convertible",
    "wagon": "wagon", "estate": "wagon", "van": "van",
}  # fmt: skip


def normalize_body_type(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip().lower()
    if text in _BODY_TYPES:
        return _BODY_TYPES[text]
    for needle, code in _BODY_TYPES.items():
        if re.search(rf"\b{re.escape(needle)}\b", text):
            return code
    return None
