"""Incremental embedding index.

Builds the documents the catalogue should have, compares content hashes with
what is stored for the active ``(embedding_model, embedding_version)``, embeds
only new or changed documents, and deletes documents that no longer exist. A
different model or version is simply another key: its set is built alongside,
and :func:`prune_other_generations` removes the old one once the new is complete.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import Table, and_, delete, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from autohub.core.settings import Settings, get_settings
from autohub.db.models import Brand, Embedding
from autohub.ingestion.pdf_text import backfill_pages
from autohub.ingestion.store import BrochureStore
from autohub.search.documents import Document, build_documents, load_snapshot
from autohub.search.embedder import EmbeddingProvider, get_embedder

logger = logging.getLogger(__name__)

EMBED_BATCH = 100
# Core table: the ORM attribute for the 'metadata' column is 'meta' (Base.metadata is taken).
EMBEDDINGS = cast(Table, Embedding.__table__)


@dataclass
class IndexStats:
    documents: int = 0
    embedded: int = 0
    unchanged: int = 0
    deleted: int = 0
    embedding_calls: int = 0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def index_catalogue(db: Session, embedder: EmbeddingProvider, *, version: str) -> IndexStats:
    stats = IndexStats()
    documents = build_documents(load_snapshot(db))
    stats.documents = len(documents)
    stored = {
        (row.entity_type, row.entity_id, row.chunk_index): row.content_hash
        for row in db.execute(
            select(
                Embedding.entity_type, Embedding.entity_id, Embedding.chunk_index, Embedding.content_hash
            ).where(Embedding.embedding_model == embedder.model, Embedding.embedding_version == version)
        )
    }
    wanted = {d.key: d for d in documents}
    todo: list[Document] = []
    for key, document in wanted.items():
        if stored.get(key) == document.content_hash:
            stats.unchanged += 1
        else:
            todo.append(document)

    for start in range(0, len(todo), EMBED_BATCH):
        batch = todo[start : start + EMBED_BATCH]
        vectors = embedder.embed_documents([d.content for d in batch])
        stats.embedding_calls += 1
        for document, vector in zip(batch, vectors, strict=True):
            values = {
                "entity_type": document.entity_type,
                "entity_id": document.entity_id,
                "chunk_index": document.chunk_index,
                "content": document.content,
                "content_hash": document.content_hash,
                "metadata": document.meta,
                "brand_id": document.brand_id,
                "model_id": document.model_id,
                "variant_id": document.variant_id,
                "embedding": vector,
                "embedding_model": embedder.model,
                "embedding_version": version,
            }
            statement = insert(EMBEDDINGS).values(values)
            db.execute(
                statement.on_conflict_do_update(
                    constraint="uq_embeddings_entity_chunk_model",
                    set_={
                        k: statement.excluded[k]
                        for k in values
                        if k
                        not in (
                            "entity_type",
                            "entity_id",
                            "chunk_index",
                            "embedding_model",
                            "embedding_version",
                        )
                    },
                )
            )
        stats.embedded += len(batch)
        db.commit()  # each batch is paid for; keep it even if a later batch fails

    stale = [key for key in stored if key not in wanted]
    for start in range(0, len(stale), 500):
        chunk = stale[start : start + 500]
        result = db.execute(
            delete(Embedding).where(
                Embedding.embedding_model == embedder.model,
                Embedding.embedding_version == version,
                tuple_(Embedding.entity_type, Embedding.entity_id, Embedding.chunk_index).in_(chunk),
            )
        )
        stats.deleted += int(getattr(result, "rowcount", 0) or 0)
    db.commit()
    logger.info("index_done %s", stats.as_dict())
    return stats


def prune_other_generations(db: Session, *, model: str, version: str) -> int:
    """Delete embeddings of any other model/version (run after the active set is complete)."""
    result = db.execute(
        delete(Embedding).where(
            or_(Embedding.embedding_model != model, Embedding.embedding_version != version)
        )
    )
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def active_filter(settings: Settings | None = None) -> Any:
    settings = settings or get_settings()
    return and_(
        Embedding.embedding_model == settings.embedding_model,
        Embedding.embedding_version == settings.embedding_version,
    )


def pipeline_stage(db: Session, *, brands: list[Brand], budget: int) -> dict[str, Any]:
    """The pipeline's ``index`` stage: page text for new versions, then the incremental index."""
    settings = get_settings()
    pages = backfill_pages(db, BrochureStore(settings.data_dir / "brochures"))
    stats = index_catalogue(db, get_embedder(settings), version=settings.embedding_version)
    return {"pages_extracted": pages, **stats.as_dict()}
