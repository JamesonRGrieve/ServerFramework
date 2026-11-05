# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run a chain: its steps in order, within its bounds, as its owner.

1. The run's variables start as its inputs. Steps run in position order; a
   condition may jump to a named step (or ``end``), and a jump back is a
   loop taken at most its ``max_loops`` times.
2. A run executes at most the chain's ``max_steps`` steps, and must finish
   before its deadline (``timeout_seconds`` from its start); one step's
   output (as JSON) is at most ``max_output_characters``, and all the
   variables together at most ``MAX_VARIABLES_CHARACTERS``.
3. Each step executed is recorded as a ChainStepResult: its input, output,
   status and timing. A failed step fails the run with its error recorded.
4. A run asked to stop (``cancel_requested``) stops before its next step,
   or while its current step is awaited.

The run acts as the chain's owner: prompts are read and models called for
them, and abilities run under their permissions, through the same invoker
agents use (the requester is filled in by the invoker, never by the chain).
The run's lifecycle is the server's bookkeeping, written as ROOT once the
requester has been shown to see the run; its step results are written as
the requester who started it (no user may create one through the manager),
since a row ROOT writes is ROOT's alone.

The model transport (``chat``) defaults to the AI extension's ``chat``; a
caller may hand in another implementing the same contract.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from zephyrex.extensions.ai_agents.AbilityInvoker import (
    AbilityAccessDenied,
    AbilityInvoker,
    ToolInvocationError,
)
from zephyrex.extensions.ai_agents.BLL_AI_Agents import AbilityGrant
from zephyrex.extensions.ai_chains.BLL_AI_Chains import (
    CANCELLED,
    END,
    FAILED,
    MAX_VARIABLES_CHARACTERS,
    NEVER_CHAIN_INVOCABLE,
    PENDING,
    RUNNING,
    SUCCEEDED,
    ChainCancelledError,
    ChainDefinitionError,
    ChainError,
    ChainLimitError,
    ChainManager,
    ChainRunManager,
    ChainStepError,
    ChainStepManager,
    ChainStepResultManager,
    ChainTimeoutError,
    check_step,
)
from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptManager, variables_in
from zephyrex.extensions.ExternalErrors import BaseExternalError
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.SafeExpression import ExpressionError, evaluate
from zephyrex.logic.BLL_Extensions import AbilityManager, ExtensionManager

# The model transport: (messages, requester_id) -> a chat answer
# ({"message": {"role", "content", "tool_calls"}, ...}). Failures raise
# (typed external errors).
ChatTransport = Callable[[List[Dict[str, Any]], str], Awaitable[Dict[str, Any]]]

# How often a running step looks for a request to stop.
CANCEL_POLL_SECONDS = 0.5
TRUNCATED_INPUT_PREVIEW = 1_000


async def ai_chat(messages: List[Dict[str, Any]], requester_id: str) -> Dict[str, Any]:
    """One chat turn through the AI extension, its tokens recorded against
    ``requester_id``."""
    from zephyrex.extensions.ai.EXT_AI import EXT_AI

    answer: Dict[str, Any] = await EXT_AI.chat(messages, requester_id=requester_id)
    return answer


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _error_text(error: BaseException) -> str:
    if isinstance(error, HTTPException):
        return str(error.detail)
    return str(error) or type(error).__name__


@dataclass
class StepOutcome:
    """What a step did: what it was given, what it made, where to go next
    (an index into the steps; None: the next one), and whether its output
    is the run's latest."""

    input: Dict[str, Any]
    output: Any = None
    jump_to: Optional[int] = None
    produced: bool = True


@dataclass
class RunState:
    """A run in progress."""

    run: Any
    chain: Any
    acting: str
    steps: List[Any]
    index_of: Dict[str, int]
    variables: Dict[str, Any]
    deadline: float
    executed: int = 0
    loops: Dict[str, int] = field(default_factory=dict)
    output: Any = None
    produced: bool = False


class ChainEngine:
    """Runs chains as ``requester_id``, who must see the run."""

    def __init__(
        self,
        model_registry: Any,
        requester_id: str,
        chat: Optional[ChatTransport] = None,
    ) -> None:
        self.model_registry = model_registry
        self.requester_id = requester_id
        self.chat = chat or ai_chat

    def _as(self, manager_class: Any, requester_id: str) -> Any:
        return manager_class(
            requester_id=requester_id, model_registry=self.model_registry
        )

    def _bookkeeping(self, manager_class: Any) -> Any:
        return self._as(manager_class, env("ROOT_ID"))

    async def run(self, run_id: str) -> Dict[str, Any]:
        """Execute the run and return a summary. A run that fails or is
        cancelled is recorded on its row (status, error, error_kind) and
        returned, never raised. A run executes once: one no longer pending
        is refused (409)."""
        run = self._as(ChainRunManager, self.requester_id).get(id=run_id)
        if run.status != PENDING:
            raise HTTPException(
                status_code=409, detail=f"The run is {run.status}, not pending"
            )
        chain = self._as(ChainManager, self.requester_id).get(id=run.chain_id)
        acting = chain.user_id or self.requester_id
        lifecycle = self._bookkeeping(ChainRunManager)
        lifecycle.update(run.id, status=RUNNING, started_at=_now())
        state = RunState(
            run=run,
            chain=chain,
            acting=acting,
            steps=[],
            index_of={},
            variables=dict(run.inputs or {}),
            deadline=time.monotonic() + chain.timeout_seconds,
        )
        try:
            self._load_steps(state)
            await self._execute(state)
        except ChainError as stopped:
            return self._finish(state, stopped)
        except Exception:  # the task-runner boundary: record, never propagate
            logger.exception("Chain run %s failed", run.id)
            failure = ChainError("internal error")
            return self._finish(state, failure)
        return self._finish(state, None)

    def _finish(self, state: RunState, stopped: Optional[ChainError]) -> Dict[str, Any]:
        status = SUCCEEDED
        if stopped is not None:
            status = CANCELLED if isinstance(stopped, ChainCancelledError) else FAILED
            logger.info("Chain run %s %s: %s", state.run.id, status, stopped)
        self._bookkeeping(ChainRunManager).update(
            state.run.id,
            status=status,
            error=None if stopped is None else str(stopped),
            error_kind=None if stopped is None else stopped.kind,
            variables=jsonable_encoder(state.variables),
            output=(
                json.dumps(jsonable_encoder(state.output), default=str)
                if state.produced
                else None
            ),
            steps_executed=state.executed,
            completed_at=_now(),
        )
        summary: Dict[str, Any] = {
            "run_id": state.run.id,
            "status": status,
            "steps_executed": state.executed,
        }
        if stopped is not None:
            summary.update(error=str(stopped), error_kind=stopped.kind)
        return summary

    def _load_steps(self, state: RunState) -> None:
        """The chain's steps in order, checked as a whole: names unique,
        every jump to a step that exists, and every loop bounded."""
        steps = self._as(ChainStepManager, state.acting).ordered(state.chain.id)
        for index, step in enumerate(steps):
            if step.name in state.index_of:
                raise ChainDefinitionError(f"two steps are named {step.name!r}")
            state.index_of[step.name] = index
        for index, step in enumerate(steps):
            try:
                check_step(step.model_dump())
            except HTTPException as invalid:
                raise ChainDefinitionError(
                    f"step {step.name!r}: {invalid.detail}"
                ) from None
            for target in (step.on_true, step.on_false):
                if target is None or target == END:
                    continue
                if target not in state.index_of:
                    raise ChainDefinitionError(
                        f"step {step.name!r} jumps to {target!r}, which is no step"
                    )
                if state.index_of[target] <= index and not step.max_loops:
                    raise ChainDefinitionError(
                        f"step {step.name!r} loops back to {target!r} "
                        "without max_loops"
                    )
        state.steps = steps

    def _check_cancelled(self, state: RunState) -> None:
        if self._bookkeeping(ChainRunManager).get(id=state.run.id).cancel_requested:
            raise ChainCancelledError("cancelled")

    def _remaining(self, state: RunState) -> float:
        remaining = state.deadline - time.monotonic()
        if remaining <= 0:
            raise ChainTimeoutError(
                f"the run passed its {state.chain.timeout_seconds}s deadline"
            )
        return remaining

    async def _execute(self, state: RunState) -> None:
        position = 0
        while position < len(state.steps):
            self._check_cancelled(state)
            if state.executed >= state.chain.max_steps:
                raise ChainLimitError(
                    f"the run reached its limit of {state.chain.max_steps} steps"
                )
            remaining = self._remaining(state)
            step = state.steps[position]
            state.executed += 1
            started, clock = _now(), time.monotonic()
            try:
                outcome = await self._await_step(state, step, remaining)
                self._keep(state, step, outcome)
            except ChainError as failed:
                self._record(state, step, started, clock, None, failed)
                raise
            self._record(state, step, started, clock, outcome, None)
            position = self._next(state, step, position, outcome)

    async def _await_step(self, state: RunState, step: Any, remaining: float) -> Any:
        """The step's outcome, awaited until it finishes, the deadline
        passes, or the run is asked to stop."""
        work = asyncio.ensure_future(self._perform(state, step))
        watch = asyncio.ensure_future(self._watch_for_cancel(state))
        try:
            done, _ = await asyncio.wait(
                {work, watch}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (work, watch):
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, watch, return_exceptions=True)
        if work in done:
            return work.result()
        if watch in done:
            raise ChainCancelledError(f"cancelled during step {step.name!r}")
        raise ChainTimeoutError(
            f"the run passed its {state.chain.timeout_seconds}s deadline "
            f"during step {step.name!r}"
        )

    async def _watch_for_cancel(self, state: RunState) -> None:
        runs = self._bookkeeping(ChainRunManager)
        while not runs.get(id=state.run.id).cancel_requested:
            await asyncio.sleep(CANCEL_POLL_SECONDS)

    async def _perform(self, state: RunState, step: Any) -> StepOutcome:
        try:
            if step.kind == "prompt":
                return await self._prompt(state, step)
            if step.kind == "ability":
                return await self._ability(state, step)
            if step.kind == "condition":
                return self._condition(state, step)
            return self._set(state, step)
        except ExpressionError as error:
            raise ChainStepError(f"step {step.name!r}: {error}") from None
        except (
            BaseExternalError,
            HTTPException,
            ToolInvocationError,
            AbilityAccessDenied,
        ) as error:
            raise ChainStepError(f"step {step.name!r}: {_error_text(error)}") from None

    def _arguments(self, state: RunState, step: Any) -> Dict[str, Any]:
        return {
            name: evaluate(expression, state.variables)
            for name, expression in (step.arguments or {}).items()
        }

    async def _prompt(self, state: RunState, step: Any) -> StepOutcome:
        """The prompt, read as the owner and filled from the run's variables
        of the same names, then the step's arguments; its answer's text."""
        prompts = self._as(PromptManager, state.acting)
        prompt = prompts.get(id=step.prompt_id)
        wanted = set(variables_in(prompt.content))
        values = {
            name: _as_text(value)
            for name, value in state.variables.items()
            if name in wanted
        }
        values.update({k: _as_text(v) for k, v in self._arguments(state, step).items()})
        built = prompts.build(step.prompt_id, values)
        if built["missing"]:
            raise ChainStepError(
                f"step {step.name!r}: the prompt's "
                f"{', '.join(built['missing'])} have no value"
            )
        messages = [{"role": "user", "content": built["text"]}]
        answer = await self.chat(messages, state.acting)
        content = answer.get("message", {}).get("content") or ""
        return StepOutcome(
            input={"prompt_id": step.prompt_id, "text": built["text"]},
            output=content,
        )

    def _grant(self, state: RunState, step: Any) -> AbilityGrant:
        """The step's ability as a grant the invoker runs, read as the
        owner."""
        ability = self._as(AbilityManager, state.acting).get(id=step.ability_id)
        extension = self._as(ExtensionManager, state.acting).get(
            id=ability.extension_id
        )
        return AbilityGrant(
            ability_id=str(step.ability_id), name=ability.name, extension=extension.name
        )

    async def _ability(self, state: RunState, step: Any) -> StepOutcome:
        """The ability called with the step's arguments, as the owner."""
        grant = self._grant(state, step)
        arguments = self._arguments(state, step)
        invoker = AbilityInvoker(
            model_registry=self.model_registry,
            requester_id=state.acting,
            never_invocable=NEVER_CHAIN_INVOCABLE,
        )
        result = await invoker.invoke(grant.name, arguments, {grant.name: grant})
        return StepOutcome(
            input={
                "ability": f"{grant.extension}.{grant.name}",
                "arguments": arguments,
            },
            output=jsonable_encoder(result),
        )

    def _condition(self, state: RunState, step: Any) -> StepOutcome:
        value = bool(evaluate(step.expression, state.variables))
        target = step.on_true if value else step.on_false
        jump_to: Optional[int] = None
        if target == END:
            jump_to = len(state.steps)
        elif target is not None:
            jump_to = state.index_of[target]
        return StepOutcome(
            input={"expression": step.expression},
            output={"value": value, "next": target},
            jump_to=jump_to,
            produced=False,
        )

    def _set(self, state: RunState, step: Any) -> StepOutcome:
        return StepOutcome(
            input={"expression": step.expression},
            output=jsonable_encoder(evaluate(step.expression, state.variables)),
        )

    def _keep(self, state: RunState, step: Any, outcome: StepOutcome) -> None:
        """The step's output, kept within the chain's bounds: into its
        variable, and as the run's latest output."""
        size = len(_as_text(outcome.output))
        if size > state.chain.max_output_characters:
            raise ChainLimitError(
                f"step {step.name!r} produced {size} characters, more than the "
                f"chain's {state.chain.max_output_characters}"
            )
        if step.variable and outcome.produced:
            state.variables[step.variable] = outcome.output
            if len(_as_text(state.variables)) > MAX_VARIABLES_CHARACTERS:
                raise ChainLimitError(
                    f"the run's variables passed {MAX_VARIABLES_CHARACTERS} characters"
                )
        if outcome.produced:
            state.output, state.produced = outcome.output, True

    def _next(
        self, state: RunState, step: Any, position: int, outcome: StepOutcome
    ) -> int:
        if outcome.jump_to is None:
            return position + 1
        if outcome.jump_to <= position:
            taken = state.loops.get(step.name, 0) + 1
            if taken > (step.max_loops or 0):
                raise ChainLimitError(
                    f"step {step.name!r} looped back {step.max_loops} times, its bound"
                )
            state.loops[step.name] = taken
        return outcome.jump_to

    def _record(
        self,
        state: RunState,
        step: Any,
        started: datetime,
        clock: float,
        outcome: Optional[StepOutcome],
        failed: Optional[ChainError],
    ) -> None:
        """One ChainStepResult for the step just executed."""
        status = SUCCEEDED
        if failed is not None:
            status = CANCELLED if isinstance(failed, ChainCancelledError) else FAILED
        recorded_input, recorded_output = self._recorded(state, outcome)
        self._bookkeeping(ChainStepResultManager).create(
            chain_run_id=state.run.id,
            chain_step_id=step.id,
            step_name=step.name,
            kind=step.kind,
            sequence=state.executed,
            status=status,
            input=recorded_input,
            output=recorded_output,
            error=None if failed is None else str(failed),
            started_at=started,
            completed_at=_now(),
            duration_ms=int((time.monotonic() - clock) * 1000),
        )

    @staticmethod
    def _recorded(
        state: RunState, outcome: Optional[StepOutcome]
    ) -> Tuple[Optional[str], Optional[str]]:
        """A step's input and output as JSON for its result. An input over
        the output limit is recorded as a preview (the step had all of it);
        an output over it was refused, and is not recorded."""
        if outcome is None:
            return None, None
        limit = state.chain.max_output_characters
        recorded_input = json.dumps(jsonable_encoder(outcome.input), default=str)
        if len(recorded_input) > limit:
            recorded_input = json.dumps(
                {
                    "truncated": True,
                    "preview": recorded_input[: min(limit, TRUNCATED_INPUT_PREVIEW)],
                }
            )
        recorded_output = json.dumps(jsonable_encoder(outcome.output), default=str)
        if len(recorded_output) > limit:
            return recorded_input, None
        return recorded_input, recorded_output
