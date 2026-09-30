# SPDX-License-Identifier: AGPL-3.0-or-later
"""A server database that is still starting is retried, not given up on.

The retry slept through ``time.sleep`` with ``time`` imported from
``datetime``, so the first failed attempt against Postgres/MySQL raised
AttributeError instead of waiting (a ``type: ignore`` hid it from mypy).
"""

from typing import List

import pytest

from zephyrex.pydantic2.registry import wait_for_database


class _Engine:
    """Refuses the first ``failures`` connections, then connects."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0

    def connect(self) -> "_Engine":
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OSError("connection refused")
        return self

    def close(self) -> None:
        pass


def test_a_database_still_starting_is_waited_for():
    engine = _Engine(failures=2)
    sleeps: List[float] = []
    wait_for_database(engine, attempts=5, retry_seconds=1.5, sleep=sleeps.append)
    assert engine.attempts == 3
    assert sleeps == [1.5, 1.5]


def test_the_real_sleep_is_callable():
    """The default sleep is the time module's, not datetime.time's."""
    engine = _Engine(failures=1)
    wait_for_database(engine, attempts=2, retry_seconds=0)
    assert engine.attempts == 2


def test_it_gives_up_after_the_last_attempt_naming_the_cause():
    engine = _Engine(failures=10)
    sleeps: List[float] = []
    with pytest.raises(ConnectionError) as raised:
        wait_for_database(engine, attempts=3, retry_seconds=1, sleep=sleeps.append)
    assert engine.attempts == 3
    assert sleeps == [1, 1]
    assert isinstance(raised.value.__cause__, OSError)
