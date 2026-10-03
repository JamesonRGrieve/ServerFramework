# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for ai_memories: owns ``memories``, the long-term
memories agents keep for their users.

Before this, ai_memories kept memories in process memory, and the agents
extension kept its own in a separate SQLite file
(``agent_long_term_memory.db``, AGENT_LTM_SQLITE_PATH). Neither is carried
over: the separate file is left for the operator to delete.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_ai_memories_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_ai_memories",)
depends_on: Union[str, Sequence[str], None] = None

TABLE = "memories"


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=True,
            comment="The ID of the related user",
        ),
        _text("team_id", "The ID of the related team"),
        _text("agent_id", "The agent the memory is about", nullable=False),
        _text("conversation_id", "The conversation it came from, if any"),
        _text("key", "A short label for the memory"),
        _text("content", "What is remembered", nullable=False),
        _text("source", "Where it came from (agent, user, import)", nullable=False),
        _text("embedding", "The embedding, as a JSON array"),
        _text("embedding_model", "The model that made the embedding"),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Long-term memories an agent keeps for its user, with embeddings for recall",
        info={"extension": "ai_memories"},
    )
    op.create_index("ix_memories_agent_created", TABLE, ["agent_id", "created_at"])
    op.create_index(f"ix_{TABLE}_deleted_at", TABLE, ["deleted_at"])


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_deleted_at", table_name=TABLE)
    op.drop_index("ix_memories_agent_created", table_name=TABLE)
    op.drop_table(TABLE)
