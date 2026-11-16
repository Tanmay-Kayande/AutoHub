"""Published model prices: matching to models, the catalogue fallback, and the discover stage."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from autohub.ingestion.adapters.base import DiscoveredPrice, DiscoveryResult, PageFetcher
from autohub.ingestion.prices import apply_model_prices
from tests.factories import make_brand, make_model, make_variant
from tests.integration.test_pipeline import Env, TestaAdapter

API = "/api/v1"
PAGE = "https://www.testa.example/cars"
TODAY = date(2026, 11, 17)


def price(model: str, low: int, high: int | None = None) -> DiscoveredPrice:
    return DiscoveredPrice(
        brand="Testa",
        model_hint=model,
        price_min=Decimal(low),
        price_max=None if high is None else Decimal(high),
        source_url=PAGE,
    )


def test_prices_are_applied_by_exact_name_ignoring_case_and_punctuation(db: Session) -> None:
    brand = make_brand(db)
    scorpio = make_model(db, brand, "Scorpio N")
    neo = make_model(db, brand, "Bolero Neo")
    other_brand_model = make_model(db, make_brand(db, name="Otherco"), "Scorpio N")

    outcome = apply_model_prices(
        db,
        brand.id,
        [price("SCORPIO-N", 1_369_000, 2_549_000), price("BOLERO NEO PLUS", 1_146_000, 1_356_000)],
        as_of=TODAY,
    )
    assert (outcome.updated, outcome.unchanged, outcome.unmatched) == (1, 0, ["BOLERO NEO PLUS"])
    assert (scorpio.price_min_ex_showroom, scorpio.price_max_ex_showroom) == (
        Decimal(1_369_000),
        Decimal(2_549_000),
    )
    assert (scorpio.price_source, scorpio.price_as_of) == (PAGE, TODAY)
    # A near miss must not put another model's price on this one; another brand is untouched.
    assert neo.price_min_ex_showroom is None and other_brand_model.price_min_ex_showroom is None


def test_reapplying_refreshes_the_date_and_a_starting_price_clears_the_old_maximum(db: Session) -> None:
    brand = make_brand(db)
    model = make_model(db, brand, "Model Q")
    apply_model_prices(db, brand.id, [price("Model Q", 700_000, 900_000)], as_of=date(2026, 11, 1))

    same = apply_model_prices(db, brand.id, [price("Model Q", 700_000, 900_000)], as_of=TODAY)
    assert (same.updated, same.unchanged) == (0, 1) and model.price_as_of == TODAY

    changed = apply_model_prices(db, brand.id, [price("Model Q", 739_000)], as_of=TODAY)
    assert (changed.updated, changed.unchanged) == (1, 0)
    assert (model.price_min_ex_showroom, model.price_max_ex_showroom) == (Decimal(739_000), None)


def test_catalogue_shows_the_published_range_only_when_no_variant_has_a_price(
    client: Any, db: Session
) -> None:
    brand = make_brand(db)
    published = make_model(db, brand, "Model Q")
    make_variant(db, published, "LX")
    precise = make_model(db, brand, "Model R")
    make_variant(db, precise, "LX", price=Decimal(1_000_000))
    make_variant(db, precise, "ZX", price=Decimal(1_800_000))
    make_model(db, brand, "Model S")
    unpriced = make_model(db, brand, "Model T")
    apply_model_prices(
        db,
        brand.id,
        [price("Model Q", 700_000, 900_000), price("Model R", 100_000, 200_000), price("Model S", 529_000)],
        as_of=TODAY,
    )
    db.flush()

    items = {m["name"]: m for m in client.get(f"{API}/models", params={"brand": brand.slug}).json()["items"]}
    assert len(items) == 4 and unpriced.name in items

    def shown(name: str) -> tuple[Any, Any, Any, Any]:
        m = items[name]
        return m["price_min"], m["price_max"], m["price_source"], m["price_as_of"]

    assert shown("Model Q") == (700_000, 900_000, PAGE, "2026-11-17")
    # Variant prices are the precise ones and are not labelled as a published range.
    assert shown("Model R") == (1_000_000, 1_800_000, None, None)
    assert shown("Model S") == (529_000, None, PAGE, "2026-11-17")
    assert shown("Model T") == (None, None, None, None)

    detail = client.get(f"{API}/models/{brand.slug}/{published.slug}").json()
    assert (detail["price_min"], detail["price_max"], detail["price_source"]) == (700_000, 900_000, PAGE)


class PricedAdapter(TestaAdapter):
    prices: ClassVar[list[tuple[str, int, int | None]]] = [
        ("MODEL-Q", 700_000, 900_000),
        ("Model Z", 500_000, None),
    ]

    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        result = super().discover(fetcher)
        result.prices = [
            DiscoveredPrice(
                self.brand_name, name, Decimal(low), None if high is None else Decimal(high), PAGE
            )
            for name, low, high in self.prices
        ]
        return result


def test_discover_stage_applies_prices_and_reports_unmatched_names(
    db: Session, engine: Engine, tmp_path: Path
) -> None:
    env = Env(db, engine, tmp_path, {}, [])
    env.pipeline._resolve = lambda keys: ([PricedAdapter()], [])
    from autohub.jobs.pipeline import PipelineOptions

    # First run: the brand has no models yet (extraction creates them), so nothing matches.
    first = env.pipeline.run(db, PipelineOptions(brands=["testa"], stages=("discover",)))
    assert first.stats is not None
    assert first.stats["discover"]["testa"]["prices"] == {
        "found": 2,
        "updated": 0,
        "unchanged": 0,
        "unmatched": ["MODEL-Q", "Model Z"],
    }

    from sqlalchemy import select

    from autohub.db.models import Brand

    brand = db.execute(select(Brand).where(Brand.name == "Testa Pipeline")).scalar_one()
    model = make_model(db, brand, "Model Q")
    db.commit()
    second = env.pipeline.run(db, PipelineOptions(brands=["testa"], stages=("discover",)))
    assert second.status == "succeeded" and second.stats is not None
    assert second.stats["discover"]["testa"]["prices"] == {
        "found": 2,
        "updated": 1,
        "unchanged": 0,
        "unmatched": ["Model Z"],
    }
    db.refresh(model)
    assert model.price_min_ex_showroom == Decimal(700_000) and model.price_source == PAGE
