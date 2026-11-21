# SPDX-License-Identifier: AGPL-3.0-or-later
"""A record's own key/values: `metadata` rows may belong to a record (`target_entity_type` +
`target_entity_id`) instead of a user or team, with how to read the value (`value_type`), its
place (`ordinal`) and whether only the record's editors read it (`is_private`).

The record is polymorphic, so there is no foreign key; access follows the record through its own
manager (lib.RecordTarget). Each column and the index are added only when missing, so a database
whose table was made from the current model is left as it is.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_metadata_record_target"
down_revision: Union[str, None] = "001_metadata_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "metadata"
INDEX = "ix_metadata_target"
COLUMNS = (
    sa.Column("target_entity_type", sa.String(), nullable=True),
    sa.Column("target_entity_id", sa.String(), nullable=True),
    sa.Column("value_type", sa.String(), nullable=True),
    sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.false()),
)


def _columns() -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE)}


def _indexes() -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {index["name"] for index in inspector.get_indexes(TABLE)}


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(TABLE):
        return
    present = _columns()
    missing = [column for column in COLUMNS if column.name not in present]
    if missing:
        with op.batch_alter_table(TABLE) as batch:
            for column in missing:
                batch.add_column(column.copy())
    if INDEX not in _indexes():
        op.create_index(INDEX, TABLE, ["target_entity_type", "target_entity_id"])


def downgrade() -> None:
    if INDEX in _indexes():
        op.drop_index(INDEX, table_name=TABLE)
    present = _columns()
    gone = [column.name for column in COLUMNS if column.name in present]
    if gone:
        with op.batch_alter_table(TABLE) as batch:
            for name in gone:
                batch.drop_column(name)
