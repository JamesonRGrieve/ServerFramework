"""Operational surface: probes, alerts, runbooks, DLQ admin UX (Item 81).

This module mounts the framework's operator-facing endpoints. The router is
constructed via :func:`create_operations_router` and intentionally has no
hard dependency on the BLL/extension surface — every cross-module reference
is resolved lazily so the router is importable in any environment, including
test fixtures and reduced-import deployments.

Endpoints
---------
- ``GET /healthz`` — liveness. Always 200 unless an explicit shutdown signal
  has been recorded.
- ``GET /readyz`` — readiness. 200 only when the DB is reachable, the
  credential store is reachable, and every Critical-tier provider's
  ``health_check`` reports OK. Critical-tier providers must be DOWN for
  more than ``READYZ_HYSTERESIS_SECONDS`` (default 60s) before the endpoint
  flips to 503; this prevents flapping providers from cycling the
  deployment in/out of the load balancer.
- ``GET /admin/dlq`` — paginated DLQ listing with filtering by extension,
  ability, error class, and timestamp window.
- ``POST /admin/dlq/{id}/replay`` — re-enqueue the DLQ entry into the outbox.
- ``POST /admin/dlq/{id}/discard`` — mark the entry discarded.
- ``GET /admin/services`` — list services in the supervisor's ``failed`` state.
- ``POST /admin/services/{name}/reset`` — clear a service's ``failed`` state.

Alerts and runbooks
-------------------
:class:`Alert` declarations are registered into ``ALERT_REGISTRY`` at import
time (typically alongside the metric emission) and rendered into a Prometheus
AlertManager rules file via :func:`generate_alertmanager_yaml`.
:func:`add_runbook_url_to_error` is the writer side of the runbook URL: it
attaches a documentation link to a typed framework error so the structured
log emitter (Item 85) can include it in the operator's view.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from lib.Logging import logger


# ---------------------------------------------------------------------------
# Process-level shutdown signal
# ---------------------------------------------------------------------------


_SHUTDOWN_FLAG: Dict[str, bool] = {"shutdown": False}


def signal_shutdown() -> None:
    """Mark the process as shutting down. ``/healthz`` will then return 503."""
    _SHUTDOWN_FLAG["shutdown"] = True


def clear_shutdown() -> None:
    """Clear the shutdown signal (test helper)."""
    _SHUTDOWN_FLAG["shutdown"] = False


def is_shutting_down() -> bool:
    return _SHUTDOWN_FLAG["shutdown"]


# ---------------------------------------------------------------------------
# Critical-tier provider hysteresis tracker
# ---------------------------------------------------------------------------


# Per-provider first-seen-DOWN timestamp. Cleared when the provider returns
# OK. The hysteresis window is read from ``READYZ_HYSTERESIS_SECONDS`` (env)
# at every check so tests can adjust it without re-creating the router.
_CRITICAL_PROVIDER_DOWN_SINCE: Dict[str, float] = {}


def _hysteresis_seconds() -> float:
    raw = os.environ.get("READYZ_HYSTERESIS_SECONDS")
    if raw is None:
        return 60.0
    try:
        return float(raw)
    except ValueError:
        return 60.0


def _record_critical_health(provider_id: str, ok: bool) -> bool:
    """Track a Critical-tier provider's health and return True if it should
    fail readiness (DOWN for > the hysteresis window)."""
    now = time.monotonic()
    if ok:
        _CRITICAL_PROVIDER_DOWN_SINCE.pop(provider_id, None)
        return False
    first = _CRITICAL_PROVIDER_DOWN_SINCE.get(provider_id)
    if first is None:
        _CRITICAL_PROVIDER_DOWN_SINCE[provider_id] = now
        return False
    return (now - first) > _hysteresis_seconds()


def reset_hysteresis_state() -> None:
    """Clear the per-provider DOWN tracker (test helper)."""
    _CRITICAL_PROVIDER_DOWN_SINCE.clear()


# ---------------------------------------------------------------------------
# Pluggable health checkers
# ---------------------------------------------------------------------------


HealthChecker = Callable[[], bool]
CriticalProvidersFn = Callable[[], List[Any]]


@dataclass
class _OperationsConfig:
    """Mutable config injected into the router at construction.

    Tests pass overrides; production wires actual DB / credential / provider
    discovery functions.
    """

    db_check: Optional[HealthChecker] = None
    credential_check: Optional[HealthChecker] = None
    critical_providers: Optional[CriticalProvidersFn] = None
    dlq_lister: Optional[Callable[..., List[Any]]] = None
    dlq_replay: Optional[Callable[[str], bool]] = None
    dlq_discard: Optional[Callable[[str], bool]] = None
    failed_services_lister: Optional[Callable[[], List[Any]]] = None
    service_reset: Optional[Callable[[str], bool]] = None


# ---------------------------------------------------------------------------
# Alert taxonomy
# ---------------------------------------------------------------------------


@dataclass
class Alert:
    """Declarative alert tying a metric to a runbook entry."""

    metric: str
    threshold: float
    window: str
    runbook: str
    severity: str = "warning"
    description: str = ""

    def to_alertmanager_dict(self) -> Dict[str, Any]:
        """Render to Prometheus AlertManager rule shape."""
        expr = f"{self.metric} > {self.threshold}"
        annotations = {
            "runbook_url": self.runbook,
        }
        if self.description:
            annotations["description"] = self.description
        return {
            "alert": self.metric,
            "expr": expr,
            "for": self.window,
            "labels": {"severity": self.severity},
            "annotations": annotations,
        }


# Module-level registry of alerts. Populated at import time by emitting
# modules (Item 34's tracing layer is the canonical caller).
ALERT_REGISTRY: List[Alert] = []


def register_alert(alert: Alert) -> None:
    """Append ``alert`` to :data:`ALERT_REGISTRY`."""
    ALERT_REGISTRY.append(alert)


def clear_alerts() -> None:
    """Clear the alert registry (test helper)."""
    ALERT_REGISTRY.clear()


def generate_alertmanager_yaml() -> str:
    """Emit a Prometheus AlertManager rules YAML for every registered alert.

    YAML is rendered by hand (no extra dependency on PyYAML) since the
    output shape is small and stable. The result is the body of an
    ``alerts.yaml`` file callers drop into the AlertManager config.
    """
    if not ALERT_REGISTRY:
        return "groups:\n  - name: serverframework\n    rules: []\n"

    lines = ["groups:", "  - name: serverframework", "    rules:"]
    for a in ALERT_REGISTRY:
        d = a.to_alertmanager_dict()
        lines.append(f"      - alert: {d['alert']}")
        lines.append(f"        expr: {d['expr']}")
        lines.append(f"        for: {d['for']}")
        lines.append("        labels:")
        for lk, lv in d["labels"].items():
            lines.append(f"          {lk}: {lv}")
        lines.append("        annotations:")
        for ak, av in d["annotations"].items():
            # Quote runbook URLs and free-text descriptions.
            lines.append(f"          {ak}: {_yaml_quote(av)}")
    lines.append("")
    return "\n".join(lines)


def _yaml_quote(value: str) -> str:
    """Minimal YAML scalar quoting: wrap in double-quotes, escape ``"``."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# Runbook URL writer for typed framework errors
# ---------------------------------------------------------------------------


def add_runbook_url_to_error(exc: BaseException, url: str) -> None:
    """Set ``runbook_url`` on a typed framework error.

    The error hierarchy from :mod:`extensions.ExternalErrors` carries
    ``runbook_url`` as a class-level default; this is the writer side that
    operators / framework code use when emitting an instance with a more
    specific runbook than the class default. The URL is set on the
    instance, leaving the class-level default untouched.
    """
    setattr(exc, "runbook_url", url)


# ---------------------------------------------------------------------------
# DLQ filtering helpers
# ---------------------------------------------------------------------------


class DLQFilter(BaseModel):
    """Filter envelope used to query DLQ entries."""

    model_config = ConfigDict(extra="ignore")

    extension: Optional[str] = None
    ability: Optional[str] = None
    error_class: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None


def _apply_dlq_filter(entries: List[Any], flt: DLQFilter) -> List[Any]:
    out: List[Any] = []
    for e in entries:
        # Tolerant attribute / dict access so the filter works against the
        # framework's :class:`logic.Outbox.DLQEntry` (a Pydantic model) and
        # against simpler test fixtures (dicts / namespaces).
        provider = _get_attr(e, "target_provider")
        ability = _get_attr(e, "target_ability")
        error_str = str(_get_attr(e, "final_error", ""))
        moved_at = _get_attr(e, "moved_at")
        if flt.extension and provider and flt.extension.lower() not in str(
            provider
        ).lower():
            continue
        if flt.ability and ability and flt.ability.lower() not in str(
            ability
        ).lower():
            continue
        if flt.error_class and flt.error_class not in error_str:
            continue
        if flt.since and moved_at and _as_dt(moved_at) < flt.since:
            continue
        if flt.until and moved_at and _as_dt(moved_at) > flt.until:
            continue
        out.append(e)
    return out


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def _serialize_dlq_entry(entry: Any) -> Dict[str, Any]:
    """Render a DLQ entry into a JSON-safe dict regardless of source shape."""
    if hasattr(entry, "model_dump"):
        return entry.model_dump(mode="json")
    if isinstance(entry, dict):
        return dict(entry)
    return {
        k: getattr(entry, k)
        for k in dir(entry)
        if not k.startswith("_") and not callable(getattr(entry, k))
    }


# ---------------------------------------------------------------------------
# Lazy default wiring
# ---------------------------------------------------------------------------


def _default_dlq_lister(**_kwargs: Any) -> List[Any]:
    """Best-effort lazy lookup for the framework's DLQ list.

    Without an injected lister we return an empty list. The keyword-only
    signature absorbs ``limit`` (and any future filter args) so the
    endpoint can call this default the same way it calls a wired lister.
    """
    try:
        from logic.Outbox import DLQEntry  # noqa: F401 - imported for type only
    except ImportError:
        return []
    return []


def _default_failed_services() -> List[Any]:
    """Lazy lookup for ``failed``-state services from the registry."""
    try:
        from logic.AbstractService import ServiceRegistry
    except ImportError:
        return []
    failed: List[Any] = []
    for sid in ServiceRegistry.list():
        svc = ServiceRegistry.get(sid)
        if svc is None:
            continue
        if getattr(svc, "failed", False):
            failed.append(svc)
    return failed


def _default_service_reset(name: str) -> bool:
    try:
        from logic.AbstractService import ServiceRegistry
    except ImportError:
        return False
    svc = ServiceRegistry.get(name)
    if svc is None:
        return False
    if not getattr(svc, "failed", False):
        return False
    reset = getattr(svc, "reset_failed", None)
    if not callable(reset):
        return False
    reset()
    return True


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_operations_router(
    *,
    db_check: Optional[HealthChecker] = None,
    credential_check: Optional[HealthChecker] = None,
    critical_providers: Optional[CriticalProvidersFn] = None,
    dlq_lister: Optional[Callable[..., List[Any]]] = None,
    dlq_replay: Optional[Callable[[str], bool]] = None,
    dlq_discard: Optional[Callable[[str], bool]] = None,
    failed_services_lister: Optional[Callable[[], List[Any]]] = None,
    service_reset: Optional[Callable[[str], bool]] = None,
) -> APIRouter:
    """Construct the operations ``APIRouter``.

    Every external dependency is injected so the router is testable in
    isolation. Production wiring is expected to pass the framework's
    ``DatabaseManager.is_reachable``, ``Credentials.health_check``, the
    Critical-tier provider iterator, and the framework's DLQ store; tests
    pass simple closures.
    """

    cfg = _OperationsConfig(
        db_check=db_check,
        credential_check=credential_check,
        critical_providers=critical_providers,
        dlq_lister=dlq_lister or _default_dlq_lister,
        dlq_replay=dlq_replay,
        dlq_discard=dlq_discard,
        failed_services_lister=failed_services_lister or _default_failed_services,
        service_reset=service_reset or _default_service_reset,
    )

    router = APIRouter(tags=["operations"])

    # ----- Probes ---------------------------------------------------------

    @router.get("/healthz")
    def healthz() -> Dict[str, Any]:
        if is_shutting_down():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="shutting down",
            )
        return {"status": "ok"}

    @router.get("/readyz")
    def readyz() -> Dict[str, Any]:
        problems: List[str] = []

        # DB reachability.
        if cfg.db_check is not None:
            try:
                if not cfg.db_check():
                    problems.append("db_unreachable")
            except Exception as e:  # noqa: BLE001
                problems.append(f"db_unreachable:{type(e).__name__}")

        # Credential store reachability.
        if cfg.credential_check is not None:
            try:
                if not cfg.credential_check():
                    problems.append("credentials_unreachable")
            except Exception as e:  # noqa: BLE001
                problems.append(f"credentials_unreachable:{type(e).__name__}")

        # Critical-tier providers, with hysteresis.
        if cfg.critical_providers is not None:
            try:
                providers = cfg.critical_providers()
            except Exception as e:  # noqa: BLE001
                providers = []
                problems.append(f"critical_providers_lookup_failed:{type(e).__name__}")
            for prv in providers:
                pid = (
                    getattr(prv, "__name__", None)
                    or getattr(prv, "name", None)
                    or repr(prv)
                )
                try:
                    report = prv.health_check()
                except Exception as e:  # noqa: BLE001
                    if _record_critical_health(pid, ok=False):
                        problems.append(f"provider_down:{pid}")
                    continue
                ok = _is_health_ok(report)
                if _record_critical_health(pid, ok=ok):
                    problems.append(f"provider_down:{pid}")

        if problems:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "not_ready", "problems": problems},
            )
        return {"status": "ready"}

    # ----- DLQ admin ------------------------------------------------------

    @router.get("/admin/dlq")
    def list_dlq(
        extension: Optional[str] = Query(None),
        ability: Optional[str] = Query(None),
        error_class: Optional[str] = Query(None),
        since: Optional[datetime] = Query(None),
        until: Optional[datetime] = Query(None),
        limit: int = Query(100, ge=1, le=1000),
    ) -> Dict[str, Any]:
        if cfg.dlq_lister is None:
            entries: List[Any] = []
        else:
            entries = cfg.dlq_lister(limit=limit)
        flt = DLQFilter(
            extension=extension,
            ability=ability,
            error_class=error_class,
            since=since,
            until=until,
        )
        filtered = _apply_dlq_filter(entries, flt)
        return {
            "entries": [_serialize_dlq_entry(e) for e in filtered[:limit]],
            "total": len(filtered),
        }

    @router.post("/admin/dlq/{entry_id}/replay")
    def replay_dlq(entry_id: str) -> Dict[str, Any]:
        if cfg.dlq_replay is None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="dlq replay not wired",
            )
        ok = bool(cfg.dlq_replay(entry_id))
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"dlq entry {entry_id} not found",
            )
        logger.info(f"DLQ replay: id={entry_id}")
        return {"status": "replayed", "id": entry_id}

    @router.post("/admin/dlq/{entry_id}/discard")
    def discard_dlq(entry_id: str) -> Dict[str, Any]:
        if cfg.dlq_discard is None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="dlq discard not wired",
            )
        ok = bool(cfg.dlq_discard(entry_id))
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"dlq entry {entry_id} not found",
            )
        logger.info(f"DLQ discard: id={entry_id}")
        return {"status": "discarded", "id": entry_id}

    # ----- Service admin --------------------------------------------------

    @router.get("/admin/services")
    def list_failed_services() -> Dict[str, Any]:
        services = cfg.failed_services_lister() if cfg.failed_services_lister else []
        rendered = []
        for svc in services:
            rendered.append(
                {
                    "name": _service_identity(svc),
                    "class": type(svc).__name__,
                    "failed": True,
                    "failed_reason": getattr(svc, "_failed_reason", None),
                }
            )
        return {"services": rendered, "total": len(rendered)}

    @router.post("/admin/services/{name}/reset")
    def reset_service(name: str) -> Dict[str, Any]:
        if cfg.service_reset is None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="service reset not wired",
            )
        ok = bool(cfg.service_reset(name))
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"service {name} is not in the failed state or not found",
            )
        logger.info(f"Service reset: name={name}")
        return {"status": "reset", "name": name}

    return router


def _is_health_ok(report: Any) -> bool:
    """Tolerant interpretation of a health report.

    The framework has two "OK" shapes:
    - :class:`extensions.AbstractExtensionProvider.HealthReport` whose
      ``.status`` is a :class:`HealthStatus` enum.
    - :class:`logic.AbstractService.HealthStatus` whose ``.status`` is a
      string.
    Both treat the literal value ``"ok"`` (any case) as healthy.
    """
    status_obj = getattr(report, "status", report)
    raw = getattr(status_obj, "value", status_obj)
    return str(raw).lower() == "ok"


def _service_identity(svc: Any) -> str:
    return (
        getattr(svc, "service_id", None)
        or getattr(svc, "name", None)
        or type(svc).__name__
    )


__all__ = [
    "create_operations_router",
    "Alert",
    "ALERT_REGISTRY",
    "register_alert",
    "clear_alerts",
    "generate_alertmanager_yaml",
    "add_runbook_url_to_error",
    "signal_shutdown",
    "clear_shutdown",
    "is_shutting_down",
    "reset_hysteresis_state",
    "DLQFilter",
]
