"""Initial migration for auth_lockout — owns `failed_login_attempts`.

The `failed_login_attempts` table was originally created by core in
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


revision: str = "001_auth_lockout_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("auth_lockout",)
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _existing_index_names(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    if not _has_table("failed_login_attempts"):
        op.create_table(
            "failed_login_attempts",
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("ip_address", sa.String(), nullable=True),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Failed login attempts for rate limiting and lockout",
            info={
                "source_module": "zephyrex.extensions.auth_lockout.BLL_Lockout",
                "extension": "auth_lockout",
            },
        )

    existing = _existing_index_names("failed_login_attempts")
    if "ix_failed_login_attempts_user_created" not in existing:
        op.create_index(
            "ix_failed_login_attempts_user_created",
            "failed_login_attempts",
            ["user_id", "created_at"],
        )


def downgrade() -> None:
    existing = _existing_index_names("failed_login_attempts")
    if "ix_failed_login_attempts_user_created" in existing:
        op.drop_index(
            "ix_failed_login_attempts_user_created",
            table_name="failed_login_attempts",
        )
    if _has_table("failed_login_attempts"):
        op.drop_table("failed_login_attempts")
