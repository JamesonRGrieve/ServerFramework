"""Per-request cost audit emitter (Item 84).

Bridges the rotation-system cost callback into the meta-logging audit
log so a tenant's spend across providers is reconstructible from the
audit trail. The audit log is the source of truth; the in-process
counter is just a fast read-side cache that the daily roll-up drains.

Integration contract:
    ``RotationManager.rotate`` is expected to call
    :func:`get_cost_audit_emitter` after a successful provider call
    (right where the in-process ``_cost_counter`` is updated, around
    BLL_Providers.py line 1717) and, if non-None, invoke it with the
    payload shape documented on :func:`make_cost_audit_emitter`.
    Wiring of that consumer call lives in BLL_Providers.py and is
    intentionally not done here so each module remains independently
    landable.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from zephyrex.lib.Logging import logger

if TYPE_CHECKING:
    from zephyrex.extensions.meta_logging.BLL_Meta_Logging import (
        AuditLogManager,
    )


CostAuditEmitter = Callable[[Dict[str, Any]], None]


_lock = threading.Lock()
_registered_emitter: Optional[CostAuditEmitter] = None


def register_cost_audit_emitter(emitter: Optional[CostAuditEmitter]) -> None:
    """Install (or clear, with ``None``) the process-wide cost-audit emitter."""
    global _registered_emitter
    with _lock:
        _registered_emitter = emitter


def get_cost_audit_emitter() -> Optional[CostAuditEmitter]:
    """Return the registered emitter, or ``None`` if no consumer is wired."""
    with _lock:
        return _registered_emitter


def _coerce_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def make_cost_audit_emitter(
    audit_log_manager_factory: Callable[[], "AuditLogManager"],
) -> CostAuditEmitter:
    """Return a callable that persists a cost-emission payload as an
    ``AuditLogModel`` row.

    Expected payload shape::

        {
            "tenant_id": str,
            "provider": str,
            "ability": str,
            "cost_usd": Decimal | str | float,
            "occurred_at": datetime | str | None,
            "traceparent": str | None,
        }

    The returned callable:
      1. Constructs an ``AuditLogModel.Create`` with
         ``action="provider_cost"`` and ``resource_type="provider"``.
      2. Persists via ``audit_log_manager_factory().log_audit_event(...)``
         (sync or async — the result is not awaited; failures are
         logged at WARNING level and never raised).
      3. Carries the full payload in ``additional_data`` so a regulator
         can reconstruct spend per tenant from the audit log alone.
    """
    from zephyrex.extensions.meta_logging.BLL_Meta_Logging import (
        AuditLogModel,
    )

    def emit(payload: Dict[str, Any]) -> None:
        try:
            tenant_id = str(payload.get("tenant_id", "unknown"))
            provider = str(payload.get("provider", "unknown"))
            ability = str(payload.get("ability", "unknown"))
            cost_raw = payload.get("cost_usd", 0)
            try:
                cost_usd = Decimal(str(cost_raw))
            except Exception:  # noqa: BLE001
                cost_usd = Decimal("0")
            occurred_at = _coerce_iso(payload.get("occurred_at"))
            if occurred_at is None:
                occurred_at = datetime.now(timezone.utc).isoformat()
            traceparent = payload.get("traceparent")

            additional_data: Dict[str, Any] = {
                "tenant_id": tenant_id,
                "provider": provider,
                "ability": ability,
                "cost_usd": str(cost_usd),
                "occurred_at": occurred_at,
            }
            if traceparent is not None:
                additional_data["traceparent"] = str(traceparent)

            create = AuditLogModel.Create(
                user_id=tenant_id,
                action="provider_cost",
                resource_type="provider",
                resource_id=provider,
                success=True,
                additional_data=additional_data,
            )

            manager = audit_log_manager_factory()
            log_method = getattr(manager, "log_audit_event", None)
            if log_method is None:
                logger.warning(
                    "cost_audit_emitter: audit manager has no log_audit_event method"
                )
                return
            try:
                result = log_method(create)
                # If the manager returns a coroutine, fire-and-forget via
                # the running loop; if no loop is running, schedule a new
                # one only when explicitly safe. Failures here are
                # absorbed identically to sync failures.
                import asyncio

                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.ensure_future(result)
                        else:
                            loop.run_until_complete(result)
                    except RuntimeError:
                        # No event loop available; synchronously execute
                        # the coroutine in a fresh loop and discard.
                        try:
                            asyncio.run(result)
                        except Exception as inner:  # noqa: BLE001
                            logger.warning(
                                "cost_audit_emitter: coroutine drain failed: %s",
                                inner,
                            )
            except Exception as inner:  # noqa: BLE001
                logger.warning(
                    "cost_audit_emitter: log_audit_event failed: %s", inner
                )
        except Exception as outer:  # noqa: BLE001
            logger.warning("cost_audit_emitter: emission failed: %s", outer)

    return emit


__all__ = [
    "CostAuditEmitter",
    "register_cost_audit_emitter",
    "get_cost_audit_emitter",
    "make_cost_audit_emitter",
]
