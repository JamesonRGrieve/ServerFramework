# SPDX-License-Identifier: AGPL-3.0-or-later
"""A prompt's description is optional.

The model has always held it optional, but the initial schema made the
column NOT NULL, so a prompt created without one failed in the database
(a 500, found by the client's smoke test against 0.0.1a4).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_ai_prompts_description_optional"
down_revision: Union[str, None] = "95260599be0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "prompts"


def _nullable() -> bool:
    for column in sa.inspect(op.get_bind()).get_columns(TABLE):
        if column["name"] == "description":
            return bool(column["nullable"])
    return True


def upgrade() -> None:
    if _nullable():
        return
    with op.batch_alter_table(TABLE) as batch:
        batch.alter_column(
            "description",
            existing_type=sa.String(),
            nullable=True,
            existing_comment="The description",
        )


def downgrade() -> None:
    # Prompts written without a description since would block it; the column
    # stays nullable.
    pass
