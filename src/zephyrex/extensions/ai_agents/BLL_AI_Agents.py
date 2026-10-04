# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agents, what they may do, what wakes them, and what they did.

An agent belongs to whoever creates it (ROOT and SYSTEM may name another
owner); an update never moves its owner or team. Everything hanging off an
agent (its ability grants, context prompts, conversation seats, provider
instance links, short-term memories, invocation triggers and turns) inherits
access from it, so only someone who may edit an agent configures it.

A turn is one :class:`InvocationInstanceModel`; what fires turns is an
:class:`InvocationTriggerModel`: a cron schedule, a timer (a delay, an
interval, or a one-shot at ``due_at``), a conversation message, a signed
webhook call or an email (see EventSources). A task is a trigger whose
payload is its instructions, with a due time and a priority. A turn's
activities hang off its instance and inherit access from it. An agent
pinned to provider instances (:class:`ProviderInstanceAgentModel`) thinks
only on them.

Projects belong to their creator and group context prompts, providers and
conversations.

Every record a create or update references is read as the requester, so no
one can point their records at something they cannot see. The system
catalogs (abilities, extensions, providers), which anyone may reference,
are read as SYSTEM.
"""

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Any, ClassVar, Dict, List, Literal, Optional, Set

from croniter import croniter
from fastapi import HTTPException, Request
from pydantic import BaseModel as RouteModel
from pydantic import ConfigDict, Field

from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptManager, PromptModel
from zephyrex.extensions.conversations.BLL_Conversations import (
    ArtifactManager,
    ArtifactModel,
    ConversationManager,
    ConversationModel,
    MessageManager,
)
from zephyrex.extensions.email.InboundEmail import InboundEmail, on_inbound_email
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import rate_limit
from zephyrex.lib.Logging import logger
from zephyrex.lib.SecretEncryption import encrypt_secret
from zephyrex.lib.SessionCookies import accept_cross_site_writes
from zephyrex.lib.SignedRequests import SIGNATURE_HEADER, TIMESTAMP_HEADER
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    HookContext,
    HookTiming,
    ModelMeta,
    NameMixinModel,
    NumericalSearchModel,
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
    hook_bll,
)
from zephyrex.logic.AbstractLogicManager.ownership import (
    OWNERSHIP_FIELDS,
    created_records,
    each_created,
    owned_by,
    server_only,
    server_side,
    without,
)
from zephyrex.logic.BLL_Auth import (
    TeamManager,
    TeamModel,
    UserManager,
    UserModel,
    UserTeamManager,
)
from zephyrex.logic.BLL_Extensions import (
    AbilityManager,
    AbilityModel,
    ExtensionManager,
)
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderManager,
    ProviderModel,
    RotationManager,
)
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

EXTENSION_NAME = "ai_agents"

# Task priority: 1 is the most urgent. Due triggers fire most urgent first.
HIGHEST_PRIORITY = 1
LOWEST_PRIORITY = 5
DEFAULT_PRIORITY = 3

# The event sources a trigger can listen to: the ones something fires. A
# conversation message fires through the message hook, a webhook through
# the trigger's signed endpoint, an email through the email extension's
# inbound hook (see EventSources).
EventSource = Literal["conversation_message", "webhook", "email"]
InvocationType = Literal["schedule", "timer", "event"]
WEBHOOK, EMAIL = "webhook", "email"
# What an email trigger's event_filter may match on, besides its address.
EMAIL_FILTER_KEYS = frozenset({"from", "subject"})
# The domain email triggers' addresses are at; unset, there are none.
EMAIL_DOMAIN_SETTING = "AI_AGENTS_EMAIL_DOMAIN"
# Random bytes in an email trigger's address: it is unguessable, so only
# who it is given to can wake the agent by mail.
EMAIL_ADDRESS_TOKEN_BYTES = 12
# Calls a webhook trigger takes from one address in a minute.
WEBHOOK_RATE_LIMIT = "60/min"
WEBHOOK_PATH = "/{trigger_id}/webhook"

# Trigger bookkeeping only the monitor (ROOT) writes.
TRIGGER_BOOKKEEPING = ("last_fired_at", "next_fire_at", "fire_count")
# Written by the server alone: a webhook's (encrypted) secret, and an email
# trigger's address.
TRIGGER_SERVER_FIELDS = ("webhook_secret", "email_address")
# A turn's lifecycle, which only the executor writes.
TURN_LIFECYCLE = ("status", "error", "started_at", "completed_at")
PENDING = "pending"


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


def _visible(manager: AbstractBLLManager, record_id: str, detail: str) -> Any:
    """The record ``record_id`` as ``manager``'s requester sees it, or 404."""
    try:
        return manager.get(id=record_id)
    except HTTPException as error:
        if error.status_code == 404:
            raise _not_found(detail) from None
        raise


def _as(manager_class: Any, source: AbstractBLLManager) -> Any:
    """``manager_class`` acting as ``source``'s requester."""
    return manager_class(
        requester_id=source.requester.id, model_registry=source.model_registry
    )


def _catalog(manager_class: Any, source: AbstractBLLManager) -> Any:
    """``manager_class`` over a system catalog (abilities, extensions,
    providers) that every user may reference, read as SYSTEM: catalog rows
    are not anyone's, and one made by ROOT is visible to ROOT alone."""
    return manager_class(
        requester_id=env("SYSTEM_ID"), model_registry=source.model_registry
    )


def loaded_in(model_registry: Any) -> bool:
    """Hooks are registered process-wide; they act only in apps that load
    this extension."""
    registry = getattr(model_registry, "extension_registry", None)
    return registry is not None and EXTENSION_NAME in registry.extension_names


def _ai_agents_loaded(manager: AbstractBLLManager) -> bool:
    return loaded_in(manager.model_registry)


class ActivityState(IntEnum):
    SUCCESS = 0
    WARNING = 1
    ERROR = 2


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
    rotation_id: Optional[str] = Field(
        None, description="The rotation of model instances the agent thinks with"
    )
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


@dataclass(frozen=True)
class AbilityGrant:
    """An ability an agent may use: its row, its name, and the extension
    that performs it."""

    ability_id: str
    name: str
    extension: str


def tool_names(grants: List[AbilityGrant]) -> Dict[str, AbilityGrant]:
    """Each grant under the name the agent's model calls it by: its ability
    name, or ``<extension>__<name>`` where two extensions' abilities share
    a name."""
    counts: Dict[str, int] = {}
    for grant in grants:
        counts[grant.name] = counts.get(grant.name, 0) + 1
    return {
        (
            grant.name
            if counts[grant.name] == 1
            else f"{grant.extension}__{grant.name}"
        ): grant
        for grant in grants
    }


class InvocationTriggerModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """What makes an agent take a turn, many times over.

    - ``schedule``: on a cron expression (``cron``); with ``due_at``, from
      the first tick after it.
    - ``timer``: after ``interval_seconds`` and every ``interval_seconds``
      after that, or (``one_shot``) once; with ``due_at``, first at it.
    - ``event``: when ``event_source`` happens: a user's message in a
      conversation the agent takes part in, a signed call to the trigger's
      webhook (its secret is ``webhook_secret``, write-only), or an email
      to the trigger's ``email_address`` that its ``event_filter`` matches.

    A task is a trigger: its ``invocation_payload`` is the instructions, and
    ``due_at`` and ``priority`` say when it is due and how urgent it is. Each
    firing is one :class:`InvocationInstanceModel`.
    """

    invocation_type: InvocationType = Field(
        ...,
        description="How the agent turn is triggered: 'schedule' | 'timer' | 'event'",
    )
    enabled: bool = Field(True, description="Whether this trigger is active")
    cron: Optional[str] = Field(
        None, description="Cron expression (invocation_type='schedule')"
    )
    interval_seconds: Optional[int] = Field(
        None, description="Delay/interval in seconds (invocation_type='timer')", gt=0
    )
    one_shot: bool = Field(False, description="Timer only: fire once, then disable")
    event_source: Optional[EventSource] = Field(
        None, description="Event source (invocation_type='event')"
    )
    event_filter: Optional[str] = Field(
        None, description="JSON-encoded match criteria against the event"
    )
    invocation_payload: Optional[str] = Field(
        None, description="The instructions handed to the turn when this fires"
    )
    due_at: Optional[datetime] = Field(
        None, description="When it is first due (a task's due time)"
    )
    priority: int = Field(
        DEFAULT_PRIORITY,
        ge=HIGHEST_PRIORITY,
        le=LOWEST_PRIORITY,
        description="1 (most urgent) to 5; due triggers fire most urgent first",
    )
    last_fired_at: Optional[datetime] = Field(
        None, description="Set by the server: when this trigger last fired"
    )
    next_fire_at: Optional[datetime] = Field(
        None, description="Set by the server: the next time it fires"
    )
    fire_count: int = Field(
        0, description="Set by the server: how many times it has fired"
    )
    # Write-only: excluded from every serialization; the endpoint reads it.
    webhook_secret: Optional[str] = Field(
        None,
        exclude=True,
        description="Set by the server: the webhook's signing secret, encrypted",
    )
    email_address: Optional[str] = Field(
        None, description="Set by the server: where mail wakes an email trigger"
    )

    table_comment: ClassVar[str] = (
        "An InvocationTrigger is a standing listener that triggers an Agent to "
        "take a turn - on a schedule (cron), a timer (interval or one-shot), or "
        "an event (a conversation message, a signed webhook call, an email). "
        "It fires many times; each firing is an InvocationInstance. A task is "
        "a trigger: instructions as its payload, with a due time and a priority."
    )
    permission_references: ClassVar[List[str]] = ["agent"]

    class Create(
        BaseModel,
        AgentModel.Reference.ID,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        invocation_type: InvocationType = Field(
            ..., description="'schedule' | 'timer' | 'event'"
        )
        enabled: Optional[bool] = Field(True)
        cron: Optional[str] = Field(None)
        interval_seconds: Optional[int] = Field(None, gt=0)
        one_shot: Optional[bool] = Field(False)
        event_source: Optional[EventSource] = Field(None)
        event_filter: Optional[str] = Field(None)
        invocation_payload: Optional[str] = Field(None)
        due_at: Optional[datetime] = Field(None)
        priority: Optional[int] = Field(
            DEFAULT_PRIORITY, ge=HIGHEST_PRIORITY, le=LOWEST_PRIORITY
        )
        next_fire_at: Optional[datetime] = Field(None, description="Set by the server")
        email_address: Optional[str] = Field(None, description="Set by the server")

    class Update(BaseModel):
        invocation_type: Optional[InvocationType] = Field(None)
        enabled: Optional[bool] = Field(None)
        cron: Optional[str] = Field(None)
        interval_seconds: Optional[int] = Field(None, gt=0)
        one_shot: Optional[bool] = Field(None)
        event_source: Optional[EventSource] = Field(None)
        event_filter: Optional[str] = Field(None)
        invocation_payload: Optional[str] = Field(None)
        due_at: Optional[datetime] = Field(None)
        priority: Optional[int] = Field(None, ge=HIGHEST_PRIORITY, le=LOWEST_PRIORITY)
        last_fired_at: Optional[datetime] = Field(None, description="Set by the server")
        next_fire_at: Optional[datetime] = Field(None, description="Set by the server")
        fire_count: Optional[int] = Field(None, description="Set by the server")
        webhook_secret: Optional[str] = Field(None, description="Set by the server")
        email_address: Optional[str] = Field(None, description="Set by the server")

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
        priority: Optional[NumericalSearchModel] = None
        email_address: Optional[StringSearchModel] = None


def first_fire(fields: Dict[str, Any]) -> Optional[datetime]:
    """When a trigger configured as ``fields`` first fires: ``due_at`` for
    a timer, the first cron tick after ``due_at`` for a schedule; None for an
    event, or a trigger with no due time (a timer then fires at once, a
    schedule on its next tick)."""
    due_at = fields.get("due_at")
    if due_at is None or fields.get("invocation_type") == "event":
        return None
    if fields.get("invocation_type") == "schedule":
        tick: datetime = croniter(fields["cron"], due_at).get_next(datetime)
        return tick
    due: datetime = due_at
    return due


def check_trigger(fields: Dict[str, Any]) -> None:
    """422 unless ``fields`` describe a trigger that can fire."""

    def refuse(detail: str) -> None:
        raise HTTPException(status_code=422, detail=detail)

    kind = fields.get("invocation_type")
    if kind == "schedule":
        if not fields.get("cron") or not croniter.is_valid(fields["cron"]):
            refuse("A scheduled trigger needs a valid cron expression")
    elif kind == "timer":
        if not fields.get("interval_seconds") and not fields.get("due_at"):
            refuse("A timer needs interval_seconds, due_at, or both")
        if not fields.get("interval_seconds") and not fields.get("one_shot"):
            refuse("A timer without interval_seconds fires once: set one_shot")
    elif kind == "event":
        if not fields.get("event_source"):
            refuse("An event trigger needs an event_source")
        if fields.get("event_source") == EMAIL:
            email_filter(fields.get("event_filter"))


def email_filter(text: Optional[str]) -> Dict[str, str]:
    """An email trigger's ``event_filter``: a JSON object whose ``from`` (an
    address, or ``@domain``) and ``subject`` (a phrase it contains) a
    message must match. 422 for anything else."""
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if (
        not isinstance(parsed, dict)
        or not set(parsed) <= EMAIL_FILTER_KEYS
        or not all(isinstance(value, str) and value for value in parsed.values())
    ):
        raise HTTPException(
            status_code=422,
            detail="An email trigger's event_filter is a JSON object of "
            '"from" and/or "subject" strings',
        )
    return parsed


def email_trigger_address() -> str:
    """A new, unguessable address at the configured domain. 422 when no
    domain is configured: mail could never reach the trigger."""
    domain = env(EMAIL_DOMAIN_SETTING).strip().lower()
    if not domain:
        raise HTTPException(
            status_code=422,
            detail=f"Email triggers need {EMAIL_DOMAIN_SETTING}, the domain "
            "their mail arrives at",
        )
    return f"agent-{secrets.token_hex(EMAIL_ADDRESS_TOKEN_BYTES)}@{domain}"


class NoParameters(RouteModel):
    """An action that takes nothing but its path."""


class WebhookEvent(RouteModel):
    """Any JSON object: the event, exactly as its sender signed it."""

    model_config = ConfigDict(extra="allow")


class WebhookFired(RouteModel):
    invocation_instance_id: str = Field(description="The turn the call fired")
    status: str = Field(description="How the turn ended")


class WebhookSecret(RouteModel):
    trigger_id: str
    secret: str = Field(
        description="Shown once: signs every call to the trigger's webhook"
    )
    timestamp_header: str
    signature_header: str


class InvocationTriggerManager(AbstractBLLManager, RouterMixin):
    _model = InvocationTriggerModel

    prefix: ClassVar[Optional[str]] = "/v1/invocation-trigger"
    tags: ClassVar[Optional[List[str]]] = ["Agent Invocation Trigger Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create(self, **kwargs: Any) -> Any:
        """Triggers owned by the requester, in their agent's team, first
        due when their ``due_at`` says; an email trigger gets its address."""
        parse = self.model_registry.apply(self.Model).Create

        def prepare(fields: Dict[str, Any]) -> Dict[str, Any]:
            fields = _in_agents_team(self, owned_by(self.requester.id)(fields))
            without(fields, (*TRIGGER_BOOKKEEPING, *TRIGGER_SERVER_FIELDS))
            spec = parse(**fields).model_dump()
            check_trigger(spec)
            fields["next_fire_at"] = first_fire(spec)
            if _listens_to(spec, EMAIL):
                fields["email_address"] = email_trigger_address()
            return fields

        return super().create(**each_created(kwargs, prepare))

    def update(self, id: str, **kwargs: Any) -> Any:
        """Owner, team and agent stay; bookkeeping is the monitor's (ROOT);
        a new ``due_at`` or schedule moves the next firing; a trigger turned
        to email gets its address. The webhook secret is written only by
        :meth:`rotate_webhook_secret`."""
        without(kwargs, (*OWNERSHIP_FIELDS, "webhook_secret"))
        if not server_side(self.requester.id):
            without(kwargs, (*TRIGGER_BOOKKEEPING, "email_address"))
            changes = (
                self.model_registry.apply(self.Model)
                .Update(**kwargs)
                .model_dump(exclude_unset=True)
            )
            current = self.get(id=id)
            merged = {**current.model_dump(), **changes}
            check_trigger(merged)
            if {"due_at", "cron", "invocation_type"} & changes.keys():
                kwargs["next_fire_at"] = first_fire(merged)
            if _listens_to(merged, EMAIL) and not current.email_address:
                kwargs["email_address"] = email_trigger_address()
        return super().update(id, **kwargs)

    def rotate_webhook_secret(self, id: str) -> WebhookSecret:
        """A new signing secret for a webhook trigger, which only someone
        who may edit it gets; the old one stops working. It is kept
        encrypted and never shown again."""
        from zephyrex.extensions.ai_agents.EventSources import new_webhook_secret

        trigger = _visible(self, id, "Invocation trigger not found")
        if not _listens_to(trigger.model_dump(), WEBHOOK):
            raise HTTPException(
                status_code=422, detail="Only a webhook trigger has a secret"
            )
        secret = new_webhook_secret()
        super().update(id, webhook_secret=encrypt_secret(secret))
        return WebhookSecret(
            trigger_id=id,
            secret=secret,
            timestamp_header=TIMESTAMP_HEADER,
            signature_header=SIGNATURE_HEADER,
        )

    @custom_route(
        method="POST",
        path="/{trigger_id}/webhook-secret",
        input_model=NoParameters,
        output_model=WebhookSecret,
        authentication_type="jwt",
        openapi_tags=("Agent Invocation Trigger Management",),
        summary="A new signing secret for a webhook trigger, shown once",
        expose_in=(ExposeIn.REST,),
    )
    def webhook_secret_route(self, trigger_id: str) -> WebhookSecret:
        return self.rotate_webhook_secret(trigger_id)

    @custom_route(
        method="POST",
        path=WEBHOOK_PATH,
        input_model=WebhookEvent,
        output_model=WebhookFired,
        authentication_type="none",
        openapi_tags=("Agent Invocation Trigger Management",),
        summary="Fire a webhook trigger: a POST signed with its secret",
        description=(
            "The body is a JSON object. The X-Zephyrex-Signature header is "
            "sha256=<hex HMAC-SHA256 of '<X-Zephyrex-Timestamp>.' + the raw "
            "body>, keyed by the trigger's secret; the timestamp (Unix "
            "seconds) must be recent. The body is handed to the agent's "
            "turn, which runs as the agent's owner. 401 for a bad, stale or "
            "replayed signature."
        ),
        expose_in=(ExposeIn.REST,),
    )
    @rate_limit(WEBHOOK_RATE_LIMIT, scope="(ip, endpoint)")
    async def webhook_route(self, trigger_id: str, request: Request) -> WebhookFired:
        from zephyrex.extensions.ai_agents.EventSources import receive_webhook

        return await receive_webhook(self.model_registry, trigger_id, request)


def _listens_to(fields: Dict[str, Any], source: str) -> bool:
    return bool(
        fields.get("invocation_type") == "event"
        and fields.get("event_source") == source
    )


# A webhook is called by another server, which has no session here: the
# call is authenticated by its signature alone, never a cookie.
accept_cross_site_writes(
    re.escape(InvocationTriggerManager.prefix or "")
    + WEBHOOK_PATH.replace("{trigger_id}", "[^/]+")
)


def _in_agents_team(
    manager: AbstractBLLManager, fields: Dict[str, Any]
) -> Dict[str, Any]:
    """``fields`` for a record of an agent, in the agent's team: what the
    requester sees of the agent decides who else sees the record. A
    malformed agent_id is left for validation (422)."""
    if isinstance(fields.get("agent_id"), str):
        agent = _visible(
            _as(AgentManager, manager), fields["agent_id"], "Agent not found"
        )
        fields["team_id"] = agent.team_id
    return fields


class InvocationInstanceModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    InvocationTriggerModel.Reference.Optional,
    AgentModel.Reference,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """One agent turn: what caused it (its trigger, a conversation message,
    or neither for an ad-hoc turn), what it was handed (``payload``), and its
    lifecycle. The turn's activities hang off it."""

    status: str = Field(
        PENDING,
        description="Set by the server: 'pending' | 'running' | 'succeeded' | 'failed'",
    )
    trigger_message_id: Optional[str] = Field(
        None, description="The conversation message that fired this turn"
    )
    payload: Optional[str] = Field(
        None, description="Prompt/context handed to this specific firing"
    )
    error: Optional[str] = Field(None, description="Set by the server: why it failed")
    started_at: Optional[datetime] = Field(
        None, description="Set by the server: when the turn began"
    )
    completed_at: Optional[datetime] = Field(
        None, description="Set by the server: when the turn finished"
    )

    table_comment: ClassVar[str] = (
        "An InvocationInstance is a single firing of an InvocationTrigger - one "
        "agent turn. It records the cause (trigger and/or triggering message), "
        "the context handed to the turn, and its lifecycle status; the turn's "
        "Activity tree links to it via invocation_instance_id."
    )
    permission_references: ClassVar[List[str]] = ["agent"]

    class Create(
        BaseModel,
        AgentModel.Reference.ID,
        InvocationTriggerModel.Reference.ID.Optional,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        status: Optional[str] = Field(PENDING, description="Set by the server")
        trigger_message_id: Optional[str] = Field(None)
        payload: Optional[str] = Field(None)

    class Update(BaseModel):
        payload: Optional[str] = Field(None)
        status: Optional[str] = Field(None, description="Set by the server")
        error: Optional[str] = Field(None, description="Set by the server")
        started_at: Optional[datetime] = Field(None, description="Set by the server")
        completed_at: Optional[datetime] = Field(None, description="Set by the server")

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

    def create_validation(self, entity: Any) -> None:
        if entity.invocation_trigger_id:
            trigger = _visible(
                _as(InvocationTriggerManager, self),
                entity.invocation_trigger_id,
                "Invocation trigger not found",
            )
            if trigger.agent_id != entity.agent_id:
                raise HTTPException(
                    status_code=400, detail="A turn is its trigger's agent's"
                )
        if entity.trigger_message_id:
            _visible(
                _as(MessageManager, self),
                entity.trigger_message_id,
                "Message not found",
            )

    def create(self, **kwargs: Any) -> Any:
        """Turns owned by the requester, in their agent's team; every turn
        starts pending."""

        def prepare(fields: Dict[str, Any]) -> Dict[str, Any]:
            fields = _in_agents_team(self, owned_by(self.requester.id)(fields))
            fields["status"] = PENDING
            return fields

        return super().create(**each_created(kwargs, prepare))

    def update(self, id: str, **kwargs: Any) -> Any:
        """A turn's lifecycle is the executor's (ROOT) to record."""
        return super().update(
            id, **server_only(self.requester.id, kwargs, TURN_LIFECYCLE)
        )


class TurnRequest(RouteModel):
    payload: Optional[str] = Field(
        None, description="What the agent is asked or told this turn"
    )


class GrantedAbility(RouteModel):
    tool: str = Field(description="The name the agent's model calls it by")
    name: str
    extension: str
    ability_id: str


class GrantedAbilities(RouteModel):
    abilities: List[GrantedAbility]


class AgentManager(AbstractBLLManager, RouterMixin):
    _model = AgentModel

    prefix: ClassVar[Optional[str]] = "/v1/agent"
    tags: ClassVar[Optional[List[str]]] = ["Agent Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create_validation(self, entity: Any) -> None:
        if entity.rotation_id:
            _visible(
                _as(RotationManager, self), entity.rotation_id, "Rotation not found"
            )
        if entity.team_id:
            _visible(_as(TeamManager, self), entity.team_id, "Team not found")
        if entity.user_id != self.requester.id:
            _user_exists(self.model_registry, entity.user_id)

    def create(self, **kwargs: Any) -> Any:
        """Agents owned by the requester (ROOT and SYSTEM may name another)."""
        return super().create(**each_created(kwargs, owned_by(self.requester.id)))

    def update(self, id: str, **kwargs: Any) -> Any:
        """An agent's owner and team are not changed by an update."""
        without(kwargs, OWNERSHIP_FIELDS)
        if kwargs.get("rotation_id"):
            _visible(
                _as(RotationManager, self), kwargs["rotation_id"], "Rotation not found"
            )
        return super().update(id, **kwargs)

    async def take_turn(self, agent_id: str, payload: Optional[str]) -> Any:
        """Run one turn of the agent now, handed ``payload``; the turn's
        instance, finished (succeeded or failed)."""
        from zephyrex.extensions.ai_agents.AgentTurnExecutor import (
            AgentTurnExecutor,
        )

        instances = _as(InvocationInstanceManager, self)
        instance = instances.create(agent_id=agent_id, payload=payload)
        await AgentTurnExecutor(
            model_registry=self.model_registry, requester_id=self.requester.id
        ).run(instance.id)
        return instances.get(id=instance.id)

    @custom_route(
        method="POST",
        path="/{agent_id}/turn",
        input_model=TurnRequest,
        output_model=InvocationInstanceModel,
        authentication_type="jwt",
        openapi_tags=("Agent Management",),
        summary="Run a turn of the agent now",
        expose_in=(ExposeIn.REST,),
    )
    async def turn_route(self, agent_id: str, body: TurnRequest) -> Any:
        return await self.take_turn(agent_id, body.payload)

    @custom_route(
        method="GET",
        path="/{agent_id}/abilities",
        output_model=GrantedAbilities,
        authentication_type="jwt",
        openapi_tags=("Agent Management",),
        summary="The abilities the agent may use, as its model sees them",
        expose_in=(ExposeIn.REST,),
    )
    def abilities_route(self, agent_id: str) -> GrantedAbilities:
        _visible(self, agent_id, "Agent not found")
        grants = _as(AgentAbilityManager, self).grants(agent_id)
        return GrantedAbilities(
            abilities=[
                GrantedAbility(
                    tool=tool,
                    name=grant.name,
                    extension=grant.extension,
                    ability_id=grant.ability_id,
                )
                for tool, grant in tool_names(grants).items()
            ]
        )


class ConversationAgentModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    ConversationModel.Reference,
    metaclass=ModelMeta,
):
    active: bool = Field(
        True, description="Whether the agent is actively participating"
    )
    auto_respond: bool = Field(
        False,
        description="Whether the agent takes a turn on every user message, trigger or not",
    )

    table_comment: ClassVar[str] = (
        "ConversationAgent represents an AI agent's participation in a conversation, allowing agents to be conversation participants alongside users."
    )
    permission_references: ClassVar[List[str]] = ["agent"]

    class Create(BaseModel, AgentModel.Reference.ID, ConversationModel.Reference.ID):
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

    class Search(
        ApplicationModel.Search,
        AgentModel.Reference.ID.Search,
        ConversationModel.Reference.ID.Search,
    ):
        active: Optional[bool] = None
        auto_respond: Optional[bool] = None


class ConversationAgentManager(AbstractBLLManager, RouterMixin):
    _model = ConversationAgentModel

    def create_validation(self, entity: Any) -> None:
        _visible(_as(AgentManager, self), entity.agent_id, "Agent not found")
        _visible(
            _as(ConversationManager, self),
            entity.conversation_id,
            "Conversation not found",
        )


class ProviderInstanceAgentModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    ProviderInstanceModel.Reference,
    metaclass=ModelMeta,
):
    table_comment: ClassVar[str] = (
        "A ProviderInstanceAgent pins an Agent to a ProviderInstance: an agent "
        "with any thinks only on those instances, in the order linked, instead "
        "of its rotation."
    )
    permission_references: ClassVar[List[str]] = ["agent"]

    class Create(
        BaseModel, AgentModel.Reference.ID, ProviderInstanceModel.Reference.ID
    ):
        pass

    class Update(BaseModel):
        pass

    class Search(
        ApplicationModel.Search,
        AgentModel.Reference.ID.Search,
        ProviderInstanceModel.Reference.ID.Search,
    ):
        pass


def _agent_and_instance_visible(manager: AbstractBLLManager, entity: Any) -> None:
    """The agent is one the requester sees, and the instance one both they
    and the agent's owner see: the owner's turns think on it."""
    agent = _visible(_as(AgentManager, manager), entity.agent_id, "Agent not found")
    _visible(
        _as(ProviderInstanceManager, manager),
        entity.provider_instance_id,
        "Provider instance not found",
    )
    if agent.user_id and agent.user_id != manager.requester.id:
        _visible(
            ProviderInstanceManager(
                requester_id=agent.user_id, model_registry=manager.model_registry
            ),
            entity.provider_instance_id,
            "Provider instance not found",
        )


class ProviderInstanceAgentManager(AbstractBLLManager, RouterMixin):
    _model = ProviderInstanceAgentModel

    def create_validation(self, entity: Any) -> None:
        _agent_and_instance_visible(self, entity)

    def pinned(self, agent_id: str) -> List[str]:
        """The instances the agent is pinned to, in the order linked."""
        ordered: List[str] = []
        for link in self.list(
            agent_id=agent_id, sort_by="created_at", sort_order="asc"
        ):
            if link.provider_instance_id not in ordered:
                ordered.append(link.provider_instance_id)
        return ordered


class ProviderInstanceAgentAbilityModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    ProviderInstanceModel.Reference,
    AbilityModel.Reference,
    metaclass=ModelMeta,
):
    """What one of an agent's pinned instances may be used for. An instance
    with no rows may be used for anything; one with rows only for the
    abilities whose row has ``state`` true."""

    state: bool = Field(
        default=True, description="Whether the instance may be used for the ability"
    )

    table_comment: ClassVar[str] = (
        "A ProviderInstanceAgentAbility restricts what one of an Agent's pinned "
        "ProviderInstances is used for: with any rows, only the abilities whose "
        "row has state true."
    )
    permission_references: ClassVar[List[str]] = ["agent"]

    class Create(
        BaseModel,
        AgentModel.Reference.ID,
        ProviderInstanceModel.Reference.ID,
        AbilityModel.Reference.ID,
    ):
        state: bool = Field(
            default=True,
            description="Whether the instance may be used for the ability",
        )

    class Update(BaseModel):
        state: Optional[bool] = Field(
            None, description="Whether the instance may be used for the ability"
        )

    class Search(
        ApplicationModel.Search,
        AgentModel.Reference.ID.Search,
        ProviderInstanceModel.Reference.ID.Search,
        AbilityModel.Reference.ID.Search,
    ):
        state: Optional[bool] = None


class ProviderInstanceAgentAbilityManager(AbstractBLLManager, RouterMixin):
    _model = ProviderInstanceAgentAbilityModel

    def create_validation(self, entity: Any) -> None:
        _agent_and_instance_visible(self, entity)
        _visible(_catalog(AbilityManager, self), entity.ability_id, "Ability not found")

    def allowed(self, agent_id: str) -> Dict[str, Set[str]]:
        """Each restricted instance of the agent's: the ability ids it may
        be used for. An instance not in it is unrestricted."""
        found: Dict[str, Set[str]] = {}
        for row in self.list(agent_id=agent_id):
            abilities = found.setdefault(row.provider_instance_id, set())
            if row.state:
                abilities.add(row.ability_id)
        return found


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

    class Update(BaseModel, NameMixinModel.Optional, ParentMixinModel.Optional):
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

    def create_validation(self, entity: Any) -> None:
        if entity.user_id != self.requester.id:
            _user_exists(self.model_registry, entity.user_id)
        if entity.team_id:
            _visible(_as(TeamManager, self), entity.team_id, "Team not found")
        if entity.parent_id:
            _visible(self, entity.parent_id, "Parent project not found")

    def create(self, **kwargs: Any) -> Any:
        """Projects owned by the requester (ROOT and SYSTEM may name another)."""
        return super().create(**each_created(kwargs, owned_by(self.requester.id)))

    def update(self, id: str, **kwargs: Any) -> Any:
        """A project's owner and team are not changed by an update; a new
        parent is one the requester sees, and not the project itself."""
        without(kwargs, OWNERSHIP_FIELDS)
        if kwargs.get("parent_id"):
            if kwargs["parent_id"] == id:
                raise HTTPException(
                    status_code=400, detail="A project is not its own parent"
                )
            _visible(self, kwargs["parent_id"], "Parent project not found")
        return super().update(id, **kwargs)


class ProjectContextProviderModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    ProjectModel.Reference,
    ProviderModel.Reference,
    metaclass=ModelMeta,
):
    context_resource: Optional[str] = Field(
        None, description="Resource identifier for context"
    )

    table_comment: ClassVar[str] = (
        "A ProjectContextProvider represents the association of a Provider with a Project for context injection."
    )
    permission_references: ClassVar[List[str]] = ["project"]

    class Create(BaseModel, ProjectModel.Reference.ID, ProviderModel.Reference.ID):
        context_resource: Optional[str] = Field(
            None, description="Resource identifier for context"
        )

    class Update(BaseModel):
        context_resource: Optional[str] = Field(
            None, description="Resource identifier for context"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ProjectModel.Reference.ID.Search,
        ProviderModel.Reference.ID.Search,
    ):
        context_resource: Optional[StringSearchModel] = None


class ProjectContextProviderManager(AbstractBLLManager, RouterMixin):
    _model = ProjectContextProviderModel

    def create_validation(self, entity: Any) -> None:
        _visible(_as(ProjectManager, self), entity.project_id, "Project not found")
        _visible(
            _catalog(ProviderManager, self), entity.provider_id, "Provider not found"
        )


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
    permission_references: ClassVar[List[str]] = ["project"]

    class Create(BaseModel, ProjectModel.Reference.ID, PromptModel.Reference.ID):
        pass

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

    def create_validation(self, entity: Any) -> None:
        _visible(_as(ProjectManager, self), entity.project_id, "Project not found")
        _visible(_as(PromptManager, self), entity.prompt_id, "Prompt not found")


class ProjectConversationModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    ProjectModel.Reference,
    ConversationModel.Reference,
    metaclass=ModelMeta,
):
    """A conversation filed in a project. Whoever sees the project sees its
    links; linking or unlinking needs edit on the project, and a link is
    only to a conversation the linker sees."""

    table_comment: ClassVar[str] = (
        "A ProjectConversation files a Conversation in a Project; it is seen "
        "and changed through the project."
    )
    permission_references: ClassVar[List[str]] = ["project"]

    class Create(BaseModel, ProjectModel.Reference.ID, ConversationModel.Reference.ID):
        pass

    class Update(BaseModel):
        pass

    class Search(
        ApplicationModel.Search,
        ProjectModel.Reference.ID.Search,
        ConversationModel.Reference.ID.Search,
    ):
        pass


class ProjectConversationManager(AbstractBLLManager, RouterMixin):
    _model = ProjectConversationModel

    def create_validation(self, entity: Any) -> None:
        _visible(_as(ProjectManager, self), entity.project_id, "Project not found")
        _visible(
            _as(ConversationManager, self),
            entity.conversation_id,
            "Conversation not found",
        )

    def link(self, project_id: str, conversation_id: str) -> Any:
        """File the conversation in the project; filing it twice is once."""
        existing = self.list(project_id=project_id, conversation_id=conversation_id)
        if existing:
            return existing[0]
        return self.create(project_id=project_id, conversation_id=conversation_id)

    def unlink(self, project_id: str, conversation_id: str) -> int:
        """Take the conversation out of the project; how many links went.
        404 when the project is not one the requester sees."""
        _visible(_as(ProjectManager, self), project_id, "Project not found")
        links = self.list(project_id=project_id, conversation_id=conversation_id)
        for link in links:
            self.delete(id=link.id)
        return len(links)


class ActivityModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    ParentMixinModel,
    InvocationInstanceModel.Reference,
    AbilityModel.Reference,
    ArtifactModel.Reference.Optional,
    ProviderModel.Reference.Optional,
    metaclass=ModelMeta,
):
    title: str = Field(..., description="Title of the activity")
    body: str = Field(..., description="Body content of the activity")
    state: Optional[ActivityState] = Field(None, description="State of the activity")

    table_comment: ClassVar[str] = (
        "An Activity represents an action an Agent takes (or took) during a turn. "
        "Every activity of a turn belongs to its InvocationInstance via "
        "invocation_instance_id; sub-actions (e.g., steps in a web search) are "
        "indicated with the parent_id field. Activities are typed using "
        "ability_id and can optionally produce an Artifact."
    )
    permission_references: ClassVar[List[str]] = ["invocation_instance"]

    class Create(
        BaseModel,
        InvocationInstanceModel.Reference.ID,
        AbilityModel.Reference.ID,
        ParentMixinModel.Optional,
        ProviderModel.Reference.ID.Optional,
        ArtifactModel.Reference.ID.Optional,
    ):
        title: str = Field(..., description="Title of the activity")
        body: str = Field(..., description="Body content of the activity")
        state: Optional[ActivityState] = Field(
            None, description="State of the activity"
        )

    class Update(BaseModel):
        title: Optional[str] = Field(None, description="Title of the activity")
        body: Optional[str] = Field(None, description="Body content of the activity")
        state: Optional[ActivityState] = Field(
            None, description="State of the activity"
        )
        artifact_id: Optional[str] = Field(
            None, description="ID of the artifact created by this activity"
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


class ActivityHierarchy(RouteModel):
    activities: Dict[str, Any] = Field(
        description="Each root activity's id: {activity, children}, recursively"
    )


class ActivityManager(AbstractBLLManager, RouterMixin):
    _model = ActivityModel

    prefix: ClassVar[Optional[str]] = "/v1/activity"
    tags: ClassVar[Optional[List[str]]] = ["Activity Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def hierarchy(self, invocation_instance_id: str) -> Dict[str, Any]:
        """A turn's activities as trees: each root activity's id to
        ``{activity, children}``, children keyed by id the same way."""
        _visible(
            _as(InvocationInstanceManager, self),
            invocation_instance_id,
            "Invocation instance not found",
        )
        activities = self.list(invocation_instance_id=invocation_instance_id)
        children_of: Dict[Optional[str], List[Any]] = {}
        for activity in activities:
            children_of.setdefault(activity.parent_id, []).append(activity)

        placed: Set[str] = set()

        def tree(activity: Any) -> Dict[str, Any]:
            placed.add(activity.id)
            return {
                "activity": activity.model_dump(mode="json"),
                "children": {
                    child.id: tree(child)
                    for child in children_of.get(activity.id, [])
                    if child.id not in placed
                },
            }

        return {a.id: tree(a) for a in children_of.get(None, [])}

    @custom_route(
        method="GET",
        path="/hierarchy/{invocation_instance_id}",
        output_model=ActivityHierarchy,
        authentication_type="jwt",
        openapi_tags=("Activity Management",),
        summary="A turn's activities as trees",
        expose_in=(ExposeIn.REST,),
    )
    def hierarchy_route(self, invocation_instance_id: str) -> ActivityHierarchy:
        return ActivityHierarchy(activities=self.hierarchy(invocation_instance_id))

    def create_validation(self, entity: Any) -> None:
        _visible(
            _as(InvocationInstanceManager, self),
            entity.invocation_instance_id,
            "Invocation instance not found",
        )
        _visible(_catalog(AbilityManager, self), entity.ability_id, "Ability not found")
        if entity.parent_id:
            parent = _visible(self, entity.parent_id, "Parent activity not found")
            if parent.invocation_instance_id != entity.invocation_instance_id:
                raise HTTPException(
                    status_code=400, detail="An activity is in its parent's turn"
                )
        if entity.artifact_id:
            _visible(
                _as(ArtifactManager, self), entity.artifact_id, "Artifact not found"
            )
        if entity.provider_id:
            _visible(
                _catalog(ProviderManager, self),
                entity.provider_id,
                "Provider not found",
            )

    def update(self, id: str, **kwargs: Any) -> Any:
        if kwargs.get("artifact_id"):
            _visible(
                _as(ArtifactManager, self), kwargs["artifact_id"], "Artifact not found"
            )
        return super().update(id, **kwargs)


class AgentContextPromptModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    PromptModel.Reference,
    metaclass=ModelMeta,
):
    table_comment: ClassVar[str] = (
        "An AgentContextPrompt represents the association of a Prompt with an Agent for context injection."
    )
    permission_references: ClassVar[List[str]] = ["agent"]

    class Create(BaseModel, AgentModel.Reference.ID, PromptModel.Reference.ID):
        pass

    class Update(BaseModel):
        pass

    class Search(
        ApplicationModel.Search,
        AgentModel.Reference.ID.Search,
        PromptModel.Reference.ID.Search,
    ):
        pass


class AgentContextPromptManager(AbstractBLLManager, RouterMixin):
    _model = AgentContextPromptModel

    def create_validation(self, entity: Any) -> None:
        _visible(_as(AgentManager, self), entity.agent_id, "Agent not found")
        _visible(_as(PromptManager, self), entity.prompt_id, "Prompt not found")

    def contents(self, agent_id: str) -> List[str]:
        """The agent's context prompts' contents, in the order linked; a
        prompt the requester can no longer see is left out."""
        prompts = _as(PromptManager, self)
        found: List[str] = []
        for link in self.list(
            agent_id=agent_id, sort_by="created_at", sort_order="asc"
        ):
            try:
                content = prompts.get(id=link.prompt_id).content
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                continue
            if content:
                found.append(content)
        return found


class AgentAbilityModel(
    ApplicationModel.Optional,
    UpdateMixinModel,
    AgentModel.Reference,
    AbilityModel.Reference,
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
    permission_references: ClassVar[List[str]] = ["agent"]

    class Create(BaseModel, AgentModel.Reference.ID, AbilityModel.Reference.ID):
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

    def create_validation(self, entity: Any) -> None:
        _visible(_as(AgentManager, self), entity.agent_id, "Agent not found")
        _visible(_catalog(AbilityManager, self), entity.ability_id, "Ability not found")

    def grants(self, agent_id: str) -> List[AbilityGrant]:
        """The abilities the agent may use: its enabled grants, each with
        the extension that performs it. A grant whose ability is gone is
        left out."""
        abilities = _catalog(AbilityManager, self)
        extensions = _catalog(ExtensionManager, self)
        extension_names: Dict[str, str] = {}
        found: List[AbilityGrant] = []
        for link in self.list(agent_id=agent_id, enabled=True):
            try:
                ability = abilities.get(id=link.ability_id)
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                continue
            if ability.extension_id not in extension_names:
                extension_names[ability.extension_id] = extensions.get(
                    id=ability.extension_id
                ).name
            found.append(
                AbilityGrant(
                    ability_id=link.ability_id,
                    name=ability.name,
                    extension=extension_names[ability.extension_id],
                )
            )
        return found


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
    lives in the ai_memories extension.
    """

    key: str = Field(..., description="Memory key (unique per agent)")
    content: str = Field(..., description="Memory content")

    table_comment: ClassVar[str] = (
        "An AgentMemory is a keyed short-term (working) memory for an Agent, "
        "injected into each turn's prompt and prunable via the trim ability."
    )
    permission_references: ClassVar[List[str]] = ["agent"]

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

    def create_validation(self, entity: Any) -> None:
        _visible(_as(AgentManager, self), entity.agent_id, "Agent not found")
        if self.list(agent_id=entity.agent_id, key=entity.key):
            raise HTTPException(
                status_code=409, detail="The agent already has a memory with that key"
            )

    def remember(self, agent_id: str, key: str, content: str) -> Any:
        """Write ``content`` under ``key``, replacing what was there."""
        existing = self.list(agent_id=agent_id, key=key)
        if existing:
            return self.update(id=existing[0].id, content=content)
        return self.create(agent_id=agent_id, key=key, content=content)

    def forget(self, agent_id: str, keys: List[str]) -> int:
        """Delete the given keys; how many entries went."""
        removed = 0
        for key in keys:
            for entry in self.list(agent_id=agent_id, key=key):
                self.delete(id=entry.id)
                removed += 1
        return removed

    def as_dict(self, agent_id: str) -> Dict[str, str]:
        """The agent's short-term memory as ``{key: content}``."""
        return {entry.key: entry.content for entry in self.list(agent_id=agent_id)}


def _user_exists(model_registry: Any, user_id: str) -> None:
    """404 unless ``user_id`` names a user: for ROOT and SYSTEM, who may
    create on a user's behalf."""
    try:
        UserManager(requester_id=env("SYSTEM_ID"), model_registry=model_registry).get(
            id=user_id
        )
    except HTTPException:
        raise _not_found("User not found") from None


@hook_bll(MessageManager.create, timing=HookTiming.AFTER)
async def fire_conversation_message_turns(context: HookContext) -> None:
    """A user's message wakes the agents in its conversation that react to
    messages: each active participant with ``auto_respond`` or an enabled
    ``conversation_message`` trigger takes a turn, as its owner, handed the
    message.

    Agents post through ``MessageManager.create_agent_message``, which is not
    ``create``; a message without an author is skipped too, so an agent's
    reply never wakes an agent."""
    if not _ai_agents_loaded(context.manager):
        return
    for message in created_records(context.result):
        if message.user_id:
            await _wake_agents_for(context.manager.model_registry, message)


async def _wake_agents_for(registry: Any, message: Any) -> None:
    from zephyrex.extensions.ai_agents.AgentTurnExecutor import fire_turn
    from zephyrex.extensions.ai_agents.EventSources import count_firing

    root = env("ROOT_ID")
    seats = ConversationAgentManager(requester_id=root, model_registry=registry).list(
        conversation_id=message.conversation_id, active=True
    )
    triggers = InvocationTriggerManager(requester_id=root, model_registry=registry)
    for seat in seats:
        listening = triggers.list(
            agent_id=seat.agent_id,
            invocation_type="event",
            event_source="conversation_message",
            enabled=True,
        )
        if not seat.auto_respond and not listening:
            continue
        try:
            await fire_turn(
                registry,
                seat.agent_id,
                message.content,
                trigger_id=listening[0].id if listening else None,
                trigger_message_id=message.id,
            )
        except HTTPException as refused:
            logger.warning(
                "Agent %s cannot take a turn on message %s: %s",
                seat.agent_id,
                message.id,
                refused.detail,
            )
            continue
        if listening:
            count_firing(registry, listening[0])


async def fire_email_triggers(model_registry: Any, message: InboundEmail) -> None:
    """Mail the email extension received wakes the agents whose email
    triggers it is addressed to and matches (see EventSources). It listens
    only in apps that load this extension."""
    from zephyrex.extensions.ai_agents.EventSources import receive_email

    await receive_email(model_registry, message)


on_inbound_email(EXTENSION_NAME, fire_email_triggers)


@hook_bll(TeamManager.create, timing=HookTiming.AFTER, priority=10)
def create_agent_on_team_creation(context: HookContext) -> None:
    """Every new team gets an agent, its creator's, marked favourite."""
    if not _ai_agents_loaded(context.manager):
        return
    agents = AgentManager(
        requester_id=context.manager.requester.id,
        model_registry=context.manager.model_registry,
    )
    for team in created_records(context.result):
        agents.create(name=f"{team.name} Agent", team_id=team.id, favourite=True)


@hook_bll(ConversationManager.create, timing=HookTiming.AFTER, priority=10)
def associate_agent_with_conversation(context: HookContext) -> None:
    """A new conversation gets an agent of one of its creator's teams, one
    with a rotation to think with, auto-responding."""
    if not _ai_agents_loaded(context.manager):
        return
    requester_id = context.manager.requester.id
    registry = context.manager.model_registry
    seats = ConversationAgentManager(requester_id=requester_id, model_registry=registry)
    agents = AgentManager(requester_id=requester_id, model_registry=registry)
    memberships = UserTeamManager(requester_id=requester_id, model_registry=registry)
    for conversation in created_records(context.result):
        if seats.list(conversation_id=conversation.id, active=True):
            continue
        team_agents = (
            agent
            for membership in memberships.list(user_id=conversation.user_id)
            for agent in agents.list(team_id=membership.team_id)
            if agent.rotation_id
        )
        agent = next(team_agents, None)
        if agent is not None:
            seats.create(
                conversation_id=conversation.id,
                agent_id=agent.id,
                active=True,
                auto_respond=True,
            )
