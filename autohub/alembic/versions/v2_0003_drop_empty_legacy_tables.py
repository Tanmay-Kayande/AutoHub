"""drop empty legacy V1 catalogue tables

The V1 tables ``car_brands``, ``car_models``, ``car_variants``, ``car_specs``
and ``car_images`` are superseded by the V2 schema. This revision removes them
**only if every one of them is empty**. If any holds a row it stops with an
error: that data must first be moved with
``python -m tools.migrate_sqlite_to_postgres`` or dealt with explicitly.

Revision ID: v2_0003
Revises: v2_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v2_0003"
down_revision: str | Sequence[str] | None = "v2_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Children first.
LEGACY_TABLES = ("car_specs", "car_images", "car_variants", "car_models", "car_brands")


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    non_empty: dict[str, int] = {}
    for table in LEGACY_TABLES:
        if table in existing:
            count = bind.execute(sa.text(f'SELECT count(*) FROM "{table}"')).scalar_one()
            if count:
                non_empty[table] = count
    if non_empty:
        raise RuntimeError(
            "Refusing to drop legacy V1 tables that still contain rows: "
            + ", ".join(f"{t} ({n})" for t, n in non_empty.items())
            + ". Migrate or remove that data explicitly first."
        )
    for table in LEGACY_TABLES:
        if table in existing:
            op.drop_table(table)


def downgrade() -> None:
    """Recreate the (empty) V1 tables exactly as revision 045d1b03bb71 defined them."""
    op.create_table(
        "car_brands",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id", name="car_brands_pkey"),
        sa.UniqueConstraint("name", name="car_brands_name_key"),
    )
    op.create_table(
        "car_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=False),
        sa.Column("body_type", sa.String(length=50), nullable=True),
        sa.Column("launch_date", sa.String(length=20), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["brand_id"], ["car_brands.id"], name="car_models_brand_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="car_models_pkey"),
        sa.UniqueConstraint("name", "brand_id", name="uq_model_brand"),
    )
    op.create_table(
        "car_images",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("image_url", sa.String(length=300), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("image_type", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["model_id"], ["car_models.id"], name="car_images_model_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="car_images_pkey"),
    )
    op.create_table(
        "car_variants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("variant_name", sa.String(length=120), nullable=False),
        sa.Column("fuel_type", sa.String(length=50), nullable=True),
        sa.Column("transmission", sa.String(length=120), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("fuel_type_id", sa.Integer(), nullable=True),
        sa.Column("transmission_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["model_id"], ["car_models.id"], name="car_variants_model_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="car_variants_pkey"),
        sa.UniqueConstraint("model_id", "variant_name", name="uq_model_variant"),
    )
    op.create_table(
        "car_specs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("engine", sa.String(length=100), nullable=True),
        sa.Column("engine_capacity", sa.Float(), nullable=True),
        sa.Column("power", sa.String(length=50), nullable=True),
        sa.Column("torque", sa.String(length=50), nullable=True),
        sa.Column("mileage", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["variant_id"], ["car_variants.id"], name="car_specs_variant_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="car_specs_pkey"),
        sa.UniqueConstraint("variant_id", name="car_specs_variant_id_key"),
    )
