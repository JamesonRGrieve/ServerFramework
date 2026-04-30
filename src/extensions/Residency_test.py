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

from extensions.Residency import (
    JurisdictionMapping,
    JurisdictionRegistry,
    NoInJurisdictionProviderError,
    ResidencyJurisdiction,
    ResidencyRegion,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Clear registry state before each test so cross-test bleed is impossible.

    The registry is process-global (classvar dict); without this fixture the
    order tests run in could change their outcomes.
    """
    JurisdictionRegistry.clear()
    yield
    JurisdictionRegistry.clear()


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
