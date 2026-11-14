"""The migrations themselves: full round trip and no drift from the ORM models."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

import autohub.db.models  # noqa: F401
from autohub.db.base import Base
from autohub.specs.taxonomy import ALL_SPECS, TAXONOMY

REPO_ROOT = Path(__file__).resolve().parents[2]
V1_REVISION = "045d1b03bb71"
LEGACY_TABLES = {"car_brands", "car_models", "car_variants", "car_specs", "car_images"}
V2_TABLES = {
    "brands", "vehicle_models", "model_generations", "variants", "spec_categories", "spec_definitions",
    "variant_specs", "variant_spec_history", "brochures", "brochure_versions", "brochure_pages",
    "extraction_runs", "pipeline_runs", "vehicle_images", "news_vehicles", "conversations", "messages",
    "embeddings",
}  # fmt: skip


@pytest.fixture()
def alembic_config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_head_schema_matches_models(engine: Engine) -> None:
    """`alembic check` equivalent: the migrations produce exactly what the models declare."""

    def include_object(
        obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
    ) -> bool:
        return not (type_ == "table" and reflected and name in LEGACY_TABLES)

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "include_object": include_object}
        )
        diff = compare_metadata(context, Base.metadata)
    assert diff == []


def test_extensions_and_tables_exist(engine: Engine) -> None:
    with engine.connect() as connection:
        extensions = set(connection.execute(text("select extname from pg_extension")).scalars())
    assert {"vector", "pg_trgm"} <= extensions
    tables = _tables(engine)
    assert tables >= V2_TABLES
    assert {"users", "news", "news_images"} <= tables
    assert tables.isdisjoint(LEGACY_TABLES), "empty legacy tables should have been dropped by v2_0003"


def test_taxonomy_is_seeded(engine: Engine) -> None:
    with engine.connect() as connection:
        categories = connection.execute(text("select count(*) from spec_categories")).scalar_one()
        keys = set(connection.execute(text("select key from spec_definitions")).scalars())
    assert categories == len(TAXONOMY)
    assert keys == set(ALL_SPECS)


def test_vector_and_fulltext_indexes_exist(engine: Engine) -> None:
    with engine.connect() as connection:
        index_defs = dict(
            connection.execute(
                text("select indexname, indexdef from pg_indexes where tablename = 'embeddings'")
            ).all()
        )
    assert "hnsw" in index_defs["ix_embeddings_embedding_hnsw"]
    assert "vector_cosine_ops" in index_defs["ix_embeddings_embedding_hnsw"]
    assert "gin" in index_defs["ix_embeddings_tsv"]


def test_downgrade_to_v1_and_upgrade_again(engine: Engine, alembic_config: Config) -> None:
    """Runs last in this module: leaves the schema back at head for other tests."""
    try:
        command.downgrade(alembic_config, V1_REVISION)
        tables = _tables(engine)
        assert tables >= LEGACY_TABLES
        assert tables.isdisjoint(V2_TABLES)
        with engine.connect() as connection:
            columns = {c["name"] for c in inspect(connection).get_columns("users")}
        assert "role" not in columns
    finally:
        command.upgrade(alembic_config, "head")
    assert _tables(engine) >= V2_TABLES


def test_legacy_drop_refuses_when_tables_hold_rows(engine: Engine, alembic_config: Config) -> None:
    try:
        command.downgrade(alembic_config, "v2_0002")
        with engine.begin() as connection:
            connection.execute(text("insert into car_brands (name) values ('Testa')"))
        with pytest.raises(RuntimeError, match="Refusing to drop legacy V1 tables"):
            command.upgrade(alembic_config, "head")
        assert "car_brands" in _tables(engine)
    finally:
        with engine.begin() as connection:
            if "car_brands" in _tables(engine):
                connection.execute(text("delete from car_brands"))
        command.upgrade(alembic_config, "head")
