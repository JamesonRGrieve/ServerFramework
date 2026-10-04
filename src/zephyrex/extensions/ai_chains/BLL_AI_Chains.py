# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chains: owned, ordered steps that a run executes (see ChainEngine).

A chain belongs to whoever creates it (ROOT and SYSTEM may name another
owner); an update never moves its owner or team. Its steps inherit access
from it, so only someone who may edit a chain changes its steps or runs it.
A run executes as the chain's owner and inherits access from the chain; the
record of each step it executed (a :class:`ChainStepResultModel`) inherits
access from the run. A run's lifecycle and its step results are written by
the server alone.

A step is one of four kinds, in ``position`` order:

- ``prompt``: a stored prompt (ai_prompts) filled from the run's variables
  and sent to the AI extension's chat models; the answer's text is the
  step's output.
- ``ability``: an extension's ability, called with ``arguments`` evaluated
  from the run's variables; its result is the output.
- ``condition``: an ``expression`` over the run's variables; true goes to
  the step named ``on_true``, false to ``on_false`` (unnamed: the next step;
  ``end``: finish). A jump back to this step or an earlier one is a loop,
  and needs ``max_loops``, the most times it may be taken.
- ``set``: ``variable`` takes the value of ``expression``.

A prompt or ability step's output goes to ``variable`` when one is named.
Expressions are SafeExpressions (``zephyrex.lib.SafeExpression``): no code
runs, and their size and work are bounded.

Every record a step references (its prompt, its ability) is read as the
requester when the step is saved, and as the chain's owner when it runs.
"""

import json
import re
from datetime import datetime
from typing import Any, Callable, ClassVar, Dict, List, Literal, Optional

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel as RouteModel
from pydantic import Field, ValidationError

from zephyrex.database.StaticPermissions import is_root_id, is_system_id
from zephyrex.extensions.ai_agents.AbilityInvoker import NEVER_AGENT_INVOCABLE
from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptManager, PromptModel
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.SafeExpression import ExpressionError, check
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DescriptionMixinModel,
    ModelMeta,
    NameMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel
from zephyrex.logic.BLL_Extensions import AbilityManager, AbilityModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

# A chain's own bounds, and the ceilings an owner may raise them to.
DEFAULT_MAX_STEPS = 100
MAX_MAX_STEPS = 10_000
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 3_600
DEFAULT_MAX_OUTPUT_CHARACTERS = 20_000
MAX_MAX_OUTPUT_CHARACTERS = 100_000
MAX_LOOPS = 1_000

# What one chain may hold, and what one run may carry.
MAX_STEPS_PER_CHAIN = 200
MAX_INPUT_CHARACTERS = 100_000
MAX_VARIABLES_CHARACTERS = 1_000_000
MAX_ARGUMENTS = 50

IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
END = "end"
StepKind = Literal["prompt", "ability", "condition", "set"]
RunStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
PENDING: RunStatus = "pending"
RUNNING: RunStatus = "running"
SUCCEEDED: RunStatus = "succeeded"
FAILED: RunStatus = "failed"
CANCELLED: RunStatus = "cancelled"

# Abilities no chain step may call: running chains (a chain running itself
# never ends), and everything no agent may use.
NEVER_CHAIN_INVOCABLE: frozenset[str] = NEVER_AGENT_INVOCABLE | {"run_chain"}

# A run's lifecycle, which only the engine (ROOT) writes.
RUN_LIFECYCLE = (
    "status",
    "error",
    "error_kind",
    "variables",
    "output",
    "steps_executed",
    "started_at",
    "completed_at",
)


class ChainError(Exception):
    """Why a run stopped short of finishing; ``kind`` is recorded on it."""

    kind: ClassVar[str] = "error"


class ChainDefinitionError(ChainError):
    """The chain's steps cannot run as defined (a jump to no step, a loop
    without a bound, a step missing what its kind needs)."""

    kind = "definition"


class ChainLimitError(ChainError):
    """A bound was reached: steps executed, a loop's count, an output's or
    the variables' size."""

    kind = "limit"


class ChainTimeoutError(ChainError):
    """The run's deadline passed."""

    kind = "timeout"


class ChainStepError(ChainError):
    """A step failed: its expression, prompt, model or ability."""

    kind = "step"


class ChainCancelledError(ChainError):
    """The run was cancelled."""

    kind = "cancelled"


def _server_side(requester_id: str) -> bool:
    """ROOT and SYSTEM act on others' behalf; users act as themselves."""
    return is_root_id(requester_id) or is_system_id(requester_id)


def _each(
    kwargs: Dict[str, Any], prepare: Callable[[Dict[str, Any]], Dict[str, Any]]
) -> Dict[str, Any]:
    """``kwargs`` for a create, or each of a batch's ``entities``, prepared."""
    if isinstance(kwargs.get("entities"), list):
        return {**kwargs, "entities": [prepare(dict(e)) for e in kwargs["entities"]]}
    return prepare(dict(kwargs))


def _owned_by(requester_id: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """The owner is the requester; ROOT and SYSTEM may name another."""

    def prepare(fields: Dict[str, Any]) -> Dict[str, Any]:
        if not _server_side(requester_id) or not fields.get("user_id"):
            fields["user_id"] = requester_id
        return fields

    return prepare


def _as(manager_class: Any, source: AbstractBLLManager) -> Any:
    """``manager_class`` acting as ``source``'s requester."""
    return manager_class(
        requester_id=source.requester.id, model_registry=source.model_registry
    )


def _visible(manager: AbstractBLLManager, record_id: str, detail: str) -> Any:
    """The record ``record_id`` as ``manager``'s requester sees it, or 404."""
    try:
        return manager.get(id=record_id)
    except HTTPException as error:
        if error.status_code == 404:
            raise HTTPException(status_code=404, detail=detail) from None
        raise


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def json_size(value: Any) -> int:
    """The length of ``value`` as JSON text."""
    return len(json.dumps(jsonable_encoder(value), default=str))


def check_inputs(inputs: Any) -> Dict[str, Any]:
    """A run's inputs: an object of variables, each named as an identifier,
    within the size limit."""
    if inputs is None:
        return {}
    if not isinstance(inputs, dict):
        raise _invalid("a run's inputs are an object of variables")
    for name in inputs:
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
            raise _invalid(f"an input's name is an identifier, not {name!r}")
    if json_size(inputs) > MAX_INPUT_CHARACTERS:
        raise _invalid(f"a run's inputs are at most {MAX_INPUT_CHARACTERS} characters")
    return dict(inputs)


def _expression(text: Optional[str], what: str) -> None:
    try:
        check(text or "")
    except ExpressionError as error:
        raise _invalid(f"{what}: {error}") from None


def _identifier(name: Optional[str], what: str) -> None:
    if name is None or not IDENTIFIER.fullmatch(name) or name == END:
        raise _invalid(f"{what} is an identifier other than {END!r}, not {name!r}")


def check_step(step: Dict[str, Any]) -> None:
    """``step``'s fields are what its kind needs (422 otherwise): the parts
    of a step that can be checked without reading anything."""
    kind = step.get("kind")
    _identifier(step.get("name"), "a step's name")
    if step.get("variable") is not None or kind == "set":
        _identifier(step.get("variable"), "a step's variable")
    arguments = step.get("arguments") or {}
    if len(arguments) > MAX_ARGUMENTS:
        raise _invalid(f"a step has at most {MAX_ARGUMENTS} arguments")
    for name, expression in arguments.items():
        if not IDENTIFIER.fullmatch(name):
            raise _invalid(f"an argument's name is an identifier, not {name!r}")
        _expression(expression, f"argument {name}")
    if kind == "prompt" and not step.get("prompt_id"):
        raise _invalid("a prompt step names its prompt")
    if kind == "ability" and not step.get("ability_id"):
        raise _invalid("an ability step names its ability")
    if kind in ("condition", "set"):
        _expression(step.get("expression"), f"a {kind} step's expression")
    for target in ("on_true", "on_false"):
        named = step.get(target)
        if named is not None and named != END and not IDENTIFIER.fullmatch(named):
            raise _invalid(f"{target} names a step or {END!r}, not {named!r}")
    if kind != "condition" and (step.get("on_true") or step.get("on_false")):
        raise _invalid("only a condition step jumps")


class ChainModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    favourite: bool = Field(False, description="Marked as a favourite")
    max_steps: int = Field(
        DEFAULT_MAX_STEPS,
        description="The most steps a run executes (loops included)",
        ge=1,
        le=MAX_MAX_STEPS,
    )
    timeout_seconds: int = Field(
        DEFAULT_TIMEOUT_SECONDS,
        description="How long a run may take before it fails",
        ge=1,
        le=MAX_TIMEOUT_SECONDS,
    )
    max_output_characters: int = Field(
        DEFAULT_MAX_OUTPUT_CHARACTERS,
        description="The largest output (as JSON text) one step may produce",
        ge=1,
        le=MAX_MAX_OUTPUT_CHARACTERS,
    )

    table_comment: ClassVar[str] = (
        "A Chain is an owned, ordered set of steps (prompts, abilities, "
        "conditions and variables) that a run executes within its bounds."
    )
    is_system_entity: ClassVar[bool] = False

    class Create(
        BaseModel,
        NameMixinModel,
        DescriptionMixinModel.Optional,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        favourite: bool = Field(False, description="Marked as a favourite")
        max_steps: int = Field(DEFAULT_MAX_STEPS, ge=1, le=MAX_MAX_STEPS)
        timeout_seconds: int = Field(
            DEFAULT_TIMEOUT_SECONDS, ge=1, le=MAX_TIMEOUT_SECONDS
        )
        max_output_characters: int = Field(
            DEFAULT_MAX_OUTPUT_CHARACTERS, ge=1, le=MAX_MAX_OUTPUT_CHARACTERS
        )

    class Update(BaseModel, NameMixinModel.Optional, DescriptionMixinModel.Optional):
        favourite: Optional[bool] = Field(None, description="Marked as a favourite")
        max_steps: Optional[int] = Field(None, ge=1, le=MAX_MAX_STEPS)
        timeout_seconds: Optional[int] = Field(None, ge=1, le=MAX_TIMEOUT_SECONDS)
        max_output_characters: Optional[int] = Field(
            None, ge=1, le=MAX_MAX_OUTPUT_CHARACTERS
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        DescriptionMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        favourite: Optional[bool] = Field(None, description="Filter by favourite")


class ChainRunModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    ChainModel.Reference,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """One run of a chain: what it started with, where it got to, and how
    it ended. The steps it executed are its ChainStepResults."""

    status: RunStatus = Field(
        PENDING,
        description="Set by the server: pending | running | succeeded | "
        "failed | cancelled",
    )
    inputs: Optional[Dict[str, Any]] = Field(
        None, description="The run's starting variables"
    )
    variables: Optional[Dict[str, Any]] = Field(
        None, description="Set by the server: the variables when it ended"
    )
    output: Optional[str] = Field(
        None, description="Set by the server: the last step's output, as JSON"
    )
    error: Optional[str] = Field(None, description="Set by the server: why it failed")
    error_kind: Optional[str] = Field(
        None,
        description="Set by the server: definition | limit | timeout | step | "
        "cancelled | error",
    )
    steps_executed: int = Field(0, description="Set by the server")
    cancel_requested: bool = Field(
        False, description="Asked to stop: the run stops before its next step"
    )
    started_at: Optional[datetime] = Field(None, description="Set by the server")
    completed_at: Optional[datetime] = Field(None, description="Set by the server")

    table_comment: ClassVar[str] = (
        "A ChainRun is one execution of a Chain, as its owner: its inputs, "
        "final variables and output, and its lifecycle."
    )
    is_system_entity: ClassVar[bool] = False
    permission_references: ClassVar[List[str]] = ["chain"]

    class Create(BaseModel, ChainModel.Reference.ID, UserModel.Reference.ID.Optional):
        inputs: Optional[Dict[str, Any]] = None
        status: Optional[str] = Field(PENDING, description="Set by the server")

    class Update(BaseModel):
        cancel_requested: Optional[bool] = None
        status: Optional[str] = Field(None, description="Set by the server")
        error: Optional[str] = Field(None, description="Set by the server")
        error_kind: Optional[str] = Field(None, description="Set by the server")
        variables: Optional[Dict[str, Any]] = Field(
            None, description="Set by the server"
        )
        output: Optional[str] = Field(None, description="Set by the server")
        steps_executed: Optional[int] = Field(None, description="Set by the server")
        started_at: Optional[datetime] = Field(None, description="Set by the server")
        completed_at: Optional[datetime] = Field(None, description="Set by the server")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ChainModel.Reference.ID.Search,
        UserModel.Reference.ID.Search,
    ):
        status: Optional[StringSearchModel] = None


class RunRequest(RouteModel):
    inputs: Dict[str, Any] = Field(
        default_factory=dict, description="The run's starting variables"
    )


class ChainManager(AbstractBLLManager, RouterMixin):
    _model = ChainModel

    prefix: ClassVar[Optional[str]] = "/v1/chain"
    tags: ClassVar[Optional[List[str]]] = ["Chain Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create(self, **kwargs: Any) -> Any:
        """Chains owned by the requester (ROOT and SYSTEM may name another)."""
        return super().create(**_each(kwargs, _owned_by(self.requester.id)))

    def update(self, id: str, **kwargs: Any) -> Any:
        """A chain's owner and team are not changed by an update."""
        kwargs.pop("user_id", None)
        kwargs.pop("team_id", None)
        return super().update(id, **kwargs)

    async def run(self, chain_id: str, inputs: Optional[Dict[str, Any]]) -> Any:
        """Run the chain now with ``inputs`` as its starting variables; the
        run, finished (succeeded, failed or cancelled). Only someone who may
        edit the chain runs it; it runs as the chain's owner."""
        from zephyrex.extensions.ai_chains.ChainEngine import ChainEngine

        runs = _as(ChainRunManager, self)
        run = runs.create(chain_id=chain_id, inputs=check_inputs(inputs))
        await ChainEngine(
            model_registry=self.model_registry, requester_id=self.requester.id
        ).run(run.id)
        return runs.get(id=run.id)

    @custom_route(
        method="POST",
        path="/{chain_id}/run",
        input_model=RunRequest,
        output_model=ChainRunModel,
        authentication_type="jwt",
        openapi_tags=("Chain Management",),
        summary="Run the chain now",
        expose_in=(ExposeIn.REST,),
    )
    async def run_route(self, chain_id: str, body: RunRequest) -> Any:
        return await self.run(chain_id, body.inputs)


class ChainStepModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    ChainModel.Reference,
    PromptModel.Reference.Optional,
    AbilityModel.Reference.Optional,
    metaclass=ModelMeta,
):
    name: str = Field(..., description="The step's name, an identifier")
    position: int = Field(0, description="Where the step runs, lowest first")
    kind: StepKind = Field(..., description="prompt | ability | condition | set")
    arguments: Optional[Dict[str, str]] = Field(
        None,
        description="Expressions over the run's variables: an ability's "
        "arguments, or a prompt's {VARIABLE}s",
    )
    expression: Optional[str] = Field(
        None, description="A condition's test, or a set step's value"
    )
    variable: Optional[str] = Field(
        None, description="The variable the step's output goes to"
    )
    on_true: Optional[str] = Field(
        None, description="The step a true condition goes to (none: the next)"
    )
    on_false: Optional[str] = Field(
        None, description="The step a false condition goes to (none: the next)"
    )
    max_loops: Optional[int] = Field(
        None, description="The most times a condition's jump back may be taken"
    )

    table_comment: ClassVar[str] = (
        "A ChainStep is one step of a Chain: a prompt, an ability, a "
        "condition or a variable set, run in position order."
    )
    is_system_entity: ClassVar[bool] = False
    permission_references: ClassVar[List[str]] = ["chain"]

    class Create(
        BaseModel,
        ChainModel.Reference.ID,
        PromptModel.Reference.ID.Optional,
        AbilityModel.Reference.ID.Optional,
    ):
        name: str = Field(..., max_length=64)
        position: int = Field(0, ge=0, le=1_000_000)
        kind: StepKind
        arguments: Optional[Dict[str, str]] = None
        expression: Optional[str] = Field(None, max_length=2_000)
        variable: Optional[str] = Field(None, max_length=64)
        on_true: Optional[str] = Field(None, max_length=64)
        on_false: Optional[str] = Field(None, max_length=64)
        max_loops: Optional[int] = Field(None, ge=1, le=MAX_LOOPS)

    class Update(
        BaseModel,
        PromptModel.Reference.ID.Optional,
        AbilityModel.Reference.ID.Optional,
    ):
        name: Optional[str] = Field(None, max_length=64)
        position: Optional[int] = Field(None, ge=0, le=1_000_000)
        kind: Optional[StepKind] = None
        arguments: Optional[Dict[str, str]] = None
        expression: Optional[str] = Field(None, max_length=2_000)
        variable: Optional[str] = Field(None, max_length=64)
        on_true: Optional[str] = Field(None, max_length=64)
        on_false: Optional[str] = Field(None, max_length=64)
        max_loops: Optional[int] = Field(None, ge=1, le=MAX_LOOPS)

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ChainModel.Reference.ID.Search,
        PromptModel.Reference.ID.Search,
        AbilityModel.Reference.ID.Search,
    ):
        name: Optional[StringSearchModel] = None
        kind: Optional[StringSearchModel] = None


class ChainStepManager(AbstractBLLManager, RouterMixin):
    _model = ChainStepModel

    prefix: ClassVar[Optional[str]] = "/v1/chain-step"
    tags: ClassVar[Optional[List[str]]] = ["Chain Step Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def ordered(self, chain_id: str) -> List[Any]:
        """The chain's steps in the order they run."""
        steps = self.list(chain_id=chain_id)
        return sorted(steps, key=lambda s: (s.position, str(s.created_at), s.id))

    def _references(self, step: Dict[str, Any]) -> None:
        """The step's prompt and ability, read as the requester; an ability
        no chain may call is refused."""
        if step.get("kind") == "prompt":
            _visible(_as(PromptManager, self), step["prompt_id"], "Prompt not found")
        if step.get("kind") == "ability":
            ability = _visible(
                _as(AbilityManager, self), step["ability_id"], "Ability not found"
            )
            if ability.name in NEVER_CHAIN_INVOCABLE:
                raise HTTPException(
                    status_code=403, detail=f"No chain may call {ability.name!r}"
                )

    def _unique_name(self, chain_id: str, name: str, step_id: Optional[str]) -> None:
        clash = [s for s in self.list(chain_id=chain_id, name=name) if s.id != step_id]
        if clash:
            raise HTTPException(
                status_code=409, detail=f"The chain already has a step {name!r}"
            )

    def create_validation(self, entity: Any) -> None:
        step = entity.model_dump()
        _visible(_as(ChainManager, self), entity.chain_id, "Chain not found")
        check_step(step)
        self._references(step)
        self._unique_name(entity.chain_id, entity.name, None)
        if len(self.list(chain_id=entity.chain_id)) >= MAX_STEPS_PER_CHAIN:
            raise _invalid(f"a chain has at most {MAX_STEPS_PER_CHAIN} steps")

    def update(self, id: str, **kwargs: Any) -> Any:
        """A step stays in its chain; the step as changed is checked as a
        new one would be."""
        kwargs.pop("chain_id", None)
        try:
            changes = (
                self.model_registry.apply(self.Model)
                .Update(**kwargs)
                .model_dump(exclude_unset=True)
            )
        except ValidationError as invalid:
            raise HTTPException(status_code=422, detail=invalid.errors()) from None
        current = self.get(id=id)
        merged = {**current.model_dump(), **changes}
        check_step(merged)
        if {"kind", "prompt_id", "ability_id"} & changes.keys():
            self._references(merged)
        if "name" in changes:
            self._unique_name(current.chain_id, merged["name"], id)
        return super().update(id, **kwargs)


class CancelRequest(RouteModel):
    reason: Optional[str] = Field(
        None, max_length=500, description="Why the run is stopped"
    )


class ChainRunManager(AbstractBLLManager, RouterMixin):
    _model = ChainRunModel

    prefix: ClassVar[Optional[str]] = "/v1/chain-run"
    tags: ClassVar[Optional[List[str]]] = ["Chain Run Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
    ]

    def create_validation(self, entity: Any) -> None:
        _visible(_as(ChainManager, self), entity.chain_id, "Chain not found")

    def create(self, **kwargs: Any) -> Any:
        """Runs started by the requester; every run starts pending."""

        def prepare(fields: Dict[str, Any]) -> Dict[str, Any]:
            fields = _owned_by(self.requester.id)(fields)
            fields["status"] = PENDING
            fields["inputs"] = check_inputs(fields.get("inputs"))
            return fields

        return super().create(**_each(kwargs, prepare))

    def update(self, id: str, **kwargs: Any) -> Any:
        """A run's lifecycle is the engine's (ROOT) to record; a user may
        only ask it to stop."""
        if not _server_side(self.requester.id):
            for field in RUN_LIFECYCLE:
                kwargs.pop(field, None)
            if kwargs.get("cancel_requested") is not True:
                kwargs.pop("cancel_requested", None)
        return super().update(id, **kwargs)

    def cancel(self, run_id: str, reason: Optional[str] = None) -> Any:
        """Ask the run to stop: a pending run is cancelled at once, a running
        one before its next step (or while its current step is awaited)."""
        run = self.update(run_id, cancel_requested=True)
        if run.status == PENDING:
            ChainRunManager(
                requester_id=env("ROOT_ID"), model_registry=self.model_registry
            ).update(
                run_id,
                status=CANCELLED,
                error=reason or "cancelled before it started",
                error_kind=ChainCancelledError.kind,
            )
        return self.get(id=run_id)

    @custom_route(
        method="POST",
        path="/{run_id}/cancel",
        input_model=CancelRequest,
        output_model=ChainRunModel,
        authentication_type="jwt",
        openapi_tags=("Chain Run Management",),
        summary="Stop a run",
        expose_in=(ExposeIn.REST,),
    )
    def cancel_route(self, run_id: str, body: CancelRequest) -> Any:
        return self.cancel(run_id, body.reason)


class ChainStepResultModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    ChainRunModel.Reference,
    ChainStepModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """One step a run executed: its input, output, status and timing. A
    step that ran several times (in a loop) has a result for each time."""

    step_name: str = Field(..., description="The step's name when it ran")
    kind: str = Field(..., description="The step's kind when it ran")
    sequence: int = Field(..., description="Its place in the run, from 1")
    status: str = Field(..., description="succeeded | failed | cancelled")
    input: Optional[str] = Field(None, description="What the step was given, as JSON")
    output: Optional[str] = Field(None, description="What it produced, as JSON")
    error: Optional[str] = Field(None, description="Why it failed")
    started_at: Optional[datetime] = Field(None, description="When it began")
    completed_at: Optional[datetime] = Field(None, description="When it ended")
    duration_ms: Optional[int] = Field(None, description="How long it took")

    table_comment: ClassVar[str] = (
        "A ChainStepResult records one step a ChainRun executed: its input, "
        "output, status and timing."
    )
    is_system_entity: ClassVar[bool] = False
    permission_references: ClassVar[List[str]] = ["chain_run"]

    class Create(
        BaseModel, ChainRunModel.Reference.ID, ChainStepModel.Reference.ID.Optional
    ):
        step_name: str
        kind: str
        sequence: int
        status: str
        input: Optional[str] = None
        output: Optional[str] = None
        error: Optional[str] = None
        started_at: Optional[datetime] = None
        completed_at: Optional[datetime] = None
        duration_ms: Optional[int] = None

    class Update(BaseModel):
        pass

    class Search(
        ApplicationModel.Search,
        ChainRunModel.Reference.ID.Search,
        ChainStepModel.Reference.ID.Search,
    ):
        step_name: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None


class ChainStepResultManager(AbstractBLLManager, RouterMixin):
    _model = ChainStepResultModel

    prefix: ClassVar[Optional[str]] = "/v1/chain-step-result"
    tags: ClassVar[Optional[List[str]]] = ["Chain Run Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
    ]

    def create(self, **kwargs: Any) -> Any:
        """Step results are the engine's to record, as ROOT: only ROOT and
        SYSTEM create them. A result inherits access from its run alone, so
        whoever may see the run sees what ROOT recorded, and none of them
        may change it."""
        if not _server_side(self.requester.id):
            raise HTTPException(
                status_code=403, detail="A run's step results are the server's"
            )
        return super().create(**kwargs)

    def of(self, run_id: str) -> List[Any]:
        """The run's step results in the order they ran."""
        return sorted(self.list(chain_run_id=run_id), key=lambda r: r.sequence)
