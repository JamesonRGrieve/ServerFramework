"""initial schema for auth_magic_link

Revision ID: 9a1f4d3b2c10
Revises:
Create Date: 2026-04-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a1f4d3b2c10"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_auth_magic_link",)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "auth_magic_link_tokens",
        sa.Column(
            "user_id",
            sa.String(),
            nullable=False,
            comment="User this token authenticates",
        ),
        sa.Column(
            "requested_email",
            sa.String(),
            nullable=False,
            comment="Email the token was issued for; binds token to identity",
        ),
        sa.Column(
            "code_hash",
            sa.String(),
            nullable=False,
            comment="bcrypt hash of the raw token code",
        ),
        sa.Column(
            "code_salt",
            sa.String(),
            nullable=False,
            comment="Salt used when hashing the token code",
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
            comment="Whether this token has been redeemed",
        ),
        sa.Column(
            "used_at",
            sa.DateTime(),
            nullable=True,
            comment="When the token was redeemed, if any",
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="Magic-link one-time tokens (Item 58 passwordless email auth)",
        info={
            "source_module": "zephyrex.extensions.auth_magic_link.BLL_Auth_MagicLink",
            "extension": "auth_magic_link",
        },
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("auth_magic_link_tokens")
