"""
Mirror-on-create / -update / -delete lifecycle decorators.

Item 14 — Mirror-on-create lifecycle primitive.

A `@mirror_on_create(local=UserModel, external=Stripe_CustomerModel,
link_field="external_payment_id")` decorator on a manager class registers
a BEFORE-COMMIT hook on the local manager that creates the external
entity, captures its ID, and writes it back into the link field.

Two policies are offered: `MirrorPolicy.OUTBOX` (the recommended
default; depends on Item 35's outbox infrastructure) and
`MirrorPolicy.SAGA` (roll-forward saga; used when the outbox is not
available). When a manager class is decorated and the outbox handle
is not yet available at registration time, the decorator falls back
to SAGA and emits a warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, ClassVar, List, Optional, Type

from serverframework.lib.Logging import logger


class MirrorPolicy(str, Enum):
    """Coordination strategy between the local commit and the external mirror."""

    OUTBOX = "outbox"
    """Write the local row and an outbox entry in the same transaction.
    A background worker performs the external create and writes back
    the link field. Recommended default; requires Item 35."""

    SAGA = "saga"
    """Local create runs first, then external create runs after the
    local commit, then the link field is written in a separate commit.
    Failure of the external call schedules a compensating delete via a
    background service."""


class MirrorEvent(str, Enum):
    """The local lifecycle event a mirror spec attaches to."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class MirrorSpec:
    """One mirror declaration on a manager class.

    Stored on the manager under `_mirror_specs: ClassVar[List[MirrorSpec]]`
    so that the framework can introspect declarations at startup time.
    """

    event: MirrorEvent
    local: Type
    external: Type
    link_field: str
    policy: MirrorPolicy = MirrorPolicy.OUTBOX
    idempotency_key_fn: Optional[Callable[..., str]] = None
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Decorator implementation
# ---------------------------------------------------------------------------


def _attach_spec(manager_cls: type, spec: MirrorSpec) -> None:
    """Attach the spec to the manager class, creating the list if missing.

    `_mirror_specs` is declared per concrete subclass so subclasses do
    not inherit the parent's list by reference.
    """

    if "_mirror_specs" not in manager_cls.__dict__:
        # Copy the parent's list so we do not mutate it.
        parent_specs = list(getattr(manager_cls, "_mirror_specs", []) or [])
        manager_cls._mirror_specs = parent_specs
    manager_cls._mirror_specs.append(spec)


def _register_hook(manager_cls: type, spec: MirrorSpec) -> None:
    """Register a BEFORE-COMMIT hook on the manager for this spec.

    Imports `hook_bll` lazily to avoid a circular dependency with
    `logic.AbstractLogicManager`. When `manager_cls` is not a real
    `AbstractBLLManager` (for example during unit tests that exercise
    only the spec accumulation), hook registration is skipped silently
    after the spec has been attached.
    """

    try:
        from serverframework.logic.AbstractLogicManager import (
            AbstractBLLManager,
            HookTiming,
            hook_bll,
        )
    except Exception as exc:  # pragma: no cover - import-time integration
        logger.warning(
            "Mirror hook registration deferred for %s: cannot import hook_bll (%s).",
            manager_cls.__name__,
            exc,
        )
        return

    if not issubclass(manager_cls, AbstractBLLManager):
        # The decorator has been applied to a non-BLL class (test fixture,
        # documentation example, etc.). The spec is still attached; only
        # hook registration is skipped.
        logger.debug(
            "Skipping mirror hook registration on non-BLL class %s; "
            "spec recorded on the class.",
            manager_cls.__name__,
        )
        return

    method_name = {
        MirrorEvent.CREATE: "create",
        MirrorEvent.UPDATE: "update",
        MirrorEvent.DELETE: "delete",
    }[spec.event]

    # The actual outbox enrolment / saga execution is owned by Batch C
    # and is wired into the framework's hook executor; we register a
    # placeholder hook that records the spec on the active context for
    # the executor to pick up.
    @hook_bll(manager_cls, timing=HookTiming.BEFORE, priority=20)
    def _mirror_hook(context):  # type: ignore[no-redef]
        if context.method_name != method_name:
            return
        bag = getattr(context, "condition_data", None)
        if bag is None:
            return
        bag.setdefault("mirror_specs", []).append(spec)
        # Detect outbox availability lazily: if the manager has an
        # `outbox_table_handle` attribute (provided by Batch C), use
        # OUTBOX semantics; otherwise warn and fall back to SAGA.
        if (
            spec.policy == MirrorPolicy.OUTBOX
            and not getattr(context.manager, "outbox_table_handle", None)
        ):
            logger.warning(
                "Mirror spec on %s requested OUTBOX but no outbox_table_handle "
                "is bound; falling back to SAGA.",
                manager_cls.__name__,
            )
            # Effective policy on this run is SAGA; record on the bag so
            # the executor can branch.
            bag["effective_mirror_policy"] = MirrorPolicy.SAGA
        else:
            bag["effective_mirror_policy"] = spec.policy


def _make_decorator(event: MirrorEvent) -> Callable[..., Callable[[type], type]]:
    def decorator_factory(
        local: Type,
        external: Type,
        link_field: str,
        idempotency_key_fn: Optional[Callable[..., str]] = None,
        policy: MirrorPolicy = MirrorPolicy.OUTBOX,
        **extra: Any,
    ) -> Callable[[type], type]:
        spec = MirrorSpec(
            event=event,
            local=local,
            external=external,
            link_field=link_field,
            policy=policy,
            idempotency_key_fn=idempotency_key_fn,
            extra=extra,
        )

        def wrap(manager_cls: type) -> type:
            _attach_spec(manager_cls, spec)
            _register_hook(manager_cls, spec)
            return manager_cls

        return wrap

    decorator_factory.__name__ = f"mirror_on_{event.value}"
    return decorator_factory


mirror_on_create = _make_decorator(MirrorEvent.CREATE)
mirror_on_update = _make_decorator(MirrorEvent.UPDATE)
mirror_on_delete = _make_decorator(MirrorEvent.DELETE)


def get_mirror_specs(manager_cls: type) -> List[MirrorSpec]:
    """Return the (possibly inherited) list of mirror specs on a manager."""

    return list(getattr(manager_cls, "_mirror_specs", []) or [])


__all__ = [
    "MirrorPolicy",
    "MirrorEvent",
    "MirrorSpec",
    "mirror_on_create",
    "mirror_on_update",
    "mirror_on_delete",
    "get_mirror_specs",
]
