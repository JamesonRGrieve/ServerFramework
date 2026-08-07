"""Schema-drift detection for external-provider OpenAPI specs (Item 11).

External APIs change beneath us — fields appear, enums shift, error
envelopes get reshaped. Without contract tests the first signal is a
production incident. This module gives federation authors three pieces:

1. **OpenAPI fetch + diff** — pull the upstream spec live, structurally
   diff it against a committed snapshot, classify each delta as breaking
   or non-breaking.
2. **Response-snapshot recording** — for upstreams that don't publish a
   machine-readable spec, capture real recorded responses and run a
   structural diff against committed samples.
3. **Convention** — extension authors set ``openapi_url: ClassVar[Optional[str]]``
   on their provider classes (typically the extension's
   ``AbstractStaticProvider`` subclass). The CI job iterates committed
   snapshots under ``src/extensions/{name}/contracts/{provider}.openapi.json``
   and calls ``diff_openapi`` against the live fetch.

Convention (per Item 11):

    class Stripe_Provider(AbstractDatabaseExtensionProvider):
        openapi_url: ClassVar[Optional[str]] = (
            "https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json"
        )

We document it here rather than modifying ``AbstractStaticProvider`` —
the convention is opt-in and the absence of the attribute means "no
upstream spec to diff."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

import json


# ---------------------------------------------------------------------------
# DiffEntry
# ---------------------------------------------------------------------------


DiffKind = Literal["breaking", "non_breaking"]


@dataclass(frozen=True)
class DiffEntry:
    """A single structural difference between two specs/snapshots.

    Attributes:
        kind: ``breaking`` or ``non_breaking``. Item 11 spec:
            removed fields, narrowed types, removed enum values are
            ``breaking``; added fields and widened types are
            ``non_breaking``.
        path: Dot-separated JSON path to the differing element
            (``components.schemas.User.properties.email``).
        detail: Human-readable description of the change.
    """

    kind: DiffKind
    path: str
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "detail": self.detail}


def is_breaking(diff: List[DiffEntry]) -> bool:
    """Return True iff any entry in ``diff`` is classified as breaking."""
    return any(entry.kind == "breaking" for entry in diff)


# ---------------------------------------------------------------------------
# OpenAPI fetch
# ---------------------------------------------------------------------------


def fetch_openapi_spec(
    url: str,
    auth_strategy: Optional[Callable[[Dict[str, str]], Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Fetch and parse an OpenAPI spec.

    Args:
        url: URL to fetch (typically a JSON or YAML OpenAPI document).
            YAML is *not* supported by this in-repo helper — convert to
            JSON in CI before passing.
        auth_strategy: Optional callable that receives a header dict and
            returns the augmented dict. Use this for upstreams that
            require authentication on their spec endpoint (e.g. a
            ``Authorization: Bearer ...`` for GitHub's spec).

    Returns:
        The parsed spec as a dict.

    Raises:
        RuntimeError: when ``httpx`` is not installed (the framework
            already depends on httpx; this guard catches misconfigured
            test environments).
        Any ``httpx`` exception when the request fails.
    """
    try:
        import httpx  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - httpx is a runtime dep
        raise RuntimeError(
            "httpx is required for fetch_openapi_spec; install via pip"
        ) from exc

    headers: Dict[str, str] = {"Accept": "application/json"}
    if auth_strategy is not None:
        headers = auth_strategy(headers)

    # Bound redirect chain. Unbounded follow_redirects allows a hostile
    # endpoint to bounce the fetcher to internal services (cloud-metadata,
    # localhost) — the SSRF cousin of an open redirect.
    transport = httpx.HTTPTransport(retries=0)
    with httpx.Client(
        transport=transport,
        follow_redirects=True,
        max_redirects=3,
        timeout=30.0,
    ) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# OpenAPI diff
# ---------------------------------------------------------------------------


# Type "narrowing" rules: when a type changes from a wider tier to a
# narrower one, that's breaking; the inverse is non-breaking. The tiers
# are not perfectly ordered (e.g. integer/string aren't comparable) but
# the JSON-Schema/OpenAPI-3 type list is small enough that we can express
# the safe widenings explicitly.
_WIDENINGS: Dict[str, set] = {
    # New type -> set of "old" types that count as widenings to it.
    "string": {"integer", "number", "boolean"},  # str can render anything
    "number": {"integer"},  # int -> num is a widening
}


def _is_widening(old: str, new: str) -> bool:
    return old in _WIDENINGS.get(new, set())


def _walk_schema(
    old: Dict[str, Any],
    new: Dict[str, Any],
    path: str,
    diff: List[DiffEntry],
) -> None:
    """Recursively diff two JSON-Schema-shaped dicts."""
    # Type changes
    old_type = old.get("type")
    new_type = new.get("type")
    if old_type and new_type and old_type != new_type:
        if _is_widening(old_type, new_type):
            diff.append(
                DiffEntry(
                    kind="non_breaking",
                    path=f"{path}.type",
                    detail=f"type widened from {old_type!r} to {new_type!r}",
                )
            )
        else:
            diff.append(
                DiffEntry(
                    kind="breaking",
                    path=f"{path}.type",
                    detail=f"type narrowed from {old_type!r} to {new_type!r}",
                )
            )

    # Enum changes
    old_enum = old.get("enum")
    new_enum = new.get("enum")
    if old_enum is not None or new_enum is not None:
        old_set = set(old_enum or ())
        new_set = set(new_enum or ())
        for removed in sorted(old_set - new_set, key=str):
            diff.append(
                DiffEntry(
                    kind="breaking",
                    path=f"{path}.enum",
                    detail=f"enum value removed: {removed!r}",
                )
            )
        for added in sorted(new_set - old_set, key=str):
            diff.append(
                DiffEntry(
                    kind="non_breaking",
                    path=f"{path}.enum",
                    detail=f"enum value added: {added!r}",
                )
            )

    # Properties changes (recursive)
    old_props = old.get("properties") or {}
    new_props = new.get("properties") or {}
    for prop_name in sorted(set(old_props) | set(new_props)):
        sub_path = f"{path}.properties.{prop_name}"
        if prop_name in old_props and prop_name not in new_props:
            diff.append(
                DiffEntry(
                    kind="breaking",
                    path=sub_path,
                    detail="property removed",
                )
            )
        elif prop_name in new_props and prop_name not in old_props:
            diff.append(
                DiffEntry(
                    kind="non_breaking",
                    path=sub_path,
                    detail="property added",
                )
            )
        else:
            old_sub = old_props[prop_name]
            new_sub = new_props[prop_name]
            if isinstance(old_sub, dict) and isinstance(new_sub, dict):
                _walk_schema(old_sub, new_sub, sub_path, diff)

    # Required-set changes — required additions tighten the contract,
    # required removals loosen it. Treat additions as breaking.
    old_required = set(old.get("required") or ())
    new_required = set(new.get("required") or ())
    for added in sorted(new_required - old_required):
        diff.append(
            DiffEntry(
                kind="breaking",
                path=f"{path}.required",
                detail=f"field newly required: {added!r}",
            )
        )
    for removed in sorted(old_required - new_required):
        diff.append(
            DiffEntry(
                kind="non_breaking",
                path=f"{path}.required",
                detail=f"field no longer required: {removed!r}",
            )
        )


def _walk_paths(
    old: Dict[str, Any],
    new: Dict[str, Any],
    diff: List[DiffEntry],
) -> None:
    """Diff the ``paths`` block of two OpenAPI documents."""
    for op_path in sorted(set(old) | set(new)):
        sub_path = f"paths.{op_path}"
        if op_path in old and op_path not in new:
            diff.append(
                DiffEntry(
                    kind="breaking",
                    path=sub_path,
                    detail="path removed",
                )
            )
        elif op_path in new and op_path not in old:
            diff.append(
                DiffEntry(
                    kind="non_breaking",
                    path=sub_path,
                    detail="path added",
                )
            )
        else:
            old_ops = old[op_path] or {}
            new_ops = new[op_path] or {}
            for method in sorted(set(old_ops) | set(new_ops)):
                if method.startswith("x-") or method == "parameters":
                    continue
                method_path = f"{sub_path}.{method}"
                if method in old_ops and method not in new_ops:
                    diff.append(
                        DiffEntry(
                            kind="breaking",
                            path=method_path,
                            detail="operation removed",
                        )
                    )
                elif method in new_ops and method not in old_ops:
                    diff.append(
                        DiffEntry(
                            kind="non_breaking",
                            path=method_path,
                            detail="operation added",
                        )
                    )


def _walk_components(
    old: Dict[str, Any],
    new: Dict[str, Any],
    diff: List[DiffEntry],
) -> None:
    """Diff the ``components.schemas`` block of two OpenAPI documents."""
    old_schemas = old.get("schemas") or {}
    new_schemas = new.get("schemas") or {}
    for name in sorted(set(old_schemas) | set(new_schemas)):
        sub_path = f"components.schemas.{name}"
        if name in old_schemas and name not in new_schemas:
            diff.append(
                DiffEntry(
                    kind="breaking",
                    path=sub_path,
                    detail="schema removed",
                )
            )
        elif name in new_schemas and name not in old_schemas:
            diff.append(
                DiffEntry(
                    kind="non_breaking",
                    path=sub_path,
                    detail="schema added",
                )
            )
        else:
            _walk_schema(old_schemas[name], new_schemas[name], sub_path, diff)


def diff_openapi(
    committed_path: Union[str, Path],
    live_spec: Dict[str, Any],
) -> List[DiffEntry]:
    """Structurally diff a committed snapshot against a live OpenAPI dict.

    Args:
        committed_path: Filesystem path to the committed JSON snapshot
            (typically ``src/extensions/{name}/contracts/{provider}.openapi.json``).
        live_spec: The just-fetched live spec, as a dict.

    Returns:
        A list of ``DiffEntry`` instances. Pass to ``is_breaking`` for a
        boolean gate, or print the entries to surface the diff in CI logs.
    """
    committed_path = Path(committed_path)
    if not committed_path.exists():
        # No committed snapshot yet — treat the live spec as the new
        # baseline. Surface a single non-breaking entry so callers know
        # the snapshot needs to be persisted.
        return [
            DiffEntry(
                kind="non_breaking",
                path="<root>",
                detail=(
                    f"no committed snapshot at {committed_path}; treating "
                    "live spec as new baseline"
                ),
            )
        ]
    with committed_path.open("r", encoding="utf-8") as fh:
        committed = json.load(fh)

    diff: List[DiffEntry] = []
    _walk_paths(committed.get("paths") or {}, live_spec.get("paths") or {}, diff)
    _walk_components(
        committed.get("components") or {},
        live_spec.get("components") or {},
        diff,
    )
    return diff


# ---------------------------------------------------------------------------
# Response snapshot recording / comparison
# ---------------------------------------------------------------------------


_DEFAULT_NORMALIZE = ("id", "created_at", "updated_at")


def record_response_snapshot(
    response: Dict[str, Any],
    normalize: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Strip instance-specific identifiers from a response for snapshotting.

    Args:
        response: The raw response (already parsed to a dict).
        normalize: Names of fields to redact at any depth. Defaults to
            ``["id", "created_at", "updated_at"]``. Pass an empty list
            to disable normalization.

    Returns:
        A deep-copied dict with normalized fields replaced by the sentinel
        ``"<normalized>"``. Keys are otherwise preserved exactly.
    """
    norm_set = set(normalize if normalize is not None else _DEFAULT_NORMALIZE)

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: ("<normalized>" if k in norm_set else walk(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(response)  # type: ignore[no-any-return]


def _walk_response(
    old: Any,
    new: Any,
    path: str,
    diff: List[DiffEntry],
) -> None:
    """Recursively diff two snapshot dicts/lists for response-shape drift."""
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            sub = f"{path}.{key}" if path else key
            if key in old and key not in new:
                diff.append(
                    DiffEntry(
                        kind="breaking",
                        path=sub,
                        detail="field removed",
                    )
                )
            elif key in new and key not in old:
                diff.append(
                    DiffEntry(
                        kind="non_breaking",
                        path=sub,
                        detail="field added",
                    )
                )
            else:
                _walk_response(old[key], new[key], sub, diff)
        return

    if isinstance(old, list) and isinstance(new, list):
        # Lists: compare shape of the first element if both have one.
        if old and new:
            _walk_response(old[0], new[0], f"{path}[0]", diff)
        return

    # Type mismatch at a leaf — count as breaking.
    if type(old) is not type(new):
        diff.append(
            DiffEntry(
                kind="breaking",
                path=path or "<root>",
                detail=(
                    f"type changed from {type(old).__name__} to "
                    f"{type(new).__name__}"
                ),
            )
        )


def compare_response_snapshots(
    committed: Dict[str, Any],
    live: Dict[str, Any],
) -> List[DiffEntry]:
    """Structural diff of two normalized response snapshots."""
    diff: List[DiffEntry] = []
    _walk_response(committed, live, "", diff)
    return diff


__all__ = [
    "DiffEntry",
    "DiffKind",
    "is_breaking",
    "fetch_openapi_spec",
    "diff_openapi",
    "record_response_snapshot",
    "compare_response_snapshots",
]
