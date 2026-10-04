# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pinned instances' abilities, conversations in projects, and the webhook
and email event sources.

- ``provider_instance_agent_abilities`` names the ability each row allows
  (``ability_id``) and allows it by default. Rows from before could not say
  which ability they meant and were never read, so they are dropped.
- ``project_conversations`` files conversations in projects.
- ``invocation_triggers`` keeps a webhook trigger's encrypted secret and an
  email trigger's address.

Each step runs only where it has not already been done.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Inspector

revision: str = "003_ai_agents_pins_projects_event_sources"
down_revision: Union[str, None] = "002_ai_agents_trigger_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INFO = {"source_module": "extensions.ai_agents.BLL_AI_Agents", "extension": "ai_agents"}
RESTRICTIONS = "provider_instance_agent_abilities"
PROJECT_CONVERSATIONS = "project_conversations"
TRIGGERS = "invocation_triggers"
TRIGGER_COLUMNS = (
    ("webhook_secret", "The webhook's signing secret, encrypted"),
    ("email_address", "Where mail wakes an email trigger"),
)


def _inspector() -> Inspector:
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in _inspector().get_columns(table)}


def _restrictions_name_their_ability() -> None:
    if "ability_id" in _columns(RESTRICTIONS):
        return
    op.execute(sa.table(RESTRICTIONS).delete())
    # The table is recreated (batch), so the new column can be NOT NULL; an
    # unnamed foreign key cannot be added in batch mode on every backend,
    # so the reference to abilities is the model's to keep.
    with op.batch_alter_table(RESTRICTIONS) as batch:
        batch.add_column(
            sa.Column(
                "ability_id",
                sa.String(),
                nullable=False,
                comment="The ID of the related ability",
            )
        )
        batch.alter_column(
            "state",
            existing_type=sa.Boolean(),
            server_default=sa.true(),
            comment="Whether the instance may be used for the ability",
        )


def _project_conversations() -> None:
    if _inspector().has_table(PROJECT_CONVERSATIONS):
        return
    op.create_table(
        PROJECT_CONVERSATIONS,
        sa.Column(
            "project_id",
            sa.String(),
            sa.ForeignKey("projects.id"),
            nullable=False,
            comment="The ID of the related project",
        ),
        # The conversations extension's table may not exist yet: its
        # migrations run on their own, so no foreign key.
        sa.Column(
            "conversation_id",
            sa.String(),
            nullable=False,
            comment="The ID of the related conversation",
        ),
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        comment="A ProjectConversation files a Conversation in a Project; it is "
        "seen and changed through the project.",
        info=INFO,
    )


def _trigger_event_sources() -> None:
    present = _columns(TRIGGERS)
    for name, comment in TRIGGER_COLUMNS:
        if name not in present:
            op.add_column(
                TRIGGERS, sa.Column(name, sa.String(), nullable=True, comment=comment)
            )


def upgrade() -> None:
    _restrictions_name_their_ability()
    _project_conversations()
    _trigger_event_sources()


def downgrade() -> None:
    present = _columns(TRIGGERS)
    with op.batch_alter_table(TRIGGERS) as batch:
        for name, _ in reversed(TRIGGER_COLUMNS):
            if name in present:
                batch.drop_column(name)
    if _inspector().has_table(PROJECT_CONVERSATIONS):
        op.drop_table(PROJECT_CONVERSATIONS)
    if "ability_id" in _columns(RESTRICTIONS):
        with op.batch_alter_table(RESTRICTIONS) as batch:
            batch.drop_column("ability_id")
            batch.alter_column(
                "state", existing_type=sa.Boolean(), server_default=sa.false()
            )
