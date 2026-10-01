"""Tests for the long-term memory providers.

The SQLite provider is exercised directly against a temporary database file —
store, recall (keyword + recency), recent, agent scoping, and forget.
"""

import os
import uuid

import pytest

from zephyrex.extensions.ai_agents.MemoryProvider import SQLiteMemoryProvider


@pytest.fixture
def provider(tmp_path):
    return SQLiteMemoryProvider(db_path=str(tmp_path / "ltm.db"))


def test_store_and_recent(provider):
    a = str(uuid.uuid4())
    provider.store(a, "the sky is blue")
    provider.store(a, "cats are independent")
    recent = provider.recent(a, limit=10)
    assert len(recent) == 2
    # Most recent first.
    assert recent[0]["content"] == "cats are independent"
    assert all("id" in m and "created_at" in m for m in recent)


def test_recall_keyword(provider):
    a = str(uuid.uuid4())
    provider.store(a, "James prefers concise answers")
    provider.store(a, "the project deadline is Friday")
    hits = provider.recall(a, "deadline")
    assert len(hits) == 1
    assert "deadline" in hits[0]["content"]


def test_recall_empty_query_returns_recent(provider):
    a = str(uuid.uuid4())
    provider.store(a, "one")
    provider.store(a, "two")
    hits = provider.recall(a, "   ", limit=5)
    assert [h["content"] for h in hits] == ["two", "one"]


def test_agent_scoping(provider):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    provider.store(a, "belongs to A")
    provider.store(b, "belongs to B")
    assert [m["content"] for m in provider.recent(a)] == ["belongs to A"]
    assert [m["content"] for m in provider.recent(b)] == ["belongs to B"]
    assert provider.recall(a, "belongs to B") == []


def test_store_with_key_and_metadata(provider):
    a = str(uuid.uuid4())
    mid = provider.store(a, "content here", key="fact-1", metadata={"src": "test"})
    assert isinstance(mid, str) and mid
    hit = provider.recall(a, "fact-1")  # key is searched too
    assert len(hit) == 1
    assert hit[0]["key"] == "fact-1"
    assert hit[0]["metadata"] == {"src": "test"}


def test_forget(provider):
    a = str(uuid.uuid4())
    mid = provider.store(a, "temporary")
    assert provider.forget(a, mid) is True
    assert provider.recent(a) == []
    # Second delete is a no-op.
    assert provider.forget(a, mid) is False


def test_forget_is_agent_scoped(provider):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    mid = provider.store(a, "A's memory")
    # b cannot delete a's memory.
    assert provider.forget(b, mid) is False
    assert len(provider.recent(a)) == 1
