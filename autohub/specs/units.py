"""Parsing and conversion of specification quantities.

Guiding rule: *no number is better than a wrong number*. When the unit of a
value cannot be established, or a string holds several different values,
``value`` is left ``None`` and a flag explains why. The caller always keeps the
original text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# canonical unit -> {accepted unit spelling (lower-case, no spaces) -> factor to canonical}
_D = Decimal
UNIT_FAMILIES: dict[str, dict[str, Decimal]] = {
    "kW": {"kw": _D(1), "ps": _D("0.73549875"), "bhp": _D("0.74569987"), "hp": _D("0.74569987")},
    "Nm": {
        "nm": _D(1),
        "kgm": _D("9.80665"),
        "kg-m": _D("9.80665"),
        "lb-ft": _D("1.35581795"),
        "lbft": _D("1.35581795"),
    },
    "mm": {"mm": _D(1), "cm": _D(10), "m": _D(1000), "inch": _D("25.4"), "in": _D("25.4"), '"': _D("25.4")},
    "m": {"m": _D(1), "mm": _D("0.001"), "cm": _D("0.01")},
    "l": {
        "l": _D(1),
        "litre": _D(1),
        "litres": _D(1),
        "liter": _D(1),
        "liters": _D(1),
        "ltr": _D(1),
        "ltrs": _D(1),
    },
    "cc": {
        "cc": _D(1),
        "cm3": _D(1),
        "cm³": _D(1),
        "l": _D(1000),
        "litre": _D(1000),
        "litres": _D(1000),
        "liter": _D(1000),
        "ltr": _D(1000),
    },
    "kg": {"kg": _D(1), "kgs": _D(1), "tonne": _D(1000), "tonnes": _D(1000), "t": _D(1000)},
    "km/l": {"km/l": _D(1), "kmpl": _D(1), "kmpl.": _D(1), "km/litre": _D(1), "km/kg": _D(1)},
    "kWh": {"kwh": _D(1)},
    "km": {"km": _D(1), "kms": _D(1)},
    "km/h": {"km/h": _D(1), "kmph": _D(1), "kph": _D(1), "km/hr": _D(1)},
    "s": {"s": _D(1), "sec": _D(1), "secs": _D(1), "second": _D(1), "seconds": _D(1)},
    "rpm": {"rpm": _D(1), "r/min": _D(1)},
    "inch": {"inch": _D(1), "inches": _D(1), "in": _D(1), '"': _D(1), "”": _D(1), "cm": _D("0.3937007874")},
    "deg": {"deg": _D(1), "°": _D(1), "degree": _D(1), "degrees": _D(1)},
    "stars": {"stars": _D(1), "star": _D(1)},
}

# Spellings whose presence means the figure is not a plain value of the canonical unit.
_NOTE_UNITS = {"km/kg": "unit_km_per_kg"}

_NUMBER = r"\d+(?:\.\d+)?"
_RANGE_SEP = r"\s*(?:-|–|—|to)\s*"
# unit token: letters, '/', '³', '°', '"', '-', '.' (e.g. "r/min", "kg-m", "km/l")
_UNIT = r"[A-Za-z°\"”³][A-Za-z0-9°\"”³/\-.]*"
_QUANTITY_RE = re.compile(
    rf"(?P<lo>{_NUMBER})(?:{_RANGE_SEP}(?P<hi>{_NUMBER}))?\s*(?P<unit>{_UNIT})?",
)
_AT_RPM_RE = re.compile(
    # The unit is optional: brochures also write "@ 1250-3000 (with MT)".
    rf"@\s*(?P<lo>{_NUMBER})(?:{_RANGE_SEP}(?P<hi>{_NUMBER}))?(?:\s*(?:rpm|r/min))?",
    re.IGNORECASE,
)
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")

_QUANT = Decimal("0.0001")


@dataclass(frozen=True)
class ParsedQuantity:
    value: Decimal | None = None
    value_max: Decimal | None = None
    unit: str | None = None  # canonical unit of ``value``
    original_unit: str | None = None  # unit as written in the source
    flags: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.value is not None


def _clean_number_text(text: str) -> str:
    return _THOUSANDS_RE.sub("", text)


def _norm_unit(token: str | None) -> str | None:
    if not token:
        return None
    return token.strip().rstrip(".,;:)").lower().replace(" ", "") or None


def _q(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


def convert(
    value: Decimal | float | int | str, unit: str | None, canonical_unit: str | None
) -> ParsedQuantity:
    """Convert an already-separated ``value`` + ``unit`` to the canonical unit."""
    try:
        number = Decimal(_clean_number_text(str(value)).strip())
    except Exception:
        return ParsedQuantity(flags=("not_a_number",))
    if canonical_unit is None:
        return ParsedQuantity(value=_q(number), unit=None, original_unit=unit or None)
    family = UNIT_FAMILIES.get(canonical_unit)
    token = _norm_unit(unit)
    if family is None:
        return ParsedQuantity(flags=("unknown_canonical_unit",))
    if token is None:
        return ParsedQuantity(original_unit=None, flags=("unit_missing",))
    factor = family.get(token)
    if factor is None:
        return ParsedQuantity(original_unit=unit, flags=("unit_unrecognised",))
    flags = (_NOTE_UNITS[token],) if token in _NOTE_UNITS else ()
    return ParsedQuantity(value=_q(number * factor), unit=canonical_unit, original_unit=unit, flags=flags)


def parse_quantity(
    text: str | None,
    canonical_unit: str | None,
    *,
    default_unit: str | None = None,
) -> ParsedQuantity:
    """Extract a quantity in ``canonical_unit`` from free text such as ``"110 kW | 150 PS"``.

    * Several figures that agree after conversion (kW and PS of the same output)
      resolve to the first one.
    * Several figures that disagree (``"300 Nm (MT) / 320 Nm (AT)"``) yield no
      value and the flag ``multi_value``.
    * A bare number is accepted only for unitless specs or when the caller
      supplies ``default_unit``; otherwise the flag is ``unit_missing``.
    """
    if text is None or not str(text).strip():
        return ParsedQuantity(flags=("empty",))
    cleaned = _clean_number_text(str(text))

    if canonical_unit is None:
        match = re.search(_NUMBER, cleaned)
        if not match:
            return ParsedQuantity(flags=("no_number",))
        return ParsedQuantity(value=_q(Decimal(match.group(0))))

    family = UNIT_FAMILIES.get(canonical_unit)
    if family is None:
        return ParsedQuantity(flags=("unknown_canonical_unit",))

    # Ignore "@ 3750 rpm" style qualifiers unless rpm itself is being parsed.
    search_text = cleaned if canonical_unit == "rpm" else _AT_RPM_RE.sub(" ", cleaned)

    candidates: list[ParsedQuantity] = []
    bare_numbers: list[Decimal] = []
    for match in _QUANTITY_RE.finditer(search_text):
        token = _norm_unit(match.group("unit"))
        low = Decimal(match.group("lo"))
        high = Decimal(match.group("hi")) if match.group("hi") else None
        if token is None or token not in family:
            if token is None:
                bare_numbers.append(low)
            continue
        factor = family[token]
        flags: list[str] = []
        if token in _NOTE_UNITS:
            flags.append(_NOTE_UNITS[token])
        if high is not None:
            flags.append("range")
        candidates.append(
            ParsedQuantity(
                value=_q(low * factor),
                value_max=_q(high * factor) if high is not None else None,
                unit=canonical_unit,
                original_unit=(match.group("unit") or "").rstrip(".,;:)"),
                flags=tuple(flags),
            )
        )

    if candidates:
        first = candidates[0]
        assert first.value is not None
        for other in candidates[1:]:
            assert other.value is not None
            tolerance = max(abs(first.value) * Decimal("0.02"), Decimal("0.5"))
            if abs(other.value - first.value) > tolerance:
                return ParsedQuantity(original_unit=first.original_unit, flags=("multi_value",))
        return first

    if bare_numbers and default_unit is not None:
        converted = convert(bare_numbers[0], default_unit, canonical_unit)
        if converted.ok:
            return ParsedQuantity(
                value=converted.value,
                unit=converted.unit,
                original_unit=None,
                flags=(*converted.flags, "unit_assumed"),
            )
    if bare_numbers:
        return ParsedQuantity(flags=("unit_missing",))
    return ParsedQuantity(flags=("no_number",))


def parse_at_rpm(text: str | None) -> ParsedQuantity:
    """Extract the engine-speed qualifier from strings like ``"300 Nm @ 1600-2800 r/min"``.

    Returns no value when the text holds more than one differing qualifier.
    """
    if not text:
        return ParsedQuantity(flags=("empty",))
    matches = list(_AT_RPM_RE.finditer(_clean_number_text(str(text))))
    if not matches:
        return ParsedQuantity(flags=("no_number",))
    spans = {(m.group("lo"), m.group("hi")) for m in matches}
    if len(spans) > 1:
        return ParsedQuantity(flags=("multi_value",))
    low, high = next(iter(spans))
    return ParsedQuantity(
        value=_q(Decimal(low)),
        value_max=_q(Decimal(high)) if high else None,
        unit="rpm",
        original_unit="rpm",
        flags=("range",) if high else (),
    )


_TRUE = {"yes", "y", "true", "standard", "std", "available", "✓", "✔", "●", "s", "present"}
_FALSE = {"no", "n", "false", "not available", "na", "n/a", "-", "–", "—", "x", "✗", "✕", "absent"}
_OPTIONAL = {"optional", "opt", "o", "accessory", "option"}


def parse_feature(text: str | None) -> tuple[bool | None, str | None]:
    """Interpret a feature-matrix cell.

    Returns ``(value, status)`` where status is one of ``present``,
    ``not_available``, ``optional`` or ``None`` when the cell is not understood.
    """
    if text is None:
        return None, None
    token = str(text).strip().lower()
    if token in _TRUE:
        return True, "present"
    if token in _FALSE:
        return False, "not_available"
    if token in _OPTIONAL:
        return None, "optional"
    return None, None
