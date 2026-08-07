"""GraphQL federation primitives (Item 16).

Schema-stitching, Apollo Federation v2 composition, selection-set push-down,
batched cross-subgraph resolution, and per-request response caching.

Three federation styles are supported:

* ``apollo_v2`` — upstream advertises ``_service { sdl }``; honor ``@key``,
  ``@external``, ``@requires``, ``@provides``.
* ``stitching`` — upstream supports introspection only; the framework becomes
  the gateway and merges types directly.
* ``namespaced`` — last-resort prefixing of every upstream type/field to
  avoid collisions when the upstream cannot be cleanly stitched.

This module is transport-agnostic: it knows how to introspect, transform,
plan, batch, and cache. The concrete HTTP send is delegated through a
caller-supplied ``transport`` callable so the rotation system, idempotency
machinery, and rate limiting from ``ProviderHTTPClient`` participate in
upstream calls without this module re-implementing them.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from graphql import (
    GraphQLSchema,
    build_client_schema,
    build_schema,
    get_introspection_query,
    parse,
    print_schema,
)
from graphql.language import ast as gql_ast


# ---------------------------------------------------------------------------
# Standard introspection query (cached)
# ---------------------------------------------------------------------------


INTROSPECTION_QUERY: str = get_introspection_query(descriptions=True)

# Apollo Federation v2 SDL probe. Compliant subgraphs answer with the
# subgraph's SDL string in `data._service.sdl`.
APOLLO_SDL_QUERY: str = "{ _service { sdl } }"


# Apollo Federation v2 directive definitions. Subgraph SDLs reference
# `@key`, `@external`, `@requires`, `@provides`, and `@shareable`; we
# declare them locally so `graphql-core`'s `build_schema` accepts the
# subgraph SDL without complaining about undefined directives.
APOLLO_DIRECTIVES_PREAMBLE: str = """
directive @key(fields: String!, resolvable: Boolean = true) repeatable on OBJECT | INTERFACE
directive @external on FIELD_DEFINITION | OBJECT
directive @requires(fields: String!) on FIELD_DEFINITION
directive @provides(fields: String!) on FIELD_DEFINITION
directive @shareable on FIELD_DEFINITION | OBJECT
directive @inaccessible on FIELD_DEFINITION | OBJECT | INTERFACE | UNION | ENUM | ENUM_VALUE | SCALAR | INPUT_OBJECT | INPUT_FIELD_DEFINITION | ARGUMENT_DEFINITION
directive @override(from: String!) on FIELD_DEFINITION
directive @tag(name: String!) repeatable on FIELD_DEFINITION | INTERFACE | OBJECT | UNION | ARGUMENT_DEFINITION | SCALAR | ENUM | ENUM_VALUE | INPUT_OBJECT | INPUT_FIELD_DEFINITION
""".strip()


# ---------------------------------------------------------------------------
# Selection-set push-down
# ---------------------------------------------------------------------------


def reconstruct_selection_set(selected_fields: Sequence[Any]) -> str:
    """Reconstruct a printable upstream selection set from Strawberry's
    ``info.selected_fields`` shape.

    Selection-set push-down is the entire point of GraphQL federation; without
    it the framework has rebuilt RPC inside a GraphQL costume. Each entry is
    expected to expose ``.name`` and ``.selections`` (Strawberry's
    ``SelectedField``); plain dicts with the same keys are also accepted so
    callers can synthesize selections without instantiating Strawberry types.
    """

    pieces: List[str] = []
    for f in selected_fields or ():
        name = _get(f, "name")
        if not name:
            continue
        if name.startswith("__"):
            # Skip introspection meta-fields; they belong to the local schema.
            continue
        sub = _get(f, "selections", default=()) or ()
        args = _format_args(_get(f, "arguments", default=None))
        if sub:
            pieces.append(f"{name}{args} {{ {reconstruct_selection_set(sub)} }}")
        else:
            pieces.append(f"{name}{args}")
    return " ".join(pieces)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _format_args(arguments: Any) -> str:
    """Render Strawberry-style arguments (``{name: value}``) into ``(...)``.

    Returns an empty string when the call site has no arguments so the
    rendered query text stays valid for fields that take zero arguments.
    """

    if not arguments:
        return ""
    if isinstance(arguments, Mapping):
        items = arguments.items()
    else:
        # iterables of objects with `.name`/`.value`
        items = ((_get(a, "name"), _get(a, "value")) for a in arguments)  # type: ignore[assignment]
    formatted = ", ".join(
        f"{k}: {_format_argument_value(v)}" for k, v in items if k is not None
    )
    return f"({formatted})" if formatted else ""


def _format_argument_value(value: Any) -> str:
    """Render an argument value as a GraphQL literal."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_argument_value(v) for v in value) + "]"
    if isinstance(value, Mapping):
        body = ", ".join(
            f"{k}: {_format_argument_value(v)}" for k, v in value.items()
        )
        return "{" + body + "}"
    return json.dumps(str(value))


def build_query_document(
    operation: str,
    selection_body: str,
    operation_type: str = "query",
    variables_signature: str = "",
) -> str:
    """Build a syntactically valid GraphQL document.

    ``operation`` is the upstream root field being invoked, ``selection_body``
    is the rendered selection set returned from :func:`reconstruct_selection_set`,
    and ``variables_signature`` (e.g. ``"$id: ID!"``) is rendered into the
    operation header when the caller is forwarding variables rather than
    inlining literals.
    """

    sig = f"({variables_signature})" if variables_signature else ""
    return f"{operation_type} ProxyOp{sig} {{ {operation} {{ {selection_body} }} }}"


# ---------------------------------------------------------------------------
# Schema transformer pipeline
# ---------------------------------------------------------------------------


def _split_fields(body: str) -> List[str]:
    """Split a type body into individual field-definition strings.

    GraphQL field bodies can span lines or be inlined on one line; we tokenize
    by detecting balanced parens (for argument lists) and treating any
    ``name(...)?: ...`` chunk up to the next field boundary as one entry.
    """

    import re as _re

    body = body.strip()
    if not body:
        return []
    out: List[str] = []
    i = 0
    n = len(body)
    while i < n:
        # Skip leading whitespace.
        while i < n and body[i] in " \t\n\r":
            i += 1
        if i >= n:
            break
        # Read field name.
        name_match = _re.match(r"[A-Za-z_][A-Za-z0-9_]*", body[i:])
        if not name_match:
            # Unrecognized chunk — append the rest unchanged so we don't
            # silently drop content like trailing comments.
            out.append(body[i:].strip())
            break
        start = i
        i += len(name_match.group(0))
        # Optional argument list ``(...)``.
        if i < n and body[i] == "(":
            depth = 1
            i += 1
            while i < n and depth > 0:
                if body[i] == "(":
                    depth += 1
                elif body[i] == ")":
                    depth -= 1
                i += 1
        # Required ``: type``.
        while i < n and body[i] in " \t":
            i += 1
        if i < n and body[i] == ":":
            i += 1
            # Read until next field name (followed by optional `(` or `:`)
            # or end of body. Track bracket depth so list/non-null markers
            # don't break us early.
            depth = 0
            while i < n:
                c = body[i]
                if c in "[":
                    depth += 1
                    i += 1
                    continue
                if c == "]":
                    depth -= 1
                    i += 1
                    continue
                if depth == 0 and c in "\n,":
                    break
                if depth == 0 and c == " ":
                    # Look ahead: is this whitespace followed by a new field?
                    j = i + 1
                    while j < n and body[j] in " \t\n\r":
                        j += 1
                    if j < n and _re.match(r"[A-Za-z_][A-Za-z0-9_]*[ \t]*[(:]", body[j:]):
                        break
                i += 1
        out.append(body[start:i].strip().rstrip(","))
        if i < n and body[i] in ",\n":
            i += 1
    return [f for f in out if f]


@dataclass
class SchemaTransformer:
    """Transform an upstream SDL on its way into the merged registry.

    The pipeline supports five composable transformations declared up-front
    on the provider:

    * ``rename``         — ``{upstream_type: local_type}``
    * ``prefix``         — prepend a namespace token to every type name
    * ``hide_fields``    — ``{type: {field, field, ...}}``
    * ``mask_arguments`` — ``{type: {field: {arg, arg, ...}}}``
    * ``override_resolvers`` — ``{type.field: callable}`` (callables are
      not applied here; they are stored on the merged record so the
      stitching engine can wire them in at resolver-build time).
    """

    rename: Dict[str, str] = field(default_factory=dict)
    prefix: Optional[str] = None
    hide_fields: Dict[str, Set[str]] = field(default_factory=dict)
    mask_arguments: Dict[str, Dict[str, Set[str]]] = field(default_factory=dict)
    override_resolvers: Dict[str, Callable[..., Any]] = field(default_factory=dict)

    def transform(self, sdl: str) -> str:
        """Apply the pipeline to ``sdl`` and return the new SDL string.

        Implementation note: we work at the SDL string level rather than via
        AST mutation. ``graphql-core``'s AST nodes are quasi-immutable and
        the printer rejects partial in-place edits. SDL-level rewriting is
        also cheap and avoids re-implementing the full visitor pattern.
        """

        import re as _re

        if not sdl:
            return sdl
        document = parse(sdl)  # validates input
        renames = self._compute_renames(document)
        out_sdl = sdl

        # Step 1: rename type definitions and references.
        # We rewrite definitions first (to their new names) then any
        # remaining bare references to the old name.
        for old_name, new_name in renames.items():
            # Update declarations: ``type Old``, ``input Old``, ``enum Old``,
            # ``scalar Old``, ``union Old``, ``interface Old``.
            out_sdl = _re.sub(
                rf"(\b(?:type|input|enum|scalar|union|interface)\s+){old_name}\b",
                lambda m: m.group(1) + new_name,
                out_sdl,
            )
            # Update remaining references (field types, union members,
            # interface implementations, etc.). We use word boundaries so
            # ``Customer`` doesn't match ``CustomerEmail``.
            out_sdl = _re.sub(rf"\b{old_name}\b", new_name, out_sdl)

        # Step 2: hide fields.
        for type_name, hidden in self.hide_fields.items():
            mapped = renames.get(type_name, type_name)
            out_sdl = self._strip_fields(out_sdl, mapped, hidden)

        # Step 3: mask arguments.
        for type_name, fields_args in self.mask_arguments.items():
            mapped = renames.get(type_name, type_name)
            for fname, masked in fields_args.items():
                out_sdl = self._strip_args(out_sdl, mapped, fname, masked)

        # Validate that the output is still parseable so a buggy transformer
        # surfaces immediately rather than at registry-build time.
        parse(out_sdl)
        return out_sdl

    # --- internals ----------------------------------------------------------

    def _compute_renames(self, document: gql_ast.DocumentNode) -> Dict[str, str]:
        out: Dict[str, str] = dict(self.rename)
        if self.prefix:
            for d in document.definitions:
                name = getattr(getattr(d, "name", None), "value", None)
                if not name or name.startswith("__"):
                    continue
                if name in out:
                    continue
                # Reserve roots — they're folded into the local Query/Mutation,
                # not exposed as named subtypes.
                if name in {"Query", "Mutation", "Subscription"}:
                    continue
                # Skip GraphQL builtins.
                if name in {
                    "String", "Int", "Float", "Boolean", "ID",
                }:
                    continue
                out[name] = f"{self.prefix}{name}"
        return out

    def _strip_fields(self, sdl: str, type_name: str, hidden: Set[str]) -> str:
        """Drop hidden fields from a type declaration in ``sdl``."""

        import re as _re

        # Match the type body and remove field definitions whose name is in
        # ``hidden``. We tokenize fields by matching ``name(args)?: type``
        # patterns so the function works regardless of whether the source
        # SDL is on one line or many.
        pattern = _re.compile(
            r"(\b(?:type|interface|input)\s+" + _re.escape(type_name) + r"\b[^{]*\{)([\s\S]*?)(\})",
        )

        def _rewrite(m: "_re.Match[str]") -> str:
            head, body, tail = m.group(1), m.group(2), m.group(3)
            fields = _split_fields(body)
            kept = [
                f for f in fields
                if not (
                    (name_match := _re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", f))
                    and name_match.group(1) in hidden
                )
            ]
            sep = "\n" if "\n" in body else " "
            return head + sep.join(kept) + tail

        return pattern.sub(_rewrite, sdl, count=1)

    def _strip_args(
        self, sdl: str, type_name: str, field_name: str, masked: Set[str]
    ) -> str:
        """Drop masked arguments from a single field signature."""

        import re as _re

        # Locate the type body, then the specific field's arg list.
        pattern = _re.compile(
            r"(\b(?:type|interface|input)\s+"
            + _re.escape(type_name)
            + r"\b[^{]*\{)([^}]*)(\})",
            _re.DOTALL,
        )

        def _rewrite_body(m: "_re.Match[str]") -> str:
            head, body, tail = m.group(1), m.group(2), m.group(3)
            field_pat = _re.compile(
                r"(\b" + _re.escape(field_name) + r"\b)\(([^)]*)\)"
            )

            def _rewrite_field(fm: "_re.Match[str]") -> str:
                args = fm.group(2)
                kept: List[str] = []
                for piece in args.split(","):
                    piece = piece.strip()
                    if not piece:
                        continue
                    arg_name_match = _re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", piece)
                    if arg_name_match and arg_name_match.group(1) in masked:
                        continue
                    kept.append(piece)
                if not kept:
                    return fm.group(1)
                return f"{fm.group(1)}({', '.join(kept)})"

            return head + field_pat.sub(_rewrite_field, body) + tail

        return pattern.sub(_rewrite_body, sdl, count=1)


# ---------------------------------------------------------------------------
# Federation-style detection and SDL ingestion
# ---------------------------------------------------------------------------


@dataclass
class IngestedSchema:
    """An upstream SDL plus the metadata needed to merge it.

    ``sdl`` is always a valid SDL string. ``apollo_v2`` indicates whether the
    upstream advertised a Federation v2 subgraph (in which case ``key_fields``
    captures ``@key`` directives keyed by entity name). ``hash`` is a stable
    fingerprint used by ``MergedSchemaRegistry`` to detect drift between
    refreshes.
    """

    sdl: str
    apollo_v2: bool
    key_fields: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.sdl.encode("utf-8")).hexdigest()


def detect_federation_directives(sdl: str) -> Dict[str, List[str]]:
    """Extract ``@key(fields: "...")`` directives from an Apollo subgraph SDL.

    Returns ``{type_name: [field_set, ...]}``. Empty dict means no
    Federation v2 ``@key`` directives were declared.
    """

    if not sdl or "@key" not in sdl:
        return {}
    document = parse(sdl)
    out: Dict[str, List[str]] = {}
    for d in document.definitions:
        if d.kind != "object_type_definition":
            continue
        for directive in d.directives or ():  # type: ignore[attr-defined]
            if directive.name.value != "key":
                continue
            for arg in directive.arguments or ():
                if arg.name.value != "fields":
                    continue
                value = getattr(arg.value, "value", None)
                if value:
                    out.setdefault(d.name.value, []).append(value)  # type: ignore[attr-defined]
    return out


def ingest_introspection(payload: Mapping[str, Any]) -> IngestedSchema:
    """Build an :class:`IngestedSchema` from a stitching introspection payload.

    ``payload`` is the ``data`` object returned by an ``IntrospectionQuery``
    response (i.e. ``{"__schema": {...}}``). Apollo Federation v2 is *not*
    detected via this path — use :func:`ingest_apollo_sdl` for compliant
    subgraphs.
    """

    schema: GraphQLSchema = build_client_schema({"__schema": payload["__schema"]})
    sdl = print_schema(schema)
    return IngestedSchema(sdl=sdl, apollo_v2=False)


def ingest_apollo_sdl(sdl: str) -> IngestedSchema:
    """Build an :class:`IngestedSchema` from an Apollo Federation v2 ``sdl``."""

    keys = detect_federation_directives(sdl)
    return IngestedSchema(sdl=sdl, apollo_v2=True, key_fields=keys)


# ---------------------------------------------------------------------------
# Per-request response cache (Item 16: dedupe within a single GraphQL op)
# ---------------------------------------------------------------------------


def cache_key(
    query: str, variables: Optional[Mapping[str, Any]], requester_credentials: str
) -> str:
    """Compute the per-request cache key.

    Keyed by ``(query_hash, variables_hash, requester_credentials_hash)`` so
    two distinct requesters never share a cached upstream result, and so
    identical sub-requests within one outer GraphQL operation collapse to a
    single upstream call.
    """

    hasher = hashlib.sha256()
    hasher.update(query.encode("utf-8"))
    hasher.update(b"|")
    hasher.update(
        json.dumps(variables or {}, sort_keys=True, default=str).encode("utf-8")
    )
    hasher.update(b"|")
    hasher.update((requester_credentials or "").encode("utf-8"))
    return hasher.hexdigest()


class ResponseCache:
    """Per-request, in-memory response cache.

    Bound onto a contextvar by the inbound middleware so every resolver
    inside a single GraphQL operation shares one cache. Lookups are
    thread-safe; the cache is intentionally bounded to the request — there
    is no eviction policy because the cache is discarded with the context.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: Dict[str, Any] = {}
        # Pending awaitables for in-flight upstream calls, used to dedupe
        # concurrent resolver invocations within a single operation.
        self._pending: Dict[str, asyncio.Future] = {}

    def get(self, key: str) -> Tuple[bool, Any]:
        with self._lock:
            if key in self._store:
                return True, self._store[key]
            return False, None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = value

    def get_or_register_pending(
        self, key: str, factory: Callable[[], "asyncio.Future"]
    ) -> Tuple["asyncio.Future", bool]:
        """Return ``(future, is_owner)``. ``is_owner=True`` means the caller is
        responsible for resolving the future via :meth:`resolve_pending`.
        Subsequent callers receive the same future and must just await it."""

        with self._lock:
            existing = self._pending.get(key)
            if existing is not None:
                return existing, False
            fut = factory()
            self._pending[key] = fut
            return fut, True

    def resolve_pending(self, key: str, value: Any) -> None:
        with self._lock:
            fut = self._pending.pop(key, None)
            self._store[key] = value
        if fut is not None and not fut.done():
            fut.set_result(value)

    def fail_pending(self, key: str, exc: BaseException) -> None:
        with self._lock:
            fut = self._pending.pop(key, None)
        if fut is not None and not fut.done():
            fut.set_exception(exc)


_response_cache_var: contextvars.ContextVar[Optional[ResponseCache]] = (
    contextvars.ContextVar("federation_response_cache", default=None)
)


def bind_response_cache(cache: Optional[ResponseCache]) -> contextvars.Token:
    return _response_cache_var.set(cache)


def reset_response_cache(token: contextvars.Token) -> None:
    _response_cache_var.reset(token)


def get_response_cache() -> Optional[ResponseCache]:
    return _response_cache_var.get()


# ---------------------------------------------------------------------------
# Persistent cache hook (Item 16: ``@cache(ttl=...)`` directive on merged type)
# ---------------------------------------------------------------------------


class _PersistentCache:
    """Tiny TTL cache used when an upstream type opts in via ``cache_ttls``.

    A real deployment swaps this out for Redis. The interface is intentionally
    minimal so a Redis-backed adapter is a drop-in replacement: ``get``,
    ``set``, and ``invalidate``.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Tuple[bool, Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False, None
            expiry, value = entry
            if expiry and expiry < time.time():
                self._store.pop(key, None)
                return False, None
            return True, value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            self._store[key] = (
                time.time() + ttl_seconds if ttl_seconds > 0 else 0.0,
                value,
            )

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


_global_persistent_cache = _PersistentCache()


def get_persistent_cache() -> _PersistentCache:
    """Return the process-wide persistent cache. Tests reset between runs."""

    return _global_persistent_cache


# ---------------------------------------------------------------------------
# Batched field resolver
# ---------------------------------------------------------------------------


@dataclass
class _PendingBatch:
    """An accumulated batch of cross-subgraph requests for a single field."""

    operation: str
    selection_body: str
    requester_credentials: str
    variables_signature: str
    keys: List[Any] = field(default_factory=list)
    futures: List["asyncio.Future"] = field(default_factory=list)
    transport_args: Dict[str, Any] = field(default_factory=dict)


class BatchedFieldResolver:
    """Collects N parallel resolver invocations into a single upstream call.

    Bridges the framework's ``include`` mechanism (Item 9) to upstream batching
    capabilities. For upstreams that support list-by-id, a single batched
    document is sent. For upstreams that don't, the resolver falls back to
    bounded-concurrency individual calls and lets the rotation system's rate
    limiter (Item 17) gate them.

    The resolver is per-request: bind one onto the ``contextvars`` slot when
    the inbound GraphQL middleware enters, and reset it on exit.
    """

    def __init__(self, max_concurrency: int = 8) -> None:
        self._lock = threading.Lock()
        self._batches: Dict[Tuple[str, str], _PendingBatch] = {}
        self._max_concurrency = max(1, max_concurrency)

    async def resolve(
        self,
        *,
        provider_key: str,
        operation: str,
        key: Any,
        selection_body: str,
        transport: Callable[..., Awaitable[Any]],
        variables_signature: str = "",
        requester_credentials: str = "",
        list_by_id_arg: Optional[str] = "ids",
        per_item_arg: str = "id",
    ) -> Any:
        """Resolve a single ``operation(key)`` against the upstream.

        N concurrent ``resolve`` calls for the same ``(provider_key, operation,
        selection_body)`` collapse into one upstream call when ``list_by_id_arg``
        is set; otherwise they fall through to bounded-concurrency per-item
        calls. The ``transport`` is the caller's federation HTTP send (typically
        ``ProviderHTTPClient.post`` shaped to the upstream GraphQL endpoint).
        """

        cache = get_response_cache()
        if cache is not None:
            inline_query = build_query_document(
                operation=f"{operation}({per_item_arg}: {_format_argument_value(key)})",
                selection_body=selection_body,
            )
            ck = cache_key(inline_query, None, requester_credentials)
            hit, cached = cache.get(ck)
            if hit:
                return cached

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()

        if list_by_id_arg is None:
            # No batching available: schedule a bounded individual call.
            await _bounded_individual_call(
                future,
                transport,
                operation,
                key,
                selection_body,
                per_item_arg,
                self._max_concurrency,
            )
            return await future

        with self._lock:
            bucket = self._batches.get((provider_key, operation))
            owner = False
            if bucket is None:
                bucket = _PendingBatch(
                    operation=operation,
                    selection_body=selection_body,
                    requester_credentials=requester_credentials,
                    variables_signature=variables_signature,
                )
                self._batches[(provider_key, operation)] = bucket
                owner = True
            bucket.keys.append(key)
            bucket.futures.append(future)

        if owner:
            asyncio.get_event_loop().call_soon(
                lambda: asyncio.ensure_future(
                    self._flush(provider_key, operation, transport, list_by_id_arg)
                )
            )
        return await future

    async def _flush(
        self,
        provider_key: str,
        operation: str,
        transport: Callable[..., Awaitable[Any]],
        list_by_id_arg: str,
    ) -> None:
        # Yield once so other callers register their futures inside the same
        # event-loop tick before we collect the batch.
        await asyncio.sleep(0)
        with self._lock:
            bucket = self._batches.pop((provider_key, operation), None)
        if bucket is None:
            return
        document = build_query_document(
            operation=(
                f"{operation}("
                f"{list_by_id_arg}: {_format_argument_value(bucket.keys)}"
                ")"
            ),
            selection_body=bucket.selection_body,
        )
        try:
            response = await transport(query=document, variables=None)
        except BaseException as exc:
            for fut in bucket.futures:
                if not fut.done():
                    fut.set_exception(exc)
            return
        items = _extract_items(response, operation)
        keyed: Dict[Any, Any] = {}
        for item in items:
            if isinstance(item, Mapping):
                k = item.get("id") or item.get(list_by_id_arg) or None
                if k is None:
                    continue
                keyed[k] = item
        cache = get_response_cache()
        for key, fut in zip(bucket.keys, bucket.futures):
            value = keyed.get(key)
            if cache is not None:
                inline_query = build_query_document(
                    operation=(
                        f"{operation}(id: {_format_argument_value(key)})"
                    ),
                    selection_body=bucket.selection_body,
                )
                cache.set(
                    cache_key(inline_query, None, bucket.requester_credentials),
                    value,
                )
            if not fut.done():
                fut.set_result(value)


async def _bounded_individual_call(
    future: "asyncio.Future",
    transport: Callable[..., Awaitable[Any]],
    operation: str,
    key: Any,
    selection_body: str,
    per_item_arg: str,
    max_concurrency: int,
) -> None:
    """Fallback path for upstreams without list-by-id batching."""

    sem = _bounded_semaphore_for(max_concurrency)
    async with sem:
        document = build_query_document(
            operation=f"{operation}({per_item_arg}: {_format_argument_value(key)})",
            selection_body=selection_body,
        )
        try:
            response = await transport(query=document, variables=None)
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
            return
        items = _extract_items(response, operation)
        if not future.done():
            future.set_result(items[0] if items else None)


_individual_semaphores: Dict[int, asyncio.Semaphore] = {}
_individual_semaphores_lock = threading.Lock()


def _bounded_semaphore_for(max_concurrency: int) -> asyncio.Semaphore:
    with _individual_semaphores_lock:
        sem = _individual_semaphores.get(max_concurrency)
        if sem is None:
            sem = asyncio.Semaphore(max_concurrency)
            _individual_semaphores[max_concurrency] = sem
        return sem


def reset_individual_semaphores() -> None:
    """Test helper: drop cached semaphores (their event loop has died)."""

    with _individual_semaphores_lock:
        _individual_semaphores.clear()


def _extract_items(response: Any, operation: str) -> List[Any]:
    """Pull the operation's result list out of a GraphQL response envelope."""

    if response is None:
        return []
    if isinstance(response, Mapping):
        data = response.get("data")
        if isinstance(data, Mapping):
            value = data.get(operation)
            if isinstance(value, list):
                return list(value)
            if value is None:
                return []
            return [value]
    if isinstance(response, list):
        return list(response)
    return [response]


_resolver_var: contextvars.ContextVar[Optional[BatchedFieldResolver]] = (
    contextvars.ContextVar("federation_batched_resolver", default=None)
)


def bind_batched_resolver(
    resolver: Optional[BatchedFieldResolver],
) -> contextvars.Token:
    return _resolver_var.set(resolver)


def reset_batched_resolver(token: contextvars.Token) -> None:
    _resolver_var.reset(token)


def get_batched_resolver() -> Optional[BatchedFieldResolver]:
    return _resolver_var.get()


# ---------------------------------------------------------------------------
# Merged schema registry
# ---------------------------------------------------------------------------


@dataclass
class FederatedSubgraph:
    """One registered upstream subgraph in the merged schema."""

    name: str
    style: str  # "apollo_v2" | "stitching" | "namespaced"
    namespace: Optional[str]
    sdl: str
    schema: GraphQLSchema
    key_fields: Dict[str, List[str]]
    sdl_hash: str
    transport: Optional[Callable[..., Awaitable[Any]]] = None
    cache_ttls: Dict[str, float] = field(default_factory=dict)


class MergedSchemaRegistry:
    """Holds the merged federated schema across all registered subgraphs.

    The merged schema is computed once at startup and on TTL refresh, not per
    request. Calling :meth:`build` is idempotent; the second call returns the
    same merged ``GraphQLSchema`` unless one of the underlying SDL hashes has
    changed since the previous build.

    The registry is process-wide (a singleton via :func:`global_registry`)
    but instances are equally usable for tests.
    """

    SDL_TTL_SECONDS: ClassVar[int] = 300

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subgraphs: Dict[str, FederatedSubgraph] = {}
        self._merged: Optional[GraphQLSchema] = None
        self._merged_hash: Optional[str] = None
        self._merged_built_at: float = 0.0
        # Listeners notified after every successful merge.
        self._listeners: List[Callable[[GraphQLSchema, Dict[str, str]], None]] = []
        # Hash table from previous merge for diffing.
        self._previous_hashes: Dict[str, str] = {}

    # --- Registration -------------------------------------------------------

    def register(
        self,
        *,
        name: str,
        ingested: IngestedSchema,
        style: str,
        namespace: Optional[str] = None,
        transport: Optional[Callable[..., Awaitable[Any]]] = None,
        cache_ttls: Optional[Mapping[str, float]] = None,
    ) -> FederatedSubgraph:
        if style not in {"apollo_v2", "stitching", "namespaced"}:
            raise ValueError(
                f"Unknown federation style {style!r}; must be apollo_v2|stitching|namespaced"
            )
        sdl_for_build = (
            APOLLO_DIRECTIVES_PREAMBLE + "\n" + ingested.sdl
            if ingested.apollo_v2 and "directive @key" not in ingested.sdl
            else ingested.sdl
        )
        schema = build_schema(sdl_for_build)
        subgraph = FederatedSubgraph(
            name=name,
            style=style,
            namespace=namespace,
            sdl=ingested.sdl,
            schema=schema,
            key_fields=ingested.key_fields,
            sdl_hash=ingested.hash,
            transport=transport,
            cache_ttls=dict(cache_ttls or {}),
        )
        with self._lock:
            self._subgraphs[name] = subgraph
            self._merged = None  # invalidate
        return subgraph

    def unregister(self, name: str) -> bool:
        with self._lock:
            removed = self._subgraphs.pop(name, None) is not None
            if removed:
                self._merged = None
            return removed

    # --- Build --------------------------------------------------------------

    def subgraphs(self) -> List[FederatedSubgraph]:
        with self._lock:
            return list(self._subgraphs.values())

    def build(self) -> GraphQLSchema:
        """Compute the merged schema from all registered subgraphs."""

        with self._lock:
            if self._merged is not None and not self._needs_rebuild_locked():
                return self._merged
            merged_sdl = self._merge_sdls_locked()
            if merged_sdl:
                # Always preface with the Apollo directives so subgraphs that
                # carry @key/@external survive a rebuild even when no
                # subgraph happens to redeclare them.
                schema = build_schema(APOLLO_DIRECTIVES_PREAMBLE + "\n" + merged_sdl)
            else:
                schema = _empty_schema()
            diff = self._diff_locked()
            self._merged = schema
            self._merged_hash = hashlib.sha256(merged_sdl.encode("utf-8")).hexdigest()
            self._merged_built_at = time.time()
            self._previous_hashes = {
                name: sg.sdl_hash for name, sg in self._subgraphs.items()
            }
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(schema, diff)
            except Exception:
                # Listener failures must not prevent schema construction.
                continue
        return schema

    def merged_sdl(self) -> str:
        with self._lock:
            return self._merge_sdls_locked()

    def merged_hash(self) -> Optional[str]:
        with self._lock:
            return self._merged_hash

    def add_listener(
        self, listener: Callable[[GraphQLSchema, Dict[str, str]], None]
    ) -> None:
        with self._lock:
            self._listeners.append(listener)

    # --- internals ----------------------------------------------------------

    def _needs_rebuild_locked(self) -> bool:
        if self._merged is None:
            return True
        if (time.time() - self._merged_built_at) > self.SDL_TTL_SECONDS:
            return True
        for name, sg in self._subgraphs.items():
            if self._previous_hashes.get(name) != sg.sdl_hash:
                return True
        # Removal: any previous-hash key not in current set means rebuild.
        for prev_name in self._previous_hashes.keys():
            if prev_name not in self._subgraphs:
                return True
        return False

    def _merge_sdls_locked(self) -> str:
        """Concatenate the registered SDLs, deduplicating shared roots.

        The merged SDL keeps each subgraph's types intact and emits a single
        ``Query``/``Mutation``/``Subscription`` whose fields are the union of
        each subgraph's roots. Duplicate field names produce a startup error
        (mirroring the in-process collision rule).
        """

        if not self._subgraphs:
            return ""
        type_blocks: List[str] = []
        seen_types: Set[str] = set()
        query_fields: List[str] = []
        mutation_fields: List[str] = []
        subscription_fields: List[str] = []
        seen_field_names: Dict[str, str] = {}
        for sg in self._subgraphs.values():
            for d in parse(sg.sdl).definitions:
                kind = d.kind
                if kind not in (
                    "object_type_definition",
                    "interface_type_definition",
                    "input_object_type_definition",
                    "union_type_definition",
                    "enum_type_definition",
                    "scalar_type_definition",
                ):
                    continue
                name = d.name.value  # type: ignore[attr-defined]
                if name in {"Query", "Mutation", "Subscription"}:
                    fields = self._field_lines_for_root(d, sg, seen_field_names)
                    if name == "Query":
                        query_fields.extend(fields)
                    elif name == "Mutation":
                        mutation_fields.extend(fields)
                    else:
                        subscription_fields.extend(fields)
                    continue
                if name in seen_types:
                    continue
                from graphql.language import print_ast

                type_blocks.append(print_ast(d))
                seen_types.add(name)
        rendered = list(type_blocks)
        if query_fields:
            rendered.append(
                "type Query {\n  " + "\n  ".join(query_fields) + "\n}"
            )
        if mutation_fields:
            rendered.append(
                "type Mutation {\n  " + "\n  ".join(mutation_fields) + "\n}"
            )
        if subscription_fields:
            rendered.append(
                "type Subscription {\n  " + "\n  ".join(subscription_fields) + "\n}"
            )
        if not rendered:
            return ""
        return "\n\n".join(rendered)

    def _field_lines_for_root(
        self,
        root_def: Any,
        subgraph: FederatedSubgraph,
        seen_field_names: Dict[str, str],
    ) -> List[str]:
        from graphql.language import print_ast

        out: List[str] = []
        for f in root_def.fields or []:
            fname = f.name.value
            owner = seen_field_names.get(fname)
            if owner is not None and owner != subgraph.name:
                raise ValueError(
                    f"Federated root field collision on {fname!r}: "
                    f"both {owner!r} and {subgraph.name!r} declare it"
                )
            seen_field_names[fname] = subgraph.name
            out.append(print_ast(f))
        return out

    def _diff_locked(self) -> Dict[str, str]:
        diff: Dict[str, str] = {}
        for name, sg in self._subgraphs.items():
            previous = self._previous_hashes.get(name)
            if previous is None:
                diff[name] = "added"
            elif previous != sg.sdl_hash:
                diff[name] = "updated"
        for prev_name in self._previous_hashes.keys():
            if prev_name not in self._subgraphs:
                diff[prev_name] = "removed"
        return diff


def _empty_schema() -> GraphQLSchema:
    """Return a placeholder schema used when no subgraphs are registered."""

    return build_schema("type Query { _empty: Boolean }")


_global_registry = MergedSchemaRegistry()


def global_registry() -> MergedSchemaRegistry:
    """Process-wide :class:`MergedSchemaRegistry` singleton."""

    return _global_registry


def reset_global_registry() -> None:
    """Test helper: drop all registered subgraphs."""

    global _global_registry
    _global_registry = MergedSchemaRegistry()


# ---------------------------------------------------------------------------
# SDL → Pydantic model importer
# ---------------------------------------------------------------------------
#
# Lifts an upstream GraphQL SDL into Pydantic model classes so the framework's
# existing ``Pydantic2Strawberry`` and ``Pydantic2FastAPI`` pipelines can
# project the imported types onto BOTH inbound surfaces (REST and GraphQL).
# This is the cleanest way to give a GraphQL upstream a REST face: you don't
# port resolver-by-resolver, you port the schema once into Pydantic and let
# the existing machinery do the rest.
#
# This is one half of the "schema → Pydantic" story; the other half lives in
# ``Federation_REST.py`` for OpenAPI upstreams.


GQL_SCALAR_TO_PY: Dict[str, type] = {
    "String": str,
    "Int": int,
    "Float": float,
    "Boolean": bool,
    "ID": str,
    "DateTime": str,  # ISO-8601 string round-trip; consumer parses
    "Date": str,
    "Time": str,
    "JSON": dict,
    "JSONObject": dict,
}


@dataclass
class SDLToPydanticResult:
    """Models, scalars, and enums imported from a GraphQL SDL.

    Returned from :func:`sdl_to_pydantic_models`. Keys are the *prefixed*
    type names; values are the generated Pydantic ``BaseModel`` subclasses
    (or ``Enum`` types for GraphQL enums).
    """

    models: Dict[str, type] = field(default_factory=dict)
    enums: Dict[str, type] = field(default_factory=dict)
    inputs: Dict[str, type] = field(default_factory=dict)


def sdl_to_pydantic_models(
    sdl: str,
    *,
    prefix: Optional[str] = None,
    include_inputs: bool = True,
) -> SDLToPydanticResult:
    """Generate Pydantic model classes from a GraphQL SDL.

    Object types and interfaces become :class:`pydantic.BaseModel` subclasses.
    Enums become :class:`enum.Enum` subclasses. Input types become
    :class:`pydantic.BaseModel` subclasses (kept separate from output types so
    a downstream registry can register them under different keys). Unions are
    rendered as ``Union[...]`` annotations on the parent field; they do not
    produce a top-level model entry. Scalars unknown to
    :data:`GQL_SCALAR_TO_PY` are mapped to ``str``.

    The generated models inherit from
    :class:`AbstractFederatedExternalModel` so a ``RotationManager`` knows
    they were lifted from an upstream and can route operations through the
    federated transport rather than the database.
    """

    from enum import Enum
    from pydantic import BaseModel, ConfigDict, Field
    from typing import Optional as _Optional, List as _List, Union as _Union

    if not sdl:
        return SDLToPydanticResult()

    document = parse(sdl)

    # First pass: collect names so forward references resolve.
    enum_names: Dict[str, List[str]] = {}
    type_field_specs: Dict[str, List[Tuple[str, Any, bool]]] = {}
    input_field_specs: Dict[str, List[Tuple[str, Any, bool]]] = {}
    union_members: Dict[str, List[str]] = {}

    def _name(d: Any) -> str:
        return d.name.value

    def _full(name: str) -> str:
        if name in {"Query", "Mutation", "Subscription"}:
            return name
        if not prefix or name in GQL_SCALAR_TO_PY:
            return name
        if name.startswith(prefix):
            return name
        return f"{prefix}{name}"

    for d in document.definitions:
        if d.kind == "enum_type_definition":
            enum_names[_full(_name(d))] = [v.name.value for v in d.values or ()]  # type: ignore[attr-defined]
        elif d.kind == "union_type_definition":
            union_members[_full(_name(d))] = [_full(t.name.value) for t in d.types or ()]  # type: ignore[attr-defined]
        elif d.kind in ("object_type_definition", "interface_type_definition"):
            if _name(d) in {"Query", "Mutation", "Subscription"}:
                continue  # Roots are not materialized as Pydantic models
            specs: List[Tuple[str, Any, bool]] = []
            for f in d.fields or ():  # type: ignore[attr-defined]
                ann, required = _gql_type_to_python(f.type, _full)
                specs.append((f.name.value, ann, required))
            type_field_specs[_full(_name(d))] = specs
        elif d.kind == "input_object_type_definition":
            specs2: List[Tuple[str, Any, bool]] = []
            for f in d.fields or ():  # type: ignore[attr-defined]
                ann, required = _gql_type_to_python(f.type, _full)
                specs2.append((f.name.value, ann, required))
            input_field_specs[_full(_name(d))] = specs2

    # Build enum classes first so models can reference them.
    enums: Dict[str, type] = {}
    for name, values in enum_names.items():
        enums[name] = Enum(name, {v: v for v in values})  # type: ignore[assignment]

    # Second pass: materialize models. Resolve string references against
    # the in-progress dict so cycles are handled by `model_rebuild` later.
    models: Dict[str, type] = {}

    def _resolve(annotation: Any) -> Any:
        if isinstance(annotation, str):
            if annotation in enums:
                return enums[annotation]
            if annotation in models:
                return models[annotation]
            if annotation in union_members:
                # Render unions as ``Union[member1, member2, ...]``
                resolved = tuple(
                    models.get(m, str) for m in union_members[annotation]
                )
                return _Union[resolved] if len(resolved) > 1 else resolved[0]
            return annotation  # forward reference; rebuild later
        return annotation

    for type_name, specs in type_field_specs.items():
        annotations: Dict[str, Any] = {}
        defaults: Dict[str, Any] = {}
        for fname, ann, required in specs:
            resolved = _resolve(ann)
            annotations[fname] = resolved if required else _Optional[resolved]
            if not required:
                defaults[fname] = None
        cls_dict: Dict[str, Any] = {
            "__annotations__": annotations,
            "model_config": ConfigDict(arbitrary_types_allowed=True),
            **defaults,
        }
        models[type_name] = type(type_name, (BaseModel,), cls_dict)

    # Inputs follow the same shape but are kept separate.
    inputs: Dict[str, type] = {}
    if include_inputs:
        for in_name, specs3 in input_field_specs.items():
            annotations2: Dict[str, Any] = {}
            defaults2: Dict[str, Any] = {}
            for fname, ann, required in specs3:
                resolved = _resolve(ann)
                annotations2[fname] = resolved if required else _Optional[resolved]
                if not required:
                    defaults2[fname] = None
            cls_dict2: Dict[str, Any] = {
                "__annotations__": annotations2,
                "model_config": ConfigDict(arbitrary_types_allowed=True),
                **defaults2,
            }
            inputs[in_name] = type(in_name, (BaseModel,), cls_dict2)

    # Rebuild forward references — Pydantic v2 `model_rebuild` resolves
    # string-named annotations once all sibling models exist.
    namespace = {**models, **enums, **inputs}
    for cls in models.values():
        try:
            cls.model_rebuild(_types_namespace=namespace)  # type: ignore[attr-defined]
        except Exception:
            continue
    for cls in inputs.values():
        try:
            cls.model_rebuild(_types_namespace=namespace)  # type: ignore[attr-defined]
        except Exception:
            continue

    return SDLToPydanticResult(models=models, enums=enums, inputs=inputs)


def _gql_type_to_python(
    type_node: Any, fullname: Callable[[str], str]
) -> Tuple[Any, bool]:
    """Translate a GraphQL type node into ``(python_annotation, required)``.

    Required means non-null on the outermost wrapper. Inner non-null status
    is preserved on list element types via ``List[T]`` (Pydantic does not
    distinguish ``List[T!]`` from ``List[T]`` in serialization).
    """

    from typing import Optional as _Optional, List as _List

    required = False
    if isinstance(type_node, gql_ast.NonNullTypeNode):
        required = True
        type_node = type_node.type
    if isinstance(type_node, gql_ast.ListTypeNode):
        inner_ann, _inner_required = _gql_type_to_python(type_node.type, fullname)
        return _List[inner_ann], required  # type: ignore[valid-type]
    if isinstance(type_node, gql_ast.NamedTypeNode):
        name = type_node.name.value
        if name in GQL_SCALAR_TO_PY:
            return GQL_SCALAR_TO_PY[name], required
        return fullname(name), required
    return Any, required


# ---------------------------------------------------------------------------
# GQL upstream transport (used by `*_via_provider` adapters)
# ---------------------------------------------------------------------------


class GQLUpstreamTransport:
    """Send GraphQL operations to a federated upstream.

    The transport is a thin wrapper around ``ProviderHTTPClient`` that fixes
    the ``query``/``variables`` body shape, the response envelope, and the
    selection-set push-down. Concrete external models call into this
    transport from their ``*_via_provider`` methods so the rotation system,
    auth strategy, idempotency machinery, and rate limiting all participate
    automatically — this module never re-implements any of those.
    """

    def __init__(self, http_client: Any, upstream_url: str) -> None:
        self._http = http_client
        self._url = upstream_url

    async def send(
        self,
        *,
        query: str,
        variables: Optional[Mapping[str, Any]] = None,
        requester_id: Optional[str] = None,
    ) -> Any:
        """Send a GraphQL operation upstream and return the JSON-decoded body."""

        body: Dict[str, Any] = {"query": query}
        if variables is not None:
            body["variables"] = dict(variables)
        return await self._http.post(
            self._url, json=body, requester_id=requester_id
        )

    def send_sync(
        self,
        *,
        query: str,
        variables: Optional[Mapping[str, Any]] = None,
        requester_id: Optional[str] = None,
    ) -> Any:
        body: Dict[str, Any] = {"query": query}
        if variables is not None:
            body["variables"] = dict(variables)
        return self._http.post(self._url, json=body, requester_id=requester_id)


def build_proxy_resolver(
    *,
    operation: str,
    transport: Callable[..., Awaitable[Any]],
    operation_type: str = "query",
    requester_credentials: str = "",
) -> Callable[..., Awaitable[Any]]:
    """Build a resolver callable that forwards selected_fields to the upstream.

    The returned callable expects ``(info, **arguments)`` and:

    1. Reconstructs the upstream selection set from ``info.selected_fields``.
    2. Looks up the per-request response cache; returns the cached value on
       hit so identical sub-requests within one outer GraphQL operation
       collapse to a single upstream call.
    3. Forwards the document via ``transport`` and returns
       ``response["data"][operation]``.

    This is the resolver attached to merged-schema fields when the framework
    is operating in stitching mode.
    """

    async def _resolver(info: Any, **arguments: Any) -> Any:
        selection_body = reconstruct_selection_set(
            getattr(info, "selected_fields", ())
        )
        formatted_args = _format_args(arguments)
        document = build_query_document(
            operation=f"{operation}{formatted_args}",
            selection_body=selection_body,
            operation_type=operation_type,
        )
        cache = get_response_cache()
        if cache is not None:
            ck = cache_key(document, None, requester_credentials)
            hit, cached = cache.get(ck)
            if hit:
                return cached
            fut, owner = cache.get_or_register_pending(
                ck, lambda: asyncio.get_event_loop().create_future()
            )
            if not owner:
                return await fut
            try:
                response = await transport(query=document, variables=None)
            except BaseException as exc:
                cache.fail_pending(ck, exc)
                raise
            value = _extract_single(response, operation)
            cache.resolve_pending(ck, value)
            return value
        response = await transport(query=document, variables=None)
        return _extract_single(response, operation)

    _resolver.__name__ = f"federated_{operation}"
    return _resolver


def _extract_single(response: Any, operation: str) -> Any:
    if isinstance(response, Mapping):
        data = response.get("data")
        if isinstance(data, Mapping):
            return data.get(operation)
    return response


# ---------------------------------------------------------------------------
# GQL → REST projection
# ---------------------------------------------------------------------------
#
# Take an external GraphQL upstream and expose its root fields as REST routes
# on the local FastAPI app. This is the inverse of REST→GQL projection in
# ``Federation_REST.py``: it lets REST clients consume an upstream that
# only speaks GraphQL, so the framework's "an external resource looks like
# a local row" claim holds for either inbound channel.


def project_gql_as_rest(
    *,
    subgraph: FederatedSubgraph,
    transport: Callable[..., Awaitable[Any]],
    prefix: str,
    operation_types: Sequence[str] = ("query", "mutation"),
) -> Any:
    """Generate a FastAPI router that projects a GQL subgraph as REST routes.

    For every root field declared on the upstream's ``Query`` / ``Mutation``
    types, a single REST route is mounted at ``{prefix}/{field_name}``. The
    route accepts JSON-serializable arguments as either query params (GET) or
    a request body (POST) and returns the decoded ``data.{field_name}``
    payload. Selection set defaults to ``__typename`` plus every leaf scalar
    on the field's return type, so REST clients receive a flat result without
    needing GraphQL knowledge.

    The returned router is unmounted; the caller decides where to attach it
    (typically under the framework's ``/extensions/{ext_name}`` umbrella).
    """

    from fastapi import APIRouter, HTTPException

    router = APIRouter(prefix=prefix, tags=[f"{subgraph.name} (gql→rest)"])
    schema = subgraph.schema
    for op_type in operation_types:
        root = (
            schema.query_type if op_type == "query" else schema.mutation_type
            if op_type == "mutation"
            else schema.subscription_type
        )
        if root is None:
            continue
        for field_name, field_def in root.fields.items():
            method = "POST" if op_type == "mutation" else "GET"
            selection_body = _default_selection_for(schema, field_def.type)
            args = list(field_def.args.keys())
            _mount_gql_to_rest_route(
                router=router,
                method=method,
                path=f"/{field_name}",
                operation=field_name,
                arg_names=args,
                selection_body=selection_body,
                transport=transport,
            )
    return router


def _default_selection_for(schema: GraphQLSchema, gql_type: Any) -> str:
    """Build a default selection set covering all scalar leaves of a type."""

    inner = _unwrap_type(gql_type)
    if inner is None or not hasattr(inner, "fields"):
        return ""
    pieces: List[str] = ["__typename"]
    for name, fdef in inner.fields.items():
        ftype = _unwrap_type(fdef.type)
        if ftype is None:
            continue
        if hasattr(ftype, "fields"):
            # Composite — descend one level to keep the projection reasonable
            sub = ", ".join(["__typename"] + list(ftype.fields.keys()))
            pieces.append(f"{name} {{ {sub} }}")
        else:
            pieces.append(name)
    return " ".join(pieces)


def _unwrap_type(t: Any) -> Any:
    """Strip ``NonNull`` / ``List`` wrappers off a GraphQL type."""

    while True:
        of_type = getattr(t, "of_type", None)
        if of_type is None:
            return t
        t = of_type


def _mount_gql_to_rest_route(
    *,
    router: Any,
    method: str,
    path: str,
    operation: str,
    arg_names: Sequence[str],
    selection_body: str,
    transport: Callable[..., Awaitable[Any]],
) -> None:
    from fastapi import HTTPException, Request

    async def handler(request: "Request") -> Any:  # type: ignore[name-defined]
        params: Dict[str, Any] = {}
        if method == "POST":
            try:
                body = await request.json()
            except Exception:
                body = {}
            if isinstance(body, Mapping):
                params.update(body)
        for k, v in request.query_params.multi_items():
            if k not in params:
                params[k] = v
        rendered_args = _format_args({k: v for k, v in params.items() if k in arg_names})
        document = build_query_document(
            operation=f"{operation}{rendered_args}",
            selection_body=selection_body,
            operation_type="mutation" if method == "POST" else "query",
        )
        try:
            response = await transport(query=document, variables=None)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        if isinstance(response, Mapping):
            errors = response.get("errors")
            data = response.get("data")
            if errors and not data:
                raise HTTPException(status_code=502, detail=errors)
            if isinstance(data, Mapping):
                return data.get(operation)
        return response

    handler.__name__ = f"gql_to_rest_{operation}"
    if method == "GET":
        router.get(path, name=f"gql_rest_{operation}")(handler)
    else:
        router.post(path, name=f"gql_rest_{operation}")(handler)


__all__ = [
    "INTROSPECTION_QUERY",
    "APOLLO_SDL_QUERY",
    "APOLLO_DIRECTIVES_PREAMBLE",
    "reconstruct_selection_set",
    "build_query_document",
    "SchemaTransformer",
    "IngestedSchema",
    "ingest_introspection",
    "ingest_apollo_sdl",
    "detect_federation_directives",
    "cache_key",
    "ResponseCache",
    "bind_response_cache",
    "reset_response_cache",
    "get_response_cache",
    "get_persistent_cache",
    "BatchedFieldResolver",
    "bind_batched_resolver",
    "reset_batched_resolver",
    "get_batched_resolver",
    "FederatedSubgraph",
    "MergedSchemaRegistry",
    "global_registry",
    "reset_global_registry",
    "reset_individual_semaphores",
    "project_gql_as_rest",
    "GQL_SCALAR_TO_PY",
    "SDLToPydanticResult",
    "sdl_to_pydantic_models",
    "GQLUpstreamTransport",
    "build_proxy_resolver",
]
