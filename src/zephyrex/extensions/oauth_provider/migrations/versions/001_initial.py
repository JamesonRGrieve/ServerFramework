# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for oauth_provider, the authorization server and
OpenID Connect provider: ``oauth_clients``, ``oauth_grants``,
``oauth_authorization_requests``, ``oauth_authorization_codes``,
``oauth_tokens`` and ``oauth_signing_keys``.

Before this, oauth_provider had no migration, and two other extensions
served the same purpose. A deployment may hold their tables:
``o_auth2_clients`` (client secrets in plain text), ``o_auth2_auth_codes``
and ``o_auth2_tokens`` (raw tokens), with ``alembic_version_auth_oauth2_server``
(the replaced auth_oauth2_server, revision ``001_auth_oauth2_server_initial``),
and ``o_i_d_c_signing_keys`` from the replaced oidc_provider's create_all
fallback. They belong to the replaced code (``o_auth2_clients`` and
``o_auth2_tokens`` hold live credentials in plain text) and are left for
the operator to drop.
"""

from typing import Any, Callable, List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_oauth_provider_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_oauth_provider",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "oauth_provider"}
TABLES = (
    "oauth_signing_keys",
    "oauth_tokens",
    "oauth_authorization_codes",
    "oauth_authorization_requests",
    "oauth_grants",
    "oauth_clients",
)


def _audit_columns() -> List[sa.Column]:
    return [
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    ]


def _user_column() -> sa.Column:
    return sa.Column(
        "user_id", sa.String(), nullable=False, comment="The ID of the related user"
    )


def _text(name: str, nullable: bool = False, comment: str = "") -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment or None)


def _moment(name: str, nullable: bool = False, comment: str = "") -> sa.Column:
    return sa.Column(name, sa.DateTime(), nullable=nullable, comment=comment or None)


def _create(name: str, comment: str, columns: Callable[[], List[Any]]) -> None:
    if sa.inspect(op.get_bind()).has_table(name):
        return
    op.create_table(
        name,
        *columns(),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        comment=comment,
        info=TABLE_INFO,
    )
    op.create_index(f"ix_{name}_deleted_at", name, ["deleted_at"], unique=False)


def upgrade() -> None:
    _create(
        "oauth_clients",
        "OAuth 2.0 / OpenID Connect registered clients",
        lambda: [
            _user_column(),
            _text("name", comment="Name shown to users on the consent screen"),
            _text("client_id", comment="Public client identifier"),
            _text(
                "client_secret_hash",
                nullable=True,
                comment="SHA-256 of the client secret (confidential clients; write-only)",
            ),
            sa.Column(
                "is_confidential",
                sa.Boolean(),
                nullable=False,
                comment="Whether the client authenticates with a secret",
            ),
            _text("application_type", comment="'web' or 'native'"),
            _text(
                "token_endpoint_auth_method",
                comment="client_secret_basic, client_secret_post, or none (public)",
            ),
            _text("redirect_uris", comment="JSON array of registered redirect URIs"),
            _text("allowed_scopes", comment="Space-delimited scopes it may request"),
            _text(
                "resource_uri",
                nullable=True,
                comment="The protected resource this client is (an access token "
                "audience); set by the operator only",
            ),
            _text(
                "id_token_signed_response_alg",
                comment="Algorithm its ID tokens are signed with",
            ),
            sa.Column(
                "is_enabled",
                sa.Boolean(),
                nullable=False,
                comment="Whether the client may be used",
            ),
            sa.UniqueConstraint("client_id"),
        ],
    )
    _create(
        "oauth_grants",
        "Users' consents to OAuth clients",
        lambda: [
            _user_column(),
            _text("client_id", comment="The client the consent is for"),
            _text("client_name", comment="The client's name when consent was given"),
            _text("scopes", comment="Space-delimited scopes consented to"),
        ],
    )
    _create(
        "oauth_authorization_requests",
        "Pending OAuth authorization requests",
        lambda: [
            sa.Column(
                "user_id",
                sa.String(),
                nullable=True,
                comment="Optional foreign key to UserModel",
            ),
            _text("client_id"),
            _text("redirect_uri"),
            _text("scopes"),
            _text("state", nullable=True),
            _text("nonce", nullable=True),
            _text("code_challenge", comment="PKCE S256 challenge"),
            _text("prompt", nullable=True),
            sa.Column("max_age", sa.Integer(), nullable=True),
            _text("resources", comment="JSON array of RFC 8707 resource indicators"),
            _moment("expires_at"),
            _moment("decided_at", nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        ],
    )
    _create(
        "oauth_authorization_codes",
        "OAuth authorization codes (digest only, single-use)",
        lambda: [
            _user_column(),
            _text("code_hash"),
            _text("client_id"),
            _text("redirect_uri"),
            _text("scopes"),
            _text("nonce", nullable=True),
            _text("code_challenge"),
            _text("resources"),
            _moment("auth_time", comment="When the user authenticated"),
            _moment("expires_at"),
            _moment("used_at", nullable=True),
            sa.UniqueConstraint("code_hash"),
        ],
    )
    _create(
        "oauth_tokens",
        "OAuth access and refresh tokens (digest only)",
        lambda: [
            _user_column(),
            _text("token_hash"),
            _text("token_type", comment="access_token or refresh_token"),
            _text("client_id"),
            _text("scopes"),
            _text(
                "audience",
                comment="Access token: its audiences. Refresh token: the "
                "resources authorized (JSON arrays)",
            ),
            _text("family_id", comment="The authorization the token descends from"),
            _moment("auth_time"),
            _moment("expires_at"),
            _moment("used_at", nullable=True, comment="When a refresh token rotated"),
            _moment("revoked_at", nullable=True),
            sa.UniqueConstraint("token_hash"),
        ],
    )
    _create(
        "oauth_signing_keys",
        "OpenID Connect ID token signing keys",
        lambda: [
            _text("kid", comment="RFC 7638 thumbprint"),
            _text("algorithm"),
            _text("public_jwk", comment="The public key as a JWK (JSON)"),
            _text(
                "private_key",
                nullable=True,
                comment="PKCS#8 PEM, encrypted at rest (write-only)",
            ),
            _text("status"),
            _moment("activated_at"),
            _moment("retiring_at", nullable=True),
            sa.UniqueConstraint("kid"),
        ],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name in TABLES:
        if inspector.has_table(name):
            op.drop_index(f"ix_{name}_deleted_at", table_name=name)
            op.drop_table(name)
