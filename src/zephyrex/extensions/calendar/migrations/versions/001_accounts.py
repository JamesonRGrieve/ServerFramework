# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for calendar: each user's own calendar accounts
(``calendar_accounts``) and whom they share their availability with
(``availability_shares``)."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_calendar_accounts"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_calendar",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "calendar"}


def _record_columns() -> list:
    return [
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    ]


def _create(name: str, comment: str, *columns: sa.Column) -> None:
    if sa.inspect(op.get_bind()).has_table(name):
        return
    op.create_table(
        name,
        *columns,
        *_record_columns(),
        sa.PrimaryKeyConstraint("id"),
        comment=comment,
        info=TABLE_INFO,
    )
    op.create_index(f"ix_{name}_created_by_user_id", name, ["created_by_user_id"])


def upgrade() -> None:
    _create(
        "calendar_accounts",
        "Each user's own calendar accounts",
        sa.Column(
            "provider",
            sa.String(),
            nullable=False,
            comment="caldav, google_calendar or microsoft_calendar",
        ),
        sa.Column(
            "label", sa.String(), nullable=False, comment="What the user calls it"
        ),
        sa.Column(
            "calendar_url",
            sa.String(),
            nullable=True,
            comment="CalDAV: the collection's URL",
        ),
        sa.Column(
            "username", sa.String(), nullable=True, comment="CalDAV: the sign-in name"
        ),
        sa.Column(
            "secret",
            sa.Text(),
            nullable=True,
            comment="CalDAV: the password, encrypted",
        ),
        sa.Column(
            "calendar_id", sa.String(), nullable=True, comment="Google: which calendar"
        ),
    )
    _create(
        "availability_shares",
        "Whom each user shares their availability with",
        sa.Column(
            "viewer_user_id",
            sa.String(),
            nullable=False,
            comment="Who sees when the owner is busy",
        ),
        sa.Column(
            "can_book",
            sa.Boolean(),
            nullable=False,
            comment="Whether they may book the owner",
        ),
    )
    op.create_index(
        "ix_availability_shares_viewer_user_id",
        "availability_shares",
        ["viewer_user_id"],
    )


def downgrade() -> None:
    for name in ("availability_shares", "calendar_accounts"):
        op.drop_table(name)
