# SPDX-License-Identifier: AGPL-3.0-or-later
"""``route_logs_to`` moves the framework's log sink and hands back the old
stream, so the admin CLI keeps stdout for JSON and restores it after."""

from __future__ import annotations

import io

from zephyrex.lib.Logging import logger, route_logs_to


def test_route_logs_to_moves_and_restores():
    target = io.StringIO()
    previous = route_logs_to(target)
    try:
        logger.warning("routed %s", "line")
    finally:
        assert route_logs_to(previous) is target
    assert "routed line" in target.getvalue()
    logger.warning("after restore")
    assert "after restore" not in target.getvalue()
