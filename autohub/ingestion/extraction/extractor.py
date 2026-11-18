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
from autohub.db.models import Brochure, BrochurePage, BrochureVersion, ExtractionRun
from autohub.ingestion.extraction.prompt import prompt_version, system_prompt, user_prompt
from autohub.ingestion.extraction.schema import BrochureExtraction, ExtractedSpec
from autohub.ingestion.store import BrochureStore
from autohub.specs.taxonomy import get_spec

logger = logging.getLogger(__name__)

# gemini-2.5-flash's output ceiling. Brochures with many variants (Scorpio N, Thar Roxx,
# XUV 3XO) were cut off at 32768; a lower cap only saves tokens on outputs that then fail.
EXTRACTION_MAX_OUTPUT_TOKENS = 65536
RAW_TEXT_LIMIT = 200_000
# Gemini accepts PDFs up to 50 MB. A larger brochure (image-heavy, e.g. the 63 MB Tata
# Tiago file) is sent as its stored page text instead; page numbers are kept in the text.
PDF_UPLOAD_LIMIT_BYTES = 50 * 1024 * 1024


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
        """False when this content already has a succeeded (or empty) run for this prompt and model.

        Content means the file's digest within the brand, not the version row: a site can
        publish one PDF under two links (Tata's "Aeris" and "Aeris iCNG" brochures are the
        same file), and extracting it twice would spend a request to write the same values.
        """
        brand_id = db.execute(
            select(Brochure.brand_id).where(Brochure.id == version.brochure_id)
        ).scalar_one()
        done = db.execute(
            select(ExtractionRun.id)
            .join(BrochureVersion, BrochureVersion.id == ExtractionRun.brochure_version_id)
            .join(Brochure, Brochure.id == BrochureVersion.brochure_id)
            .where(
                BrochureVersion.sha256 == version.sha256,
                Brochure.brand_id == brand_id,
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

        prompt = user_prompt(brand=brochure.brand.name, model_hint=brochure.model_hint)
        files = [path]
        if path.stat().st_size > PDF_UPLOAD_LIMIT_BYTES:
            page_text = self._page_text(db, version)
            if not page_text:
                return self._finish(
                    db,
                    outcome,
                    "failed",
                    "file_too_large",
                    f"{path.stat().st_size} bytes exceeds the PDF upload limit and the file has no text layer",
                )
            files = []
            prompt = (
                f"{prompt}\n\nThe PDF is too large to attach. Its text follows, page by page; "
                f"each page starts with a [Page N] line.\n\n{page_text}"
            )

        try:
            result = self.provider.generate_structured(
                system=system_prompt(),
                prompt=prompt,
                schema=BrochureExtraction,
                files=files,
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
    def _page_text(db: Session, version: BrochureVersion) -> str:
        """The version's stored page text with page markers, or "" if it has none."""
        rows = db.execute(
            select(BrochurePage.page_number, BrochurePage.content)
            .where(BrochurePage.brochure_version_id == version.id)
            .order_by(BrochurePage.page_number)
        ).all()
        return "\n\n".join(f"[Page {number}]\n{content}" for number, content in rows if content.strip())

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
