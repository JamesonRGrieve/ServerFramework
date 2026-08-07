"""Tests for Permissions registry and resolver (Item 18)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from zephyrex.logic.Permissions import (
    PermissionCollisionError,
    PermissionDef,
    PermissionRegistry,
    collect_extension_permissions_into,
    expand_wildcard,
    has_permission,
    is_session_freshly_verified,
    iter_extension_permissions,
)


def test_permission_def_is_frozen():
    perm = PermissionDef(name="payment.subscription.read", description="Read")
    with pytest.raises(Exception):
        perm.name = "other"  # type: ignore[misc]


def test_register_and_get():
    reg = PermissionRegistry()
    perm = PermissionDef(name="payment.subscription.read", description="Read")
    reg.register([perm], source="payment")
    assert reg.get("payment.subscription.read") is perm
    assert "payment.subscription.read" in reg
    assert list(reg.iter_permissions()) == [perm]


def test_identical_duplicate_accepted():
    reg = PermissionRegistry()
    perm = PermissionDef(name="payment.subscription.read", description="Read")
    reg.register([perm], source="payment")
    reg.register([perm], source="another")
    # No exception; both sources tracked.
    assert sorted(reg.sources("payment.subscription.read")) == [
        "another",
        "payment",
    ]


def test_conflicting_duplicate_raises_with_sources():
    reg = PermissionRegistry()
    p1 = PermissionDef(name="payment.subscription.read", description="Read")
    p2 = PermissionDef(name="payment.subscription.read", description="Different")
    reg.register([p1], source="payment")
    with pytest.raises(PermissionCollisionError) as exc:
        reg.register([p2], source="legacy_billing")
    assert "payment" in exc.value.sources
    assert "legacy_billing" in exc.value.sources


def test_has_permission_direct_role():
    reg = PermissionRegistry()
    reg.register(
        [PermissionDef(name="payment.subscription.read", description="Read")],
        source="payment",
    )
    assert has_permission(
        "payment.subscription.read",
        role_grants={"payment.subscription.read"},
        registry=reg,
    )
    assert not has_permission(
        "payment.subscription.write",
        role_grants={"payment.subscription.read"},
        registry=reg,
    )


def test_has_permission_oauth_intersection_invariant():
    """Token can never escalate beyond role grants."""
    reg = PermissionRegistry()
    reg.register(
        [
            PermissionDef(name="payment.subscription.read", description="Read"),
            PermissionDef(name="payment.subscription.write", description="Write"),
        ],
        source="payment",
    )
    # Role grants only read; token claims write -- write must NOT be granted.
    assert not has_permission(
        "payment.subscription.write",
        role_grants={"payment.subscription.read"},
        token_scopes={"payment.subscription.write"},
        registry=reg,
    )
    # Both role and token grant read -- granted.
    assert has_permission(
        "payment.subscription.read",
        role_grants={"payment.subscription.read"},
        token_scopes={"payment.subscription.read"},
        registry=reg,
    )


def test_has_permission_walks_implies_graph():
    reg = PermissionRegistry()
    reg.register(
        [
            PermissionDef(
                name="payment.subscription",
                description="All sub ops",
                implies=("payment.subscription.read", "payment.subscription.write"),
            ),
            PermissionDef(name="payment.subscription.read", description="Read"),
            PermissionDef(name="payment.subscription.write", description="Write"),
        ],
        source="payment",
    )
    assert has_permission(
        "payment.subscription.read",
        role_grants={"payment.subscription"},
        registry=reg,
    )


def test_expand_wildcard():
    reg = PermissionRegistry()
    reg.register(
        [
            PermissionDef(name="payment.subscription.read", description="Read"),
            PermissionDef(name="payment.subscription.write", description="Write"),
            PermissionDef(name="payment.invoice.read", description="Read"),
            PermissionDef(
                name="payment.subscription.system",
                description="System",
                system_only=True,
            ),
        ],
        source="payment",
    )
    expansion = expand_wildcard("payment.subscription.*", reg)
    assert "payment.subscription.read" in expansion
    assert "payment.subscription.write" in expansion
    assert "payment.invoice.read" not in expansion
    # system_only excluded from wildcard expansions.
    assert "payment.subscription.system" not in expansion


def test_expand_wildcard_concrete_passthrough():
    reg = PermissionRegistry()
    reg.register(
        [PermissionDef(name="payment.subscription.read", description="Read")],
        source="payment",
    )
    assert expand_wildcard("payment.subscription.read", reg) == {
        "payment.subscription.read"
    }
    assert expand_wildcard("payment.unknown.read", reg) == set()


def test_is_session_freshly_verified():
    now = datetime(2026, 4, 28, 15, 0, 0, tzinfo=timezone.utc)

    class S:
        verified_at = now - timedelta(seconds=60)

    assert is_session_freshly_verified(S(), freshness_window_seconds=300, now=now)

    class Stale:
        verified_at = now - timedelta(seconds=600)

    assert not is_session_freshly_verified(
        Stale(), freshness_window_seconds=300, now=now
    )

    assert not is_session_freshly_verified(None, now=now)


def test_iter_extension_permissions_uses_get_permissions():
    class FakeExt:
        name = "fake"

        @classmethod
        def get_permissions(cls):
            return [PermissionDef(name="fake.thing.read", description="r")]

    class NoMethod:
        name = "nomethod"

    pairs = list(iter_extension_permissions([FakeExt, NoMethod]))
    assert len(pairs) == 1
    assert pairs[0][1].name == "fake.thing.read"


def test_validate_uniqueness_no_op_when_clean():
    reg = PermissionRegistry()
    reg.register(
        [PermissionDef(name="p.r.a", description="d")], source="ext1"
    )
    # Identical duplicate accepted -> validate_uniqueness must still pass.
    reg.register(
        [PermissionDef(name="p.r.a", description="d")], source="ext2"
    )
    reg.validate_uniqueness()  # no raise


def test_collect_extension_permissions_into():
    class FakeExt:
        name = "fake"

        @classmethod
        def get_permissions(cls):
            return [PermissionDef(name="fake.thing.read", description="r")]

    reg = PermissionRegistry()
    collect_extension_permissions_into(reg, [FakeExt])
    assert "fake.thing.read" in reg
    assert "fake" in reg.sources("fake.thing.read")


###############################################################################
# Parameterized tests for has_permission, expand_wildcard, implies graph
###############################################################################


class TestHasPermissionWithImplies:
    """Test the implies-graph expansion in has_permission."""

    @pytest.fixture
    def registry(self):
        reg = PermissionRegistry()
        reg.register([
            PermissionDef(name="admin", description="admin", implies=["read", "write"]),
            PermissionDef(name="read", description="read"),
            PermissionDef(name="write", description="write", implies=["read"]),
            PermissionDef(name="delete", description="delete"),
        ], source="core")
        return reg

    @pytest.mark.parametrize(
        "perm, grants, expected",
        [
            ("read", {"read"}, True),
            ("read", {"admin"}, True),
            ("write", {"admin"}, True),
            ("read", {"write"}, True),
            ("delete", {"admin"}, False),
            ("delete", {"delete"}, True),
            ("read", set(), False),
        ],
        ids=["direct", "implied-by-admin", "write-implied-by-admin",
             "read-implied-by-write", "delete-not-implied", "delete-direct", "empty-grants"],
    )
    def test_role_grant_expansion(self, registry, perm, grants, expected):
        assert has_permission(perm, grants, registry=registry) == expected

    @pytest.mark.parametrize(
        "perm, grants, scopes, expected",
        [
            ("read", {"admin"}, {"read"}, True),
            ("read", {"admin"}, {"write"}, True),
            ("read", {"admin"}, {"delete"}, False),
            ("write", {"admin"}, {"read"}, False),
        ],
        ids=["scope-intersect-yes", "scope-implies-read", "scope-no-intersect", "scope-narrows"],
    )
    def test_token_scope_intersection(self, registry, perm, grants, scopes, expected):
        assert has_permission(perm, grants, token_scopes=scopes, registry=registry) == expected

    def test_no_registry_direct_membership(self):
        assert has_permission("x", {"x", "y"}) is True
        assert has_permission("z", {"x", "y"}) is False

    def test_no_registry_with_scopes(self):
        assert has_permission("x", {"x", "y"}, token_scopes={"x"}) is True
        assert has_permission("x", {"x", "y"}, token_scopes={"y"}) is False


class TestExpandWildcard:
    @pytest.fixture
    def registry(self):
        reg = PermissionRegistry()
        reg.register([
            PermissionDef(name="pay.sub", description="base"),
            PermissionDef(name="pay.sub.read", description="read"),
            PermissionDef(name="pay.sub.write", description="write"),
            PermissionDef(name="pay.other", description="other"),
            PermissionDef(name="pay.sub.admin", description="admin", system_only=True),
            PermissionDef(name="pay.sub.internal", description="not grantable", user_grantable=False),
        ], source="ext")
        return reg

    def test_wildcard_expands_prefix(self, registry):
        result = expand_wildcard("pay.sub.*", registry)
        assert "pay.sub" in result
        assert "pay.sub.read" in result
        assert "pay.sub.write" in result

    def test_wildcard_excludes_system_only(self, registry):
        result = expand_wildcard("pay.sub.*", registry)
        assert "pay.sub.admin" not in result

    def test_wildcard_excludes_non_grantable(self, registry):
        result = expand_wildcard("pay.sub.*", registry)
        assert "pay.sub.internal" not in result

    def test_wildcard_does_not_match_sibling(self, registry):
        result = expand_wildcard("pay.sub.*", registry)
        assert "pay.other" not in result

    def test_non_wildcard_returns_singleton(self, registry):
        result = expand_wildcard("pay.sub.read", registry)
        assert result == {"pay.sub.read"}

    def test_non_wildcard_unknown_returns_empty(self, registry):
        result = expand_wildcard("unknown.perm", registry)
        assert result == set()


class TestValidateUniqueness:
    def test_no_collision_on_identical_registrations(self):
        reg = PermissionRegistry()
        p = PermissionDef(name="x", description="x")
        reg.register([p], source="a")
        reg.register([p], source="b")
        reg.validate_uniqueness()
