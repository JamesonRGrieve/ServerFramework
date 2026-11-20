"""a sign-up link's token names no user until its click makes the account

Revision ID: c3d7e1f20a45
Revises: 8b2e5a4c1d09
Create Date: 2026-11-20 00:00:00.000000

Magic-link sign-up (operator, 2026-11-20: "Create on link click"): a token can
be issued for an email with no account, and the account is created when its
owner clicks the link. Until then the token's user_id is NULL.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d7e1f20a45"
down_revision: Union[str, None] = "8b2e5a4c1d09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("auth_magic_link_tokens") as batch:
        batch.alter_column("user_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    # Unredeemed sign-up tokens have no user to keep; drop them first.
    op.execute("DELETE FROM auth_magic_link_tokens WHERE user_id IS NULL")
    with op.batch_alter_table("auth_magic_link_tokens") as batch:
        batch.alter_column("user_id", existing_type=sa.String(), nullable=False)
