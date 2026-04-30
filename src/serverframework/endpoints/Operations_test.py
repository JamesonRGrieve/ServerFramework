"""Tests for ``endpoints.Operations`` (Item 81).

Exercises the operator-facing router via a real :class:`fastapi.FastAPI`
app + :class:`starlette.testclient.TestClient`. No mocks for the BLL or
extension surface; the router's external dependencies are passed in via
explicit closures because the router is constructed by an injection-friendly
factory.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from serverframework.endpoints.Operations import (
    ALERT_REGISTRY,
    Alert,
    DLQFilter,
    _CRITICAL_PROVIDER_DOWN_SINCE,
    _apply_dlq_filter,
    _is_health_ok,
    add_runbook_url_to_error,
    clear_alerts,
    clear_shutdown,
    create_operations_router,
    generate_alertmanager_yaml,
    register_alert,
    reset_hysteresis_state,
    signal_shutdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Tiny stand-in for a Critical-tier provider class."""

    def __init__(self, name: str, ok: bool = True) -> None:
        self.__name__ = name
        self.ok = ok

    def health_check(self):  # noqa: D401 - tiny stub
        return _FakeReport("ok" if self.ok else "down")


class _FakeReport:
    def __init__(self, status: str) -> None:
        self.status = status


def _build_client(**kwargs: Any) -> TestClient:
    app = FastAPI()
    router = create_operations_router(**kwargs)
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_module_state():
    saved_env = os.environ.get("READYZ_HYSTERESIS_SECONDS")
    clear_shutdown()
    reset_hysteresis_state()
    clear_alerts()
    yield
    clear_shutdown()
    reset_hysteresis_state()
    clear_alerts()
    if saved_env is None:
        os.environ.pop("READYZ_HYSTERESIS_SECONDS", None)
    else:
        os.environ["READYZ_HYSTERESIS_SECONDS"] = saved_env


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_healthz_returns_200_when_up():
    client = _build_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.unit
def test_healthz_returns_503_after_shutdown_signal():
    client = _build_client()
    signal_shutdown()
    resp = client.get("/healthz")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /readyz
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_readyz_200_when_no_checks_wired():
    client = _build_client()
    resp = client.get("/readyz")
    assert resp.status_code == 200


@pytest.mark.unit
def test_readyz_503_when_db_check_fails():
    client = _build_client(db_check=lambda: False)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert "db_unreachable" in resp.json()["detail"]["problems"]


@pytest.mark.unit
def test_readyz_503_when_credential_check_fails():
    client = _build_client(credential_check=lambda: False)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert "credentials_unreachable" in resp.json()["detail"]["problems"]


@pytest.mark.unit
def test_readyz_critical_provider_does_not_immediately_fail():
    """Hysteresis: the first DOWN observation does NOT flip to 503."""
    os.environ["READYZ_HYSTERESIS_SECONDS"] = "60"
    prv = _FakeProvider("critical-x", ok=False)
    client = _build_client(critical_providers=lambda: [prv])
    resp = client.get("/readyz")
    assert resp.status_code == 200  # within hysteresis window


@pytest.mark.unit
def test_readyz_critical_provider_fails_after_hysteresis_window():
    os.environ["READYZ_HYSTERESIS_SECONDS"] = "0.01"
    prv = _FakeProvider("critical-x", ok=False)
    client = _build_client(critical_providers=lambda: [prv])
    # First call records DOWN since now.
    assert client.get("/readyz").status_code == 200
    # Wait past the window.
    time.sleep(0.05)
    # Now it should flip to 503.
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert any(
        p.startswith("provider_down:critical-x")
        for p in resp.json()["detail"]["problems"]
    )


@pytest.mark.unit
def test_readyz_recovers_when_critical_provider_returns_ok():
    os.environ["READYZ_HYSTERESIS_SECONDS"] = "0.01"
    prv = _FakeProvider("critical-y", ok=False)
    client = _build_client(critical_providers=lambda: [prv])
    client.get("/readyz")  # record DOWN
    time.sleep(0.05)
    assert client.get("/readyz").status_code == 503
    prv.ok = True
    resp = client.get("/readyz")
    assert resp.status_code == 200
    # Tracker entry is cleared on recovery.
    assert "critical-y" not in _CRITICAL_PROVIDER_DOWN_SINCE


@pytest.mark.unit
def test_readyz_provider_health_check_exception_treated_as_down():
    os.environ["READYZ_HYSTERESIS_SECONDS"] = "0.01"

    class _Boom:
        __name__ = "boom"

        def health_check(self):
            raise RuntimeError("upstream timeout")

    client = _build_client(critical_providers=lambda: [_Boom()])
    client.get("/readyz")  # first observation
    time.sleep(0.05)
    resp = client.get("/readyz")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /admin/dlq listing + filtering
# ---------------------------------------------------------------------------


def _entry(
    *,
    id: str = "e1",
    provider: str = "stripe",
    ability: str = "send_email",
    error: str = "TransientExternalError: timeout",
    moved_at: Optional[datetime] = None,
) -> dict:
    return {
        "id": id,
        "operation_type": "outbound",
        "target_provider": provider,
        "target_ability": ability,
        "payload": {},
        "idempotency_key": id,
        "final_error": error,
        "moved_at": moved_at or datetime.now(timezone.utc),
    }


@pytest.mark.unit
def test_apply_dlq_filter_extension_match():
    entries = [_entry(provider="stripe"), _entry(provider="paypal")]
    out = _apply_dlq_filter(entries, DLQFilter(extension="stripe"))
    assert len(out) == 1
    assert out[0]["target_provider"] == "stripe"


@pytest.mark.unit
def test_apply_dlq_filter_error_class_substring():
    entries = [
        _entry(error="TransientExternalError: timeout"),
        _entry(error="AuthExternalError: bad key"),
    ]
    out = _apply_dlq_filter(entries, DLQFilter(error_class="TransientExternal"))
    assert len(out) == 1


@pytest.mark.unit
def test_apply_dlq_filter_time_window():
    now = datetime.now(timezone.utc)
    old = _entry(id="old", moved_at=now - timedelta(hours=2))
    fresh = _entry(id="fresh", moved_at=now)
    out = _apply_dlq_filter(
        [old, fresh], DLQFilter(since=now - timedelta(minutes=30))
    )
    assert [e["id"] for e in out] == ["fresh"]


@pytest.mark.unit
def test_dlq_endpoint_returns_filtered_entries():
    entries = [_entry(id="a", provider="stripe"), _entry(id="b", provider="paypal")]
    client = _build_client(dlq_lister=lambda **_: list(entries))
    resp = client.get("/admin/dlq?extension=stripe")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["entries"][0]["id"] == "a"


@pytest.mark.unit
def test_dlq_endpoint_default_lister_returns_empty_list():
    client = _build_client()
    resp = client.get("/admin/dlq")
    assert resp.status_code == 200
    assert resp.json() == {"entries": [], "total": 0}


# ---------------------------------------------------------------------------
# /admin/dlq replay + discard
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dlq_replay_invokes_handler():
    seen: List[str] = []

    def replay(eid: str) -> bool:
        seen.append(eid)
        return True

    client = _build_client(dlq_replay=replay)
    resp = client.post("/admin/dlq/abc/replay")
    assert resp.status_code == 200
    assert resp.json() == {"status": "replayed", "id": "abc"}
    assert seen == ["abc"]


@pytest.mark.unit
def test_dlq_replay_404_when_handler_returns_false():
    client = _build_client(dlq_replay=lambda _eid: False)
    resp = client.post("/admin/dlq/missing/replay")
    assert resp.status_code == 404


@pytest.mark.unit
def test_dlq_replay_501_when_not_wired():
    client = _build_client()
    resp = client.post("/admin/dlq/abc/replay")
    assert resp.status_code == 501


@pytest.mark.unit
def test_dlq_discard_invokes_handler():
    seen: List[str] = []
    client = _build_client(
        dlq_discard=lambda eid: (seen.append(eid) or True)
    )
    resp = client.post("/admin/dlq/xyz/discard")
    assert resp.status_code == 200
    assert resp.json() == {"status": "discarded", "id": "xyz"}
    assert seen == ["xyz"]


# ---------------------------------------------------------------------------
# /admin/services
# ---------------------------------------------------------------------------


class _FakeService:
    def __init__(self, name: str, failed: bool = True, reason: str = "boom") -> None:
        self.service_id = name
        self.failed = failed
        self._failed_reason = reason


@pytest.mark.unit
def test_list_failed_services_empty():
    client = _build_client(failed_services_lister=lambda: [])
    resp = client.get("/admin/services")
    assert resp.status_code == 200
    assert resp.json() == {"services": [], "total": 0}


@pytest.mark.unit
def test_list_failed_services_renders_identity_and_reason():
    svc = _FakeService("queue-drain")
    client = _build_client(failed_services_lister=lambda: [svc])
    resp = client.get("/admin/services")
    body = resp.json()
    assert body["total"] == 1
    assert body["services"][0]["name"] == "queue-drain"
    assert body["services"][0]["failed"] is True
    assert body["services"][0]["failed_reason"] == "boom"


@pytest.mark.unit
def test_service_reset_calls_handler():
    seen: List[str] = []
    client = _build_client(
        service_reset=lambda name: (seen.append(name) or True)
    )
    resp = client.post("/admin/services/queue-drain/reset")
    assert resp.status_code == 200
    assert seen == ["queue-drain"]


@pytest.mark.unit
def test_service_reset_404_when_not_failed():
    client = _build_client(service_reset=lambda _name: False)
    resp = client.post("/admin/services/unknown/reset")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Alerts + AlertManager YAML
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_register_alert_appends_to_registry():
    register_alert(
        Alert(
            metric="provider_silent_drop_total",
            threshold=0,
            window="5m",
            runbook="docs/runbooks/silent-drop.md",
        )
    )
    assert len(ALERT_REGISTRY) == 1
    assert ALERT_REGISTRY[0].metric == "provider_silent_drop_total"


@pytest.mark.unit
def test_generate_alertmanager_yaml_empty_registry():
    out = generate_alertmanager_yaml()
    assert "groups:" in out
    assert "rules: []" in out


@pytest.mark.unit
def test_generate_alertmanager_yaml_renders_each_alert():
    register_alert(
        Alert(
            metric="dlq_depth",
            threshold=10,
            window="1m",
            runbook="docs/runbooks/dlq-depth.md",
            severity="critical",
            description="Outbox DLQ has accumulated entries",
        )
    )
    out = generate_alertmanager_yaml()
    assert "alert: dlq_depth" in out
    assert "expr: dlq_depth > 10" in out
    assert "for: 1m" in out
    assert "severity: critical" in out
    assert '"docs/runbooks/dlq-depth.md"' in out
    assert "description:" in out


@pytest.mark.unit
def test_alert_to_alertmanager_dict_shape():
    a = Alert(metric="m", threshold=1, window="2m", runbook="r")
    d = a.to_alertmanager_dict()
    assert d["alert"] == "m"
    assert d["expr"] == "m > 1"
    assert d["for"] == "2m"
    assert d["labels"] == {"severity": "warning"}
    assert d["annotations"]["runbook_url"] == "r"


# ---------------------------------------------------------------------------
# Runbook URL writer
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_add_runbook_url_to_error_sets_attribute():
    err = ValueError("boom")
    add_runbook_url_to_error(err, "docs/runbooks/value-error.md")
    assert getattr(err, "runbook_url") == "docs/runbooks/value-error.md"


@pytest.mark.unit
def test_add_runbook_url_overrides_class_default():
    """When the error class declares a default runbook_url (Item 1's
    BaseExternalError), the writer's URL takes precedence on the instance."""

    class _Typed(Exception):
        runbook_url = "default.md"

    e = _Typed()
    add_runbook_url_to_error(e, "instance.md")
    assert e.runbook_url == "instance.md"


# ---------------------------------------------------------------------------
# _is_health_ok tolerance
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_health_ok_handles_string_status():
    class R:
        status = "ok"

    assert _is_health_ok(R()) is True


@pytest.mark.unit
def test_is_health_ok_handles_enum_value_status():
    class S:
        value = "OK"

    class R:
        status = S()

    assert _is_health_ok(R()) is True


@pytest.mark.unit
def test_is_health_ok_returns_false_for_down():
    class R:
        status = "down"

    assert _is_health_ok(R()) is False
