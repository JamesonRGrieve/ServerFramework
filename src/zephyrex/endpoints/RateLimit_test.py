# SPDX-License-Identifier: AGPL-3.0-or-later
"""@rate_limit policies on manager methods are enforced over HTTP.

Route discovery used to walk only the app's top-level routes, but included
routers stay nested, and the route adapters served each method through an
endpoint that did not carry its @rate_limit metadata: no policy was ever
registered, so every limit was decorative.
"""

from zephyrex.lib.InboundSecurity import (
    DEFAULT_AUTH_RATE_LIMIT,
    _RATE_LIMIT_REGISTRY,
    _rate_limit_policy,
    parse_rate_spec,
    register_rate_limited_route,
)


def test_registration_is_registered_with_its_policy(server):
    count, window = parse_rate_spec(DEFAULT_AUTH_RATE_LIMIT)
    assert _RATE_LIMIT_REGISTRY[("POST", "/v1/user")] == (count, window, "ip")


def test_registration_is_throttled_per_ip(server):
    count, _ = parse_rate_spec(DEFAULT_AUTH_RATE_LIMIT)
    # Invalid bodies: nothing is created, but every attempt is counted.
    statuses = [
        server.post("/v1/user", json={"user": {}}).status_code for _ in range(count)
    ]
    assert 429 not in statuses

    over = server.post("/v1/user", json={"user": {}})
    assert over.status_code == 429
    assert over.headers.get("Retry-After")


def test_templated_paths_match_by_pattern():
    """The middleware runs before routing, so a templated route's policy is
    found by matching the request path against its pattern."""
    register_rate_limited_route(
        method="POST",
        path="/v1/rate-limit-probe/{item_id}/act",
        count=3,
        window_seconds=60,
        scope="ip",
    )

    template = "/v1/rate-limit-probe/{item_id}/act"
    # Every value of the parameter counts against the template, so varying
    # it can't buy a fresh budget (#276).
    for item in ("abc", "xyz"):
        assert _rate_limit_policy("POST", f"/v1/rate-limit-probe/{item}/act") == (
            (3, 60, "ip"),
            template,
        )
    assert _rate_limit_policy("POST", "/v1/rate-limit-probe/abc") is None
    assert _rate_limit_policy("GET", "/v1/rate-limit-probe/abc/act") is None


def test_idle_counter_keys_are_dropped():
    """#276: the in-memory counter dropped old timestamps but never its keys,
    so every one-off key stayed for the life of the process."""
    from zephyrex.lib.InboundSecurity import _InMemoryCounter

    counter = _InMemoryCounter()
    counter.incr("one-off", 60)
    counter._buckets["one-off"][-1] -= 120  # last counted two windows ago
    for n in range(_InMemoryCounter.SWEEP_EVERY):
        counter.incr("steady", 60)
    assert "one-off" not in counter._buckets
    assert counter.incr("steady", 60) == _InMemoryCounter.SWEEP_EVERY + 1
