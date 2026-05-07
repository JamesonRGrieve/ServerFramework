"""Initial migration for auth_recovery_questions — owns `user_recovery_questions`.

The `user_recovery_questions` table was originally created by core in
`e0b0dc9d5070_initial_schema.py` before the Phase-2 carve-out (PR #144).
On databases stamped with that core revision the table already exists;
the Inspector check below makes the upgrade a no-op in that case. On
fresh databases, core's initial schema is still the file that creates
the table — this revision tracks ownership for any *subsequent* schema
changes the extension makes to it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "001_auth_recovery_questions_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("auth_recovery_questions",)
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _existing_index_names(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    if not _has_table("user_recovery_questions"):
        op.create_table(
            "user_recovery_questions",
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("question", sa.String(), nullable=False),
            sa.Column("answer", sa.String(), nullable=False),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            comment="Per-user recovery question/answer pairs",
            info={
                "source_module": "serverframework.extensions.auth_recovery_questions.BLL_Recovery_Questions",
                "extension": "auth_recovery_questions",
            },
        )

    existing = _existing_index_names("user_recovery_questions")
    if "ix_user_recovery_questions_user_id" not in existing:
        op.create_index(
            "ix_user_recovery_questions_user_id",
            "user_recovery_questions",
            ["user_id"],
        )


def downgrade() -> None:
    existing = _existing_index_names("user_recovery_questions")
    if "ix_user_recovery_questions_user_id" in existing:
        op.drop_index(
            "ix_user_recovery_questions_user_id",
            table_name="user_recovery_questions",
        )
    if _has_table("user_recovery_questions"):
        op.drop_table("user_recovery_questions")
