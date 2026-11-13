"""Ingestion pipeline: discover → download → extract (→ images → index).

One run at a time, enforced by a PostgreSQL advisory lock; a run that cannot
get the lock is recorded as ``skipped``. Each brochure is its own transaction,
so one failure rolls back only that brochure and the loop moves on. Budgets
bound the paid work per run (LLM extractions, image searches). A quota error
ends the extract stage cleanly; unprocessed brochures stay eligible for the
next run because eligibility is derived from ``extraction_runs``.

CLI::

    python -m autohub.jobs.pipeline --brands hyundai --stages discover,download
    python -m autohub.jobs.pipeline --stages extract --max-extractions 2
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from autohub.assistant.llm import LLMProvider, get_llm_provider
from autohub.catalog.slugs import slugify
from autohub.core.settings import Settings, get_settings
from autohub.db.models import Brand, Brochure, BrochureVersion, ExtractionRun, PipelineRun
from autohub.ingestion.adapters.base import BrandAdapter, DiscoveredBrochure
from autohub.ingestion.adapters.registry import resolve_adapters
from autohub.ingestion.downloader import BrochureDownloader, DownloadStatus
from autohub.ingestion.extraction.extractor import BrochureExtractor
from autohub.ingestion.fetcher import CrawlPolicy, PolitePageFetcher
from autohub.ingestion.store import BrochureStore
from autohub.ingestion.writer import write_extraction
from autohub.jobs.locks import advisory_lock

logger = logging.getLogger(__name__)

STAGES: tuple[str, ...] = ("discover", "download", "extract", "images", "index")
DEFAULT_STAGES: tuple[str, ...] = STAGES


class StageHandler(Protocol):
    """An optional later stage (images, index). Returns stats for the run record."""

    def __call__(self, db: Session, *, brands: list[Brand], budget: int) -> dict[str, Any]: ...


@dataclass
class PipelineOptions:
    brands: list[str] | None = None  # None -> settings.enabled_brand_keys
    stages: tuple[str, ...] = DEFAULT_STAGES
    max_extractions: int | None = None  # None -> settings
    max_image_searches: int | None = None
    trigger: str = "manual"
    triggered_by: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "brands": self.brands,
            "stages": list(self.stages),
            "max_extractions": self.max_extractions,
            "max_image_searches": self.max_image_searches,
        }


class PageFetcherWithPolicy(Protocol):
    policy: CrawlPolicy

    def close(self) -> None: ...


@dataclass
class _Stats:
    discover: dict[str, Any] = field(default_factory=dict)
    download: Counter[str] = field(default_factory=Counter)
    extract: Counter[str] = field(default_factory=Counter)
    errors: list[dict[str, Any]] = field(default_factory=list)
    other: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "discover": self.discover,
            "download": dict(self.download),
            "extract": dict(self.extract),
            "errors": self.errors[:200],
            "error_count": len(self.errors),
            **self.other,
        }


def default_fetcher_factory(adapter: BrandAdapter, settings: Settings) -> PolitePageFetcher:
    return PolitePageFetcher(allowed_domains=adapter.official_domains, settings=settings)


class Pipeline:
    def __init__(
        self,
        *,
        lock_connect: Callable[[], Connection],
        settings: Settings | None = None,
        resolve: Callable[[list[str]], tuple[list[BrandAdapter], list[str]]] = resolve_adapters,
        fetcher_factory: Callable[[BrandAdapter, Settings], Any] = default_fetcher_factory,
        provider_factory: Callable[[], LLMProvider] | None = None,
        store: BrochureStore | None = None,
        stage_handlers: Mapping[str, StageHandler] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._lock_connect = lock_connect
        self._resolve = resolve
        self._fetcher_factory = fetcher_factory
        self._provider_factory = provider_factory or (
            lambda: get_llm_provider(self.settings, model=self.settings.extraction_model)
        )
        self.store = store or BrochureStore(self.settings.data_dir / "brochures")
        self._handlers = dict(stage_handlers or {})

    # ------------------------------------------------------------------ entry points
    def create_run(self, db: Session, options: PipelineOptions) -> PipelineRun:
        run = PipelineRun(
            trigger=options.trigger,
            triggered_by=options.triggered_by,
            status="running",
            options=options.as_dict(),
        )
        db.add(run)
        db.commit()
        return run

    def run(self, db: Session, options: PipelineOptions, *, run_id: int | None = None) -> PipelineRun:
        """Execute a run. ``run_id`` continues a row made by :meth:`create_run` (API path)."""
        run = db.get(PipelineRun, run_id) if run_id is not None else self.create_run(db, options)
        assert run is not None
        with advisory_lock(self._lock_connect) as acquired:
            if not acquired:
                return self._close(db, run, "skipped", None, error="another pipeline run holds the lock")
            stats = _Stats()
            try:
                self._execute(db, run, options, stats)
            except Exception as exc:  # recorded, then re-raised for the caller's logs
                db.rollback()
                self._close(db, run, "failed", stats, error=f"{type(exc).__name__}: {exc}")
                raise
            status = "partial" if stats.errors else "succeeded"
            return self._close(db, run, status, stats)

    # ------------------------------------------------------------------ stages
    def _execute(self, db: Session, run: PipelineRun, options: PipelineOptions, stats: _Stats) -> None:
        unknown_stages = [s for s in options.stages if s not in STAGES]
        if unknown_stages:
            raise ValueError(f"unknown stages: {', '.join(unknown_stages)}")
        keys = options.brands if options.brands is not None else self.settings.enabled_brand_keys
        adapters, unknown = self._resolve(keys)
        if unknown:
            stats.other["unknown_brands"] = unknown
            stats.errors.extend({"stage": "config", "brand": k, "message": "no adapter"} for k in unknown)
        brands = {adapter.key: self._ensure_brand(db, adapter) for adapter in adapters}
        db.commit()

        for adapter in adapters:
            if not {"discover", "download"} & set(options.stages):
                break
            fetcher = self._fetcher_factory(adapter, self.settings)
            try:
                if "discover" in options.stages:
                    self._discover(db, adapter, brands[adapter.key], fetcher, stats)
                if "download" in options.stages:
                    self._download(db, adapter, brands[adapter.key], fetcher.policy, stats)
            finally:
                fetcher.close()

        if "extract" in options.stages:
            budget = (
                options.max_extractions
                if options.max_extractions is not None
                else self.settings.pipeline_max_extractions_per_run
            )
            self._extract(db, run, list(brands.values()), budget, stats)

        for stage in ("images", "index"):
            if stage not in options.stages:
                continue
            handler = self._handlers.get(stage)
            if handler is None:
                stats.other[stage] = {"skipped": "stage not configured"}
                continue
            budget = (
                options.max_image_searches
                if options.max_image_searches is not None
                else self.settings.pipeline_max_image_searches_per_run
            )
            try:
                stats.other[stage] = handler(db, brands=list(brands.values()), budget=budget)
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.exception("pipeline_stage_failed stage=%s", stage)
                stats.errors.append({"stage": stage, "message": f"{type(exc).__name__}: {exc}"})

    @staticmethod
    def _ensure_brand(db: Session, adapter: BrandAdapter) -> Brand:
        brand = db.execute(select(Brand).where(Brand.name == adapter.brand_name)).scalar_one_or_none()
        if brand is None:
            brand = Brand(
                name=adapter.brand_name,
                slug=slugify(adapter.brand_name),
                country=adapter.country,
                website_url=adapter.website_url,
            )
            db.add(brand)
            db.flush()
        elif brand.website_url is None:
            brand.website_url = adapter.website_url
        return brand

    def _discover(
        self, db: Session, adapter: BrandAdapter, brand: Brand, fetcher: Any, stats: _Stats
    ) -> None:
        result = adapter.discover(fetcher)
        now = datetime.now(UTC)
        new = 0
        for found in result.brochures:
            new += self._upsert_brochure(db, adapter, brand, found, now)
        db.commit()
        for error in result.errors:
            stats.errors.append({"stage": "discover", "brand": adapter.key, **error.as_dict()})
        stats.discover[adapter.key] = {
            "found": len(result.brochures),
            "new": new,
            "pages": result.pages_visited,
            "errors": len(result.errors),
        }

    @staticmethod
    def _upsert_brochure(
        db: Session, adapter: BrandAdapter, brand: Brand, found: DiscoveredBrochure, now: datetime
    ) -> int:
        brochure = db.execute(
            select(Brochure).where(Brochure.source_url == found.source_url)
        ).scalar_one_or_none()
        if brochure is None:
            db.add(
                Brochure(
                    brand_id=brand.id,
                    adapter=adapter.key,
                    source_url=found.source_url,
                    source_page_url=found.source_page_url,
                    title=found.title,
                    model_hint=found.model_hint,
                    layout_group=found.layout_group,
                    is_primary_layout=found.is_primary_layout,
                    is_active=True,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            return 1
        brochure.last_seen_at = now
        brochure.is_active = True
        brochure.source_page_url = found.source_page_url
        brochure.title = found.title or brochure.title
        brochure.model_hint = found.model_hint or brochure.model_hint
        brochure.layout_group = found.layout_group
        brochure.is_primary_layout = found.is_primary_layout
        return 0

    @staticmethod
    def _work_brochures(db: Session, brand_ids: Iterable[int]) -> list[Brochure]:
        return list(
            db.execute(
                select(Brochure)
                .where(
                    Brochure.brand_id.in_(list(brand_ids)),
                    Brochure.is_active.is_(True),
                    Brochure.is_primary_layout.is_(True),
                )
                .order_by(Brochure.id)
            ).scalars()
        )

    def _download(
        self, db: Session, adapter: BrandAdapter, brand: Brand, policy: CrawlPolicy, stats: _Stats
    ) -> None:
        downloader = BrochureDownloader(policy=policy, store=self.store)
        for brochure in self._work_brochures(db, [brand.id]):
            try:
                result = downloader.download(db, brochure)
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.exception("pipeline_download_crashed brochure_id=%s", brochure.id)
                stats.download["crashed"] += 1
                stats.errors.append({"stage": "download", "brochure_id": brochure.id, "message": str(exc)})
                continue
            stats.download[result.status.value] += 1
            if result.status in (DownloadStatus.FAILED, DownloadStatus.REJECTED):
                stats.errors.append(
                    {
                        "stage": "download",
                        "brochure_id": brochure.id,
                        "url": brochure.source_url,
                        "reason": f"{result.stage}: {result.reason}",
                    }
                )

    @staticmethod
    def _current_version(db: Session, brochure_id: int) -> BrochureVersion | None:
        return (
            db.execute(
                select(BrochureVersion)
                .where(BrochureVersion.brochure_id == brochure_id)
                .order_by(BrochureVersion.downloaded_at.desc(), BrochureVersion.id.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )

    def _extract(
        self, db: Session, run: PipelineRun, brands: list[Brand], budget: int, stats: _Stats
    ) -> None:
        pending: list[BrochureVersion] = []
        extractor: BrochureExtractor | None = None
        for brochure in self._work_brochures(db, [b.id for b in brands]):
            version = self._current_version(db, brochure.id)
            if version is None:
                continue
            if extractor is None:
                try:
                    provider = self._provider_factory()
                except Exception as exc:  # e.g. no API key configured
                    stats.errors.append({"stage": "extract", "message": f"LLM provider unavailable: {exc}"})
                    return
                extractor = BrochureExtractor(provider, self.store)
            if extractor.needs_extraction(db, version):
                pending.append(version)
        stats.extract["pending"] = len(pending)
        if extractor is None:
            return

        for index, version in enumerate(pending):
            if index >= budget:
                stats.extract["deferred_budget"] = len(pending) - index
                break
            try:
                outcome = extractor.extract(db, version, pipeline_run_id=run.id)
                db.commit()  # the run record survives whatever happens while writing
                if outcome.succeeded:
                    assert outcome.extraction is not None
                    write_extraction(db, version=version, run=outcome.run, extraction=outcome.extraction)
                    db.commit()
            except Exception as exc:
                db.rollback()
                logger.exception("pipeline_extract_crashed version_id=%s", version.id)
                self._mark_run_failed(db, version.id, run.id, exc)
                stats.extract["crashed"] += 1
                stats.errors.append({"stage": "extract", "version_id": version.id, "message": str(exc)})
                continue
            stats.extract[outcome.run.status] += 1
            if outcome.run.status != "succeeded":
                stats.errors.append(
                    {
                        "stage": "extract",
                        "version_id": version.id,
                        "status": outcome.run.status,
                        "error_type": outcome.run.error_type,
                        "message": outcome.run.error_message,
                    }
                )
            if outcome.run.error_type == "quota":
                stats.extract["stopped_on_quota"] = 1
                stats.extract["deferred_quota"] = len(pending) - index - 1
                break

    @staticmethod
    def _mark_run_failed(db: Session, version_id: int, pipeline_run_id: int, exc: Exception) -> None:
        """A crash while writing rolled back the specs; the run row says why."""
        run = (
            db.execute(
                select(ExtractionRun)
                .where(
                    ExtractionRun.brochure_version_id == version_id,
                    ExtractionRun.pipeline_run_id == pipeline_run_id,
                )
                .order_by(ExtractionRun.id.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if run is not None:
            run.status = "failed"
            run.error_type = "write_error"
            run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
            run.finished_at = datetime.now(UTC)
            db.commit()

    @staticmethod
    def _close(
        db: Session, run: PipelineRun, status: str, stats: _Stats | None, *, error: str | None = None
    ) -> PipelineRun:
        run.status = status
        run.stats = stats.as_dict() if stats is not None else None
        run.error = error
        run.finished_at = datetime.now(UTC)
        db.commit()
        logger.info("pipeline_run id=%s status=%s error=%s", run.id, status, error)
        return run


# ---------------------------------------------------------------- production wiring
def build_pipeline(settings: Settings | None = None) -> Pipeline:
    from autohub.db.session import get_engine

    settings = settings or get_settings()
    handlers: dict[str, StageHandler] = {}
    for stage, loader in _OPTIONAL_STAGES.items():
        handler = loader()
        if handler is not None:
            handlers[stage] = handler
    return Pipeline(lock_connect=get_engine().connect, settings=settings, stage_handlers=handlers)


def _optional_stage(module_name: str) -> StageHandler | None:
    """A later stage's ``pipeline_stage`` function, if that module exists yet."""
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return None
    handler: StageHandler | None = getattr(module, "pipeline_stage", None)
    return handler


_OPTIONAL_STAGES: dict[str, Callable[[], StageHandler | None]] = {
    "images": lambda: _optional_stage("autohub.images.service"),
    "index": lambda: _optional_stage("autohub.search.indexer"),
}


def run_pipeline(options: PipelineOptions, *, run_id: int | None = None) -> int:
    """Open a session and execute one run; returns the run id. Used by the API, scheduler and CLI."""
    from autohub.db.session import get_session_factory

    pipeline = build_pipeline()
    db = get_session_factory()()
    try:
        return pipeline.run(db, options, run_id=run_id).id
    finally:
        db.close()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m autohub.jobs.pipeline", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--brands", help="comma-separated adapter keys (default: ENABLED_BRANDS)")
    parser.add_argument("--stages", default=",".join(DEFAULT_STAGES), help=f"comma-separated, from {STAGES}")
    parser.add_argument("--max-extractions", type=int, help="LLM extraction budget for this run")
    parser.add_argument("--max-image-searches", type=int, help="image search budget for this run")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    from autohub.core.logging import configure_logging

    configure_logging()
    args = _parse_args(argv)
    options = PipelineOptions(
        brands=[b for b in args.brands.split(",") if b.strip()] if args.brands else None,
        stages=tuple(s.strip() for s in args.stages.split(",") if s.strip()),
        max_extractions=args.max_extractions,
        max_image_searches=args.max_image_searches,
        trigger="cli",
    )
    from autohub.db.session import get_session_factory

    db = get_session_factory()()
    try:
        run = build_pipeline().run(db, options)
        print(f"pipeline run {run.id}: {run.status}")
        if run.stats:
            import json

            print(json.dumps(run.stats, indent=2, default=str))
        return 0 if run.status in ("succeeded", "partial") else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
