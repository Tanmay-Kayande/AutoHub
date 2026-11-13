"""seed specification taxonomy

Idempotent upsert of spec categories and definitions from
``autohub.specs.taxonomy``. Re-seeding after the taxonomy changes is done with
``python -m tools.seed_taxonomy`` (no new revision needed).

Revision ID: v2_0002
Revises: v2_0001
"""

from collections.abc import Sequence

from alembic import op

from autohub.specs.seed import seed_taxonomy

revision: str = "v2_0002"
down_revision: str | Sequence[str] | None = "v2_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    seed_taxonomy(op.get_bind())


def downgrade() -> None:
    # Definitions referenced by variant_specs cannot be removed; v2_0001's
    # downgrade drops the tables altogether.
    op.execute(
        "DELETE FROM spec_definitions d "
        "WHERE NOT EXISTS (SELECT 1 FROM variant_specs s WHERE s.spec_definition_id = d.id)"
    )
    op.execute(
        "DELETE FROM spec_categories c "
        "WHERE NOT EXISTS (SELECT 1 FROM spec_definitions d WHERE d.category_id = c.id)"
    )
