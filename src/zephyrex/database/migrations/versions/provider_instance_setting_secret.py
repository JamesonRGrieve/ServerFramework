# SPDX-License-Identifier: AGPL-3.0-or-later
"""provider_instance_setting_secret

Revision ID: provider_instance_setting_secret
Revises: team_retired_columns_expand

Adds ``provider_instance_settings.write_only``. A secret setting (one its
provider declares secret, or a caller marked) is stored encrypted and its
value is never returned. Existing rows were written in the clear, so they
start not-secret; rewriting one through the API seals it. A boolean with a
server default is safe for v1 and v2 inserts alike (Item 80).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "provider_instance_setting_secret"
down_revision: Union[str, None] = "team_retired_columns_expand"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "provider_instance_settings",
        sa.Column(
            "write_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="Write-only: its value is stored encrypted and never returned",
        ),
    )


def downgrade() -> None:
    op.drop_column("provider_instance_settings", "write_only")
