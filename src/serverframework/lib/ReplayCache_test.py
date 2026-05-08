"""Tests for the ReplayCache primitive."""

import os
import time

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "replay_cache_test")

from serverframework.lib.ReplayCache import (
    InMemoryReplayCache,
    ReplayCache,
    get_replay_cache,
    set_replay_cache,
)


class TestInMemoryReplayCache:
    def test_unmarked_is_not_used(self):
        cache = InMemoryReplayCache()
        assert cache.is_used("never-marked") is False

    def test_marked_is_used(self):
        cache = InMemoryReplayCache()
        cache.mark_used("k", ttl_seconds=60)
        assert cache.is_used("k") is True

    def test_distinct_keys_independent(self):
        cache = InMemoryReplayCache()
        cache.mark_used("a", ttl_seconds=60)
        assert cache.is_used("b") is False

    def test_ttl_zero_expires_immediately(self):
        # A TTL of 0 means the entry is immediately expired on the next
        # check; useful for "burn on read" semantics in callers that
        # track consumption separately.
        cache = InMemoryReplayCache()
        cache.mark_used("burn", ttl_seconds=0)
        # ``time.time()`` may return the same float across the two calls
        # in fast environments; sleep a microsecond to advance the clock.
        time.sleep(0.001)
        assert cache.is_used("burn") is False

    def test_ttl_distinguishes_short_from_long(self):
        # Short TTL expires while long TTL still holds.
        cache = InMemoryReplayCache()
        cache.mark_used("short", ttl_seconds=0)
        cache.mark_used("long", ttl_seconds=60)
        time.sleep(0.001)
        assert cache.is_used("short") is False
        assert cache.is_used("long") is True


class TestGlobalBinding:
    def test_set_and_get(self):
        custom = InMemoryReplayCache()
        set_replay_cache(custom)
        assert get_replay_cache() is custom

    def test_default_is_in_memory(self):
        # Reset to None by installing a fresh instance, then access via get
        set_replay_cache(InMemoryReplayCache())
        assert isinstance(get_replay_cache(), ReplayCache)
