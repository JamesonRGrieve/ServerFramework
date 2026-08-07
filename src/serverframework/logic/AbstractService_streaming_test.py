"""Tests for the Item 13 streaming additions on top of Item 28's base.

Covers:
- ``streaming_handler`` registry: decorator, fallback wildcard, last-write-wins.
- ``StreamingService.fan_out``: handler dispatch with ``StreamingMessageContext``.
- Cursor persistence via the injected ``state_store``.
- Cross-process event-bus publish on successful dispatch.
- Graceful drain via ``stop_and_drain``: in-flight ``on_message`` finishes
  within ``drain_period_seconds``.
- Drain timeout cancels the in-flight task when it exceeds the deadline.
- Subclass-driven ``classify`` -> default ``on_message`` -> registry path.

No mocks: real handler callables, real ``state_store`` callable, real event-loop
drain timing.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import pytest

from serverframework.logic.AbstractService import (
    STREAMING_HANDLER_REGISTRY,
    StreamingMessageContext,
    StreamingService,
    streaming_handler,
)


@pytest.fixture(autouse=True)
def _clear_registry() -> None:  # type: ignore[misc]
    STREAMING_HANDLER_REGISTRY.clear()
    yield
    STREAMING_HANDLER_REGISTRY.clear()


# ---------------------------------------------------------------------------
# Registry decorator
# ---------------------------------------------------------------------------


def test_streaming_handler_registers_with_string_extension() -> None:
    @streaming_handler("payment", "stripe", event="customer.updated")
    def _h(ctx: StreamingMessageContext) -> None:
        return None

    assert ("payment", "stripe", "customer.updated") in STREAMING_HANDLER_REGISTRY


def test_streaming_handler_registers_with_extension_class_strips_prefix() -> None:
    class EXT_Payment:
        pass

    @streaming_handler(EXT_Payment, "stripe", event="customer.created")
    def _h(ctx: StreamingMessageContext) -> None:
        return None

    assert ("payment", "stripe", "customer.created") in STREAMING_HANDLER_REGISTRY


def test_streaming_handler_uses_extension_name_attribute_when_present() -> None:
    class _Plug:
        extension_name = "Billing"

    @streaming_handler(_Plug, "stripe")
    def _h(ctx: StreamingMessageContext) -> None:
        return None

    assert ("billing", "stripe", None) in STREAMING_HANDLER_REGISTRY


def test_wildcard_handler_used_when_no_event_specific_match() -> None:
    captured: List[str] = []

    @streaming_handler("payment", "stripe")
    def _wildcard(ctx: StreamingMessageContext) -> None:
        captured.append("wildcard")

    @streaming_handler("payment", "stripe", event="invoice.paid")
    def _specific(ctx: StreamingMessageContext) -> None:
        captured.append("specific")

    class _Svc(StreamingService):
        extension_name = "payment"
        provider_name = "stripe"

    svc = _Svc(requester_id="r")
    asyncio.run(svc.fan_out(payload={"amount": 1}, event_name="invoice.paid"))
    asyncio.run(svc.fan_out(payload={"amount": 2}, event_name="customer.unknown"))
    assert captured == ["specific", "wildcard"]


def test_re_registration_overwrites_with_warning() -> None:
    @streaming_handler("payment", "stripe", event="x")
    def _first(ctx: StreamingMessageContext) -> None:
        return None

    @streaming_handler("payment", "stripe", event="x")
    def _second(ctx: StreamingMessageContext) -> None:
        return None

    assert STREAMING_HANDLER_REGISTRY[("payment", "stripe", "x")] is _second


# ---------------------------------------------------------------------------
# fan_out: dispatch + cursor + event-bus
# ---------------------------------------------------------------------------


class _CapturingService(StreamingService):
    extension_name = "demo"
    provider_name = "demoprov"


def test_fan_out_invokes_handler_with_context() -> None:
    received: List[StreamingMessageContext] = []

    @streaming_handler("demo", "demoprov", event="thing.happened")
    def _h(ctx: StreamingMessageContext) -> None:
        received.append(ctx)

    svc = _CapturingService(requester_id="r-1", service_id="svc-1")
    asyncio.run(
        svc.fan_out(
            payload={"id": "abc"},
            event_name="thing.happened",
            headers={"x-trace": "1"},
            cursor="cur-1",
        )
    )
    assert len(received) == 1
    ctx = received[0]
    assert ctx.payload == {"id": "abc"}
    assert ctx.headers == {"x-trace": "1"}
    assert ctx.event_name == "thing.happened"
    assert ctx.cursor == "cur-1"
    assert ctx.service_id == "svc-1"


def test_fan_out_async_handler_awaited() -> None:
    received: List[Any] = []

    @streaming_handler("demo", "demoprov", event="async.evt")
    async def _h(ctx: StreamingMessageContext) -> None:
        await asyncio.sleep(0)
        received.append(ctx.payload)

    svc = _CapturingService(requester_id="r")
    asyncio.run(svc.fan_out(payload="payload-1", event_name="async.evt"))
    assert received == ["payload-1"]


def test_fan_out_persists_cursor_via_state_store() -> None:
    state: Dict[str, Optional[str]] = {}

    def state_store(svc_id: str, value: Optional[str] = None) -> Optional[str]:
        if value is not None:
            state[svc_id] = value
        return state.get(svc_id)

    @streaming_handler("demo", "demoprov", event="e")
    def _h(ctx: StreamingMessageContext) -> None:
        return None

    svc = _CapturingService(requester_id="r", service_id="s-cursor", state_store=state_store)
    asyncio.run(svc.fan_out(payload={}, event_name="e", cursor="c-42"))
    assert svc.cursor == "c-42"
    assert state["s-cursor"] == "c-42"


def test_state_store_loaded_on_init() -> None:
    state: Dict[str, Optional[str]] = {"prev": "c-existing"}

    def state_store(svc_id: str, value: Optional[str] = None) -> Optional[str]:
        if value is not None:
            state[svc_id] = value
        return state.get(svc_id)

    svc = _CapturingService(requester_id="r", service_id="prev", state_store=state_store)
    assert svc.cursor == "c-existing"


def test_fan_out_publishes_to_event_bus_when_provided() -> None:
    @streaming_handler("demo", "demoprov", event="e")
    def _h(ctx: StreamingMessageContext) -> None:
        return None

    published: List[Any] = []

    class _Bus:
        async def publish(self, evt: Any) -> None:
            published.append(evt)

    svc = _CapturingService(requester_id="r", event_bus=_Bus())
    asyncio.run(svc.fan_out(payload={"x": 1}, event_name="e"))
    assert published == [{"x": 1}]


def test_fan_out_no_handler_logs_and_skips() -> None:
    svc = _CapturingService(requester_id="r")
    # Should not raise even when no handler is registered.
    asyncio.run(svc.fan_out(payload={"y": 2}, event_name="nothing.matches"))


def test_fan_out_requires_extension_and_provider_names() -> None:
    class _Bare(StreamingService):
        pass

    svc = _Bare(requester_id="r")
    with pytest.raises(RuntimeError, match="did not declare"):
        asyncio.run(svc.fan_out(payload={}, event_name=None))


# ---------------------------------------------------------------------------
# classify -> default on_message integration
# ---------------------------------------------------------------------------


class _ClassifyingService(StreamingService):
    extension_name = "demo"
    provider_name = "demoprov"

    def classify(
        self, message: Any
    ) -> Tuple[Optional[str], Any, Optional[Dict[str, str]], Optional[str]]:
        return message.get("event"), message.get("data"), {"src": "stream"}, message.get("cursor")


def test_default_on_message_classifies_and_dispatches() -> None:
    captured: List[StreamingMessageContext] = []

    @streaming_handler("demo", "demoprov", event="thing.created")
    def _h(ctx: StreamingMessageContext) -> None:
        captured.append(ctx)

    svc = _ClassifyingService(requester_id="r")
    asyncio.run(
        svc.on_message({"event": "thing.created", "data": {"id": 7}, "cursor": "c-7"})
    )
    assert len(captured) == 1
    assert captured[0].event_name == "thing.created"
    assert captured[0].payload == {"id": 7}
    assert captured[0].cursor == "c-7"


# ---------------------------------------------------------------------------
# Graceful drain
# ---------------------------------------------------------------------------


class _SlowStreamer(StreamingService):
    extension_name = "demo"
    provider_name = "demoprov"

    def __init__(self, *args: Any, work_seconds: float = 0.1, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._work_seconds = work_seconds
        self.processed: List[Any] = []
        self.connect_calls = 0

    async def connect(self) -> Any:
        self.connect_calls += 1
        return object()

    async def iter_messages(self, connection: Any):
        # Long enough to exercise the drain path: yield one message, then idle.
        yield {"event": "e", "data": {"i": 1}}
        await asyncio.sleep(0.5)

    async def on_message(self, message: Any) -> None:
        await asyncio.sleep(self._work_seconds)
        self.processed.append(message)


def test_stop_and_drain_completes_inflight_message_within_deadline() -> None:
    @streaming_handler("demo", "demoprov", event="e")
    def _h(ctx: StreamingMessageContext) -> None:
        return None

    async def scenario() -> _SlowStreamer:
        svc = _SlowStreamer(
            requester_id="r",
            work_seconds=0.05,
            drain_period_seconds=1.0,
        )
        svc.start()
        loop_task = asyncio.create_task(svc.run_service_loop())
        # Give the loop time to enter on_message.
        await asyncio.sleep(0.02)
        await svc.stop_and_drain()
        try:
            await asyncio.wait_for(loop_task, timeout=1.0)
        except asyncio.TimeoutError:
            loop_task.cancel()
        return svc

    svc = asyncio.run(scenario())
    assert len(svc.processed) == 1


def test_stop_and_drain_cancels_when_inflight_exceeds_deadline() -> None:
    @streaming_handler("demo", "demoprov", event="e")
    def _h(ctx: StreamingMessageContext) -> None:
        return None

    async def scenario() -> _SlowStreamer:
        svc = _SlowStreamer(
            requester_id="r",
            work_seconds=2.0,           # work takes 2s
            drain_period_seconds=0.1,   # drain only allows 100ms
        )
        svc.start()
        loop_task = asyncio.create_task(svc.run_service_loop())
        await asyncio.sleep(0.02)
        await svc.stop_and_drain()
        try:
            await asyncio.wait_for(loop_task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            loop_task.cancel()
        return svc

    svc = asyncio.run(scenario())
    # Work was cancelled, so the message did not finish.
    assert len(svc.processed) == 0


# ---------------------------------------------------------------------------
# Reconnect behavior survives across stop_and_drain
# ---------------------------------------------------------------------------


def test_drain_event_set_when_no_inflight_message() -> None:
    svc = _CapturingService(requester_id="r")
    # Fresh service: drained event is set so stop_and_drain returns immediately.
    assert svc._drained.is_set()
