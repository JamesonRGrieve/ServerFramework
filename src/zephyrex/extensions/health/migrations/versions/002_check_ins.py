# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check-ins: the ``health_check_ins`` table (mood over time, about the user
or someone)."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_health_check_ins"
down_revision: Union[str, None] = "001_health_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "health"}
NAME = "health_check_ins"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(NAME):
        return
    op.create_table(
        NAME,
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=True,
            comment="Optional foreign key to UserModel",
        ),
        sa.Column(
            "checked_at",
            sa.DateTime(),
            nullable=False,
            comment="When the check-in was made",
        ),
        sa.Column("mood", sa.Integer(), nullable=False, comment="1 (low) to 5 (great)"),
        sa.Column(
            "about",
            sa.String(),
            nullable=True,
            comment="Who or what it is about; the user themselves when empty",
        ),
        sa.Column("notes", sa.String(), nullable=True, comment="Free-form notes"),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="A user's check-ins: mood over time, about themselves or someone",
        info=TABLE_INFO,
    )
    op.create_index(f"ix_{NAME}_deleted_at", NAME, ["deleted_at"])


def downgrade() -> None:
    op.drop_index(f"ix_{NAME}_deleted_at", table_name=NAME)
    op.drop_table(NAME)
