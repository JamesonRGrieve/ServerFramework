# SPDX-License-Identifier: AGPL-3.0-or-later
"""A request body read whole, but never past a size cap.

An endpoint that needs the raw body in memory (a signed webhook, a raw
message) reads it with :func:`capped_body`: one whose declared length is
over the cap is refused unread, and one sent without a length (chunked
transfer) is refused as soon as what has arrived passes the cap, so an
oversized body never sits in memory before its 413.
"""

from typing import Any, Iterable, List, Mapping, Optional

from fastapi import HTTPException, Request

# Set on a route's endpoint by ``register_custom_routes`` when its
# ``@custom_route`` declares ``max_body_bytes``: the most that route accepts.
ROUTE_BODY_LIMIT_ATTRIBUTE = "__zephyrex_max_body_bytes__"


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


def _route_path(scope: Mapping[str, Any]) -> str:
    """The path routes are matched against: the request's, less the root path
    it was served under (as Starlette routes it)."""
    path = str(scope.get("path", ""))
    root = str(scope.get("root_path", ""))
    if root and (path == root or path.startswith(root + "/")):
        return path[len(root) :] or "/"
    return path


def route_body_limit(scope: Mapping[str, Any]) -> Optional[int]:
    """The body cap the route this request is for declares (its
    ``@custom_route`` ``max_body_bytes``), or None: matched by method and
    path against the app's routes, included routers flattened as FastAPI
    flattens them for OpenAPI, from the app Starlette puts in the scope
    before its middleware runs."""
    routes = getattr(getattr(scope.get("app"), "router", None), "routes", ())
    try:
        from fastapi.routing import iter_route_contexts

        candidates: Iterable[Any] = iter_route_contexts(routes)
    except ImportError:  # FastAPI before route contexts: included routes are flat.
        candidates = routes
    method = str(scope.get("method", ""))
    path = _route_path(scope)
    for route in candidates:
        cap = getattr(
            getattr(route, "endpoint", None), ROUTE_BODY_LIMIT_ATTRIBUTE, None
        )
        pattern = getattr(route, "path_regex", None)
        if (
            isinstance(cap, int)
            and pattern is not None
            and method in (getattr(route, "methods", None) or ())
        ):
            if pattern.match(path):
                return cap
    return None
