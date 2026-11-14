"""Pagination parameters and the list-response envelope used by every collection endpoint."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from autohub.core.errors import ValidationFailedError

MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class PageParams:
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE, description="Items per page"),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


def make_page[T](items: Sequence[T], total: int, params: PageParams) -> Page[T]:
    return Page[T](
        items=list(items),
        total=total,
        page=params.page,
        page_size=params.page_size,
        pages=math.ceil(total / params.page_size) if total else 0,
    )


def count_rows(db: Session, stmt: Select[Any]) -> int:
    """Total rows a statement would return, ignoring its ordering and paging."""
    subquery = stmt.order_by(None).limit(None).offset(None).subquery()
    return int(db.execute(select(func.count()).select_from(subquery)).scalar_one())


def apply_sort(stmt: Select[Any], sort: str | None, allowed: dict[str, Any], default: str) -> Select[Any]:
    """Apply ``sort`` ("field" or "-field"); only whitelisted fields are accepted."""
    raw = sort or default
    descending = raw.startswith("-")
    key = raw[1:] if descending else raw
    if key not in allowed:
        raise ValidationFailedError(
            f"Cannot sort by '{key}'.",
            details={"allowed": sorted(allowed)},
        )
    column = allowed[key]
    return stmt.order_by(column.desc().nulls_last() if descending else column.asc().nulls_last())
