# SPDX-License-Identifier: AGPL-3.0-or-later
"""Event-loop stall watchdog.

A daemon thread posts a no-op onto the worker's event loop every
``LOOP_WATCHDOG_INTERVAL_SECONDS`` (default 0.5) and waits for the loop to
run it. When the loop has not answered within ``LOOP_STALL_WARN_SECONDS``
(default 2; 0 turns the watchdog off) something synchronous is holding it,
and every request on the worker is waiting: the watchdog logs one WARNING
with the loop thread's stack, so the code holding it is named, then one
more with the stall's length when the loop answers again.
"""

import asyncio
import sys
import threading
import time
import traceback
from typing import Optional

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

STALL_WARN_DEFAULT = 2.0
INTERVAL_DEFAULT = 0.5


def _seconds(name: str, default: float) -> float:
    raw = (env(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %s", name, raw, default)
        return default


class LoopWatchdog:
    """Watches one event loop from a daemon thread (see the module doc)."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        loop_thread_id: int,
        threshold: float,
        interval: float,
    ) -> None:
        self.loop = loop
        self.loop_thread_id = loop_thread_id
        self.threshold = threshold
        self.interval = interval
        self.stalls = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="zephyrex-loop-watchdog", daemon=True
        )

    def start(self) -> "LoopWatchdog":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval + self.threshold + 1)

    def _stack(self) -> str:
        frame = sys._current_frames().get(self.loop_thread_id)
        if frame is None:
            return "  (the loop thread's frame is not available)"
        return "".join(traceback.format_stack(frame))

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            answered = threading.Event()
            sent = time.monotonic()
            try:
                self.loop.call_soon_threadsafe(answered.set)
            except RuntimeError:  # the loop is closed: the worker is gone
                return
            if answered.wait(self.threshold):
                continue
            self.stalls += 1
            logger.warning(
                "Event loop stalled: no answer in %.1fs; every request on this "
                "worker is waiting. The loop thread is in:\n%s",
                self.threshold,
                self._stack(),
            )
            while not answered.wait(self.interval):
                if self._stop.is_set() or self.loop.is_closed():
                    return
            logger.warning(
                "Event loop stall cleared after %.1fs", time.monotonic() - sent
            )


def start_loop_watchdog() -> Optional[LoopWatchdog]:
    """Watch the running loop, unless ``LOOP_STALL_WARN_SECONDS`` is 0."""
    threshold = _seconds("LOOP_STALL_WARN_SECONDS", STALL_WARN_DEFAULT)
    if threshold <= 0:
        return None
    interval = _seconds("LOOP_WATCHDOG_INTERVAL_SECONDS", INTERVAL_DEFAULT)
    return LoopWatchdog(
        asyncio.get_running_loop(),
        threading.get_ident(),
        threshold,
        max(interval, 0.05),
    ).start()
