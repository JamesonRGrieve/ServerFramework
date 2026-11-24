# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for auth_api_keys: `a_p_i_keys`.

Generated from the extension's own models. The Inspector guards make it a no-op on a database
that already has the tables (made from the models before this extension shipped migrations).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_auth_api_keys_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_auth_api_keys",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {
    "source_module": "zephyrex.extensions.auth_api_keys.BLL_Auth_APIKeys",
    "extension": "auth_api_keys",
}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("a_p_i_keys"):
        op.create_table(
            "a_p_i_keys",
            sa.Column(
                "role_id",
                sa.String(),
                sa.ForeignKey("roles.id"),
                nullable=True,
                comment="Optional foreign key to RoleModel",
            ),
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
            sa.Column(
                "name",
                sa.String(),
                nullable=False,
                comment="Human-readable label for this key",
            ),
            sa.Column(
                "key_hash",
                sa.String(),
                nullable=True,
                comment="SHA-256 hex digest of the issued key",
            ),
            sa.Column(
                "last_used_at",
                sa.DateTime(),
                nullable=True,
                comment="When the key was most recently presented and accepted",
            ),
            sa.Column(
                "expires_at",
                sa.DateTime(),
                nullable=True,
                comment="When the key stops being honoured. None = no expiry.",
            ),
            sa.Column(
                "is_revoked", sa.Boolean(), nullable=False, comment="Soft-revoke flag"
            ),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="API keys for programmatic access; hashed at rest, scoped to user/team/role",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_a_p_i_keys_deleted_at", "a_p_i_keys", ["deleted_at"], unique=False
        )


def downgrade() -> None:
    if _has_table("a_p_i_keys"):
        op.drop_table("a_p_i_keys")
