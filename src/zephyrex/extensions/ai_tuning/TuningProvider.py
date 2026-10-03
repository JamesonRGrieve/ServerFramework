# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a fine-tuning provider offers, and the checks a job's inputs pass
before any of them reach one.

A dataset is chat-format JSONL: one JSON object per line, each with
``messages`` in the Chat Completions shape (optionally ``tools`` and
``parallel_tool_calls``), at least one assistant message, and an
assistant message's optional ``weight`` 0 or 1."""

import json
from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

TUNING_REQUEST_TIMEOUT_SECONDS = 120.0
FINE_TUNING = "fine_tuning"
# The provider's own minimum (OpenAI refuses a file of fewer lines).
MIN_EXAMPLES = 10
# This server's caps, under OpenAI's 512 MB file limit: a dataset arrives
# in a request body, which the inbound limit holds to 10 MiB.
MAX_DATASET_BYTES = 10 * 1024 * 1024
MAX_EXAMPLES = 50_000
MAX_SUFFIX_CHARACTERS = 64
MAX_MODEL_CHARACTERS = 256
MAX_PAGE = 100
ROLES = ("system", "developer", "user", "assistant", "tool")
DATASET_KEYS = {"messages", "tools", "parallel_tool_calls"}


def _checked_message(line: int, message: Any) -> bool:
    """Check one message of example ``line``; whether it is the assistant's."""
    if not isinstance(message, dict) or message.get("role") not in ROLES:
        raise InvalidInputExternalError(
            f"line {line}: each message is an object whose role is one of "
            f"{', '.join(ROLES)}"
        )
    role = message["role"]
    content = message.get("content")
    if content is None:
        if not (role == "assistant" and message.get("tool_calls")):
            raise InvalidInputExternalError(
                f"line {line}: a {role} message has content"
            )
    elif not isinstance(content, (str, list)):
        raise InvalidInputExternalError(
            f"line {line}: content is text or a list of content parts"
        )
    if "weight" in message and (
        role != "assistant"
        or isinstance(message["weight"], bool)
        or message["weight"] not in (0, 1)
    ):
        raise InvalidInputExternalError(
            f"line {line}: weight is 0 or 1, on an assistant message"
        )
    if message.get("tool_calls") is not None and not isinstance(
        message["tool_calls"], list
    ):
        raise InvalidInputExternalError(f"line {line}: tool_calls is a list")
    return bool(role == "assistant")


def _checked_example(line: int, text: str) -> None:
    try:
        example = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidInputExternalError(f"line {line} is not JSON") from exc
    if not isinstance(example, dict):
        raise InvalidInputExternalError(f"line {line} is not a JSON object")
    unknown = set(example) - DATASET_KEYS
    if unknown:
        raise InvalidInputExternalError(
            f"line {line}: unexpected keys {', '.join(sorted(unknown))}"
        )
    messages = example.get("messages")
    if not isinstance(messages, list) or not messages:
        raise InvalidInputExternalError(f"line {line}: messages is a non-empty list")
    if "tools" in example and not isinstance(example["tools"], list):
        raise InvalidInputExternalError(f"line {line}: tools is a list")
    assistant = [_checked_message(line, message) for message in messages]
    if not any(assistant):
        raise InvalidInputExternalError(
            f"line {line}: an example has an assistant message to learn from"
        )


def dataset_lines(data: str) -> List[str]:
    """``data``'s lines, a final newline allowed."""
    lines = data.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def checked_dataset(data: Any, what: str = "training_data") -> bytes:
    """``data`` (chat-format JSONL text) as the bytes to upload, or an
    invalid-input error naming the first bad line."""
    if not isinstance(data, str) or not data.strip():
        raise InvalidInputExternalError(f"{what} is chat-format JSONL text")
    content = data.encode()
    if len(content) > MAX_DATASET_BYTES:
        raise InvalidInputExternalError(
            f"{what} is at most {MAX_DATASET_BYTES // (1024 * 1024)} MiB"
        )
    lines = dataset_lines(data)
    if not MIN_EXAMPLES <= len(lines) <= MAX_EXAMPLES:
        raise InvalidInputExternalError(
            f"{what} has {MIN_EXAMPLES}-{MAX_EXAMPLES} examples, one per line, "
            f"not {len(lines)}"
        )
    for number, text in enumerate(lines, start=1):
        if not text.strip():
            raise InvalidInputExternalError(f"{what}: line {number} is empty")
        try:
            _checked_example(number, text)
        except InvalidInputExternalError as exc:
            raise InvalidInputExternalError(f"{what}: {exc.message}") from exc
    return content


def checked_hyperparameters(
    n_epochs: Optional[int],
    batch_size: Optional[int],
    learning_rate_multiplier: Optional[float],
) -> Dict[str, Any]:
    """The hyperparameters asked for; the provider picks the rest."""
    found: Dict[str, Any] = {}
    for key, value in (("n_epochs", n_epochs), ("batch_size", batch_size)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise InvalidInputExternalError(f"{key} is a positive whole number")
        found[key] = value
    rate = learning_rate_multiplier
    if rate is not None:
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate <= 0:
            raise InvalidInputExternalError(
                "learning_rate_multiplier is a positive number"
            )
        found["learning_rate_multiplier"] = float(rate)
    return found


def checked_model(model: Any) -> str:
    if (
        not isinstance(model, str)
        or not model.strip()
        or len(model) > MAX_MODEL_CHARACTERS
    ):
        raise InvalidInputExternalError(
            f"base_model is 1-{MAX_MODEL_CHARACTERS} characters"
        )
    return model


def checked_suffix(suffix: Optional[str]) -> Optional[str]:
    if suffix is None:
        return None
    if not isinstance(suffix, str) or not 1 <= len(suffix) <= MAX_SUFFIX_CHARACTERS:
        raise InvalidInputExternalError(
            f"suffix is 1-{MAX_SUFFIX_CHARACTERS} characters"
        )
    return suffix


def checked_seed(seed: Optional[int]) -> Optional[int]:
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise InvalidInputExternalError("seed is a whole number")
    return seed


def checked_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise InvalidInputExternalError("limit is a whole number")
    if not 1 <= limit <= MAX_PAGE:
        raise InvalidInputExternalError(f"limit is 1-{MAX_PAGE}")
    return limit


class AbstractTuningProvider(AbstractStaticProvider):
    """A fine-tuning API; each instance is one account.

    A job comes back as ``{provider_job_id, base_model, status,
    fine_tuned_model, training_file_id, validation_file_id, error,
    trained_tokens, hyperparameters, finished_at}``, with ``status`` the
    provider's own word for it; a page as ``{data, has_more}``."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {FINE_TUNING}
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = TUNING_REQUEST_TIMEOUT_SECONDS
    # Statuses after which a job no longer changes.
    finished_statuses: ClassVar[Set[str]] = set()

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def unsupported(cls, what: str) -> PermanentExternalError:
        return PermanentExternalError(
            f"{cls.friendly_name} does not offer {what}", provider=cls.name
        )

    @classmethod
    async def upload_dataset(
        cls, instance: ProviderInstanceModel, content: bytes, filename: str
    ) -> Dict[str, Any]:
        """Upload a checked dataset: ``{file_id, bytes, filename}``."""
        raise cls.unsupported("dataset uploads")

    @classmethod
    async def create_job(
        cls,
        instance: ProviderInstanceModel,
        base_model: str,
        training_file_id: str,
        validation_file_id: Optional[str],
        suffix: Optional[str],
        hyperparameters: Dict[str, Any],
        seed: Optional[int],
    ) -> Dict[str, Any]:
        raise cls.unsupported("fine-tuning jobs")

    @classmethod
    async def list_jobs(
        cls, instance: ProviderInstanceModel, after: Optional[str], limit: int
    ) -> Dict[str, Any]:
        """The account's jobs, newest first: ``{data: [job], has_more}``."""
        raise cls.unsupported("fine-tuning jobs")

    @classmethod
    async def get_job(
        cls, instance: ProviderInstanceModel, job_id: str
    ) -> Dict[str, Any]:
        raise cls.unsupported("fine-tuning jobs")

    @classmethod
    async def cancel_job(
        cls, instance: ProviderInstanceModel, job_id: str
    ) -> Dict[str, Any]:
        raise cls.unsupported("cancelling jobs")

    @classmethod
    async def job_events(
        cls,
        instance: ProviderInstanceModel,
        job_id: str,
        after: Optional[str],
        limit: int,
    ) -> Dict[str, Any]:
        """``{data: [{id, created_at, level, message, type, data}],
        has_more}``, newest first."""
        raise cls.unsupported("job events")

    @classmethod
    async def job_checkpoints(
        cls,
        instance: ProviderInstanceModel,
        job_id: str,
        after: Optional[str],
        limit: int,
    ) -> Dict[str, Any]:
        """``{data: [{id, created_at, model, step, metrics}], has_more}``;
        each checkpoint's ``model`` is usable as a model name."""
        raise cls.unsupported("checkpoints")

    @classmethod
    def services(cls) -> List[str]:
        return ["ai_tuning"]
