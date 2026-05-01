"""Tests for :mod:`extensions.RetentionArchive` (Item 56 — task 1)."""

from __future__ import annotations

import gzip
import json
from typing import Any, Dict, List, Optional, Tuple

import pytest

from serverframework.extensions.RetentionArchive import (
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
    callback = make_object_storage_archive_callback(
        storage, bucket_or_prefix="root"
    )

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
    callback = make_object_storage_archive_callback(
        storage, bucket_or_prefix="audit"
    )

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


def test_archive_callback_integrates_with_retention_service():
    """Smoke test: archive callback wired into RetentionService archives + purges."""
    from datetime import datetime, timedelta, timezone

    from serverframework.extensions.RetentionPolicy import RetentionPolicy
    from serverframework.extensions.RetentionService import (
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
