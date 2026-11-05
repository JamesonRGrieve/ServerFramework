# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stored prompts with {VARIABLE} placeholders and their arguments (see
BLL_AI_Prompts). Abilities act for the user named by ``requester_id``,
under that user's permissions."""

from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import (
    MAX_PROMPT_CHARACTERS,
    PromptManager,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency


def _row(model: Any) -> Dict[str, Any]:
    dumped: Dict[str, Any] = model.model_dump(mode="json")
    return dumped


class EXT_AI_Prompts(AbstractStaticExtension):
    name: ClassVar[str] = "ai_prompts"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Stored prompts with {VARIABLE} placeholders, their arguments and defaults"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="conversations",
                friendly_name="Conversations",
                optional=True,
                reason="Messages record the prompt they came from",
            )
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "list_prompts",
        "get_prompt",
        "build_prompt",
        "save_prompt",
    }

    @classmethod
    def prompts(cls, requester_id: str) -> PromptManager:
        manager: PromptManager = cls.as_requester(PromptManager, requester_id)
        return manager

    @classmethod
    @ability("list_prompts")
    async def list_prompts(
        cls, requester_id: str, favourites_only: bool = False
    ) -> List[Dict[str, Any]]:
        """The prompts the user can see: id, name, description, favourite."""
        prompts = cls.prompts(requester_id)
        found = prompts.list(favourite=True) if favourites_only else prompts.list()
        return [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "favourite": p.favourite,
            }
            for p in found
        ]

    @classmethod
    @ability("get_prompt")
    async def get_prompt(cls, requester_id: str, prompt_id: str) -> Dict[str, Any]:
        """A prompt with its arguments and their defaults."""
        prompts = cls.prompts(requester_id)
        return {
            **_row(prompts.get(id=prompt_id)),
            "arguments": prompts.defaults(prompt_id),
        }

    @classmethod
    @ability("build_prompt")
    async def build_prompt(
        cls,
        requester_id: str,
        prompt_id: str,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """The prompt with its variables filled: ``{text, missing}``."""
        values = variables or {}
        if not isinstance(values, dict):
            raise InvalidInputExternalError("variables is an object")
        return cls.prompts(requester_id).build(
            prompt_id, {str(k): str(v) for k, v in values.items()}
        )

    @classmethod
    @ability("save_prompt")
    async def save_prompt(
        cls,
        requester_id: str,
        name: str,
        content: str,
        description: Optional[str] = None,
        prompt_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a prompt (or, given ``prompt_id``, update one) and add an
        argument for each new variable its content uses."""
        if not name or not name.strip():
            raise InvalidInputExternalError("a prompt has a name")
        if not content or len(content) > MAX_PROMPT_CHARACTERS:
            raise InvalidInputExternalError(
                f"a prompt's content is 1-{MAX_PROMPT_CHARACTERS} characters"
            )
        prompts = cls.prompts(requester_id)
        fields = {"name": name, "content": content, "description": description or ""}
        if prompt_id:
            saved = prompts.update(prompt_id, **fields)
        else:
            saved = prompts.create(**fields)
        added = prompts.sync_arguments(saved.id)
        return {**_row(saved), "arguments_added": added}
