"""provider_instance_scope

Revision ID: a1b2c3d4e5f6
Revises: e0b0dc9d5070
Create Date: 2026-04-29 00:00:00.000000

Item 19: Add ``scope`` column to ``provider_instances``.

The column distinguishes provider scopes per Item 19 — ``root`` (framework-
internal only), ``system`` (SaaS-owned, user-invokable), ``team``
(team-owned), and ``user`` (user-owned). Per Item 80 (rolling-deploy
migration safety), the column is added NOT NULL but with a
``server_default='user'`` so v1 inserts that pre-date this column continue
to succeed during the rolling deploy window.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "e0b0dc9d5070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add provider_instances.scope (NOT NULL, server_default='user')."""
    op.add_column(
        "provider_instances",
        sa.Column(
            "scope",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'user'"),
            comment=(
                "Item 19 provider scope: root, system, team, or user. "
                "Root is framework-internal only; system/team/user are "
                "user-invokable via the unified Quota model."
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema: drop provider_instances.scope."""
    op.drop_column("provider_instances", "scope")
