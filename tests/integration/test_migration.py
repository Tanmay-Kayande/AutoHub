"""SQLite (V1) -> PostgreSQL (V2) migration, on a synthetic V1 database.

The synthetic rows reproduce each awkward shape found in the real data
(docs/V2/AUDIT.md §4.2) using obviously fictional vehicles.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from tests.conftest import TEST_DATABASE_URL
from tools.migrate_sqlite_to_postgres import MigrationError, run, sha256_file

DATA_TABLES = (
    "variant_spec_history, variant_specs, vehicle_images, variants, model_generations, brochures, "
    "vehicle_models, brands, news_images, news_vehicles, news, messages, conversations, users"
)

V1_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(50) NOT NULL, email VARCHAR(256) NOT NULL,
    gender VARCHAR(10), location VARCHAR(60), hashed_password VARCHAR(200) NOT NULL);
CREATE TABLE car_brands (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE);
CREATE TABLE car_models (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, brand_id INTEGER NOT NULL,
    body_type VARCHAR(50), launch_date VARCHAR(20), description VARCHAR(500));
CREATE TABLE car_variants (id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL, variant_name VARCHAR(120) NOT NULL,
    fuel_type VARCHAR(50), transmission VARCHAR(120), price FLOAT, fuel_type_id INTEGER, transmission_id INTEGER);
CREATE TABLE car_specs (id INTEGER PRIMARY KEY, variant_id INTEGER NOT NULL UNIQUE, engine VARCHAR(100),
    engine_capacity FLOAT, power VARCHAR(50), torque VARCHAR(50), mileage FLOAT);
CREATE TABLE car_images (id INTEGER PRIMARY KEY, image_url VARCHAR(300) NOT NULL, model_id INTEGER NOT NULL,
    image_type VARCHAR(20));
CREATE TABLE news (id INTEGER PRIMARY KEY, title VARCHAR(200) NOT NULL, content VARCHAR(2000) NOT NULL,
    published_at VARCHAR(30) NOT NULL);
CREATE TABLE news_images (id INTEGER PRIMARY KEY, image_url VARCHAR(300) NOT NULL, news_id INTEGER NOT NULL);
"""

ARGON_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHRzb21lc2FsdA$Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4"


def _build_v1(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(V1_SCHEMA)
    con.execute("INSERT INTO users VALUES (7, 'Tess', 'tess@example.com', 'F', 'Pune', ?)", (ARGON_HASH,))
    con.executemany("INSERT INTO car_brands VALUES (?, ?)", [(1, "Testa"), (4, "Emptia")])
    con.executemany(
        "INSERT INTO car_models VALUES (?, ?, ?, ?, ?, ?)",
        [(3, "Model Q", 1, "SUV", None, None), (5, "Volt E", 1, "SUV", None, None)],
    )
    con.executemany(
        "INSERT INTO car_variants VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
        [
            (10, 3, "LX Diesel", "Diesel", "6-Speed Manual, Rear Wheel Drive (RWD)", None),
            (11, 3, "ZX Petrol", "Petrol", "6 AT", None),
            (12, 3, "ZX+", None, "6-Speed Manual / 6-Speed Torque Converter Automatic", None),
            (13, 3, "ZX", None, None, None),
            (20, 5, "EV Prime", "Electric", None, None),
            (21, 3, "LX Diesel MT RWD", "Diesel", "6-Speed Manual", None),
        ],
    )
    con.executemany(
        "INSERT INTO car_specs VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 10, "tHawk CRDe", 2184.0, "97 kW @ 3750 r/min", "300 Nm @ 1600-2800 r/min", None),
            (2, 11, "tStallion TGDi", 2.0, "149.2kW @5000 r/min", "380 Nm @ 1750-3000 r/min", 15.2),
            (
                3,
                12,
                None,
                1497.0,
                "110 kW | 150 PS",
                "300 Nm @ 1250-3000 (with MT) / 320 Nm @ 1500-3000 (with AT)",
                None,
            ),
            (4, 13, None, None, None, None, None),
            (5, 20, None, None, "110 kW | 150 PS", "310 Nm", 456.0),
            (6, 21, None, 35.0, "strong", None, None),
        ],
    )
    con.executemany(
        "INSERT INTO car_images VALUES (?, ?, ?, ?)",
        [
            (1, "https://img.example.com/q/front.jpg", 3, "exterior"),
            (2, "https://img.example.com/q/dash.jpg", 3, "interior"),
            (3, "https://IMG.example.com/q/front.jpg", 3, "exterior"),  # same URL, different host case
            (4, "https://www.example.org/volt.jpg", 5, None),
        ],
    )
    con.execute("INSERT INTO news VALUES (2, 'Testa launches Model Q', 'Body text.', '2026-01-05T10:30:00Z')")
    con.execute("INSERT INTO news_images VALUES (9, 'https://img.example.com/news.jpg', 2)")
    con.commit()
    con.close()


@pytest.fixture()
def v1_db(tmp_path: Path) -> Path:
    path = tmp_path / "v1.db"
    _build_v1(path)
    return path


@pytest.fixture()
def clean_target(engine: Engine) -> Iterator[Engine]:
    """The tool commits through its own connection, so clean up explicitly."""

    def wipe() -> None:
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {DATA_TABLES} RESTART IDENTITY CASCADE"))

    wipe()
    yield engine
    wipe()


def _run(source: Path, tmp_path: Path, **kwargs: object):  # type: ignore[no-untyped-def]
    return run(
        source_path=source,
        target_url=TEST_DATABASE_URL,
        backup_dir=tmp_path / "backups",
        report_dir=tmp_path / "reports",
        **kwargs,  # type: ignore[arg-type]
    )


def _specs(engine: Engine, variant_id: int) -> dict[str, dict[str, object]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "select d.key, s.value_num, s.value_num_max, s.value_text, s.unit, s.original_value, s.flags, "
                "s.source_type, s.confidence from variant_specs s join spec_definitions d on d.id = s.spec_definition_id "
                "where s.variant_id = :v"
            ),
            {"v": variant_id},
        ).mappings()
        return {row["key"]: dict(row) for row in rows}


def test_full_migration_passes_all_checks(v1_db: Path, tmp_path: Path, clean_target: Engine) -> None:
    before = sha256_file(v1_db)
    report = _run(v1_db, tmp_path)

    assert report.outcome == "SUCCESS — committed"
    assert [c.name for c in report.checks if not c.passed] == []
    assert sha256_file(v1_db) == before
    assert report.target_counts["users"] == 1
    assert report.target_counts["brands"] == 2
    assert report.target_counts["vehicle_models"] == 2
    assert report.target_counts["model_generations"] == 2
    assert report.target_counts["variants"] == 6
    assert report.target_counts["vehicle_images"] == 3  # one duplicate URL skipped
    assert report.target_counts["news"] == 1
    assert Path(report.backup_path).is_file()  # type: ignore[arg-type]
    assert sha256_file(Path(report.backup_path)) == before  # type: ignore[arg-type]
    assert list((tmp_path / "reports").glob("migration_report_*.md"))


def test_ids_relationships_and_hash_are_preserved(v1_db: Path, tmp_path: Path, clean_target: Engine) -> None:
    _run(v1_db, tmp_path)
    with clean_target.connect() as c:
        assert c.execute(text("select id, email, hashed_password, role, is_active from users")).one() == (
            7, "tess@example.com", ARGON_HASH, "user", True,
        )  # fmt: skip
        assert c.execute(text("select id, name, slug from brands order by id")).all() == [
            (1, "Testa", "testa"), (4, "Emptia", "emptia"),
        ]  # fmt: skip
        assert c.execute(text("select id, brand_id, slug, body_type from vehicle_models order by id")).all() == [
            (3, 1, "model-q", "suv"), (5, 1, "volt-e", "suv"),
        ]  # fmt: skip
        # one generation per model, sharing the model's id
        assert c.execute(text("select id, model_id, name, is_current from model_generations order by id")).all() == [
            (3, 3, "Current", True), (5, 5, "Current", True),
        ]  # fmt: skip
        variant = c.execute(
            text("select generation_id, name, slug, fuel_type, transmission_type, drivetrain, fuel_type_raw, "
                 "transmission_raw, price_ex_showroom from variants where id = 10")
        ).one()  # fmt: skip
        assert variant == (
            3, "LX Diesel", "lx-diesel", "diesel", "manual", "rwd", "Diesel",
            "6-Speed Manual, Rear Wheel Drive (RWD)", None,
        )  # fmt: skip
        # "ZX" and "ZX+" must not collide
        assert c.execute(text("select slug from variants where id in (12, 13) order by id")).scalars().all() == [
            "zx-plus", "zx",
        ]  # fmt: skip
        news = c.execute(text("select id, slug, published_at at time zone 'UTC' from news")).one()
        assert (news[0], news[1], str(news[2])) == (2, "testa-launches-model-q", "2026-01-05 10:30:00")


def test_ambiguous_transmission_is_not_guessed(v1_db: Path, tmp_path: Path, clean_target: Engine) -> None:
    report = _run(v1_db, tmp_path)
    with clean_target.connect() as c:
        row = c.execute(text("select transmission_type, transmission_raw from variants where id = 12")).one()
    assert row == (None, "6-Speed Manual / 6-Speed Torque Converter Automatic")
    assert {"table": "car_variants", "id": 12, "field": "transmission",
            "value": "6-Speed Manual / 6-Speed Torque Converter Automatic"} in report.unmapped  # fmt: skip


def test_spec_values_units_and_flags(v1_db: Path, tmp_path: Path, clean_target: Engine) -> None:
    _run(v1_db, tmp_path)

    diesel = _specs(clean_target, 10)
    assert diesel["engine_name"]["value_text"] == "tHawk CRDe"
    assert diesel["engine_displacement"]["value_num"] == Decimal("2184")
    assert diesel["engine_displacement"]["original_value"] == "2184.0"
    assert "unit_assumed" in diesel["engine_displacement"]["flags"]  # type: ignore[operator]
    assert diesel["max_power"]["value_num"] == Decimal("97") and diesel["max_power"]["unit"] == "kW"
    assert diesel["max_power_rpm"]["value_num"] == Decimal("3750")
    assert (diesel["max_torque_rpm"]["value_num"], diesel["max_torque_rpm"]["value_num_max"]) == (
        Decimal("1600"), Decimal("2800"),
    )  # fmt: skip
    assert all(
        row["source_type"] == "legacy_v1" and row["confidence"] == Decimal("0.50") for row in diesel.values()
    )

    # litres stored without a unit are converted to cc and flagged
    petrol = _specs(clean_target, 11)
    assert petrol["engine_displacement"]["value_num"] == Decimal("2000")
    assert petrol["engine_displacement"]["original_value"] == "2.0"
    assert "unit_inferred_litres" in petrol["engine_displacement"]["flags"]  # type: ignore[operator]
    assert petrol["mileage_arai"]["value_num"] == Decimal("15.2")
    assert "ev_range" not in petrol

    # two different torque figures: no number, text kept
    multi = _specs(clean_target, 12)
    assert multi["max_torque"]["value_num"] is None
    assert multi["max_torque"]["value_text"].startswith("300 Nm @ 1250-3000")  # type: ignore[union-attr]
    assert "multi_value" in multi["max_torque"]["flags"]  # type: ignore[operator]
    assert "max_torque_rpm" not in multi
    assert multi["max_power"]["value_num"] == Decimal("110")

    # all-NULL spec row produces nothing
    assert _specs(clean_target, 13) == {}

    # an EV's "mileage" is really its range
    electric = _specs(clean_target, 20)
    assert "mileage_arai" not in electric
    assert electric["ev_range"]["value_num"] == Decimal("456") and electric["ev_range"]["unit"] == "km"
    assert "reclassified_from_mileage" in electric["ev_range"]["flags"]  # type: ignore[operator]

    # values that cannot be interpreted keep their text and get no number
    odd = _specs(clean_target, 21)
    assert odd["engine_displacement"]["value_num"] is None
    assert "unit_ambiguous" in odd["engine_displacement"]["flags"]  # type: ignore[operator]
    assert odd["max_power"]["value_num"] is None and odd["max_power"]["value_text"] == "strong"


def test_images_are_deduplicated_and_categorised(v1_db: Path, tmp_path: Path, clean_target: Engine) -> None:
    report = _run(v1_db, tmp_path)
    with clean_target.connect() as c:
        rows = c.execute(
            text("select id, model_id, generation_id, category, source_domain, source_kind, validation_status, "
                 "is_official from vehicle_images order by id")
        ).all()  # fmt: skip
    assert rows == [
        (1, 3, 3, "exterior", "img.example.com", "legacy_v1", "unvalidated", False),
        (2, 3, 3, "interior", "img.example.com", "legacy_v1", "unvalidated", False),
        (4, 5, 5, "other", "example.org", "legacy_v1", "unvalidated", False),
    ]
    flags = {(f["id"], f["flag"]) for f in report.flags if f["table"] == "car_images"}
    assert flags == {(3, "duplicate_skipped"), (4, "category_unknown")}


def test_possible_duplicate_variants_are_reported_not_merged(
    v1_db: Path, tmp_path: Path, clean_target: Engine
) -> None:
    report = _run(v1_db, tmp_path)
    pairs = {tuple(d["variant_ids"]) for d in report.possible_duplicate_variants}
    assert (10, 21) in pairs  # "LX Diesel" vs "LX Diesel MT RWD"
    with clean_target.connect() as c:
        assert c.execute(text("select count(*) from variants where id in (10, 21)")).scalar_one() == 2


def test_sequences_continue_after_migrated_ids(v1_db: Path, tmp_path: Path, clean_target: Engine) -> None:
    _run(v1_db, tmp_path)
    with clean_target.begin() as c:
        new_id = c.execute(
            text("insert into brands (name, slug) values ('Novus', 'novus') returning id")
        ).scalar_one()
    assert new_id == 5


def test_dry_run_writes_nothing(v1_db: Path, tmp_path: Path, clean_target: Engine) -> None:
    report = _run(v1_db, tmp_path, dry_run=True)
    assert report.outcome == "DRY RUN OK — rolled back"
    assert report.ok
    assert report.backup_path is None
    with clean_target.connect() as c:
        assert c.execute(text("select count(*) from brands")).scalar_one() == 0
        assert c.execute(text("select count(*) from users")).scalar_one() == 0


def test_refuses_non_empty_target_without_resume(v1_db: Path, tmp_path: Path, clean_target: Engine) -> None:
    _run(v1_db, tmp_path)
    with pytest.raises(MigrationError, match="already contains data"):
        _run(v1_db, tmp_path)


def test_resume_is_idempotent(v1_db: Path, tmp_path: Path, clean_target: Engine) -> None:
    _run(v1_db, tmp_path)
    with clean_target.connect() as c:
        before = c.execute(text("select count(*) from variant_specs")).scalar_one()
    report = _run(v1_db, tmp_path, resume=True)
    assert report.ok
    assert all(count == 0 for count in report.inserted.values())
    with clean_target.connect() as c:
        assert c.execute(text("select count(*) from variant_specs")).scalar_one() == before
        assert c.execute(text("select count(*) from variants")).scalar_one() == 6


def test_protected_target_is_refused(v1_db: Path, tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="protected"):
        run(
            source_path=v1_db,
            target_url="postgresql://someone:pw@localhost:5432/autohub",
            backup_dir=None,
            report_dir=None,
        )


def test_missing_source_is_refused(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="not found"):
        run(source_path=tmp_path / "nope.db", target_url=TEST_DATABASE_URL, backup_dir=None, report_dir=None)


def test_failed_validation_rolls_back(
    v1_db: Path, tmp_path: Path, clean_target: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If any check fails, nothing is committed."""
    import tools.migrate_sqlite_to_postgres as tool

    original = tool.validate

    def failing(connection, source, report):  # type: ignore[no-untyped-def]
        original(connection, source, report)
        report.check("forced failure", False, "simulated")

    monkeypatch.setattr(tool, "validate", failing)
    report = _run(v1_db, tmp_path)
    assert report.outcome == "FAILED — rolled back"
    with clean_target.connect() as c:
        assert c.execute(text("select count(*) from brands")).scalar_one() == 0
