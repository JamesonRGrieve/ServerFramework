"""Event-schema compatibility checker (Item 42 follow-up).

Modeled on Item 11's contract-drift snapshots: every event class on the
event bus has its Pydantic schema captured into a committed JSON snapshot.
A CI step compares the live schema against the snapshot and fails on any
*breaking* change:

- Removing a field
- Renaming a field
- Narrowing a field's type
- Tightening optionality (making an optional field required)

Additive changes (new optional fields, new enum values) are non-breaking
and allowed; the snapshot updates without failing the build.

This module is deliberately self-contained — no broker dependencies, no
network — so the checker can run in any CI job that imports the events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Type

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Schema extraction
# ---------------------------------------------------------------------------


def event_schema(event_class: Type[BaseModel]) -> Dict[str, Any]:
    """Return a normalized schema dict for ``event_class``.

    Normalization ensures the diff is stable across Pydantic versions:
    sorted field order, type-name normalization (e.g. `Optional[str]` →
    `{"type": "str", "optional": true}`), default-value extraction.
    """
    fields: Dict[str, Dict[str, Any]] = {}
    for fname, finfo in sorted(event_class.model_fields.items()):
        annotation = finfo.annotation
        type_name = _type_name(annotation)
        optional = _is_optional(annotation)
        required = finfo.is_required()
        entry: Dict[str, Any] = {
            "type": type_name,
            "optional": optional,
            "required": required,
        }
        if not required:
            try:
                # `default` is PydanticUndefined when there's no default;
                # we treat that as no entry.
                default = finfo.default
                if default is not None and not _is_pydantic_undefined(default):
                    entry["default"] = _safe_jsonify(default)
            except Exception:  # noqa: BLE001
                pass
        fields[fname] = entry
    return {
        "module": event_class.__module__,
        "name": event_class.__name__,
        "fields": fields,
    }


def _type_name(annotation: Any) -> str:
    if annotation is None:
        return "None"
    name = getattr(annotation, "__name__", None)
    if name is not None:
        return name
    # Optional[X], Union[X, None], List[X], etc. — fall back to repr.
    return str(annotation).replace("typing.", "")


def _is_optional(annotation: Any) -> bool:
    origin = getattr(annotation, "__origin__", None)
    if origin is None:
        # Direct Optional[X] resolves to typing.Union with NoneType.
        from typing import get_args, get_origin

        origin = get_origin(annotation)
        if origin is None:
            return False
    from typing import Union, get_args

    if origin is Union:
        return type(None) in get_args(annotation)
    return False


def _is_pydantic_undefined(value: Any) -> bool:
    return repr(value).endswith("PydanticUndefined")


def _safe_jsonify(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


# ---------------------------------------------------------------------------
# Snapshot file format
# ---------------------------------------------------------------------------


def write_snapshot(
    snapshot_path: Path,
    event_classes: Sequence[Type[BaseModel]],
) -> None:
    """Write the snapshot file for ``event_classes`` to ``snapshot_path``.

    Snapshot is sorted by ``module.name`` so the file is byte-stable
    across runs and reviewable as a diff in PRs.
    """
    schemas = [event_schema(c) for c in event_classes]
    schemas.sort(key=lambda s: (s["module"], s["name"]))
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps({"schemas": schemas}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def read_snapshot(snapshot_path: Path) -> List[Dict[str, Any]]:
    """Read the snapshot file. Returns the schemas list."""
    if not snapshot_path.exists():
        return []
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    return list(payload.get("schemas") or [])


# ---------------------------------------------------------------------------
# Compatibility check
# ---------------------------------------------------------------------------


@dataclass
class CompatibilityFinding:
    """A single breaking-change finding from the compatibility checker."""

    event: str
    kind: str  # "field_removed", "field_renamed", "type_narrowed", "now_required"
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"event": self.event, "kind": self.kind, "detail": self.detail}


@dataclass
class CompatibilityReport:
    """Outcome of a single check run."""

    breaking: List[CompatibilityFinding] = field(default_factory=list)
    additive: List[str] = field(default_factory=list)

    @property
    def is_compatible(self) -> bool:
        return not self.breaking


def check_compatibility(
    snapshot_schemas: Iterable[Dict[str, Any]],
    live_event_classes: Sequence[Type[BaseModel]],
) -> CompatibilityReport:
    """Compare live event classes against a committed snapshot.

    Reports every breaking change (field removed, renamed, type narrowed,
    or made required-from-optional) as a `CompatibilityFinding`. Additive
    changes (new fields, new enum values) are recorded as additive notes
    but do not produce findings.

    A class present in the snapshot but missing from the live set produces
    a `field_removed`-shaped finding for the whole class — removing an
    event type is a breaking change.
    """
    live_by_key = {
        f"{c.__module__}.{c.__name__}": event_schema(c) for c in live_event_classes
    }
    snapshot_by_key = {f"{s['module']}.{s['name']}": s for s in snapshot_schemas}

    report = CompatibilityReport()

    # Removed events.
    for key, snap in snapshot_by_key.items():
        if key not in live_by_key:
            report.breaking.append(
                CompatibilityFinding(
                    event=key,
                    kind="event_removed",
                    detail="Event class no longer present in live registry",
                )
            )

    # Per-event field comparisons.
    for key, live in live_by_key.items():
        snap = snapshot_by_key.get(key)
        if snap is None:
            # New event class — additive, ok.
            report.additive.append(f"new event: {key}")
            continue

        snap_fields = snap.get("fields") or {}
        live_fields = live.get("fields") or {}

        for fname, snap_meta in snap_fields.items():
            live_meta = live_fields.get(fname)
            if live_meta is None:
                report.breaking.append(
                    CompatibilityFinding(
                        event=key,
                        kind="field_removed",
                        detail=f"Field '{fname}' was removed",
                    )
                )
                continue
            if live_meta.get("type") != snap_meta.get("type"):
                report.breaking.append(
                    CompatibilityFinding(
                        event=key,
                        kind="type_narrowed",
                        detail=(
                            f"Field '{fname}' type changed from "
                            f"{snap_meta.get('type')!r} to {live_meta.get('type')!r}"
                        ),
                    )
                )
            if snap_meta.get("optional") and not live_meta.get("optional"):
                report.breaking.append(
                    CompatibilityFinding(
                        event=key,
                        kind="now_required",
                        detail=(
                            f"Field '{fname}' was optional, is now required"
                        ),
                    )
                )

        for fname in live_fields.keys():
            if fname not in snap_fields:
                report.additive.append(f"{key}: new field {fname}")

    return report


def discover_event_classes(modules: Sequence[str]) -> List[Type[BaseModel]]:
    """Walk the named modules, return every Pydantic event class found.

    "Event class" is heuristically defined as a `BaseModel` subclass
    whose name ends with one of the canonical suffixes (`Event`,
    `Created`, `Updated`, `Deleted`, `Failed`). Operators can override
    by directly listing classes in their CI script.
    """
    import importlib
    import inspect

    suffixes = ("Event", "Created", "Updated", "Deleted", "Failed")
    discovered: List[Type[BaseModel]] = []
    for mod_name in modules:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if not issubclass(obj, BaseModel) or obj is BaseModel:
                continue
            if obj.__module__ != mod.__name__:
                continue
            if obj.__name__.endswith(suffixes):
                discovered.append(obj)
    return discovered


__all__ = [
    "CompatibilityFinding",
    "CompatibilityReport",
    "event_schema",
    "write_snapshot",
    "read_snapshot",
    "check_compatibility",
    "discover_event_classes",
]
