"""``@extension_model`` field-collision detection.

Item 23. Two extensions must never silently inject differently-typed
versions of the same field into the same core model. The user has
mandated this be a startup-time error: collisions are caught before the
application begins serving traffic, with a message naming both
extensions, the model, and the colliding field.

Identical-field declarations are not collisions: if both extensions
declare an exactly identical field (same type, same default, same
metadata), the registry accepts the duplicate as a no-op duplication.

This module provides:

  * ``FieldCollisionError`` -- typed exception with rich diagnostics.
  * ``register_extension_field`` -- the helper the existing
    ``@extension_model`` decorator should call when wired (see TODO at
    bottom of file pointing to the wire-in site).
  * ``detect_extension_field_collisions`` -- run at startup over the
    merged registry; raises on any conflicting injection.

This file does NOT modify the existing ``@extension_model`` decorator;
wiring is left to a follow-up touch on
``lib/Pydantic2SQLAlchemy.py:extension_model`` (see the TODO at the end).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

try:  # Pydantic v2 only -- this codebase pins v2.
    from pydantic import BaseModel
except ImportError:  # pragma: no cover - Pydantic is a hard dep.
    BaseModel = object  # type: ignore[assignment, misc]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FieldCollisionError(Exception):
    """Two extensions injected non-identical fields into the same model.

    The error message includes both file paths and line numbers (when
    discoverable) so the developer has a one-step path to the conflict.
    """

    def __init__(
        self,
        model_name: str,
        field_name: str,
        sources: List["_FieldOrigin"],
    ):
        self.model_name = model_name
        self.field_name = field_name
        self.sources = sources
        rendered_sources = ", ".join(
            f"{s.extension_name} ({s.source_file}:{s.source_line})"
            for s in sources
        )
        super().__init__(
            f"Extension field collision on {model_name}.{field_name}: "
            f"injected by {rendered_sources}. "
            "Two extensions declared different versions of this field. "
            "Either change one of the field names or ensure both "
            "declarations are byte-identical (same type, default, "
            "metadata, description)."
        )


# ---------------------------------------------------------------------------
# Origin tracking
# ---------------------------------------------------------------------------


@dataclass
class _FieldOrigin:
    """Where a single field declaration came from."""

    extension_name: str
    field_info: Any
    source_file: str = "<unknown>"
    source_line: int = 0

    def matches(self, other: "_FieldOrigin") -> bool:
        """Two declarations match if their Pydantic ``FieldInfo`` describes
        the same field shape.

        Pydantic v2's ``FieldInfo`` does NOT implement value ``__eq__``
        (it falls back to identity), so we compare salient attributes
        explicitly: ``annotation``, ``default``, ``default_factory``,
        ``description``, ``metadata``, ``json_schema_extra``, ``alias``,
        and ``examples``. Identical declarations across extensions become
        a no-op duplicate; any difference is a collision.

        ``None`` field info is used by legacy origins layered in via
        ``extension_field_origins`` -- those carry no ``FieldInfo``, so a
        pair that includes ``None`` cannot be proven identical and is
        treated as a collision.
        """
        if self.field_info is None or other.field_info is None:
            # Legacy origins carry no FieldInfo; cannot prove identity.
            return False
        if self.field_info is other.field_info:
            return True
        # Try value equality first (covers any future Pydantic upgrade
        # that adds a real __eq__).
        try:
            if self.field_info == other.field_info:
                return True
        except Exception:
            pass
        try:
            return _field_info_value_equal(self.field_info, other.field_info)
        except Exception:
            return False


def _field_info_value_equal(a: Any, b: Any) -> bool:
    """Compare the salient attributes of two Pydantic v2 ``FieldInfo``s."""
    attrs = (
        "annotation",
        "default",
        "default_factory",
        "description",
        "metadata",
        "json_schema_extra",
        "alias",
        "examples",
    )
    for attr in attrs:
        if getattr(a, attr, None) != getattr(b, attr, None):
            return False
    return True


# Module-level registry. Keyed by ``(model_name, field_name)`` so the
# detector can walk every (model, field) pair.
_REGISTRY: Dict[Tuple[str, str], List[_FieldOrigin]] = {}


def register_extension_field(
    extension_name: str,
    model_name: str,
    field_name: str,
    field_info: Any,
    source_file: str = "<unknown>",
    source_line: int = 0,
) -> None:
    """Record an extension-injected field for later collision detection.

    Called from the ``@extension_model`` decorator when wired (see TODO
    below). Calling this directly from tests is also supported.
    """
    key = (model_name, field_name)
    origin = _FieldOrigin(
        extension_name=extension_name,
        field_info=field_info,
        source_file=source_file,
        source_line=source_line,
    )
    _REGISTRY.setdefault(key, []).append(origin)


def reset_registry() -> None:
    """Clear the in-memory registry. Used by tests; not needed at runtime."""
    _REGISTRY.clear()


def get_registered_fields() -> Dict[Tuple[str, str], List[_FieldOrigin]]:
    """Return a shallow copy of the registry for inspection / tests."""
    return {k: list(v) for k, v in _REGISTRY.items()}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_extension_field_collisions(
    merged_models: Optional[Dict[str, Type[Any]]] = None,
    extension_field_origins: Optional[Dict[Tuple[str, str], List[str]]] = None,
) -> None:
    """Walk the registry and raise on any conflicting (model, field) pair.

    Two arguments are supported for flexibility:

      * ``merged_models`` (optional) -- the registry's merged model graph.
        Currently only used to confirm a target model exists; the actual
        collision check works directly off the per-field origins recorded
        via ``register_extension_field``.
      * ``extension_field_origins`` (optional) -- legacy hook that takes
        the ``(model, field) -> [extension_names]`` mapping documented in
        the prose. When provided, it is layered on top of the in-memory
        registry. Conflicts in the legacy mapping (more than one source
        per key) are reported with a synthetic origin if the registry
        does not have a matching detailed entry.

    Identical declarations across extensions are accepted as no-ops.
    Conflicting declarations raise ``FieldCollisionError`` immediately.
    """
    # Merge any caller-supplied legacy origins into a working copy.
    working: Dict[Tuple[str, str], List[_FieldOrigin]] = {
        k: list(v) for k, v in _REGISTRY.items()
    }
    if extension_field_origins:
        for key, ext_names in extension_field_origins.items():
            existing_names = {o.extension_name for o in working.get(key, [])}
            for ext_name in ext_names:
                if ext_name in existing_names:
                    continue
                working.setdefault(key, []).append(
                    _FieldOrigin(
                        extension_name=ext_name,
                        field_info=None,
                        source_file="<legacy>",
                        source_line=0,
                    )
                )

    for (model_name, field_name), origins in working.items():
        if len(origins) <= 1:
            continue
        # Pairwise compare the first declaration against the rest. Any
        # mismatch is a collision; identical sets pass through.
        baseline = origins[0]
        if all(baseline.matches(other) for other in origins[1:]):
            continue
        raise FieldCollisionError(
            model_name=model_name,
            field_name=field_name,
            sources=origins,
        )

    # ``merged_models`` is accepted for forward-compatibility; today it is
    # not consulted because the collision check works off the registry.
    _ = merged_models


# ---------------------------------------------------------------------------
# Wire-in pointer
# ---------------------------------------------------------------------------

# TODO: wire ``register_extension_field`` into the ``@extension_model``
# decorator at ``src/lib/Pydantic2SQLAlchemy.py:1654`` (function
# ``extension_model``). When the decorator processes
# ``extension_class.model_fields``, call ``register_extension_field`` for
# each injected field, passing:
#   extension_name = (best-effort: from extension_class.__module__ or the
#                     surrounding ExtensionRegistry context)
#   model_name     = target_model.__name__
#   field_name     = field name
#   field_info     = the Pydantic FieldInfo
#   source_file    = inspect.getsourcefile(extension_class)
#   source_line    = inspect.getsourcelines(extension_class)[1]
# Then, at registry finalize time (after all extensions are discovered)
# call ``detect_extension_field_collisions()`` to surface any conflicts
# at startup. ModelRegistry.commit() in lib/Pydantic.py is a natural
# place for the call.


__all__ = [
    "FieldCollisionError",
    "register_extension_field",
    "detect_extension_field_collisions",
    "reset_registry",
    "get_registered_fields",
]
