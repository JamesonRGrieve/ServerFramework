"""provider_instance_auth_strategy_name

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-06 00:00:00.000000

Item 10: Add ``auth_strategy_name`` column to ``provider_instances``.

Per-instance override for the AuthStrategy registry name (e.g. ``"oauth2"``
for a Stripe Connect user whose provider class defaults to ``"api_key"``).
NULL inherits the provider class's ``auth_strategy_name`` ClassVar via
``AbstractStaticProvider.build_auth_strategy``. Per Item 80 (rolling-deploy
migration safety), nullable additive columns are forward- and backward-
compatible with both v1 (pre-column) and v2 (post-column) inserts.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add provider_instances.auth_strategy_name (nullable)."""
    op.add_column(
        "provider_instances",
        sa.Column(
            "auth_strategy_name",
            sa.String(length=64),
            nullable=True,
            comment=(
                "Item 10 per-instance AuthStrategy registry name override. "
                "NULL inherits the provider class's `auth_strategy_name` "
                "ClassVar. Common values: 'api_key', 'oauth2', 'jwt_bearer', "
                "'basic', 'mtls', 'aws_sigv4'."
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema: drop provider_instances.auth_strategy_name."""
    op.drop_column("provider_instances", "auth_strategy_name")
