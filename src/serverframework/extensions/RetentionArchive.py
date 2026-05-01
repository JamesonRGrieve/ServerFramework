"""Object-storage archive callback adapter for Item 56."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Literal, Sequence


_logger = logging.getLogger(__name__)


def _serialize_jsonl(rows: Sequence[Dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(r, sort_keys=True, default=str) for r in rows)).encode(
        "utf-8"
    )


def _maybe_run(result: Any) -> Any:
    if asyncio.iscoroutine(result):
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            return asyncio.run(result)
        return asyncio.run(result)
    return result


def make_object_storage_archive_callback(
    object_storage: Any,
    *,
    bucket_or_prefix: str,
    compression: Literal["gzip", "none"] = "gzip",
    format: Literal["jsonl"] = "jsonl",
) -> Callable[[Sequence[Dict[str, Any]], str], bool]:
    """Build a `(rows, archive_to) -> bool` archive callback that uploads
    compressed JSONL to the supplied object-storage provider.
    """
    if format != "jsonl":
        raise ValueError(f"unsupported format: {format!r}")
    if compression not in ("gzip", "none"):
        raise ValueError(f"unsupported compression: {compression!r}")

    def _archive(rows: Sequence[Dict[str, Any]], archive_to: str) -> bool:
        try:
            body = _serialize_jsonl(rows)
            if compression == "gzip":
                body = gzip.compress(body)
                ext = "jsonl.gz"
            else:
                ext = "jsonl"

            ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            digest_prefix = hashlib.sha256(body).hexdigest()[:12]
            registration = (archive_to or "default").rstrip("/")
            key = (
                f"{bucket_or_prefix.rstrip('/')}/{registration}/"
                f"{ts_ms}-{len(rows)}-{digest_prefix}.{ext}"
            )

            result = object_storage.upload(key, body)
            _maybe_run(result)
            return True
        except Exception:  # noqa: BLE001
            _logger.exception(
                "object-storage archive failed (archive_to=%s, rows=%d)",
                archive_to,
                len(rows),
            )
            return False

    return _archive


__all__ = ["make_object_storage_archive_callback"]
