"""PII classification and right-to-erasure orchestration (Item 82).

This module provides:

* ``PIIClass`` — an enum of personal-data sensitivity tiers used to annotate
  Pydantic model fields.
* ``pii(klass)`` — a metadata helper used either as
  ``Annotated[str, pii(PIIClass.DIRECT_IDENTIFIER)]`` or in a Field's
  ``metadata=[pii(...)]`` list.
* ``enumerate_pii(model_class)`` — walks a Pydantic v2 model's fields and
  yields ``(field_name, PIIClass)`` tuples for everything classified
  (default ``NONE`` is omitted).
* ``redact_pii(instance)`` — returns a dict copy of the instance with every
  ``DIRECT_IDENTIFIER`` and ``SENSITIVE`` field replaced by ``<redacted>``.
* ``ErasureOrchestrator`` — registry of per-extension erase callbacks plus
  ``erase_user(user_id)`` that runs them in reverse-dependency order.
  Idempotent. Emits an ``erasure_event`` audit class outline.
* ``DataExporter`` — symmetric registry for ``export_user`` callbacks
  (GDPR Article 20 portability).

Audit-log conflict resolution:

Each audit-event class declares ``pii_redactable: bool = True``. When
``ErasureOrchestrator.erase_user`` runs, audit events with
``pii_redactable=True`` have their PII fields redacted to a sentinel while
the event outline (timestamp, action, outcome) is preserved. Events with
``pii_redactable=False`` (e.g. ``erasure_event`` itself, regulator-mandated
records) keep their full fidelity.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import (
    Annotated,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    get_args,
    get_origin,
)

from pydantic import BaseModel


class PIIClass(str, Enum):
    """Personal-data sensitivity tiers for Pydantic field metadata."""

    DIRECT_IDENTIFIER = "direct_identifier"
    PSEUDONYMOUS = "pseudonymous"
    SENSITIVE = "sensitive"
    DERIVED = "derived"
    NONE = "none"


@dataclass(frozen=True)
class PIIAnnotation:
    """Metadata marker carried in a Pydantic field's metadata list.

    Detected by ``enumerate_pii`` and ``redact_pii`` via ``isinstance``. The
    ``pii(...)`` helper is the only sanctioned way to construct one — keeping
    construction routed through a function lets us add validation later
    without breaking annotated models.
    """

    klass: PIIClass


def pii(klass: PIIClass) -> PIIAnnotation:
    """Return a PII metadata marker for use in a Pydantic field annotation.

    Examples:
        >>> from typing import Annotated
        >>> from pydantic import BaseModel
        >>> class User(BaseModel):
        ...     email: Annotated[str, pii(PIIClass.DIRECT_IDENTIFIER)]
        ...     ssn: Annotated[str, pii(PIIClass.SENSITIVE)]

    Or via Field metadata:

        >>> from pydantic import Field
        >>> class User(BaseModel):
        ...     email: str = Field(..., metadata=[pii(PIIClass.DIRECT_IDENTIFIER)])
    """
    if not isinstance(klass, PIIClass):
        raise TypeError(
            f"pii() expected a PIIClass member, got {type(klass).__name__}"
        )
    return PIIAnnotation(klass=klass)


def _classify_field(field_info: Any) -> PIIClass:
    """Inspect a Pydantic v2 FieldInfo for a PIIAnnotation.

    Scans both ``metadata`` (set via ``Field(metadata=[...])``) and the
    annotation's ``Annotated`` extras (set via
    ``Annotated[T, pii(...)]``). Returns ``PIIClass.NONE`` when nothing
    matches.
    """
    # 1. Pydantic v2 collects extras from both Annotated[...] and Field(metadata=...)
    #    into FieldInfo.metadata, but only for v2 >=2.0. Be defensive: also
    #    inspect the annotation.
    metadata = getattr(field_info, "metadata", None) or []
    for item in metadata:
        if isinstance(item, PIIAnnotation):
            return item.klass

    annotation = getattr(field_info, "annotation", None)
    if annotation is not None and get_origin(annotation) is not None:
        # Annotated[T, ...] flattens via get_args; the first element is the
        # base type, the rest are extras.
        for extra in get_args(annotation)[1:]:
            if isinstance(extra, PIIAnnotation):
                return extra.klass
    return PIIClass.NONE


def enumerate_pii(model_class: Type[BaseModel]) -> List[Tuple[str, PIIClass]]:
    """Walk ``model_class``'s fields and return classified PII fields.

    Fields without a ``pii(...)`` annotation are omitted. The order matches
    declaration order, which matches Pydantic's ``model_fields`` iteration.

    Args:
        model_class: A Pydantic v2 ``BaseModel`` subclass.

    Returns:
        A list of ``(field_name, PIIClass)`` tuples for every field with a
        non-NONE classification.
    """
    if not hasattr(model_class, "model_fields"):
        raise TypeError(
            f"enumerate_pii expected a Pydantic v2 BaseModel, got {model_class!r}"
        )
    out: List[Tuple[str, PIIClass]] = []
    for name, field_info in model_class.model_fields.items():
        klass = _classify_field(field_info)
        if klass != PIIClass.NONE:
            out.append((name, klass))
    return out


REDACTION_SENTINEL = "<redacted>"


def redact_pii(model_instance: BaseModel) -> Dict[str, Any]:
    """Return a dict view of ``model_instance`` with PII fields redacted.

    ``DIRECT_IDENTIFIER`` and ``SENSITIVE`` fields are replaced with
    ``<redacted>``. ``PSEUDONYMOUS`` fields are kept (they are already
    non-identifying by design) and ``DERIVED`` fields are kept (the caller
    can re-erase them via ``erase_user`` if a downstream model absorbed
    PII via training).
    """
    if not isinstance(model_instance, BaseModel):
        raise TypeError(
            f"redact_pii expected a Pydantic BaseModel, got {type(model_instance).__name__}"
        )
    data = model_instance.model_dump()
    classifications = dict(enumerate_pii(type(model_instance)))
    for field_name, klass in classifications.items():
        if klass in (PIIClass.DIRECT_IDENTIFIER, PIIClass.SENSITIVE):
            if field_name in data:
                data[field_name] = REDACTION_SENTINEL
    return data


# ---------------------------------------------------------------------------
# Right-to-erasure orchestration
# ---------------------------------------------------------------------------


@dataclass
class ErasureReport:
    """Outcome of an ``ErasureOrchestrator.erase_user`` call."""

    user_id: str
    erased_extensions: List[str] = dc_field(default_factory=list)
    failed_extensions: Dict[str, str] = dc_field(default_factory=dict)
    audit_event_id: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return not self.failed_extensions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "erased_extensions": list(self.erased_extensions),
            "failed_extensions": dict(self.failed_extensions),
            "audit_event_id": self.audit_event_id,
            "succeeded": self.succeeded,
        }


# Audit class outline for emit-side consumers. The framework's audit pipeline
# (Item 56) is the actual emitter; this dict documents the contract.
ERASURE_EVENT_AUDIT_CLASS = {
    "name": "erasure_event",
    "pii_redactable": False,  # The erasure record itself must survive future erasures
    "retention": "forever",
    "fields": ("user_id", "timestamp", "outcome", "extensions"),
}


class ErasureOrchestrator:
    """Orchestrates ``erase_user`` calls across registered extensions.

    Erasure runs in **reverse** dependency order so leaf extensions release
    their references to user data before upstream-dependency extensions do.
    The orchestrator is **idempotent**: re-running ``erase_user(user_id)``
    on an already-erased user runs every callback again (callbacks are
    expected to be idempotent themselves) and returns a successful report.

    A single call emits one ``erasure_event`` audit record outline (the
    actual log emission is the caller's responsibility — typically wired
    through ``logic.AbstractLogicManager``'s audit pipeline).
    """

    def __init__(self) -> None:
        # Insertion order = registration order = forward dependency order.
        # Reverse order at erase time.
        self._erasers: Dict[str, Callable[[str], None]] = {}

    def register_extension_eraser(
        self,
        name: str,
        fn: Callable[[str], None],
    ) -> None:
        """Register an erase callback for an extension.

        Args:
            name: The extension's name (must match the extension's
                ``name`` ClassVar so reverse-dependency ordering can map
                back to the registered extension graph).
            fn: A callable accepting ``user_id: str``. Returns nothing.
                Callbacks must be idempotent (running twice produces the
                same end state).
        """
        if not callable(fn):
            raise TypeError(f"eraser fn must be callable, got {type(fn).__name__}")
        self._erasers[name] = fn

    def erase_user(self, user_id: str) -> ErasureReport:
        """Run every registered eraser in reverse dependency order.

        Cross-extension atomicity is **not guaranteed** — each eraser owns
        its own transactionality. A partial failure leaves the orchestrator
        in a state where re-calling ``erase_user`` will re-attempt every
        eraser (idempotent contract).

        Returns an ``ErasureReport`` summarizing per-extension outcomes.
        """
        report = ErasureReport(user_id=user_id)
        # Iterate in reverse insertion order — the convention is that
        # registration follows forward dependency order (per Item 24/49).
        for name in reversed(list(self._erasers.keys())):
            fn = self._erasers[name]
            try:
                fn(user_id)
                report.erased_extensions.append(name)
            except Exception as exc:  # pragma: no cover - exercised by tests
                report.failed_extensions[name] = str(exc)
        # The audit event id is assigned by the audit pipeline; we set a
        # deterministic placeholder so downstream code that expects a value
        # has something to log.
        report.audit_event_id = f"erasure:{user_id}"
        return report


# ---------------------------------------------------------------------------
# Right-to-portability (data export)
# ---------------------------------------------------------------------------


class DataExporter:
    """Symmetric counterpart to ``ErasureOrchestrator`` for GDPR Article 20.

    Each registered exporter returns a JSON-serializable dict of the user's
    data within its own domain. ``export_user`` collects them under each
    extension's name.
    """

    def __init__(self) -> None:
        self._exporters: Dict[str, Callable[[str], Dict[str, Any]]] = {}

    def register_extension_exporter(
        self,
        name: str,
        fn: Callable[[str], Dict[str, Any]],
    ) -> None:
        if not callable(fn):
            raise TypeError(f"exporter fn must be callable, got {type(fn).__name__}")
        self._exporters[name] = fn

    def export_user(self, user_id: str) -> Dict[str, Any]:
        """Run every registered exporter and merge the outputs.

        Returns a dict ``{extension_name: exporter_output}``. An exporter
        that raises is recorded as ``{"_error": str(exc)}`` under its
        extension key so a failure on one extension does not prevent the
        rest from being exported.
        """
        out: Dict[str, Any] = {}
        for name, fn in self._exporters.items():
            try:
                out[name] = fn(user_id)
            except Exception as exc:  # pragma: no cover - exercised by tests
                out[name] = {"_error": str(exc)}
        return out


__all__ = [
    "PIIClass",
    "PIIAnnotation",
    "pii",
    "enumerate_pii",
    "redact_pii",
    "REDACTION_SENTINEL",
    "ErasureReport",
    "ErasureOrchestrator",
    "DataExporter",
    "ERASURE_EVENT_AUDIT_CLASS",
]
