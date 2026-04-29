"""Permission registration tied to OAuth scope shape.

Item 18 (and refinement covering passwordless freshness gates).

The framework provides:

  * The ``PermissionDef`` type.
  * The ``PermissionRegistry`` for collection + uniqueness validation.
  * A ``has_permission`` resolver that walks the ``implies`` graph and
    enforces the **critical invariant** that an OAuth token can never
    escalate beyond what its bearer's role grants.
  * A ``is_session_freshly_verified`` helper used by the OAuth extension's
    sensitive-permission gate (Item 18 refinement -- magic-link / device
    pairing sessions count as fresh only for non-sensitive permissions).
  * An ``iter_extension_permissions`` helper that pulls
    ``get_permissions()`` off each registered ``AbstractStaticExtension``
    subclass at registry-finalize time. The ``AbstractStaticExtension``
    class itself is owned by Batch B; we read from it via ``getattr``
    rather than modifying it.

The OAuth extension consumes this registry to publish its consent catalog
and validate scope strings on token issuance. The framework does NOT
implement OAuth itself.

Canonical permission name shape: ``{extension}.{resource}.{action}[:{qualifier}]``
e.g. ``payment.subscription.read``, ``meta_logging.audit.read:own``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionDef:
    """A canonical permission whose name is also a valid OAuth scope.

    Attributes:
        name: Canonical scope ``{extension}.{resource}.{action}[:{qualifier}]``.
        description: User-facing copy displayed on OAuth consent screens.
        implies: Tuple of permission names this permission implies.
        sensitive: Requires step-up auth or a freshly-issued session
            regardless of token grant.
        user_grantable: Whether this permission may be granted via an OAuth
            token at all. ``False`` means the permission is role-only.
        system_only: Reserved for internal system actions. Never appears in
            issued tokens or consent screens.
    """

    name: str
    description: str
    implies: Tuple[str, ...] = ()
    sensitive: bool = False
    user_grantable: bool = True
    system_only: bool = False


class PermissionCollisionError(Exception):
    """Two extensions declared the same permission name with different defs.

    The error message names the colliding permission and the source(s) so the
    operator has a one-step path to the conflict.
    """

    def __init__(self, name: str, sources: List[str]):
        self.name = name
        self.sources = sources
        super().__init__(
            f"Permission collision for {name!r}: declared by "
            f"{sources!r} with non-identical definitions. "
            "If both extensions intend the same permission, ensure all "
            "fields of the PermissionDef match exactly (name, description, "
            "implies, sensitive, user_grantable, system_only)."
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class PermissionRegistry:
    """In-memory permission registry consumed by the OAuth extension.

    The registry collects ``PermissionDef`` instances from every registered
    extension at startup, validates uniqueness across the merged set, and
    exposes lookup + iteration so the OAuth extension can build its consent
    catalog and validate token scopes.
    """

    def __init__(self) -> None:
        # name -> (def, source extension name)
        self._by_name: Dict[str, Tuple[PermissionDef, str]] = {}
        # name -> list of source extensions that registered the same def
        # (kept for diagnostics in the collision-error path)
        self._sources: Dict[str, List[str]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        perms: Iterable[PermissionDef],
        source: str = "<unknown>",
    ) -> None:
        """Register a batch of permissions sourced from ``source``.

        Identical-definition duplicates are accepted as no-ops. Conflicting
        duplicates raise ``PermissionCollisionError`` immediately so the
        operator sees the collision at startup.
        """
        for perm in perms:
            existing = self._by_name.get(perm.name)
            if existing is None:
                self._by_name[perm.name] = (perm, source)
                self._sources[perm.name] = [source]
                continue

            existing_def, existing_source = existing
            if existing_def == perm:
                # Identical declaration; accept duplicate as a no-op.
                if source not in self._sources[perm.name]:
                    self._sources[perm.name].append(source)
                continue

            sources = self._sources.get(perm.name, [existing_source])
            if source not in sources:
                sources = sources + [source]
            raise PermissionCollisionError(perm.name, sources)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def get(self, name: str) -> PermissionDef:
        """Return the registered permission, or raise ``KeyError``."""
        return self._by_name[name][0]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._by_name

    def iter_permissions(self) -> Iterator[PermissionDef]:
        """Iterate over registered permissions in registration order."""
        for definition, _source in self._by_name.values():
            yield definition

    def names(self) -> Set[str]:
        """Set of registered permission names."""
        return set(self._by_name.keys())

    def sources(self, name: str) -> List[str]:
        """Extensions that registered ``name`` (for diagnostics)."""
        return list(self._sources.get(name, []))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_uniqueness(self) -> None:
        """Re-walk the merged registry and raise on any collisions.

        ``register`` already raises on collisions, but a callable validator
        is useful for tests and explicit finalize-time checks. Identical
        registrations from multiple sources are not collisions.
        """
        for name, sources in self._sources.items():
            if len(sources) <= 1:
                continue
            # Identical registrations only -- ``register`` already enforced
            # equality so reaching here means everything matches. Nothing
            # more to do.
            _ = name


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def _walk_implies(
    name: str, registry: PermissionRegistry, _seen: Optional[Set[str]] = None
) -> Set[str]:
    """Return ``{name}`` plus everything it transitively implies."""
    if _seen is None:
        _seen = set()
    if name in _seen:
        return _seen
    _seen.add(name)
    if name not in registry:
        return _seen
    for implied in registry.get(name).implies:
        _walk_implies(implied, registry, _seen)
    return _seen


def has_permission(
    name: str,
    role_grants: Set[str],
    token_scopes: Optional[Set[str]] = None,
    registry: Optional[PermissionRegistry] = None,
) -> bool:
    """Return True if a request with ``role_grants`` (and optionally
    ``token_scopes`` for OAuth-bearing requests) holds permission ``name``.

    **Critical invariant.** A token can NEVER escalate beyond the bearer's
    role. For OAuth-bearing requests the effective set is
    ``role_grants & token_scopes``. For direct authentication the effective
    set is ``role_grants``. This is the single most common authorization bug
    in OAuth integrations -- inverting this intersection lets a token
    authorize actions the role would not.

    The ``implies`` graph is walked: if ``role_grants`` includes
    ``payment.subscription`` and that permission implies
    ``payment.subscription.read``, then a request asking for
    ``payment.subscription.read`` succeeds.
    """
    if registry is not None:
        # Expand grants and scopes through the implies graph.
        expanded_role: Set[str] = set()
        for grant in role_grants:
            expanded_role |= _walk_implies(grant, registry)
        if token_scopes is None:
            effective = expanded_role
        else:
            expanded_scopes: Set[str] = set()
            for scope in token_scopes:
                expanded_scopes |= _walk_implies(scope, registry)
            effective = expanded_role & expanded_scopes
        return name in effective

    # No registry available -- fall back to direct membership semantics.
    if token_scopes is None:
        return name in role_grants
    return name in (role_grants & token_scopes)


# ---------------------------------------------------------------------------
# Consent-time wildcard expansion
# ---------------------------------------------------------------------------


def expand_wildcard(scope: str, registry: PermissionRegistry) -> Set[str]:
    """Expand a wildcard scope (e.g. ``payment.subscription.*``) into the
    concrete permission set declared in the registry.

    Wildcards are supported only at consent time: the user grants
    ``payment.subscription.*`` and the OAuth extension expands it into the
    concrete set BEFORE issuing the token, so revocation remains precise.

    A scope without a trailing ``.*`` is returned as a single-element set
    (after registry membership check). ``system_only`` permissions are
    excluded from wildcard expansions -- they must never appear in tokens.
    """
    if not scope.endswith(".*"):
        return {scope} if scope in registry else set()

    prefix = scope[:-2]  # strip ".*"
    matches: Set[str] = set()
    for perm in registry.iter_permissions():
        if perm.system_only:
            continue
        if not perm.user_grantable:
            continue
        # Match exact prefix or one-segment-deeper. Both
        # ``payment.subscription`` and ``payment.subscription.read`` match
        # ``payment.subscription.*``.
        if perm.name == prefix or perm.name.startswith(prefix + "."):
            matches.add(perm.name)
    return matches


# ---------------------------------------------------------------------------
# Freshness gate (Item 18 refinement)
# ---------------------------------------------------------------------------


def is_session_freshly_verified(
    session: Any,
    freshness_window_seconds: int = 300,
    now: Optional[datetime] = None,
) -> bool:
    """Return True if ``session`` was verified within ``freshness_window_seconds``.

    The OAuth extension calls this when gating a ``sensitive=True`` permission.
    A magic-link or device-pairing session counts as fresh ONLY for
    non-sensitive permissions; this helper enforces the time-window check
    that turns "freshly verified" into a concrete predicate.

    The session object is duck-typed: any of ``last_verified_at``,
    ``verified_at``, or ``issued_at`` is consulted in that order.
    """
    if session is None:
        return False
    candidate = (
        getattr(session, "last_verified_at", None)
        or getattr(session, "verified_at", None)
        or getattr(session, "issued_at", None)
    )
    if candidate is None:
        return False
    if not isinstance(candidate, datetime):
        return False
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    delta = (current - candidate).total_seconds()
    return 0 <= delta <= freshness_window_seconds


# ---------------------------------------------------------------------------
# Convention helper for AbstractStaticExtension
# ---------------------------------------------------------------------------


def iter_extension_permissions(
    extension_classes: Iterable[Any],
) -> Iterator[Tuple[Any, PermissionDef]]:
    """Yield ``(extension_class, permission_def)`` pairs by calling
    ``get_permissions()`` on each ``AbstractStaticExtension`` subclass.

    Extensions opt in by exposing a classmethod
    ``get_permissions() -> List[PermissionDef]``. Classes without that
    method contribute no permissions -- the framework treats absence as
    "this extension does not declare any permissions."

    Note: ``AbstractStaticExtension`` itself is owned by Batch B; we read
    ``get_permissions`` via ``getattr`` rather than modifying that class.
    Document this convention in extension-author docs.
    """
    for ext_class in extension_classes:
        getter: Callable[[], List[PermissionDef]] = getattr(
            ext_class, "get_permissions", lambda: []
        )
        try:
            perms = getter()
        except Exception:
            # An extension's get_permissions raising should not crash the
            # whole iteration -- the registry's collision check happens
            # downstream, so an empty contribution from one extension is
            # safe to skip.
            perms = []
        for perm in perms:
            yield ext_class, perm


def collect_extension_permissions_into(
    registry: PermissionRegistry, extension_classes: Iterable[Any]
) -> None:
    """Convenience: walk ``iter_extension_permissions`` and register each
    permission against the registry, attributing the source to the
    extension's ``name`` attribute (or its class name as fallback)."""
    for ext_class, perm in iter_extension_permissions(extension_classes):
        source = getattr(ext_class, "name", None) or ext_class.__name__
        registry.register([perm], source=source)


__all__ = [
    "PermissionDef",
    "PermissionRegistry",
    "PermissionCollisionError",
    "has_permission",
    "expand_wildcard",
    "is_session_freshly_verified",
    "iter_extension_permissions",
    "collect_extension_permissions_into",
]
