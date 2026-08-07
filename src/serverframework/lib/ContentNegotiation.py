"""
Multi-format content negotiation for REST and MCP endpoints.

Supports five serialization formats via Content-Type (request parsing) and
Accept (response serialization) headers, plus format-suffix URL routing:

1. TOON  (application/toon)  -- compact, token-efficient LLM format
2. JSON  (application/json)  -- default, backward-compatible
3. YAML  (application/yaml, application/x-yaml)
4. TOML  (application/toml)
5. XML   (application/xml, text/xml)

Usage::

    from serverframework.lib.ContentNegotiation import ContentNegotiationMiddleware

    app.add_middleware(ContentNegotiationMiddleware)

Clients request a format in three (prioritized) ways:

1. URL suffix -- ``/items.toon``, ``/items.yaml``, etc.
2. ``Accept`` header -- standard HTTP content negotiation.
3. Default -- ``application/json`` when neither is present.
"""

from __future__ import annotations

import json
import tomllib
from typing import Any
from xml.etree.ElementTree import Element, indent, tostring
from xml.etree.ElementTree import fromstring as xml_fromstring

import tomli_w
import yaml
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from toon_format import decode as toon_decode
from toon_format import encode as toon_encode

# ---------------------------------------------------------------------------
# Media-type constants
# ---------------------------------------------------------------------------

MIME_JSON = "application/json"
MIME_TOON = "application/toon"
MIME_YAML = "application/yaml"
MIME_YAML_ALT = "application/x-yaml"
MIME_TOML = "application/toml"
MIME_XML = "application/xml"
MIME_XML_ALT = "text/xml"

# Map every recognized media type to its canonical key.
_MEDIA_TYPE_MAP: dict[str, str] = {
    MIME_JSON: "json",
    MIME_TOON: "toon",
    MIME_YAML: "yaml",
    MIME_YAML_ALT: "yaml",
    MIME_TOML: "toml",
    MIME_XML: "xml",
    MIME_XML_ALT: "xml",
}

# URL suffix -> canonical key.
_SUFFIX_MAP: dict[str, str] = {
    ".json": "json",
    ".toon": "toon",
    ".yaml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
}

# Canonical key -> primary media type for response Content-Type.
_KEY_TO_MIME: dict[str, str] = {
    "json": MIME_JSON,
    "toon": MIME_TOON,
    "yaml": MIME_YAML,
    "toml": MIME_TOML,
    "xml": MIME_XML,
}

DEFAULT_FORMAT = "json"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def serialize(data: Any, fmt: str) -> tuple[str, str]:
    """Serialize *data* to the format identified by canonical key *fmt*.

    Returns ``(body_text, content_type)``.

    Raises ``ValueError`` for unsupported formats.
    """
    if fmt == "json":
        return json.dumps(data, default=str), MIME_JSON
    if fmt == "toon":
        return toon_encode(data), MIME_TOON
    if fmt == "yaml":
        return yaml.dump(data, default_flow_style=False, allow_unicode=True), MIME_YAML
    if fmt == "toml":
        # TOML requires a top-level table; wrap bare values.
        if not isinstance(data, dict):
            data = {"value": data}
        return tomli_w.dumps(data), MIME_TOML
    if fmt == "xml":
        return _dict_to_xml(data), MIME_XML
    raise ValueError(f"Unsupported serialization format: {fmt}")


def deserialize(body: str | bytes, fmt: str) -> Any:
    """Deserialize *body* from the format identified by canonical key *fmt*.

    Returns the parsed Python object (dict / list / scalar).

    Raises ``ValueError`` for unsupported formats or malformed input.
    """
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    if not body.strip():
        raise ValueError("Empty request body")
    if fmt == "json":
        return json.loads(body)
    if fmt == "toon":
        return toon_decode(body)
    if fmt == "yaml":
        return yaml.safe_load(body)
    if fmt == "toml":
        return tomllib.loads(body)
    if fmt == "xml":
        return _xml_to_dict(body)
    raise ValueError(f"Unsupported deserialization format: {fmt}")


# ---------------------------------------------------------------------------
# XML helpers (simple recursive dict <-> XML)
# ---------------------------------------------------------------------------

_XML_ROOT_TAG = "root"
_XML_ITEM_TAG = "item"


def _dict_to_xml(data: Any, root_tag: str = _XML_ROOT_TAG) -> str:
    """Convert a Python dict/list/scalar to a simple XML string."""
    root = _build_element(root_tag, data)
    indent(root)
    return tostring(root, encoding="unicode", xml_declaration=True)


def _build_element(tag: str, value: Any) -> Element:
    """Recursively build an ``Element`` tree from *value*."""
    elem = Element(tag)
    if isinstance(value, dict):
        for k, v in value.items():
            child = _build_element(str(k), v)
            elem.append(child)
    elif isinstance(value, (list, tuple)):
        for item in value:
            child = _build_element(_XML_ITEM_TAG, item)
            elem.append(child)
    elif value is None:
        elem.text = ""
    elif isinstance(value, bool):
        elem.text = "true" if value else "false"
    else:
        elem.text = str(value)
    return elem


def _xml_to_dict(xml_str: str) -> Any:
    """Parse a simple XML string back to a Python dict/list/scalar."""
    root = xml_fromstring(xml_str)
    return _parse_element(root)


def _parse_element(elem: Element) -> Any:
    """Recursively parse an ``Element`` into a Python object."""
    children = list(elem)
    if not children:
        # Leaf node -- coerce text to Python types.
        return _coerce_text(elem.text)

    # If all children share the same tag and that tag is the item sentinel,
    # produce a list.
    if all(c.tag == _XML_ITEM_TAG for c in children):
        return [_parse_element(c) for c in children]

    # Otherwise produce a dict.
    result: dict[str, Any] = {}
    for child in children:
        result[child.tag] = _parse_element(child)
    return result


def _coerce_text(text: str | None) -> Any:
    """Best-effort coercion of XML text to native Python types."""
    if text is None or text == "":
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


# ---------------------------------------------------------------------------
# Content-negotiation resolution
# ---------------------------------------------------------------------------


def resolve_request_format(content_type: str | None) -> str:
    """Return the canonical format key for the given ``Content-Type`` header.

    Returns ``"json"`` when the header is absent or unrecognized.
    """
    if not content_type:
        return DEFAULT_FORMAT
    # Strip parameters (charset, boundary, ...).
    media_type = content_type.split(";")[0].strip().lower()
    return _MEDIA_TYPE_MAP.get(media_type, DEFAULT_FORMAT)


def resolve_response_format(
    accept: str | None,
    path: str | None = None,
) -> str | None:
    """Determine the best response format from the ``Accept`` header and URL
    suffix.

    URL suffix takes precedence over the ``Accept`` header.

    Returns ``None`` when the client explicitly requests a format that is
    not supported (→ caller should return 406).
    """
    # 1. URL suffix wins.
    if path:
        for suffix, key in _SUFFIX_MAP.items():
            if path.endswith(suffix):
                return key

    # 2. Accept header.
    if not accept or accept == "*/*":
        return DEFAULT_FORMAT

    # Parse quality-weighted Accept entries.
    best_key: str | None = None
    best_q: float = -1.0
    for entry in accept.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(";")
        media = parts[0].strip().lower()
        q = 1.0
        for param in parts[1:]:
            param = param.strip()
            if param.startswith("q="):
                try:
                    q = float(param[2:])
                except ValueError:
                    q = 0.0
        if media == "*/*":
            if q > best_q:
                best_q = q
                best_key = DEFAULT_FORMAT
            continue
        key = _MEDIA_TYPE_MAP.get(media)
        if key is not None and q > best_q:
            best_q = q
            best_key = key

    return best_key


def strip_format_suffix(path: str) -> str:
    """Remove a recognized format suffix from *path* so the router can match
    the base route.
    """
    for suffix in _SUFFIX_MAP:
        if path.endswith(suffix):
            return path[: -len(suffix)]
    return path


# ---------------------------------------------------------------------------
# FastAPI / Starlette middleware
# ---------------------------------------------------------------------------


class ContentNegotiationMiddleware:
    """Pure ASGI middleware for multi-format content negotiation.

    * **Request path**: when ``Content-Type`` indicates a non-JSON format,
      the body is deserialized to a Python object and re-encoded as JSON
      before downstream processing (so Pydantic / FastAPI validation works
      unchanged).
    * **Response path**: when the ``Accept`` header or a URL suffix
      requests a non-JSON format, the JSON response body is re-serialized
      to the requested format.

    Implemented as a raw ASGI middleware (not ``BaseHTTPMiddleware``) so
    the request ``receive`` callable can be swapped before Starlette's
    body cache reads it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers_raw: list[tuple[bytes, bytes]] = scope.get("headers", [])
        headers = {k.lower(): v for k, v in headers_raw}

        original_path: str = scope.get("path", "")

        # --- suffix rewrite ------------------------------------------------
        suffix_fmt: str | None = None
        for suffix, key in _SUFFIX_MAP.items():
            if original_path.endswith(suffix):
                suffix_fmt = key
                scope["path"] = original_path[: -len(suffix)]
                break

        # --- determine response format early -------------------------------
        accept_value = headers.get(b"accept", b"").decode("latin-1")
        resp_fmt = suffix_fmt or resolve_response_format(accept_value or None)
        if resp_fmt is None:
            # 406 Not Acceptable -- short-circuit.
            response = Response(
                content=json.dumps({"detail": "Not Acceptable"}),
                status_code=406,
                media_type=MIME_JSON,
            )
            await response(scope, receive, send)
            return

        # --- request body transcoding --------------------------------------
        content_type_value = headers.get(b"content-type", b"").decode("latin-1")
        req_fmt = resolve_request_format(content_type_value or None)
        method = scope.get("method", "GET")

        if req_fmt != "json" and method in ("POST", "PUT", "PATCH"):
            # Consume the full body from the original ``receive``.
            body_parts: list[bytes] = []
            while True:
                msg = await receive()
                body_parts.append(msg.get("body", b""))
                if not msg.get("more_body", False):
                    break
            raw_body = b"".join(body_parts)

            if raw_body:
                try:
                    parsed = deserialize(raw_body, req_fmt)
                except Exception:
                    response = Response(
                        content=json.dumps(
                            {"detail": f"Malformed {req_fmt.upper()} request body"}
                        ),
                        status_code=422,
                        media_type=MIME_JSON,
                    )
                    await response(scope, receive, send)
                    return

                json_body = json.dumps(parsed).encode("utf-8")

                # Replace the receive callable so downstream reads JSON.
                _body_sent = False

                async def _json_receive() -> Message:
                    nonlocal _body_sent
                    if not _body_sent:
                        _body_sent = True
                        return {
                            "type": "http.request",
                            "body": json_body,
                            "more_body": False,
                        }
                    # Subsequent reads return empty.
                    return {"type": "http.request", "body": b"", "more_body": False}

                receive = _json_receive

                # Patch Content-Type header to application/json.
                new_headers: list[tuple[bytes, bytes]] = []
                for k, v in headers_raw:
                    if k.lower() == b"content-type":
                        new_headers.append((k, MIME_JSON.encode("latin-1")))
                    else:
                        new_headers.append((k, v))
                scope["headers"] = new_headers

        # --- response body transcoding -------------------------------------
        if resp_fmt == "json":
            # No transcoding needed -- pass through.
            await self.app(scope, receive, send)
            return

        # Capture the response so we can re-serialize the body.
        response_started = False
        initial_message: dict[str, Any] = {}
        response_body_parts: list[bytes] = []

        async def _capture_send(message: Message) -> None:
            nonlocal response_started, initial_message
            if message["type"] == "http.response.start":
                response_started = True
                initial_message = message
            elif message["type"] == "http.response.body":
                response_body_parts.append(message.get("body", b""))
                if not message.get("more_body", False):
                    # All body chunks collected -- transcode and send.
                    await _flush_response(send)

        async def _flush_response(real_send: Send) -> None:
            body = b"".join(response_body_parts)
            status = initial_message.get("status", 200)

            # Only transcode successful JSON responses.
            if status >= 400 or not body:
                await real_send(initial_message)
                await real_send(
                    {"type": "http.response.body", "body": body, "more_body": False}
                )
                return

            try:
                data = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Not JSON -- pass through untouched.
                await real_send(initial_message)
                await real_send(
                    {"type": "http.response.body", "body": body, "more_body": False}
                )
                return

            try:
                serialized, mime = serialize(data, resp_fmt)
            except Exception:
                # Serialization failure -- fall back to original JSON.
                await real_send(initial_message)
                await real_send(
                    {"type": "http.response.body", "body": body, "more_body": False}
                )
                return

            # Rewrite headers with new Content-Type and Content-Length.
            orig_headers = initial_message.get("headers", [])
            new_resp_headers: list[tuple[bytes, bytes]] = []
            encoded_body = serialized.encode("utf-8")
            for k, v in orig_headers:
                key_lower = k.lower() if isinstance(k, bytes) else k.encode().lower()
                if key_lower in (b"content-type", b"content-length"):
                    continue
                new_resp_headers.append((k, v))
            new_resp_headers.append((b"content-type", mime.encode("latin-1")))
            new_resp_headers.append(
                (b"content-length", str(len(encoded_body)).encode("latin-1"))
            )

            initial_message["headers"] = new_resp_headers
            await real_send(initial_message)
            await real_send(
                {
                    "type": "http.response.body",
                    "body": encoded_body,
                    "more_body": False,
                }
            )

        await self.app(scope, receive, _capture_send)
