# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for oauth_consumer: owns ``o_auth_identities`` (a
user's accounts at identity providers) and ``o_auth_login_states``
(sign-ins begun and not yet finished).

oauth_consumer had no migration before, and the two extensions merged into
it (auth_oauth2_client, oidc_consumer) are gone, so no deployed revision
of this branch exists to keep. A deployment may still hold their tables:
``user_o_auth_links`` (oauth_consumer's runtime create_all fallback),
``user_o_auths``, ``o_auth_providers`` and ``o_auth_external_scopes``
(auth_oauth2_client, revision ``001_auth_oauth2_client_initial`` in
``alembic_version_auth_oauth2_client``; IdP tokens in plain text), and
``o_i_d_c_provider_configs`` / ``user_o_i_d_c_links`` (oidc_consumer's
fallback; a plain-text client secret). They belong to the replaced code
and are left for the operator to drop.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_oauth_consumer_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_oauth_consumer",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "oauth_consumer"}
TABLES = ("o_auth_login_states", "o_auth_identities")


def _record_columns() -> List[sa.Column]:
    return [
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    ]


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def _when(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(), nullable=nullable, comment=comment)


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
    op.create_index(f"ix_{name}_deleted_at", name, ["deleted_at"])


def upgrade() -> None:
    _create(
        "o_auth_identities",
        "A local user's account at an external identity provider (OAuth/OIDC)",
        _text("user_id", "The ID of the related user", nullable=False),
        _text(
            "provider_instance_id",
            "The configured provider (provider instance) it is at",
            nullable=False,
        ),
        _text("provider", "The provider's public name (google, oidc…)", nullable=False),
        _text("issuer", "The OpenID issuer, if any"),
        _text("subject", "The account's stable id at the provider", nullable=False),
        _text("email", "Email the provider reported"),
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            comment="Whether the provider said it verified the email",
        ),
        _text("display_name", "Name the provider reported"),
        _text("access_token", "The provider's access token (encrypted)"),
        _text("refresh_token", "The provider's refresh token (encrypted)"),
        _when("token_expires_at", "When the access token expires"),
        _text("scopes", "Scopes the provider granted"),
        _when("last_login_at", "When it last signed in"),
    )
    _create(
        "o_auth_login_states",
        "OAuth sign-ins begun and not yet completed",
        _text("state_hash", "SHA-256 of the state", nullable=False),
        _text("binding_hash", "SHA-256 of the browser binding", nullable=False),
        _text("provider_instance_id", "The provider signed in with", nullable=False),
        _text("redirect_uri", "The exact redirect URI sent", nullable=False),
        _text("code_verifier", "The PKCE code verifier (encrypted)", nullable=False),
        _text("nonce", "The OpenID nonce sent", nullable=False),
        _text("link_user_id", "The signed-in user linking a provider, if linking"),
        _when("expires_at", "When the state lapses", nullable=False),
        _when("consumed_at", "When a callback spent the state"),
    )


def downgrade() -> None:
    for name in TABLES:
        if sa.inspect(op.get_bind()).has_table(name):
            op.drop_index(f"ix_{name}_deleted_at", table_name=name)
            op.drop_table(name)
