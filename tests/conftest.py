"""Test harness.

Safety properties (see docs/V2/TESTING_STRATEGY.md):

* The environment is rewritten *before* any application module is imported, so
  settings can only ever point at the dedicated test database, a throw-away
  data directory, and no external-service credentials.
* A guard refuses to run database tests against a database whose name does not
  end in ``_test``.
* Each test runs in a transaction that is rolled back.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


def _test_database_url() -> str:
    """From the environment, else from the git-ignored ``.env`` at the repo root. No password in code."""
    from dotenv import dotenv_values

    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(
        Path(__file__).resolve().parents[1] / ".env"
    ).get("TEST_DATABASE_URL")
    # Without a configured URL, this password-less one fails to connect and DB tests are skipped.
    return url or "postgresql://autohub@127.0.0.1:5433/autohub_test"


TEST_DATABASE_URL = _test_database_url()

_TMP_DATA_DIR = tempfile.mkdtemp(prefix="autohub-test-data-")

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": TEST_DATABASE_URL,
        "SECRET_KEY": "test-only-secret-key-not-used-anywhere-else",
        "GEMINI_API_KEY": "",  # never reach a paid API from tests
        "SERPAPI_KEY": "",
        "DATA_DIR": _TMP_DATA_DIR,
        "SCHEDULER_ENABLED": "false",
        "RATE_LIMIT_ENABLED": "false",
        "CRAWLER_DELAY_SECONDS": "0",
    }
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def assert_safe_test_database(url: str) -> None:
    """Abort unless ``url`` names a database reserved for tests."""
    from sqlalchemy.engine import make_url

    name = make_url(url).database or ""
    if not name.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run database tests against '{name}': the database name must end in '_test'."
        )


def _database_available(url: str) -> bool:
    from sqlalchemy import create_engine, text

    try:
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def engine() -> Iterator[object]:
    """Schema built by Alembic once per session — every run exercises the migrations."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    assert_safe_test_database(TEST_DATABASE_URL)
    if not _database_available(TEST_DATABASE_URL):
        pytest.skip(
            "PostgreSQL test database is not reachable: run `docker compose up -d db` and set "
            "TEST_DATABASE_URL in the repo-root .env (see README)"
        )

    eng = create_engine(TEST_DATABASE_URL)
    # Start from an empty schema so leftovers from an aborted run cannot leak in.
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = Config(str(REPO_ROOT / "alembic.ini"))
    command.upgrade(config, "head")

    from autohub.db import session as db_session

    db_session.configure_engine(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine: object) -> Iterator[object]:
    """A session bound to one connection whose outer transaction is always rolled back."""
    from sqlalchemy.orm import Session

    connection = engine.connect()  # type: ignore[attr-defined]
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db: object) -> Iterator[object]:
    """FastAPI test client whose requests share the test's rolled-back session."""
    from fastapi.testclient import TestClient

    from autohub.db.session import get_db
    from autohub.main import create_app

    app = create_app()

    def _override() -> Iterator[object]:
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Anything under tests/integration needs the database."""
    for item in items:
        if "integration" in Path(str(item.fspath)).parts:
            item.add_marker(pytest.mark.integration)
