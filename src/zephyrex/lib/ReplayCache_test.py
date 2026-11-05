"""Tests for the ReplayCache primitive."""

import os
import time

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "replay_cache_test")

from zephyrex.lib.ReplayCache import (
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


class TestMarkIfUnused:
    """H-7 — atomic CAS used by OAuth state verification."""

    def test_first_caller_wins(self):
        cache = InMemoryReplayCache()
        assert cache.mark_if_unused("oauth-state", ttl_seconds=60) is True

    def test_second_caller_loses(self):
        cache = InMemoryReplayCache()
        assert cache.mark_if_unused("oauth-state", ttl_seconds=60) is True
        assert cache.mark_if_unused("oauth-state", ttl_seconds=60) is False

    def test_after_expiry_a_new_caller_wins(self):
        cache = InMemoryReplayCache()
        assert cache.mark_if_unused("k", ttl_seconds=0) is True
        time.sleep(0.001)
        assert cache.mark_if_unused("k", ttl_seconds=60) is True

    def test_concurrent_callers_only_one_wins(self):
        # Verify the in-process lock makes the CAS race-free even when
        # called from many threads. Without ``mark_if_unused``, two
        # readers could both observe ``not is_used`` and both proceed.
        import threading

        cache = InMemoryReplayCache()
        wins = []

        def attempt():
            if cache.mark_if_unused("shared", ttl_seconds=60):
                wins.append(True)

        threads = [threading.Thread(target=attempt) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(wins) == 1


class TestBaseDefaultMarkIfUnused:
    """The abstract base provides a default ``mark_if_unused`` that is
    correct on a single-threaded caller via the in-memory implementation."""

    def test_default_uses_is_used_then_mark_used(self):
        cache = InMemoryReplayCache()
        # Burn via the default path.
        assert cache.mark_if_unused("alpha", ttl_seconds=60) is True
        assert cache.is_used("alpha") is True
        # Second caller refused.
        assert cache.mark_if_unused("alpha", ttl_seconds=60) is False


class TestBound:
    """#282: ``max_entries`` was not a cap. Past it, a sweep removed only
    expired keys, so live keys grew without bound and every later write
    scanned the whole cache under the lock."""

    def test_live_entries_never_exceed_the_bound(self):
        cache = InMemoryReplayCache(max_entries=50)
        for n in range(500):
            assert cache.mark_if_unused(f"k{n}", ttl_seconds=3600)
        assert len(cache._impl) == 50
        assert len(cache._deadlines) <= 2 * max(len(cache._impl), 1024)

    def test_the_entries_closest_to_expiry_are_evicted_first(self):
        cache = InMemoryReplayCache(max_entries=3)
        cache.mark_used("soon", ttl_seconds=10)
        cache.mark_used("later", ttl_seconds=1000)
        cache.mark_used("latest", ttl_seconds=2000)
        cache.mark_used("new", ttl_seconds=500)
        assert not cache.is_used("soon")
        assert all(cache.is_used(k) for k in ("later", "latest", "new"))

    def test_expired_entries_go_before_any_live_one(self):
        cache = InMemoryReplayCache(max_entries=3)
        cache.mark_used("expired", ttl_seconds=-1)
        cache.mark_used("a", ttl_seconds=1000)
        cache.mark_used("b", ttl_seconds=1000)
        cache.mark_used("c", ttl_seconds=1000)
        assert "expired" not in cache._impl
        assert all(cache.is_used(k) for k in ("a", "b", "c"))

    def test_re_marking_a_key_keeps_its_new_deadline(self):
        cache = InMemoryReplayCache(max_entries=2)
        cache.mark_used("k", ttl_seconds=10)
        cache.mark_used("k", ttl_seconds=1000)
        cache.mark_used("other", ttl_seconds=500)
        cache.mark_used("third", ttl_seconds=2000)
        # The stale 10 s entry for "k" must not evict its live 1000 s one.
        assert cache.is_used("k") and cache.is_used("third")
        assert not cache.is_used("other")

    def test_stale_heap_entries_are_compacted(self):
        cache = InMemoryReplayCache(max_entries=10)
        for _ in range(5000):
            cache.mark_used("same", ttl_seconds=1000)
        assert len(cache._deadlines) <= 2 * 1024
