# SPDX-License-Identifier: AGPL-3.0-or-later
"""A capped body, read off real ASGI requests: refused unread past its
declared length, and refused as it streams in past the cap."""

from typing import Any, Dict, List

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from zephyrex.lib.RequestBody import capped_body

LIMIT = 1000
TOO_LARGE = "A body is at most 1000 bytes"


def request_of(chunks: List[bytes], headers: List[Any], read: List[bytes]) -> Request:
    """A POST whose body arrives as ``chunks``; each one read is recorded
    in ``read``."""

    async def receive() -> Dict[str, Any]:
        chunk = chunks.pop(0)
        read.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": headers}, receive
    )


async def test_a_body_within_the_cap_is_read_whole() -> None:
    read: List[bytes] = []
    request = request_of([b"x" * 600, b"y" * 400], [], read)
    assert await capped_body(request, LIMIT, TOO_LARGE) == b"x" * 600 + b"y" * 400


async def test_a_body_streaming_past_the_cap_is_refused_as_it_arrives() -> None:
    """No Content-Length: the cap holds as the chunks come in."""
    read: List[bytes] = []
    request = request_of([b"x" * 600, b"x" * 600, b"never read"], [], read)
    with pytest.raises(HTTPException) as refused:
        await capped_body(request, LIMIT, TOO_LARGE)
    assert (refused.value.status_code, refused.value.detail) == (413, TOO_LARGE)
    assert b"never read" not in read


async def test_a_declared_length_past_the_cap_is_refused_unread() -> None:
    read: List[bytes] = []
    request = request_of(
        [b"x" * (LIMIT + 1)], [(b"content-length", str(LIMIT + 1).encode())], read
    )
    with pytest.raises(HTTPException) as refused:
        await capped_body(request, LIMIT, TOO_LARGE)
    assert (refused.value.status_code, refused.value.detail) == (413, TOO_LARGE)
    assert read == []
