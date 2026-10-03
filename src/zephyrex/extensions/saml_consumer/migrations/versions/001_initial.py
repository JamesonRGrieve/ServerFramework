# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for saml_consumer: the IdPs users sign in through,
their identities at them, outstanding AuthnRequests, and the request and
assertion IDs already used.

Before this the extension had no migration; a deployment may hold the
tables of the replaced models (``SAMLIdPConfigModel``,
``UserSAMLLinkModel``) from the runtime create_all fallback. Nothing ever
signed in through them; they are left for the operator to drop.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_saml_consumer_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_saml_consumer",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "saml_consumer"}
SOFT_DELETED = ("saml_identity_providers", "saml_identities", "saml_authn_requests")


def _created_columns() -> List[sa.Column]:
    return [
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
    ]


def _updated_columns() -> List[sa.Column]:
    return [
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    ]


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def _flag(name: str, comment: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), nullable=False, comment=comment)


def _create(
    name: str, comment: str, *columns: sa.Column, soft_deleted: bool = True
) -> None:
    if sa.inspect(op.get_bind()).has_table(name):
        return
    op.create_table(
        name,
        *columns,
        *_created_columns(),
        *(_updated_columns() if soft_deleted else []),
        sa.PrimaryKeyConstraint("id"),
        comment=comment,
        info=TABLE_INFO,
    )
    if soft_deleted:
        op.create_index(f"ix_{name}_deleted_at", name, ["deleted_at"])


def upgrade() -> None:
    _create(
        "saml_identity_providers",
        "External SAML 2.0 identity providers, managed by ROOT",
        _text("name", "The name", nullable=False),
        _text("entity_id", "The IdP's entity ID, read from its metadata"),
        _text("metadata_xml", "The IdP's SAML 2.0 metadata", nullable=False),
        _text("metadata_url", "Where the metadata was fetched from, to refresh it"),
        _text(
            "sp_entity_id",
            "This SP's entity ID for the IdP (default: the SP metadata URL)",
        ),
        _text("sp_certificate", "SP certificate, PEM (write-only)"),
        _text(
            "sp_private_key",
            "SP signing/decryption key, PEM (write-only, stored encrypted)",
        ),
        _flag("sp_key_configured", "Whether an SP key and certificate are set"),
        _text("name_id_format", "The NameID format to request"),
        _text(
            "identity_attribute",
            "An attribute identifying the user, instead of the NameID",
        ),
        _text("email_attribute", "The attribute carrying the email"),
        _flag("emails_verified", "The IdP vouches for its users' emails"),
        _flag("allow_unsolicited", "Accept IdP-initiated (unsolicited) responses"),
        _flag("want_assertions_signed", "Require the Assertion to be signed"),
        _flag("want_response_signed", "Require the Response to be signed"),
        _flag("is_enabled", "Whether users may sign in through it"),
    )
    _create(
        "saml_identities",
        "Links a local user to the identity a SAML IdP asserts",
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="The ID of the related user",
        ),
        _text(
            "identity_provider_id",
            "The SamlIdentityProvider it was asserted through",
            nullable=False,
        ),
        _text("idp_entity_id", "The asserting IdP's entity ID", nullable=False),
        _text(
            "subject",
            "The NameID, or the configured identity attribute",
            nullable=False,
        ),
        _text("subject_format", "The NameID format"),
        _text("asserted_email", "The email the IdP last asserted"),
        sa.Column(
            "last_login_at",
            sa.DateTime(),
            nullable=True,
            comment="Last sign-in through it",
        ),
    )
    _create(
        "saml_authn_requests",
        "Outstanding SAML AuthnRequests",
        _text("request_id", "The AuthnRequest ID", nullable=False),
        _text("identity_provider_id", "The IdP it was sent to", nullable=False),
        _text("return_to", "Where the browser goes after signing in", nullable=False),
        _text(
            "browser_key_hash",
            "SHA-256 of the browser's request cookie",
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(),
            nullable=False,
            comment="When the request stops being answerable",
        ),
    )
    _create(
        "saml_spent_messages",
        "SAML request and assertion IDs already used (replay protection)",
        sa.Column(
            "spent_key",
            sa.String(),
            nullable=False,
            unique=True,
            comment="request:<id> or assertion:<issuer>:<id>",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(),
            nullable=False,
            comment="When the message could no longer be accepted anyway",
        ),
        soft_deleted=False,
    )


def downgrade() -> None:
    op.drop_table("saml_spent_messages")
    for name in SOFT_DELETED:
        op.drop_index(f"ix_{name}_deleted_at", table_name=name)
        op.drop_table(name)
