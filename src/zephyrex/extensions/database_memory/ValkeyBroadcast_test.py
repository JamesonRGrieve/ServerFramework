# SPDX-License-Identifier: AGPL-3.0-or-later
"""Against a real Valkey server (``ZEPHYREX_TEST_VALKEY_SERVER`` names its
binary; skipped where none is configured): broadcast events reach every
process, work events reach one, room changes cross buses, and the admin
CLI's cache clearing removes exactly the touched tables' keys.

Each "process" is its own transport and bus, as two server processes would
have."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import BaseModel

from zephyrex.extensions.database_memory.PRV_Valkey import (
    PRV_Valkey,
    ValkeyConnectionStub,
)

VALKEY_SERVER = os.environ.get("ZEPHYREX_TEST_VALKEY_SERVER", "")
DELIVERY_TIMEOUT_SECONDS = 5.0
START_TIMEOUT_SECONDS = 10.0

pytestmark = pytest.mark.skipif(
    not VALKEY_SERVER, reason="ZEPHYREX_TEST_VALKEY_SERVER names no valkey-server"
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def valkey_url() -> Iterator[str]:
    """A fresh, empty Valkey for one test."""
    port = _free_port()
    server = subprocess.Popen(
        [VALKEY_SERVER, "--port", str(port), "--bind", "127.0.0.1"]
        + ["--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            if time.monotonic() > deadline or server.poll() is not None:
                server.kill()
                raise RuntimeError(f"valkey-server did not start on {port}")
            time.sleep(0.1)
    url = f"redis://127.0.0.1:{port}/0"
    try:
        yield url
    finally:
        PRV_Valkey._connections.pop(url, None)
        server.terminate()
        server.wait(timeout=10)


def _transport(url: str) -> Any:
    return PRV_Valkey.build_streams_transport(
        ValkeyConnectionStub(api_key=url), consumer_group="zephyrex"
    )


async def _until(condition: Any) -> None:
    deadline = time.monotonic() + DELIVERY_TIMEOUT_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            return
        await asyncio.sleep(0.05)


async def test_a_broadcast_reaches_every_process(valkey_url: str) -> None:
    first, second = _transport(valkey_url), _transport(valkey_url)
    heard: dict[str, list[bytes]] = {"first": [], "second": []}

    def hearer(name: str) -> Any:
        async def hear(payload: bytes) -> None:
            heard[name].append(payload)

        return hear

    await first.subscribe_broadcast("rooms.test", hearer("first"))
    await second.subscribe_broadcast("rooms.test", hearer("second"))
    await first.broadcast("rooms.test", b"change-1")
    await _until(lambda: heard["first"] and heard["second"])
    await first.close()
    await second.close()
    assert heard == {"first": [b"change-1"], "second": [b"change-1"]}


async def test_work_still_goes_to_one_consumer(valkey_url: str) -> None:
    first, second = _transport(valkey_url), _transport(valkey_url)
    taken: list[bytes] = []

    async def take(payload: bytes) -> None:
        taken.append(payload)

    await first.subscribe("jobs.test", take)
    await second.subscribe("jobs.test", take)
    await first.send("jobs.test", b"job-1")
    await _until(lambda: taken)
    await asyncio.sleep(1.5)  # long enough for a second delivery to show
    await first.close()
    await second.close()
    assert taken == [b"job-1"]


async def test_a_room_change_reaches_every_processs_bus(valkey_url: str) -> None:
    from zephyrex.extensions.rooms.BLL_Rooms import RoomChange, RoomRef
    from zephyrex.logic.EventBus import RedisStreamsEventBus

    buses = [RedisStreamsEventBus(transport=_transport(valkey_url)) for _ in range(2)]
    heard: list[list[RoomChange]] = [[], []]
    for bus, inbox in zip(buses, heard):

        def hear(event: BaseModel, inbox: list[RoomChange] = inbox) -> None:
            assert isinstance(event, RoomChange)
            inbox.append(event)

        bus.subscribe(RoomChange, hear)
    await asyncio.sleep(0.5)  # subscriptions are wired on the running loop

    change = RoomChange(
        id="c1",
        room=RoomRef(type="scene", id="s1"),
        record=RoomRef(type="scene_token", id="t1"),
        op="updated",
        etag='"v2"',
        changed_at="2026-11-21T00:00:00",
    )
    await buses[0].publish(change)
    await _until(lambda: heard[0] and heard[1])
    for bus in buses:
        await bus.close()
    assert heard == [[change], [change]]


def test_cli_clears_only_the_touched_tables_keys(valkey_url: str, monkeypatch) -> None:
    import redis

    from zephyrex.cli.Context import clear_shared_caches
    from zephyrex.lib.Environment import settings

    client = redis.Redis.from_url(valkey_url)
    for key in (
        "entity:teams:1",
        "idx:teams:name:a",
        "resp:v1/team:abc",
        "entity:users:1",
        "resp:v1/user:abc",
    ):
        client.set(key, "x")
    monkeypatch.setattr(settings, "VALKEY_URI", valkey_url, raising=False)

    assert clear_shared_caches({"teams": "team"}) == ["teams"]
    keys = client.keys("*")
    assert sorted(k.decode() if isinstance(k, bytes) else k for k in keys) == [
        "entity:users:1",
        "resp:v1/user:abc",
    ]
    client.close()
