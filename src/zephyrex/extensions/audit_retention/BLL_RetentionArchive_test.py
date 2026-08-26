"""Tests for :mod:`extensions.audit_retention.BLL_RetentionArchive` (Item 56 — task 1)."""

from __future__ import annotations

import asyncio
import gzip
import json
from typing import Any, Dict, List, Optional, Tuple

import pytest

from zephyrex.extensions.audit_retention.BLL_RetentionArchive import (
    make_object_storage_archive_callback,
)


class _FakeObjectStorage:
    """Records uploads. ``fail`` toggles raising in :meth:`upload`."""

    def __init__(self, fail: bool = False) -> None:
        self.uploads: List[Tuple[str, bytes]] = []
        self.fail = fail

    def upload(self, key: str, body: bytes, content_type: Optional[str] = None) -> str:
        if self.fail:
            raise RuntimeError("upload boom")
        self.uploads.append((key, body))
        return f"fake://{key}"


def test_archive_callback_uploads_gzip_jsonl_and_returns_true():
    storage = _FakeObjectStorage()
    callback = make_object_storage_archive_callback(
        storage, bucket_or_prefix="audit-archive"
    )

    rows: List[Dict[str, Any]] = [
        {"id": 1, "msg": "alpha"},
        {"id": 2, "msg": "beta"},
    ]
    ok = callback(rows, "audit_login")

    assert ok is True
    assert len(storage.uploads) == 1
    key, body = storage.uploads[0]
    assert key.startswith("audit-archive/audit_login/")
    assert key.endswith(".jsonl.gz")
    decoded = gzip.decompress(body).decode("utf-8")
    lines = decoded.split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"id": 1, "msg": "alpha"}
    assert json.loads(lines[1]) == {"id": 2, "msg": "beta"}


def test_archive_callback_no_compression_uses_jsonl_extension():
    storage = _FakeObjectStorage()
    callback = make_object_storage_archive_callback(
        storage, bucket_or_prefix="prefix", compression="none"
    )

    rows = [{"a": 1}]
    ok = callback(rows, "audit_login")

    assert ok is True
    key, body = storage.uploads[0]
    assert key.endswith(".jsonl")
    assert b".gz" not in key.encode()
    assert body.decode("utf-8") == '{"a": 1}'


def test_archive_callback_key_includes_registration_and_timestamp():
    storage = _FakeObjectStorage()
    callback = make_object_storage_archive_callback(storage, bucket_or_prefix="root")

    callback([{"x": 1}], "audit_class_x")

    key = storage.uploads[0][0]
    parts = key.split("/")
    assert parts[0] == "root"
    assert parts[1] == "audit_class_x"
    # filename: {ts_ms}-{rowcount}-{digest}.jsonl.gz
    filename = parts[2]
    timestamp = filename.split("-")[0]
    assert timestamp.isdigit()
    assert len(timestamp) >= 13  # ms precision since 1970


def test_archive_callback_returns_false_on_upload_failure():
    storage = _FakeObjectStorage(fail=True)
    callback = make_object_storage_archive_callback(storage, bucket_or_prefix="audit")

    ok = callback([{"id": 1}], "audit_login")

    assert ok is False
    assert storage.uploads == []


def test_archive_callback_serializes_with_sorted_keys():
    storage = _FakeObjectStorage()
    callback = make_object_storage_archive_callback(
        storage, bucket_or_prefix="audit", compression="none"
    )

    callback([{"z": 1, "a": 2, "m": 3}], "x")

    body = storage.uploads[0][1].decode("utf-8")
    assert body == '{"a": 2, "m": 3, "z": 1}'


def test_archive_callback_default_serializer_handles_nonjson_types():
    from datetime import datetime

    storage = _FakeObjectStorage()
    callback = make_object_storage_archive_callback(
        storage, bucket_or_prefix="audit", compression="none"
    )

    ok = callback([{"ts": datetime(2026, 1, 1)}], "x")

    assert ok is True
    body = storage.uploads[0][1].decode("utf-8")
    assert "2026-01-01" in body


def test_unsupported_format_raises():
    storage = _FakeObjectStorage()
    with pytest.raises(ValueError):
        make_object_storage_archive_callback(
            storage, bucket_or_prefix="x", format="csv"  # type: ignore[arg-type]
        )


def test_unsupported_compression_raises():
    storage = _FakeObjectStorage()
    with pytest.raises(ValueError):
        make_object_storage_archive_callback(
            storage, bucket_or_prefix="x", compression="zstd"  # type: ignore[arg-type]
        )


class _AsyncFakeObjectStorage:
    """Object-storage provider whose ``upload`` is a coroutine (async provider)."""

    def __init__(self) -> None:
        self.uploads: List[Tuple[str, bytes]] = []

    async def upload(
        self, key: str, body: bytes, content_type: Optional[str] = None
    ) -> str:
        self.uploads.append((key, body))
        return f"fake://{key}"


def test_archive_callback_drives_async_upload_without_running_loop():
    """An async-provider upload is driven to completion when no loop is running."""
    storage = _AsyncFakeObjectStorage()
    callback = make_object_storage_archive_callback(
        storage, bucket_or_prefix="audit-archive"
    )

    ok = callback([{"id": 1}], "audit_login")

    assert ok is True
    assert len(storage.uploads) == 1


def test_archive_callback_succeeds_under_running_event_loop():
    """Regression (Item 56): the archive callback previously called
    ``asyncio.run`` under a running loop, which raises "cannot be called from a
    running event loop". That exception was swallowed by ``_archive`` and the
    callback returned False, silently failing every scheduled archive. Routing
    through ``_cache_sync_run`` drives the async upload on a worker thread so the
    callback now succeeds when invoked from within an async context.
    """
    storage = _AsyncFakeObjectStorage()
    callback = make_object_storage_archive_callback(
        storage, bucket_or_prefix="audit-archive"
    )
    rows = [{"id": 1}, {"id": 2}]

    async def _driver():
        # Invoked from within a running event loop, exactly as the retention
        # pass runs when scheduled inside an async context.
        return callback(rows, "audit_login")

    ok = asyncio.run(_driver())

    assert ok is True
    assert len(storage.uploads) == 1
    key, _body = storage.uploads[0]
    assert key.startswith("audit-archive/audit_login/")


def test_retention_service_purges_under_running_loop_with_async_archive():
    """End-to-end regression: with an async object-storage provider, running the
    retention pass from within an event loop archives AND purges. Before the fix
    the archive callback returned False under the running loop, so the service
    set ``skipped_due_to_archive_failure`` and refused to purge the expired rows.
    """
    from datetime import datetime, timedelta, timezone

    from zephyrex.extensions.audit_retention.BLL_RetentionPolicy import RetentionPolicy
    from zephyrex.extensions.audit_retention.BLL_RetentionService import (
        RetentionRegistration,
        RetentionService,
    )

    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    storage = _AsyncFakeObjectStorage()
    archive_cb = make_object_storage_archive_callback(
        storage, bucket_or_prefix="archive"
    )

    rows = [
        {"id": f"r-{i}", "created_at": (now - timedelta(days=90)).isoformat()}
        for i in range(3)
    ]
    deleted: List[Dict[str, Any]] = []

    def expired_query(_cutoff):
        return list(rows)

    def delete(rs):
        deleted.extend(rs)
        return len(rs)

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_login",
                policy=RetentionPolicy(window="30d", archive_to="audit_login"),
                expired_query=expired_query,
                delete=delete,
                archive=archive_cb,
            )
        ],
        clock=lambda: now,
    )

    async def _driver():
        return svc.run_pass()

    [report] = asyncio.run(_driver())

    assert report.skipped_due_to_archive_failure == 0
    assert report.archived_count == 3
    assert report.purged_count == 3
    assert len(deleted) == 3
    assert len(storage.uploads) == 1


def test_archive_callback_integrates_with_retention_service():
    """Smoke test: archive callback wired into RetentionService archives + purges."""
    from datetime import datetime, timedelta, timezone

    from zephyrex.extensions.audit_retention.BLL_RetentionPolicy import RetentionPolicy
    from zephyrex.extensions.audit_retention.BLL_RetentionService import (
        RetentionRegistration,
        RetentionService,
    )

    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    storage = _FakeObjectStorage()
    archive_cb = make_object_storage_archive_callback(
        storage, bucket_or_prefix="archive"
    )

    rows = [
        {"id": f"r-{i}", "created_at": (now - timedelta(days=90)).isoformat()}
        for i in range(3)
    ]
    deleted: List[Dict[str, Any]] = []

    def expired_query(_cutoff):
        return list(rows)

    def delete(rs):
        deleted.extend(rs)
        return len(rs)

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_login",
                policy=RetentionPolicy(window="30d", archive_to="audit_login"),
                expired_query=expired_query,
                delete=delete,
                archive=archive_cb,
            )
        ],
        clock=lambda: now,
    )

    [report] = svc.run_pass()

    assert report.archived_count == 3
    assert report.purged_count == 3
    assert len(storage.uploads) == 1
    assert "audit_login" in storage.uploads[0][0]
