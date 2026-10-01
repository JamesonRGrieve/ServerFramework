from datetime import datetime
from enum import IntEnum
from typing import Any, ClassVar, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptModel
from zephyrex.extensions.conversations.BLL_Conversations import ArtifactModel, MessageModel
from zephyrex.lib.Environment import env
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    NameMixinModel,
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel
from zephyrex.logic.BLL_Extensions import AbilityModel
from zephyrex.logic.BLL_Providers import ProviderInstanceModel, ProviderModel


def _validate_fk(mgr, fk_value, manager_cls, not_found_detail):
    """Return a clean 404 (not a raw 500/201) when a referenced FK row does
    not exist. A falsy FK is left for the model/DB layer to handle."""
    if not fk_value:
        return
    try:
        manager_cls(
            requester_id=env("SYSTEM_ID"),
            model_registry=mgr.model_registry,
        ).get(id=fk_value)
    except HTTPException:
        raise HTTPException(status_code=404, detail=not_found_detail)


# Enums
class ActivityState(IntEnum):
    SUCCESS = 0
    WARNING = 1
    ERROR = 2


# Agent Models
class AgentModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    name: str = Field(..., description="Name of the agent")
    favourite: bool = Field(
        False, description="Whether the agent is marked as favourite"
    )
    rotation_id: Optional[str] = Field(None, description="ID of the rotation")
    image_url: Optional[str] = Field(None, description="URL of the agent image")

    table_comment: ClassVar[str] = (
        "An Agent represents a container of configuration and memories with which to communicate."
    )

    class Create(
        BaseModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        name: str = Field(..., description="Name of the agent")
        favourite: Optional[bool] = Field(
            False, description="Whether the agent is marked as favourite"
        )
        rotation_id: Optional[str] = Field(None, description="ID of the rotation")
        image_url: Optional[str] = Field(None, description="URL of the agent image")

    class Update(BaseModel):
        name: Optional[str] = Field(None, description="Name of the agent")
        favourite: Optional[bool] = Field(
            None, description="Whether the agent is marked as favourite"
        )
        rotation_id: Optional[str] = Field(None, description="ID of the rotation")
        image_url: Optional[str] = Field(None, description="URL of the agent image")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        name: Optional[StringSearchModel] = None
        favourite: Optional[bool] = None
        rotation_id: Optional[StringSearchModel] = None
        image_url: Optional[StringSearchModel] = None


class AgentManager(AbstractBLLManager, RouterMixin):
    _model = AgentModel

    prefix: ClassVar[Optional[str]] = "/v1/agent"
    tags: ClassVar[Optional[List[str]]] = ["Agent Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    custom_routes: ClassVar[List[Dict[str, Any]]] = [
        {
            "path": "/{id}/prompt",
            "method": "post",
            "function": "prompt",
            "auth_type": AuthType.JWT,
            "summary": "Prompt agent",
            "description": "Send a prompt to an agent and get a response.",
            "status_code": 200,
        },
        {
            "path": "/{id}/transcribe",
            "method": "post",
            "function": "transcribe",
            "auth_type": AuthType.JWT,
            "summary": "Transcribe audio",
            "description": "Transcribe audio using the agent's provider.",
            "status_code": 200,
        },
        {
            "path": "/{id}/ability",
            "method": "get",
            "function": "list_abilities",
            "auth_type": AuthType.JWT,
            "summary": "List enabled abilities for an agent",
            "description": "Retrieves a list of abilities that are enabled for the specified agent.",
            "status_code": 200,
        },
        {
            "path": "/{id}/awaken",
            "method": "post",
            "function": "start_agent_service",
            "auth_type": AuthType.JWT,
            "summary": "Start the agent service",
            "description": "Starts the service for the specified agent.",
            "status_code": 200,
        },
    ]

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )


# Agent Invocation Models
class InvocationTriggerModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference.Optional,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """A standing listener that triggers an Agent to take a turn.

    Polymorphic over ``invocation_type``:

    - ``schedule`` — fire on a cron expression (``cron``).
    - ``timer`` — fire after / every ``interval_seconds`` (``one_shot`` for a
      single delayed fire vs. a recurring interval).
    - ``event`` — fire when an external event from ``event_source``
      (``email`` | ``conversation_message`` | ``webhook``) matches
      ``event_filter``.

    A trigger is a persistent configuration that fires many times; each firing
    produces one :class:`InvocationInstanceModel` (the turn). A scheduled
    ``ai_tasks`` Task is one kind of trigger: ``ai_tasks`` consumes this model
    rather than re-implementing triggering.
    """

    invocation_type: str = Field(
        ...,
        description="How the agent turn is triggered: 'schedule' | 'timer' | 'event'",
    )
    enabled: bool = Field(True, description="Whether this trigger is active")
    # schedule
    cron: Optional[str] = Field(
        None, description="Cron expression (invocation_type='schedule')"
    )
    # timer
    interval_seconds: Optional[int] = Field(
        None,
        description="Delay/interval in seconds (invocation_type='timer')",
    )
    one_shot: bool = Field(
        False,
        description="Timer only: fire once after interval_seconds then disable",
    )
    # event
    event_source: Optional[str] = Field(
        None,
        description="Event source (invocation_type='event'): 'email' | 'conversation_message' | 'webhook'",
    )
    event_filter: Optional[str] = Field(
        None,
        description="JSON-encoded match criteria against the event (sender, folder, conversation_id, ...)",
    )
    # what the agent does when it fires
    invocation_payload: Optional[str] = Field(
        None,
        description="Default prompt/context handed to the agent turn when this trigger fires",
    )
    # bookkeeping
    last_fired_at: Optional[datetime] = Field(
        None, description="When this trigger last fired"
    )
    next_fire_at: Optional[datetime] = Field(
        None, description="Next scheduled fire time (schedule/timer)"
    )
    fire_count: int = Field(
        0, description="Number of times this trigger has fired"
    )

    table_comment: ClassVar[str] = (
        "An InvocationTrigger is a standing listener that triggers an Agent to "
        "take a turn — on a schedule (cron), a timer (interval), or an external "
        "event (email, conversation message, webhook). It is a persistent config "
        "that fires many times; each firing is an InvocationInstance. Tasks are a "
        "type of trigger."
    )

    class Create(
        BaseModel,
        AgentModel.Reference.ID,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        invocation_type: str = Field(
            ..., description="'schedule' | 'timer' | 'event'"
        )
        enabled: Optional[bool] = Field(True)
        cron: Optional[str] = Field(None)
        interval_seconds: Optional[int] = Field(None)
        one_shot: Optional[bool] = Field(False)
        event_source: Optional[str] = Field(None)
        event_filter: Optional[str] = Field(None)
        invocation_payload: Optional[str] = Field(None)

    class Update(BaseModel):
        invocation_type: Optional[str] = Field(None)
        enabled: Optional[bool] = Field(None)
        cron: Optional[str] = Field(None)
        interval_seconds: Optional[int] = Field(None)
        one_shot: Optional[bool] = Field(None)
        event_source: Optional[str] = Field(None)
        event_filter: Optional[str] = Field(None)
        invocation_payload: Optional[str] = Field(None)
        # Firing bookkeeping — written by the monitor after each firing.
        last_fired_at: Optional[datetime] = Field(None)
        next_fire_at: Optional[datetime] = Field(None)
        fire_count: Optional[int] = Field(None)

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        AgentModel.Reference.ID.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        invocation_type: Optional[StringSearchModel] = None
        enabled: Optional[bool] = None
        event_source: Optional[StringSearchModel] = None


class InvocationTriggerManager(AbstractBLLManager, RouterMixin):
    _model = InvocationTriggerModel

    prefix: ClassVar[Optional[str]] = "/v1/invocation-trigger"
    tags: ClassVar[Optional[List[str]]] = ["Agent Invocation Trigger Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )

    def create_validation(self, entity):
        _validate_fk(
            self, getattr(entity, "agent_id", None), AgentManager, "Agent not found"
        )


class InvocationInstanceModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    InvocationTriggerModel.Reference.Optional,
    AgentModel.Reference,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """A single firing of a trigger — i.e. one agent turn.

    Created each time an :class:`InvocationTriggerModel` fires (or, for ad-hoc /
    manual turns, with ``invocation_trigger_id`` null). It records what caused
    the turn (``trigger_message_id`` for a conversation event, else the trigger
    kind), the context handed to it (``payload``), and its lifecycle
    (``status``, ``started_at``/``completed_at``). The turn's Activity tree
    hangs off this instance via ``Activity.invocation_instance_id``.
    """

    status: str = Field(
        "pending",
        description="Lifecycle: 'pending' | 'running' | 'succeeded' | 'failed'",
    )
    trigger_message_id: Optional[str] = Field(
        None,
        description="Message that fired this instance (invocation_type='event'/conversation_message)",
    )
    payload: Optional[str] = Field(
        None, description="Prompt/context handed to this specific firing"
    )
    error: Optional[str] = Field(
        None, description="Error detail when status='failed'"
    )
    started_at: Optional[datetime] = Field(
        None, description="When the turn began executing"
    )
    completed_at: Optional[datetime] = Field(
        None, description="When the turn finished"
    )

    table_comment: ClassVar[str] = (
        "An InvocationInstance is a single firing of an InvocationTrigger — one "
        "agent turn. It records the cause (trigger and/or triggering message), "
        "the context handed to the turn, and its lifecycle status; the turn's "
        "Activity tree links to it via invocation_instance_id."
    )

    class Create(
        BaseModel,
        AgentModel.Reference.ID,
        InvocationTriggerModel.Reference.ID.Optional,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        status: Optional[str] = Field("pending")
        trigger_message_id: Optional[str] = Field(None)
        payload: Optional[str] = Field(None)
        started_at: Optional[datetime] = Field(None)

    class Update(BaseModel):
        status: Optional[str] = Field(None)
        error: Optional[str] = Field(None)
        started_at: Optional[datetime] = Field(None)
        completed_at: Optional[datetime] = Field(None)

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        AgentModel.Reference.ID.Search,
        InvocationTriggerModel.Reference.ID.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        status: Optional[StringSearchModel] = None
        trigger_message_id: Optional[StringSearchModel] = None


class InvocationInstanceManager(AbstractBLLManager, RouterMixin):
    _model = InvocationInstanceModel

    prefix: ClassVar[Optional[str]] = "/v1/invocation-instance"
    tags: ClassVar[Optional[List[str]]] = ["Agent Invocation Instance Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )

    def create_validation(self, entity):
        _validate_fk(
            self, getattr(entity, "agent_id", None), AgentManager, "Agent not found"
        )
        _validate_fk(
            self,
            getattr(entity, "invocation_trigger_id", None),
            InvocationTriggerManager,
            "Invocation trigger not found",
        )

    @property
    def triggers(self):
        return InvocationTriggerManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )


# Agent Conversation Participation Models
class ConversationAgentModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    metaclass=ModelMeta,
):
    conversation_id: str = Field(..., description="ID of the conversation")
    active: bool = Field(
        True, description="Whether the agent is actively participating"
    )
    auto_respond: bool = Field(
        False, description="Whether the agent should automatically respond"
    )

    table_comment: ClassVar[str] = (
        "ConversationAgent represents an AI agent's participation in a conversation, allowing agents to be conversation participants alongside users."
    )

    class Create(BaseModel, AgentModel.Reference.ID):
        conversation_id: str = Field(..., description="ID of the conversation")
        active: Optional[bool] = Field(
            True, description="Whether the agent is actively participating"
        )
        auto_respond: Optional[bool] = Field(
            False, description="Whether the agent should automatically respond"
        )

    class Update(BaseModel):
        active: Optional[bool] = Field(
            None, description="Whether the agent is actively participating"
        )
        auto_respond: Optional[bool] = Field(
            None, description="Whether the agent should automatically respond"
        )

    class Search(ApplicationModel.Search, AgentModel.Reference.ID.Search):
        conversation_id: Optional[StringSearchModel] = None
        active: Optional[bool] = None
        auto_respond: Optional[bool] = None


class ConversationAgentManager(AbstractBLLManager, RouterMixin):
    _model = ConversationAgentModel

    @property
    def agents(self):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentManager

        return AgentManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    @property
    def conversations(self):
        from zephyrex.extensions.conversations.BLL_Conversations import ConversationManager

        return ConversationManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    @property
    def context_prompts(self):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentContextPromptManager

        return AgentContextPromptManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    async def prompt(self, id: str, prompt_content: dict):
        """
        Prompt a model against a prompt string with named entities for completion.

        Args:
            name: The name of the prompt (must exist in the system)
            prompt_content: A dictionary of entities to substitute into the prompt
        """
        # Note: Prompt with named entities is a potential feature but not implemented in the current system
        raise NotImplementedError("Named entity prompting is not currently supported")

    async def transcribe(self, id: str, audio_path: str):
        """
        Submit an audio file for transcription using the selected agent's provider.

        Args:
            name: Name of the agent configuration to use
            audio_path: Path to the audio file to transcribe

        Returns:
            Transcription result
        """
        raise NotImplementedError(
            "Transcription functionality is not currently supported"
        )

    async def list_abilities(self, id: str):
        """List all abilities available to a specific agent."""
        return await self.abilities.list(AgentModel.Reference.ID(agent_id=id))

    async def start_agent_service(self, id: str):
        """Start the agent service for continuous operation."""
        raise NotImplementedError(
            "Agent service functionality is not currently supported"
        )


# Provider Instance Agent Models
class ProviderInstanceAgentModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    metaclass=ModelMeta,
):
    provider_instance_id: str = Field(..., description="ID of the provider instance")

    __table_args__ = {"info": {"provider_instance_id": "provider_instance_id"}}

    table_comment: ClassVar[str] = (
        "A ProviderInstanceAgent represents a link between a ProviderInstance and an Agent..."
    )

    class Create(BaseModel):
        provider_instance_id: str = Field(
            ..., description="ID of the provider instance"
        )
        agent_id: str = Field(..., description="ID of the agent")

    class Update(BaseModel):
        provider_instance_id: Optional[str] = Field(
            None, description="ID of the provider instance"
        )
        agent_id: Optional[str] = Field(None, description="ID of the agent")

    class Search(
        ApplicationModel.Search,
        AgentModel.Reference.ID.Search,
        ProviderInstanceModel.Reference.ID.Search,
    ):
        pass


class ProviderInstanceAgentManager(AbstractBLLManager, RouterMixin):
    _model = ProviderInstanceAgentModel

    def create_validation(self, entity):
        from zephyrex.logic.BLL_Providers import ProviderInstanceManager

        _validate_fk(
            self,
            getattr(entity, "provider_instance_id", None),
            ProviderInstanceManager,
            "Provider instance not found",
        )
        _validate_fk(
            self, getattr(entity, "agent_id", None), AgentManager, "Agent not found"
        )

    @property
    def agents(self):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentManager

        return AgentManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    @property
    def provider_instances(self):
        from zephyrex.logic.BLL_Providers import ProviderInstanceManager

        return ProviderInstanceManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )


# Provider Instance Agent Ability Models
class ProviderInstanceAgentAbilityModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    metaclass=ModelMeta,
):
    # Explicitly define the provider_instance_id field
    provider_instance_id: str = Field(..., description="ID of the provider instance")
    state: bool = Field(default=False, description="State of the ability")

    # Add table args to ensure field mapping
    __table_args__ = {"info": {"provider_instance_id": "provider_instance_id"}}

    table_comment: ClassVar[str] = (
        "Links provider instances to agent abilities and tracks their state"
    )

    class Create(BaseModel):
        provider_instance_id: str = Field(
            ..., description="ID of the provider instance"
        )
        agent_id: str = Field(..., description="ID of the agent")
        state: bool = Field(default=False, description="State of the ability")

    class Update(BaseModel):
        provider_instance_id: Optional[str] = Field(
            None, description="ID of the provider instance"
        )
        agent_id: Optional[str] = Field(None, description="ID of the agent")
        state: Optional[bool] = Field(None, description="State of the ability")

    class Search(ApplicationModel.Search):
        provider_instance_id: Optional[StringSearchModel] = None
        agent_id: Optional[StringSearchModel] = None
        state: Optional[bool] = None


class ProviderInstanceAgentAbilityManager(AbstractBLLManager, RouterMixin):
    _model = ProviderInstanceAgentAbilityModel

    def create_validation(self, entity):
        from zephyrex.logic.BLL_Providers import ProviderInstanceManager

        _validate_fk(
            self,
            getattr(entity, "provider_instance_id", None),
            ProviderInstanceManager,
            "Provider instance not found",
        )
        _validate_fk(
            self, getattr(entity, "agent_id", None), AgentManager, "Agent not found"
        )

    @property
    def agents(self):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentManager

        return AgentManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    @property
    def provider_instances(self):
        from zephyrex.logic.BLL_Providers import ProviderInstanceManager

        return ProviderInstanceManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    @property
    def abilities(self):
        from zephyrex.logic.BLL_Providers import ProviderInstanceAbilityManager

        return ProviderInstanceAbilityManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    @property
    def provider_abilities(self):
        return self.abilities


# Project Models
class ProjectModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    NameMixinModel,
    ParentMixinModel,
    UserModel.Reference,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    description: Optional[str] = Field(None, description="Description of the project")

    table_comment: ClassVar[str] = (
        "A Project represents an enclosing organizational container for Conversations with additional context injection options."
    )

    class Create(
        BaseModel,
        NameMixinModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
        ParentMixinModel.Optional,
    ):
        description: Optional[str] = Field(
            None, description="Description of the project"
        )

    class Update(
        BaseModel,
        NameMixinModel.Optional,
        TeamModel.Reference.ID.Optional,
        ParentMixinModel.Optional,
    ):
        description: Optional[str] = Field(
            None, description="Description of the project"
        )

    class Search(
        ApplicationModel.Search,
        NameMixinModel.Search,
        ParentMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        description: Optional[StringSearchModel] = None


class ProjectManager(AbstractBLLManager, RouterMixin):
    _model = ProjectModel

    prefix: ClassVar[Optional[str]] = "/v1/project"
    tags: ClassVar[Optional[List[str]]] = ["Project Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_team_id"]

    def create(self, **kwargs):
        """Default the project's owner to the authenticated requester.

        `user_id` isn't a path parameter, and the generic `target_id`
        auto-population in `_create_single_entity` (`create_args["user_id"]
        = self.target_id`) never actually fires for this manager --
        `target_id` is only populated for managers that request it via
        `factory_params`/path resolution, and Project is deliberately
        flat-prefixed with no such wiring. Without this default, any
        caller that omits `user_id` hits the DB's NOT NULL constraint on
        `projects.user_id` as a raw, unhandled IntegrityError (500)
        instead of the row simply belonging to whoever created it.
        """
        kwargs.setdefault("user_id", self.requester_id)
        return super().create(**kwargs)

    def create_validation(self, entity):
        """Validate project creation references existing user/team rows.

        Neither `user_id` nor `team_id` is a path parameter for /v1/project
        (it's a flat prefix), so unlike nested resources (e.g. provider/
        instance) there is no router-level 404 for a bogus reference --
        it must be checked explicitly here, matching the pattern used by
        conversations.BLL_Conversations (e.g. MessageManager.create_validation).

        A `None` user_id (e.g. explicitly nulled by a caller) is rejected
        here with a clean 422 rather than being allowed to reach the DB,
        where `projects.user_id NOT NULL` would otherwise surface as an
        unhandled IntegrityError (500).
        """
        if not getattr(entity, "user_id", None):
            raise HTTPException(status_code=422, detail="user_id is required")

        try:
            from zephyrex.logic.BLL_Auth import UserManager

            UserManager(
                requester_id=env("SYSTEM_ID"),
                model_registry=self.model_registry,
            ).get(id=entity.user_id)
        except HTTPException:
            raise HTTPException(status_code=404, detail="User not found")

        if getattr(entity, "team_id", None):
            try:
                from zephyrex.logic.BLL_Auth import TeamManager

                TeamManager(
                    requester_id=env("SYSTEM_ID"),
                    model_registry=self.model_registry,
                ).get(id=entity.team_id)
            except HTTPException:
                raise HTTPException(status_code=404, detail="Team not found")

    @property
    def context_prompts(self):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import ProjectContextPromptManager

        return ProjectContextPromptManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    @property
    def context_providers(self):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import ProjectContextProviderManager

        return ProjectContextProviderManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )


# Project Context Provider Models
class ProjectContextProviderModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    provider_id: str = Field(..., description="ID of the provider extension")
    project_id: str = Field(..., description="ID of the project")
    context_resource: Optional[str] = Field(
        None, description="Resource identifier for context"
    )

    table_comment: ClassVar[str] = (
        "A ProjectContextProvider represents the association of a Provider with a Project for context injection."
    )

    class Create(BaseModel):
        provider_id: str = Field(..., description="ID of the provider extension")
        project_id: str = Field(..., description="ID of the project")
        context_resource: Optional[str] = Field(
            None, description="Resource identifier for context"
        )

    class Update(BaseModel):
        context_resource: Optional[str] = Field(
            None, description="Resource identifier for context"
        )

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        provider_id: Optional[StringSearchModel] = None
        project_id: Optional[StringSearchModel] = None
        context_resource: Optional[StringSearchModel] = None


class ProjectContextProviderManager(AbstractBLLManager, RouterMixin):
    _model = ProjectContextProviderModel

    def create_validation(self, entity):
        from zephyrex.logic.BLL_Providers import ProviderManager

        _validate_fk(
            self, getattr(entity, "project_id", None), ProjectManager, "Project not found"
        )
        _validate_fk(
            self,
            getattr(entity, "provider_id", None),
            ProviderManager,
            "Provider not found",
        )


# Project Context Prompt Models
class ProjectContextPromptModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    ProjectModel.Reference,
    PromptModel.Reference,
    metaclass=ModelMeta,
):
    table_comment: ClassVar[str] = (
        "A ProjectContextPrompt represents the association of a Prompt with a Project for context injection."
    )

    class Create(BaseModel, PromptModel.Reference.ID):
        project_id: str = Field(..., description="ID of the project")

    class Update(BaseModel):
        pass

    class Search(
        ApplicationModel.Search,
        ProjectModel.Reference.ID.Search,
        PromptModel.Reference.ID.Search,
    ):
        pass


class ProjectContextPromptManager(AbstractBLLManager, RouterMixin):
    _model = ProjectContextPromptModel

    def create_validation(self, entity):
        from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptManager

        _validate_fk(
            self, getattr(entity, "project_id", None), ProjectManager, "Project not found"
        )
        _validate_fk(
            self, getattr(entity, "prompt_id", None), PromptManager, "Prompt not found"
        )

    @property
    def projects(self):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import ProjectManager

        return ProjectManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    @property
    def prompts(self):
        from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptManager

        return PromptManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )


# Activity Models
class ActivityModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    ParentMixinModel,
    metaclass=ModelMeta,
):
    title: str = Field(..., description="Title of the activity")
    body: str = Field(..., description="Body content of the activity")
    state: Optional[ActivityState] = Field(None, description="State of the activity")
    invocation_instance_id: Optional[str] = Field(
        None,
        description="ID of the InvocationInstance (turn) this activity belongs to",
    )
    ability_id: str = Field(..., description="ID of the ability")
    artifact_id: Optional[str] = Field(
        None, description="ID of the artifact created by this activity"
    )
    provider_id: Optional[str] = Field(
        None, description="ID of the provider used for this activity"
    )
    # chain_link_id: Optional[str] = Field(None, description="ID of the chain step (if ai_chains installed)") # Conditional field

    # Relationships (for potential inclusion)
    invocation_instance: Optional[InvocationInstanceModel] = None
    ability: Optional[AbilityModel] = None
    artifact: Optional[ArtifactModel] = None
    provider: Optional[ProviderModel] = None

    table_comment: ClassVar[str] = (
        "An Activity represents an action an Agent takes (or took) during a turn. "
        "A turn's activities belong to its InvocationInstance via "
        "invocation_instance_id; the root activity of a turn carries it and "
        "children inherit it through parent_id. Activities are typed using "
        "ability_id. Sub-actions (e.g., steps in a web search) are indicated with "
        "the parent_id field. Activities can optionally produce an Artifact."
    )

    class Create(
        BaseModel,
        InvocationInstanceModel.Reference.ID.Optional,
        AbilityModel.Reference.ID,
        ParentMixinModel.Optional,
        ProviderModel.Reference.ID.Optional,  # Add provider optional ref
        # ChainLinkModel.Reference.ID.Optional, # Conditional ref
    ):
        title: str = Field(..., description="Title of the activity")
        body: str = Field(..., description="Body content of the activity")
        artifact_id: Optional[str] = Field(
            None, description="ID of the artifact created by this activity"
        )
        state: Optional[ActivityState] = Field(
            None, description="State of the activity"
        )

    class Update(BaseModel, ParentMixinModel.Optional):
        title: Optional[str] = Field(None, description="Title of the activity")
        body: Optional[str] = Field(None, description="Body content of the activity")
        state: Optional[ActivityState] = Field(
            None, description="State of the activity"
        )
        # message_id/ability_id are generally not updatable
        artifact_id: Optional[str] = Field(
            None, description="ID of the artifact created by this activity"
        )
        provider_id: Optional[str] = Field(
            None, description="ID of the provider used for this activity"
        )

    class Search(
        ApplicationModel.Search, UpdateMixinModel.Search, ParentMixinModel.Search
    ):
        title: Optional[StringSearchModel] = None
        body: Optional[StringSearchModel] = None
        state: Optional[ActivityState] = None
        invocation_instance_id: Optional[StringSearchModel] = None
        ability_id: Optional[StringSearchModel] = None
        artifact_id: Optional[StringSearchModel] = None
        provider_id: Optional[StringSearchModel] = None


class ActivityManager(AbstractBLLManager, RouterMixin):
    _model = ActivityModel
    # Permission flows from the InvocationInstance (→ its agent's owner/team);
    # for conversation turns the instance's trigger_message_id ties it to a
    # conversation the requester can already see.
    permission_references = ["invocation_instance"]

    prefix: ClassVar[Optional[str]] = "/v1/activity"
    tags: ClassVar[Optional[List[str]]] = ["Activity Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_team_id"]
    custom_routes: ClassVar[List[Dict[str, Any]]] = [
        {
            "path": "/hierarchy/{invocation_instance_id}",
            "method": "get",
            "function": "get_hierarchy_for_instance",
            "auth_type": AuthType.JWT,
            "summary": "Get activity hierarchy for a turn (invocation instance)",
            "description": "Retrieves the hierarchical structure of activities produced by a single agent turn.",
            "status_code": 200,
        }
    ]

    @staticmethod
    def get_hierarchy(
        manager: "ActivityManager", invocation_instance_id: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get a hierarchical representation of a turn's activities.

        Args:
            manager: An ActivityManager instance
            invocation_instance_id: The InvocationInstance (turn) to build the
                activity tree for

        Returns:
            Dictionary with activity hierarchy (root activity id -> tree)
        """
        # Fetch every activity for the turn in one query, then assemble the
        # tree in memory: one index pass, no N+1 per-node lookups, and no
        # reliance on a self-referential parent_id equals-search. The filter is
        # passed as a keyword (``invocation_instance_id=...``) rather than a
        # positional ``Search`` model: the positional-Search form does not apply
        # this equality filter (it returns every activity), which would mix
        # turns together.
        activities = manager.list(invocation_instance_id=invocation_instance_id)
        children_by_parent: Dict[Optional[str], List[ActivityModel]] = {}
        for activity in activities:
            children_by_parent.setdefault(activity.parent_id, []).append(activity)

        # `visited` guards against self-references and cycles so a malformed
        # parent chain can never recurse without bound.
        hierarchy = {}
        visited: set = set()
        for activity in activities:
            if activity.parent_id is None:
                hierarchy[activity.id] = manager._build_hierarchy(
                    manager, activity, children_by_parent, visited
                )

        return hierarchy

    @staticmethod
    def _build_hierarchy(
        manager: "ActivityManager",
        activity: ActivityModel,
        children_by_parent: Dict[Optional[str], List[ActivityModel]],
        visited: Optional[set] = None,
    ) -> Dict[str, Any]:
        """
        Recursively build hierarchy for an activity from a pre-built
        parent-id -> children index.

        Args:
            manager: An ActivityManager instance
            activity: The activity to build hierarchy for
            children_by_parent: Index of parent_id -> child activities
            visited: Ids already placed in the tree, to break cycles

        Returns:
            Dictionary with activity and its children
        """
        if visited is None:
            visited = set()
        result: Dict[str, Any] = {"activity": activity.dict(), "children": {}}
        visited.add(activity.id)

        for child in children_by_parent.get(activity.id, []):
            # Skip self-links and any node already in the current tree path.
            if child.id == activity.id or child.id in visited:
                continue
            result["children"][child.id] = manager._build_hierarchy(
                manager, child, children_by_parent, visited
            )

        return result

    @property
    def activities(self):
        """Get activities manager for nested operations."""
        return ActivityManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    def get_hierarchy_for_instance(
        self, invocation_instance_id: str
    ) -> Dict[str, Any]:
        """Get the activity hierarchy for a single turn (invocation instance),
        keyed under ``activities`` (root activity id -> {activity, children})."""
        return {
            "activities": self.get_hierarchy(
                manager=self, invocation_instance_id=invocation_instance_id
            )
        }

    def _validate_fk_exists(self, fk_value, model_cls, not_found_detail):
        """Raw, ACL-independent existence check: return a clean 404 (not a raw
        500/201) when the referenced row does not exist at all. Uses ROOT so a
        row the caller cannot see through ACL -- a ROOT-owned ability, an
        access-scoped message -- still counts as existing; only a genuinely
        absent row 404s. A falsy FK is left for the model/DB layer to handle."""
        if not fk_value:
            return
        db_cls = model_cls.DB(self.model_registry.DB.manager.Base)
        if not db_cls.exists(
            id=fk_value,
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        ):
            raise HTTPException(status_code=404, detail=not_found_detail)

    def create_validation(self, entity):
        invocation_instance_id = getattr(entity, "invocation_instance_id", None)
        parent_id = getattr(entity, "parent_id", None)

        # The root activity of a turn (no parent) must be anchored to the
        # InvocationInstance that produced it. Child activities inherit their
        # anchor through parent_id, so they need not carry it. Without this a
        # rootless, unanchored activity could not be rendered or
        # permission-scoped.
        if parent_id is None and not invocation_instance_id:
            raise HTTPException(
                status_code=422,
                detail="A root activity must have an invocation_instance_id",
            )

        # A referenced InvocationInstance must actually exist, else a clean 404
        # rather than a dangling soft link. Checked ACL-independently.
        self._validate_fk_exists(
            invocation_instance_id,
            InvocationInstanceModel,
            "Invocation instance not found",
        )
        # parent_id is an optional self-referential Activity link.
        self._validate_fk_exists(
            parent_id,
            ActivityModel,
            "Parent activity not found",
        )


# Agent Context Prompt Models
class AgentContextPromptModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    PromptModel.Reference.ID,
    metaclass=ModelMeta,
):
    # Existing fields
    table_comment: ClassVar[str] = (
        "An AgentContextPrompt represents the association of a Prompt with an Agent for context injection."
    )

    class Create(BaseModel):
        agent_id: str = Field(..., description="ID of the agent")
        prompt_id: str = Field(..., description="ID of the prompt")

    class Update(BaseModel):
        agent_id: Optional[str] = Field(None, description="ID of the agent")
        prompt_id: Optional[str] = Field(None, description="ID of the prompt")

    class Search(
        ApplicationModel.Search,
        AgentModel.Reference.ID.Search,
        PromptModel.Reference.ID.Search,
    ):
        pass


class AgentContextPromptManager(AbstractBLLManager, RouterMixin):
    _model = AgentContextPromptModel

    def create_validation(self, entity):
        from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptManager

        _validate_fk(
            self, getattr(entity, "agent_id", None), AgentManager, "Agent not found"
        )
        _validate_fk(
            self, getattr(entity, "prompt_id", None), PromptManager, "Prompt not found"
        )

    @property
    def agents(self):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentManager

        return AgentManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    @property
    def prompts(self):
        from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptManager

        return PromptManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )


# Agent Ability Models (the default-deny tool allowlist)
class AgentAbilityModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    AbilityModel.Reference.ID,
    metaclass=ModelMeta,
):
    enabled: bool = Field(
        True, description="Whether this ability is enabled for the agent"
    )

    table_comment: ClassVar[str] = (
        "An AgentAbility grants an Agent permission to invoke an Ability as a "
        "tool. It is the agent's default-deny allowlist: an agent may invoke "
        "only the abilities for which it has an enabled AgentAbility."
    )

    class Create(BaseModel, AbilityModel.Reference.ID):
        agent_id: str = Field(..., description="ID of the agent")
        enabled: Optional[bool] = Field(
            True, description="Whether this ability is enabled for the agent"
        )

    class Update(BaseModel):
        enabled: Optional[bool] = Field(
            None, description="Whether this ability is enabled for the agent"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        AgentModel.Reference.ID.Search,
        AbilityModel.Reference.ID.Search,
    ):
        enabled: Optional[bool] = None


class AgentAbilityManager(AbstractBLLManager, RouterMixin):
    _model = AgentAbilityModel

    prefix: ClassVar[Optional[str]] = "/v1/agent-ability"
    tags: ClassVar[Optional[List[str]]] = ["Agent Ability Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_team_id"]

    def create_validation(self, entity):
        from zephyrex.logic.BLL_Extensions import AbilityManager

        _validate_fk(
            self, getattr(entity, "agent_id", None), AgentManager, "Agent not found"
        )
        _validate_fk(
            self,
            getattr(entity, "ability_id", None),
            AbilityManager,
            "Ability not found",
        )

    @property
    def agents(self):
        return AgentManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    @property
    def abilities(self):
        from zephyrex.logic.BLL_Extensions import AbilityManager

        return AbilityManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    def enabled_abilities(self, agent_id: str) -> Dict[str, str]:
        """Return an agent's granted abilities as a ``{name: ability_id}`` map.

        This is the default-deny allowlist consumed by ``AbilityInvoker`` (its
        keys) and the turn executor (which needs the ``ability_id`` to type each
        tool-call Activity). Only abilities with an enabled ``AgentAbility`` link
        are included. Ability ids are resolved to names via the (system-entity)
        Ability rows using a ROOT-scoped manager, so a granted ability is never
        dropped merely because the agent's own requester can't read the Ability
        row; a genuinely dangling link (ability row absent) is skipped rather
        than widening access.
        """
        from zephyrex.logic.BLL_Extensions import AbilityManager

        links = self.list(agent_id=agent_id, enabled=True)
        if not links:
            return {}
        ability_manager = AbilityManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        mapping: Dict[str, str] = {}
        for link in links:
            try:
                ability = ability_manager.get(id=link.ability_id)
            except HTTPException:
                continue
            name = getattr(ability, "name", None)
            if name:
                mapping[name] = link.ability_id
        return mapping

    def enabled_ability_names(self, agent_id: str) -> set:
        """Return the set of ability *names* an agent is permitted to invoke
        (the keys of :meth:`enabled_abilities`)."""
        return set(self.enabled_abilities(agent_id).keys())


# Agent short-term memory (context management)
class AgentMemoryModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    metaclass=ModelMeta,
):
    """A keyed short-term (working) memory entry for an Agent.

    Short-term memory is the agent's bounded, prunable working context: it is
    injected into every turn's prompt (as ``{{SHORT_TERM_MEMORIES}}``), written
    by the ``memorize`` ability, and removed by the ``trim`` ability. Keys are
    unique per agent (writing an existing key updates it). Long-term memory
    lives elsewhere (a separate store behind a memory provider).
    """

    key: str = Field(..., description="Memory key (unique per agent)")
    content: str = Field(..., description="Memory content")

    table_comment: ClassVar[str] = (
        "An AgentMemory is a keyed short-term (working) memory for an Agent, "
        "injected into each turn's prompt and prunable via the trim ability."
    )

    class Create(BaseModel, AgentModel.Reference.ID):
        key: str = Field(..., description="Memory key (unique per agent)")
        content: str = Field(..., description="Memory content")

    class Update(BaseModel):
        content: Optional[str] = Field(None, description="Memory content")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        AgentModel.Reference.ID.Search,
    ):
        key: Optional[StringSearchModel] = None
        content: Optional[StringSearchModel] = None


class AgentMemoryManager(AbstractBLLManager, RouterMixin):
    _model = AgentMemoryModel

    prefix: ClassVar[Optional[str]] = "/v1/agent-memory"
    tags: ClassVar[Optional[List[str]]] = ["Agent Memory Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_team_id"]

    def create_validation(self, entity):
        _validate_fk(
            self, getattr(entity, "agent_id", None), AgentManager, "Agent not found"
        )

    def remember(self, agent_id: str, key: str, content: str):
        """Upsert a short-term memory: update the entry if ``key`` already
        exists for the agent, else create it (keys are unique per agent).

        Filters use plain-value kwargs (equality), not a positional/kwarg
        ``StringSearchModel`` — the latter's field is ``eq``/``inc``, not
        ``equals``, so an ``equals=`` filter silently matches nothing.
        """
        existing = self.list(agent_id=agent_id, key=key)
        if existing:
            return self.update(id=existing[0].id, content=content)
        return self.create(agent_id=agent_id, key=key, content=content)

    def forget(self, agent_id: str, keys: List[str]) -> int:
        """Delete the given short-term memory keys for the agent. Returns the
        number removed."""
        removed = 0
        for key in keys or []:
            for entry in self.list(agent_id=agent_id, key=key):
                self.delete(id=entry.id)
                removed += 1
        return removed

    def as_dict(self, agent_id: str) -> Dict[str, str]:
        """Return the agent's short-term memory as a ``{key: content}`` map for
        injection into the turn prompt."""
        return {
            entry.key: entry.content for entry in self.list(agent_id=agent_id)
        }


# Extension hooks for integrating AI agents with other models

# Hook to add agent_id field to Message model when conversations extension is loaded
try:
    from zephyrex.extensions.conversations.BLL_Conversations import MessageModel

    # Dynamically add agent_id field to MessageModel
    if not hasattr(MessageModel, "agent_id"):
        MessageModel.agent_id = Field(
            None, description="ID of the agent that created this message"
        )

    # Link a message back to the agent turn that produced it, so the frontend
    # can render a speak-bubble alongside that turn's thinking Activity tree.
    if not hasattr(MessageModel, "invocation_instance_id"):
        MessageModel.invocation_instance_id = Field(
            None,
            description="ID of the InvocationInstance (turn) that produced this message",
        )

    # Add activities property to MessageManager
    def _get_activities(self) -> ActivityManager:
        return ActivityManager(
            requester_id=self.requester_id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

    from zephyrex.extensions.conversations.BLL_Conversations import MessageManager

    if not hasattr(MessageManager, "activities"):
        MessageManager.activities = property(_get_activities)

except ImportError:
    # Conversations extension not available
    pass

# Hook to add project_id field to Conversation model when conversations extension is loaded
try:
    from zephyrex.extensions.conversations.BLL_Conversations import ConversationModel

    # Dynamically add project_id field to ConversationModel
    if not hasattr(ConversationModel, "project_id"):
        ConversationModel.project_id = Field(
            None, description="ID of the project this conversation belongs to"
        )

except ImportError:
    # Conversations extension not available
    pass

# Hook to add project_id field to Artifact model when conversations extension is loaded
try:
    from zephyrex.extensions.conversations.BLL_Conversations import ArtifactModel

    # Dynamically add project_id field to ArtifactModel
    if not hasattr(ArtifactModel, "project_id"):
        ArtifactModel.project_id = Field(
            None, description="ID of the project this artifact belongs to"
        )

except ImportError:
    # Conversations extension not available
    pass

# Hook to add chain_link_id field to Activity model when ai_chains extension is loaded
# try:
#     from extensions.ai_chains.BLL_AI_Chains import ChainLinkModel

#     # Dynamically add chain_link_id field to ActivityModel
#     if not hasattr(ActivityModel, "chain_link_id"):
#         ActivityModel.chain_link_id = Field(
#             None, description="ID of the chain step (if ai_chains installed)"
#         )

#     # Add chain_link_id to Create and Update models
#     if not hasattr(ActivityModel.Create, "chain_link_id"):
#         ActivityModel.Create.chain_link_id = Field(
#             None, description="ID of the chain step"
#         )

#     if not hasattr(ActivityModel.Update, "chain_link_id"):
#         ActivityModel.Update.chain_link_id = Field(
#             None, description="ID of the chain step"
#         )

#     if not hasattr(ActivityModel.Search, "chain_link_id"):
#         ActivityModel.Search.chain_link_id = Field(
#             None, description="ID of the chain step"
#         )

# except ImportError:
#     # AI Chains extension not available
#     pass


# Hook to create agent when team is created - registered at bottom of file to avoid circular imports

# Hook to auto-respond with AI agents when users send messages
try:
    from zephyrex.extensions.conversations.BLL_Conversations import MessageManager
    from zephyrex.lib.Logging import logger
    from zephyrex.logic.AbstractLogicManager import HookContext, hook_bll

    @hook_bll(MessageManager.create, timing="after")
    async def fire_conversation_message_turns(context: HookContext):
        """Fire an agent turn when a user message matches a conversation_message
        trigger — the reactive/conversational counterpart to the timer monitor.

        A user's message drives a turn straight to chat with no background loop
        required. It fires ONLY for agents that (a) actively participate in the
        conversation and (b) have an enabled ``conversation_message``
        InvocationTrigger, so ordinary messages incur no agent work unless an
        agent is explicitly configured to react.

        Loop-safe: agent-authored messages (``user_id`` is None) are skipped, so
        an agent's own ``speak`` reply never re-triggers the hook. Fully
        defensive — never raises, so a turn failure cannot break message
        creation.
        """
        try:
            message = context.result
            if not message or not getattr(message, "conversation_id", None):
                return
            # Skip agent-authored messages (no user_id) — the loop guard.
            if not getattr(message, "user_id", None):
                return

            model_registry = getattr(context.manager, "model_registry", None)
            requester_id = message.user_id

            participants = ConversationAgentManager(
                requester_id=requester_id, model_registry=model_registry
            ).list(conversation_id=message.conversation_id, active=True)
            if not participants:
                return
            participant_agent_ids = {p.agent_id for p in participants}

            # Enabled conversation_message triggers whose agent is in this
            # conversation (ROOT-scoped: the driver must see all triggers).
            triggers = InvocationTriggerManager(
                requester_id=env("ROOT_ID"), model_registry=model_registry
            ).list(
                invocation_type="event",
                event_source="conversation_message",
                enabled=True,
            )

            from zephyrex.extensions.ai_agents.AgentTurnExecutor import (
                AgentTurnExecutor,
            )

            for trigger in triggers:
                if trigger.agent_id not in participant_agent_ids:
                    continue
                try:
                    instance = InvocationInstanceManager(
                        requester_id=requester_id, model_registry=model_registry
                    ).create(
                        agent_id=trigger.agent_id,
                        invocation_trigger_id=trigger.id,
                        trigger_message_id=message.id,
                        payload=message.content,
                        user_id=message.user_id,
                    )
                    await AgentTurnExecutor(
                        model_registry=model_registry, requester_id=requester_id
                    ).run(instance.id)
                except Exception as turn_error:
                    logger.error(
                        "Conversation-message turn failed for agent %s: %s",
                        trigger.agent_id,
                        turn_error,
                    )

        except Exception as e:
            logger.error(f"Error in fire_conversation_message_turns hook: {e}")
            # Never break message creation on a turn failure.

except ImportError:
    # Conversations extension not available
    pass


from zephyrex.lib.Logging import logger

# Register hook to create agent when team is created
from zephyrex.logic.AbstractLogicManager import HookContext, HookTiming, hook_bll
from zephyrex.logic.BLL_Auth import TeamManager


@hook_bll(TeamManager.create, timing=HookTiming.AFTER, priority=10)
def create_agent_on_team_creation(context: HookContext) -> None:
    """
    Hook that automatically creates an AI agent when a new team is created.

    Args:
        context: Hook context containing the created team information
    """
    try:
        # Get the created team from the result
        team = context.result
        if not team or not hasattr(team, "id"):
            logger.warning("No team found in hook context result")
            return

        # Get requester_id from the manager instance
        requester_id = context.manager.requester.id

        # Create an agent for the team
        agent_manager = AgentManager(
            requester_id=requester_id,
            target_team_id=team.id,
            model_registry=context.manager.model_registry,
        )

        # Create the agent with team reference
        agent = agent_manager.create(
            name=f"{team.name} Agent",
            team_id=team.id,
            favourite=True,  # Mark as favourite by default
        )

        logger.info(
            f"Successfully created agent '{agent.name}' for team '{team.name}' (ID: {team.id})"
        )

    except Exception as e:
        # Log error but don't fail the team creation
        logger.error(f"Failed to create agent for team: {str(e)}")


from zephyrex.extensions.conversations.BLL_Conversations import ConversationManager
from zephyrex.logic.BLL_Auth import UserTeamManager


# TODO: This is a patch for MVP and should be removed in the future
@hook_bll(ConversationManager.create, timing=HookTiming.AFTER, priority=10)
def associate_agent_with_conversation(context: HookContext) -> None:
    """
    Hook that associates an agent with a conversation when a new conversation is created.

    Args:
        context: Hook context containing the created conversation information
    """
    try:
        # Get the created conversation from the result
        conversation = context.result
        if not conversation or not hasattr(conversation, "id"):
            logger.warning("No conversation found in hook context result")
            return

        # Get requester_id from the manager instance
        requester_id = context.manager.requester.id

        # check if link already exists
        conversation_agent_manager = ConversationAgentManager(
            requester_id=requester_id,
            model_registry=context.manager.model_registry,
        )

        conversation_agents = conversation_agent_manager.list(
            conversation_id=conversation.id,
            active=True,  # Only consider active agents
            auto_respond=True,  # Only consider agents that auto-respond
        )

        if conversation_agents:
            logger.info(
                f"Conversation {conversation.id} already has associated agents: {', '.join([agent.agent_id for agent in conversation_agents])}"
            )
            return

        # find teams the user is part of
        user_team_manager = UserTeamManager(
            requester_id=requester_id,
            model_registry=context.manager.model_registry,
        )

        user_teams = user_team_manager.list(
            user_id=conversation.user_id or requester_id,
        )

        agent_manager = AgentManager(
            requester_id=requester_id,
            model_registry=context.manager.model_registry,
        )

        for user_team in user_teams:
            # find agents associated with the team
            agents = agent_manager.list(team_id=user_team.team_id)
            for agent in agents:
                if not agent.rotation_id:
                    logger.warning(
                        f"Agent {agent.name} (ID: {agent.id}) has no rotation assigned, skipping association with conversation {conversation.id}"
                    )
                    continue
                conversation_agent = conversation_agent_manager.create(
                    conversation_id=conversation.id,
                    agent_id=agent.id,
                    active=True,
                    auto_respond=True,
                )
                logger.info(
                    f"Associated agent {agent.name} (ID: {agent.id}) with conversation {conversation.id}"
                    f" (Team ID: {user_team.team_id})"
                    f" - Conversation Agent ID: {conversation_agent.id}"
                )
                break

    except Exception as e:
        # Log error but don't fail the conversation creation
        logger.error(f"Failed to associate agent with conversation: {str(e)}")
