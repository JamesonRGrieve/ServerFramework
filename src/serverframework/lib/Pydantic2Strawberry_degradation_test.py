"""Item 48 — GraphQL degradation sentinel conversion tests.

Real Strawberry schemas, no mocks: each test builds a tiny schema whose
resolver returns one of the framework's rotation-exhaustion sentinels and
asserts the response payload exposes a typed degradation arm
(``QueuedForRetryGQL`` / ``SilentDroppedGQL``) rather than collapsing to
``null``.
"""

from __future__ import annotations

from typing import Annotated, Optional, Union

import strawberry
from strawberry.types import Info

from serverframework.extensions.ExternalErrors import (
    QueuedForRetry,
    SilentDropped,
)
from serverframework.lib.Pydantic2Strawberry import (
    QueuedForRetryGQL,
    SilentDroppedGQL,
    degradation_aware,
    render_degradation_sentinel_gql,
)


# ---------------------------------------------------------------------------
# render_degradation_sentinel_gql converter
# ---------------------------------------------------------------------------


def test_render_queued_for_retry_returns_typed_result() -> None:
    converted = render_degradation_sentinel_gql(QueuedForRetry(tracking_id="abc"))
    assert isinstance(converted, QueuedForRetryGQL)
    assert converted.status == "accepted"
    assert converted.tracking_id == "abc"


def test_render_silent_dropped_returns_typed_result() -> None:
    converted = render_degradation_sentinel_gql(
        SilentDropped(provider="email_provider", ability="send_email")
    )
    assert isinstance(converted, SilentDroppedGQL)
    assert converted.status == "silent_dropped"
    assert converted.provider == "email_provider"
    assert converted.ability == "send_email"


def test_render_passes_through_non_sentinel() -> None:
    assert render_degradation_sentinel_gql(None) is None
    assert render_degradation_sentinel_gql({"id": "w1"}) is None
    assert render_degradation_sentinel_gql(42) is None


# ---------------------------------------------------------------------------
# Strawberry schema integration via degradation_aware decorator
# ---------------------------------------------------------------------------


@strawberry.type
class WidgetGQL:
    id: str
    name: str


SendResult = Annotated[
    Union[WidgetGQL, QueuedForRetryGQL, SilentDroppedGQL],
    strawberry.union("SendResult"),
]


def _build_schema(payload):
    @degradation_aware
    def send_resolver(info: Info) -> SendResult:  # type: ignore[valid-type]
        return payload

    Query = strawberry.type(
        type(
            "Query",
            (),
            {
                "__annotations__": {"send": SendResult},
                "send": strawberry.field(resolver=send_resolver),
            },
        )
    )
    return strawberry.Schema(query=Query)


def test_queued_for_retry_propagates_through_graphql_resolver() -> None:
    schema = _build_schema(QueuedForRetry(tracking_id="abc"))
    result = schema.execute_sync(
        "{ send { __typename ... on QueuedForRetryGQL { status trackingId } } }"
    )
    assert result.errors is None, result.errors
    assert result.data == {
        "send": {
            "__typename": "QueuedForRetryGQL",
            "status": "accepted",
            "trackingId": "abc",
        }
    }


def test_silent_dropped_propagates_through_graphql_resolver() -> None:
    schema = _build_schema(SilentDropped(provider="p", ability="a"))
    result = schema.execute_sync(
        "{ send { __typename ... on SilentDroppedGQL { status provider ability } } }"
    )
    assert result.errors is None, result.errors
    assert result.data == {
        "send": {
            "__typename": "SilentDroppedGQL",
            "status": "silent_dropped",
            "provider": "p",
            "ability": "a",
        }
    }


def test_normal_payload_passes_through_graphql_resolver() -> None:
    schema = _build_schema(WidgetGQL(id="w1", name="widget"))
    result = schema.execute_sync(
        "{ send { __typename ... on WidgetGQL { id name } } }"
    )
    assert result.errors is None, result.errors
    assert result.data == {
        "send": {"__typename": "WidgetGQL", "id": "w1", "name": "widget"}
    }


# ---------------------------------------------------------------------------
# degradation_aware preserves async resolvers
# ---------------------------------------------------------------------------


async def test_degradation_aware_handles_async_resolvers() -> None:
    @degradation_aware
    async def send_async(info: Info) -> SendResult:  # type: ignore[valid-type]
        return QueuedForRetry(tracking_id="xyz")

    Query = strawberry.type(
        type(
            "Query",
            (),
            {
                "__annotations__": {"send": SendResult},
                "send": strawberry.field(resolver=send_async),
            },
        )
    )
    schema = strawberry.Schema(query=Query)
    result = await schema.execute(
        "{ send { __typename ... on QueuedForRetryGQL { trackingId } } }"
    )
    assert result.errors is None, result.errors
    assert result.data == {
        "send": {"__typename": "QueuedForRetryGQL", "trackingId": "xyz"}
    }


def test_degradation_aware_preserves_function_metadata() -> None:
    @degradation_aware
    def named_resolver(info: Info) -> SendResult:  # type: ignore[valid-type]
        """Doc preserved."""
        return None

    assert named_resolver.__name__ == "named_resolver"
    assert named_resolver.__doc__ == "Doc preserved."
