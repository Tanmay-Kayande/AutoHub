"""News reads and writes."""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, joinedload, selectinload

from autohub.api.schemas import BrandRef, ModelRef, NewsImageOut, NewsIn, NewsOut, NewsUpdateIn
from autohub.catalog.slugs import slugify
from autohub.core.errors import NotFoundError, ValidationFailedError
from autohub.core.pagination import PageParams, apply_sort, count_rows
from autohub.db.models import News, NewsImage, VehicleModel


def _query() -> Select[tuple[News]]:
    return select(News).options(
        selectinload(News.news_images),
        selectinload(News.vehicles).joinedload(VehicleModel.brand),
    )


def to_out(news: News) -> NewsOut:
    return NewsOut(
        id=news.id,
        title=news.title,
        slug=news.slug,
        summary=news.summary,
        content=news.content,
        source_name=news.source_name,
        source_url=news.source_url,
        published_at=news.published_at,
        news_images=[NewsImageOut.model_validate(image) for image in news.news_images],
        vehicles=[
            ModelRef(id=v.id, name=v.name, slug=v.slug, brand=BrandRef.model_validate(v.brand))
            for v in news.vehicles
        ],
    )


def list_news(
    db: Session, params: PageParams, *, model_id: int | None = None, sort: str | None = None
) -> tuple[list[NewsOut], int]:
    stmt = _query()
    if model_id is not None:
        stmt = stmt.where(News.vehicles.any(VehicleModel.id == model_id))
    total = count_rows(db, stmt)
    stmt = apply_sort(
        stmt, sort, {"published_at": News.published_at, "title": News.title}, default="-published_at"
    )
    rows = db.execute(stmt.offset(params.offset).limit(params.page_size)).scalars()
    return [to_out(n) for n in rows], total


def get_news(db: Session, news_id: int) -> News:
    news = db.execute(_query().where(News.id == news_id)).scalar_one_or_none()
    if news is None:
        raise NotFoundError(f"News {news_id} was not found.")
    return news


def _unique_slug(db: Session, title: str, *, exclude_id: int | None = None) -> str:
    base = slugify(title)[:120]
    slug, counter = base, 2
    while True:
        stmt = select(News.id).where(News.slug == slug)
        if exclude_id is not None:
            stmt = stmt.where(News.id != exclude_id)
        if db.execute(stmt).first() is None:
            return slug
        slug = f"{base}-{counter}"
        counter += 1


def _vehicles(db: Session, model_ids: list[int]) -> list[VehicleModel]:
    if not model_ids:
        return []
    models = list(
        db.execute(
            select(VehicleModel).where(VehicleModel.id.in_(model_ids)).options(joinedload(VehicleModel.brand))
        ).scalars()
    )
    missing = sorted(set(model_ids) - {m.id for m in models})
    if missing:
        raise ValidationFailedError(f"Unknown model id(s): {missing}")
    return models


def create_news(db: Session, data: NewsIn) -> News:
    news = News(
        title=data.title,
        slug=_unique_slug(db, data.title),
        summary=data.summary,
        content=data.content,
        source_name=data.source_name,
        source_url=data.source_url,
        published_at=data.published_at,
        news_images=[NewsImage(image_url=url) for url in data.image_urls],
        vehicles=_vehicles(db, data.model_ids),
    )
    db.add(news)
    db.commit()
    return get_news(db, news.id)


def update_news(db: Session, news_id: int, data: NewsUpdateIn) -> News:
    news = get_news(db, news_id)
    changes = data.model_dump(exclude_unset=True)
    for field in ("title", "content", "published_at"):
        if field in changes and changes[field] is None:
            raise ValidationFailedError(f"'{field}' cannot be null.")
    for field in ("title", "content", "summary", "source_name", "source_url", "published_at"):
        if field in changes:
            setattr(news, field, changes[field])
    if "title" in changes:
        news.slug = _unique_slug(db, news.title, exclude_id=news.id)
    if changes.get("image_urls") is not None:
        news.news_images = [NewsImage(image_url=url) for url in changes["image_urls"]]
    if changes.get("model_ids") is not None:
        news.vehicles = _vehicles(db, changes["model_ids"])
    db.commit()
    return get_news(db, news.id)


def delete_news(db: Session, news_id: int) -> None:
    db.delete(get_news(db, news_id))
    db.commit()
