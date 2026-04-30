"""Tests for FairQueueConsumerService (Item 57).

Real InMemoryQueueSource overridden for tenant_key/priority. No mocks.
"""

from __future__ import annotations

from typing import Any, List

import pytest

from serverframework.logic.AbstractService import (
    FairQueueConsumerService,
    InMemoryQueueSource,
    JobPriority,
)


class _TenantPrioritySource(InMemoryQueueSource):
    def tenant_key(self, item: Any) -> str:
        return item.get("tenant", "_default")

    def priority(self, item: Any) -> JobPriority:
        return JobPriority(item.get("priority", "normal"))


class _RecordingFair(FairQueueConsumerService):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.processed: List[dict] = []

    async def process(self, item: dict) -> None:
        self.processed.append(item)


@pytest.fixture(autouse=True)
def _reset_metrics():
    FairQueueConsumerService.reset_metrics()
    yield
    FairQueueConsumerService.reset_metrics()


@pytest.mark.unit
async def test_fair_round_robin_starves_no_tenant():
    src = _TenantPrioritySource()
    for i in range(20):
        src.push({"id": f"a-{i}", "tenant": "A", "priority": "normal"})
    src.push({"id": "b-0", "tenant": "B", "priority": "normal"})

    svc = _RecordingFair(requester_id="r", queue_source=src, batch_size=64)

    await svc.update()
    await svc.update()

    ids = [p["id"] for p in svc.processed]
    assert "b-0" in ids
    assert ids.index("b-0") <= 1


@pytest.mark.unit
async def test_high_priority_drains_before_normal():
    src = _TenantPrioritySource()
    src.push({"id": "a-high", "tenant": "A", "priority": "high"})
    for i in range(100):
        src.push({"id": f"b-{i}", "tenant": "B", "priority": "normal"})

    svc = _RecordingFair(requester_id="r", queue_source=src, batch_size=200)

    await svc.update()
    assert svc.processed[0]["id"] == "a-high"


@pytest.mark.unit
async def test_lane_order_and_within_lane_round_robin():
    src = _TenantPrioritySource()
    # Three tenants, mixed priorities.
    for tenant in ("A", "B", "C"):
        for prio in ("high", "normal", "low"):
            src.push({"id": f"{tenant}-{prio}", "tenant": tenant, "priority": prio})

    svc = _RecordingFair(requester_id="r", queue_source=src, batch_size=64)

    for _ in range(9):
        await svc.update()

    ids = [p["id"] for p in svc.processed]
    # First 3 are high lane, next 3 normal, next 3 low.
    assert {ids[0], ids[1], ids[2]} == {"A-high", "B-high", "C-high"}
    assert {ids[3], ids[4], ids[5]} == {"A-normal", "B-normal", "C-normal"}
    assert {ids[6], ids[7], ids[8]} == {"A-low", "B-low", "C-low"}


@pytest.mark.unit
async def test_within_lane_alternates_tenants():
    src = _TenantPrioritySource()
    src.push({"id": "a-1", "tenant": "A", "priority": "normal"})
    src.push({"id": "a-2", "tenant": "A", "priority": "normal"})
    src.push({"id": "b-1", "tenant": "B", "priority": "normal"})
    src.push({"id": "b-2", "tenant": "B", "priority": "normal"})

    svc = _RecordingFair(requester_id="r", queue_source=src, batch_size=16)

    for _ in range(4):
        await svc.update()

    tenants_in_order = [p["tenant"] for p in svc.processed]
    # Round-robin: A, B, A, B (cursor advances each dispatch).
    assert tenants_in_order[0] != tenants_in_order[1]
    assert tenants_in_order[1] != tenants_in_order[2]
    assert tenants_in_order[2] != tenants_in_order[3]


@pytest.mark.unit
async def test_defaults_single_tenant_normal():
    # Source without tenant_key/priority overrides -> all items go to (_default, normal).
    src = InMemoryQueueSource()
    src.push({"id": "x-1"})
    src.push({"id": "x-2"})
    src.push({"id": "x-3"})

    svc = _RecordingFair(requester_id="r", queue_source=src, batch_size=16)
    for _ in range(3):
        await svc.update()

    ids = [p["id"] for p in svc.processed]
    assert ids == ["x-1", "x-2", "x-3"]
    counter = FairQueueConsumerService.get_drain_counter()
    assert counter.get(("_default", "normal"), 0) == 3


@pytest.mark.unit
async def test_drain_counter_increments_per_dispatch():
    src = _TenantPrioritySource()
    src.push({"id": "a-1", "tenant": "A", "priority": "high"})
    src.push({"id": "b-1", "tenant": "B", "priority": "normal"})
    src.push({"id": "a-2", "tenant": "A", "priority": "high"})

    svc = _RecordingFair(requester_id="r", queue_source=src, batch_size=16)

    for _ in range(3):
        await svc.update()

    counter = FairQueueConsumerService.get_drain_counter()
    assert counter[("A", "high")] == 2
    assert counter[("B", "normal")] == 1


@pytest.mark.unit
async def test_wait_summary_records_dispatch_latency():
    src = _TenantPrioritySource()
    src.push({"id": "a-1", "tenant": "A", "priority": "normal"})

    svc = _RecordingFair(requester_id="r", queue_source=src, batch_size=16)
    await svc.update()

    summary = FairQueueConsumerService.get_wait_summary()
    s = summary[("A", "normal")]
    assert s["count"] == 1
    assert s["min"] >= 0.0
    assert s["max"] >= s["min"]
    assert s["sum"] >= s["min"]


@pytest.mark.unit
async def test_handler_callable_used_instead_of_process():
    received: List[dict] = []

    def my_handler(item: dict) -> None:
        received.append(item)

    src = _TenantPrioritySource()
    src.push({"id": "h-1", "tenant": "A", "priority": "normal"})

    svc = FairQueueConsumerService(
        requester_id="r",
        queue_source=src,
        batch_size=4,
        handler=my_handler,
    )
    await svc.update()
    assert received == [{"id": "h-1", "tenant": "A", "priority": "normal"}]


@pytest.mark.unit
async def test_lane_falls_through_when_empty():
    src = _TenantPrioritySource()
    src.push({"id": "low-1", "tenant": "A", "priority": "low"})

    svc = _RecordingFair(requester_id="r", queue_source=src, batch_size=8)
    await svc.update()
    assert [p["id"] for p in svc.processed] == ["low-1"]


@pytest.mark.unit
async def test_empty_source_no_dispatch():
    src = _TenantPrioritySource()
    svc = _RecordingFair(requester_id="r", queue_source=src, batch_size=4)
    await svc.update()
    assert svc.processed == []
