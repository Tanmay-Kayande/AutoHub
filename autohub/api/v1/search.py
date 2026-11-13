"""Search endpoints: contextual (hybrid / semantic / keyword) and structured SQL search."""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from autohub.api.schemas import SearchHitOut, SearchOut, StructuredOut
from autohub.core.errors import ValidationFailedError
from autohub.core.ratelimit import rate_limit
from autohub.core.settings import get_settings
from autohub.db.session import get_db
from autohub.search.embedder import EmbeddingProvider, get_embedder
from autohub.search.service import SearchHit, SearchScope, hybrid_search, keyword_search, semantic_search
from autohub.search.structured import QueryValidationError, StructuredQuery, run_structured

router = APIRouter(prefix="/search", tags=["search"])
_limit = Depends(rate_limit("search", limit=60))


def query_embedder() -> EmbeddingProvider | None:
    """The embedder, or ``None`` when no provider is configured (keyword search still works)."""
    try:
        return get_embedder()
    except Exception:
        return None


def _out(hit: SearchHit) -> SearchHitOut:
    data = asdict(hit)
    data["metadata"] = data.pop("meta")
    data.pop("id")
    return SearchHitOut(**data)


@router.get("", response_model=SearchOut, dependencies=[_limit])
def search(
    q: str = Query(..., min_length=2, max_length=300),
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid",
    brand_id: list[int] = Query(default_factory=list),
    model_id: list[int] = Query(default_factory=list),
    variant_id: list[int] = Query(default_factory=list),
    entity_type: list[str] = Query(default_factory=list),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    embedder: EmbeddingProvider | None = Depends(query_embedder),
) -> SearchOut:
    scope = SearchScope(
        brand_ids=brand_id, model_ids=model_id, variant_ids=variant_id, entity_types=entity_type
    )
    degraded = False
    if mode == "keyword" or (mode == "semantic" and embedder is None):
        model = embedder.model if embedder is not None else get_settings().embedding_model
        hits = keyword_search(db, q, model=model, scope=scope, limit=limit)
        degraded = mode == "semantic"
    elif mode == "semantic":
        assert embedder is not None
        hits = semantic_search(db, embedder, q, scope=scope, limit=limit)
    else:
        hits, degraded = hybrid_search(db, embedder, q, scope=scope, limit=limit)
    return SearchOut(query=q, mode=mode, degraded=degraded, items=[_out(h) for h in hits])


@router.post("/structured", response_model=StructuredOut, dependencies=[_limit])
def structured(query: StructuredQuery, db: Session = Depends(get_db)) -> StructuredOut:
    try:
        result = run_structured(db, query)
    except QueryValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc
    return StructuredOut.model_validate(
        {
            "total_matched": result.total_matched,
            "missing_value_counts": result.missing_value_counts,
            "items": [asdict(row) for row in result.rows],
        }
    )
