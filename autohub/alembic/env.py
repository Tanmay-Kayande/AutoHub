"""Alembic environment.

The database URL comes from, in order: ``-x db_url=...`` on the command line,
then application settings (``DATABASE_URL``).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import autohub.db.models  # noqa: F401  (registers every table on Base.metadata)
from autohub.core.settings import get_settings
from autohub.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# V1 tables that stay in the database until revision v2_0003 drops them (only if empty).
LEGACY_TABLES = {"car_brands", "car_models", "car_variants", "car_specs", "car_images"}


def _database_url() -> str:
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    return override or get_settings().database_url


def include_object(obj: object, name: str | None, type_: str, reflected: bool, compare_to: object) -> bool:
    return not (type_ == "table" and reflected and name in LEGACY_TABLES)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
