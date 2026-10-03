# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for x509_consumer: owns ``x509_trust_anchors``, the
CAs whose client certificates sign in, and ``user_x509_links``, the
certificate identities linked to local users."""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_x509_consumer_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_x509_consumer",)
depends_on: Union[str, Sequence[str], None] = None

ANCHORS = "x509_trust_anchors"
LINKS = "user_x509_links"


def _audit_columns() -> List[sa.schema.SchemaItem]:
    return [
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    ]


def _create_anchors() -> None:
    op.create_table(
        ANCHORS,
        sa.Column(
            "name",
            sa.String(),
            nullable=False,
            comment="Friendly name for this trust anchor",
        ),
        sa.Column(
            "ca_cert_pem",
            sa.String(),
            nullable=False,
            comment="PEM-encoded CA certificate",
        ),
        sa.Column(
            "fingerprint_sha256",
            sa.String(),
            nullable=True,
            comment="SHA-256 fingerprint of the CA certificate (computed)",
        ),
        sa.Column(
            "subject_dn",
            sa.String(),
            nullable=True,
            comment="The CA certificate's subject DN (computed)",
        ),
        sa.Column(
            "identity_attribute",
            sa.String(),
            nullable=False,
            comment="What names the user: subject, subject:CN|UID|emailAddress|"
            "serialNumber, or san:email|upn|uri|dns",
        ),
        sa.Column(
            "trusted_for_email",
            sa.Boolean(),
            nullable=False,
            comment="The CA vouches for the emails in its certificates: a "
            "certificate may sign in to the account with its email",
        ),
        sa.Column(
            "revocation_check",
            sa.String(),
            nullable=False,
            comment="How revocation is checked: none, crl, ocsp, ocsp_then_crl",
        ),
        sa.Column(
            "revocation_required",
            sa.Boolean(),
            nullable=False,
            comment="Refuse a certificate whose revocation status cannot be "
            "established (fail closed)",
        ),
        sa.Column(
            "crl_url",
            sa.String(),
            nullable=True,
            comment="CRL for the certificates this CA issues itself, tried before "
            "their own distribution points",
        ),
        sa.Column(
            "ocsp_url",
            sa.String(),
            nullable=True,
            comment="OCSP responder for the certificates this CA issues itself, "
            "tried before their own AIA",
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            comment="Whether certificates under it sign in",
        ),
        *_audit_columns(),
        comment="Trust anchors for X.509 client certificate sign-in",
        info={"extension": "x509_consumer"},
    )
    op.create_index(f"ix_{ANCHORS}_fingerprint_sha256", ANCHORS, ["fingerprint_sha256"])
    op.create_index(f"ix_{ANCHORS}_deleted_at", ANCHORS, ["deleted_at"])


def _create_links() -> None:
    op.create_table(
        LINKS,
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="The ID of the related user",
        ),
        sa.Column(
            "trust_anchor_id",
            sa.String(),
            nullable=False,
            comment="The trust anchor that vouches for it",
        ),
        sa.Column(
            "identity",
            sa.String(),
            nullable=False,
            comment="The anchor's identity attribute, as the certificate gives it",
        ),
        sa.Column(
            "subject_dn",
            sa.String(),
            nullable=True,
            comment="Last certificate's subject DN",
        ),
        sa.Column(
            "issuer_dn",
            sa.String(),
            nullable=True,
            comment="Last certificate's issuer DN",
        ),
        sa.Column(
            "serial_number",
            sa.String(),
            nullable=True,
            comment="Last certificate's serial number (hex)",
        ),
        sa.Column(
            "fingerprint_sha256",
            sa.String(),
            nullable=True,
            comment="Last certificate's SHA-256 fingerprint",
        ),
        sa.Column(
            "not_after",
            sa.DateTime(),
            nullable=True,
            comment="Last certificate's expiry",
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(),
            nullable=True,
            comment="Last successful certificate sign-in",
        ),
        *_audit_columns(),
        comment="Links a local user to an X.509 certificate identity",
        info={"extension": "x509_consumer"},
    )
    op.create_index(
        f"ix_{LINKS}_anchor_identity", LINKS, ["trust_anchor_id", "identity"]
    )
    op.create_index(f"ix_{LINKS}_user_id", LINKS, ["user_id"])
    op.create_index(f"ix_{LINKS}_deleted_at", LINKS, ["deleted_at"])


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(ANCHORS):
        _create_anchors()
    if not inspector.has_table(LINKS):
        _create_links()


def downgrade() -> None:
    for table, indexes in (
        (LINKS, ("deleted_at", "user_id", "anchor_identity")),
        (ANCHORS, ("deleted_at", "fingerprint_sha256")),
    ):
        for index in indexes:
            op.drop_index(f"ix_{table}_{index}", table_name=table)
        op.drop_table(table)
