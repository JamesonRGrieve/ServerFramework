# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP response caching decorator.

``@cached_response(ttl=60)`` wraps a FastAPI endpoint handler with
Valkey-backed response caching. On GET requests:

1. Check Valkey for a cached response → return immediately on hit
2. On miss → call the handler, cache the response, return it
3. Respect inbound ``Cache-Control: no-cache / no-store``
4. Add ``ETag`` + ``Cache-Control: max-age=TTL`` to outbound responses
5. Support ``If-None-Match`` → 304 Not Modified

When Valkey is unavailable, the decorator is a transparent pass-through.

Module-level accessor:
    ``set_response_cache(cache)`` / ``get_response_cache()``
    wired by ``EXT_DatabaseMemory.wire_framework_backends()``
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Any, Callable, Optional

_logger = logging.getLogger(__name__)

_response_cache: Any = None


def set_response_cache(cache: Any) -> None:
    """Install the Valkey response cache. Called once at boot."""
    global _response_cache
    _response_cache = cache


def get_response_cache() -> Any:
    """Return the active response cache, or None."""
    return _response_cache


def cached_response(
    ttl: int = 60,
    vary_on_user: bool = True,
) -> Callable:
    """Decorator: cache the full JSON response for GET endpoints.

    Args:
        ttl: Cache TTL in seconds.
        vary_on_user: If True, cache is per-requester (default for
            authenticated endpoints). False for public endpoints.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            cache = get_response_cache()
            if cache is None:
                return await func(*args, **kwargs)

            from fastapi import Request
            from fastapi.responses import JSONResponse

            request = _extract_request(args, kwargs)
            if request is None or request.method != "GET":
                return await func(*args, **kwargs)

            cc = (request.headers.get("cache-control") or "").lower()
            if "no-store" in cc or "no-cache" in cc:
                return await func(*args, **kwargs)

            requester_id = None
            if vary_on_user:
                from zephyrex.lib.RequestContext import get_request_user

                user = get_request_user()
                requester_id = user.get("user_id") if user else None

            sorted_query = "&".join(
                f"{k}={v}" for k, v in sorted(request.query_params.items())
            )

            from zephyrex.extensions.database_memory.ValkeyResponseCache import (
                ValkeyResponseCache,
            )

            key = ValkeyResponseCache.cache_key(
                request.method, request.url.path, sorted_query, requester_id
            )

            try:
                cached = await cache.get(key)
            except Exception:
                cached = None

            if cached is not None:
                etag = cached.get("etag", "")
                if_none_match = request.headers.get("if-none-match", "")
                if if_none_match and if_none_match == etag:
                    return JSONResponse(
                        status_code=304,
                        content=None,
                        headers={
                            "ETag": etag,
                            "Cache-Control": f"max-age={ttl}",
                        },
                    )
                headers = cached.get("headers", {})
                headers["ETag"] = etag
                headers["Cache-Control"] = f"max-age={ttl}"
                headers["X-Cache"] = "HIT"
                return JSONResponse(
                    status_code=cached.get("status_code", 200),
                    content=(
                        json.loads(cached["body"])
                        if isinstance(cached.get("body"), str)
                        else cached.get("body")
                    ),
                    headers=headers,
                )

            result = await func(*args, **kwargs)

            if isinstance(result, JSONResponse):
                body_bytes = result.body
                body_str = (
                    body_bytes.decode()
                    if isinstance(body_bytes, bytes)
                    else str(body_bytes)
                )
            elif hasattr(result, "model_dump"):
                body_str = json.dumps(result.model_dump(mode="json"), default=str)
            elif isinstance(result, (dict, list)):
                body_str = json.dumps(result, default=str)
            else:
                return result

            status_code = getattr(result, "status_code", 200)
            if 200 <= status_code < 300:
                try:
                    await cache.put(key, status_code, body_str, {}, ttl)
                except Exception as exc:
                    _logger.debug("Response cache put failed: %s", exc)

                etag = ValkeyResponseCache.etag_for(body_str)
                if isinstance(result, JSONResponse):
                    result.headers["ETag"] = etag
                    result.headers["Cache-Control"] = f"max-age={ttl}"
                    result.headers["X-Cache"] = "MISS"

            return result

        wrapper._cached_response_ttl = ttl  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _extract_request(args, kwargs):
    """Find the FastAPI Request object from handler args."""
    from fastapi import Request

    for v in kwargs.values():
        if isinstance(v, Request):
            return v
    if "request" in kwargs:
        return kwargs["request"]
    for arg in args:
        if isinstance(arg, Request):
            return arg
    request_dict = kwargs.get("request")
    if isinstance(request_dict, dict) and "_request" in request_dict:
        return request_dict["_request"]
    return None
