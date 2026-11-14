"""Run the LLM over one brochure version and record the attempt.

Every call creates an ``extraction_runs`` row, whatever happens:

=============  ====================================================  ==============
status         when                                                  error_type
=============  ====================================================  ==============
succeeded      valid output with at least one variant or common spec  —
empty          valid output, but no specification table              no_spec_table
invalid        the model answered with unusable output               invalid_output
failed         provider error, or the PDF is missing from the store  quota, timeout,
                                                                     unavailable,
                                                                     request,
                                                                     file_missing
=============  ====================================================  ==============

The extractor never writes specifications; on success it returns the cleaned
:class:`BrochureExtraction` for the writer. Unknown keys are dropped here and
counted on the run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.assistant.llm import LLMError, LLMOutputError, LLMProvider
from autohub.db.models import Brochure, BrochureVersion, ExtractionRun
from autohub.ingestion.extraction.prompt import prompt_version, system_prompt, user_prompt
from autohub.ingestion.extraction.schema import BrochureExtraction, ExtractedSpec
from autohub.ingestion.store import BrochureStore
from autohub.specs.taxonomy import get_spec

logger = logging.getLogger(__name__)

EXTRACTION_MAX_OUTPUT_TOKENS = 32768
RAW_TEXT_LIMIT = 200_000


@dataclass
class ExtractionOutcome:
    run: ExtractionRun
    extraction: BrochureExtraction | None = None
    unknown_keys: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.run.status == "succeeded" and self.extraction is not None


def _clean(specs: list[ExtractedSpec], unknown: list[str]) -> list[ExtractedSpec]:
    """Drop unknown keys (recorded) and repeated keys (first one wins)."""
    seen: set[str] = set()
    kept: list[ExtractedSpec] = []
    for spec in specs:
        key = spec.key.strip()
        if get_spec(key) is None:
            unknown.append(key)
            continue
        if key in seen:
            continue
        seen.add(key)
        kept.append(spec.model_copy(update={"key": key}))
    return kept


def clean_extraction(extraction: BrochureExtraction) -> tuple[BrochureExtraction, list[str]]:
    unknown: list[str] = []
    variants = [
        v.model_copy(update={"name": v.name.strip(), "specs": _clean(v.specs, unknown)})
        for v in extraction.variants
        if v.name.strip()
    ]
    cleaned = extraction.model_copy(
        update={"common_specs": _clean(extraction.common_specs, unknown), "variants": variants}
    )
    return cleaned, unknown


class BrochureExtractor:
    def __init__(
        self,
        provider: LLMProvider,
        store: BrochureStore,
        *,
        max_output_tokens: int = EXTRACTION_MAX_OUTPUT_TOKENS,
    ) -> None:
        self.provider = provider
        self.store = store
        self.max_output_tokens = max_output_tokens

    def needs_extraction(self, db: Session, version: BrochureVersion) -> bool:
        """False when this version already has a succeeded (or empty) run for this prompt and model."""
        done = db.execute(
            select(ExtractionRun.id)
            .where(
                ExtractionRun.brochure_version_id == version.id,
                ExtractionRun.prompt_version == prompt_version(),
                ExtractionRun.llm_model == self.provider.model,
                ExtractionRun.status.in_(("succeeded", "empty")),
            )
            .limit(1)
        ).first()
        return done is None

    def extract(
        self, db: Session, version: BrochureVersion, *, pipeline_run_id: int | None = None
    ) -> ExtractionOutcome:
        brochure = db.get(Brochure, version.brochure_id)
        assert brochure is not None
        run = ExtractionRun(
            brochure_version_id=version.id,
            pipeline_run_id=pipeline_run_id,
            llm_provider=self.provider.name,
            llm_model=self.provider.model,
            prompt_version=prompt_version(),
            status="running",
            attempts=0,
        )
        db.add(run)
        db.flush()
        outcome = ExtractionOutcome(run=run)

        path = self.store.path_for(version.sha256)
        if not path.is_file():
            return self._finish(db, outcome, "failed", "file_missing", f"stored file {path.name} not found")

        try:
            result = self.provider.generate_structured(
                system=system_prompt(),
                prompt=user_prompt(brand=brochure.brand.name, model_hint=brochure.model_hint),
                schema=BrochureExtraction,
                files=[path],
                max_output_tokens=self.max_output_tokens,
            )
        except LLMOutputError as exc:
            run.attempts = 1
            run.raw_output = {"text": exc.raw_text[:RAW_TEXT_LIMIT], "finish_reason": exc.finish_reason}
            return self._finish(db, outcome, "invalid", exc.error_type, str(exc))
        except LLMError as exc:
            return self._finish(db, outcome, "failed", exc.error_type, str(exc))

        run.attempts = result.attempts
        run.input_tokens = result.usage.input_tokens
        run.output_tokens = result.usage.output_tokens
        extraction, unknown = clean_extraction(result.value)
        run.raw_output = result.value.model_dump(mode="json")
        run.unknown_keys = len(unknown)
        run.variants_found = len(extraction.variants)
        outcome.unknown_keys = unknown

        has_content = bool(extraction.variants) or bool(extraction.common_specs)
        if not extraction.contains_specifications or not has_content:
            return self._finish(
                db, outcome, "empty", "no_spec_table", "the brochure has no specification table"
            )
        outcome.extraction = extraction
        return self._finish(db, outcome, "succeeded", None, None)

    @staticmethod
    def _finish(
        db: Session, outcome: ExtractionOutcome, status: str, error_type: str | None, message: str | None
    ) -> ExtractionOutcome:
        run = outcome.run
        run.status = status
        run.error_type = error_type
        run.error_message = message[:2000] if message else None
        if status != "succeeded":
            run.finished_at = datetime.now(UTC)
        db.flush()
        log = logger.info if status == "succeeded" else logger.warning
        log(
            "extraction_run id=%s version=%s status=%s error_type=%s variants=%s unknown_keys=%s",
            run.id,
            run.brochure_version_id,
            status,
            error_type,
            run.variants_found,
            run.unknown_keys,
        )
        return outcome
