"""add code_fingerprint column for indexed token lookup (H-5)

Revision ID: 8b2e5a4c1d09
Revises: 9a1f4d3b2c10
Create Date: 2026-05-07 00:00:00.000000

See auth_device_pairing's parallel migration for context. Outstanding
unredeemed magic links must be re-issued post-deploy.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8b2e5a4c1d09"
down_revision: Union[str, None] = "9a1f4d3b2c10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "auth_magic_link_tokens",
        sa.Column(
            "code_fingerprint",
            sa.String(),
            nullable=True,
            comment=(
                "HMAC-SHA256 fingerprint of the raw magic-link token (H-5). "
                "Indexed for verify-time lookup."
            ),
        ),
    )
    op.create_index(
        "ix_auth_magic_link_tokens_code_fingerprint",
        "auth_magic_link_tokens",
        ["code_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auth_magic_link_tokens_code_fingerprint",
        table_name="auth_magic_link_tokens",
    )
    op.drop_column("auth_magic_link_tokens", "code_fingerprint")
