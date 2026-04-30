"""Tests for the broadened service-interface subclasses (Item 28).

These cover the new ``ScheduledService``, ``QueueConsumerService``, and
``StreamingService`` flavors added at the bottom of ``logic.AbstractService``.
"""

from __future__ import annotations

import asyncio
from typing import Any, List

import pytest

from serverframework.logic.AbstractService import (
    DEFAULT_DRAIN_PERIOD_SECONDS,
    ConsumerStreamingService,
    HealthStatus,
    InMemoryQueueSource,
    PerpetualService,
    ProducerStreamingService,
    QueueConsumerService,
    ScheduledService,
    StreamingService,
)


# ---------------------------------------------------------------------------
# ScheduledService
# ---------------------------------------------------------------------------


class _RecordingScheduled(ScheduledService):
    async def update(self) -> None:
        self.runs = getattr(self, "runs", 0) + 1


@pytest.mark.unit
async def test_scheduled_service_persists_last_run():
    saved: dict = {}

    def state_store(service_id, value=None):
        if value is not None:
            saved[service_id] = value
            return None
        return saved.get(service_id)

    svc = _RecordingScheduled(
        requester_id="r-1",
        interval_seconds=0,  # fire as fast as possible
        state_store=state_store,
        service_id="sched-1",
    )
    svc.start()

    async def run_briefly():
        task = asyncio.create_task(svc.run_service_loop())
        await asyncio.sleep(0.05)
        svc.stop()
        await asyncio.wait_for(task, timeout=1.0)

    await run_briefly()
    assert getattr(svc, "runs", 0) >= 1
    assert "sched-1" in saved
    assert svc.last_run_at is not None


# ---------------------------------------------------------------------------
# QueueConsumerService
# ---------------------------------------------------------------------------


class _RecordingConsumer(QueueConsumerService):
    def __init__(self, *args, fail_n_times: int = 0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.processed: List[dict] = []
        self.fail_n_times = fail_n_times
        self._failed = 0

    async def process(self, item: dict) -> None:
        if self._failed < self.fail_n_times:
            self._failed += 1
            raise RuntimeError("transient")
        self.processed.append(item)


@pytest.mark.unit
async def test_queue_consumer_processes_and_acks():
    src = InMemoryQueueSource()
    src.push({"id": "i-1", "v": 1})
    svc = _RecordingConsumer(
        requester_id="r-2",
        queue_source=src,
        max_retries=3,
    )
    await svc.update()
    assert svc.processed == [{"id": "i-1", "v": 1}]
    assert src.acked == [{"id": "i-1", "v": 1}]


@pytest.mark.unit
async def test_queue_consumer_dlqs_after_max_retries():
    src = InMemoryQueueSource()
    src.push({"id": "i-bad", "v": 0})
    svc = _RecordingConsumer(
        requester_id="r-3",
        queue_source=src,
        max_retries=2,
        fail_n_times=99,
    )
    # 3 attempts: each fails, first two nack (re-enqueue), third moves to DLQ.
    for _ in range(3):
        await svc.update()
    assert len(src.dlq) == 1
    assert src.dlq[0]["item"]["id"] == "i-bad"


# ---------------------------------------------------------------------------
# StreamingService
# ---------------------------------------------------------------------------


class _RecordingStreamer(StreamingService):
    def __init__(self, *args, messages: List[Any], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._messages = list(messages)
        self.received: List[Any] = []
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def connect(self) -> Any:
        self.connect_calls += 1
        return object()

    async def iter_messages(self, connection):
        for m in self._messages:
            yield m

    async def on_message(self, message: Any) -> None:
        self.received.append(message)
        if len(self.received) >= len(self._messages):
            self.stop()

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


@pytest.mark.unit
async def test_streaming_service_connects_consumes_and_disconnects():
    svc = _RecordingStreamer(
        requester_id="r-4",
        messages=["a", "b", "c"],
    )
    svc.start()
    await asyncio.wait_for(svc.run_service_loop(), timeout=2.0)
    assert svc.received == ["a", "b", "c"]
    assert svc.connect_calls == 1
    assert svc.disconnect_calls == 1


@pytest.mark.unit
def test_subclass_aliases_exist():
    assert issubclass(ConsumerStreamingService, StreamingService)
    assert issubclass(ProducerStreamingService, StreamingService)
    # PerpetualService is a transparent alias for AbstractService semantics.
    assert PerpetualService is not None


# ---------------------------------------------------------------------------
# Item 28 cross-flavor lifecycle: health, drain period, restart policy, failed
# ---------------------------------------------------------------------------


class _NoopPerpetual(PerpetualService):
    async def update(self) -> None:
        return None


@pytest.mark.unit
def test_default_drain_period_is_30s():
    assert DEFAULT_DRAIN_PERIOD_SECONDS == 30.0
    svc = _NoopPerpetual(requester_id="r")
    assert svc.drain_period_seconds == 30.0


@pytest.mark.unit
def test_drain_period_override():
    svc = _NoopPerpetual(requester_id="r", drain_period_seconds=5)
    assert svc.drain_period_seconds == 5


@pytest.mark.unit
def test_default_restart_policy_is_on_failure():
    svc = _NoopPerpetual(requester_id="r")
    assert svc.restart_policy == "on_failure"


@pytest.mark.unit
def test_restart_policy_override():
    svc = _NoopPerpetual(requester_id="r", restart_policy="always")
    assert svc.restart_policy == "always"


@pytest.mark.unit
def test_health_when_not_running():
    svc = _NoopPerpetual(requester_id="r")
    h = svc.health()
    assert h.status == HealthStatus.DOWN


@pytest.mark.unit
def test_health_when_running():
    svc = _NoopPerpetual(requester_id="r")
    svc.start()
    assert svc.health().status == HealthStatus.OK
    svc.pause()
    assert svc.health().status == HealthStatus.DEGRADED
    svc.resume()
    assert svc.health().status == HealthStatus.OK
    svc.stop()


@pytest.mark.unit
def test_failed_state_after_max_failures():
    svc = _NoopPerpetual(requester_id="r", max_failures=1)
    # Trip through _handle_failure to enter terminal failed state.
    try:
        svc._handle_failure(RuntimeError("boom"))
    except Exception:
        pass
    try:
        svc._handle_failure(RuntimeError("boom"))
    except Exception:
        pass
    assert svc.failed is True
    assert svc.health().status == HealthStatus.FAILED


@pytest.mark.unit
def test_reset_failed_clears_state():
    svc = _NoopPerpetual(requester_id="r", max_failures=0)
    try:
        svc._handle_failure(RuntimeError("boom"))
    except Exception:
        pass
    assert svc.failed is True
    svc.reset_failed()
    assert svc.failed is False
    assert svc.failures == 0


@pytest.mark.unit
def test_health_status_to_dict():
    svc = _NoopPerpetual(requester_id="r")
    d = svc.health().to_dict()
    assert "status" in d and "detail" in d and "timestamp" in d
