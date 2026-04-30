"""
Item 36 — Data residency and regional provider pools (framework primitives).

This module ships only the *framework-side* types that the unmerged residency
extension consumes. Concretely:

  * `ResidencyJurisdiction` — opaque string newtype for legal/policy umbrellas
    such as ``"EU"`` or ``"HEALTHCARE_HIPAA"``. Free-form: the framework does
    not enumerate jurisdictions.
  * `ResidencyRegion` — opaque string newtype for physical placements such as
    ``"eu-west-1"`` or ``"us-east-1"``. Free-form for the same reason.
  * `JurisdictionMapping` — frozen dataclass associating a jurisdiction with
    the set of regions that satisfy it.
  * `JurisdictionRegistry` — process-global registry that operators populate
    at startup from configuration. Provides ``register`` /
    ``regions_for`` / ``is_in_jurisdiction`` / ``list_jurisdictions`` /
    ``clear`` (test-only) classmethods.
  * `NoInJurisdictionProviderError` — typed exception (subclass of
    ``HTTPException(status_code=400)``) carrying ``requester_id``,
    ``ability``, and ``jurisdiction`` for the unmerged extension to raise
    when no provider in the rotation chain is placed in a region that maps
    to the caller's required jurisdiction.

Resolution / enforcement logic is **deliberately not implemented here**.
Item 36 ships only primitives; the unmerged residency extension supplies the
policy that consults `ProviderInstanceModel.region` against this registry
during `RotationManager.rotate`. Keeping this module logic-free is the
contract that lets the extension land cleanly on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, FrozenSet, Iterable, List, Optional

from fastapi import HTTPException


class ResidencyJurisdiction(str):
    """Legal/policy umbrella (EU, US, HEALTHCARE_HIPAA). Free-form.

    Subclassing ``str`` keeps the value transparent to JSON / OpenAPI /
    GraphQL while giving call-sites a typed handle for static analysis.
    """


class ResidencyRegion(str):
    """Physical placement (eu-west-1, us-east-1). Free-form.

    Subclassing ``str`` for the same reason as `ResidencyJurisdiction`.
    """


@dataclass(frozen=True)
class JurisdictionMapping:
    """Frozen association of one jurisdiction with the set of regions that
    satisfy it. Used as the value type when operators describe their
    residency configuration declaratively (e.g. in a YAML / TOML file
    consumed at startup)."""

    jurisdiction: str  # e.g. "EU"
    regions: FrozenSet[str]  # e.g. {"eu-west-1", "eu-central-1"}


class JurisdictionRegistry:
    """Process-global registry that maps jurisdictions -> regions.

    Operators populate this at startup from configuration; the residency
    extension consults it when resolving a provider chain to filter
    instances whose ``region`` does not satisfy the caller's required
    jurisdiction. The framework itself never reads the registry — leaving
    enforcement entirely to the extension is the Item 36 contract.
    """

    _mappings: ClassVar[Dict[str, FrozenSet[str]]] = {}

    @classmethod
    def register(cls, jurisdiction: str, regions: Iterable[str]) -> None:
        """Register or overwrite the region set for ``jurisdiction``."""
        cls._mappings[jurisdiction] = frozenset(regions)

    @classmethod
    def regions_for(cls, jurisdiction: str) -> FrozenSet[str]:
        """Return the regions registered for ``jurisdiction`` (empty
        frozenset if the jurisdiction is unknown)."""
        return cls._mappings.get(jurisdiction, frozenset())

    @classmethod
    def is_in_jurisdiction(cls, region: str, jurisdiction: str) -> bool:
        """Return ``True`` iff ``region`` is registered as part of
        ``jurisdiction``."""
        return region in cls.regions_for(jurisdiction)

    @classmethod
    def list_jurisdictions(cls) -> List[str]:
        """Return registered jurisdictions in alphabetical order."""
        return sorted(cls._mappings.keys())

    @classmethod
    def clear(cls) -> None:
        """Test-only: reset state."""
        cls._mappings.clear()


class NoInJurisdictionProviderError(HTTPException):
    """Item 36 — raised by the unmerged residency extension during provider
    resolution when no provider instance in the chain is placed in a
    region that satisfies the caller's required jurisdiction.

    Subclasses ``HTTPException(status_code=400)`` so the FastAPI error
    handler renders a 4xx (this is a caller-context configuration
    problem, not a framework or upstream failure). The framework only
    provides the type; raising it is the residency extension's job.

    Attributes:
        requester_id: ID of the caller whose context required the
            jurisdiction (typically ``user_id`` or ``team_id``).
        ability: Name of the ability/operation being resolved when the
            chain came up empty (e.g. ``"charge.create"``).
        jurisdiction: The jurisdiction that filtered the chain to zero
            (e.g. ``"EU"``).
    """

    def __init__(
        self,
        *,
        requester_id: str,
        ability: str,
        jurisdiction: str,
        detail: Optional[Any] = None,
    ) -> None:
        self.requester_id = requester_id
        self.ability = ability
        self.jurisdiction = jurisdiction
        super().__init__(
            status_code=400,
            detail=detail
            or (
                f"No provider available in jurisdiction '{jurisdiction}' "
                f"for ability '{ability}' (requester={requester_id})."
            ),
        )


__all__ = [
    "ResidencyJurisdiction",
    "ResidencyRegion",
    "JurisdictionMapping",
    "JurisdictionRegistry",
    "NoInJurisdictionProviderError",
]
