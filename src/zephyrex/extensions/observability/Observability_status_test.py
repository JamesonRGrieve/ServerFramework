# SPDX-License-Identifier: AGPL-3.0-or-later
"""GET /v1/observability/status: root sees which metrics backend and error
reporter this server wired, with credentials reported only as set or unset.

Observability keeps no table, so these also prove a model-less manager's
custom routes mount, on REST and on GraphQL.
"""

import os
from typing import Any, Dict

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.observability.BLL_Observability import (
    ObservabilityManager,
)
from zephyrex.extensions.observability.ErrorReporters import (
    RollbarErrorReporter,
    SentryErrorReporter,
)
from zephyrex.extensions.observability.EXT_Observability import EXT_Observability
from zephyrex.extensions.observability.MetricsBackends import (
    OpenTelemetryMetricsBackend,
    PrometheusMetricsBackend,
)
from zephyrex.lib.CustomRoute import graphql_field_name
from zephyrex.lib.Logging import (
    NoopErrorReporter,
    installed_error_reporter,
    set_error_reporter,
)
from zephyrex.lib.Metrics import (
    NoopMetricsBackend,
    get_metrics_backend,
    set_metrics_backend,
)

STATUS = "/v1/observability/status"
_NOTHING_WIRED = {
    "metrics": {"backend": None, "active": False, "endpoint": None},
    "error_reporter": {"backend": None, "active": False, "dsn_set": False},
}


def _root() -> Dict[str, str]:
    return {"X-API-Key": os.environ["ROOT_API_KEY"]}


@pytest.fixture(autouse=True)
def _nothing_wired(monkeypatch):
    """Each test starts with no backend, no reporter and no credentials, and
    the process-global backend and reporter are restored after it."""
    saved_backend = get_metrics_backend()
    saved_reporter = installed_error_reporter()
    set_metrics_backend(NoopMetricsBackend())
    set_error_reporter(NoopErrorReporter())
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("ROLLBAR_TOKEN", raising=False)
    try:
        yield
    finally:
        set_metrics_backend(saved_backend)
        set_error_reporter(saved_reporter)


class TestObservabilityStatus(ExtensionServerMixin):
    extension_class = EXT_Observability

    def _status(self, server: Any) -> Dict[str, Any]:
        response = server.get(STATUS, headers=_root())
        assert response.status_code == 200, response.text
        body: Dict[str, Any] = response.json()
        return body

    def test_only_root_may_read_it(self, server, admin_a):
        assert server.get(STATUS).status_code == 401
        signed_in = server.get(
            STATUS, headers={"Authorization": f"Bearer {admin_a.jwt}"}
        )
        assert signed_in.status_code == 403, signed_in.text

    def test_nothing_wired(self, server):
        assert self._status(server) == _NOTHING_WIRED

    def test_prometheus_reports_its_scrape_endpoint(self, server):
        # prometheus_client is optional; the status reads only the type.
        set_metrics_backend(PrometheusMetricsBackend.__new__(PrometheusMetricsBackend))
        assert self._status(server)["metrics"] == {
            "backend": "prometheus",
            "active": True,
            "endpoint": "/metrics",
        }

    def test_opentelemetry(self, server):
        set_metrics_backend(OpenTelemetryMetricsBackend())
        assert self._status(server)["metrics"] == {
            "backend": "otel",
            "active": True,
            "endpoint": None,
        }

    def test_the_sentry_dsn_is_reported_only_as_set(self, server, monkeypatch):
        dsn = "https://zx-probe-key@example.invalid/1"
        monkeypatch.setenv("SENTRY_DSN", dsn)
        set_error_reporter(SentryErrorReporter())

        response = server.get(STATUS, headers=_root())
        assert "zx-probe-key" not in response.text
        assert response.json()["error_reporter"] == {
            "backend": "sentry",
            "active": True,
            "dsn_set": True,
        }

    def test_rollbar(self, server, monkeypatch):
        monkeypatch.setenv("ROLLBAR_TOKEN", "zx-probe-token")
        set_error_reporter(RollbarErrorReporter())

        response = server.get(STATUS, headers=_root())
        assert "zx-probe-token" not in response.text
        assert response.json()["error_reporter"] == {
            "backend": "rollbar",
            "active": True,
            "dsn_set": True,
        }

    def test_a_credential_that_wired_nothing_shows(self, server, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://k@example.invalid/1")
        assert self._status(server)["error_reporter"] == {
            "backend": None,
            "active": False,
            "dsn_set": True,
        }

    def test_graphql_serves_the_same_status(self, server):
        from strawberry.utils.str_converters import to_camel_case

        field = to_camel_case(graphql_field_name(ObservabilityManager, "status_route"))
        query = (
            f"{{ {field} {{ metrics {{ backend active endpoint }} "
            "errorReporter { backend active dsnSet } } }"
        )
        response = server.post("/graphql", json={"query": query}, headers=_root())
        assert response.status_code == 200, response.text
        assert response.json() == {
            "data": {
                field: {
                    "metrics": {"backend": None, "active": False, "endpoint": None},
                    "errorReporter": {
                        "backend": None,
                        "active": False,
                        "dsnSet": False,
                    },
                }
            }
        }
