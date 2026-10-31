"""initial schema for live_sync

Revision ID: a730a3fba22d
Revises:
Create Date: 2026-10-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a730a3fba22d"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("ext_live_sync",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "live_devices",
        sa.Column(
            "channel",
            sa.String(),
            nullable=False,
            comment="The channel the device joined",
        ),
        sa.Column(
            "client_id",
            sa.String(),
            nullable=False,
            comment="The device's own id, chosen by the client",
        ),
        sa.Column(
            "label",
            sa.String(),
            nullable=True,
            comment="What the user calls the device",
        ),
        sa.Column(
            "is_main",
            sa.Boolean(),
            nullable=False,
            comment="Whether this device drives the channel",
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(),
            nullable=True,
            comment="When the device last joined",
        ),
        sa.Column(
            "created_by_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "updated_by_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Live sync devices: each user's windows and devices per channel",
        info={"extension": "live_sync"},
    )
    op.create_index(
        "ix_live_devices_owner_channel",
        "live_devices",
        ["created_by_user_id", "channel"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_live_devices_owner_channel", table_name="live_devices")
    op.drop_table("live_devices")
