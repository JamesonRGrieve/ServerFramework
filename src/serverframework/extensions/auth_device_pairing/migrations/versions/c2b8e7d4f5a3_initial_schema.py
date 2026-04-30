"""initial schema for auth_device_pairing

Revision ID: c2b8e7d4f5a3
Revises:
Create Date: 2026-04-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2b8e7d4f5a3"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_auth_device_pairing",)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "device_pairing_requests",
        sa.Column(
            "requesting_device_type",
            sa.String(),
            nullable=False,
            comment="mobile / desktop / web / unknown",
        ),
        sa.Column(
            "requesting_device_name",
            sa.String(),
            nullable=True,
            comment="Human-friendly device label",
        ),
        sa.Column(
            "requesting_ip",
            sa.String(),
            nullable=True,
            comment="IP address that requested the pairing",
        ),
        sa.Column(
            "requesting_user_agent",
            sa.String(),
            nullable=True,
            comment="User-agent string that requested the pairing",
        ),
        sa.Column(
            "approver_user_id",
            sa.String(),
            nullable=True,
            comment="User who approved this pairing, set on approval",
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(),
            nullable=True,
            comment="When this pairing was approved",
        ),
        sa.Column(
            "denied_at",
            sa.DateTime(),
            nullable=True,
            comment="When this pairing was denied",
        ),
        sa.Column(
            "pending_session_id",
            sa.String(),
            nullable=True,
            comment="Session row id created at approval time",
        ),
        sa.Column(
            "code_hash",
            sa.String(),
            nullable=False,
            comment="bcrypt hash of the raw pairing token",
        ),
        sa.Column(
            "code_salt",
            sa.String(),
            nullable=False,
            comment="Salt used when hashing the pairing token",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(),
            nullable=False,
            comment="UTC expiry timestamp",
        ),
        sa.Column(
            "is_used",
            sa.Boolean(),
            nullable=False,
            comment="Whether the pairing token has been redeemed",
        ),
        sa.Column(
            "used_at",
            sa.DateTime(),
            nullable=True,
            comment="When the token was redeemed",
        ),
        sa.Column(
            "created_ip",
            sa.String(),
            nullable=True,
            comment="IP address that generated the token",
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Device pairing requests (Item 59 cross-device passwordless auth)",
        info={
            "source_module": "serverframework.extensions.auth_device_pairing.BLL_Auth_DevicePairing",
            "extension": "auth_device_pairing",
        },
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("device_pairing_requests")
