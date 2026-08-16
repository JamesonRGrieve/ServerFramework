"""
Pagination homogenization for external API list endpoints.

Item 7 — Pagination homogenization.

Internal list contracts speak offset/limit; external APIs speak cursor,
page-token, link-header, or offset. A `Paginator` translates between
the two and round-trips opaque cursor state through a base64-encoded
JSON envelope on the `next_token` field of `Pagination`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from zephyrex.extensions.ExternalErrors import InvalidPaginationError


# ---------------------------------------------------------------------------
# Pagination model
# ---------------------------------------------------------------------------


class Pagination(BaseModel):
    """Typed pagination envelope returned alongside list responses.

    `supports_random_access` lets clients adapt their UI: cursor-only
    upstreams cannot honor "jump to page 100" without walking 1-99, so
    callers should hide page-number controls when this flag is False.
    """

    model_config = ConfigDict(extra="forbid")

    offset: Optional[int] = Field(default=None, description="Logical offset; meaningful only on offset-style providers.")
    limit: Optional[int] = Field(default=None, description="Page size requested.")
    next_token: Optional[str] = Field(default=None, description="Opaque cursor for the next page, or None on the last page.")
    total: Optional[int] = Field(default=None, description="Total result count if the upstream reports it; None otherwise.")
    has_more: bool = Field(default=False, description="True if at least one more page exists.")
    supports_random_access: bool = Field(default=False, description="True for offset-style upstreams; False for cursor / page-token / link-header.")


# ---------------------------------------------------------------------------
# Token envelope
# ---------------------------------------------------------------------------


def query_hash(query_params: Optional[Dict[str, Any]]) -> str:
    """Stable hash of query parameters for tripwire detection.

    A `next_token` whose embedded `query_hash` does not match the new
    query parameters indicates the client changed filters mid-pagination
    and would otherwise receive misaligned results.
    """

    if not query_params:
        canonical = "{}"
    else:
        canonical = json.dumps(query_params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _token_hmac_key() -> bytes:
    from zephyrex.lib.Environment import env
    secret = env("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET must be set for pagination token signing")
    return secret.encode("utf-8")


def encode_token(provider_cursor: Any, page_size: Optional[int], q_hash: str) -> str:
    """Encode an opaque cursor envelope as a signed base64 JSON string."""

    payload = {
        "provider_cursor": provider_cursor,
        "page_size": page_size,
        "query_hash": q_hash,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(_token_hmac_key(), raw, "sha256").hexdigest()[:16]
    return f"{encoded}.{sig}"


def decode_token(token: str, expected_query_hash: Optional[str] = None) -> Dict[str, Any]:
    """Decode a token produced by `encode_token`.

    When `expected_query_hash` is provided, raises
    `InvalidPaginationError` if the embedded `query_hash` does not match.
    """

    if not token:
        raise InvalidPaginationError("Empty pagination token")
    try:
        if "." in token:
            encoded_part, sig = token.rsplit(".", 1)
        else:
            encoded_part = token
            sig = None
        padded = encoded_part + "=" * (-len(encoded_part) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        if sig is not None:
            expected_sig = hmac.new(_token_hmac_key(), raw, "sha256").hexdigest()[:16]
            if not hmac.compare_digest(sig, expected_sig):
                raise InvalidPaginationError("Pagination token signature invalid")
        payload = json.loads(raw)
    except InvalidPaginationError:
        raise
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidPaginationError(f"Malformed pagination token: {exc}") from exc

    if not isinstance(payload, dict):
        raise InvalidPaginationError("Pagination token is not a JSON object")

    if expected_query_hash is not None:
        if payload.get("query_hash") != expected_query_hash:
            raise InvalidPaginationError(
                "Pagination token query_hash does not match the current query; "
                "filters changed mid-pagination."
            )

    return payload


# ---------------------------------------------------------------------------
# Paginator hierarchy
# ---------------------------------------------------------------------------


class AbstractPaginator(ABC):
    """Per-provider pagination translator.

    Each subclass owns one contract: convert the internal offset/limit
    request to upstream parameters (`to_external_params`) and convert
    the upstream response cursor back to a `next_token` plus the items
    list (`from_external_response`).
    """

    supports_random_access: ClassVar[bool] = False

    @abstractmethod
    def to_external_params(
        self,
        *,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
        query_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return the provider-shaped query-parameter dict."""

    @abstractmethod
    def from_external_response(
        self,
        response: Any,
        *,
        query_params: Optional[Dict[str, Any]] = None,
        page_size: Optional[int] = None,
    ) -> Tuple[List[Any], Optional[str]]:
        """Return `(items, next_token)`; next_token is None on the last page."""

    def build_pagination(
        self,
        next_token: Optional[str],
        *,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        total: Optional[int] = None,
    ) -> Pagination:
        """Build a `Pagination` envelope reflecting this paginator's
        capabilities. The `supports_random_access` flag is taken from the
        paginator class so callers can hide page-number controls when the
        upstream is cursor-based."""

        return Pagination(
            offset=offset,
            limit=limit,
            next_token=next_token,
            total=total,
            has_more=bool(next_token),
            supports_random_access=self.supports_random_access,
        )


class OffsetPaginator(AbstractPaginator):
    """Trivial offset/limit -> offset/limit identity translator."""

    supports_random_access: ClassVar[bool] = True

    def to_external_params(
        self,
        *,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
        query_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = dict(query_params or {})
        if next_token:
            envelope = decode_token(next_token, query_hash(query_params))
            params["offset"] = envelope.get("provider_cursor", 0)
            if envelope.get("page_size") is not None:
                params["limit"] = envelope["page_size"]
        else:
            if offset is not None:
                params["offset"] = offset
            if limit is not None:
                params["limit"] = limit
        return params

    def from_external_response(
        self,
        response: Any,
        *,
        query_params: Optional[Dict[str, Any]] = None,
        page_size: Optional[int] = None,
    ) -> Tuple[List[Any], Optional[str]]:
        # Accept either a raw list or a dict with `data`/`items`.
        if isinstance(response, list):
            items = response
            total = None
        else:
            items = response.get("data") or response.get("items") or []
            total = response.get("total")

        if not items or (page_size is not None and len(items) < page_size):
            return list(items), None

        # Compute the next offset from the previous offset + page size.
        prev_offset = (response.get("offset") if isinstance(response, dict) else 0) or 0
        next_offset = prev_offset + len(items)
        if total is not None and next_offset >= total:
            return list(items), None

        token = encode_token(next_offset, page_size, query_hash(query_params))
        return list(items), token


class CursorPaginator(AbstractPaginator):
    """Stripe-style cursor paginator using `starting_after` /
    `ending_before` semantics.

    Configure the parameter names via the constructor when an upstream
    uses different names.
    """

    supports_random_access: ClassVar[bool] = False

    def __init__(
        self,
        starting_after_param: str = "starting_after",
        ending_before_param: str = "ending_before",
        limit_param: str = "limit",
        cursor_field: str = "id",
        has_more_field: str = "has_more",
    ) -> None:
        self.starting_after_param = starting_after_param
        self.ending_before_param = ending_before_param
        self.limit_param = limit_param
        self.cursor_field = cursor_field
        self.has_more_field = has_more_field

    def to_external_params(
        self,
        *,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
        query_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = dict(query_params or {})
        if limit is not None:
            params[self.limit_param] = limit
        if next_token:
            envelope = decode_token(next_token, query_hash(query_params))
            cursor = envelope.get("provider_cursor")
            if cursor is not None:
                params[self.starting_after_param] = cursor
        return params

    def from_external_response(
        self,
        response: Any,
        *,
        query_params: Optional[Dict[str, Any]] = None,
        page_size: Optional[int] = None,
    ) -> Tuple[List[Any], Optional[str]]:
        if isinstance(response, list):
            items = response
            has_more = bool(items) and (page_size is not None and len(items) >= page_size)
        else:
            items = response.get("data") or response.get("items") or []
            has_more = bool(response.get(self.has_more_field, len(items) > 0 and page_size is not None and len(items) >= page_size))

        if not items or not has_more:
            return list(items), None

        last = items[-1]
        if isinstance(last, dict):
            cursor = last.get(self.cursor_field)
        else:
            cursor = getattr(last, self.cursor_field, None)
        if cursor is None:
            return list(items), None

        token = encode_token(cursor, page_size, query_hash(query_params))
        return list(items), token


class PageTokenPaginator(AbstractPaginator):
    """Google-style page-token paginator.

    The upstream returns a `next_page_token` field; we pass it back as
    `page_token` on the next request.
    """

    supports_random_access: ClassVar[bool] = False

    def __init__(
        self,
        page_token_param: str = "page_token",
        next_page_token_field: str = "next_page_token",
        page_size_param: str = "page_size",
    ) -> None:
        self.page_token_param = page_token_param
        self.next_page_token_field = next_page_token_field
        self.page_size_param = page_size_param

    def to_external_params(
        self,
        *,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
        query_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = dict(query_params or {})
        if limit is not None:
            params[self.page_size_param] = limit
        if next_token:
            envelope = decode_token(next_token, query_hash(query_params))
            cursor = envelope.get("provider_cursor")
            if cursor is not None:
                params[self.page_token_param] = cursor
        return params

    def from_external_response(
        self,
        response: Any,
        *,
        query_params: Optional[Dict[str, Any]] = None,
        page_size: Optional[int] = None,
    ) -> Tuple[List[Any], Optional[str]]:
        if isinstance(response, list):
            return list(response), None
        items = response.get("data") or response.get("items") or []
        next_page_token = response.get(self.next_page_token_field)
        if not next_page_token:
            return list(items), None
        token = encode_token(next_page_token, page_size, query_hash(query_params))
        return list(items), token


class LinkHeaderPaginator(AbstractPaginator):
    """RFC-5988 `Link` header paginator (GitHub-style).

    The upstream response is expected to carry a `headers` mapping (or
    a `_headers` attribute) with a `Link` entry; the next URL is
    extracted and stored verbatim in the cursor envelope.
    """

    supports_random_access: ClassVar[bool] = False

    def __init__(
        self,
        page_param: str = "page",
        per_page_param: str = "per_page",
    ) -> None:
        self.page_param = page_param
        self.per_page_param = per_page_param

    def to_external_params(
        self,
        *,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
        query_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = dict(query_params or {})
        if limit is not None:
            params[self.per_page_param] = limit
        if next_token:
            envelope = decode_token(next_token, query_hash(query_params))
            cursor = envelope.get("provider_cursor")
            if cursor is not None:
                # The cursor is the next page URL; let the caller route on it.
                params["__next_url__"] = cursor
        elif offset is not None and limit:
            # Translate offset to a 1-based page number.
            page = (offset // limit) + 1
            params[self.page_param] = page
        return params

    def from_external_response(
        self,
        response: Any,
        *,
        query_params: Optional[Dict[str, Any]] = None,
        page_size: Optional[int] = None,
    ) -> Tuple[List[Any], Optional[str]]:
        # Accept (items, headers) tuple, or dict with `data` + `headers`.
        headers: Dict[str, Any] = {}
        if isinstance(response, tuple) and len(response) == 2:
            items, headers = response
        elif isinstance(response, dict):
            items = response.get("data") or response.get("items") or []
            headers = response.get("headers") or {}
        else:
            items = list(response or [])

        link_header = headers.get("Link") or headers.get("link") or ""
        next_url = self._parse_next_link(link_header)
        if not next_url:
            return list(items), None
        token = encode_token(next_url, page_size, query_hash(query_params))
        return list(items), token

    @staticmethod
    def _parse_next_link(link_header: str) -> Optional[str]:
        if not link_header:
            return None
        # Naive RFC-5988 parser sufficient for next-link extraction.
        for chunk in link_header.split(","):
            chunk = chunk.strip()
            if 'rel="next"' in chunk:
                start = chunk.find("<")
                end = chunk.find(">")
                if start != -1 and end != -1 and end > start:
                    return chunk[start + 1 : end]
        return None


__all__ = [
    "Pagination",
    "AbstractPaginator",
    "OffsetPaginator",
    "CursorPaginator",
    "PageTokenPaginator",
    "LinkHeaderPaginator",
    "encode_token",
    "decode_token",
    "query_hash",
]
