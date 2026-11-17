"""Image collection, de-duplication, validation of stored rows and primary selection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from autohub.db.models import PipelineRun, VehicleImage
from autohub.images.provider import FakeImageSearchProvider, ImageCandidate
from autohub.images.service import (
    CATEGORY_QUERIES,
    choose_primary,
    collect_images,
    recently_exhausted,
    slot_key,
    validate_pending,
)
from autohub.images.urls import url_hash
from autohub.images.validator import Validation
from tests.factories import make_brand, make_image, make_model


class FakeValidator:
    def __init__(self, verdicts: dict[str, Validation] | None = None) -> None:
        self.verdicts = verdicts or {}
        self.checked: list[str] = []

    def validate(self, url: str) -> Validation:
        self.checked.append(url)
        return self.verdicts.get(url, Validation("valid"))


def cand(
    url: str, *, page: str | None = None, w: int = 1600, h: int = 900, title: str | None = None
) -> ImageCandidate:
    return ImageCandidate(url, None, page, None, title, w, h, 1)


def test_collect_inserts_filters_validates_and_sets_primary(db: Session) -> None:
    brand = make_brand(db)
    brand.website_url = "https://www.testa.example/"
    model = make_model(db, brand)
    other = make_model(db, brand, "Model R")
    provider = FakeImageSearchProvider(
        {
            "Testa Model Q exterior": [
                cand("https://cdn.example.com/big.jpg", w=2000, h=1200),
                cand(
                    "https://www.testa.example/official.jpg",
                    page="https://www.testa.example/q",
                    w=1200,
                    h=700,
                ),
                cand("https://cdn.example.com/small.jpg", w=200, h=100),
                cand("https://cdn.example.com/other.jpg", title="Testa Model R front"),
                cand("https://cdn.example.com/dead.jpg"),
            ]
        }
    )
    validator = FakeValidator({"https://cdn.example.com/dead.jpg": Validation("broken", "HTTP 404")})

    stats = collect_images(db, brands=[brand], budget=1, provider=provider, validator=validator, target=5)

    assert stats.searches == 1 and stats.inserted == 2 and stats.filtered == 2 and stats.broken == 1
    images = db.execute(select(VehicleImage).where(VehicleImage.model_id == model.id)).scalars().all()
    assert {i.image_url for i in images} == {
        "https://cdn.example.com/big.jpg",
        "https://www.testa.example/official.jpg",
    }
    official = next(i for i in images if i.is_official)
    assert official.is_primary and official.source_kind == "image_search"  # official beats bigger
    assert sum(i.is_primary for i in images) == 1

    # Empty slots now come first; skip them to re-search Model Q's partly filled exterior.
    empty = frozenset((model.id, c) for c in CATEGORY_QUERIES if c != "exterior")
    other.is_active = False
    db.flush()
    again = collect_images(
        db, brands=[brand], budget=1, provider=provider, validator=validator, target=5, skip=empty
    )
    assert again.inserted == 0 and again.duplicates == 2
    assert again.exhausted == [f"{model.id}/exterior"]


def test_budget_and_target_limit_searches(db: Session) -> None:
    brand = make_brand(db)
    model = make_model(db, brand)
    for n in range(2):
        make_image(db, model, f"https://cdn.example.com/e{n}.jpg")
    provider = FakeImageSearchProvider()
    collect_images(db, brands=[brand], budget=3, provider=provider, validator=FakeValidator(), target=2)
    # exterior already has 2 usable images -> not searched
    assert provider.queries == [
        "Testa Model Q interior cabin",
        "Testa Model Q dashboard",
        "Testa Model Q rear seat",
    ]


def test_empty_slots_are_searched_before_partly_filled_ones(db: Session) -> None:
    brand = make_brand(db)
    model = make_model(db, brand)
    make_image(db, model, "https://cdn.example.com/e.jpg")  # exterior 1 of 3: partly filled
    make_image(db, model, "https://cdn.example.com/i.jpg", category="interior")
    provider = FakeImageSearchProvider()
    collect_images(db, brands=[brand], budget=3, provider=provider, validator=FakeValidator(), target=3)
    assert provider.queries == [
        "Testa Model Q dashboard",
        "Testa Model Q rear seat",
        "Testa Model Q boot space",
    ]


def test_search_without_new_images_is_recorded_and_skipped_later(db: Session) -> None:
    brand = make_brand(db)
    model = make_model(db, brand)
    provider = FakeImageSearchProvider(
        {"Testa Model Q exterior": [cand("https://cdn.example.com/front.jpg")]}
    )
    stats = collect_images(
        db, brands=[brand], budget=2, provider=provider, validator=FakeValidator(), target=3
    )
    assert stats.inserted == 1
    assert stats.exhausted == [slot_key(model.id, "interior")]  # exterior added one, interior none

    now = datetime.now(UTC)
    db.add(PipelineRun(trigger="cli", status="succeeded", stats={"images": stats.as_dict()}))
    db.add(  # outside the window: ignored
        PipelineRun(
            trigger="cli",
            status="succeeded",
            stats={"images": {"exhausted": [slot_key(model.id, "dashboard")]}},
            started_at=now - timedelta(days=40),
        )
    )
    db.add(
        PipelineRun(trigger="cli", status="succeeded", stats={"images": {"skipped": "stage not configured"}})
    )
    db.flush()
    skip = recently_exhausted(db, days=30, now=now)
    assert skip == {(model.id, "interior")}
    assert recently_exhausted(db, days=0, now=now) == frozenset()

    later = FakeImageSearchProvider()
    again = collect_images(
        db, brands=[brand], budget=2, provider=later, validator=FakeValidator(), target=3, skip=skip
    )
    assert again.skipped_recent == 1
    assert later.queries == ["Testa Model Q dashboard", "Testa Model Q rear seat"]


def test_duplicate_insert_is_impossible(db: Session) -> None:
    model = make_model(db, make_brand(db))
    make_image(db, model, "https://cdn.example.com/x.jpg")
    result = db.execute(
        insert(VehicleImage)
        .values(
            model_id=model.id,
            category="exterior",
            image_url="https://CDN.example.com/x.jpg",
            url_hash=url_hash("https://CDN.example.com/x.jpg"),
            source_kind="image_search",
        )
        .on_conflict_do_nothing(index_elements=["model_id", "url_hash"])
    )
    assert result.rowcount == 0


def test_validate_pending_marks_rows_and_moves_primary(db: Session) -> None:
    model = make_model(db, make_brand(db))
    old = make_image(db, model, "https://legacy.example/old.jpg", status="unvalidated", primary=True)
    good = make_image(db, model, "https://legacy.example/good.jpg", status="unvalidated")
    interior = make_image(
        db, model, "https://legacy.example/in.jpg", category="interior", status="unvalidated"
    )
    validator = FakeValidator(
        {
            "https://legacy.example/old.jpg": Validation("broken", "HTTP 404"),
            "https://legacy.example/in.jpg": Validation("rejected", "content type text/html"),
        }
    )
    stats = validate_pending(db, validator)  # type: ignore[arg-type]
    assert (stats.valid, stats.broken, stats.rejected) == (1, 1, 1)
    for image in (old, good, interior):
        db.refresh(image)
    assert old.validation_status == "broken" and old.validation_note == "HTTP 404" and not old.is_primary
    assert good.is_primary and good.validated_at is not None
    remaining = db.execute(
        select(func.count()).select_from(VehicleImage).where(VehicleImage.validation_status == "unvalidated")
    ).scalar_one()
    assert remaining == 0


def test_choose_primary_is_stable_and_clears_when_nothing_valid(db: Session) -> None:
    model = make_model(db, make_brand(db))
    image = make_image(db, model, "https://cdn.example.com/a.jpg", primary=True)
    assert choose_primary(db, model.id) == 0
    image.validation_status = "broken"
    db.flush()
    assert choose_primary(db, model.id) == 1
    db.refresh(image)
    assert not image.is_primary
