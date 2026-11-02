# SPDX-License-Identifier: AGPL-3.0-or-later
"""Server-sent event responses that reach the browser as they are written.

A stream passes through the client's proxy (a Next rewrite, nginx, HAProxy) on its way
to the browser. ``no-transform`` stops a compressing proxy from gzipping it, which would
hold its events until a compression block fills (found behind Next's dev server:
nothing arrived); ``X-Accel-Buffering: no`` stops nginx buffering it; ``no-cache`` keeps
every proxy and the browser from caching it.
"""

from collections.abc import AsyncIterator, Iterator

from fastapi.responses import StreamingResponse

EVENT_STREAM_MEDIA_TYPE = "text/event-stream"
EVENT_STREAM_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}


def event_stream(events: AsyncIterator[str] | Iterator[str]) -> StreamingResponse:
    """``events`` (each a complete ``data: ...\\n\\n`` frame or ``:`` comment) as a
    server-sent event response."""
    return StreamingResponse(
        events, media_type=EVENT_STREAM_MEDIA_TYPE, headers=dict(EVENT_STREAM_HEADERS)
    )
