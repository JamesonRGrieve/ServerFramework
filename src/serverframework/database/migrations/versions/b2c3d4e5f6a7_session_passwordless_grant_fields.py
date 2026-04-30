"""session_passwordless_grant_fields

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-30 00:00:00.000000

Items 58/59 framework provisions: add ``grant_type`` and ``pending_state``
columns to the ``sessions`` table so the magic-link and device-pairing
extensions can record which grant kind issued a session and gate
cross-device sessions awaiting approval. Both are nullable by design — the
existing password ``login`` flow leaves them ``NULL``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add sessions.grant_type and sessions.pending_state."""
    op.add_column(
        "sessions",
        sa.Column(
            "grant_type",
            sa.String(),
            nullable=True,
            comment=(
                "Identifier of the grant kind that issued this session "
                "(e.g. magic_link, device_pairing). NULL for legacy/password."
            ),
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "pending_state",
            sa.String(),
            nullable=True,
            comment=(
                "Pending approval state for cross-device passwordless "
                "flows: awaiting_approval, approved, or denied."
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema: drop sessions.pending_state and sessions.grant_type."""
    op.drop_column("sessions", "pending_state")
    op.drop_column("sessions", "grant_type")
