"""Outbox tracking-id lookup endpoint (Item 48).

Clients that receive a ``202 Accepted`` response with a ``tracking_id``
poll this endpoint to learn the eventual outcome of their queued
operation. The endpoint is read-only and intentionally narrow: it
returns the entry status (``pending``, ``in_flight``, ``complete``,
``dlq``) plus the last error or completion side effect.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic2FastAPI import AuthType, get_auth_dependency


def _resolve_default_store() -> Optional[Any]:
    """Return the default outbox store the route should query.

    Looks first at ``RotationManager._outbox_store`` so test fixtures
    that inject an in-memory store via :py:meth:`set_outbox_store` are
    transparently picked up. Returns ``None`` when the framework has
    never been wired with a store.
    """
    try:
        from zephyrex.logic.BLL_Providers import RotationManager

        store = getattr(RotationManager, "_outbox_store", None)
        if store is not None:
            return store
    except Exception:
        pass
    return None


def create_outbox_router(
    *,
    store_resolver: Optional[Any] = None,
    require_auth: bool = True,
) -> APIRouter:
    """Construct the outbox tracking-id ``APIRouter``.

    Args:
        store_resolver: A zero-arg callable returning the
            :class:`OutboxStore` to query. Defaults to looking up
            ``RotationManager._outbox_store``.
        require_auth: When ``True`` the route depends on the framework
            JWT/API-key auth dependency. Tests pass ``False`` to bypass.
    """

    resolver = store_resolver or _resolve_default_store

    dependencies = []
    if require_auth:
        auth_dep = get_auth_dependency(AuthType.JWT)
        if auth_dep is not None:
            dependencies.append(auth_dep)

    router = APIRouter(tags=["outbox"])

    @router.get("/v1/outbox/{tracking_id}", dependencies=dependencies)
    def get_outbox_entry(tracking_id: str) -> Dict[str, Any]:
        store = resolver() if callable(resolver) else resolver
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="outbox store not configured",
            )

        entry = None
        getter = getattr(store, "get", None)
        if callable(getter):
            try:
                entry = getter(tracking_id)
            except Exception as exc:
                logger.debug(f"outbox store.get raised: {exc}")
                entry = None

        if entry is None:
            entries = getattr(store, "_entries", None)
            if isinstance(entries, dict):
                entry = entries.get(tracking_id)

        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"tracking_id {tracking_id} not found",
            )

        entry_status = getattr(entry, "status", None)
        last_error = getattr(entry, "last_error", None)
        retry_count = getattr(entry, "retry_count", None)
        payload = getattr(entry, "payload", None)

        body: Dict[str, Any] = {
            "tracking_id": tracking_id,
            "status": entry_status,
        }
        if retry_count is not None:
            body["retry_count"] = retry_count
        if entry_status == "complete":
            body["result"] = payload
        if entry_status in {"dlq", "failed"} and last_error:
            body["error"] = last_error
        elif last_error and entry_status != "complete":
            body["last_error"] = last_error

        return body

    return router


__all__ = ["create_outbox_router"]
