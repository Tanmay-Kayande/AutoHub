"""model-level ex-showroom prices

Adds the price range a brand publishes for a model on its own site, with the page it
was read from and the date. Additive and nullable: existing rows are unaffected and
code that does not know the columns keeps working. Downgrade drops the columns and
with them the scraped prices, which the next discovery run reads again.

Revision ID: v2_0004
Revises: v2_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v2_0004"
down_revision: str | Sequence[str] | None = "v2_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "vehicle_models"
CHECKS = {
    "model_price_requires_source": (
        "price_min_ex_showroom IS NULL OR (price_source IS NOT NULL AND price_as_of IS NOT NULL)"
    ),
    "model_price_range_valid": (
        "price_max_ex_showroom IS NULL OR "
        "(price_min_ex_showroom IS NOT NULL AND price_max_ex_showroom >= price_min_ex_showroom)"
    ),
    "model_price_non_negative": "price_min_ex_showroom IS NULL OR price_min_ex_showroom >= 0",
}


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("price_min_ex_showroom", sa.Numeric(12, 2), nullable=True))
    op.add_column(TABLE, sa.Column("price_max_ex_showroom", sa.Numeric(12, 2), nullable=True))
    op.add_column(TABLE, sa.Column("price_source", sa.Text(), nullable=True))
    op.add_column(TABLE, sa.Column("price_as_of", sa.Date(), nullable=True))
    for name, condition in CHECKS.items():
        op.create_check_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, condition)


def downgrade() -> None:
    for name in CHECKS:
        op.drop_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, type_="check")
    for column in ("price_as_of", "price_source", "price_max_ex_showroom", "price_min_ex_showroom"):
        op.drop_column(TABLE, column)
