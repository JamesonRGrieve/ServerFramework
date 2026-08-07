"""add code_fingerprint column for indexed token lookup (H-5)

Revision ID: d3a9c0e1f2b4
Revises: c2b8e7d4f5a3
Create Date: 2026-05-07 00:00:00.000000

The H-5 fix adds an HMAC fingerprint to OneTimeTokenMixin so verify-time
lookup is a single indexed row read instead of a bcrypt-against-every-
unused-token loop. Existing rows have NULL fingerprint and will be
unable to verify until they are re-issued (the fingerprint key derives
from FRAMEWORK_FERNET_KEY/JWT_SECRET — the framework cannot reconstruct
it from old rows). Outstanding tokens must be re-issued post-deploy.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d3a9c0e1f2b4"
down_revision: Union[str, None] = "c2b8e7d4f5a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "device_pairing_requests",
        sa.Column(
            "code_fingerprint",
            sa.String(),
            nullable=True,
            comment=(
                "HMAC-SHA256 fingerprint of the raw token (H-5). Indexed; "
                "replaces the unbounded bcrypt-loop verify."
            ),
        ),
    )
    op.create_index(
        "ix_device_pairing_requests_code_fingerprint",
        "device_pairing_requests",
        ["code_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_device_pairing_requests_code_fingerprint",
        table_name="device_pairing_requests",
    )
    op.drop_column("device_pairing_requests", "code_fingerprint")
