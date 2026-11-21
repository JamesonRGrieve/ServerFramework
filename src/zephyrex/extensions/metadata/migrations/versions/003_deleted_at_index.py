# SPDX-License-Identifier: AGPL-3.0-or-later
"""The soft-delete index every model with ``deleted_at`` declares
(``ix_metadata_deleted_at``), which 001 never created. Added only when
missing, so a database whose table was made from the current model is left
as it is.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_metadata_deleted_at_index"
down_revision: Union[str, None] = "002_metadata_record_target"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "metadata"
INDEX = "ix_metadata_deleted_at"


def _indexes() -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {index["name"] for index in inspector.get_indexes(TABLE)}


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE) and INDEX not in _indexes():
        op.create_index(INDEX, TABLE, ["deleted_at"])


def downgrade() -> None:
    if INDEX in _indexes():
        op.drop_index(INDEX, table_name=TABLE)
