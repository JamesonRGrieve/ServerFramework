# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for media: shared watch-lists (``watch_lists``,
``watch_list_members``, ``watch_list_titles`` and ``watch_list_wants``)."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_media_watch_lists"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_media",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "media"}


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
        "watch_lists",
        "Watch-lists, kept by their keeper and shared with members",
        sa.Column(
            "name", sa.String(), nullable=False, comment="What the list is called"
        ),
    )
    _create(
        "watch_list_members",
        "Whom each watch-list is shared with",
        sa.Column("list_id", sa.String(), nullable=False, comment="The list"),
        sa.Column(
            "user_id",
            sa.String(),
            nullable=False,
            comment="An account the list is shared with",
        ),
    )
    _create(
        "watch_list_titles",
        "The titles on each watch-list",
        sa.Column("list_id", sa.String(), nullable=False, comment="The list"),
        sa.Column(
            "media_id", sa.String(), nullable=False, comment="The catalogue's id"
        ),
        sa.Column(
            "title", sa.String(), nullable=False, comment="As the catalogue names it"
        ),
        sa.Column("kind", sa.String(), nullable=False, comment="movie, tv or video"),
        sa.Column("year", sa.Integer(), nullable=True, comment="The year it came out"),
        sa.Column("overview", sa.Text(), nullable=True, comment="What it is about"),
        sa.Column(
            "url", sa.String(), nullable=True, comment="Its page at the catalogue"
        ),
        sa.Column(
            "image", sa.String(), nullable=True, comment="Its poster or thumbnail"
        ),
        sa.Column(
            "watched_on", sa.Date(), nullable=True, comment="When the list watched it"
        ),
    )
    _create(
        "watch_list_wants",
        "Who wants to see each title on a watch-list",
        sa.Column("title_id", sa.String(), nullable=False, comment="The title"),
    )
    op.create_index("ix_watch_list_members_user_id", "watch_list_members", ["user_id"])
    op.create_index("ix_watch_list_titles_list_id", "watch_list_titles", ["list_id"])
    op.create_index("ix_watch_list_wants_title_id", "watch_list_wants", ["title_id"])


def downgrade() -> None:
    for name in (
        "watch_list_wants",
        "watch_list_titles",
        "watch_list_members",
        "watch_lists",
    ):
        op.drop_table(name)
