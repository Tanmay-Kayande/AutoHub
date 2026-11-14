"""Migrate AutoHub V1 data (SQLite) into the V2 schema (PostgreSQL).

Design and safety rules: docs/V2/MIGRATION_PLAN.md.

* The SQLite file is opened read-only and its SHA-256 is checked before and after.
* Everything is written and validated inside one transaction; any failed check
  rolls the whole migration back.
* ``--dry-run`` does the full migration and validation, then rolls back.

Usage::

    python -m tools.migrate_sqlite_to_postgres --target postgresql://.../autohub_dev [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine, func, insert, select, text
from sqlalchemy.engine import make_url

from autohub.catalog.slugs import unique_slug
from autohub.catalog.vocab import (
    normalize_body_type,
    normalize_drivetrain,
    normalize_fuel_type,
    normalize_transmission_type,
)
from autohub.db import models as m
from autohub.images.urls import domain_of, url_hash
from autohub.specs.units import ParsedQuantity, parse_at_rpm, parse_quantity

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "autohub.db"
DEFAULT_REPORT_DIR = REPO_ROOT / "docs" / "V2" / "reports"
DEFAULT_BACKUP_DIR = REPO_ROOT / "backups"
# The pre-V2 PostgreSQL database; never a migration target unless explicitly allowed.
DEFAULT_PROTECTED = ("localhost:5432/autohub", "127.0.0.1:5432/autohub")

LEGACY_CONFIDENCE = Decimal("0.50")
SOURCE_TABLES = (
    "users",
    "car_brands",
    "car_models",
    "car_variants",
    "car_specs",
    "car_images",
    "news",
    "news_images",
)
SEQUENCE_TABLES = (
    "users", "brands", "vehicle_models", "model_generations", "variants", "variant_specs",
    "vehicle_images", "news", "news_images",
)  # fmt: skip


class MigrationError(RuntimeError):
    """The migration cannot proceed or failed validation."""


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class Report:
    started_at: str
    source_path: str
    source_sha256: str
    target: str
    dry_run: bool
    resume: bool
    backup_path: str | None = None
    source_counts: dict[str, int] = field(default_factory=dict)
    target_counts: dict[str, int] = field(default_factory=dict)
    inserted: dict[str, int] = field(default_factory=dict)
    skipped_existing: dict[str, int] = field(default_factory=dict)
    flags: list[dict[str, Any]] = field(default_factory=list)
    possible_duplicate_variants: list[dict[str, Any]] = field(default_factory=list)
    unmapped: list[dict[str, Any]] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    outcome: str = "pending"

    def flag(self, table: str, row_id: int, flag: str, detail: str) -> None:
        self.flags.append({"table": table, "id": row_id, "flag": flag, "detail": detail})

    def check(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(Check(name, passed, detail))

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)


# --------------------------------------------------------------------------- source
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_source(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Read every V1 table through a read-only connection."""
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise MigrationError(f"SQLite integrity_check failed: {integrity}")
        existing = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        data: dict[str, list[dict[str, Any]]] = {}
        for table in SOURCE_TABLES:
            if table not in existing:
                data[table] = []
                continue
            data[table] = [dict(r) for r in connection.execute(f'SELECT * FROM "{table}" ORDER BY id')]
        return data
    finally:
        connection.close()


def backup_source(path: Path, sha256: str, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"{path.name}.{stamp}.{sha256[:12]}.bak"
    shutil.copy2(path, destination)
    if sha256_file(destination) != sha256:
        raise MigrationError(f"Backup verification failed for {destination}")
    return destination


# ------------------------------------------------------------------------ transforms
def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _num_text(value: Any) -> str:
    """Stable text form of a V1 float (``2184.0`` stays ``"2184.0"``)."""
    return repr(float(value)) if isinstance(value, int | float) else str(value)


def _spec_row(
    *,
    variant_id: int,
    definition_id: int,
    original: str,
    parsed: ParsedQuantity | None = None,
    value_text: str | None = None,
    flags: tuple[str, ...] = (),
    now: datetime,
) -> dict[str, Any]:
    all_flags = sorted({*flags, *(parsed.flags if parsed else ())})
    value_num = parsed.value if parsed else None
    return {
        "variant_id": variant_id,
        "spec_definition_id": definition_id,
        "value_num": value_num,
        "value_num_max": parsed.value_max if parsed else None,
        # A 'present' row must carry a value: keep the text when no number could be derived.
        "value_text": value_text if value_text is not None else (original if value_num is None else None),
        "value_bool": None,
        "unit": parsed.unit if (parsed and value_num is not None) else None,
        "original_value": original,
        "original_unit": parsed.original_unit if parsed else None,
        "status": "present",
        "source_type": "legacy_v1",
        "confidence": LEGACY_CONFIDENCE,
        "flags": all_flags or None,
        "extracted_at": now,
    }


def build_spec_rows(
    spec: dict[str, Any],
    *,
    fuel_type: str | None,
    definition_ids: dict[str, int],
    report: Report,
    now: datetime,
) -> list[dict[str, Any]]:
    """Turn one V1 ``car_specs`` row (5 columns) into V2 ``variant_specs`` rows."""
    rows: list[dict[str, Any]] = []
    variant_id = spec["variant_id"]
    spec_id = spec["id"]

    def add(key: str, **kwargs: Any) -> None:
        row = _spec_row(variant_id=variant_id, definition_id=definition_ids[key], now=now, **kwargs)
        rows.append(row)
        for flag in row["flags"] or ():
            report.flag("car_specs", spec_id, flag, f"{key}: {kwargs['original']!r}")

    if spec.get("engine"):
        add("engine_name", original=str(spec["engine"]), value_text=str(spec["engine"]))

    capacity = spec.get("engine_capacity")
    if capacity is not None:
        original = _num_text(capacity)
        value = Decimal(str(capacity))
        if value > 50:
            parsed = ParsedQuantity(value=value, unit="cc", flags=("unit_assumed",))
        elif 0 < value <= 10:
            parsed = ParsedQuantity(
                value=value * 1000, unit="cc", original_unit="l", flags=("unit_inferred_litres",)
            )
        else:
            parsed = ParsedQuantity(flags=("unit_ambiguous",))
        add("engine_displacement", original=original, parsed=parsed)

    for column, key, rpm_key, unit in (
        ("power", "max_power", "max_power_rpm", "kW"),
        ("torque", "max_torque", "max_torque_rpm", "Nm"),
    ):
        raw = spec.get(column)
        if not raw:
            continue
        add(key, original=str(raw), parsed=parse_quantity(str(raw), unit))
        rpm = parse_at_rpm(str(raw))
        if rpm.ok:
            add(rpm_key, original=str(raw), parsed=rpm, flags=("derived_from_combined_field",))

    mileage = spec.get("mileage")
    if mileage is not None:
        original = _num_text(mileage)
        value = Decimal(str(mileage))
        if fuel_type == "electric":
            # V1 stored an EV's range in the fuel-mileage column (audit Q-2).
            parsed = ParsedQuantity(
                value=value, unit="km", flags=("reclassified_from_mileage", "unit_assumed")
            )
            add("ev_range", original=original, parsed=parsed)
        else:
            parsed = ParsedQuantity(value=value, unit="km/l", flags=("unit_assumed",))
            add("mileage_arai", original=original, parsed=parsed)
    return rows


def expected_spec_fields(spec: dict[str, Any]) -> int:
    return sum(
        1
        for column in ("engine", "engine_capacity", "power", "torque", "mileage")
        if spec.get(column) not in (None, "")
    )


def find_possible_duplicates(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Heuristic: same model and fuel, and one name equals the other plus only
    transmission / drivetrain descriptor words (the shape produced when two
    brochures of one car describe the same variant differently).

    Reported for review, never merged. Sibling trims that differ only by
    drivetrain (e.g. "... AT" vs "... AWD AT") also match and are false positives.
    """
    import re

    descriptors = {"mt", "at", "amt", "manual", "automatic", "4wd", "2wd", "awd", "rwd", "fwd", "4x4", "4x2"}

    def tokens(name: str) -> set[str]:
        return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}

    found: list[dict[str, Any]] = []
    by_model: dict[int, list[dict[str, Any]]] = {}
    for variant in variants:
        by_model.setdefault(variant["model_id"], []).append(variant)
    for model_id, group in by_model.items():
        for index, first in enumerate(group):
            for second in group[index + 1 :]:
                if (first.get("fuel_type") or "") != (second.get("fuel_type") or ""):
                    continue
                a, b = tokens(first["variant_name"]), tokens(second["variant_name"])
                if not a or not b:
                    continue
                jaccard = len(a & b) / len(a | b)
                extra = (a ^ b) if (a <= b or b <= a) else None
                if extra and extra <= descriptors:
                    found.append(
                        {
                            "model_id": model_id,
                            "variant_ids": [first["id"], second["id"]],
                            "names": [first["variant_name"], second["variant_name"]],
                            "token_overlap": round(jaccard, 2),
                        }
                    )
    return found


def parse_published_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------- target
def describe_target(url: str) -> str:
    parsed = make_url(url)
    return f"{parsed.host}:{parsed.port or 5432}/{parsed.database}"


def assert_target_allowed(url: str, protected: tuple[str, ...]) -> None:
    target = describe_target(url)
    if make_url(url).get_backend_name() != "postgresql":
        raise MigrationError("Target must be a PostgreSQL database.")
    if target in protected:
        raise MigrationError(
            f"Target {target} is protected. Use the V2 development database (see docs/V2/MIGRATION_PLAN.md)."
        )


def assert_schema_ready(connection: Connection) -> None:
    present = set(
        connection.execute(text("select tablename from pg_tables where schemaname = 'public'")).scalars()
    )
    required = {
        "brands",
        "vehicle_models",
        "model_generations",
        "variants",
        "variant_specs",
        "vehicle_images",
    }
    if not required <= present:
        raise MigrationError("Target schema is not at the V2 revision. Run: alembic upgrade head")
    if connection.execute(text("select count(*) from spec_definitions")).scalar_one() == 0:
        raise MigrationError("Spec taxonomy is not seeded. Run: alembic upgrade head")


def _existing_ids(connection: Connection, table: Any) -> set[int]:
    return set(connection.execute(select(table.c.id)).scalars())


def _insert_missing(
    connection: Connection, table: Any, rows: list[dict[str, Any]], report: Report, label: str
) -> None:
    existing = _existing_ids(connection, table)
    fresh = [row for row in rows if row["id"] not in existing]
    if fresh:
        connection.execute(insert(table), fresh)
    report.inserted[label] = len(fresh)
    report.skipped_existing[label] = len(rows) - len(fresh)


def migrate(connection: Connection, source: dict[str, list[dict[str, Any]]], report: Report) -> None:
    now = datetime.now(UTC)
    definition_ids: dict[str, int] = dict(
        connection.execute(select(m.SpecDefinition.key, m.SpecDefinition.id)).tuples().all()
    )

    # users ------------------------------------------------------------------
    users = [
        {
            "id": u["id"], "name": u["name"], "email": u["email"], "gender": u.get("gender"),
            "location": u.get("location"), "hashed_password": u["hashed_password"],
            "role": "user", "is_active": True,
        }
        for u in source["users"]
    ]  # fmt: skip
    _insert_missing(connection, m.User.__table__, users, report, "users")

    # brands -----------------------------------------------------------------
    brand_slugs: set[str] = set()
    brands = []
    for b in source["car_brands"]:
        slug = unique_slug(b["name"], brand_slugs)
        brand_slugs.add(slug)
        brands.append({"id": b["id"], "name": b["name"], "slug": slug})
    _insert_missing(connection, m.Brand.__table__, brands, report, "brands")

    # models + one generation each ------------------------------------------
    model_slugs: dict[int, set[str]] = {}
    models, generations = [], []
    for row in source["car_models"]:
        taken = model_slugs.setdefault(row["brand_id"], set())
        slug = unique_slug(row["name"], taken)
        taken.add(slug)
        body_type = normalize_body_type(row.get("body_type"))
        if row.get("body_type") and body_type is None:
            report.unmapped.append(
                {"table": "car_models", "id": row["id"], "field": "body_type", "value": row["body_type"]}
            )
        if body_type is not None:
            # V1 hard-coded "SUV" for every model (audit D-08): keep it, but say so.
            report.flag("car_models", row["id"], "legacy_unverified", f"body_type={row['body_type']!r}")
        description = row.get("description")
        if row.get("launch_date"):
            report.flag("car_models", row["id"], "launch_date_not_migrated", str(row["launch_date"]))
        models.append(
            {"id": row["id"], "brand_id": row["brand_id"], "name": row["name"], "slug": slug,
             "body_type": body_type, "description": description}
        )  # fmt: skip
        generations.append({"id": row["id"], "model_id": row["id"], "name": "Current", "is_current": True})
    _insert_missing(connection, m.VehicleModel.__table__, models, report, "vehicle_models")
    _insert_missing(connection, m.ModelGeneration.__table__, generations, report, "model_generations")

    # variants ---------------------------------------------------------------
    variant_slugs: dict[int, set[str]] = {}
    variants = []
    fuel_by_variant: dict[int, str | None] = {}
    for row in source["car_variants"]:
        taken = variant_slugs.setdefault(row["model_id"], set())
        slug = unique_slug(row["variant_name"], taken)
        taken.add(slug)
        fuel = normalize_fuel_type(row.get("fuel_type")) or normalize_fuel_type(row["variant_name"])
        transmission = normalize_transmission_type(row.get("transmission")) or normalize_transmission_type(
            row["variant_name"]
        )
        drivetrain = normalize_drivetrain(row.get("transmission"), row["variant_name"])
        for field_name, raw, code in (
            ("fuel_type", row.get("fuel_type"), fuel),
            ("transmission", row.get("transmission"), transmission),
        ):
            if raw and code is None:
                report.unmapped.append(
                    {"table": "car_variants", "id": row["id"], "field": field_name, "value": raw}
                )
        if row.get("price") is not None:
            report.flag("car_variants", row["id"], "price_without_source", _num_text(row["price"]))
        fuel_by_variant[row["id"]] = fuel
        variants.append(
            {
                "id": row["id"],
                "generation_id": row["model_id"],  # generation id == model id by construction
                "name": row["variant_name"],
                "slug": slug,
                "fuel_type": fuel,
                "transmission_type": transmission,
                "drivetrain": drivetrain,
                "fuel_type_raw": row.get("fuel_type"),
                "transmission_raw": row.get("transmission"),
                "price_ex_showroom": _dec(row.get("price")),
                "price_currency": "INR" if row.get("price") is not None else None,
                "price_source": "legacy_v1 (source not recorded)" if row.get("price") is not None else None,
            }
        )
    _insert_missing(connection, m.Variant.__table__, variants, report, "variants")
    report.possible_duplicate_variants = find_possible_duplicates(source["car_variants"])

    # specs ------------------------------------------------------------------
    already = set(
        connection.execute(select(m.VariantSpec.variant_id, m.VariantSpec.spec_definition_id)).all()
    )
    spec_rows: list[dict[str, Any]] = []
    for spec in source["car_specs"]:
        spec_rows.extend(
            build_spec_rows(
                spec,
                fuel_type=fuel_by_variant.get(spec["variant_id"]),
                definition_ids=definition_ids,
                report=report,
                now=now,
            )
        )
    fresh_specs = [r for r in spec_rows if (r["variant_id"], r["spec_definition_id"]) not in already]
    if fresh_specs:
        connection.execute(insert(m.VariantSpec), fresh_specs)
    report.inserted["variant_specs"] = len(fresh_specs)
    report.skipped_existing["variant_specs"] = len(spec_rows) - len(fresh_specs)

    # images -----------------------------------------------------------------
    images = []
    seen_hashes: set[tuple[int, str]] = set()
    for row in source["car_images"]:
        digest = url_hash(row["image_url"])
        key = (row["model_id"], digest)
        if key in seen_hashes:
            report.flag("car_images", row["id"], "duplicate_skipped", row["image_url"][:120])
            continue
        seen_hashes.add(key)
        category = row.get("image_type") if row.get("image_type") in m.IMAGE_CATEGORIES else "other"
        if category == "other":
            report.flag("car_images", row["id"], "category_unknown", str(row.get("image_type")))
        images.append(
            {
                "id": row["id"], "model_id": row["model_id"], "generation_id": row["model_id"],
                "category": category, "image_url": row["image_url"], "url_hash": digest,
                "source_domain": domain_of(row["image_url"]), "source_kind": "legacy_v1",
                "is_official": False, "validation_status": "unvalidated", "is_primary": False,
                "sort_order": row["id"],
            }
        )  # fmt: skip
    _insert_missing(connection, m.VehicleImage.__table__, images, report, "vehicle_images")

    # news -------------------------------------------------------------------
    news_slugs: set[str] = set()
    news = []
    for row in source["news"]:
        slug = unique_slug(row["title"], news_slugs)
        news_slugs.add(slug)
        news.append(
            {"id": row["id"], "title": row["title"], "slug": slug, "content": row["content"],
             "published_at": parse_published_at(row["published_at"])}
        )  # fmt: skip
    _insert_missing(connection, m.News.__table__, news, report, "news")
    news_images = [
        {"id": r["id"], "image_url": r["image_url"], "news_id": r["news_id"]} for r in source["news_images"]
    ]
    _insert_missing(connection, m.NewsImage.__table__, news_images, report, "news_images")

    # sequences --------------------------------------------------------------
    for table in SEQUENCE_TABLES:
        connection.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table}), 1), "
                f"(SELECT COUNT(*) > 0 FROM {table}))"
            )
        )


# ------------------------------------------------------------------------ validation
def validate(connection: Connection, source: dict[str, list[dict[str, Any]]], report: Report) -> None:
    def count(table: Any) -> int:
        return int(connection.execute(select(func.count()).select_from(table)).scalar_one())

    def ids_subset(table: Any, source_rows: list[dict[str, Any]]) -> bool:
        return {r["id"] for r in source_rows} <= _existing_ids(connection, table)

    report.source_counts = {table: len(rows) for table, rows in source.items()}
    skipped_duplicate_images = sum(1 for f in report.flags if f["flag"] == "duplicate_skipped")
    pairs = (
        ("users", m.User.__table__, len(source["users"])),
        ("brands", m.Brand.__table__, len(source["car_brands"])),
        ("vehicle_models", m.VehicleModel.__table__, len(source["car_models"])),
        ("model_generations", m.ModelGeneration.__table__, len(source["car_models"])),
        ("variants", m.Variant.__table__, len(source["car_variants"])),
        ("vehicle_images", m.VehicleImage.__table__, len(source["car_images"]) - skipped_duplicate_images),
        ("news", m.News.__table__, len(source["news"])),
        ("news_images", m.NewsImage.__table__, len(source["news_images"])),
    )
    for label, table, expected in pairs:
        actual = count(table)
        report.target_counts[label] = actual
        # With --resume the target may legitimately hold more rows than the source.
        passed = actual >= expected if report.resume else actual == expected
        report.check(f"row count: {label}", passed, f"source={expected} target={actual}")
    report.target_counts["variant_specs"] = count(m.VariantSpec.__table__)

    # primary keys preserved, natural keys equal
    report.check(
        "primary keys preserved",
        all(
            ids_subset(table, rows)
            for table, rows in (
                (m.User.__table__, source["users"]),
                (m.Brand.__table__, source["car_brands"]),
                (m.VehicleModel.__table__, source["car_models"]),
                (m.Variant.__table__, source["car_variants"]),
                (m.News.__table__, source["news"]),
            )
        ),
        "every source id exists in the target",
    )
    target_models = {
        r.id: (r.name, r.brand_id)
        for r in connection.execute(select(m.VehicleModel.id, m.VehicleModel.name, m.VehicleModel.brand_id))
    }
    mismatched_models = [
        r["id"] for r in source["car_models"] if target_models.get(r["id"]) != (r["name"], r["brand_id"])
    ]
    report.check(
        "model name and brand equal per id", not mismatched_models, f"mismatches={mismatched_models}"
    )

    # relationships: variant -> generation -> model equals the V1 model_id
    variant_model: dict[int, int] = dict(
        connection.execute(
            select(m.Variant.id, m.ModelGeneration.model_id).join(
                m.ModelGeneration, m.Variant.generation_id == m.ModelGeneration.id
            )
        )
        .tuples()
        .all()
    )
    bad_variants = [r["id"] for r in source["car_variants"] if variant_model.get(r["id"]) != r["model_id"]]
    report.check("variant -> model relationships", not bad_variants, f"mismatches={bad_variants}")

    variant_names: dict[int, str] = dict(
        connection.execute(select(m.Variant.id, m.Variant.name)).tuples().all()
    )
    bad_names = [r["id"] for r in source["car_variants"] if variant_names.get(r["id"]) != r["variant_name"]]
    report.check("variant names equal per id", not bad_names, f"mismatches={bad_names}")

    raw_fields = {
        r.id: (r.fuel_type_raw, r.transmission_raw)
        for r in connection.execute(select(m.Variant.id, m.Variant.fuel_type_raw, m.Variant.transmission_raw))
    }
    bad_raw = [
        r["id"]
        for r in source["car_variants"]
        if raw_fields.get(r["id"]) != (r.get("fuel_type"), r.get("transmission"))
    ]
    report.check("raw fuel/transmission text preserved verbatim", not bad_raw, f"mismatches={bad_raw}")

    image_rows = {
        r.id: (r.model_id, r.image_url)
        for r in connection.execute(
            select(m.VehicleImage.id, m.VehicleImage.model_id, m.VehicleImage.image_url)
        )
    }
    skipped_ids = {f["id"] for f in report.flags if f["flag"] == "duplicate_skipped"}
    bad_images = [
        r["id"]
        for r in source["car_images"]
        if r["id"] not in skipped_ids and image_rows.get(r["id"]) != (r["model_id"], r["image_url"])
    ]
    report.check("image -> model relationships and URLs", not bad_images, f"mismatches={bad_images}")

    # specs: each non-null V1 field is represented by a row carrying the original text
    originals: dict[int, list[str]] = {}
    derived = "derived_from_combined_field"
    for row in connection.execute(
        select(m.VariantSpec.variant_id, m.VariantSpec.original_value, m.VariantSpec.flags).where(
            m.VariantSpec.source_type == "legacy_v1"
        )
    ):
        if derived in (row.flags or ()):
            continue
        originals.setdefault(row.variant_id, []).append(row.original_value)
    missing_specs: list[str] = []
    for spec in source["car_specs"]:
        have = list(originals.get(spec["variant_id"], []))
        for column in ("engine", "engine_capacity", "power", "torque", "mileage"):
            value = spec.get(column)
            if value in (None, ""):
                continue
            expected_text = _num_text(value) if column in ("engine_capacity", "mileage") else str(value)
            if expected_text in have:
                have.remove(expected_text)
            else:
                missing_specs.append(f"car_specs.id={spec['id']}.{column}")
    expected_total = sum(expected_spec_fields(s) for s in source["car_specs"])
    report.check(
        "every V1 spec value preserved verbatim",
        not missing_specs,
        f"expected_values={expected_total} missing={missing_specs[:10]}",
    )

    # password hashes byte-equal
    hashes: dict[int, str] = dict(
        connection.execute(select(m.User.id, m.User.hashed_password)).tuples().all()
    )
    bad_hashes = [u["id"] for u in source["users"] if hashes.get(u["id"]) != u["hashed_password"]]
    report.check("password hashes identical", not bad_hashes, f"mismatches={bad_hashes}")

    # orphans (belt and braces; foreign keys already enforce these)
    orphan_sql = {
        "variants without generation": "select count(*) from variants v left join model_generations g on g.id = v.generation_id where g.id is null",
        "specs without variant": "select count(*) from variant_specs s left join variants v on v.id = s.variant_id where v.id is null",
        "images without model": "select count(*) from vehicle_images i left join vehicle_models m on m.id = i.model_id where m.id is null",
        "models without generation": "select count(*) from vehicle_models m left join model_generations g on g.model_id = m.id where g.id is null",
    }
    orphans = {name: connection.execute(text(sql)).scalar_one() for name, sql in orphan_sql.items()}
    report.check("no orphaned rows", not any(orphans.values()), json.dumps(orphans))

    # sequences ahead of data
    behind = []
    for table_name in SEQUENCE_TABLES:
        max_id, last_value, is_called = connection.execute(
            text(
                f"select (select coalesce(max(id), 0) from {table_name}), last_value, is_called "
                f"from {table_name}_id_seq"
            )
        ).one()
        next_value = last_value + 1 if is_called else last_value
        if next_value <= max_id:
            behind.append(table_name)
    report.check("sequences ahead of max(id)", not behind, f"behind={behind}")


# ---------------------------------------------------------------------------- report
def render_markdown(report: Report) -> str:
    lines = [
        "# SQLite → PostgreSQL migration report",
        "",
        f"- **Outcome:** {report.outcome}",
        f"- **Started (UTC):** {report.started_at}",
        f"- **Mode:** {'dry run (rolled back)' if report.dry_run else 'real run'}{' + resume' if report.resume else ''}",
        f"- **Source:** `{report.source_path}`",
        f"- **Source SHA-256:** `{report.source_sha256}`",
        f"- **Backup:** `{report.backup_path or 'not taken'}`",
        f"- **Target:** `{report.target}`",
        "",
        "## Row counts",
        "",
        "| V1 table | Source rows | V2 table | Target rows | Inserted this run | Already present |",
        "|---|---:|---|---:|---:|---:|",
    ]
    mapping = (
        ("users", "users"), ("car_brands", "brands"), ("car_models", "vehicle_models"),
        ("(derived)", "model_generations"), ("car_variants", "variants"), ("car_specs", "variant_specs"),
        ("car_images", "vehicle_images"), ("news", "news"), ("news_images", "news_images"),
    )  # fmt: skip
    for v1, v2 in mapping:
        lines.append(
            f"| {v1} | {report.source_counts.get(v1, '—')} | {v2} | {report.target_counts.get(v2, '—')} "
            f"| {report.inserted.get(v2, 0)} | {report.skipped_existing.get(v2, 0)} |"
        )
    lines += [
        "",
        "`car_specs` holds one row of five columns per variant; `variant_specs` holds one row per value "
        "(plus derived rpm rows), so the counts differ by design.",
        "",
        "## Validation",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for check in report.checks:
        lines.append(f"| {check.name} | {'PASS' if check.passed else '**FAIL**'} | {check.detail} |")

    lines += ["", "## Flags", ""]
    if report.flags:
        summary: dict[str, int] = {}
        for item in report.flags:
            summary[item["flag"]] = summary.get(item["flag"], 0) + 1
        lines += ["| Flag | Rows |", "|---|---:|"]
        lines += [f"| `{name}` | {total} |" for name, total in sorted(summary.items())]
        lines += [
            "",
            "<details><summary>All flagged rows</summary>",
            "",
            "| Table | Id | Flag | Detail |",
            "|---|---:|---|---|",
        ]
        lines += [
            f"| {i['table']} | {i['id']} | `{i['flag']}` | {str(i['detail']).replace('|', '\\|')} |"
            for i in report.flags
        ]
        lines += ["", "</details>"]
    else:
        lines.append("None.")

    lines += ["", "## Values that could not be mapped to a code (raw text kept)", ""]
    if report.unmapped:
        lines += ["| Table | Id | Field | Raw value |", "|---|---:|---|---|"]
        lines += [
            f"| {u['table']} | {u['id']} | {u['field']} | {str(u['value']).replace('|', '\\|')} |"
            for u in report.unmapped
        ]
    else:
        lines.append("None.")

    lines += ["", "## Possible duplicate variants (reported, not merged)", ""]
    if report.possible_duplicate_variants:
        lines += ["| Model id | Variant ids | Names | Token overlap |", "|---:|---|---|---:|"]
        lines += [
            f"| {d['model_id']} | {d['variant_ids']} | {' ⟷ '.join(d['names'])} | {d['token_overlap']} |"
            for d in report.possible_duplicate_variants
        ]
    else:
        lines.append("None detected.")
    return "\n".join(lines) + "\n"


def write_report(report: Report, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.started_at.replace(":", "").replace("-", "").split(".")[0]
    suffix = "_dryrun" if report.dry_run else ""
    base = report_dir / f"migration_report_{stamp}{suffix}"
    base.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    payload = {**report.__dict__, "checks": [c.__dict__ for c in report.checks]}
    base.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return base.with_suffix(".md")


# ------------------------------------------------------------------------------ main
def run(
    *,
    source_path: Path,
    target_url: str,
    dry_run: bool = False,
    resume: bool = False,
    backup_dir: Path | None = DEFAULT_BACKUP_DIR,
    report_dir: Path | None = DEFAULT_REPORT_DIR,
    protected: tuple[str, ...] = DEFAULT_PROTECTED,
) -> Report:
    if not source_path.is_file():
        raise MigrationError(f"Source SQLite file not found: {source_path}")
    assert_target_allowed(target_url, protected)

    source_hash = sha256_file(source_path)
    report = Report(
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        source_path=str(source_path),
        source_sha256=source_hash,
        target=describe_target(target_url),
        dry_run=dry_run,
        resume=resume,
    )
    if backup_dir is not None and not dry_run:
        report.backup_path = str(backup_source(source_path, source_hash, backup_dir))

    source = read_source(source_path)
    engine = create_engine(target_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert_schema_ready(connection)
                populated = {
                    label: n
                    for label, table in (
                        ("users", m.User.__table__),
                        ("brands", m.Brand.__table__),
                        ("vehicle_models", m.VehicleModel.__table__),
                        ("variants", m.Variant.__table__),
                    )
                    if (n := connection.execute(select(func.count()).select_from(table)).scalar_one())
                }
                if populated and not resume:
                    raise MigrationError(
                        f"Target already contains data {populated}. Use --resume to add only missing rows."
                    )
                migrate(connection, source, report)
                validate(connection, source, report)
                source_unchanged = sha256_file(source_path) == source_hash
                report.check("source SQLite file unchanged", source_unchanged, f"sha256={source_hash[:16]}…")
                if not report.ok:
                    transaction.rollback()
                    report.outcome = "FAILED — rolled back"
                elif dry_run:
                    transaction.rollback()
                    report.outcome = "DRY RUN OK — rolled back"
                else:
                    transaction.commit()
                    report.outcome = "SUCCESS — committed"
            except Exception:
                transaction.rollback()
                report.outcome = "ERROR — rolled back"
                raise
    finally:
        engine.dispose()
        if report_dir is not None:
            write_report(report, report_dir)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source", type=Path, default=DEFAULT_SOURCE, help="V1 SQLite file (opened read-only)"
    )
    parser.add_argument("--target", required=True, help="PostgreSQL URL of the V2 database")
    parser.add_argument("--dry-run", action="store_true", help="migrate and validate, then roll back")
    parser.add_argument(
        "--resume", action="store_true", help="allow a non-empty target; insert only missing ids"
    )
    parser.add_argument("--no-backup", action="store_true", help="do not copy the SQLite file to backups/")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args(argv)

    try:
        report = run(
            source_path=args.source.resolve(),
            target_url=args.target,
            dry_run=args.dry_run,
            resume=args.resume,
            backup_dir=None if args.no_backup else DEFAULT_BACKUP_DIR,
            report_dir=args.report_dir,
        )
    except MigrationError as exc:
        print(f"MIGRATION REFUSED/FAILED: {exc}", file=sys.stderr)
        return 2

    print(f"Outcome: {report.outcome}")
    print(f"Target:  {report.target}")
    for check in report.checks:
        print(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name} — {check.detail}")
    flag_counts: dict[str, int] = {}
    for item in report.flags:
        flag_counts[item["flag"]] = flag_counts.get(item["flag"], 0) + 1
    print(f"Flags:   {flag_counts or 'none'}")
    print(f"Possible duplicate variants: {len(report.possible_duplicate_variants)}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
