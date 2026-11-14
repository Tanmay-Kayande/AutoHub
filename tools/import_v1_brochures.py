"""Import the brochure PDFs V1 already downloaded, so V2 does not fetch them again.

Reads V1's download metadata and ``checksums.json``, verifies every file's
SHA-256 against the V1 record, **copies** it into the V2 content-addressed
store and creates ``brochures`` / ``brochure_versions`` rows. V1 files are only
read. Nothing is marked as extracted (V2 uses a different prompt and schema).

Imported brochures are stored with ``is_active = false``: the next discovery
run reactivates those still published on the brand site, so superseded V1
documents are kept for provenance but never re-extracted.

    python -m tools.import_v1_brochures            # dry run: verify and report
    python -m tools.import_v1_brochures --commit   # write to DATABASE_URL

Re-running is safe: existing brochures and versions are left as they are.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.core.settings import REPO_ROOT, get_settings
from autohub.db.models import Brand, Brochure, BrochureVersion
from autohub.ingestion.adapters.mahindra import (
    _layout_rank,
    canonical_brochure_url,
    layout_group_for,
    model_hint_from_file,
)
from autohub.ingestion.downloader import inspect_pdf
from autohub.ingestion.store import BrochureStore

V1_ROOT = REPO_ROOT / "autohub" / "automation" / "brochures"
V1_METADATA = V1_ROOT / "data" / "metadata" / "brochure_download_metadata.json"
V1_CHECKSUMS = V1_ROOT / "checksums.json"
V1_PDFS = V1_ROOT / "data" / "pdfs"
ADAPTERS = {"Mahindra": "mahindra"}


@dataclass
class Item:
    brand: str
    url: str
    canonical_url: str
    path: Path
    expected_sha256: str
    downloaded_at: datetime


@dataclass
class Report:
    checked: int = 0
    hash_ok: int = 0
    brochures_created: int = 0
    versions_created: int = 0
    files_stored: int = 0
    problems: list[str] | None = None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_items(metadata_path: Path, checksums_path: Path, pdf_root: Path) -> tuple[list[Item], list[str]]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    items: list[Item] = []
    problems: list[str] = []
    for entry in metadata:
        recorded = PureWindowsPath(entry["file_path"])
        try:
            relative = recorded.relative_to(
                PureWindowsPath(*recorded.parts[: recorded.parts.index("pdfs") + 1])
            )
        except ValueError:
            problems.append(f"unexpected file path {entry['file_path']}")
            continue
        key = str(relative)  # V1 keys use backslashes
        record = checksums.get(key)
        if record is None:
            problems.append(f"no checksum record for {key}")
            continue
        items.append(
            Item(
                brand=entry["brand"],
                url=entry["brochure_url"],
                canonical_url=canonical_brochure_url(entry["brochure_url"]),
                path=pdf_root.joinpath(*relative.parts),
                expected_sha256=record["checksum"],
                downloaded_at=datetime.fromisoformat(record["timestamp"]),
            )
        )
    return items, problems


def import_items(db: Session, items: list[Item], store: BrochureStore, *, write: bool) -> Report:
    report = Report(problems=[])
    assert report.problems is not None
    groups: dict[str, list[Item]] = {}
    for item in items:
        groups.setdefault(layout_group_for(item.canonical_url), []).append(item)
    primary = {min(g, key=lambda i: _layout_rank(i.canonical_url)).canonical_url for g in groups.values()}

    for item in items:
        report.checked += 1
        if not item.path.is_file():
            report.problems.append(f"missing file {item.path}")
            continue
        actual = sha256_of(item.path)
        if actual != item.expected_sha256:
            report.problems.append(f"hash mismatch {item.path}: {actual} != {item.expected_sha256}")
            continue
        report.hash_ok += 1
        if not write:
            continue

        adapter = ADAPTERS.get(item.brand)
        brand = db.execute(select(Brand).where(Brand.name == item.brand)).scalar_one_or_none()
        if adapter is None or brand is None:
            report.problems.append(f"no adapter or brand row for {item.brand}")
            continue
        info = inspect_pdf(item.path)

        if not store.exists(actual):
            temp = store.new_temp_path()
            shutil.copyfile(item.path, temp)
            if sha256_of(temp) != actual:
                temp.unlink(missing_ok=True)
                report.problems.append(f"copy of {item.path} does not match its hash")
                continue
            store.adopt(temp, actual)
            report.files_stored += 1

        brochure = db.execute(
            select(Brochure).where(Brochure.source_url == item.canonical_url)
        ).scalar_one_or_none()
        if brochure is None:
            brochure = Brochure(
                brand_id=brand.id,
                adapter=adapter,
                source_url=item.canonical_url,
                title=None,
                model_hint=model_hint_from_file(item.canonical_url),
                layout_group=layout_group_for(item.canonical_url),
                is_primary_layout=item.canonical_url in primary,
                is_active=False,
                first_seen_at=item.downloaded_at,
                last_seen_at=item.downloaded_at,
            )
            db.add(brochure)
            db.flush()
            report.brochures_created += 1

        exists = db.execute(
            select(BrochureVersion.id).where(
                BrochureVersion.brochure_id == brochure.id, BrochureVersion.sha256 == actual
            )
        ).first()
        if exists is None:
            db.add(
                BrochureVersion(
                    brochure_id=brochure.id,
                    sha256=actual,
                    file_path=store.key_for(actual),
                    file_size=item.path.stat().st_size,
                    page_count=info.page_count,
                    has_text_layer=info.has_text_layer,
                    downloaded_at=item.downloaded_at,
                )
            )
            report.versions_created += 1
    db.flush()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", action="store_true", help="write rows and files (default: dry run)")
    args = parser.parse_args(argv)

    items, problems = load_items(V1_METADATA, V1_CHECKSUMS, V1_PDFS)
    before = {
        item.path: (item.path.stat().st_mtime_ns, item.path.stat().st_size)
        for item in items
        if item.path.is_file()
    }
    store = BrochureStore(get_settings().data_dir / "brochures")

    from autohub.db.session import session_scope

    with session_scope() as db:
        report = import_items(db, items, store, write=args.commit)
        if not args.commit:
            db.rollback()

    after = {
        item.path: (item.path.stat().st_mtime_ns, item.path.stat().st_size)
        for item in items
        if item.path.is_file()
    }
    all_problems = problems + (report.problems or [])
    print(f"V1 entries:            {len(items)}")
    print(f"hash matches V1 record: {report.hash_ok}/{report.checked}")
    print(f"brochures created:     {report.brochures_created}")
    print(f"versions created:      {report.versions_created}")
    print(f"files stored:          {report.files_stored}")
    print(f"V1 files untouched:    {before == after}")
    print(f"mode:                  {'commit' if args.commit else 'dry run'}")
    for problem in all_problems:
        print(f"PROBLEM: {problem}")
    return 0 if not all_problems and before == after else 1


if __name__ == "__main__":
    sys.exit(main())
