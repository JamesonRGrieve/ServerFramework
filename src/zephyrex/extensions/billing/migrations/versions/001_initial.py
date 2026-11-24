# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for billing: `cost_summaries`.

Generated from the extension's own models. The Inspector guards make it a no-op on a database
that already has the tables (made from the models before this extension shipped migrations).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_billing_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_billing",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {
    "source_module": "zephyrex.extensions.billing.BLL_CostSummary",
    "extension": "billing",
}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("cost_summaries"):
        op.create_table(
            "cost_summaries",
            sa.Column(
                "tenant_id",
                sa.String(),
                nullable=False,
                comment="Team or user id; matches RotationManager._tenant_id()",
            ),
            sa.Column(
                "provider",
                sa.String(),
                nullable=False,
                comment="Provider name (e.g. 'openai')",
            ),
            sa.Column(
                "ability",
                sa.String(),
                nullable=False,
                comment="Ability name; 'unknown' if the call was not labeled",
            ),
            sa.Column(
                "period", sa.String(), nullable=False, comment="Aggregation window"
            ),
            sa.Column(
                "period_key",
                sa.String(),
                nullable=False,
                comment="Period bucket key, e.g. '2026-04-30' for daily",
            ),
            sa.Column(
                "total_cost_usd",
                sa.String(),
                nullable=False,
                comment="Sum of per-request cost rows in this bucket",
            ),
            sa.Column(
                "call_count",
                sa.Integer(),
                nullable=False,
                comment="Number of billable calls in this bucket",
            ),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Daily/weekly/monthly cost roll-up (Item 84 cost observability)",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_cost_summaries_deleted_at",
            "cost_summaries",
            ["deleted_at"],
            unique=False,
        )


def downgrade() -> None:
    if _has_table("cost_summaries"):
        op.drop_table("cost_summaries")
