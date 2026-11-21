# SPDX-License-Identifier: AGPL-3.0-or-later
"""The soft-delete index every model with ``deleted_at`` declares
(``ix_room_events_deleted_at``), which 001 left out. Added only when missing.

Revision ID: 002_rooms_deleted_at_index
Revises: 001_rooms_initial
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002_rooms_deleted_at_index"
down_revision: str | None = "001_rooms_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "room_events"
INDEX = "ix_room_events_deleted_at"


def _indexes() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(TABLE)}


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE) and INDEX not in _indexes():
        op.create_index(INDEX, TABLE, ["deleted_at"])


def downgrade() -> None:
    if INDEX in _indexes():
        op.drop_index(INDEX, table_name=TABLE)
