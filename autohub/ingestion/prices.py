"""Model-level ex-showroom prices published on brand sites.

Brochures carry no prices, so the catalogue shows the range (or starting price) the
manufacturer prints for a model on its own site, with the page and date it was read.
These are model-level figures: they are never written to variants, so per-variant
price filters keep meaning "a price known for this variant".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.db.models import VehicleModel
from autohub.ingestion.adapters.base import DiscoveredPrice

LAKH = Decimal(100_000)
# Anything outside this is a parsing mistake, not a car price (1 lakh .. 100 crore rupees).
MIN_PLAUSIBLE = Decimal(100_000)
MAX_PLAUSIBLE = Decimal(1_000_000_000)

_NOISE = re.compile(r"₹|\brs\b\.?|\binr\b|\*|\bonwards\b|\bfrom\b", re.IGNORECASE)
_RANGE_SEPARATOR = re.compile(r"\s*(?:-|–|—|\bto\b)\s*", re.IGNORECASE)
_LAKH = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s*(?:l|lakhs?)?", re.IGNORECASE)
_GROUPED_RUPEES = re.compile(r"\d{1,3}(?:[ ,]\d{2,3})+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _amount(text: str) -> Decimal | None:
    """One printed amount in rupees: ``"10.99"`` or ``"7.79L"`` (lakh), ``"55 70 600"`` (rupees)."""
    cleaned = " ".join(_NOISE.sub(" ", text).split())
    try:
        lakh = _LAKH.fullmatch(cleaned)
        if _GROUPED_RUPEES.fullmatch(cleaned):
            value = Decimal(re.sub(r"[ ,]", "", cleaned))
        elif lakh:
            value = Decimal(lakh.group(1)) * LAKH
        else:
            return None
    except InvalidOperation:
        return None
    return value if MIN_PLAUSIBLE <= value <= MAX_PLAUSIBLE else None


def parse_price_range(text: str) -> tuple[Decimal, Decimal | None] | None:
    """``(min, max)`` in rupees from a printed ex-showroom price, or None if it is not one.

    Accepts the forms the brand sites use: ``"₹ 10.99 - 18.41"`` and ``"₹7.79L - ₹15.04L"``
    (lakh), ``"₹ 6.40"`` and ``"₹7.39 L*"`` (a starting price: max is None), and
    ``"₹ 55 70 600"`` (rupees with Indian digit grouping).
    """
    parts = [p for p in _RANGE_SEPARATOR.split(text.strip()) if p.strip()]
    if not 1 <= len(parts) <= 2:
        return None
    amounts = [_amount(p) for p in parts]
    if any(a is None for a in amounts):
        return None
    low = amounts[0]
    assert low is not None
    if len(amounts) == 1:
        return low, None
    high = amounts[1]
    assert high is not None
    return (low, high) if high >= low else None


def _key(name: str) -> str:
    return _NON_ALNUM.sub("", name.lower())


@dataclass
class PriceUpdate:
    updated: int = 0
    unchanged: int = 0
    unmatched: list[str] = field(default_factory=list)


def apply_model_prices(
    db: Session, brand_id: int, prices: list[DiscoveredPrice], *, as_of: date
) -> PriceUpdate:
    """Write discovered prices to the brand's models. The caller commits.

    A price is applied only to a model whose name is the same ignoring case, spaces and
    punctuation ("SCORPIO-N" is "Scorpio N"). There is no fuzzy matching here: a near
    miss ("Bolero Neo Plus" for "Bolero Neo") would put a wrong price on a real car, so
    it is reported as unmatched instead.
    """
    outcome = PriceUpdate()
    models = {
        _key(m.name): m
        for m in db.execute(select(VehicleModel).where(VehicleModel.brand_id == brand_id)).scalars()
    }
    for price in prices:
        model = models.get(_key(price.model_hint))
        if model is None:
            outcome.unmatched.append(price.model_hint)
            continue
        same = (
            model.price_min_ex_showroom == price.price_min
            and model.price_max_ex_showroom == price.price_max
            and model.price_source == price.source_url
        )
        model.price_min_ex_showroom = price.price_min
        model.price_max_ex_showroom = price.price_max
        model.price_source = price.source_url
        model.price_as_of = as_of  # the price was confirmed today even if it did not change
        if same:
            outcome.unchanged += 1
        else:
            outcome.updated += 1
    db.flush()
    return outcome
