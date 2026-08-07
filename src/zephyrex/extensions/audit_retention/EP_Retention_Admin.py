"""Admin surface for Item 56 — legal-hold release on RetentionService."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from zephyrex.extensions.audit_retention.BLL_RetentionService import RetentionService


class LegalHoldReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    registration_name: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


def _default_admin_check() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="admin authentication not configured",
    )


def create_retention_admin_router(
    *,
    retention_service: Optional[RetentionService] = None,
    retention_service_resolver: Optional[Callable[[], Optional[RetentionService]]] = None,
    admin_check: Optional[Callable[..., Any]] = None,
    requester_resolver: Optional[Callable[..., str]] = None,
) -> APIRouter:
    """Construct the retention-admin ``APIRouter``.

    Either pass a ``retention_service`` directly, or pass a
    ``retention_service_resolver`` that returns one (lazy lookup against the
    framework's :class:`ServiceRegistry`). ``admin_check`` is a FastAPI
    dependency that must raise on unauthenticated/unauthorized callers and
    return the requester id (or any object). ``requester_resolver`` extracts
    a stable string id from whatever ``admin_check`` returned; if absent,
    we fall back to ``str(...)``.
    """
    if retention_service is None and retention_service_resolver is None:
        raise ValueError(
            "create_retention_admin_router requires either retention_service "
            "or retention_service_resolver"
        )

    auth_dep = admin_check or _default_admin_check

    def _resolve_service() -> RetentionService:
        svc = retention_service
        if svc is None and retention_service_resolver is not None:
            svc = retention_service_resolver()
        if svc is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="retention service not registered",
            )
        return svc

    def _resolve_requester(principal: Any) -> str:
        if requester_resolver is not None:
            try:
                return str(requester_resolver(principal))
            except Exception:  # noqa: BLE001
                pass
        if principal is None:
            return "unknown"
        for attr in ("id", "user_id", "requester_id", "username"):
            value = getattr(principal, attr, None)
            if value:
                return str(value)
        if isinstance(principal, dict):
            for key in ("id", "user_id", "requester_id", "username"):
                if principal.get(key):
                    return str(principal[key])
        return str(principal)

    router = APIRouter(tags=["retention-admin"])

    @router.post("/admin/retention/legal-hold/release")
    def release_legal_hold(
        body: LegalHoldReleaseRequest,
        principal: Any = Depends(auth_dep),
    ) -> dict:
        svc = _resolve_service()
        requester_id = _resolve_requester(principal)
        event_id = svc.release_legal_hold(
            body.registration_name,
            reason=body.reason,
            requester_id=requester_id,
        )
        return {
            "status": "released",
            "registration_name": body.registration_name,
            "event_id": event_id,
        }

    @router.delete("/admin/retention/legal-hold/release/{registration_name}")
    def reengage_legal_hold(
        registration_name: str,
        principal: Any = Depends(auth_dep),
    ) -> dict:
        svc = _resolve_service()
        if not svc.is_released(registration_name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{registration_name} is not in the release set",
            )
        svc.clear_release(registration_name)
        return {"status": "reengaged", "registration_name": registration_name}

    @router.get("/admin/retention/legal-hold/release")
    def list_released(
        principal: Any = Depends(auth_dep),
    ) -> dict:
        svc = _resolve_service()
        released = svc.list_released()
        return {"released": released, "total": len(released)}

    return router


def resolve_retention_service_from_registry(
    service_id: str,
) -> Callable[[], Optional[RetentionService]]:
    """Return a resolver that pulls the ``retention_service`` attribute off
    the :class:`ScheduledService` registered under ``service_id``.

    Pairs with :func:`make_retention_scheduled_service` which exposes the
    inner service as ``scheduled.retention_service``.
    """

    def _resolve() -> Optional[RetentionService]:
        try:
            from zephyrex.logic.AbstractService import ServiceRegistry
        except ImportError:
            return None
        scheduled = ServiceRegistry.get(service_id)
        if scheduled is None:
            return None
        return getattr(scheduled, "retention_service", None)

    return _resolve


__all__ = [
    "LegalHoldReleaseRequest",
    "create_retention_admin_router",
    "resolve_retention_service_from_registry",
]
