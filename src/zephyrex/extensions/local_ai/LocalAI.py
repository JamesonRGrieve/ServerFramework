# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every local model format shares: the provider shape (an
``AbstractAIProvider`` whose instances are models on this server, so the
``ai`` extension's neutral chat and embedding shapes hold for them too)
and the abilities each format's extension offers over its models.

Each provider instance is one model, declared by its settings: the
source, repository, pinned revision and per-file SHA-256 its files are
downloaded and checked against (see ``LocalModels``), and the bounds on
what one call may ask of it (``max_tokens``, ``timeout_seconds``). Its
files are downloaded the first time it is used, or ahead of time with
``download_model``; it is loaded into memory on first use and stays until
``unload_model`` (or until room is needed for another).

Generation is bounded: a call asks for at most the instance's
``max_tokens`` (a larger request is cut to it) and stops when its
``timeout_seconds`` run out, answering what it has with finish reason
``length``.
"""

import asyncio
import gc
from abc import abstractmethod
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple, TypeVar

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    InstanceSetting,
    ability,
)
from zephyrex.extensions.ai.EXT_AI import (
    CHAT,
    EMBEDDINGS,
    AbstractAIProvider,
    checked_messages,
    checked_texts,
)
from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.extensions.local_ai.LocalModels import (
    LOADED_MODELS,
    LoadedModel,
    ModelSpec,
    download_verified,
    holds,
    misconfigured,
    model_spec,
    models_root,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceManager, ProviderInstanceModel

TEXT_COMPLETION = "text_completion"
DEFAULT_MAX_TOKENS = 512
MAX_TOKENS_CEILING = 32768
DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 3600.0
# How long past its own deadline a call may run before it is abandoned:
# generation checks the deadline between tokens, so a call overruns it by
# at most one token (and loading, the first time).
DEADLINE_GRACE_SECONDS = 60.0
MAX_TEMPERATURE = 2.0

T = TypeVar("T")

LOCAL_MODEL_SETTINGS: Tuple[InstanceSetting, ...] = (
    InstanceSetting(
        "source",
        "Where the files are downloaded from: one of LOCAL_AI_MODEL_SOURCES",
        default="https://huggingface.co",
    ),
    InstanceSetting("repo", "The model's repository, owner/name", field="model_name"),
    InstanceSetting("revision", "The commit the files are pinned to (40 hex digits)"),
    InstanceSetting(
        "files",
        'The files to download, as JSON: {"path": "sha256", ...}',
        multiline=True,
    ),
    InstanceSetting(
        "source_token",
        "Access token for a gated repository",
        env="HF_TOKEN",
        secret=True,
        field="api_key",
    ),
    InstanceSetting(
        "max_tokens", "The most tokens one call may generate", default="512"
    ),
    InstanceSetting(
        "timeout_seconds", "The longest one call may generate for", default="120"
    ),
)


def bounded_number(
    value: Optional[str], default: float, ceiling: float, what: str, provider: str
) -> float:
    try:
        number = float(value) if value else default
    except ValueError as exc:
        raise misconfigured(f"{what} is a number", provider) from exc
    if not 0 < number <= ceiling:
        raise misconfigured(f"{what} is above 0 and at most {ceiling:g}", provider)
    return number


def checked_temperature(temperature: Optional[float]) -> Optional[float]:
    if temperature is not None and not 0 <= temperature <= MAX_TEMPERATURE:
        raise InvalidInputExternalError(
            f"temperature is between 0 and {MAX_TEMPERATURE:g}"
        )
    return temperature


def completion_answer(
    text: str,
    finish_reason: Optional[str],
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> Dict[str, Any]:
    """A raw text continuation: ``{text, finish_reason, model, usage}``."""
    return {
        "text": text,
        "finish_reason": finish_reason,
        "model": model,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


class AbstractLocalAIProvider(AbstractAIProvider):
    """A model format run on this server; each instance is one model."""

    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = LOCAL_MODEL_SETTINGS
    _env: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def spec(cls, instance: ProviderInstanceModel) -> ModelSpec:
        return model_spec(
            cls.setting(instance, "source"),
            cls.setting(instance, "repo"),
            cls.setting(instance, "revision"),
            cls.setting(instance, "files"),
            cls.name,
        )

    @classmethod
    def model(cls, instance: ProviderInstanceModel) -> str:
        return cls.spec(instance).repo

    @classmethod
    def max_tokens(
        cls, instance: ProviderInstanceModel, requested: Optional[int]
    ) -> int:
        """How many tokens a call may generate: what it asks for, at most
        the instance's ``max_tokens``."""
        cap = int(
            bounded_number(
                cls.setting(instance, "max_tokens"),
                DEFAULT_MAX_TOKENS,
                MAX_TOKENS_CEILING,
                "max_tokens",
                cls.name,
            )
        )
        if requested is None:
            return cap
        if requested < 1:
            raise InvalidInputExternalError("max_tokens is at least 1")
        return min(requested, cap)

    @classmethod
    def timeout_seconds(cls, instance: ProviderInstanceModel) -> float:
        return bounded_number(
            cls.setting(instance, "timeout_seconds"),
            DEFAULT_TIMEOUT_SECONDS,
            MAX_TIMEOUT_SECONDS,
            "timeout_seconds",
            cls.name,
        )

    @classmethod
    def directory(cls, instance: ProviderInstanceModel) -> Path:
        return cls.spec(instance).directory(models_root(), cls.name)

    @classmethod
    async def ensure_files(cls, instance: ProviderInstanceModel) -> Path:
        """The model's directory, its files downloaded and checked."""
        spec, root = cls.spec(instance), models_root()
        for file in spec.files:
            target = spec.local_path(root, file, cls.name)
            if await asyncio.to_thread(holds, target, file.sha256):
                continue
            await download_verified(
                spec.url(file),
                target,
                file.sha256,
                provider=cls.name,
                token=cls.setting(instance, "source_token"),
            )
        return spec.directory(root, cls.name)

    @classmethod
    def files_present(cls, instance: ProviderInstanceModel) -> bool:
        spec, root = cls.spec(instance), models_root()
        return all(spec.local_path(root, f, cls.name).is_file() for f in spec.files)

    @classmethod
    @abstractmethod
    def load_model(cls, instance: ProviderInstanceModel, directory: Path) -> Any:
        """The model in ``directory`` loaded into memory (runs off the
        event loop)."""

    @classmethod
    def close_model(cls, value: Any) -> None:
        """Release a loaded model's memory."""
        del value
        gc.collect()

    @classmethod
    async def loaded(cls, instance: ProviderInstanceModel) -> LoadedModel:
        """The instance's model in memory, downloaded and loaded if it is
        not yet."""
        found = LOADED_MODELS.get(str(instance.id))
        if found is not None:
            return found
        directory = await cls.ensure_files(instance)
        try:
            return await asyncio.to_thread(
                LOADED_MODELS.load,
                str(instance.id),
                instance.name,
                cls.name,
                lambda: cls.load_model(instance, directory),
                cls.close_model,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise PermanentExternalError(
                f"{instance.name} could not be loaded: {type(exc).__name__}",
                provider=cls.name,
                cause=exc,
            ) from exc

    @classmethod
    async def run(cls, instance: ProviderInstanceModel, work: Callable[[Any], T]) -> T:
        """``work`` on the instance's loaded model, off the event loop and
        alone on that model, abandoned if it overruns its deadline."""
        loaded = await cls.loaded(instance)
        budget = cls.timeout_seconds(instance) + DEADLINE_GRACE_SECONDS

        def task() -> T:
            with loaded.lock:
                LOADED_MODELS.touch(loaded)
                return work(loaded.value)

        try:
            return await asyncio.wait_for(asyncio.to_thread(task), budget)
        except TimeoutError as exc:
            raise TransientExternalError(
                f"{instance.name} did not answer within {budget:g} seconds",
                provider=cls.name,
            ) from exc

    @classmethod
    def plain_messages(
        cls,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, str]]:
        """Checked neutral messages as ``{role, content}`` text turns: a
        local text model takes neither images nor tool calls."""
        if tools:
            raise cls.unsupported("tool calls")
        plain: List[Dict[str, str]] = []
        for message in messages:
            if message.get("images"):
                raise cls.unsupported("images")
            if message["role"] == "tool" or message.get("tool_calls"):
                raise cls.unsupported("tool calls")
            plain.append({"role": message["role"], "content": message["content"] or ""})
        return plain

    @classmethod
    async def continue_text(
        cls,
        instance: ProviderInstanceModel,
        prompt: str,
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> Dict[str, Any]:
        """``prompt`` continued as raw text: a :func:`completion_answer`."""
        raise cls.unsupported("text completion")

    @classmethod
    async def download(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        await cls.ensure_files(instance)
        return cls.describe(instance)

    @classmethod
    async def load(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        await cls.loaded(instance)
        return cls.describe(instance)

    @classmethod
    async def unload(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        await asyncio.to_thread(LOADED_MODELS.unload, str(instance.id))
        return cls.describe(instance)

    @classmethod
    def describe(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        """The instance's model: what it is, and whether its files are here
        and it is in memory."""
        spec = cls.spec(instance)
        return {
            "id": str(instance.id),
            "name": instance.name,
            "provider": cls.name,
            "abilities": sorted(cls._abilities),
            "repo": spec.repo,
            "revision": spec.revision,
            "files": [f.path for f in spec.files],
            "downloaded": cls.files_present(instance),
            "loaded": LOADED_MODELS.get(str(instance.id)) is not None,
        }


class AbstractLocalAIExtension(AbstractStaticExtension):
    """The abilities a local model format's extension offers over its
    models. ``model`` names one model instance (its name or id); without
    it a call goes to the first model in the rotation that can answer.

    Named for the base extension so that defining it inspects that
    extension's folder; it is never registered (the loader matches a
    folder to the class of the same name in that folder's EXT_ module)."""

    name: ClassVar[str] = "local_ai"
    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "list_local_models",
        "download_model",
        "load_model",
        "unload_model",
        "chat",
        "generate_text",
        "complete_text",
        "embed",
    }

    @classmethod
    async def _call(
        cls, model: Optional[str], capability: str, method: str, *args: Any
    ) -> Any:
        if model:
            return await cls.rotate_on_instance(model, method, *args)
        return await cls.rotate_capable(capability, method, *args)

    @classmethod
    @ability("list_local_models")
    async def list_local_models(cls) -> List[Dict[str, Any]]:
        """The models declared for this format, with whether each is
        downloaded and loaded; one not (or wrongly) configured names why."""
        root = cls.root
        if root is None:
            return []
        instances = ProviderInstanceManager(
            model_registry=root.model_registry, requester_id=env("ROOT_ID")
        )
        local = {
            p.name: p for p in cls.providers if issubclass(p, AbstractLocalAIProvider)
        }
        found = []
        for entry in cls.instances_of():
            provider = local.get(entry["provider"])
            if provider is None:
                continue
            instance = ProviderInstanceModel.model_validate(
                instances.get(id=entry["id"]), from_attributes=True
            )
            try:
                found.append(provider.describe(instance))
            except BaseExternalError as exc:
                found.append({**entry, "error": exc.message})
        return found

    @classmethod
    @ability("download_model")
    async def download_model(cls, model: str) -> Dict[str, Any]:
        """Download a model's files ahead of its first use."""
        result: Dict[str, Any] = await cls.rotate_on_instance(model, "download")
        return result

    @classmethod
    @ability("load_model")
    async def load_model(cls, model: str) -> Dict[str, Any]:
        """Load a model into memory ahead of its first use."""
        result: Dict[str, Any] = await cls.rotate_on_instance(model, "load")
        return result

    @classmethod
    @ability("unload_model")
    async def unload_model(cls, model: str) -> Dict[str, Any]:
        """Release a model's memory; it loads again when next used."""
        result: Dict[str, Any] = await cls.rotate_on_instance(model, "unload")
        return result

    @classmethod
    @ability("chat")
    async def chat(
        cls,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
        requester_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """One chat turn over the ``ai`` extension's neutral message shape,
        answered as its chat answer."""
        result: Dict[str, Any] = await cls._call(
            model,
            CHAT,
            "chat",
            checked_messages(messages),
            None,
            max_tokens,
            checked_temperature(temperature),
            requester_id,
        )
        return result

    @classmethod
    @ability("generate_text")
    async def generate_text(
        cls,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
        requester_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """A chat reply to one prompt: ``{text, model, usage}``."""
        answer = await cls.chat(
            [{"role": "user", "content": prompt}],
            max_tokens,
            temperature,
            model,
            requester_id,
        )
        return {
            "text": answer["message"]["content"] or "",
            "model": answer["model"],
            "usage": answer["usage"],
        }

    @classmethod
    @ability("complete_text")
    async def complete_text(
        cls,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """``prompt`` continued as raw text, no chat template:
        ``{text, finish_reason, model, usage}``."""
        if not isinstance(prompt, str) or not prompt:
            raise InvalidInputExternalError("prompt is non-empty text")
        result: Dict[str, Any] = await cls._call(
            model,
            TEXT_COMPLETION,
            "continue_text",
            prompt,
            max_tokens,
            checked_temperature(temperature),
        )
        return result

    @classmethod
    @ability("embed")
    async def embed(
        cls, texts: List[str], model: Optional[str] = None
    ) -> Dict[str, Any]:
        """An embedding vector per text: ``{embeddings, model, dimensions}``."""
        result: Dict[str, Any] = await cls._call(
            model, EMBEDDINGS, "embed", checked_texts(texts)
        )
        return result
