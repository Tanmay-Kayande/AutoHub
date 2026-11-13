"""Bring the search index up to date (embeds only new or changed documents).

python -m tools.reindex                 # page text backfill + incremental index
python -m tools.reindex --prune         # afterwards, drop other model/version sets
"""

from __future__ import annotations

import argparse
import sys

from autohub.core.logging import configure_logging
from autohub.core.settings import get_settings
from autohub.db.session import get_session_factory
from autohub.ingestion.pdf_text import backfill_pages
from autohub.ingestion.store import BrochureStore
from autohub.search.embedder import get_embedder
from autohub.search.indexer import index_catalogue, prune_other_generations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--prune", action="store_true", help="delete embeddings of other models/versions")
    args = parser.parse_args(argv)
    configure_logging()
    settings = get_settings()
    db = get_session_factory()()
    try:
        pages = backfill_pages(db, BrochureStore(settings.data_dir / "brochures"))
        embedder = get_embedder(settings)
        stats = index_catalogue(db, embedder, version=settings.embedding_version)
        pruned = (
            prune_other_generations(db, model=embedder.model, version=settings.embedding_version)
            if args.prune
            else 0
        )
    finally:
        db.close()
    print(f"pages extracted: {pages}")
    print(
        f"documents: {stats.documents} embedded: {stats.embedded} unchanged: {stats.unchanged} deleted: {stats.deleted}"
    )
    print(f"embedding calls: {stats.embedding_calls} pruned: {pruned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
