# SPDX-License-Identifier: AGPL-3.0-or-later
"""A message records the prompt it was generated from.

ai_prompts extends conversations' messages with ``prompt_id`` (when
conversations is loaded), but no migration made the column: the model had
it and the table did not, so writing a message failed ("table messages has
no column named prompt_id"). Added where the table exists and lacks it; an
app without conversations has no messages table and nothing to add.
"""

from typing import Sequence, Set, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_ai_prompts_message_prompt_id"
down_revision: Union[str, None] = "002_ai_prompts_description_optional"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "messages"
COLUMN = "prompt_id"


def _columns() -> Set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE)}


def upgrade() -> None:
    columns = _columns()
    if not columns or COLUMN in columns:
        return
    op.add_column(
        TABLE,
        sa.Column(
            COLUMN,
            sa.String(),
            nullable=True,
            comment="ID of the prompt used to generate this message",
        ),
    )


def downgrade() -> None:
    if COLUMN in _columns():
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column(COLUMN)
