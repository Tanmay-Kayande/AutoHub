"""Re-extract brochures under the current prompt and model, then write them in a second step.

Splitting the two keeps paid model calls apart from catalogue changes: ``extract`` stores
each run's output and writes nothing; ``write`` replays the stored outputs through the
normal writer (including retiring superseded variants) and is a dry run unless ``--commit``.

    python -m tools.reextract extract [--brands kia,tata] [--models venue,thar] [--limit N]
    python -m tools.reextract write [--brands ...] [--report out.json]       # dry run
    python -m tools.reextract write [--brands ...] --commit

Both steps take the pipeline lock, so they never overlap a pipeline run.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autohub.assistant.llm import extraction_rate_limit, get_llm_provider
from autohub.core.logging import configure_logging
from autohub.core.settings import Settings, get_settings
from autohub.db.models import Brand, BrochureVersion, ExtractionRun, ModelGeneration, Variant, VehicleImage
from autohub.db.session import get_engine, get_session_factory
from autohub.ingestion.extraction.extractor import BrochureExtractor, clean_extraction
from autohub.ingestion.extraction.prompt import current_prompt_versions
from autohub.ingestion.extraction.schema import extraction_from_raw
from autohub.ingestion.store import BrochureStore
from autohub.ingestion.writer import write_extraction
from autohub.jobs.locks import advisory_lock
from autohub.jobs.pipeline import Pipeline


def _versions(
    db: Session, brands: list[str] | None, models: list[str] | None = None
) -> list[BrochureVersion]:
    """Current version of every active primary brochure, optionally for some brands or model hints."""
    query = select(Brand.id)
    if brands:
        query = query.where(func.lower(Brand.name).in_([b.strip().lower() for b in brands]))
    brand_ids = list(db.execute(query).scalars())
    versions = []
    wanted = {m.strip().lower() for m in models} if models else None
    for brochure in Pipeline._work_brochures(db, brand_ids):
        if wanted is not None and (brochure.model_hint or "").strip().lower() not in wanted:
            continue
        version = Pipeline._current_version(db, brochure.id)
        if version is not None:
            versions.append(version)
    return versions


def _pending_run(db: Session, version: BrochureVersion, model: str) -> ExtractionRun | None:
    """Latest succeeded run of this version under a current prompt and the model, if not yet written."""
    return db.execute(
        select(ExtractionRun)
        .where(
            ExtractionRun.brochure_version_id == version.id,
            ExtractionRun.prompt_version.in_(current_prompt_versions()),
            ExtractionRun.llm_model == model,
            ExtractionRun.status == "succeeded",
            ExtractionRun.specs_written.is_(None),
        )
        .order_by(ExtractionRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def extract(
    db: Session,
    settings: Settings,
    brands: list[str] | None,
    limit: int | None,
    models: list[str] | None = None,
) -> int:
    provider = get_llm_provider(
        settings,
        model=settings.extraction_model,
        timeout_seconds=settings.extraction_timeout_seconds,
        max_attempts=settings.extraction_max_attempts,
        thinking_level=settings.extraction_thinking_level,
        flex=settings.extraction_flex,
        rate_limit=extraction_rate_limit(settings),
    )
    extractor = BrochureExtractor(provider, BrochureStore(settings.brochure_dir))
    done = 0
    seen: set[tuple[int, str]] = set()
    for version in _versions(db, brands, models):
        if limit is not None and done >= limit:
            break
        brochure = version.brochure
        if (brochure.brand_id, version.sha256) in seen or not extractor.needs_extraction(db, version):
            continue
        seen.add((brochure.brand_id, version.sha256))
        outcome = extractor.extract(db, version)
        db.commit()
        done += 1
        run = outcome.run
        print(
            f"{brochure.model_hint!s:24} run {run.id} {run.status:9} variants={run.variants_found} "
            f"tokens={run.input_tokens}/{run.output_tokens} {run.error_type or ''}",
            flush=True,
        )
        if run.error_type == "quota":
            print("stopped: quota exhausted")
            break
    print(f"extracted: {done}")
    return 0


def _retired_details(db: Session, names: list[str], model_id: int) -> list[dict[str, Any]]:
    details = []
    for name in names:
        variant = (
            db.execute(
                select(Variant)
                .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
                .where(
                    Variant.name == name, ModelGeneration.model_id == model_id, Variant.is_active.is_(False)
                )
            )
            .scalars()
            .first()
        )
        if variant is None:
            continue
        images = db.execute(
            select(func.count()).select_from(VehicleImage).where(VehicleImage.variant_id == variant.id)
        ).scalar_one()
        details.append(
            {
                "name": name,
                "id": variant.id,
                "images": int(images),
                "price": str(variant.price_ex_showroom) if variant.price_ex_showroom is not None else None,
            }
        )
    return details


def write(
    db: Session,
    settings: Settings,
    brands: list[str] | None,
    commit: bool,
    models: list[str] | None = None,
) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for version in _versions(db, brands, models):
        run = _pending_run(db, version, settings.extraction_model)
        if run is None:
            continue
        extraction, _ = clean_extraction(extraction_from_raw(run.raw_output or {}))
        summary = write_extraction(db, version=version, run=run, extraction=extraction)
        db.flush()
        retired_details = _retired_details(db, summary.retired, summary.model_id)
        entry: dict[str, Any] = {
            "brochure_id": version.brochure_id,
            "model_hint": version.brochure.model_hint,
            "title": version.brochure.title,
            "run_id": run.id,
            "variants_created": summary.variants_created,
            "variants_matched": summary.variants_matched,
            "ambiguous_skipped": summary.ambiguous_variants,
            "retired": retired_details,
            "retire_skipped": summary.retire_skipped,
            "specs_written": summary.result.written,
            "specs_unchanged": summary.result.unchanged,
            "conflicts_skipped": summary.result.conflicts_skipped,
        }
        report.append(entry)
        retired = ", ".join(r["name"] for r in retired_details) or "-"
        print(
            f"{entry['model_hint']!s:24} created={summary.variants_created:<3} matched={summary.variants_matched:<3} "
            f"retired={len(summary.retired):<3} written={summary.result.written:<5} "
            f"conflicts={summary.result.conflicts_skipped:<4} {summary.retire_skipped or ''}\n"
            f"    retired: {retired}",
            flush=True,
        )
        if commit:
            db.commit()
    if not commit:
        db.rollback()
    print(f"brochures: {len(report)} ({'committed' if commit else 'dry run, rolled back'})")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=("extract", "write"))
    parser.add_argument("--brands", help="comma-separated brand names (default: all)")
    parser.add_argument("--models", help="comma-separated model hints, e.g. Venue,Thar (default: all)")
    parser.add_argument("--limit", type=int, help="extract: at most this many brochures")
    parser.add_argument("--commit", action="store_true", help="write: apply the changes")
    parser.add_argument("--report", type=Path, help="write: save the per-brochure report as JSON")
    args = parser.parse_args(argv)
    configure_logging()
    settings = get_settings()
    brands = [b for b in args.brands.split(",") if b.strip()] if args.brands else None
    models = [m for m in args.models.split(",") if m.strip()] if args.models else None
    with advisory_lock(get_engine().connect) as acquired:
        if not acquired:
            print("a pipeline run holds the lock; try again later", file=sys.stderr)
            return 1
        db = get_session_factory()()
        try:
            if args.step == "extract":
                return extract(db, settings, brands, args.limit, models)
            report = write(db, settings, brands, args.commit, models)
        finally:
            db.close()
    if args.report:
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
