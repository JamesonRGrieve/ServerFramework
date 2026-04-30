"""Unit tests for rate-limit primitives (Item 17)."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from serverframework.extensions.RateLimit import (
    ConcurrencyLimit,
    DistributedRateLimit,
    RateLimit,
    TokenBucket,
    parse_retry_after,
)


@pytest.mark.unit
def test_token_bucket_initial_burst_acquirable():
    b = TokenBucket(RateLimit(rps=10, burst=5))
    for _ in range(5):
        assert b.try_acquire() is True
    assert b.try_acquire() is False


@pytest.mark.unit
def test_token_bucket_refills_over_time():
    b = TokenBucket(RateLimit(rps=100, burst=2))
    assert b.try_acquire()
    assert b.try_acquire()
    assert not b.try_acquire()
    time.sleep(0.05)  # 50ms -> ~5 tokens (capped at burst=2)
    assert b.try_acquire()


@pytest.mark.unit
def test_token_bucket_acquire_blocking_succeeds_within_timeout():
    b = TokenBucket(RateLimit(rps=50, burst=1))
    assert b.acquire_blocking(timeout=0.5)
    t0 = time.monotonic()
    assert b.acquire_blocking(timeout=0.5)
    assert time.monotonic() - t0 >= 0.015


@pytest.mark.unit
def test_token_bucket_acquire_blocking_returns_false_on_timeout():
    b = TokenBucket(RateLimit(rps=1, burst=1))
    assert b.acquire_blocking(timeout=0.1)
    assert not b.acquire_blocking(timeout=0.05)


@pytest.mark.unit
def test_token_bucket_invalid_args():
    with pytest.raises(ValueError):
        TokenBucket(RateLimit(rps=0, burst=1))
    with pytest.raises(ValueError):
        TokenBucket(RateLimit(rps=1, burst=0))


@pytest.mark.unit
def test_concurrency_limit_blocks_when_full():
    c = ConcurrencyLimit(2)
    assert c.acquire(timeout=0.1)
    assert c.acquire(timeout=0.1)
    assert not c.acquire(timeout=0.05)
    c.release()
    assert c.acquire(timeout=0.1)
    c.release(); c.release()


@pytest.mark.unit
def test_concurrency_limit_context_manager():
    c = ConcurrencyLimit(1)
    with c:
        assert not c.acquire(timeout=0.05)
    assert c.acquire(timeout=0.05)
    c.release()


@pytest.mark.unit
def test_concurrency_limit_invalid():
    with pytest.raises(ValueError):
        ConcurrencyLimit(0)


@pytest.mark.unit
def test_parse_retry_after_seconds():
    assert parse_retry_after("0") == 0
    assert parse_retry_after("30") == 30
    assert parse_retry_after("  60  ") == 60


@pytest.mark.unit
def test_parse_retry_after_http_date_future():
    future = datetime.now(timezone.utc) + timedelta(seconds=120)
    s = format_datetime(future, usegmt=True)
    delay = parse_retry_after(s)
    assert delay is not None and 100 <= delay <= 130


@pytest.mark.unit
def test_parse_retry_after_http_date_past_returns_zero():
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    s = format_datetime(past, usegmt=True)
    assert parse_retry_after(s) == 0


@pytest.mark.unit
def test_parse_retry_after_invalid_returns_none():
    assert parse_retry_after("") is None
    assert parse_retry_after(None) is None
    assert parse_retry_after("not-a-date-or-int") is None


@pytest.mark.unit
def test_distributed_rate_limit_carries_counter_key():
    d = DistributedRateLimit(RateLimit(rps=5, burst=1), counter_key="provider:stripe")
    assert d.counter_key == "provider:stripe"
    assert d.try_acquire()
