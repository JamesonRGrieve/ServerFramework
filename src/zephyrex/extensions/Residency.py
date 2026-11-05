"""
Item 36 — Data residency and regional provider pools (framework primitives + filter).

This module ships:

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
    ``ability``, and ``jurisdiction`` for the residency extension to raise
    when no provider in the rotation chain is placed in a region that maps
    to the caller's required jurisdiction.
  * **Resolver registration** (`set_tenant_jurisdiction_resolver`,
    `get_tenant_jurisdiction_resolver`) — the seam through which the
    residency extension supplies the per-tenant policy lookup. The framework's
    `RotationManager.rotate` consults the registered resolver via
    :func:`filter_chain_by_jurisdiction` and skips filtering entirely when
    no resolver is present. This is what closes Item 36 inside the framework:
    a registration-only landing for the residency extension.
  * `filter_chain_by_jurisdiction` — the in-framework filter applied to a
    rotation chain. Walks instances, drops those whose region is outside the
    caller's required jurisdiction, raises `NoInJurisdictionProviderError`
    when the chain comes up empty under a non-`None` jurisdiction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Dict, FrozenSet, Iterable, List, Optional

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


# ---------------------------------------------------------------------------
# Tenant jurisdiction resolver registration (Item 36 framework seam)
# ---------------------------------------------------------------------------

# The framework cannot know how to derive a requester's jurisdiction —
# tenant policy lives in the residency extension. The extension calls
# ``set_tenant_jurisdiction_resolver(callback)`` at startup; the framework
# consults the callback during ``RotationManager.rotate``. When no resolver
# is registered (default deployment, no residency extension installed),
# ``filter_chain_by_jurisdiction`` is a no-op so behavior is unchanged.

_TenantJurisdictionResolver = Callable[[Any], Optional[str]]
"""``(requester) -> Optional[jurisdiction]``. ``None`` opts the request out
of jurisdiction filtering (no residency policy applies)."""

_resolver: Optional[_TenantJurisdictionResolver] = None


def set_tenant_jurisdiction_resolver(
    resolver: Optional[_TenantJurisdictionResolver],
) -> None:
    """Register the tenant-jurisdiction resolver.

    The residency extension calls this at startup so the framework's
    `RotationManager.rotate` can filter rotation chains. Pass ``None`` to
    unregister (test cleanup, hot-swap during install/uninstall).
    """
    global _resolver
    _resolver = resolver


def get_tenant_jurisdiction_resolver() -> Optional[_TenantJurisdictionResolver]:
    """Return the registered tenant-jurisdiction resolver, or ``None``."""
    return _resolver


def filter_chain_by_jurisdiction(
    chain: List[Any],
    requester: Any,
    *,
    ability: str,
    requester_id: Optional[str] = None,
) -> List[Any]:
    """Filter a rotation chain by the requester's tenant jurisdiction.

    Walks ``chain`` and returns only those instances whose underlying
    ``ProviderInstanceModel.region`` is registered as part of the
    requester's jurisdiction (per :class:`JurisdictionRegistry`).

    Behavior matrix:

    - **No resolver registered** → returns ``chain`` unchanged. Default
      deployment behavior is unaffected when no residency extension is
      installed.
    - **Resolver returns ``None``** → returns ``chain`` unchanged. The
      requester has no residency policy applied (pre-residency tenants,
      framework-internal calls, etc.).
    - **Resolver returns a jurisdiction** → returns instances whose
      ``region`` is mapped under that jurisdiction. If no instance
      qualifies, raises :class:`NoInJurisdictionProviderError`. An
      instance with ``region=None`` is treated as out-of-jurisdiction
      (the framework will not silently route to an unmarked instance
      under a residency policy).

    The chain entries may be ``RotationProviderInstanceModel`` instances
    (the wrapper returned by ``RotationManager._get_ordered_rotation_provider_instances``)
    or bare ``ProviderInstanceModel`` instances. The helper looks for
    ``provider_instance.region`` first; on absence, ``region`` directly.
    """
    resolver = get_tenant_jurisdiction_resolver()
    if resolver is None:
        return chain

    try:
        jurisdiction = resolver(requester)
    except Exception:  # pragma: no cover - defensive
        return chain

    if jurisdiction is None:
        return chain

    filtered: List[Any] = []
    for entry in chain:
        region = _extract_region(entry)
        if region is None:
            continue
        if JurisdictionRegistry.is_in_jurisdiction(region, jurisdiction):
            filtered.append(entry)

    if not filtered:
        raise NoInJurisdictionProviderError(
            requester_id=str(requester_id or _extract_requester_id(requester) or ""),
            ability=ability,
            jurisdiction=jurisdiction,
        )

    return filtered


def _extract_region(entry: Any) -> Optional[str]:
    """Pull the residency region off a chain entry.

    Rotation chains carry ``RotationProviderInstanceModel`` wrappers whose
    ``provider_instance`` attribute is the underlying ``ProviderInstanceModel``;
    bare ``ProviderInstanceModel`` instances expose ``region`` directly.
    """
    pi = getattr(entry, "provider_instance", None)
    if pi is not None:
        region = getattr(pi, "region", None)
        if region is not None:
            return str(region)
    region = getattr(entry, "region", None)
    return str(region) if region is not None else None


def _extract_requester_id(requester: Any) -> Optional[str]:
    rid = getattr(requester, "id", None)
    return str(rid) if rid is not None else None


__all__ = [
    "ResidencyJurisdiction",
    "ResidencyRegion",
    "JurisdictionMapping",
    "JurisdictionRegistry",
    "NoInJurisdictionProviderError",
    "set_tenant_jurisdiction_resolver",
    "get_tenant_jurisdiction_resolver",
    "filter_chain_by_jurisdiction",
]
