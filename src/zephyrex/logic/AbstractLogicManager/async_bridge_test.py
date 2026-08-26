# SPDX-License-Identifier: AGPL-3.0-or-later
"""#231: the fire-and-forget sync->async bridge is a single shared SSOT
(_fire_and_forget), no longer re-rolled per call site (hook dispatch, email
provider callback)."""

import threading

from zephyrex.logic.AbstractLogicManager import _fire_and_forget


def test_fire_and_forget_runs_coroutine_to_completion():
    ev = threading.Event()

    async def work():
        ev.set()

    _fire_and_forget(work())
    assert ev.wait(timeout=5), "coroutine did not run to completion"


def test_fire_and_forget_is_non_blocking():
    started = threading.Event()
    release = threading.Event()

    async def work():
        started.set()
        release.wait(timeout=5)

    _fire_and_forget(work())
    # The caller returns before the coroutine finishes -> non-blocking.
    assert started.wait(timeout=5)
    release.set()
