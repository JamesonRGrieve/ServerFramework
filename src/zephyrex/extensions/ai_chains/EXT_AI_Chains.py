# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chains: owned, ordered steps (prompts, abilities, conditions and
variables) that a run executes within its bounds, as the chain's owner (see
BLL_AI_Chains and ChainEngine).

Abilities act for the user named by ``requester_id``, under that user's
permissions."""

from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.ai_chains.BLL_AI_Chains import (
    ChainManager,
    ChainRunManager,
    ChainStepResultManager,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency


def _row(model: Any) -> Dict[str, Any]:
    dumped: Dict[str, Any] = model.model_dump(mode="json")
    return dumped


class EXT_AI_Chains(AbstractStaticExtension):
    name: ClassVar[str] = "ai_chains"
    friendly_name: ClassVar[str] = "AI Chains"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Chains of prompts, abilities, conditions and variables that run "
        "within their bounds as their owner"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="ai",
                friendly_name="AI",
                reason="A prompt step is answered by the AI extension's chat models",
            ),
            EXT_Dependency(
                name="ai_prompts",
                friendly_name="Prompts",
                reason="A prompt step fills a stored prompt",
            ),
            EXT_Dependency(
                name="ai_agents",
                friendly_name="AI Agents",
                reason="An ability step runs through the agents' ability invoker",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "run_chain",
        "list_chains",
        "get_chain_run",
        "cancel_chain_run",
    }

    @classmethod
    def chains(cls, requester_id: str) -> ChainManager:
        manager: ChainManager = cls.as_requester(ChainManager, requester_id)
        return manager

    @classmethod
    def runs(cls, requester_id: str) -> ChainRunManager:
        manager: ChainRunManager = cls.as_requester(ChainRunManager, requester_id)
        return manager

    @classmethod
    @ability("run_chain")
    async def run_chain(
        cls,
        requester_id: str,
        chain_id: str,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run a chain now with ``inputs`` as its starting variables; the
        finished run (succeeded, failed or cancelled)."""
        if inputs is not None and not isinstance(inputs, dict):
            raise InvalidInputExternalError("inputs is an object of variables")
        return _row(await cls.chains(requester_id).run(chain_id, inputs))

    @classmethod
    @ability("list_chains")
    async def list_chains(
        cls, requester_id: str, favourites_only: bool = False
    ) -> List[Dict[str, Any]]:
        """The chains the user can see: id, name, description, favourite."""
        chains = cls.chains(requester_id)
        found = chains.list(favourite=True) if favourites_only else chains.list()
        return [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "favourite": c.favourite,
            }
            for c in found
        ]

    @classmethod
    @ability("get_chain_run")
    async def get_chain_run(cls, requester_id: str, run_id: str) -> Dict[str, Any]:
        """A run with the steps it executed, in order."""
        run = cls.runs(requester_id).get(id=run_id)
        results: ChainStepResultManager = cls.as_requester(
            ChainStepResultManager, requester_id
        )
        return {**_row(run), "steps": [_row(r) for r in results.of(run.id)]}

    @classmethod
    @ability("cancel_chain_run")
    async def cancel_chain_run(
        cls, requester_id: str, run_id: str, reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Stop a run: at once if it has not started, else before its next
        step."""
        return _row(cls.runs(requester_id).cancel(run_id, reason))
