"""Human-readable rendering of specification values (one place, used by API, search and assistant)."""

from __future__ import annotations

from decimal import Decimal

_STATUS_TEXT = {"not_applicable": "N/A", "not_available": "No", "optional": "Optional"}


def format_number(value: Decimal | float | int) -> str:
    """``Decimal("2184.0000")`` -> ``"2184"``; ``Decimal("10.2500")`` -> ``"10.25"``."""
    number = Decimal(str(value))
    if number == number.to_integral_value():
        return f"{int(number):d}"
    return format(number.normalize(), "f")


def format_spec_value(
    *,
    data_type: str,
    status: str,
    value_num: Decimal | None,
    value_num_max: Decimal | None,
    value_text: str | None,
    value_bool: bool | None,
    unit: str | None,
) -> str:
    if status != "present":
        base = _STATUS_TEXT.get(status, status)
        return f"{base} ({value_text})" if value_text and status == "optional" else base
    if data_type == "boolean":
        if value_bool is None:
            return value_text or "Yes"
        text = "Yes" if value_bool else "No"
        return f"{text} ({value_text})" if value_text else text
    if value_num is not None:
        number = format_number(value_num)
        if value_num_max is not None and value_num_max != value_num:
            number = f"{number}–{format_number(value_num_max)}"
        return f"{number} {unit}" if unit else number
    return value_text or ""
