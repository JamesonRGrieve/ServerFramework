# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for auth_oauth2_client.

Owns the OAuth2-client (external-IdP SSO) tables: ``user_o_auths``,
``o_auth_providers``, ``o_auth_external_scopes``. Table/column names match the
pydantic2-synthesized models in ``BLL_Auth_OAuth2Client``. Inspector guards keep
the upgrade a no-op where a table already exists (e.g. a dev DB that create_all'd
the models).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_auth_oauth2_client_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_auth_oauth2_client",)
depends_on: Union[str, Sequence[str], None] = None

_INFO = {
    "source_module": "zephyrex.extensions.auth_oauth2_client.BLL_Auth_OAuth2Client",
    "extension": "auth_oauth2_client",
}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _existing_index_names(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table)}


def _base_audit_columns() -> list:
    return [
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
    ]


def upgrade() -> None:
    if not _has_table("user_o_auths"):
        op.create_table(
            "user_o_auths",
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("provider", sa.String(), nullable=True),
            sa.Column("provider_user_id", sa.String(), nullable=True),
            sa.Column("account_email", sa.String(), nullable=True),
            sa.Column("account_name", sa.String(), nullable=True),
            sa.Column("access_token", sa.String(), nullable=True),
            sa.Column("refresh_token", sa.String(), nullable=True),
            *_base_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Links a local user to an external OAuth2 identity (SSO)",
            info=_INFO,
        )

    if not _has_table("o_auth_providers"):
        op.create_table(
            "o_auth_providers",
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("client_id", sa.String(), nullable=True),
            sa.Column("auth_url", sa.String(), nullable=True),
            sa.Column("token_url", sa.String(), nullable=True),
            sa.Column("userinfo_url", sa.String(), nullable=True),
            *_base_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Admin-configurable external OAuth2 providers",
            info=_INFO,
        )

    if not _has_table("o_auth_external_scopes"):
        op.create_table(
            "o_auth_external_scopes",
            sa.Column("provider", sa.String(), nullable=True),
            sa.Column("scope_name", sa.String(), nullable=True),
            *_base_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Per-provider external OAuth2 scopes",
            info=_INFO,
        )

    if "ix_user_o_auths_user_id" not in _existing_index_names("user_o_auths"):
        op.create_index("ix_user_o_auths_user_id", "user_o_auths", ["user_id"])


def downgrade() -> None:
    if "ix_user_o_auths_user_id" in _existing_index_names("user_o_auths"):
        op.drop_index("ix_user_o_auths_user_id", table_name="user_o_auths")
    for table in ("o_auth_external_scopes", "o_auth_providers", "user_o_auths"):
        if _has_table(table):
            op.drop_table(table)
