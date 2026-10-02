# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for book: owns the ``books`` and ``book_chapters``
tables. Before this the extension kept books in process memory only."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_book_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_book",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "book"}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


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


def upgrade() -> None:
    if not _has_table("books"):
        op.create_table(
            "books",
            sa.Column(
                "team_id",
                sa.String(),
                sa.ForeignKey("teams.id"),
                nullable=True,
                comment="Optional foreign key to TeamModel",
            ),
            sa.Column(
                "user_id",
                sa.String(),
                sa.ForeignKey("users.id"),
                nullable=True,
                comment="Optional foreign key to UserModel",
            ),
            sa.Column("title", sa.String(), nullable=False, comment="Title"),
            sa.Column(
                "author", sa.String(), nullable=True, comment="Author shown on the book"
            ),
            sa.Column(
                "description", sa.String(), nullable=True, comment="Blurb or summary"
            ),
            sa.Column("genre", sa.String(), nullable=True, comment="Genre"),
            sa.Column(
                "language",
                sa.String(),
                nullable=False,
                comment="BCP 47 language tag (en, fr-CA, …)",
            ),
            sa.Column(
                "status",
                sa.String(),
                nullable=False,
                comment="Where the book is in its life",
            ),
            *_record_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Books: their metadata and status",
            info=TABLE_INFO,
        )
        op.create_index("ix_books_deleted_at", "books", ["deleted_at"])
    if not _has_table("book_chapters"):
        op.create_table(
            "book_chapters",
            sa.Column(
                "book_id",
                sa.String(),
                nullable=False,
                comment="The ID of the related book",
            ),
            sa.Column("title", sa.String(), nullable=False, comment="Chapter title"),
            sa.Column(
                "content",
                sa.String(),
                nullable=False,
                comment="Chapter text (Markdown)",
            ),
            sa.Column(
                "position",
                sa.Integer(),
                nullable=False,
                comment="Order in the book, from 1",
            ),
            sa.Column(
                "word_count",
                sa.Integer(),
                nullable=False,
                comment="Words in the content (kept by the server)",
            ),
            *_record_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="A book's chapters, in order, as Markdown",
            info=TABLE_INFO,
        )
        op.create_index("ix_book_chapters_deleted_at", "book_chapters", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_book_chapters_deleted_at", table_name="book_chapters")
    op.drop_table("book_chapters")
    op.drop_index("ix_books_deleted_at", table_name="books")
    op.drop_table("books")
