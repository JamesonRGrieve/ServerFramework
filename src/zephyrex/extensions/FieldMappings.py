"""
Field mapping pipeline for converting between internal data shapes and
external API payloads.

Item 6 — Field-mapping pipeline beyond 1:1 renames.

Replaces the legacy `field_mappings: Dict[str, str]` rename-only contract
with a typed list of transformations that compose to form the full
internal-to-external translation pipeline. Each `FieldMapping` is
reversible (`to_external` is paired with `from_external`), so round-trip
integrity is preserved automatically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Union


# ---------------------------------------------------------------------------
# Base contract
# ---------------------------------------------------------------------------


class FieldMapping(ABC):
    """
    Abstract base class for a single declarative field transformation.

    Concrete subclasses operate on the entire dictionary so that mappings
    that produce or consume multiple fields (e.g., `Compose`, `Decompose`)
    can be expressed naturally.
    """

    @abstractmethod
    def to_external(self, internal: Dict[str, Any], external: Dict[str, Any]) -> None:
        """Apply this mapping in the internal -> external direction.

        Read inputs from `internal` and write outputs into `external`
        (mutating in place).
        """

    @abstractmethod
    def from_external(self, external: Dict[str, Any], internal: Dict[str, Any]) -> None:
        """Apply this mapping in the external -> internal direction."""


# ---------------------------------------------------------------------------
# Concrete mappings
# ---------------------------------------------------------------------------


class Rename(FieldMapping):
    """Rename a single field from `internal` -> `external`."""

    def __init__(self, internal: str, external: str) -> None:
        self.internal = internal
        self.external = external

    def to_external(self, internal: Dict[str, Any], external: Dict[str, Any]) -> None:
        if self.internal in internal:
            external[self.external] = internal[self.internal]

    def from_external(self, external: Dict[str, Any], internal: Dict[str, Any]) -> None:
        if self.external in external:
            internal[self.internal] = external[self.external]


class Compose(FieldMapping):
    """Combine multiple internal fields into one external field.

    `fn(values_in_order_of_externals)` produces the external value;
    `inverse_fn(external_value)` produces a list of internal values in
    the same order.
    """

    def __init__(
        self,
        externals: List[str],
        internal: str,
        fn: Callable[..., Any],
        inverse_fn: Callable[[Any], Sequence[Any]],
    ) -> None:
        # Note: param names mirror the prose in IMPROVEMENTS_ORDERED Item 6.
        # `externals` here lists the *internal* field names that are
        # combined into one *external* output field. Reads from internal,
        # writes to external.
        self.internal_fields = externals
        self.external = internal
        self.fn = fn
        self.inverse_fn = inverse_fn

    def to_external(self, internal: Dict[str, Any], external: Dict[str, Any]) -> None:
        if all(f in internal for f in self.internal_fields):
            values = [internal[f] for f in self.internal_fields]
            external[self.external] = self.fn(*values)

    def from_external(self, external: Dict[str, Any], internal: Dict[str, Any]) -> None:
        if self.external in external:
            parts = list(self.inverse_fn(external[self.external]))
            for name, value in zip(self.internal_fields, parts):
                internal[name] = value


class Decompose(FieldMapping):
    """Split a single external field into multiple internal fields."""

    def __init__(
        self,
        external: str,
        internals: List[str],
        fn: Callable[[Any], Sequence[Any]],
        inverse_fn: Callable[..., Any],
    ) -> None:
        self.external = external
        self.internals = internals
        self.fn = fn
        self.inverse_fn = inverse_fn

    def to_external(self, internal: Dict[str, Any], external: Dict[str, Any]) -> None:
        if all(f in internal for f in self.internals):
            values = [internal[f] for f in self.internals]
            external[self.external] = self.inverse_fn(*values)

    def from_external(self, external: Dict[str, Any], internal: Dict[str, Any]) -> None:
        if self.external in external:
            parts = list(self.fn(external[self.external]))
            for name, value in zip(self.internals, parts):
                internal[name] = value


class DotPath(FieldMapping):
    """Map a flat internal field to a dotted external path
    (e.g., `address.line1`).
    """

    def __init__(self, internal: str, external_path: str) -> None:
        self.internal = internal
        self.external_path = external_path
        self._segments = external_path.split(".")

    def to_external(self, internal: Dict[str, Any], external: Dict[str, Any]) -> None:
        if self.internal not in internal:
            return
        cursor = external
        for seg in self._segments[:-1]:
            cursor = cursor.setdefault(seg, {})
            if not isinstance(cursor, dict):
                raise TypeError(
                    f"DotPath collision at {seg!r} in {self.external_path!r}: "
                    f"existing value is not a dict"
                )
        cursor[self._segments[-1]] = internal[self.internal]

    def from_external(self, external: Dict[str, Any], internal: Dict[str, Any]) -> None:
        cursor: Any = external
        for seg in self._segments:
            if not isinstance(cursor, dict) or seg not in cursor:
                return
            cursor = cursor[seg]
        internal[self.internal] = cursor


class UnitConvert(FieldMapping):
    """Multiply / divide by a constant factor when crossing the boundary.

    `to_external`: external = internal * factor.
    `from_external`: internal = external / factor.
    """

    def __init__(
        self,
        internal: str,
        external: str,
        factor: Union[int, float, Decimal],
    ) -> None:
        self.internal = internal
        self.external = external
        # Coerce factor to Decimal so multiplication with Decimal inputs works.
        self.factor = Decimal(str(factor))

    def to_external(self, internal: Dict[str, Any], external: Dict[str, Any]) -> None:
        if self.internal in internal:
            value = internal[self.internal]
            if value is None:
                external[self.external] = None
                return
            if isinstance(value, Decimal):
                converted = value * self.factor
            else:
                converted = Decimal(str(value)) * self.factor
            # Try to return an int if the result is whole.
            if converted == converted.to_integral_value():
                external[self.external] = int(converted)
            else:
                external[self.external] = converted

    def from_external(self, external: Dict[str, Any], internal: Dict[str, Any]) -> None:
        if self.external in external:
            value = external[self.external]
            if value is None:
                internal[self.internal] = None
                return
            converted = Decimal(str(value)) / self.factor
            internal[self.internal] = converted


class CentsToDecimal(UnitConvert):
    """Common Stripe-style mapping: internal Decimal dollars <->
    external integer cents."""

    def __init__(self, internal: str, external: str) -> None:
        super().__init__(internal, external, Decimal("100"))

    def to_external(self, internal: Dict[str, Any], external: Dict[str, Any]) -> None:
        if self.internal in internal:
            value = internal[self.internal]
            if value is None:
                external[self.external] = None
            else:
                # Always integer cents.
                external[self.external] = int(Decimal(str(value)) * Decimal("100"))


class EnumRemap(FieldMapping):
    """Bidirectional enum / vocabulary translation.

    `mapping` is the internal -> external direction; the inverse is
    derived. The mapping must be 1-1 (each external value mapped from
    exactly one internal value); duplicate external values raise at
    construction time.
    """

    def __init__(self, internal: str, external: str, mapping: Dict[Any, Any]) -> None:
        self.internal = internal
        self.external = external
        self.forward = dict(mapping)
        self.inverse: Dict[Any, Any] = {}
        for k, v in self.forward.items():
            if v in self.inverse:
                raise ValueError(
                    f"EnumRemap mapping is not 1-1: external value {v!r} appears "
                    f"for both {self.inverse[v]!r} and {k!r}"
                )
            self.inverse[v] = k

    def to_external(self, internal: Dict[str, Any], external: Dict[str, Any]) -> None:
        if self.internal in internal:
            v = internal[self.internal]
            external[self.external] = self.forward.get(v, v)

    def from_external(self, external: Dict[str, Any], internal: Dict[str, Any]) -> None:
        if self.external in external:
            v = external[self.external]
            internal[self.internal] = self.inverse.get(v, v)


class TimestampConvert(FieldMapping):
    """Convert between a `datetime` (internal) and a string (external).

    `format` is an `strftime` / `strptime` format string. The empty
    string format selects ISO-8601 (`isoformat()` / `fromisoformat()`).
    """

    def __init__(self, internal: str, external: str, format: str = "") -> None:
        self.internal = internal
        self.external = external
        self.format = format

    def to_external(self, internal: Dict[str, Any], external: Dict[str, Any]) -> None:
        if self.internal in internal:
            v = internal[self.internal]
            if v is None:
                external[self.external] = None
                return
            if isinstance(v, datetime):
                external[self.external] = v.isoformat() if not self.format else v.strftime(self.format)
            else:
                # Already a string; pass through.
                external[self.external] = v

    def from_external(self, external: Dict[str, Any], internal: Dict[str, Any]) -> None:
        if self.external in external:
            v = external[self.external]
            if v is None:
                internal[self.internal] = None
            elif isinstance(v, str):
                if self.format:
                    internal[self.internal] = datetime.strptime(v, self.format)
                else:
                    internal[self.internal] = datetime.fromisoformat(v)
            else:
                internal[self.internal] = v


class Custom(FieldMapping):
    """Escape hatch for everything not expressible by the built-ins.

    Both `fn_to(internal_dict) -> external_partial_dict` and
    `fn_from(external_dict) -> internal_partial_dict` must be supplied.
    The framework does not attempt to derive an inverse.
    """

    def __init__(
        self,
        fn_to: Callable[[Dict[str, Any]], Dict[str, Any]],
        fn_from: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> None:
        self.fn_to = fn_to
        self.fn_from = fn_from

    def to_external(self, internal: Dict[str, Any], external: Dict[str, Any]) -> None:
        partial = self.fn_to(internal)
        if partial:
            external.update(partial)

    def from_external(self, external: Dict[str, Any], internal: Dict[str, Any]) -> None:
        partial = self.fn_from(external)
        if partial:
            internal.update(partial)


# ---------------------------------------------------------------------------
# Pipeline application helpers
# ---------------------------------------------------------------------------


MappingsLike = Union[Dict[str, str], List[FieldMapping], None]


def normalize_mappings(mappings: MappingsLike) -> List[FieldMapping]:
    """Coerce the legacy `Dict[str, str]` shape into the typed list shape.

    Accepts:
        - None       -> []
        - Dict[str, str] -> [Rename(k, v) for k, v in items]
        - List[FieldMapping] -> returned as-is
    """

    if mappings is None:
        return []
    if isinstance(mappings, dict):
        return [Rename(internal=k, external=v) for k, v in mappings.items()]
    if isinstance(mappings, list):
        return list(mappings)
    raise TypeError(
        f"field_mappings must be a Dict[str, str] or List[FieldMapping]; got {type(mappings).__name__}"
    )


def apply_to_external(
    mappings: List[FieldMapping],
    internal_dict: Dict[str, Any],
    *,
    pass_through_unmapped: bool = True,
) -> Dict[str, Any]:
    """Convert internal dict to external dict using the given mappings.

    Fields in `internal_dict` that are not consumed by any mapping are
    copied through unchanged when `pass_through_unmapped` is True
    (default), so that callers can opt in to mappings field-by-field
    without having to enumerate the full schema.
    """

    consumed: set = set()
    external: Dict[str, Any] = {}

    for mapping in mappings:
        # Snapshot the keys present in the external dict so we can detect
        # which internal field a Custom mapping read.
        before_external_keys = set(external.keys())
        mapping.to_external(internal_dict, external)
        # Track which internal keys this mapping consumed for pass-through.
        for f in _internal_fields_consumed(mapping):
            consumed.add(f)
        # No additional bookkeeping needed; pass-through uses `consumed`.
        del before_external_keys  # reserved for future debugging hooks.

    if pass_through_unmapped:
        for k, v in internal_dict.items():
            if k not in consumed and k not in external:
                external[k] = v

    return external


def apply_from_external(
    mappings: List[FieldMapping],
    external_dict: Dict[str, Any],
    *,
    pass_through_unmapped: bool = True,
) -> Dict[str, Any]:
    """Convert external dict to internal dict using the given mappings."""

    consumed: set = set()
    internal: Dict[str, Any] = {}

    for mapping in mappings:
        mapping.from_external(external_dict, internal)
        for f in _external_fields_consumed(mapping):
            consumed.add(f)

    if pass_through_unmapped:
        for k, v in external_dict.items():
            if k not in consumed and k not in internal:
                internal[k] = v

    return internal


def _internal_fields_consumed(mapping: FieldMapping) -> List[str]:
    """Return the internal field names a mapping reads in `to_external`.

    Used by `apply_to_external` to decide which fields are pass-through
    candidates. Custom mappings declare nothing; they receive the full
    internal dict and the pass-through layer treats their inputs as
    unowned.
    """

    if isinstance(mapping, Rename):
        return [mapping.internal]
    if isinstance(mapping, Compose):
        return list(mapping.internal_fields)
    if isinstance(mapping, Decompose):
        return list(mapping.internals)
    if isinstance(mapping, DotPath):
        return [mapping.internal]
    if isinstance(mapping, UnitConvert):
        return [mapping.internal]
    if isinstance(mapping, EnumRemap):
        return [mapping.internal]
    if isinstance(mapping, TimestampConvert):
        return [mapping.internal]
    return []


def _external_fields_consumed(mapping: FieldMapping) -> List[str]:
    """Counterpart of `_internal_fields_consumed` for the inbound pipeline."""

    if isinstance(mapping, Rename):
        return [mapping.external]
    if isinstance(mapping, Compose):
        return [mapping.external]
    if isinstance(mapping, Decompose):
        return [mapping.external]
    if isinstance(mapping, DotPath):
        # The top-level segment is the consumed external key.
        return [mapping._segments[0]]
    if isinstance(mapping, UnitConvert):
        return [mapping.external]
    if isinstance(mapping, EnumRemap):
        return [mapping.external]
    if isinstance(mapping, TimestampConvert):
        return [mapping.external]
    return []


__all__ = [
    "FieldMapping",
    "Rename",
    "Compose",
    "Decompose",
    "DotPath",
    "UnitConvert",
    "CentsToDecimal",
    "EnumRemap",
    "TimestampConvert",
    "Custom",
    "MappingsLike",
    "normalize_mappings",
    "apply_to_external",
    "apply_from_external",
]
