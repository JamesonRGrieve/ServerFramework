"""End-to-end tests for the Item 36 residency framework primitives.

Covers the public surface of `extensions.Residency`:
  * `JurisdictionRegistry` register / regions_for / is_in_jurisdiction /
    list_jurisdictions / clear semantics.
  * `NoInJurisdictionProviderError` attribute carriage and HTTPException
    inheritance.
  * `JurisdictionMapping` immutability.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from serverframework.extensions.Residency import (
    JurisdictionMapping,
    JurisdictionRegistry,
    NoInJurisdictionProviderError,
    ResidencyJurisdiction,
    ResidencyRegion,
    filter_chain_by_jurisdiction,
    get_tenant_jurisdiction_resolver,
    set_tenant_jurisdiction_resolver,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Clear registry state before each test so cross-test bleed is impossible.

    The registry is process-global (classvar dict); without this fixture the
    order tests run in could change their outcomes.
    """
    JurisdictionRegistry.clear()
    set_tenant_jurisdiction_resolver(None)
    yield
    JurisdictionRegistry.clear()
    set_tenant_jurisdiction_resolver(None)


class _FakeProviderInstance:
    def __init__(self, region=None):
        self.region = region


class _FakeRotationProviderInstance:
    """Mirrors the wrapper RotationManager passes to the filter."""

    def __init__(self, region=None):
        self.provider_instance = _FakeProviderInstance(region=region)


class _FakeRequester:
    def __init__(self, id_="u1"):
        self.id = id_


class TestJurisdictionRegistry:
    def test_register_and_regions_for(self):
        JurisdictionRegistry.register("EU", ["eu-west-1", "eu-central-1"])
        assert JurisdictionRegistry.regions_for("EU") == frozenset(
            {"eu-west-1", "eu-central-1"}
        )

    def test_register_overwrites_existing(self):
        JurisdictionRegistry.register("EU", ["eu-west-1"])
        JurisdictionRegistry.register("EU", ["eu-central-1", "eu-north-1"])
        assert JurisdictionRegistry.regions_for("EU") == frozenset(
            {"eu-central-1", "eu-north-1"}
        )

    def test_is_in_jurisdiction_true(self):
        JurisdictionRegistry.register("EU", ["eu-west-1", "eu-central-1"])
        assert JurisdictionRegistry.is_in_jurisdiction("eu-west-1", "EU") is True

    def test_is_in_jurisdiction_false_for_other_region(self):
        JurisdictionRegistry.register("EU", ["eu-west-1", "eu-central-1"])
        assert JurisdictionRegistry.is_in_jurisdiction("us-east-1", "EU") is False

    def test_is_in_jurisdiction_false_for_unknown_jurisdiction(self):
        assert (
            JurisdictionRegistry.is_in_jurisdiction("eu-west-1", "UNKNOWN") is False
        )

    def test_regions_for_unknown_returns_empty_frozenset(self):
        result = JurisdictionRegistry.regions_for("UNKNOWN")
        assert result == frozenset()
        assert isinstance(result, frozenset)

    def test_list_jurisdictions_alphabetical(self):
        JurisdictionRegistry.register("US", ["us-east-1"])
        JurisdictionRegistry.register("EU", ["eu-west-1"])
        JurisdictionRegistry.register("HEALTHCARE_HIPAA", ["us-east-1"])
        JurisdictionRegistry.register("APAC", ["ap-southeast-1"])
        assert JurisdictionRegistry.list_jurisdictions() == [
            "APAC",
            "EU",
            "HEALTHCARE_HIPAA",
            "US",
        ]

    def test_list_jurisdictions_empty_when_unset(self):
        assert JurisdictionRegistry.list_jurisdictions() == []

    def test_clear_resets_state(self):
        JurisdictionRegistry.register("EU", ["eu-west-1"])
        JurisdictionRegistry.register("US", ["us-east-1"])
        assert JurisdictionRegistry.list_jurisdictions() == ["EU", "US"]
        JurisdictionRegistry.clear()
        assert JurisdictionRegistry.list_jurisdictions() == []
        assert JurisdictionRegistry.regions_for("EU") == frozenset()


class TestJurisdictionMapping:
    def test_construction_and_attributes(self):
        m = JurisdictionMapping(
            jurisdiction="EU", regions=frozenset({"eu-west-1", "eu-central-1"})
        )
        assert m.jurisdiction == "EU"
        assert m.regions == frozenset({"eu-west-1", "eu-central-1"})

    def test_is_frozen(self):
        m = JurisdictionMapping(jurisdiction="EU", regions=frozenset({"eu-west-1"}))
        with pytest.raises(Exception):
            # frozen=True dataclass disallows attribute assignment
            m.jurisdiction = "US"  # type: ignore[misc]


class TestResidencyStringNewtypes:
    def test_jurisdiction_is_str_subclass(self):
        j = ResidencyJurisdiction("EU")
        assert isinstance(j, str)
        assert j == "EU"

    def test_region_is_str_subclass(self):
        r = ResidencyRegion("eu-west-1")
        assert isinstance(r, str)
        assert r == "eu-west-1"


class TestNoInJurisdictionProviderError:
    def test_carries_three_attributes(self):
        exc = NoInJurisdictionProviderError(
            requester_id="u1", ability="charge", jurisdiction="EU"
        )
        assert exc.requester_id == "u1"
        assert exc.ability == "charge"
        assert exc.jurisdiction == "EU"

    def test_is_http_exception_400(self):
        exc = NoInJurisdictionProviderError(
            requester_id="u1", ability="charge", jurisdiction="EU"
        )
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 400

    def test_default_detail_includes_context(self):
        exc = NoInJurisdictionProviderError(
            requester_id="u1", ability="charge", jurisdiction="EU"
        )
        assert "EU" in exc.detail
        assert "charge" in exc.detail
        assert "u1" in exc.detail

    def test_custom_detail_overrides_default(self):
        exc = NoInJurisdictionProviderError(
            requester_id="u1",
            ability="charge",
            jurisdiction="EU",
            detail="custom message",
        )
        assert exc.detail == "custom message"

    def test_can_be_raised_and_caught_as_http_exception(self):
        with pytest.raises(HTTPException) as ei:
            raise NoInJurisdictionProviderError(
                requester_id="u2", ability="send", jurisdiction="US"
            )
        assert isinstance(ei.value, NoInJurisdictionProviderError)
        assert ei.value.status_code == 400
        assert ei.value.requester_id == "u2"


# ---------------------------------------------------------------------------
# Item 36 — tenant jurisdiction resolver registration + chain filter
# ---------------------------------------------------------------------------


class TestTenantJurisdictionResolverRegistration:
    def test_default_unregistered(self):
        assert get_tenant_jurisdiction_resolver() is None

    def test_set_and_get_round_trip(self):
        def resolver(req):
            return "EU"

        set_tenant_jurisdiction_resolver(resolver)
        assert get_tenant_jurisdiction_resolver() is resolver

    def test_unset_via_none(self):
        set_tenant_jurisdiction_resolver(lambda r: "EU")
        set_tenant_jurisdiction_resolver(None)
        assert get_tenant_jurisdiction_resolver() is None


class TestFilterChainByJurisdiction:
    def test_no_resolver_returns_chain_unchanged(self):
        chain = [_FakeRotationProviderInstance("eu-west-1"),
                 _FakeRotationProviderInstance("us-east-1")]
        out = filter_chain_by_jurisdiction(chain, _FakeRequester(), ability="x")
        assert out == chain

    def test_resolver_returns_none_returns_chain_unchanged(self):
        set_tenant_jurisdiction_resolver(lambda r: None)
        chain = [_FakeRotationProviderInstance("us-east-1")]
        out = filter_chain_by_jurisdiction(chain, _FakeRequester(), ability="x")
        assert out == chain

    def test_filters_to_in_jurisdiction_instances(self):
        JurisdictionRegistry.register("EU", ["eu-west-1", "eu-central-1"])
        set_tenant_jurisdiction_resolver(lambda r: "EU")
        eu_west = _FakeRotationProviderInstance("eu-west-1")
        eu_central = _FakeRotationProviderInstance("eu-central-1")
        us_east = _FakeRotationProviderInstance("us-east-1")
        chain = [eu_west, us_east, eu_central]
        out = filter_chain_by_jurisdiction(chain, _FakeRequester(), ability="x")
        assert out == [eu_west, eu_central]

    def test_drops_instances_with_unregistered_region(self):
        JurisdictionRegistry.register("EU", ["eu-west-1"])
        set_tenant_jurisdiction_resolver(lambda r: "EU")
        eu_west = _FakeRotationProviderInstance("eu-west-1")
        chain = [eu_west, _FakeRotationProviderInstance("eu-elsewhere")]
        out = filter_chain_by_jurisdiction(chain, _FakeRequester(), ability="x")
        assert out == [eu_west]

    def test_drops_instances_with_no_region(self):
        """Item 36 acceptance: an instance without an explicit residency
        region must not silently route under a residency policy. The
        framework refuses to assume placement."""
        JurisdictionRegistry.register("EU", ["eu-west-1"])
        set_tenant_jurisdiction_resolver(lambda r: "EU")
        eu_west = _FakeRotationProviderInstance("eu-west-1")
        chain = [eu_west, _FakeRotationProviderInstance(None)]
        out = filter_chain_by_jurisdiction(chain, _FakeRequester(), ability="x")
        assert out == [eu_west]

    def test_raises_when_chain_empty_under_jurisdiction(self):
        JurisdictionRegistry.register("EU", ["eu-west-1"])
        set_tenant_jurisdiction_resolver(lambda r: "EU")
        chain = [_FakeRotationProviderInstance("us-east-1")]
        with pytest.raises(NoInJurisdictionProviderError) as ei:
            filter_chain_by_jurisdiction(
                chain, _FakeRequester("u1"), ability="charge.create"
            )
        assert ei.value.requester_id == "u1"
        assert ei.value.ability == "charge.create"
        assert ei.value.jurisdiction == "EU"

    def test_raises_with_explicit_requester_id(self):
        JurisdictionRegistry.register("EU", ["eu-west-1"])
        set_tenant_jurisdiction_resolver(lambda r: "EU")
        with pytest.raises(NoInJurisdictionProviderError) as ei:
            filter_chain_by_jurisdiction(
                [], _FakeRequester("ignored"), ability="x", requester_id="explicit"
            )
        assert ei.value.requester_id == "explicit"

    def test_works_with_bare_provider_instance_entries(self):
        """The filter accepts both rotation wrappers and bare provider
        instances — the helper looks for ``provider_instance.region``
        first, then ``region`` directly."""
        JurisdictionRegistry.register("EU", ["eu-west-1"])
        set_tenant_jurisdiction_resolver(lambda r: "EU")
        bare_eu = _FakeProviderInstance("eu-west-1")
        bare_us = _FakeProviderInstance("us-east-1")
        out = filter_chain_by_jurisdiction(
            [bare_eu, bare_us], _FakeRequester(), ability="x"
        )
        assert out == [bare_eu]

    def test_resolver_exception_is_safe(self):
        """A buggy resolver must not crash rotation; the filter degrades
        to no-op rather than failing the call."""

        def bad_resolver(req):
            raise RuntimeError("buggy")

        set_tenant_jurisdiction_resolver(bad_resolver)
        chain = [_FakeRotationProviderInstance("eu-west-1")]
        out = filter_chain_by_jurisdiction(chain, _FakeRequester(), ability="x")
        assert out == chain
