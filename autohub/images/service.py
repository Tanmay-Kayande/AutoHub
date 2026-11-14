"""Image collection, validation and primary selection.

- :func:`collect_images` searches for models that have fewer than the target
  number of usable images per category, within a per-run search budget, filters
  and validates candidates, and inserts them (``ON CONFLICT DO NOTHING`` on
  ``(model_id, url_hash)``, so duplicates are impossible).
- :func:`validate_pending` checks stored URLs over plain HTTP (free) — this is
  also how migrated V1 images get a status (T-503).
- :func:`choose_primary` keeps exactly one primary per model: the largest valid
  exterior image, official sources first.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from autohub.core.settings import get_settings
from autohub.db.models import Brand, VehicleImage, VehicleModel
from autohub.images.filters import rejection_reason
from autohub.images.provider import ImageQuotaError, ImageSearchError, ImageSearchProvider, get_image_provider
from autohub.images.urls import domain_of, url_hash
from autohub.images.validator import ImageValidator

logger = logging.getLogger(__name__)

CATEGORY_QUERIES: dict[str, str] = {
    "exterior": "exterior",
    "interior": "interior cabin",
    "dashboard": "dashboard",
    "rear_seat": "rear seat",
    "boot": "boot space",
}
SEARCH_RESULTS = 10
USABLE = ("valid", "unvalidated")


@dataclass
class ImageStats:
    searches: int = 0
    candidates: int = 0
    filtered: int = 0
    inserted: int = 0
    duplicates: int = 0
    valid: int = 0
    broken: int = 0
    rejected: int = 0
    primaries_set: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def official_domains(brand: Brand) -> tuple[str, ...]:
    from autohub.ingestion.adapters.registry import available_keys, get_adapter

    for key in available_keys():
        adapter = get_adapter(key)
        if adapter.brand_name == brand.name:
            return adapter.official_domains
    domain = domain_of(brand.website_url) if brand.website_url else None
    return (domain,) if domain else ()


def is_official_url(url: str | None, domains: Iterable[str]) -> bool:
    host = domain_of(url) if url else None
    return bool(host) and any(host == d or host.endswith("." + d) for d in domains)  # type: ignore[union-attr]


def _usable_count(db: Session, model_id: int, category: str) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(VehicleImage)
            .where(
                VehicleImage.model_id == model_id,
                VehicleImage.category == category,
                VehicleImage.validation_status.in_(USABLE),
            )
        ).scalar_one()
    )


def _record(stats: ImageStats, status: str) -> None:
    setattr(stats, status, getattr(stats, status) + 1)


def collect_for_model(
    db: Session,
    model: VehicleModel,
    category: str,
    *,
    provider: ImageSearchProvider,
    validator: ImageValidator,
    target: int,
    stats: ImageStats,
) -> None:
    brand = model.brand
    others = list(
        db.execute(
            select(VehicleModel.name).where(VehicleModel.brand_id == brand.id, VehicleModel.id != model.id)
        ).scalars()
    )
    domains = official_domains(brand)
    query = f"{brand.name} {model.name} {CATEGORY_QUERIES[category]}"
    candidates = provider.search(query, limit=SEARCH_RESULTS)
    stats.searches += 1
    stats.candidates += len(candidates)
    have = _usable_count(db, model.id, category)
    now = datetime.now(UTC)
    for candidate in candidates:
        if have >= target:
            break
        if rejection_reason(candidate, model_name=model.name, other_models=others):
            stats.filtered += 1
            continue
        digest = url_hash(candidate.image_url)
        exists = db.execute(
            select(VehicleImage.id).where(VehicleImage.model_id == model.id, VehicleImage.url_hash == digest)
        ).first()
        if exists:
            stats.duplicates += 1
            continue
        verdict = validator.validate(candidate.image_url)
        _record(stats, verdict.status)
        if verdict.status != "valid":
            continue
        inserted = db.execute(
            insert(VehicleImage)
            .values(
                model_id=model.id,
                category=category,
                image_url=candidate.image_url,
                url_hash=digest,
                thumbnail_url=candidate.thumbnail_url,
                source_page_url=candidate.source_page_url,
                source_domain=candidate.source_domain,
                source_kind="image_search",
                is_official=is_official_url(candidate.source_page_url or candidate.image_url, domains),
                title=(candidate.title or "")[:300] or None,
                width=candidate.width,
                height=candidate.height,
                retrieved_at=now,
                validation_status="valid",
                validated_at=now,
                sort_order=candidate.position,
            )
            .on_conflict_do_nothing(index_elements=["model_id", "url_hash"])
            .returning(VehicleImage.id)
        ).first()
        if inserted is not None:
            stats.inserted += 1
            have += 1
        else:
            stats.duplicates += 1


def models_needing_images(db: Session, brand_ids: list[int], target: int) -> list[tuple[VehicleModel, str]]:
    models = db.execute(
        select(VehicleModel)
        .where(VehicleModel.brand_id.in_(brand_ids), VehicleModel.is_active.is_(True))
        .order_by(VehicleModel.id)
    ).scalars()
    work: list[tuple[VehicleModel, str]] = []
    for model in models:
        for category in CATEGORY_QUERIES:  # exterior first: it decides the primary image
            if _usable_count(db, model.id, category) < target:
                work.append((model, category))
    # Breadth first: every model's exterior before anyone's boot photos.
    order = list(CATEGORY_QUERIES)
    return sorted(work, key=lambda item: (order.index(item[1]), item[0].id))


def collect_images(
    db: Session,
    *,
    brands: list[Brand],
    budget: int,
    provider: ImageSearchProvider,
    validator: ImageValidator,
    target: int,
) -> ImageStats:
    stats = ImageStats()
    for model, category in models_needing_images(db, [b.id for b in brands], target):
        if stats.searches >= budget:
            break
        try:
            collect_for_model(
                db, model, category, provider=provider, validator=validator, target=target, stats=stats
            )
            db.commit()
        except ImageQuotaError as exc:
            db.rollback()
            stats.errors.append(f"quota: {exc}")
            break
        except ImageSearchError as exc:
            db.rollback()
            stats.errors.append(f"{model.name}/{category}: {exc}")
    stats.primaries_set = sum(choose_primary(db, m.id) for m in _models(db, [b.id for b in brands]))
    db.commit()
    return stats


def _models(db: Session, brand_ids: list[int]) -> list[VehicleModel]:
    return list(db.execute(select(VehicleModel).where(VehicleModel.brand_id.in_(brand_ids))).scalars())


def validate_pending(
    db: Session,
    validator: ImageValidator,
    *,
    statuses: tuple[str, ...] = ("unvalidated",),
    limit: int | None = None,
) -> ImageStats:
    stats = ImageStats()
    stmt = select(VehicleImage).where(VehicleImage.validation_status.in_(statuses)).order_by(VehicleImage.id)
    if limit:
        stmt = stmt.limit(limit)
    for image in list(db.execute(stmt).scalars()):
        verdict = validator.validate(image.image_url)
        image.validation_status = verdict.status
        image.validation_note = verdict.note
        image.validated_at = datetime.now(UTC)
        if verdict.status != "valid" and image.is_primary:
            image.is_primary = False
        _record(stats, verdict.status)
        db.commit()
    model_ids = db.execute(select(VehicleImage.model_id).distinct()).scalars().all()
    stats.primaries_set = sum(choose_primary(db, model_id) for model_id in model_ids)
    db.commit()
    return stats


def choose_primary(db: Session, model_id: int) -> int:
    """Make the best valid exterior image the model's only primary. Returns 1 if it changed."""
    candidates = list(
        db.execute(
            select(VehicleImage).where(
                VehicleImage.model_id == model_id,
                VehicleImage.category == "exterior",
                VehicleImage.validation_status == "valid",
            )
        ).scalars()
    )
    current = db.execute(
        select(VehicleImage).where(VehicleImage.model_id == model_id, VehicleImage.is_primary.is_(True))
    ).scalar_one_or_none()
    if not candidates:
        if current is not None and current.validation_status != "valid":
            current.is_primary = False
            db.flush()
            return 1
        return 0
    best = max(
        candidates,
        key=lambda i: (i.is_official, (i.width or 0) * (i.height or 0), -i.sort_order, -i.id),
    )
    if current is not None and current.id == best.id:
        return 0
    db.execute(update(VehicleImage).where(VehicleImage.model_id == model_id).values(is_primary=False))
    db.flush()
    best.is_primary = True
    db.flush()
    return 1


def pipeline_stage(db: Session, *, brands: list[Brand], budget: int) -> dict[str, Any]:
    """The pipeline's ``images`` stage."""
    settings = get_settings()
    stats = collect_images(
        db,
        brands=brands,
        budget=budget,
        provider=get_image_provider(settings),
        validator=ImageValidator(timeout_seconds=settings.http_timeout_seconds),
        target=settings.images_target_per_category,
    )
    return stats.as_dict()
