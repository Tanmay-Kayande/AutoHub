"""Re-parse stored numeric specs that have wording but no number, after normaliser fixes.

Only rows with ``value_num IS NULL`` on number definitions are considered; the
original wording is parsed again and, if a number now comes out, written back
through the guarded writer (same source, so the old row goes to history).
No LLM call is made.

    python -m tools.renormalize_specs            # dry run
    python -m tools.renormalize_specs --commit
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.db.models import SpecDefinition, VariantSpec
from autohub.db.session import get_session_factory
from autohub.ingestion.extraction.schema import ExtractedSpec
from autohub.ingestion.normalize import Provenance, normalize_spec
from autohub.specs.service import SpecValueInput, apply_spec_values


@dataclass(frozen=True)
class RenormalizeResult:
    candidates: int
    parseable: int
    written: int


def renormalize(db: Session, *, commit: bool) -> RenormalizeResult:
    """Find number specs stored as wording only and re-parse them. Writes (no commit) only if ``commit``."""
    rows = db.execute(
        select(VariantSpec, SpecDefinition)
        .join(SpecDefinition, SpecDefinition.id == VariantSpec.spec_definition_id)
        .where(
            SpecDefinition.data_type == "number",
            VariantSpec.value_num.is_(None),
            VariantSpec.status == "present",
            VariantSpec.original_value.is_not(None),
            VariantSpec.brochure_version_id.is_not(None),
        )
    ).all()
    fixed: dict[int, list[SpecValueInput]] = defaultdict(list)
    for row, definition in rows:
        item = ExtractedSpec(
            key=definition.key,
            value=row.original_value or "",
            unit=row.original_unit,
            status="present",
            page=row.source_page,
        )
        value = normalize_spec(
            item,
            Provenance(brochure_version_id=row.brochure_version_id or 0, source_type=row.source_type),
        )
        if value is not None and value.value_num is not None:
            fixed[row.variant_id].append(value)
            print(
                f"variant {row.variant_id} {definition.key}: {row.original_value!r} -> {value.value_num} {value.unit}"
            )
    written = 0
    if commit:
        for variant_id, values in fixed.items():
            written += apply_spec_values(
                db, variant_id, values, reason="re-normalised after parser fix"
            ).written
    return RenormalizeResult(len(rows), sum(len(v) for v in fixed.values()), written)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args(argv)
    db = get_session_factory()()
    try:
        result = renormalize(db, commit=args.commit)
        if args.commit:
            db.commit()
        print(f"candidates: {result.candidates} parseable now: {result.parseable} written: {result.written}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
