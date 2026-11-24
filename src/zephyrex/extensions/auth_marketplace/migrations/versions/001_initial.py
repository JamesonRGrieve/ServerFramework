# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for auth_marketplace: `user_payment_portals`, `team_payment_portals`.

Generated from the extension's own models. The Inspector guards make it a no-op on a database
that already has the tables (made from the models before this extension shipped migrations).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_auth_marketplace_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_auth_marketplace",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {
    "source_module": "zephyrex.extensions.auth_marketplace.BLL_Auth_Marketplace",
    "extension": "auth_marketplace",
}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("user_payment_portals"):
        op.create_table(
            "user_payment_portals",
            sa.Column(
                "user_id",
                sa.String(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="Foreign key to UserModel",
            ),
            sa.Column(
                "payment_portal_id",
                sa.String(),
                nullable=False,
                comment="Reference to a registered payment provider",
            ),
            sa.Column(
                "customer_id",
                sa.String(),
                nullable=True,
                comment="Customer ID at the payment provider, if assigned",
            ),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Per-user link to a payment provider; one row per (user, provider) pair",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_user_payment_portals_deleted_at",
            "user_payment_portals",
            ["deleted_at"],
            unique=False,
        )

    if not _has_table("team_payment_portals"):
        op.create_table(
            "team_payment_portals",
            sa.Column(
                "user_id",
                sa.String(),
                sa.ForeignKey("users.id"),
                nullable=True,
                comment="Optional foreign key to UserModel",
            ),
            sa.Column(
                "team_id",
                sa.String(),
                sa.ForeignKey("teams.id"),
                nullable=False,
                comment="Foreign key to TeamModel",
            ),
            sa.Column("payment_portal_id", sa.String(), nullable=False),
            sa.Column("customer_id", sa.String(), nullable=True),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Per-team link to a payment provider; one row per (team, provider) pair",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_team_payment_portals_deleted_at",
            "team_payment_portals",
            ["deleted_at"],
            unique=False,
        )


def downgrade() -> None:
    if _has_table("team_payment_portals"):
        op.drop_table("team_payment_portals")
    if _has_table("user_payment_portals"):
        op.drop_table("user_payment_portals")
