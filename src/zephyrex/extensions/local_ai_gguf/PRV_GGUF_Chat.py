# SPDX-License-Identifier: AGPL-3.0-or-later
"""A GGUF text model: chat (through the chat template the model file
carries, which llama.cpp renders sandboxed) and raw text completion."""

from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.ai.EXT_AI import CHAT, chat_answer
from zephyrex.extensions.local_ai.LocalAI import TEXT_COMPLETION, completion_answer
from zephyrex.extensions.local_ai_gguf.GGUF import (
    AbstractGGUFProvider,
    library_failure,
    llama_messages,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

STREAMED = "llama.cpp streamed an answer it was not asked to"


class PRV_GGUF_Chat(AbstractGGUFProvider):
    name: ClassVar[str] = "gguf_chat"
    friendly_name: ClassVar[str] = "GGUF text model"
    description: ClassVar[str] = "Chat and text completion with a GGUF model"
    _abilities: ClassVar[Set[str]] = {CHAT, TEXT_COMPLETION}

    @classmethod
    async def complete(
        cls,
        instance: ProviderInstanceModel,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> Dict[str, Any]:
        turns = llama_messages(cls.plain_messages(messages, tools))
        arguments, seconds = cls.generation(instance, max_tokens, temperature)
        model = cls.model(instance)

        def work(value: Any) -> Dict[str, Any]:
            llama = cls.llama(value)
            stop, processors = cls.stopper(llama, seconds)
            try:
                answer = llama.create_chat_completion(
                    messages=turns, logits_processor=processors, **arguments
                )
            except ValueError as exc:
                raise library_failure(exc) from exc
            if not isinstance(answer, dict):
                raise TypeError(STREAMED)
            choice = answer["choices"][0]
            return chat_answer(
                choice["message"].get("content"),
                None,
                cls.finish(choice.get("finish_reason"), stop),
                model,
                *cls.token_counts(answer.get("usage")),
            )

        return await cls.run(instance, work)

    @classmethod
    async def continue_text(
        cls,
        instance: ProviderInstanceModel,
        prompt: str,
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> Dict[str, Any]:
        arguments, seconds = cls.generation(instance, max_tokens, temperature)
        model = cls.model(instance)

        def work(value: Any) -> Dict[str, Any]:
            llama = cls.llama(value)
            stop, processors = cls.stopper(llama, seconds)
            try:
                answer = llama.create_completion(
                    prompt, logits_processor=processors, **arguments
                )
            except ValueError as exc:
                raise library_failure(exc) from exc
            if not isinstance(answer, dict):
                raise TypeError(STREAMED)
            choice = answer["choices"][0]
            return completion_answer(
                choice["text"],
                cls.finish(choice.get("finish_reason"), stop),
                model,
                *cls.token_counts(answer.get("usage")),
            )

        return await cls.run(instance, work)
