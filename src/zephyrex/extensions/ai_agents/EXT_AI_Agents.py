# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agents: configured identities that take turns, on a schedule, a timer,
a task's due time or a conversation message, and act through the abilities
they are granted (see BLL_AI_Agents and AgentTurnExecutor).

Abilities act for the user named by ``requester_id``, under that user's
permissions. When an agent uses one as a tool, the turn fills
``requester_id`` in with the agent's owner."""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    DEFAULT_PRIORITY,
    ActivityManager,
    AgentAbilityManager,
    AgentManager,
    InvocationTriggerManager,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency

# The executor performs these itself (see AgentTurnExecutor); they are
# abilities so an agent can be granted them and a turn's activities typed.
EXECUTOR_ABILITIES = {
    "thinking_turn",
    "speak",
    "memorize",
    "trim",
    "recall",
    "abilities",
}
TASK_TYPES = ("timer", "schedule")


def _row(model: Any) -> Dict[str, Any]:
    dumped: Dict[str, Any] = model.model_dump(mode="json")
    return dumped


def _when(text: Optional[str]) -> Optional[datetime]:
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise InvalidInputExternalError(
            f"due_at is an ISO 8601 time, not {text!r}"
        ) from None


class EXT_AI_Agents(AbstractStaticExtension):
    name: ClassVar[str] = "ai_agents"
    friendly_name: ClassVar[str] = "AI Agent Management"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Agents that take turns on schedules, tasks and messages, acting "
        "through the abilities they are granted"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="ai",
                friendly_name="AI",
                reason="An agent thinks with the AI extension's chat models",
            ),
            EXT_Dependency(
                name="conversations",
                friendly_name="Conversations",
                reason="Agents take part in conversations and speak in them",
            ),
            EXT_Dependency(
                name="ai_prompts",
                friendly_name="Prompts",
                reason="An agent's context prompts are stored prompts",
            ),
            EXT_Dependency(
                name="ai_memories",
                friendly_name="Long-term memory",
                reason="Agents keep and recall their long-term memories there",
            ),
            PIP_Dependency(
                name="croniter",
                friendly_name="croniter",
                reason="Reads the cron expressions of scheduled triggers",
                semver=">=6.0.0",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "list_agents",
        "take_turn",
        "schedule_task",
        "list_tasks",
        "cancel_task",
        "grant_ability",
        "turn_activity",
        *EXECUTOR_ABILITIES,
    }

    @classmethod
    def register_services(cls, model_registry: Any, requester_id: str) -> List[Any]:
        """The monitor that fires due triggers (what wakes agents on their
        schedules and tasks), started at boot as ``requester_id``."""
        from zephyrex.extensions.ai_agents.SVC_AI_Agents import (
            InvocationMonitorService,
        )

        return [
            InvocationMonitorService(
                requester_id=requester_id, model_registry=model_registry
            )
        ]

    @classmethod
    def agents(cls, requester_id: str) -> AgentManager:
        manager: AgentManager = cls.as_requester(AgentManager, requester_id)
        return manager

    @classmethod
    def triggers(cls, requester_id: str) -> InvocationTriggerManager:
        manager: InvocationTriggerManager = cls.as_requester(
            InvocationTriggerManager, requester_id
        )
        return manager

    @classmethod
    @ability("list_agents")
    async def list_agents(cls, requester_id: str) -> List[Dict[str, Any]]:
        """The agents the user can see."""
        return [_row(agent) for agent in cls.agents(requester_id).list()]

    @classmethod
    @ability("take_turn")
    async def take_turn(
        cls, requester_id: str, agent_id: str, payload: Optional[str] = None
    ) -> Dict[str, Any]:
        """Run one turn of an agent now, handed ``payload``; the finished
        turn."""
        return _row(await cls.agents(requester_id).take_turn(agent_id, payload))

    @classmethod
    @ability("schedule_task")
    async def schedule_task(
        cls,
        requester_id: str,
        agent_id: str,
        instructions: str,
        due_at: Optional[str] = None,
        cron: Optional[str] = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> Dict[str, Any]:
        """Give an agent a task: once at ``due_at`` (ISO 8601), or on a
        ``cron`` schedule (from ``due_at``, when given). Priority 1 is the
        most urgent, 5 the least."""
        if not instructions or not instructions.strip():
            raise InvalidInputExternalError("a task has instructions")
        if cron is None and due_at is None:
            raise InvalidInputExternalError("a task is due at a time or on a schedule")
        fields: Dict[str, Any] = (
            {"invocation_type": "schedule", "cron": cron}
            if cron is not None
            else {"invocation_type": "timer", "one_shot": True}
        )
        return _row(
            cls.triggers(requester_id).create(
                agent_id=agent_id,
                invocation_payload=instructions,
                due_at=_when(due_at),
                priority=priority,
                **fields,
            )
        )

    @classmethod
    @ability("list_tasks")
    async def list_tasks(cls, requester_id: str, agent_id: str) -> List[Dict[str, Any]]:
        """An agent's tasks still to fire, most urgent and soonest first."""
        tasks = [
            trigger
            for trigger in cls.triggers(requester_id).list(
                agent_id=agent_id, enabled=True
            )
            if trigger.invocation_type in TASK_TYPES
        ]
        tasks.sort(
            key=lambda t: (t.priority, t.next_fire_at is None, t.next_fire_at or 0)
        )
        return [_row(task) for task in tasks]

    @classmethod
    @ability("cancel_task")
    async def cancel_task(cls, requester_id: str, task_id: str) -> Dict[str, Any]:
        """Stop a task (or any trigger) from firing again."""
        return _row(cls.triggers(requester_id).update(id=task_id, enabled=False))

    @classmethod
    @ability("grant_ability")
    async def grant_ability(
        cls, requester_id: str, agent_id: str, ability_id: str, enabled: bool = True
    ) -> Dict[str, Any]:
        """Let an agent use an ability, or (``enabled`` false) stop it."""
        grants: AgentAbilityManager = cls.as_requester(
            AgentAbilityManager, requester_id
        )
        existing = grants.list(agent_id=agent_id, ability_id=ability_id)
        if existing:
            return _row(grants.update(id=existing[0].id, enabled=enabled))
        return _row(
            grants.create(agent_id=agent_id, ability_id=ability_id, enabled=enabled)
        )

    @classmethod
    @ability("turn_activity")
    async def turn_activity(
        cls, requester_id: str, invocation_instance_id: str
    ) -> Dict[str, Any]:
        """What an agent did in a turn: its activities as trees."""
        activities: ActivityManager = cls.as_requester(ActivityManager, requester_id)
        return activities.hierarchy(invocation_instance_id)
