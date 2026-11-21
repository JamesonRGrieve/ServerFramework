# SPDX-License-Identifier: AGPL-3.0-or-later
"""Probe: writes in a background thread (under ``contextvars.copy_context``)
while requests poll. Zephyrex RPG Wiki saw a 120-record copy that takes 8 s
inline take 50 s in a thread while a GET polled every 0.5 s on SQLite, with
stalls of up to 14 s: a write lock held across requests. This measures the
framework alone: the same writes inline, threaded and unpolled, and threaded
while polled, and holds the polled run to a small multiple of the inline one."""

from __future__ import annotations

import contextvars
import threading
import time
import uuid
from typing import Any, List

from zephyrex.logic.BLL_Auth import TeamManager

RECORDS = 120
POLL_SECONDS = 0.05


def _copy(registry: Any, requester_id: str, tag: str, stalls: List[float]) -> None:
    teams = TeamManager(requester_id=requester_id, model_registry=registry)
    for n in range(RECORDS):
        started = time.monotonic()
        teams.create(name=f"{tag}-{n}", description=tag)
        stalls.append(time.monotonic() - started)


def _threaded(
    registry: Any, requester_id: str, tag: str, stalls: List[float]
) -> threading.Thread:
    context = contextvars.copy_context()
    thread = threading.Thread(
        target=context.run, args=(_copy, registry, requester_id, tag, stalls)
    )
    thread.start()
    return thread


def test_background_writes_are_not_stalled_by_polling(server, admin_a):
    registry = server.app.state.model_registry
    headers = {"Authorization": f"Bearer {admin_a.jwt}"}
    watched = server.post(
        "/v1/team",
        json={"team": {"name": f"watched-{uuid.uuid4().hex[:8]}", "description": "w"}},
        headers=headers,
    ).json()["team"]["id"]

    inline: List[float] = []
    started = time.monotonic()
    _copy(registry, admin_a.id, f"inline-{uuid.uuid4().hex[:6]}", inline)
    inline_seconds = time.monotonic() - started

    unpolled: List[float] = []
    started = time.monotonic()
    _threaded(registry, admin_a.id, f"joined-{uuid.uuid4().hex[:6]}", unpolled).join()
    unpolled_seconds = time.monotonic() - started

    polled: List[float] = []
    polls = 0
    started = time.monotonic()
    thread = _threaded(registry, admin_a.id, f"polled-{uuid.uuid4().hex[:6]}", polled)
    while thread.is_alive():
        assert server.get(f"/v1/team/{watched}", headers=headers).status_code == 200
        polls += 1
        time.sleep(POLL_SECONDS)
    thread.join()
    polled_seconds = time.monotonic() - started

    report = (
        f"inline {inline_seconds:.2f}s (worst step {max(inline):.2f}s); "
        f"threaded {unpolled_seconds:.2f}s (worst {max(unpolled):.2f}s); "
        f"threaded+polled {polled_seconds:.2f}s over {polls} polls "
        f"(worst {max(polled):.2f}s)"
    )
    print(report)
    assert len(polled) == RECORDS, report
    assert polled_seconds < max(3 * inline_seconds, inline_seconds + 5), report
    assert max(polled) < 2.0, report
