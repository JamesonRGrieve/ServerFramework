# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for auth_oauth2_server.

Owns the OAuth2 authorization-server tables: ``o_auth2_clients``,
``o_auth2_auth_codes``, ``o_auth2_tokens``. Table/column names match the
pydantic2-synthesized SQLAlchemy models in ``BLL_Auth_OAuth2Server``. The
Inspector guards keep the upgrade a no-op where a table already exists (e.g.
a dev DB that create_all'd the models).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_auth_oauth2_server_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_auth_oauth2_server",)
depends_on: Union[str, Sequence[str], None] = None

_INFO = {
    "source_module": "zephyrex.extensions.auth_oauth2_server.BLL_Auth_OAuth2Server",
    "extension": "auth_oauth2_server",
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
    if not _has_table("o_auth2_clients"):
        op.create_table(
            "o_auth2_clients",
            sa.Column("team_id", sa.String(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("client_id", sa.String(), nullable=True),
            sa.Column("client_secret", sa.String(), nullable=True),
            sa.Column("is_confidential", sa.Boolean(), nullable=True),
            sa.Column("redirect_uris", sa.String(), nullable=True),
            sa.Column("allowed_scopes", sa.String(), nullable=True),
            *_base_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Registered OAuth2 clients (third-party applications)",
            info=_INFO,
        )

    if not _has_table("o_auth2_auth_codes"):
        op.create_table(
            "o_auth2_auth_codes",
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("client_id", sa.String(), nullable=True),
            sa.Column("code", sa.String(), nullable=True),
            sa.Column("redirect_uri", sa.String(), nullable=True),
            sa.Column("scopes", sa.String(), nullable=True),
            sa.Column("code_challenge", sa.String(), nullable=True),
            sa.Column("code_challenge_method", sa.String(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("is_used", sa.Boolean(), nullable=True),
            *_base_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Single-use OAuth2 authorization codes (carry the PKCE challenge)",
            info=_INFO,
        )

    if not _has_table("o_auth2_tokens"):
        op.create_table(
            "o_auth2_tokens",
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("client_id", sa.String(), nullable=True),
            sa.Column("token", sa.String(), nullable=True),
            sa.Column("token_type", sa.String(), nullable=True),
            sa.Column("scopes", sa.String(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("is_revoked", sa.Boolean(), nullable=True),
            sa.Column("parent_id", sa.String(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            *_base_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Opaque, revocable OAuth2 access/refresh tokens",
            info=_INFO,
        )

    # Hot-path lookup indexes: client by public id, code by value, token by value.
    if "ix_o_auth2_clients_client_id" not in _existing_index_names("o_auth2_clients"):
        op.create_index(
            "ix_o_auth2_clients_client_id", "o_auth2_clients", ["client_id"]
        )
    if "ix_o_auth2_auth_codes_code" not in _existing_index_names("o_auth2_auth_codes"):
        op.create_index("ix_o_auth2_auth_codes_code", "o_auth2_auth_codes", ["code"])
    if "ix_o_auth2_tokens_token" not in _existing_index_names("o_auth2_tokens"):
        op.create_index("ix_o_auth2_tokens_token", "o_auth2_tokens", ["token"])


def downgrade() -> None:
    for idx, table in (
        ("ix_o_auth2_tokens_token", "o_auth2_tokens"),
        ("ix_o_auth2_auth_codes_code", "o_auth2_auth_codes"),
        ("ix_o_auth2_clients_client_id", "o_auth2_clients"),
    ):
        if idx in _existing_index_names(table):
            op.drop_index(idx, table_name=table)

    for table in ("o_auth2_tokens", "o_auth2_auth_codes", "o_auth2_clients"):
        if _has_table(table):
            op.drop_table(table)
