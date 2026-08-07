"""Tests for the Item 43 abstract provider templates.

Covers all six abstract provider categories:

* `AbstractObjectStorageProvider`
* `AbstractCacheProvider`
* `AbstractQueueProvider`
* `AbstractSearchProvider`
* `AbstractAIProvider` (plus the canonical tool-use Pydantic types)
* `AbstractNotificationProvider`

For each abstract: confirms it cannot be instantiated directly,
declares the expected `category` ClassVar, and inherits from
`AbstractStaticProvider`. A minimal concrete subclass that implements
every `@abstractmethod` instantiates without error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator, ClassVar, Dict, List, Optional

import pytest

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
)
from zephyrex.extensions.PRV_Abstract_AI import (
    AbstractAIProvider,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from zephyrex.extensions.PRV_Abstract_Cache import AbstractCacheProvider
from zephyrex.extensions.PRV_Abstract_Notification import AbstractNotificationProvider
from zephyrex.extensions.PRV_Abstract_ObjectStorage import AbstractObjectStorageProvider
from zephyrex.extensions.PRV_Abstract_Queue import AbstractQueueProvider
from zephyrex.extensions.PRV_Abstract_Search import AbstractSearchProvider


# ---------------------------------------------------------------------------
# Minimal concrete subclasses — implement every abstractmethod so the class
# is instantiable. They are NOT registered as real providers; they exist
# only to verify the contract closes.
# ---------------------------------------------------------------------------
class _StubObjectStorage(AbstractObjectStorageProvider):
    name: ClassVar[str] = "stub_object_storage"

    @classmethod
    def bond_instance(cls, instance):
        return AbstractProviderInstance(instance)

    @classmethod
    async def upload(cls, provider_instance, key, data, content_type=None):
        return key

    @classmethod
    async def download(cls, provider_instance, key):
        return b""

    @classmethod
    async def list(cls, provider_instance, prefix=None):
        return []

    @classmethod
    async def delete(cls, provider_instance, key):
        return None

    @classmethod
    async def presigned_url(cls, provider_instance, key, expires_in_seconds=3600):
        return f"https://stub/{key}"

    @classmethod
    async def multipart_upload_init(cls, provider_instance, key):
        return "upload-id"

    @classmethod
    async def multipart_upload_part(
        cls, provider_instance, upload_id, part_number, data
    ):
        return "etag"

    @classmethod
    async def multipart_upload_complete(cls, provider_instance, upload_id, parts):
        return "https://stub/done"


class _StubCache(AbstractCacheProvider):
    name: ClassVar[str] = "stub_cache"

    @classmethod
    def bond_instance(cls, instance):
        return AbstractProviderInstance(instance)

    @classmethod
    async def get(cls, provider_instance, key):
        return None

    @classmethod
    async def set(cls, provider_instance, key, value, ttl_seconds=None):
        return None

    @classmethod
    async def delete(cls, provider_instance, key):
        return False

    @classmethod
    async def incr(cls, provider_instance, key, amount=1):
        return amount

    @classmethod
    async def setnx(cls, provider_instance, key, value, ttl_seconds=None):
        return True

    @classmethod
    async def mget(cls, provider_instance, keys):
        return {}


class _StubQueue(AbstractQueueProvider):
    name: ClassVar[str] = "stub_queue"

    @classmethod
    def bond_instance(cls, instance):
        return AbstractProviderInstance(instance)

    @classmethod
    async def enqueue(cls, provider_instance, task_name, payload, delay_seconds=None):
        return "task-1"

    @classmethod
    async def schedule(cls, provider_instance, task_name, payload, run_at):
        return "task-2"

    @classmethod
    async def cron(cls, provider_instance, task_name, payload, cron_expression):
        return "schedule-1"

    @classmethod
    async def dlq_list(cls, provider_instance, limit=100):
        return []

    @classmethod
    async def status(cls, provider_instance, task_id):
        return {"state": "pending"}


class _StubSearch(AbstractSearchProvider):
    name: ClassVar[str] = "stub_search"

    @classmethod
    def bond_instance(cls, instance):
        return AbstractProviderInstance(instance)

    @classmethod
    async def index(cls, provider_instance, index_name, doc_id, doc):
        return None

    @classmethod
    async def query(cls, provider_instance, index_name, query):
        return []

    @classmethod
    async def delete(cls, provider_instance, index_name, doc_id):
        return False

    @classmethod
    async def bulk_index(cls, provider_instance, index_name, docs):
        return len(docs)

    @classmethod
    async def create_schema(cls, provider_instance, index_name, schema):
        return None


class _StubAI(AbstractAIProvider):
    name: ClassVar[str] = "stub_ai"

    @classmethod
    def bond_instance(cls, instance):
        return AbstractProviderInstance(instance)

    @classmethod
    async def complete(
        cls, provider_instance, prompt, model, max_tokens=None, temperature=None
    ):
        return ""

    @classmethod
    async def chat(
        cls,
        provider_instance,
        messages,
        model,
        max_tokens=None,
        temperature=None,
        tools=None,
        output_format=None,
    ):
        return {"content": ""}

    @classmethod
    async def embed(cls, provider_instance, text, model):
        return [0.0]

    @classmethod
    async def stream_chat(
        cls, provider_instance, messages, model, max_tokens=None
    ) -> AsyncIterator[Dict[str, Any]]:
        async def _gen():
            yield {"content": ""}

        return _gen()  # type: ignore[no-any-return]

    @classmethod
    async def generate_image(cls, provider_instance, prompt, model, size=None):
        return b""

    @classmethod
    def _pre_estimate_tokens(cls, prompt, max_tokens=None):
        return (max_tokens or 0) + (len(prompt) if isinstance(prompt, str) else 0)

    @classmethod
    def _post_true_up_tokens(cls, response):
        if isinstance(response, dict):
            usage = response.get("usage") or {}
            return int(usage.get("total_tokens", 0))
        return 0


class _StubNotification(AbstractNotificationProvider):
    name: ClassVar[str] = "stub_notification"

    @classmethod
    def bond_instance(cls, instance):
        return AbstractProviderInstance(instance)

    @classmethod
    async def notify(
        cls, provider_instance, recipient, channel, title, body, payload=None
    ):
        return "msg-1"

    @classmethod
    async def notify_multi(
        cls, provider_instance, recipients, channels, title, body, payload=None
    ):
        return {r: f"msg-{i}" for i, r in enumerate(recipients)}


# ---------------------------------------------------------------------------
# Per-abstract contract assertions
# ---------------------------------------------------------------------------
ABSTRACTS = [
    (AbstractObjectStorageProvider, "object_storage", _StubObjectStorage),
    (AbstractCacheProvider, "cache", _StubCache),
    (AbstractQueueProvider, "queue", _StubQueue),
    (AbstractSearchProvider, "search", _StubSearch),
    (AbstractAIProvider, "ai", _StubAI),
    (AbstractNotificationProvider, "notification", _StubNotification),
]


@pytest.mark.parametrize(
    "abstract_cls,expected_category,_concrete",
    ABSTRACTS,
    ids=[a[1] for a in ABSTRACTS],
)
def test_abstract_inherits_from_static_provider(
    abstract_cls, expected_category, _concrete
):
    assert issubclass(abstract_cls, AbstractStaticProvider)


@pytest.mark.parametrize(
    "abstract_cls,expected_category,_concrete",
    ABSTRACTS,
    ids=[a[1] for a in ABSTRACTS],
)
def test_abstract_category_classvar(abstract_cls, expected_category, _concrete):
    assert abstract_cls.category == expected_category


@pytest.mark.parametrize(
    "abstract_cls,expected_category,_concrete",
    ABSTRACTS,
    ids=[a[1] for a in ABSTRACTS],
)
def test_abstract_cannot_be_instantiated(abstract_cls, expected_category, _concrete):
    # ABCMeta refuses to instantiate when there are unimplemented
    # abstractmethods — this is how the abstract contract is enforced.
    assert abstract_cls.__abstractmethods__, (
        f"{abstract_cls.__name__} declares no abstractmethods — the "
        "ability contract is not being enforced."
    )
    with pytest.raises(TypeError):
        abstract_cls()


@pytest.mark.parametrize(
    "abstract_cls,_expected_category,concrete_cls",
    ABSTRACTS,
    ids=[a[1] for a in ABSTRACTS],
)
def test_concrete_subclass_instantiates(
    abstract_cls, _expected_category, concrete_cls
):
    # The static-provider model never actually instantiates providers,
    # but Python's ABCMeta still requires every abstractmethod be
    # implemented before `__abstractmethods__` is empty. That is what
    # closes the contract for Item 43.
    assert concrete_cls.__abstractmethods__ == frozenset(), (
        f"{concrete_cls.__name__} still has unimplemented abstractmethods: "
        f"{concrete_cls.__abstractmethods__}"
    )
    # And explicitly: instantiation succeeds (no TypeError).
    instance = concrete_cls()
    assert isinstance(instance, abstract_cls)


# ---------------------------------------------------------------------------
# AI-specific: canonical tool-use Pydantic types.
# ---------------------------------------------------------------------------
class TestToolUseTypes:
    def test_tool_definition_fields(self):
        td = ToolDefinition(
            name="lookup_user",
            description="Look up a user by id.",
            parameters={"type": "object", "properties": {"id": {"type": "string"}}},
        )
        assert td.name == "lookup_user"
        assert td.description == "Look up a user by id."
        assert td.parameters == {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        }

    def test_tool_call_fields(self):
        tc = ToolCall(name="lookup_user", arguments={"id": "u1"})
        assert tc.name == "lookup_user"
        assert tc.arguments == {"id": "u1"}

    def test_tool_result_fields(self):
        tr = ToolResult(name="lookup_user", result={"id": "u1", "email": "x@y.z"})
        assert tr.name == "lookup_user"
        assert tr.result == {"id": "u1", "email": "x@y.z"}

    def test_tool_result_accepts_any_result_type(self):
        # `result: Any` MUST accept arbitrary shapes — string, list, dict,
        # number — because the tool's caller-side return is opaque to the
        # framework.
        for value in ("ok", 42, [1, 2, 3], {"k": "v"}, None):
            tr = ToolResult(name="t", result=value)
            assert tr.result == value

    def test_tool_definition_required_fields(self):
        # All three string-vs-dict required fields must be supplied.
        with pytest.raises(Exception):
            ToolDefinition(name="x", description="y")  # missing parameters
        with pytest.raises(Exception):
            ToolDefinition(name="x", parameters={})  # missing description
        with pytest.raises(Exception):
            ToolDefinition(description="y", parameters={})  # missing name


# ---------------------------------------------------------------------------
# AI-specific: quota-flow helpers exist on the abstract with the right shape.
# ---------------------------------------------------------------------------
class TestAIQuotaFlowHelpers:
    def test_pre_and_post_helpers_declared_abstract(self):
        assert "_pre_estimate_tokens" in AbstractAIProvider.__abstractmethods__
        assert "_post_true_up_tokens" in AbstractAIProvider.__abstractmethods__

    def test_stub_pre_estimate_returns_int(self):
        assert isinstance(_StubAI._pre_estimate_tokens("abc", 100), int)

    def test_stub_post_true_up_reads_usage_block(self):
        # The contract is: read the actual count out of the response.
        # The stub demonstrates the read path against the
        # OpenAI-style `usage.total_tokens` shape.
        assert _StubAI._post_true_up_tokens({"usage": {"total_tokens": 17}}) == 17
        assert _StubAI._post_true_up_tokens({}) == 0


# ---------------------------------------------------------------------------
# Smoke: stubs honour their abstract contract end-to-end.
# ---------------------------------------------------------------------------
async def test_stub_object_storage_round_trip():
    assert await _StubObjectStorage.upload(None, "k", b"d") == "k"
    assert await _StubObjectStorage.download(None, "k") == b""
    assert await _StubObjectStorage.list(None) == []
    assert await _StubObjectStorage.delete(None, "k") is None
    url = await _StubObjectStorage.presigned_url(None, "k")
    assert url.startswith("https://")
    upload_id = await _StubObjectStorage.multipart_upload_init(None, "k")
    assert isinstance(upload_id, str)
    etag = await _StubObjectStorage.multipart_upload_part(None, upload_id, 1, b"chunk")
    assert isinstance(etag, str)
    assert isinstance(
        await _StubObjectStorage.multipart_upload_complete(
            None, upload_id, [{"part_number": 1, "etag": etag}]
        ),
        str,
    )


async def test_stub_queue_round_trip():
    assert await _StubQueue.enqueue(None, "task", {}) == "task-1"
    assert (
        await _StubQueue.schedule(None, "task", {}, datetime(2030, 1, 1)) == "task-2"
    )
    assert await _StubQueue.cron(None, "task", {}, "* * * * *") == "schedule-1"
    assert await _StubQueue.dlq_list(None) == []
    assert (await _StubQueue.status(None, "task-1"))["state"] == "pending"


async def test_stub_notification_multi_aligns_recipients():
    out = await _StubNotification.notify_multi(
        None,
        recipients=["alice", "bob"],
        channels=["push", "sms"],
        title="t",
        body="b",
    )
    assert set(out.keys()) == {"alice", "bob"}
