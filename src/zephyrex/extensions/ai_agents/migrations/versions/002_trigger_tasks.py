# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tasks are triggers: a due time and a priority on every trigger.

The ai_tasks extension kept tasks of its own (with no table its migration
made); a task is now an invocation trigger whose payload is its
instructions. Databases whose invocation_triggers table predates these
columns get them here.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_ai_agents_trigger_tasks"
down_revision: Union[str, None] = "175cfe29b4df"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "invocation_triggers"


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}


def upgrade() -> None:
    present = _columns()
    if "due_at" not in present:
        op.add_column(
            TABLE,
            sa.Column(
                "due_at",
                sa.DateTime(),
                nullable=True,
                comment="When it is first due (a task's due time)",
            ),
        )
    if "priority" not in present:
        op.add_column(
            TABLE,
            sa.Column(
                "priority",
                sa.Integer(),
                nullable=False,
                server_default="3",
                comment="1 (most urgent) to 5",
            ),
        )


def downgrade() -> None:
    present = _columns()
    with op.batch_alter_table(TABLE) as batch:
        for column in ("priority", "due_at"):
            if column in present:
                batch.drop_column(column)
