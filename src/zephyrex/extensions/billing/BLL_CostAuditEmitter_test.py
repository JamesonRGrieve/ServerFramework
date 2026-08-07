"""Tests for CostAuditEmitter (Item 84)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

import pytest

from zephyrex.extensions.billing.BLL_CostAuditEmitter import (
    get_cost_audit_emitter,
    make_cost_audit_emitter,
    register_cost_audit_emitter,
)


@pytest.fixture(autouse=True)
def _clear_registration():
    """Each test starts with a fresh emitter registration."""
    register_cost_audit_emitter(None)
    yield
    register_cost_audit_emitter(None)


# ---------- register / get round-trip ----------------------------------


def test_get_returns_none_when_unregistered():
    assert get_cost_audit_emitter() is None


def test_register_and_get_roundtrip():
    def my_emitter(payload: Dict[str, Any]) -> None:
        pass

    register_cost_audit_emitter(my_emitter)
    assert get_cost_audit_emitter() is my_emitter


def test_register_none_clears():
    register_cost_audit_emitter(lambda p: None)
    register_cost_audit_emitter(None)
    assert get_cost_audit_emitter() is None


# ---------- make_cost_audit_emitter -----------------------------------


class _FakeAuditManager:
    def __init__(self, fail: bool = False) -> None:
        self.calls: List[Any] = []
        self._fail = fail

    def log_audit_event(self, create) -> Dict[str, Any]:
        if self._fail:
            raise RuntimeError("influx unreachable")
        self.calls.append(create)
        return {"success": True}


def test_emitter_persists_payload_via_factory():
    mgr = _FakeAuditManager()
    emit = make_cost_audit_emitter(lambda: mgr)

    occurred_at = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    emit(
        {
            "tenant_id": "team-1",
            "provider": "openai",
            "ability": "complete",
            "cost_usd": Decimal("0.0125"),
            "occurred_at": occurred_at,
            "traceparent": "00-abcd-efgh-01",
        }
    )

    assert len(mgr.calls) == 1
    create = mgr.calls[0]
    assert create.action == "provider_cost"
    assert create.resource_type == "provider"
    assert create.resource_id == "openai"
    assert create.user_id == "team-1"
    assert create.success is True
    additional = create.additional_data
    assert additional["tenant_id"] == "team-1"
    assert additional["provider"] == "openai"
    assert additional["ability"] == "complete"
    assert additional["cost_usd"] == "0.0125"
    assert additional["occurred_at"] == occurred_at.isoformat()
    assert additional["traceparent"] == "00-abcd-efgh-01"


def test_emitter_handles_persistence_failure_gracefully():
    mgr = _FakeAuditManager(fail=True)
    emit = make_cost_audit_emitter(lambda: mgr)

    # Must not raise even though log_audit_event raises.
    emit(
        {
            "tenant_id": "team-1",
            "provider": "openai",
            "ability": "complete",
            "cost_usd": Decimal("0.10"),
        }
    )


def test_emitter_handles_factory_failure_gracefully():
    def bad_factory():
        raise RuntimeError("manager construction failed")

    emit = make_cost_audit_emitter(bad_factory)
    # Must not raise.
    emit(
        {
            "tenant_id": "team-1",
            "provider": "openai",
            "ability": "complete",
            "cost_usd": Decimal("0.10"),
        }
    )


def test_emitter_supplies_default_occurred_at_when_missing():
    mgr = _FakeAuditManager()
    emit = make_cost_audit_emitter(lambda: mgr)
    emit(
        {
            "tenant_id": "team-1",
            "provider": "openai",
            "ability": "complete",
            "cost_usd": Decimal("0.10"),
        }
    )
    assert len(mgr.calls) == 1
    assert "occurred_at" in mgr.calls[0].additional_data


def test_emitter_coerces_numeric_cost_strings():
    mgr = _FakeAuditManager()
    emit = make_cost_audit_emitter(lambda: mgr)
    emit(
        {
            "tenant_id": "team-1",
            "provider": "openai",
            "ability": "complete",
            "cost_usd": "0.0001",
        }
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0].additional_data["cost_usd"] == "0.0001"


def test_emitter_omits_traceparent_when_absent():
    mgr = _FakeAuditManager()
    emit = make_cost_audit_emitter(lambda: mgr)
    emit(
        {
            "tenant_id": "team-1",
            "provider": "openai",
            "ability": "complete",
            "cost_usd": Decimal("0.10"),
        }
    )
    assert "traceparent" not in mgr.calls[0].additional_data


def test_emitter_handles_unknown_fields_with_defaults():
    mgr = _FakeAuditManager()
    emit = make_cost_audit_emitter(lambda: mgr)
    emit({})  # totally empty payload — should not raise

    if mgr.calls:
        create = mgr.calls[0]
        assert create.action == "provider_cost"
        assert create.resource_type == "provider"


def test_register_then_invoke_through_get():
    """Mirrors how RotationManager.rotate would consult the registered emitter."""
    received: List[Dict[str, Any]] = []
    register_cost_audit_emitter(received.append)

    consumer = get_cost_audit_emitter()
    assert consumer is not None
    consumer(
        {
            "tenant_id": "team-1",
            "provider": "openai",
            "ability": "complete",
            "cost_usd": Decimal("0.05"),
        }
    )
    assert len(received) == 1
    assert received[0]["tenant_id"] == "team-1"
