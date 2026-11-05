# SPDX-License-Identifier: AGPL-3.0-or-later
"""GGUF models run by llama.cpp (llama-cpp-python): what loading one
takes, and the deadline every generation stops at."""

import time
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import numpy.typing as npt
from llama_cpp import Llama, LogitsProcessorList
from llama_cpp.llama_types import (
    ChatCompletionRequestAssistantMessage,
    ChatCompletionRequestMessage,
    ChatCompletionRequestSystemMessage,
    ChatCompletionRequestUserMessage,
)

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.local_ai.LocalAI import (
    LOCAL_MODEL_SETTINGS,
    AbstractLocalAIProvider,
    bounded_number,
    checked_temperature,
)
from zephyrex.extensions.local_ai.LocalModels import misconfigured
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_CONTEXT_LENGTH = 2048
MAX_CONTEXT_LENGTH = 1024 * 1024
MAX_THREADS = 1024
MAX_GPU_LAYERS = 100000

GGUF_DEPENDENCIES = Dependencies(
    [
        PIP_Dependency(
            name="llama-cpp-python",
            friendly_name="llama.cpp Python bindings",
            semver=">=0.3.35",
            reason="Runs GGUF models",
        )
    ]
)


class DeadlineStop:
    """A logits processor that ends generation once ``deadline`` (a
    ``time.monotonic()`` value) passes, by leaving only end-of-text
    possible; ``fired`` records that it did."""

    def __init__(self, deadline: float, end_of_text: int) -> None:
        self.deadline = deadline
        self.end_of_text = end_of_text
        self.fired = False

    def __call__(
        self, input_ids: npt.NDArray[np.intc], scores: npt.NDArray[np.single]
    ) -> npt.NDArray[np.single]:
        if time.monotonic() < self.deadline:
            return scores
        self.fired = True
        ended = np.full_like(scores, -np.inf)
        ended[self.end_of_text] = 0.0
        return ended


def llama_messages(turns: List[Dict[str, str]]) -> List[ChatCompletionRequestMessage]:
    """Plain ``{role, content}`` turns as llama.cpp's chat messages."""
    found: List[ChatCompletionRequestMessage] = []
    for turn in turns:
        role, content = turn["role"], turn["content"]
        if role == "system":
            found.append(
                ChatCompletionRequestSystemMessage(role="system", content=content)
            )
        elif role == "assistant":
            found.append(
                ChatCompletionRequestAssistantMessage(role="assistant", content=content)
            )
        else:
            found.append(ChatCompletionRequestUserMessage(role="user", content=content))
    return found


def library_failure(exc: ValueError) -> InvalidInputExternalError:
    """llama.cpp refuses a prompt longer than the context with a
    ValueError: the caller's to shorten."""
    return InvalidInputExternalError(f"the model refused the input: {exc}")


class AbstractGGUFProvider(AbstractLocalAIProvider):
    """A GGUF model; each instance is one model file (the first ``.gguf``
    of its files: a split model's first part)."""

    dependencies: ClassVar[Dependencies] = GGUF_DEPENDENCIES
    # Whether llama.cpp loads the model to embed rather than to generate.
    embedding: ClassVar[bool] = False
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        *LOCAL_MODEL_SETTINGS,
        InstanceSetting("context_length", "Context window in tokens", default="2048"),
        InstanceSetting("threads", "CPU threads to run on (empty: llama.cpp chooses)"),
        InstanceSetting(
            "gpu_layers", "Layers to offload to the GPU (0: CPU only)", default="0"
        ),
    )

    @classmethod
    def model_file(cls, instance: ProviderInstanceModel, directory: Path) -> Path:
        weights = sorted(
            f.path for f in cls.spec(instance).files if f.path.endswith(".gguf")
        )
        if not weights:
            raise misconfigured("files name no .gguf file", cls.name)
        return directory / weights[0]

    @classmethod
    def whole_number(
        cls, instance: ProviderInstanceModel, key: str, default: int, ceiling: int
    ) -> int:
        value = cls.setting(instance, key)
        if not value:
            return default
        if not value.isdigit() or int(value) > ceiling:
            raise misconfigured(f"{key} is a whole number up to {ceiling}", cls.name)
        return int(value)

    @classmethod
    def load_model(cls, instance: ProviderInstanceModel, directory: Path) -> Any:
        threads = cls.whole_number(instance, "threads", 0, MAX_THREADS)
        return Llama(
            model_path=str(cls.model_file(instance, directory)),
            n_ctx=int(
                bounded_number(
                    cls.setting(instance, "context_length"),
                    DEFAULT_CONTEXT_LENGTH,
                    MAX_CONTEXT_LENGTH,
                    "context_length",
                    cls.name,
                )
            ),
            n_threads=threads or None,
            n_gpu_layers=cls.whole_number(instance, "gpu_layers", 0, MAX_GPU_LAYERS),
            embedding=cls.embedding,
            verbose=False,
        )

    @classmethod
    def close_model(cls, value: Any) -> None:
        if isinstance(value, Llama):
            value.close()
        super().close_model(value)

    @classmethod
    def llama(cls, value: Any) -> Llama:
        if not isinstance(value, Llama):
            raise TypeError(f"{cls.name} holds a {type(value).__name__}, not a model")
        return value

    @classmethod
    def generation(
        cls,
        instance: ProviderInstanceModel,
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> Tuple[Dict[str, Any], float]:
        """The bounded generation arguments for one call, and how long it
        may run."""
        arguments: Dict[str, Any] = {"max_tokens": cls.max_tokens(instance, max_tokens)}
        if checked_temperature(temperature) is not None:
            arguments["temperature"] = temperature
        return arguments, cls.timeout_seconds(instance)

    @classmethod
    def stopper(
        cls, llama: Llama, seconds: float
    ) -> Tuple[DeadlineStop, LogitsProcessorList]:
        stop = DeadlineStop(time.monotonic() + seconds, llama.token_eos())
        return stop, LogitsProcessorList([stop])

    @classmethod
    def finish(cls, reason: Optional[str], stop: DeadlineStop) -> Optional[str]:
        return "length" if stop.fired else reason

    @classmethod
    def token_counts(
        cls, usage: Optional[Mapping[str, Any]]
    ) -> Tuple[Optional[int], Optional[int]]:
        """An answer's input and output token counts."""
        found = usage or {}
        return found.get("prompt_tokens"), found.get("completion_tokens")


def embedding_rows(
    data: Sequence[Mapping[str, Any]], count: int, provider: str
) -> List[List[float]]:
    """The pooled vectors of a ``create_embedding`` answer, in input order."""
    rows = sorted(data, key=lambda row: int(row.get("index", 0)))
    vectors: List[List[float]] = []
    for row in rows:
        vector = row.get("embedding")
        if not isinstance(vector, list) or not all(
            isinstance(v, (int, float)) for v in vector
        ):
            raise misconfigured("the model gives no pooled embedding", provider)
        vectors.append([float(v) for v in vector])
    if len(vectors) != count:
        raise misconfigured(f"{len(vectors)} embeddings for {count} texts", provider)
    return vectors
