"""tools.provision_app_role: the app role can read and write rows but not change the schema."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.factories import make_brand
from tools.provision_app_role import ProvisionError, main, provision

ROLE = "autohub_test_app_role"


def _as_app(db: Session) -> None:
    db.execute(text(f"SET LOCAL ROLE {ROLE}"))


def _denied(db: Session, sql: str) -> bool:
    savepoint = db.begin_nested()
    try:
        db.execute(text(sql))
    except DBAPIError as exc:
        savepoint.rollback()
        return "permission denied" in str(exc.orig) or "must be owner" in str(exc.orig)
    savepoint.rollback()
    return False


def test_app_role_has_row_access_only(db: Session) -> None:
    brand = make_brand(db)
    conn = db.connection()
    provision(conn, ROLE, None)
    provision(conn, ROLE, None)  # idempotent

    attrs = conn.execute(
        text("SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin FROM pg_roles WHERE rolname = :r"),
        {"r": ROLE},
    ).one()
    assert tuple(attrs) == (False, False, False, False)

    _as_app(db)
    assert db.execute(text("SELECT count(*) FROM brands")).scalar_one() >= 1
    db.execute(text("UPDATE brands SET website_url = 'https://x.example/' WHERE id = :i"), {"i": brand.id})
    new_id = db.execute(
        text("INSERT INTO pipeline_runs (trigger, status) VALUES ('cli', 'running') RETURNING id")
    ).scalar_one()  # sequence usage
    db.execute(text("DELETE FROM pipeline_runs WHERE id = :i"), {"i": new_id})

    assert _denied(db, "CREATE TABLE app_role_probe (id int)")
    assert _denied(db, "ALTER TABLE brands ADD COLUMN probe int")
    assert _denied(db, "DROP TABLE pipeline_runs")
    assert _denied(db, "TRUNCATE brands CASCADE")
    db.execute(text("RESET ROLE"))


def test_default_privileges_cover_tables_created_later(db: Session) -> None:
    conn = db.connection()
    provision(conn, ROLE, None)
    conn.execute(text("CREATE TABLE later_migration_probe (id serial PRIMARY KEY, v int)"))
    _as_app(db)
    db.execute(text("INSERT INTO later_migration_probe (v) VALUES (1)"))
    assert db.execute(text("SELECT v FROM later_migration_probe")).scalar_one() == 1
    db.execute(text("RESET ROLE"))


def test_rejects_unsafe_names_and_self_grant(db: Session) -> None:
    conn = db.connection()
    with pytest.raises(ProvisionError):
        provision(conn, 'x"; DROP TABLE brands; --', None)
    owner = conn.execute(text("SELECT current_user")).scalar_one()
    with pytest.raises(ProvisionError):
        provision(conn, owner, None)


def test_cli_requires_a_strong_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOHUB_APP_DB_PASSWORD", "short")
    assert main([]) == 2
