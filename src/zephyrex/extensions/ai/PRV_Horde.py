# SPDX-License-Identifier: AGPL-3.0-or-later
"""The AI Horde (aihorde.net): text generation by volunteer workers, as a
job. A chat is flattened into one prompt by an instruct template, submitted
to ``/v2/generate/text/async``, and polled at
``/v2/generate/text/status/{id}`` until it is done; its first generation is
the answer. A refused, faulted, impossible or timed-out job is transient, so
the rotation moves on (a timed-out job is cancelled first). It doesn't
stream: ``chat_stream`` answers whole.

An instance needs a model (no default): an unconfigured seeded instance is
passed over, so the root rotation never sends prompts to public workers by
itself. Anonymous use is the key ``0000000000``."""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import (
    CHAT,
    IMAGES,
    AbstractAIProvider,
    chat_answer,
)
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

ANONYMOUS_KEY = "0000000000"
DEFAULT_POLL_SECONDS = 2.0
# The status endpoint allows about one read a second per job.
MIN_POLL_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 300.0
# Request fields the Horde's text params take by these names.
HORDE_PARAMS = frozenset(
    {
        "max_context_length",
        "temperature",
        "top_p",
        "top_k",
        "top_a",
        "min_p",
        "typical",
        "tfs",
        "rep_pen",
        "rep_pen_range",
        "rep_pen_slope",
        "stop_sequence",
        "sampler_order",
        "singleline",
        "frmttriminc",
        "frmtrmblln",
        "dynatemp_range",
        "dynatemp_exponent",
        "smoothing_factor",
    }
)
# Instruct templates: (system, user, assistant turn prefixes and suffixes),
# the prompt ending on an open assistant turn.
TEMPLATES: Dict[str, Dict[str, Tuple[str, str]]] = {
    "chatml": {
        "system": ("<|im_start|>system\n", "<|im_end|>\n"),
        "user": ("<|im_start|>user\n", "<|im_end|>\n"),
        "assistant": ("<|im_start|>assistant\n", "<|im_end|>\n"),
    },
    "alpaca": {
        "system": ("", "\n\n"),
        "user": ("### Instruction:\n", "\n\n"),
        "assistant": ("### Response:\n", "\n\n"),
    },
    "llama3": {
        "system": ("<|start_header_id|>system<|end_header_id|>\n\n", "<|eot_id|>"),
        "user": ("<|start_header_id|>user<|end_header_id|>\n\n", "<|eot_id|>"),
        "assistant": (
            "<|start_header_id|>assistant<|end_header_id|>\n\n",
            "<|eot_id|>",
        ),
    },
    "plain": {
        "system": ("", "\n"),
        "user": ("User: ", "\n"),
        "assistant": ("Assistant: ", "\n"),
    },
}


def instruct_prompt(messages: List[Dict[str, Any]], template: str) -> str:
    """``messages`` as one prompt in ``template``, ending on an open
    assistant turn. Tool messages read as user turns (the Horde has no
    tools)."""
    turns = TEMPLATES.get(template) or TEMPLATES["chatml"]
    parts: List[str] = []
    for message in messages:
        role = message["role"] if message["role"] in turns else "user"
        content = message.get("content")
        if not content:
            continue
        prefix, suffix = turns[role]
        parts.append(f"{prefix}{content}{suffix}")
    parts.append(turns["assistant"][0])
    return "".join(parts)


def horde_params(
    max_tokens: Optional[int],
    temperature: Optional[float],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """The job's text params: the Horde's own names as given, ``stop`` as
    ``stop_sequence``; ``max_tokens`` as ``max_length`` (unset: the Horde's
    default). Fields it has no name for are dropped."""
    fields = {k: v for k, v in params.items() if k in HORDE_PARAMS}
    stop = params.get("stop")
    if stop is not None and "stop_sequence" not in fields:
        fields["stop_sequence"] = [stop] if isinstance(stop, str) else list(stop)
    if max_tokens is not None:
        fields["max_length"] = max_tokens
    if temperature is not None:
        fields["temperature"] = temperature
    return fields


class PRV_Horde_AI(AbstractAIProvider):
    name: ClassVar[str] = "horde"
    friendly_name: ClassVar[str] = "AI Horde"
    description: ClassVar[str] = "Text generation by the AI Horde's volunteer workers"
    _abilities: ClassVar[Set[str]] = {CHAT, IMAGES}
    _env: ClassVar[Dict[str, Any]] = {}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("base_url", "API address", default="https://aihorde.net/api"),
        InstanceSetting(
            "api_key",
            "API key (0000000000 for anonymous use)",
            secret=True,
            field="api_key",
            default=ANONYMOUS_KEY,
        ),
        InstanceSetting(
            "model",
            "Models, comma-separated (any worker serving one of them may answer)",
            field="model_name",
        ),
        InstanceSetting(
            "image_model",
            "Image models, comma-separated (any worker serving one may draw)",
        ),
        InstanceSetting(
            "allow_nsfw",
            "Images may be NSFW (true/false; false asks workers to censor)",
            default="false",
        ),
        InstanceSetting(
            "instruct_template",
            "How a chat becomes one prompt: chatml, alpaca, llama3 or plain",
            default="chatml",
        ),
        InstanceSetting(
            "poll_interval_seconds",
            "Seconds between status checks (at least 1: the Horde's limit)",
            default="2",
        ),
        InstanceSetting(
            "timeout_seconds",
            "Seconds to wait for a job, queue included, before giving up on it "
            "(queues can be long)",
            default="300",
        ),
        InstanceSetting(
            "trusted_workers",
            "Only workers the Horde trusts may answer (true/false)",
            default="false",
        ),
        InstanceSetting(
            "slow_workers",
            "Slow workers may answer too (true/false)",
            default="true",
        ),
        InstanceSetting(
            "client_agent",
            "Client-Agent header (name:version:contact)",
            default="zephyrex:0:",
        ),
    )

    @classmethod
    def _number(
        cls, instance: ProviderInstanceModel, key: str, default: float
    ) -> float:
        try:
            value = float(str(cls.setting(instance, key) or default))
        except ValueError:
            return default
        return value if value > 0 else default

    @classmethod
    def _flag(cls, instance: ProviderInstanceModel, key: str) -> bool:
        return str(cls.setting(instance, key) or "").strip().lower() == "true"

    @classmethod
    def models(cls, instance: ProviderInstanceModel, key: str = "model") -> List[str]:
        models = [m.strip() for m in str(cls.setting(instance, key) or "").split(",")]
        models = [m for m in models if m]
        if not models:
            raise TransientExternalError(
                f"AI Horde {key.replace('_', ' ')} not configured", provider=cls.name
            )
        return models

    @classmethod
    def headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        return {
            "apikey": str(cls.setting(instance, "api_key") or ANONYMOUS_KEY),
            "Client-Agent": str(cls.setting(instance, "client_agent") or "zephyrex:0:"),
        }

    @classmethod
    async def complete(
        cls,
        instance: ProviderInstanceModel,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        temperature: Optional[float],
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        models = cls.models(instance)
        base = str(cls.setting(instance, "base_url")).rstrip("/")
        headers = cls.headers(instance)
        prompt = instruct_prompt(
            messages, str(cls.setting(instance, "instruct_template") or "chatml")
        )
        job = await cls.http().post(
            f"{base}/v2/generate/text/async",
            json={
                "prompt": prompt,
                "params": horde_params(
                    max_tokens, temperature, cls.request_params(instance, params)
                ),
                "models": models,
                "trusted_workers": cls._flag(instance, "trusted_workers"),
                "slow_workers": cls._flag(instance, "slow_workers"),
            },
            headers=headers,
        )
        job_id = str((job or {}).get("id") or "")
        if not job_id:
            raise TransientExternalError("AI Horde accepted no job", provider=cls.name)
        status_url = f"{base}/v2/generate/text/status/{job_id}"
        status = await cls._wait(instance, status_url, status_url, headers)
        first = cls._first_generation(status)
        return chat_answer(
            str(first.get("text") or ""),
            None,
            "stop",
            str(first.get("model") or models[0]),
            None,
            None,
        )

    @classmethod
    async def image(
        cls, instance: ProviderInstanceModel, prompt: str, size: str
    ) -> Dict[str, Any]:
        """An image job: submitted to /v2/generate/async, waited on through
        the light /check endpoint, then collected once from /status (its
        generations as URLs). ``size`` "auto" leaves the Horde's default."""
        models = cls.models(instance, "image_model")
        base = str(cls.setting(instance, "base_url")).rstrip("/")
        headers = cls.headers(instance)
        params: Dict[str, Any] = {"n": 1}
        if size and size != "auto":
            width, _, height = size.partition("x")
            params.update(width=int(width), height=int(height))
        nsfw = cls._flag(instance, "allow_nsfw")
        job = await cls.http().post(
            f"{base}/v2/generate/async",
            json={
                "prompt": prompt,
                "params": params,
                "models": models,
                "nsfw": nsfw,
                "censor_nsfw": not nsfw,
                "r2": True,
                "trusted_workers": cls._flag(instance, "trusted_workers"),
                "slow_workers": cls._flag(instance, "slow_workers"),
            },
            headers=headers,
        )
        job_id = str((job or {}).get("id") or "")
        if not job_id:
            raise TransientExternalError(
                "AI Horde accepted no image job", provider=cls.name
            )
        status_url = f"{base}/v2/generate/status/{job_id}"
        await cls._wait(
            instance, f"{base}/v2/generate/check/{job_id}", status_url, headers
        )
        status = await cls.http().get(status_url, headers=headers) or {}
        cls._first_generation(status)
        images = [
            (
                {"url": str(g["img"])}
                if str(g.get("img", "")).startswith("http")
                else {"base64": str(g.get("img", ""))}
            )
            for g in status.get("generations") or []
            if g.get("img")
        ]
        model = str((status.get("generations") or [{}])[0].get("model") or models[0])
        return {"images": images, "model": model}

    @classmethod
    async def _wait(
        cls,
        instance: ProviderInstanceModel,
        poll_url: str,
        cancel_url: str,
        headers: Dict[str, str],
    ) -> Dict[str, Any]:
        """Poll ``poll_url`` until the job is done, and return that last
        answer (a text job's status carries its generations; an image job's
        check doesn't, so its status is fetched once after). Faulted, impossible or
        past the instance's timeout is transient (the job is cancelled at
        ``cancel_url`` first, refunding its kudos)."""
        interval = max(
            MIN_POLL_SECONDS,
            cls._number(instance, "poll_interval_seconds", DEFAULT_POLL_SECONDS),
        )
        deadline = time.monotonic() + cls._number(
            instance, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS
        )
        while True:
            status = await cls.http().get(poll_url, headers=headers) or {}
            if status.get("faulted"):
                raise TransientExternalError("AI Horde job faulted", provider=cls.name)
            if status.get("is_possible") is False:
                await cls._cancel(cancel_url, headers)
                raise TransientExternalError(
                    "No AI Horde worker can serve this job", provider=cls.name
                )
            if status.get("done"):
                return dict(status)
            if time.monotonic() >= deadline:
                await cls._cancel(cancel_url, headers)
                raise TransientExternalError(
                    "AI Horde job timed out", provider=cls.name
                )
            await asyncio.sleep(interval)

    @classmethod
    def _first_generation(cls, status: Dict[str, Any]) -> Dict[str, Any]:
        generations = status.get("generations") or []
        if not generations:
            raise TransientExternalError(
                "AI Horde finished with no generation", provider=cls.name
            )
        return dict(generations[0])

    @classmethod
    async def _cancel(cls, status_url: str, headers: Dict[str, str]) -> None:
        """Withdraw a job we no longer wait for (its kudos are refunded);
        failing to is not the call's failure."""
        try:
            await cls.http().delete(status_url, headers=headers)
        except Exception:  # noqa: BLE001 - best effort
            pass
