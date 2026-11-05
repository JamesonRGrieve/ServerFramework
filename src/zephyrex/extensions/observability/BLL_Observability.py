# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which metrics backend and error reporter this server wired, for root.

Credentials (the Sentry DSN, the Rollbar token) are reported only as set or
unset.
"""

from typing import ClassVar, List, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from zephyrex.lib.CustomRoute import custom_route
from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin

# What the host app serves a Prometheus scrape at.
PROMETHEUS_ENDPOINT = "/metrics"


class MetricsStatus(BaseModel):
    backend: Optional[Literal["prometheus", "otel"]] = None
    active: bool
    endpoint: Optional[str] = None


class ErrorReporterStatus(BaseModel):
    backend: Optional[Literal["sentry", "rollbar"]] = None
    active: bool
    # Whether the credential the backend needs is configured.
    dsn_set: bool


class ObservabilityStatus(BaseModel):
    metrics: MetricsStatus
    error_reporter: ErrorReporterStatus


def metrics_status() -> MetricsStatus:
    from zephyrex.extensions.observability.MetricsBackends import (
        OpenTelemetryMetricsBackend,
        PrometheusMetricsBackend,
    )
    from zephyrex.lib.Metrics import get_metrics_backend

    backend = get_metrics_backend()
    if isinstance(backend, PrometheusMetricsBackend):
        return MetricsStatus(
            backend="prometheus", active=True, endpoint=PROMETHEUS_ENDPOINT
        )
    if isinstance(backend, OpenTelemetryMetricsBackend):
        return MetricsStatus(backend="otel", active=True)
    return MetricsStatus(active=False)


def error_reporter_status() -> ErrorReporterStatus:
    from zephyrex.extensions.observability.ErrorReporters import (
        RollbarErrorReporter,
        SentryErrorReporter,
    )
    from zephyrex.lib.Logging import installed_error_reporter

    reporter = installed_error_reporter()
    if isinstance(reporter, SentryErrorReporter):
        return ErrorReporterStatus(
            backend="sentry", active=True, dsn_set=bool(env("SENTRY_DSN"))
        )
    if isinstance(reporter, RollbarErrorReporter):
        return ErrorReporterStatus(
            backend="rollbar", active=True, dsn_set=bool(env("ROLLBAR_TOKEN"))
        )
    # Nothing wired; a credential set anyway means the wiring did not take.
    return ErrorReporterStatus(
        active=False, dsn_set=bool(env("SENTRY_DSN") or env("ROLLBAR_TOKEN"))
    )


class ObservabilityManager(AbstractBLLManager, RouterMixin):
    """Custom routes only: observability keeps no table."""

    prefix: ClassVar[Optional[str]] = "/v1/observability"
    tags: ClassVar[Optional[List[str]]] = ["Observability"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List]] = []

    @custom_route(
        method="GET",
        path="/status",
        output_model=ObservabilityStatus,
        authentication_type="jwt",
        openapi_tags=("Observability",),
        summary="The wired metrics backend and error reporter (root only)",
    )
    def status_route(self) -> ObservabilityStatus:
        from zephyrex.database.StaticPermissions import is_root_id

        if not is_root_id(self.requester.id):
            raise HTTPException(status_code=403, detail="Root only")
        return ObservabilityStatus(
            metrics=metrics_status(), error_reporter=error_reporter_status()
        )
