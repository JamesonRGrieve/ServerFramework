"""Item 48 — GraphQL degradation conversion is deferred to a follow-up."""

from __future__ import annotations

import pytest


@pytest.mark.xfail(
    reason="Item 48 GraphQL conversion deferred to follow-up",
    strict=False,
)
def test_queued_for_retry_propagates_through_graphql_resolver() -> None:
    """Placeholder: when the GraphQL resolver wrapper is updated, this
    test should construct a Strawberry schema with a resolver that
    returns ``QueuedForRetry(tracking_id="abc")`` and assert the
    response payload exposes ``tracking_id`` / ``status`` as a typed
    field rather than collapsing to ``null``.
    """

    raise AssertionError("graphql conversion not yet implemented")
