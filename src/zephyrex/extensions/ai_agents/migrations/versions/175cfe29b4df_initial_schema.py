# SPDX-License-Identifier: AGPL-3.0-or-later
"""initial schema: agents, their grants, prompts, seats, memories, triggers,
turns and activities, and projects

Revision ID: 175cfe29b4df
Revises:
Create Date: 2025-06-09 21:53:21.176039

Each table is made only where it is missing. References to the ai_prompts
and conversations extensions' tables (prompts, conversations, artifacts)
carry no foreign key constraint: each extension's migrations run on their
own, so those tables may not exist yet.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "175cfe29b4df"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_ai_agents",)
depends_on: Union[str, Sequence[str], None] = None

INFO = {"source_module": "extensions.ai_agents.BLL_AI_Agents", "extension": "ai_agents"}


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def _reference(name: str, table: str, nullable: bool = True) -> sa.Column:
    return sa.Column(
        name,
        sa.String(),
        sa.ForeignKey(f"{table}.id"),
        nullable=nullable,
        comment=f"The ID of the related {name[:-3]}",
    )


def _elsewhere(name: str, nullable: bool = True) -> sa.Column:
    """A reference to another extension's table (see the module doc)."""
    return _text(name, f"The ID of the related {name[:-3]}", nullable=nullable)


def _flag(name: str, default: bool, comment: str) -> sa.Column:
    return sa.Column(
        name,
        sa.Boolean(),
        nullable=False,
        server_default=sa.true() if default else sa.false(),
        comment=comment,
    )


def _when(name: str, comment: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(), nullable=True, comment=comment)


def _record() -> List[sa.Column]:
    return [
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
    ]


def _owned() -> List[sa.Column]:
    return [_reference("user_id", "users"), _reference("team_id", "teams")]


def _create(table: str, comment: str, *columns: sa.Column) -> None:
    if sa.inspect(op.get_bind()).has_table(table):
        return
    op.create_table(table, *columns, *_record(), comment=comment, info=INFO)


TABLES = (
    "agents",
    "invocation_triggers",
    "invocation_instances",
    "conversation_agents",
    "provider_instance_agents",
    "provider_instance_agent_abilities",
    "projects",
    "project_context_providers",
    "project_context_prompts",
    "activities",
    "agent_context_prompts",
    "agent_abilities",
    "agent_memories",
)


def upgrade() -> None:
    _create(
        "agents",
        "An Agent represents a container of configuration and memories with which to communicate.",
        *_owned(),
        _text("name", "Name of the agent", nullable=False),
        _flag("favourite", False, "Whether the agent is marked as favourite"),
        _text("rotation_id", "The rotation of model instances the agent thinks with"),
        _text("image_url", "URL of the agent image"),
    )
    _create(
        "invocation_triggers",
        "An InvocationTrigger is a standing listener that triggers an Agent to take "
        "a turn - on a schedule (cron), a timer (interval or one-shot), or an event "
        "(a conversation message). It fires many times; each firing is an "
        "InvocationInstance. A task is a trigger: instructions as its payload, with "
        "a due time and a priority.",
        _reference("agent_id", "agents", nullable=False),
        *_owned(),
        _text("invocation_type", "'schedule' | 'timer' | 'event'", nullable=False),
        _flag("enabled", True, "Whether this trigger is active"),
        _text("cron", "Cron expression (invocation_type='schedule')"),
        sa.Column(
            "interval_seconds",
            sa.Integer(),
            nullable=True,
            comment="Delay/interval in seconds (invocation_type='timer')",
        ),
        _flag("one_shot", False, "Timer only: fire once, then disable"),
        _text("event_source", "Event source (invocation_type='event')"),
        _text("event_filter", "JSON-encoded match criteria against the event"),
        _text("invocation_payload", "The instructions handed to the turn"),
        _when("due_at", "When it is first due (a task's due time)"),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="3",
            comment="1 (most urgent) to 5",
        ),
        _when("last_fired_at", "When this trigger last fired"),
        _when("next_fire_at", "The next time it fires"),
        sa.Column(
            "fire_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="How many times it has fired",
        ),
    )
    _create(
        "invocation_instances",
        "An InvocationInstance is a single firing of an InvocationTrigger - one agent "
        "turn. It records the cause (trigger and/or triggering message), the context "
        "handed to the turn, and its lifecycle status; the turn's Activity tree links "
        "to it via invocation_instance_id.",
        _reference("invocation_trigger_id", "invocation_triggers"),
        _reference("agent_id", "agents", nullable=False),
        *_owned(),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="pending",
            comment="'pending' | 'running' | 'succeeded' | 'failed'",
        ),
        _text("trigger_message_id", "The conversation message that fired this turn"),
        _text("payload", "Prompt/context handed to this specific firing"),
        _text("error", "Why it failed"),
        _when("started_at", "When the turn began"),
        _when("completed_at", "When the turn finished"),
    )
    _create(
        "conversation_agents",
        "ConversationAgent represents an AI agent's participation in a conversation, "
        "allowing agents to be conversation participants alongside users.",
        _reference("agent_id", "agents", nullable=False),
        _elsewhere("conversation_id", nullable=False),
        _flag("active", True, "Whether the agent is actively participating"),
        _flag(
            "auto_respond",
            False,
            "Whether the agent takes a turn on every user message",
        ),
    )
    _create(
        "provider_instance_agents",
        "A ProviderInstanceAgent represents a link between a ProviderInstance and an Agent.",
        _reference("agent_id", "agents", nullable=False),
        _reference("provider_instance_id", "provider_instances", nullable=False),
    )
    _create(
        "provider_instance_agent_abilities",
        "Links provider instances to agent abilities and tracks their state",
        _reference("agent_id", "agents", nullable=False),
        _reference("provider_instance_id", "provider_instances", nullable=False),
        _flag("state", False, "State of the ability"),
    )
    _create(
        "projects",
        "A Project represents an enclosing organizational container for Conversations "
        "with additional context injection options.",
        _reference("user_id", "users", nullable=False),
        _reference("team_id", "teams"),
        _text("name", "The name", nullable=False),
        _text("description", "Description of the project"),
        _reference("parent_id", "projects"),
    )
    _create(
        "project_context_providers",
        "A ProjectContextProvider represents the association of a Provider with a "
        "Project for context injection.",
        _reference("project_id", "projects", nullable=False),
        _reference("provider_id", "providers", nullable=False),
        _text("context_resource", "Resource identifier for context"),
    )
    _create(
        "project_context_prompts",
        "A ProjectContextPrompt represents the association of a Prompt with a Project "
        "for context injection.",
        _reference("project_id", "projects", nullable=False),
        _elsewhere("prompt_id", nullable=False),
    )
    _create(
        "activities",
        "An Activity represents an action an Agent takes (or took) during a turn. "
        "Every activity of a turn belongs to its InvocationInstance via "
        "invocation_instance_id; sub-actions (e.g., steps in a web search) are "
        "indicated with the parent_id field. Activities are typed using ability_id "
        "and can optionally produce an Artifact.",
        _reference("invocation_instance_id", "invocation_instances", nullable=False),
        _reference("ability_id", "abilities", nullable=False),
        _elsewhere("artifact_id"),
        _reference("provider_id", "providers"),
        _reference("parent_id", "activities"),
        _text("title", "Title of the activity", nullable=False),
        _text("body", "Body content of the activity", nullable=False),
        sa.Column(
            "state", sa.Integer(), nullable=True, comment="State of the activity"
        ),
    )
    _create(
        "agent_context_prompts",
        "An AgentContextPrompt represents the association of a Prompt with an Agent "
        "for context injection.",
        _reference("agent_id", "agents", nullable=False),
        _elsewhere("prompt_id", nullable=False),
    )
    _create(
        "agent_abilities",
        "An AgentAbility grants an Agent permission to invoke an Ability as a tool. "
        "It is the agent's default-deny allowlist: an agent may invoke only the "
        "abilities for which it has an enabled AgentAbility.",
        _reference("agent_id", "agents", nullable=False),
        _reference("ability_id", "abilities", nullable=False),
        _flag("enabled", True, "Whether this ability is enabled for the agent"),
    )
    _create(
        "agent_memories",
        "An AgentMemory is a keyed short-term (working) memory for an Agent, injected "
        "into each turn's prompt and prunable via the trim ability.",
        _reference("agent_id", "agents", nullable=False),
        _text("key", "Memory key (unique per agent)", nullable=False),
        _text("content", "Memory content", nullable=False),
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
