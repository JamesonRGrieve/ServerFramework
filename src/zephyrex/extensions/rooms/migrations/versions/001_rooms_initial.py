# SPDX-License-Identifier: AGPL-3.0-or-later
"""initial schema for rooms: the room_events change log

Revision ID: 001_rooms_initial
Revises:
Create Date: 2026-11-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_rooms_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("ext_rooms",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "room_events",
        sa.Column(
            "room_type", sa.String(), nullable=False, comment="The room record's type"
        ),
        sa.Column(
            "room_id", sa.String(), nullable=False, comment="The room record's id"
        ),
        sa.Column(
            "record_type",
            sa.String(),
            nullable=False,
            comment="The changed record's type",
        ),
        sa.Column(
            "record_id", sa.String(), nullable=False, comment="The changed record's id"
        ),
        sa.Column(
            "op", sa.String(), nullable=False, comment="created, updated or deleted"
        ),
        sa.Column(
            "etag",
            sa.String(),
            nullable=True,
            comment="The record's version after it",
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
        comment="Room changes: which record in which room changed, kept briefly for catch-up",
        info={"extension": "rooms"},
    )
    op.create_index(
        "ix_room_events_room", "room_events", ["room_type", "room_id", "created_at"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_room_events_room", table_name="room_events")
    op.drop_table("room_events")
