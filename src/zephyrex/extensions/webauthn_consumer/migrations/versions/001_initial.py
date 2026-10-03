# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for webauthn_consumer: owns ``web_authn_credentials``
(users' registered passkeys and security keys) and
``web_authn_ceremonies`` (single-use ceremony challenges).

The extension had no migration before; a deployment whose runtime
create_all fallback made an older ``web_authn_credentials`` keeps it, and
that table lacks the attestation and backup columns, so it is left for the
operator to drop."""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_webauthn_consumer_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_webauthn_consumer",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "webauthn_consumer"}
CREDENTIALS = "web_authn_credentials"
CEREMONIES = "web_authn_ceremonies"


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


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(CREDENTIALS):
        op.create_table(
            CREDENTIALS,
            sa.Column(
                "user_id",
                sa.String(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="The ID of the related user",
            ),
            sa.Column(
                "credential_id",
                sa.String(),
                nullable=False,
                unique=True,
                comment="Base64url credential ID",
            ),
            sa.Column(
                "public_key",
                sa.String(),
                nullable=False,
                comment="Base64url COSE public key",
            ),
            sa.Column(
                "sign_count",
                sa.Integer(),
                nullable=False,
                comment="Last signature counter seen",
            ),
            sa.Column(
                "aaguid",
                sa.String(),
                nullable=True,
                comment="Authenticator model GUID",
            ),
            sa.Column(
                "attestation_format",
                sa.String(),
                nullable=False,
                comment="Attestation statement format",
            ),
            sa.Column(
                "attestation_trust",
                sa.String(),
                nullable=False,
                comment="none, self, unverified or trusted",
            ),
            sa.Column(
                "device_name",
                sa.String(),
                nullable=True,
                comment="The user's name for it",
            ),
            sa.Column(
                "transports",
                sa.String(),
                nullable=True,
                comment="Space-separated transports (usb, nfc, ble, internal, …)",
            ),
            sa.Column(
                "is_discoverable",
                sa.Boolean(),
                nullable=False,
                comment="A discoverable credential (passkey), per credProps",
            ),
            sa.Column(
                "backup_eligible",
                sa.Boolean(),
                nullable=False,
                comment="May be synced across devices",
            ),
            sa.Column(
                "backed_up",
                sa.Boolean(),
                nullable=False,
                comment="Is synced, as last asserted",
            ),
            sa.Column(
                "last_used_at",
                sa.DateTime(),
                nullable=True,
                comment="Last successful assertion",
            ),
            sa.Column(
                "clone_detected_at",
                sa.DateTime(),
                nullable=True,
                comment="When a non-advancing counter disabled it",
            ),
            sa.Column(
                "is_enabled",
                sa.Boolean(),
                nullable=False,
                comment="Usable for sign-in",
            ),
            *_record_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="WebAuthn credentials registered to users",
            info=TABLE_INFO,
        )
    if not inspector.has_table(CEREMONIES):
        op.create_table(
            CEREMONIES,
            sa.Column(
                "user_id",
                sa.String(),
                sa.ForeignKey("users.id"),
                nullable=True,
                comment="Optional foreign key to UserModel",
            ),
            sa.Column(
                "ceremony",
                sa.String(),
                nullable=False,
                comment="registration, sign_in or second_factor",
            ),
            sa.Column(
                "challenge",
                sa.String(),
                nullable=False,
                comment="Base64url challenge",
            ),
            sa.Column(
                "binding",
                sa.String(),
                nullable=True,
                comment="SHA-256 of the session or MFA token it is bound to",
            ),
            sa.Column(
                "expires_at",
                sa.DateTime(),
                nullable=False,
                comment="When it stops being redeemable",
            ),
            sa.Column(
                "used_at",
                sa.DateTime(),
                nullable=True,
                comment="When it was spent",
            ),
            *_record_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="WebAuthn ceremony challenges, single use",
            info=TABLE_INFO,
        )


def downgrade() -> None:
    op.drop_table(CEREMONIES)
    op.drop_table(CREDENTIALS)
