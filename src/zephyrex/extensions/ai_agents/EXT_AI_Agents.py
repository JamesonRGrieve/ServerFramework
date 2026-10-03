"""
AI Agents extension for AGInfrastructure.
Implements AI agent management and activity tracking through the AI framework.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.registry import classproperty


class EXT_AI_Agents(AbstractStaticExtension):
    """
    AI Agents extension for AGInfrastructure.

    Provides comprehensive AI agent management capabilities including agent creation,
    configuration, provider selection, and ability management. Also manages
    agent activities and activity types within conversations. This extension depends
    on the AI framework for core AI provider functionality.

    The extension focuses on:
    - Agent management and configuration
    - Agent-provider integration through AI framework
    - Agent abilities and capability management
    - Agent activities and conversation participation
    - Project-based agent organization
    - Agent context and prompt management

    Usage:
        # Create AI agent through business logic layer
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentManager

        agent_manager = AgentManager(requester_id="user_123")
        agent = agent_manager.create(
            name="Assistant",
            description="AI Assistant Agent",
            abilities=["text_generation", "conversation"]
        )
    """

    # Extension metadata (class attributes)
    name: ClassVar[str] = "ai_agents"
    friendly_name: ClassVar[str] = "AI Agent Management"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "AI agent management extension providing agent creation, configuration, and activity tracking through AI framework"
    )

    # Environment variables this extension needs
    _env: ClassVar[Dict[str, Any]] = {
        "AI_AGENTS_MAX_MEMORY": "50",
        "AI_AGENTS_DEFAULT_MODEL": "gpt-3.5-turbo",
        "AI_AGENTS_AUTO_RESPOND": "false",
        "AI_AGENTS_ACTIVITY_LOGGING": "true",
        "AI_AGENTS_COLLABORATION_ENABLED": "true",
        "AI_AGENTS_LEARNING_ENABLED": "false",
        "AI_AGENTS_MAX_CONCURRENT_TASKS": "10",
    }

    # Extension dependencies - requires the AI framework
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="ai",
                friendly_name="AI Framework",
                reason="Required for AI agent functionality and provider integration",
                optional=False,
            ),
            EXT_Dependency(
                name="conversations",
                friendly_name="Conversation Framework",
                reason="Required for conversing with agents",
                optional=False,
            ),
            EXT_Dependency(
                name="ai_prompts",
                friendly_name="Prompt Framework",
                reason="Required for prompting agents",
                optional=False,
            ),
            EXT_Dependency(
                name="ai_memories",
                friendly_name="Long-term memory",
                reason="Agents keep and recall their long-term memories there",
                optional=False,
            ),
            PIP_Dependency(
                name="tiktoken",
                friendly_name="TikToken",
                reason="Tokenization",
                optional=False,
            ),
        ]
    )

    # Meta abilities provided by this extension for managing AI agents.
    #
    # ``thinking_turn`` and ``speak`` are the two abilities the turn executor
    # relies on: every turn's root Activity is a ``thinking_turn`` invocation
    # (deliberation itself — never offered to the model as a callable tool), and
    # ``speak`` is the sole operator-communication ability (the executor handles
    # it specially, creating a Message rather than routing through the general
    # ability invoker). Both are seeded here so their Ability rows exist for the
    # required Activity.ability_id and for the AgentAbility allowlist.
    _abilities: ClassVar[Set[str]] = {
        "manage_agents",
        "configure_agent_providers",
        "track_agent_activities",
        "manage_agent_abilities",
        "thinking_turn",
        "speak",
        # Executor-handled "self" memory abilities: short-term memorize/trim and
        # long-term memorize(long)/recall.
        "memorize",
        "trim",
        "recall",
        # Executor-handled discovery ability: enumerate/search the agent's own
        # granted, invocable abilities at runtime.
        "abilities",
    }

    @classproperty
    def pip_dependencies(cls):
        """PIP dependencies view (for AbstractEXTTest / dependency tooling)."""
        return cls.dependencies.pip

    @classproperty
    def ext_dependencies(cls):
        """Extension dependencies view."""
        return cls.dependencies.ext

    @classproperty
    def sys_dependencies(cls):
        """System dependencies view."""
        return cls.dependencies.sys

    @classmethod
    def register_services(cls, model_registry: Any, requester_id: str) -> List[Any]:
        """Return background services this extension wants started at boot.

        The framework's (opt-in, env-gated) service startup calls this on every
        extension that defines it, passing the live model registry and a driver
        identity. Here it is the :class:`InvocationMonitorService` that fires due
        agent triggers — i.e. what makes agents wake up on their schedule. The
        monitor runs as a system driver (ROOT) so it can see every agent's
        triggers; individual turns still act under their own agent's identity.
        """
        from zephyrex.extensions.ai_agents.SVC_AI_Agents import (
            InvocationMonitorService,
        )

        return [
            InvocationMonitorService(
                requester_id=requester_id, model_registry=model_registry
            )
        ]

    @ability
    @classmethod
    def manage_agents(cls, **kwargs) -> Dict[str, Any]:
        """
        Meta ability: Manage AI agents and their configurations.

        Returns:
            Dict containing agent management information
        """
        try:
            # In real implementation, would query AgentModel records
            return {
                "success": True,
                "total_agents": 0,
                "active_agents": 0,
                "message": "Agent management capability",
            }
        except Exception as e:
            logger.error(f"Error managing agents: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def configure_agent_providers(
        cls, agent_id: str, provider_settings: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        """
        Meta ability: Configure AI provider settings for a specific agent.

        Args:
            agent_id: ID of the agent to configure
            provider_settings: Provider configuration settings

        Returns:
            Configuration result
        """
        try:
            return {
                "success": True,
                "agent_id": agent_id,
                "provider_settings": provider_settings,
                "message": f"Provider settings configured for agent {agent_id}",
            }
        except Exception as e:
            logger.error(f"Error configuring agent providers: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def track_agent_activities(cls, agent_id: str = None, **kwargs) -> Dict[str, Any]:
        """
        Meta ability: Track activities of AI agents.

        Args:
            agent_id: Optional agent ID to track specific agent

        Returns:
            Activity tracking information
        """
        try:
            # In real implementation, would query AgentActivityModel records
            return {
                "success": True,
                "activities": [],
                "total_activities": 0,
                "message": f"Activity tracking for {'agent ' + agent_id if agent_id else 'all agents'}",
            }
        except Exception as e:
            logger.error(f"Error tracking agent activities: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def manage_agent_abilities(
        cls, agent_id: str, abilities: List[str], action: str = "add", **kwargs
    ) -> Dict[str, Any]:
        """
        Meta ability: Manage abilities assigned to AI agents.

        Args:
            agent_id: ID of the agent
            abilities: List of ability names
            action: Action to perform ("add" or "remove")

        Returns:
            Ability management result
        """
        try:
            return {
                "success": True,
                "agent_id": agent_id,
                "abilities": abilities,
                "action": action,
                "message": f"Successfully {action}ed abilities for agent {agent_id}",
            }
        except Exception as e:
            logger.error(f"Error managing agent abilities: {e}")
            return {"success": False, "error": str(e)}


class AbstractAIAgentProvider(AbstractStaticExtension):
    """
    Abstract base class for AI agent service providers.
    All AI agent providers should inherit from this class.
    """

    # Common abilities that AI agent providers might implement
    _abilities: ClassVar[Set[str]] = (
        set()
    )  # AI agent providers typically don't have direct abilities
