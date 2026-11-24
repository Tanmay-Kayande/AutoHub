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

A brochure whose answer exceeds the output ceiling (Tata Harrier and Safari) is
extracted in parts within the same run: one request lists the variants
(:class:`VariantIndex`), then each batch of ``VARIANT_BATCH_SIZE`` variants is
extracted with the full rules and the answers are merged. A batch that is still
too long is halved. Content that already hit the ceiling under this prompt and
model goes straight to the batched path, so the doomed full request is not repeated.

A PDF above the upload limit is sent as its stored page text. An image-only one (no
text layer; Maruti Brezza, Ertiga) is split into page ranges that each fit
(:mod:`autohub.ingestion.pdf_split`); each range is extracted in its own request, told
the variant names found on earlier pages, and the answers are merged by variant name
with page numbers mapped back to the whole brochure.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.assistant.llm import LLMError, LLMOutputError, LLMProvider
from autohub.db.models import Brochure, BrochurePage, BrochureVersion, ExtractionRun
from autohub.ingestion.extraction.prompt import (
    current_prompt_versions,
    prompt_version,
    system_prompt,
    user_prompt,
)
from autohub.ingestion.extraction.schema import (
    BrochureExtraction,
    ExtractedSpec,
    ExtractedVariant,
    GroupedExtraction,
    VariantIndex,
    VariantOutline,
)
from autohub.ingestion.pdf_split import PdfPart, split_pdf
from autohub.ingestion.store import BrochureStore
from autohub.ingestion.writer import name_tokens
from autohub.specs.taxonomy import get_spec

logger = logging.getLogger(__name__)

# gemini-2.5-flash's output ceiling. Brochures with many variants (Scorpio N, Thar Roxx,
# XUV 3XO) were cut off at 32768; a lower cap only saves tokens on outputs that then fail.
EXTRACTION_MAX_OUTPUT_TOKENS = 65536
RAW_TEXT_LIMIT = 200_000
# Gemini accepts PDFs up to 50 MB. A larger brochure (image-heavy, e.g. the 63 MB Tata
# Tiago file) is sent as its stored page text instead; page numbers are kept in the text.
PDF_UPLOAD_LIMIT_BYTES = 50 * 1024 * 1024
# About 65,000 output tokens fit 30-36 variants of a full spec table; 10 leaves room.
VARIANT_BATCH_SIZE = 10
TRUNCATED = "MAX_TOKENS"

INDEX_INSTRUCTION = (
    "\n\nThis brochure is too large to extract in one answer, so it is done in parts. "
    "In this first part, list every variant only: one entry per purchasable configuration, "
    "named and split exactly as the rules for variants say. Do not extract any specification values."
)


def batch_instruction(names: list[str], *, first: bool) -> str:
    listed = "\n".join(f"- {name}" for name in names)
    common = (
        "Fill common_specs as usual: values identical for every variant of the whole brochure."
        if first
        else "Return common_specs as an empty list; they were extracted in an earlier part."
    )
    return (
        "\n\nThis brochure is too large to extract in one answer, so it is done in parts. "
        f"In this part, extract only these variants, with exactly these names:\n{listed}\n"
        f"Values that differ between any variants of the brochure go under each of these variants. {common}"
    )


def page_range_instruction(part: PdfPart, number: int, total: int, page_count: int, known: list[str]) -> str:
    text = (
        f"\n\nThis brochure is too large to attach whole, so its pages are sent in {total} parts. "
        f"This is part {number}: brochure pages {part.first_page}-{part.last_page} of {page_count}. "
        "Extract only what these pages show, and give each value's page number within this attachment. "
        "If these pages have no specification or feature table, set contains_specifications to false "
        "and return empty lists."
    )
    if known:
        listed = "\n".join(f"- {name}" for name in known)
        text += (
            f"\nEarlier pages listed these variants:\n{listed}\n"
            "When these pages give values for any of them, or for a trim or powertrain they share, "
            "use exactly these names; add a variant only if it is not in this list."
        )
    return text


def shift_pages(extraction: BrochureExtraction, part: PdfPart) -> BrochureExtraction:
    """Attachment page numbers -> whole-brochure page numbers (unknown when out of range)."""

    def shifted(specs: list[ExtractedSpec]) -> list[ExtractedSpec]:
        return [
            s.model_copy(
                update={
                    "page": s.page + part.first_page - 1
                    if s.page is not None and 1 <= s.page <= part.page_count
                    else None
                }
            )
            for s in specs
        ]

    return extraction.model_copy(
        update={
            "common_specs": shifted(extraction.common_specs),
            "variants": [v.model_copy(update={"specs": shifted(v.specs)}) for v in extraction.variants],
        }
    )


def merge_page_ranges(parts: list[BrochureExtraction]) -> BrochureExtraction:
    """One extraction from the answers for each page range, in page order.

    Variants with the same name (order-free) are one variant: their values are joined,
    and the cleaner keeps the first value of a repeated key. Ranges without a
    specification table contribute only the model name and body type.
    """
    merged = BrochureExtraction(
        model_name=None, body_type=None, contains_specifications=False, common_specs=[], variants=[]
    )
    by_name: dict[tuple[str, ...], ExtractedVariant] = {}
    for part in parts:
        merged.model_name = merged.model_name or part.model_name
        merged.body_type = merged.body_type or part.body_type
        if not part.contains_specifications:
            continue
        merged.contains_specifications = True
        merged.common_specs.extend(part.common_specs)
        for variant in part.variants:
            key = name_tokens(variant.name)
            existing = by_name.get(key)
            if existing is None:
                by_name[key] = variant.model_copy(update={"specs": list(variant.specs)})
                merged.variants.append(by_name[key])
                continue
            existing.specs.extend(variant.specs)
            existing.fuel_type = existing.fuel_type or variant.fuel_type
            existing.transmission = existing.transmission or variant.transmission
            existing.drivetrain = existing.drivetrain or variant.drivetrain
    return merged


def _only_listed(
    variants: list[ExtractedVariant], batch: list[VariantOutline], done: list[ExtractedVariant]
) -> list[ExtractedVariant]:
    """Keep the batch's own variants (a model may add others it saw) and never repeat one.
    Names are compared order-free and the listed name is kept; the outline's powertrain
    fields fill what the part left empty."""
    wanted = {name_tokens(o.name): o for o in batch}
    seen = {name_tokens(v.name) for v in done}
    kept: list[ExtractedVariant] = []
    for variant in variants:
        key = name_tokens(variant.name)
        outline = wanted.get(key)
        if outline is None or key in seen:
            continue
        seen.add(key)
        kept.append(
            variant.model_copy(
                update={
                    "name": outline.name,
                    "fuel_type": variant.fuel_type or outline.fuel_type,
                    "transmission": variant.transmission or outline.transmission,
                    "drivetrain": variant.drivetrain or outline.drivetrain,
                }
            )
        )
    return kept


@dataclass
class _Spend:
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


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
                ExtractionRun.prompt_version.in_(current_prompt_versions()),
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
        parts: list[PdfPart] = []
        split_dir: tempfile.TemporaryDirectory[str] | None = None
        # A provider may accept less than Gemini's 50 MB (Vertex AI: inline PDFs only).
        provider_limit = getattr(self.provider, "max_pdf_bytes", None)
        upload_limit = min(PDF_UPLOAD_LIMIT_BYTES, provider_limit or PDF_UPLOAD_LIMIT_BYTES)
        if path.stat().st_size > upload_limit:
            page_text = self._page_text(db, version)
            if page_text:
                files = []
                prompt = (
                    f"{prompt}\n\nThe PDF is too large to attach. Its text follows, page by page; "
                    f"each page starts with a [Page N] line.\n\n{page_text}"
                )
            else:
                split_dir = tempfile.TemporaryDirectory(prefix="autohub-split-")
                parts = split_pdf(path, upload_limit, Path(split_dir.name)) or []
                if not parts:
                    split_dir.cleanup()
                    return self._finish(
                        db,
                        outcome,
                        "failed",
                        "file_too_large",
                        f"{path.stat().st_size} bytes exceeds the PDF upload limit, the file has no "
                        "text layer and it cannot be split into page ranges that fit",
                    )

        spend = _Spend()
        known_truncated = self._hit_ceiling_before(db, version)
        try:
            raw: dict[str, Any] | None = None
            if parts:
                value = self._in_page_ranges(prompt, parts, spend, known_truncated, version.id)
            else:
                value, raw = self._whole(prompt, files, spend, known_truncated, version.id)
        except LLMOutputError as exc:
            self._record_spend(run, spend, at_least_one=True)
            run.raw_output = {"text": exc.raw_text[:RAW_TEXT_LIMIT], "finish_reason": exc.finish_reason}
            return self._finish(db, outcome, "invalid", exc.error_type, str(exc))
        except LLMError as exc:
            self._record_spend(run, spend)
            return self._finish(db, outcome, "failed", exc.error_type, str(exc))
        finally:
            if split_dir is not None:
                split_dir.cleanup()

        self._record_spend(run, spend)
        extraction, unknown = clean_extraction(value)
        # Grouped answers are stored as answered; extraction_from_raw expands them again.
        run.raw_output = raw if raw is not None else value.model_dump(mode="json")
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

    def _call[T: BaseModel](self, prompt: str, files: list[Path], schema: type[T], spend: _Spend) -> T:
        try:
            result = self.provider.generate_structured(
                system=system_prompt(),
                prompt=prompt,
                schema=schema,
                files=files,
                max_output_tokens=self.max_output_tokens,
            )
        except LLMOutputError:
            spend.attempts += 1
            raise
        spend.attempts += result.attempts
        spend.input_tokens += result.usage.input_tokens
        spend.output_tokens += result.usage.output_tokens
        return result.value

    def _whole(
        self, prompt: str, files: list[Path], spend: _Spend, known_truncated: bool, version_id: int
    ) -> tuple[BrochureExtraction, dict[str, Any] | None]:
        """One document in one grouped answer, or in variant batches when that is cut off.
        Returns the extraction and, for a grouped answer, its raw form to store."""
        if known_truncated:
            logger.info("extraction_in_parts version=%s reason=earlier_run_truncated", version_id)
            return self._in_parts(prompt, files, spend), None
        try:
            grouped = self._call(prompt, files, GroupedExtraction, spend)
        except LLMOutputError as exc:
            if exc.finish_reason != TRUNCATED:
                raise
            logger.info("extraction_in_parts version=%s reason=truncated", version_id)
            return self._in_parts(prompt, files, spend), None
        return grouped.expand(), grouped.model_dump(mode="json")

    def _in_page_ranges(
        self, prompt: str, parts: list[PdfPart], spend: _Spend, known_truncated: bool, version_id: int
    ) -> BrochureExtraction:
        page_count = parts[-1].last_page
        answers: list[BrochureExtraction] = []
        known: list[str] = []
        seen: set[tuple[str, ...]] = set()
        for number, part in enumerate(parts, 1):
            logger.info(
                "extraction_page_range version=%s part=%s/%s pages=%s-%s bytes=%s",
                version_id,
                number,
                len(parts),
                part.first_page,
                part.last_page,
                part.path.stat().st_size,
            )
            instruction = page_range_instruction(part, number, len(parts), page_count, known)
            value, _ = self._whole(prompt + instruction, [part.path], spend, known_truncated, version_id)
            answers.append(shift_pages(value, part))
            for variant in value.variants:
                key = name_tokens(variant.name)
                if key not in seen and value.contains_specifications:
                    seen.add(key)
                    known.append(variant.name)
        return merge_page_ranges(answers)

    def _in_parts(self, prompt: str, files: list[Path], spend: _Spend) -> BrochureExtraction:
        index = self._call(prompt + INDEX_INSTRUCTION, files, VariantIndex, spend)
        merged = BrochureExtraction(
            model_name=index.model_name,
            body_type=index.body_type,
            contains_specifications=index.contains_specifications,
            common_specs=[],
            variants=[],
        )
        if not index.contains_specifications or not index.variants:
            return merged
        outlines = list({o.name: o for o in index.variants}.values())  # names must be unique
        pending = [outlines[i : i + VARIANT_BATCH_SIZE] for i in range(0, len(outlines), VARIANT_BATCH_SIZE)]
        first = True
        while pending:
            batch = pending.pop(0)
            try:
                part = self._call(
                    prompt + batch_instruction([o.name for o in batch], first=first),
                    files,
                    BrochureExtraction,
                    spend,
                )
            except LLMOutputError as exc:
                if exc.finish_reason != TRUNCATED or len(batch) == 1:
                    raise
                half = len(batch) // 2
                pending[:0] = [batch[:half], batch[half:]]
                continue
            if first:
                merged.common_specs = part.common_specs
                first = False
            merged.variants.extend(_only_listed(part.variants, batch, merged.variants))
        return merged

    def _hit_ceiling_before(self, db: Session, version: BrochureVersion) -> bool:
        """Whether this content (by digest, within the brand) was cut off at the output
        ceiling under the current prompt and model."""
        brand_id = db.execute(
            select(Brochure.brand_id).where(Brochure.id == version.brochure_id)
        ).scalar_one()
        found = db.execute(
            select(ExtractionRun.id)
            .join(BrochureVersion, BrochureVersion.id == ExtractionRun.brochure_version_id)
            .join(Brochure, Brochure.id == BrochureVersion.brochure_id)
            .where(
                BrochureVersion.sha256 == version.sha256,
                Brochure.brand_id == brand_id,
                ExtractionRun.prompt_version.in_(current_prompt_versions()),
                ExtractionRun.llm_model == self.provider.model,
                ExtractionRun.status == "invalid",
                ExtractionRun.raw_output["finish_reason"].astext == TRUNCATED,
            )
            .limit(1)
        ).first()
        return found is not None

    @staticmethod
    def _record_spend(run: ExtractionRun, spend: _Spend, *, at_least_one: bool = False) -> None:
        """Totals over every request of the run (one, or the parts of a large brochure)."""
        run.attempts = max(spend.attempts, 1) if at_least_one else spend.attempts
        if spend.input_tokens or spend.output_tokens:
            run.input_tokens = spend.input_tokens
            run.output_tokens = spend.output_tokens

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
