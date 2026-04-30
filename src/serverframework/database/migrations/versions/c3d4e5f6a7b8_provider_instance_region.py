"""provider_instance_region

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-30 00:00:01.000000

Item 36: Add ``region`` column to ``provider_instances``.

The column carries the physical placement of a provider instance (e.g.
``"eu-west-1"``) and is consumed by the unmerged residency extension to
filter rotation chains by jurisdiction. The column is nullable: existing
rows are untouched, and provider instances that opt out of residency
filtering simply leave it ``NULL``. Per Item 80 (rolling-deploy
migration safety), nullable additive columns are forward- and backward-
compatible with both v1 (pre-column) and v2 (post-column) inserts.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add provider_instances.region (nullable)."""
    op.add_column(
        "provider_instances",
        sa.Column(
            "region",
            sa.String(length=64),
            nullable=True,
            comment=(
                "Item 36 residency region for the provider instance "
                "(e.g. 'eu-west-1'). Consumed by the residency extension "
                "to filter rotation chains by jurisdiction. NULL means "
                "the instance opts out of residency-aware filtering."
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema: drop provider_instances.region."""
    op.drop_column("provider_instances", "region")
