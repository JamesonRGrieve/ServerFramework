# SPDX-License-Identifier: AGPL-3.0-or-later
"""bind a pairing's session to the device that requested it

Revision ID: e5c1f2a3b4d6
Revises: d3a9c0e1f2b4
Create Date: 2026-09-30 00:00:00.000000

The approved session used to be readable by anyone holding the pairing id.
A pairing now stores the hash of a binding secret handed only to the
requesting device, how that device takes delivery of its session, and when
it did: the session is delivered once, to the binding holder. Pairings
created before this revision have no binding and so never deliver a
session; they expire within PAIRING_TTL_SECONDS.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5c1f2a3b4d6"
down_revision: Union[str, None] = "d3a9c0e1f2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "device_pairing_requests"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "binding_hash",
            sa.String(),
            nullable=True,
            comment="SHA-256 of the requesting device's binding secret",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "token_in_body",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="Deliver the session as a bearer token, not cookies",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "consumed_at",
            sa.DateTime(),
            nullable=True,
            comment="When the requesting device took delivery of its session",
        ),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "consumed_at")
    op.drop_column(_TABLE, "token_in_body")
    op.drop_column(_TABLE, "binding_hash")
