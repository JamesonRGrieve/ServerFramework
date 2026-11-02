# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for gifts: the ``gifts`` table."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_gifts_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_gifts",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "gifts"}
NAME = "gifts"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(NAME):
        return
    op.create_table(
        NAME,
        sa.Column("for_name", sa.String(), nullable=False, comment="Who it is for"),
        sa.Column(
            "for_ref",
            sa.String(),
            nullable=True,
            comment="The app's own record of them",
        ),
        sa.Column("title", sa.String(), nullable=False, comment="What it is"),
        sa.Column("url", sa.String(), nullable=True, comment="Where to buy it"),
        sa.Column(
            "note", sa.Text(), nullable=True, comment="Sizes, colours, the occasion"
        ),
        sa.Column(
            "status", sa.String(), nullable=False, comment="idea, bought or given"
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Gift ideas, each its giver's alone",
        info=TABLE_INFO,
    )
    op.create_index("ix_gifts_created_by_user_id", NAME, ["created_by_user_id"])


def downgrade() -> None:
    op.drop_table(NAME)
