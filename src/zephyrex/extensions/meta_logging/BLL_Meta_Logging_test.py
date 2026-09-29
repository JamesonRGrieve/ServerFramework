# SPDX-License-Identifier: AGPL-3.0-or-later
"""meta_logging's managers persist log records and guard every call on them.

The hook unit tests drive the hook functions with registry-less managers; the
integration tests build a real app that loaded meta_logging (private SQLite)
and exercise the managers, their bound hooks and billing's cost-audit emitter
end to end."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import HTTPException
from loguru import logger as loguru_logger

from zephyrex.extensions.billing.BLL_CostAuditEmitter import make_cost_audit_emitter
from zephyrex.extensions.meta_logging.BLL_Meta_Logging import (
    META_LOG_QUERY_LIMIT,
    AuditLogManager,
    AuditLogModel,
    SystemLogManager,
    SystemLogModel,
    access_log_hook,
    query_rate_limit_hook,
    require_requester_hook,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import _inmemory_counter
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    HookContext,
    HookTiming,
)


@dataclass
class _Requester:
    id: str
    email: str | None = None


class _QueryManager(AbstractBLLManager[Any]):
    """A manager built without a registry (so no DB lookup); each context
    builds a fresh one, as each request does."""


def _context(
    requester_id: str | None,
    method: str = "list",
    timing: HookTiming = HookTiming.BEFORE,
) -> HookContext:
    manager = _QueryManager()
    if requester_id is not None:
        manager.requester = _Requester(id=requester_id)
    return HookContext(
        manager=manager, method_name=method, args=(), kwargs={}, timing=timing
    )


@pytest.fixture
def fresh_rate_limit_counter() -> Iterator[None]:
    _inmemory_counter.reset()
    yield
    _inmemory_counter.reset()


@pytest.fixture
def debug_messages() -> Iterator[list[str]]:
    """Messages logged at DEBUG and above while the test runs."""
    messages: list[str] = []
    sink_id = loguru_logger.add(
        lambda message: messages.append(message.record["message"]), level="DEBUG"
    )
    try:
        yield messages
    finally:
        loguru_logger.remove(sink_id)


@pytest.mark.usefixtures("fresh_rate_limit_counter")
class TestQueryRateLimit:
    """The limit persists across requests: each request builds a fresh
    manager, so the tally lives in the shared counter, not on the manager."""

    def test_blocks_the_query_after_the_limit_across_fresh_managers(self):
        for _ in range(META_LOG_QUERY_LIMIT):
            query_rate_limit_hook(_context("user-1"))
        with pytest.raises(HTTPException) as exc:
            query_rate_limit_hook(_context("user-1"))
        assert exc.value.status_code == 429

    def test_limit_is_per_requester(self):
        for _ in range(META_LOG_QUERY_LIMIT):
            query_rate_limit_hook(_context("user-1"))
        query_rate_limit_hook(_context("user-2"))

    def test_non_query_methods_are_not_counted(self):
        for _ in range(META_LOG_QUERY_LIMIT * 2):
            query_rate_limit_hook(_context("user-1", "create"))
        query_rate_limit_hook(_context("user-1"))

    def test_search_counts_against_the_same_limit_as_list(self):
        for _ in range(META_LOG_QUERY_LIMIT):
            query_rate_limit_hook(_context("user-1", "search"))
        with pytest.raises(HTTPException):
            query_rate_limit_hook(_context("user-1", "list"))


class TestRequireRequester:
    def test_rejects_a_manager_without_a_requester(self):
        with pytest.raises(HTTPException) as exc:
            require_requester_hook(_context(None, "create"))
        assert exc.value.status_code == 401

    def test_admits_an_authenticated_requester(self):
        require_requester_hook(_context("user-1", "create"))


class TestAccessLog:
    def test_logs_the_call_and_its_outcome(self, debug_messages: list[str]):
        context = _context("user-1", "list")
        access_log_hook(context)
        context.timing = HookTiming.AFTER
        context.result = ["a", "b", "c"]
        access_log_hook(context)

        assert "Meta logging access: list by user user-1" in debug_messages
        completed = [
            m for m in debug_messages if m.startswith("Meta logging completed")
        ]
        assert len(completed) == 1
        assert "list by user user-1, success=True" in completed[0]
        assert completed[0].endswith("results=3")

    def test_counts_no_results_for_an_unsized_result(self, debug_messages: list[str]):
        context = _context("user-1", "get")
        access_log_hook(context)
        context.timing = HookTiming.AFTER
        context.result = None
        access_log_hook(context)

        completed = [
            m for m in debug_messages if m.startswith("Meta logging completed")
        ]
        assert "success=False" in completed[0]
        assert completed[0].endswith("results=0")


@pytest.fixture(scope="module")
def model_registry(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    """The committed model registry of a real app that loaded meta_logging."""
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    database_dir: Path = tmp_path_factory.mktemp("meta_logging_db")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("DATABASE_PATH", str(database_dir))
        prepare_test_registry()
        app = instance(
            db_prefix=f"test.meta_logging.{uuid.uuid4().hex[:8]}",
            extensions="meta_logging",
        )
        yield app.state.model_registry


def _audit_logs(model_registry: Any) -> AuditLogManager:
    return AuditLogManager(requester_id=env("ROOT_ID"), model_registry=model_registry)


class TestManagersOnARealApp:
    def test_the_app_binds_both_log_models(self, model_registry: Any):
        assert "meta_logging" in model_registry.loaded_extension_names()
        assert model_registry.is_model_bound(AuditLogModel)
        assert model_registry.is_model_bound(SystemLogModel)

    def test_log_audit_event_persists_the_event(self, model_registry: Any):
        manager = _audit_logs(model_registry)
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        logged = manager.log_audit_event(
            AuditLogModel.Create(
                user_id="user-1",
                action="update_profile",
                resource_type="user",
                resource_id="user-2",
                ip_address="192.0.2.1",
                success=False,
                error_message="denied",
                additional_data={"field": "email"},
                privacy_impact=True,
                data_categories=["contact_info"],
            )
        )

        stored = manager.get(id=logged.id)
        assert stored.action == "update_profile"
        assert stored.resource_type == "user"
        assert stored.resource_id == "user-2"
        assert stored.ip_address == "192.0.2.1"
        assert stored.success is False
        assert stored.error_message == "denied"
        assert stored.additional_data == {"field": "email"}
        assert stored.privacy_impact is True
        assert stored.data_categories == ["contact_info"]
        after = datetime.now(timezone.utc).replace(tzinfo=None)
        assert before <= stored.timestamp.replace(tzinfo=None) <= after

    def test_system_log_manager_persists_an_event(self, model_registry: Any):
        manager = SystemLogManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        )
        occurred_at = datetime(2026, 1, 2, 3, 4, 5)
        created = manager.create(
            timestamp=occurred_at,
            level="ERROR",
            component="authentication",
            message="token expired",
            request_id="req-1",
            additional_data={"code": "JWT_EXPIRED"},
        )

        stored = manager.get(id=created.id)
        assert stored.timestamp.replace(tzinfo=None) == occurred_at
        assert stored.level == "ERROR"
        assert stored.component == "authentication"
        assert stored.message == "token expired"
        assert stored.request_id == "req-1"
        assert stored.additional_data == {"code": "JWT_EXPIRED"}

    def test_cost_audit_emitter_writes_through_the_audit_log(self, model_registry: Any):
        tenant_id = f"team-{uuid.uuid4().hex[:8]}"
        emit = make_cost_audit_emitter(lambda: _audit_logs(model_registry))

        emit(
            {
                "tenant_id": tenant_id,
                "provider": "openai",
                "ability": "complete",
                "cost_usd": Decimal("0.0125"),
            }
        )

        rows = _audit_logs(model_registry).list(user_id=tenant_id)
        assert len(rows) == 1
        assert rows[0].action == "provider_cost"
        assert rows[0].resource_type == "provider"
        assert rows[0].resource_id == "openai"
        additional_data = rows[0].additional_data
        assert additional_data is not None
        assert additional_data["cost_usd"] == "0.0125"
        assert additional_data["ability"] == "complete"


class TestHooksOnARealApp:
    """The hooks bound at import run on the managers of a built app."""

    def test_a_manager_without_a_requester_is_rejected(self, model_registry: Any):
        manager = AuditLogManager(model_registry=model_registry)
        with pytest.raises(HTTPException) as exc:
            manager.create(action="read", resource_type="report")
        assert exc.value.status_code == 401

    @pytest.mark.usefixtures("fresh_rate_limit_counter")
    def test_queries_are_rate_limited_per_requester(self, model_registry: Any):
        for _ in range(META_LOG_QUERY_LIMIT):
            _audit_logs(model_registry).list()
        with pytest.raises(HTTPException) as exc:
            _audit_logs(model_registry).list()
        assert exc.value.status_code == 429

    @pytest.mark.usefixtures("fresh_rate_limit_counter")
    def test_calls_are_access_logged(
        self, model_registry: Any, debug_messages: list[str]
    ):
        SystemLogManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).list()

        root_id = env("ROOT_ID")
        assert f"Meta logging access: list by user {root_id}" in debug_messages
        assert any(
            m.startswith(f"Meta logging completed: list by user {root_id}")
            for m in debug_messages
        )
