# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for x509_provider: owns ``x509_issued_certificates``,
the record of each client certificate the CA issued (and its revocation),
from which the CRL is signed.

Before this, x509_provider had no migration; a deployment may hold the
scaffold's ``issued_certificates`` and ``x509_provider_configs`` tables from
the runtime create_all fallback. Nothing reads them (the CA now lives in a
provider instance); they are left for the operator to drop.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_x509_provider_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_x509_provider",)
depends_on: Union[str, Sequence[str], None] = None

TABLE = "x509_issued_certificates"
INDEXED = ("deleted_at", "user_id", "ca_fingerprint_sha256")


def _text(name: str, comment: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def _moment(name: str, comment: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(), nullable=nullable, comment=comment)


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="The ID of the related user",
        ),
        _text("subject_dn", "The certificate's subject DN"),
        _text("serial_number", "The serial number (hex)"),
        _text("fingerprint_sha256", "The certificate's SHA-256"),
        _text("ca_fingerprint_sha256", "The issuing CA's SHA-256"),
        _moment("not_before", "Valid from"),
        _moment("not_after", "Valid until"),
        _text("certificate_pem", "The certificate (PEM)"),
        sa.Column(
            "is_revoked",
            sa.Boolean(),
            nullable=False,
            comment="Whether it has been revoked",
        ),
        _moment("revoked_at", "When it was revoked", nullable=True),
        _text("revocation_reason", "Why it was revoked", nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Client certificates issued by this server's CA",
        info={"extension": "x509_provider"},
    )
    for column in INDEXED:
        op.create_index(f"ix_{TABLE}_{column}", TABLE, [column])


def downgrade() -> None:
    for column in INDEXED:
        op.drop_index(f"ix_{TABLE}_{column}", table_name=TABLE)
    op.drop_table(TABLE)
