"""Item 48 — degradation sentinel HTTP wire-shape tests."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from serverframework.extensions.ExternalErrors import (
    QueuedForRetry,
    SilentDropped,
)
from serverframework.lib.Pydantic2FastAPI import (
    _degradation_responses_annotation,
    _render_degradation_sentinel,
)


class _FakeManager:
    """Mimics the minimum surface a route uses: a callable method."""

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def do(self) -> Any:
        return self._payload


def _build_app(payload: Any) -> TestClient:
    app = FastAPI()
    router = APIRouter()
    manager = _FakeManager(payload)

    @router.post("/v1/widgets")
    def create_widget(manager_: _FakeManager = Depends(lambda: manager)):
        result = manager_.do()
        if (resp := _render_degradation_sentinel(result)) is not None:
            return resp
        return {"widget": result}

    app.include_router(router)
    return TestClient(app)


def test_queued_for_retry_returns_202_with_tracking_id() -> None:
    client = _build_app(QueuedForRetry(tracking_id="abc"))
    response = client.post("/v1/widgets")
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "tracking_id": "abc"}


def test_silent_dropped_returns_200_with_documented_body() -> None:
    client = _build_app(SilentDropped(provider="x", ability="y"))
    response = client.post("/v1/widgets")
    assert response.status_code == 200
    assert response.json() == {
        "status": "silent_dropped",
        "provider": "x",
        "ability": "y",
    }


def test_normal_value_passes_through_unchanged() -> None:
    client = _build_app({"id": "w1", "name": "widget"})
    response = client.post("/v1/widgets")
    assert response.status_code == 200
    assert response.json() == {"widget": {"id": "w1", "name": "widget"}}


def test_render_helper_returns_none_for_non_sentinel() -> None:
    assert _render_degradation_sentinel(None) is None
    assert _render_degradation_sentinel({"foo": "bar"}) is None
    assert _render_degradation_sentinel(42) is None


def test_degradation_responses_annotation_empty_when_no_policy() -> None:
    class PlainManager:
        pass

    assert _degradation_responses_annotation(PlainManager) == {}


def test_degradation_responses_annotation_includes_202_when_queue_and_retry() -> None:
    from serverframework.extensions.ExternalErrors import (
        DegradationPolicy,
        DegradationMode,
    )

    class QueueProvider:
        degradation_policy = DegradationPolicy(mode=DegradationMode.QUEUE_AND_RETRY)

    class QueuedManager:
        provider = QueueProvider

    annotation = _degradation_responses_annotation(QueuedManager)
    assert 202 in annotation
    assert "tracking_id" in str(annotation[202].get("model").model_fields)


def test_degradation_responses_annotation_skips_fail_fast() -> None:
    from serverframework.extensions.ExternalErrors import (
        DegradationPolicy,
        DegradationMode,
    )

    class FailFastProvider:
        degradation_policy = DegradationPolicy(mode=DegradationMode.FAIL_FAST)

    class FailFastManager:
        provider = FailFastProvider

    assert _degradation_responses_annotation(FailFastManager) == {}
