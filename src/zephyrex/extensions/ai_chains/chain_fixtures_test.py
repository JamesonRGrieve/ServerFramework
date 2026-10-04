# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the chain tests share: a scripted model and a way to build and run
chains against the app database.

The model is the one substituted boundary: ``ScriptedChat`` implements the
chat transport's contract (``(messages, requester_id) -> {"message": ...}``,
raising typed errors on failure, as a provider's would) and replays a script,
so the engine, the invoker, the gate and every write run as in production."""

import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_chains.BLL_AI_Chains import (
    ChainManager,
    ChainRunManager,
    ChainStepManager,
    ChainStepResultManager,
)
from zephyrex.extensions.ai_chains.ChainEngine import ChainEngine
from zephyrex.extensions.ai_chains.EXT_AI_Chains import EXT_AI_Chains
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Extensions import AbilityManager, ExtensionManager

Reply = Union[str, Exception, Callable[[], Awaitable[str]]]


def answer(text: Optional[str]) -> Dict[str, Any]:
    return {
        "message": {"role": "assistant", "content": text, "tool_calls": None},
        "finish_reason": "stop",
        "model": "scripted",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class ScriptedChat:
    """A chat transport replaying a script of replies: text, an exception
    (raised), or a coroutine function whose text is awaited (a slow model)."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.calls: List[Dict[str, Any]] = []

    async def __call__(
        self, messages: List[Dict[str, Any]], requester_id: str
    ) -> Dict[str, Any]:
        reply = self.replies[min(len(self.calls), len(self.replies) - 1)]
        self.calls.append({"messages": list(messages), "requester_id": requester_id})
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return answer(await reply())
        return answer(reply)


def ability_id(model_registry: Any, extension: str, name: str) -> str:
    """The id of ``extension``'s Ability row called ``name``, made as SYSTEM
    (as seeding makes it) where the registry has none."""
    system = env("SYSTEM_ID")
    extensions = ExtensionManager(requester_id=system, model_registry=model_registry)
    abilities = AbilityManager(requester_id=system, model_registry=model_registry)
    found = extensions.list(name=extension)
    extension_id = found[0].id if found else extensions.create(name=extension).id
    existing = abilities.list(name=name, extension_id=extension_id)
    if existing:
        return str(existing[0].id)
    return str(abilities.create(name=name, extension_id=extension_id).id)


def requester_of(user: Any) -> str:
    return user if isinstance(user, str) else str(user.id)


class ChainFixtures(ExtensionServerMixin):
    extension_class = EXT_AI_Chains

    def _m(self, manager_class: Any, user: Any, model_registry: Any) -> Any:
        return manager_class(
            requester_id=requester_of(user), model_registry=model_registry
        )

    def _chain(self, user: Any, model_registry: Any, **fields: Any) -> Any:
        return self._m(ChainManager, user, model_registry).create(
            name=f"Chain {uuid.uuid4()}", **fields
        )

    def _step(
        self,
        user: Any,
        model_registry: Any,
        chain: Any,
        name: str,
        kind: str,
        position: int,
        **fields: Any,
    ) -> Any:
        return self._m(ChainStepManager, user, model_registry).create(
            chain_id=chain.id, name=name, kind=kind, position=position, **fields
        )

    def _prompt(self, user: Any, model_registry: Any, content: str) -> Any:
        from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptManager

        return self._m(PromptManager, user, model_registry).create(
            name=f"Prompt {uuid.uuid4()}", description="", content=content
        )

    async def _run(
        self,
        user: Any,
        model_registry: Any,
        chain: Any,
        inputs: Optional[Dict[str, Any]] = None,
        chat: Optional[ScriptedChat] = None,
    ) -> Any:
        runs = self._m(ChainRunManager, user, model_registry)
        run = runs.create(chain_id=chain.id, inputs=inputs or {})
        await ChainEngine(
            model_registry=model_registry,
            requester_id=requester_of(user),
            chat=chat or ScriptedChat("unused"),
        ).run(run.id)
        return runs.get(id=run.id)

    def _results(self, user: Any, model_registry: Any, run: Any) -> List[Any]:
        found: List[Any] = self._m(ChainStepResultManager, user, model_registry).of(
            run.id
        )
        return found
