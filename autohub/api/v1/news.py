from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from autohub.api.schemas import NewsIn, NewsOut, NewsUpdateIn
from autohub.auth.deps import require_admin
from autohub.core.pagination import Page, PageParams, make_page, page_params
from autohub.db.session import get_db
from autohub.news import service

router = APIRouter(prefix="/news", tags=["news"])


@router.get("", response_model=Page[NewsOut])
def list_news(
    model_id: int | None = Query(None, description="Only news linked to this model"),
    sort: str | None = Query(None, description="published_at | title (prefix '-' for descending)"),
    params: PageParams = Depends(page_params),
    db: Session = Depends(get_db),
) -> Page[NewsOut]:
    items, total = service.list_news(db, params, model_id=model_id, sort=sort)
    return make_page(items, total, params)


@router.get("/{news_id}", response_model=NewsOut)
def get_news(news_id: int, db: Session = Depends(get_db)) -> NewsOut:
    return service.to_out(service.get_news(db, news_id))


@router.post(
    "", response_model=NewsOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)]
)
def create_news(data: NewsIn, db: Session = Depends(get_db)) -> NewsOut:
    return service.to_out(service.create_news(db, data))


@router.patch("/{news_id}", response_model=NewsOut, dependencies=[Depends(require_admin)])
def update_news(news_id: int, data: NewsUpdateIn, db: Session = Depends(get_db)) -> NewsOut:
    return service.to_out(service.update_news(db, news_id, data))


@router.delete("/{news_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
def delete_news(news_id: int, db: Session = Depends(get_db)) -> None:
    service.delete_news(db, news_id)
