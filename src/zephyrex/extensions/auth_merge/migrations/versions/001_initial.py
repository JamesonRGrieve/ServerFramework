# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for auth_merge: `user_merges`.

Generated from the extension's own models. The Inspector guards make it a no-op on a database
that already has the tables (made from the models before this extension shipped migrations).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_auth_merge_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_auth_merge",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {
    "source_module": "zephyrex.extensions.auth_merge.BLL_Auth_Merge",
    "extension": "auth_merge",
}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("user_merges"):
        op.create_table(
            "user_merges",
            sa.Column(
                "initiating_user_id",
                sa.String(),
                nullable=False,
                comment="User who survives the merge (data-owner)",
            ),
            sa.Column(
                "target_user_id",
                sa.String(),
                nullable=False,
                comment="User being merged into the initiating user; deactivated.",
            ),
            sa.Column(
                "completed_at",
                sa.DateTime(),
                nullable=True,
                comment="When the side-data transfer finished",
            ),
            sa.Column(
                "handler_errors",
                sa.String(),
                nullable=True,
                comment="JSON map of handler name -> error string for any merge handler that raised. Empty/None when every handler succeeded.",
            ),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Audit record of user-account consolidation events",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_user_merges_deleted_at", "user_merges", ["deleted_at"], unique=False
        )


def downgrade() -> None:
    if _has_table("user_merges"):
        op.drop_table("user_merges")
