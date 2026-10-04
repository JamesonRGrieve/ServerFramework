# SPDX-License-Identifier: AGPL-3.0-or-later
"""A request body read whole, but never past a size cap.

An endpoint that needs the raw body in memory (a signed webhook, a raw
message) reads it with :func:`capped_body`: one whose declared length is
over the cap is refused unread, and one sent without a length (chunked
transfer) is refused as soon as what has arrived passes the cap, so an
oversized body never sits in memory before its 413.
"""

from typing import List

from fastapi import HTTPException, Request


def too_large(detail: str) -> HTTPException:
    """The 413 a body over its endpoint's cap gets."""
    return HTTPException(status_code=413, detail=detail)


async def capped_body(request: Request, limit: int, too_large_detail: str) -> bytes:
    """The request body, refused (413, ``too_large_detail``) as soon as it
    is longer than ``limit`` bytes: by its declared length, or as it streams
    in."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_large(too_large_detail)
    chunks: List[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise too_large(too_large_detail)
        chunks.append(chunk)
    return b"".join(chunks)
