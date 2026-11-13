"""Contextual retrieval over ``embeddings``: semantic (pgvector), keyword (full text) and hybrid (RRF).

Metadata filters (brand, model, variant, entity type) are applied inside each
leg, so a scoped search never borrows results from another vehicle. Queries
only ever see the active ``(embedding_model, embedding_version)`` set.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from autohub.core.settings import get_settings
from autohub.db.models import EMBEDDING_ENTITY_TYPES, Embedding, VehicleModel
from autohub.search.embedder import EmbeddingProvider

logger = logging.getLogger(__name__)

RRF_K = 60
NAME_SIMILARITY = 0.3


class SearchScope(BaseModel):
    brand_ids: list[int] = Field(default_factory=list)
    model_ids: list[int] = Field(default_factory=list)
    variant_ids: list[int] = Field(default_factory=list)
    entity_types: list[str] = Field(default_factory=list)


@dataclass
class SearchHit:
    id: int
    entity_type: str
    entity_id: int
    chunk_index: int
    content: str
    meta: dict[str, Any]
    brand_id: int | None
    model_id: int | None
    variant_id: int | None
    score: float
    sources: list[str] = field(default_factory=list)


def _hit(row: Embedding, score: float, source: str) -> SearchHit:
    return SearchHit(
        id=row.id,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        chunk_index=row.chunk_index,
        content=row.content,
        meta=dict(row.meta or {}),
        brand_id=row.brand_id,
        model_id=row.model_id,
        variant_id=row.variant_id,
        score=score,
        sources=[source],
    )


def _scoped(stmt: Select[Any], model: str, version: str, scope: SearchScope) -> Select[Any]:
    conditions: list[ColumnElement[bool]] = [
        Embedding.embedding_model == model,
        Embedding.embedding_version == version,
    ]
    if scope.brand_ids:
        conditions.append(Embedding.brand_id.in_(scope.brand_ids))
    if scope.model_ids:
        conditions.append(Embedding.model_id.in_(scope.model_ids))
    if scope.variant_ids:
        conditions.append(Embedding.variant_id.in_(scope.variant_ids))
    types = [t for t in scope.entity_types if t in EMBEDDING_ENTITY_TYPES]
    if types:
        conditions.append(Embedding.entity_type.in_(types))
    return stmt.where(*conditions)


def semantic_search(
    db: Session, embedder: EmbeddingProvider, query: str, *, scope: SearchScope, limit: int = 10
) -> list[SearchHit]:
    vector = embedder.embed_query(query)
    distance = Embedding.embedding.cosine_distance(vector)
    stmt = _scoped(
        select(Embedding, distance.label("distance")), embedder.model, get_settings().embedding_version, scope
    )
    rows = db.execute(stmt.order_by(distance).limit(limit)).all()
    return [_hit(row, 1.0 - float(d), "semantic") for row, d in rows]


def keyword_search(
    db: Session, query: str, *, model: str, scope: SearchScope, limit: int = 10
) -> list[SearchHit]:
    version = get_settings().embedding_version
    tsquery = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank_cd(Embedding.tsv, tsquery)
    stmt = _scoped(
        select(Embedding, rank.label("rank")).where(Embedding.tsv.op("@@")(tsquery)), model, version, scope
    )
    hits = [
        _hit(row, float(r), "keyword")
        for row, r in db.execute(stmt.order_by(rank.desc(), Embedding.id).limit(limit))
    ]
    if hits:
        return hits
    # Typo tolerance: model names similar to the query ("cretta" -> Creta), via pg_trgm.
    similar = select(VehicleModel.id).where(func.similarity(VehicleModel.name, query) >= NAME_SIMILARITY)
    stmt = _scoped(
        select(Embedding).where(Embedding.model_id.in_(similar), Embedding.entity_type == "model"),
        model,
        version,
        scope,
    )
    return [_hit(row, 0.0, "keyword") for row in db.execute(stmt.limit(limit)).scalars()]


def rrf(result_lists: Sequence[Sequence[SearchHit]], *, k: int = RRF_K, limit: int = 10) -> list[SearchHit]:
    """Reciprocal rank fusion: ``score = sum(1 / (k + rank))`` over the lists a hit appears in."""
    fused: dict[int, SearchHit] = {}
    scores: dict[int, float] = {}
    for hits in result_lists:
        for rank, hit in enumerate(hits, start=1):
            scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank)
            if hit.id in fused:
                fused[hit.id].sources = sorted(set(fused[hit.id].sources) | set(hit.sources))
            else:
                fused[hit.id] = SearchHit(**{**hit.__dict__, "sources": list(hit.sources)})
    ordered = sorted(fused.values(), key=lambda h: (-scores[h.id], h.id))[:limit]
    for hit in ordered:
        hit.score = round(scores[hit.id], 6)
    return ordered


def hybrid_search(
    db: Session, embedder: EmbeddingProvider | None, query: str, *, scope: SearchScope, limit: int = 10
) -> tuple[list[SearchHit], bool]:
    """Returns (hits, degraded). Degraded means the semantic leg was unavailable."""
    model = embedder.model if embedder is not None else get_settings().embedding_model
    keyword = keyword_search(db, query, model=model, scope=scope, limit=limit * 2)
    if embedder is None:
        return keyword[:limit], True
    try:
        semantic = semantic_search(db, embedder, query, scope=scope, limit=limit * 2)
    except Exception as exc:  # provider down or out of quota: keyword results still answer
        logger.warning("semantic_leg_failed error=%s", exc)
        return keyword[:limit], True
    return rrf([semantic, keyword], limit=limit), False
