# SPDX-License-Identifier: AGPL-3.0-or-later
"""A causal language model (transformers ``AutoModelForCausalLM``): chat
through its tokenizer's chat template (rendered sandboxed), and raw text
completion."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from zephyrex.extensions.ai.EXT_AI import CHAT, chat_answer, flatten_messages
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.local_ai.LocalAI import (
    TEXT_COMPLETION,
    checked_temperature,
    completion_answer,
)
from zephyrex.extensions.local_ai_torch.Torch import PRETRAINED, AbstractTorchProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


@dataclass(frozen=True)
class TextModel:
    model: Any
    tokenizer: Any


@dataclass(frozen=True)
class Generated:
    text: str
    finish_reason: str
    input_tokens: int
    output_tokens: int


def end_of_text_ids(model: Any, tokenizer: Any) -> Set[int]:
    found = getattr(model.generation_config, "eos_token_id", None)
    ids = set(found if isinstance(found, list) else [found])
    ids.add(tokenizer.eos_token_id)
    return {i for i in ids if isinstance(i, int)}


def generate(
    text_model: TextModel,
    prompt: str,
    max_new_tokens: int,
    temperature: Optional[float],
    seconds: float,
    templated: bool,
) -> Generated:
    """``prompt`` continued by at most ``max_new_tokens`` tokens, for at
    most ``seconds``; greedy at temperature 0, the model's own sampling
    defaults when no temperature is given. A ``templated`` prompt already
    carries its special tokens."""
    model, tokenizer = text_model.model, text_model.tokenizer
    batch = tokenizer(prompt, return_tensors="pt", add_special_tokens=not templated)
    prompt_length = int(batch["input_ids"].shape[1])
    window = getattr(model.config, "max_position_embeddings", None)
    if isinstance(window, int) and prompt_length >= window:
        raise InvalidInputExternalError(
            f"the prompt is {prompt_length} tokens; the model reads at most {window}"
        )
    sampling: Dict[str, Any] = {}
    if temperature == 0:
        sampling = {"do_sample": False}
    elif temperature is not None:
        sampling = {"do_sample": True, "temperature": temperature}
    pad = tokenizer.pad_token_id
    with torch.inference_mode():
        output = model.generate(
            **batch.to(model.device),
            max_new_tokens=max_new_tokens,
            max_time=seconds,
            pad_token_id=pad if pad is not None else tokenizer.eos_token_id,
            **sampling,
        )
    new = output[0][prompt_length:].tolist()
    ended = bool(new) and new[-1] in end_of_text_ids(model, tokenizer)
    return Generated(
        tokenizer.decode(new, skip_special_tokens=True),
        "stop" if ended else "length",
        prompt_length,
        len(new),
    )


class PRV_Torch_TextGeneration(AbstractTorchProvider):
    name: ClassVar[str] = "torch_text_generation"
    friendly_name: ClassVar[str] = "PyTorch text model"
    description: ClassVar[str] = "Chat and text completion with a transformers model"
    _abilities: ClassVar[Set[str]] = {CHAT, TEXT_COMPLETION}

    @classmethod
    def load_model(cls, instance: ProviderInstanceModel, directory: Path) -> Any:
        model = cls.pretrained(AutoModelForCausalLM, instance, directory)
        return TextModel(model, AutoTokenizer.from_pretrained(directory, **PRETRAINED))

    @classmethod
    def text_model(cls, value: Any) -> TextModel:
        if not isinstance(value, TextModel):
            raise TypeError(f"{cls.name} holds a {type(value).__name__}, not a model")
        return value

    @classmethod
    def bounds(
        cls,
        instance: ProviderInstanceModel,
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> Tuple[int, Optional[float], float]:
        return (
            cls.max_tokens(instance, max_tokens),
            checked_temperature(temperature),
            cls.timeout_seconds(instance),
        )

    @classmethod
    def chat_prompt(cls, tokenizer: Any, turns: List[Dict[str, str]]) -> str:
        """The conversation as the model was trained to read it, or as a
        labelled transcript when its tokenizer has no chat template."""
        if tokenizer.chat_template:
            prompt = tokenizer.apply_chat_template(
                turns, add_generation_prompt=True, tokenize=False
            )
            return str(prompt)
        return f"{flatten_messages(turns)}\nAssistant:"

    @classmethod
    async def complete(
        cls,
        instance: ProviderInstanceModel,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> Dict[str, Any]:
        turns = cls.plain_messages(messages, tools)
        limit, chosen, seconds = cls.bounds(instance, max_tokens, temperature)
        model = cls.model(instance)

        def work(value: Any) -> Dict[str, Any]:
            text_model = cls.text_model(value)
            prompt = cls.chat_prompt(text_model.tokenizer, turns)
            found = generate(text_model, prompt, limit, chosen, seconds, True)
            return chat_answer(
                found.text.strip(),
                None,
                found.finish_reason,
                model,
                found.input_tokens,
                found.output_tokens,
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
        limit, chosen, seconds = cls.bounds(instance, max_tokens, temperature)
        model = cls.model(instance)

        def work(value: Any) -> Dict[str, Any]:
            found = generate(
                cls.text_model(value), prompt, limit, chosen, seconds, False
            )
            return completion_answer(
                found.text,
                found.finish_reason,
                model,
                found.input_tokens,
                found.output_tokens,
            )

        return await cls.run(instance, work)
