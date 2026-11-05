import inspect as _inspect
from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Hashable,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from zephyrex.lib.Logging import logger

# -- Item 46 helpers ----------------------------------------------------------


def _apply_federation_directives(type_class: type, directives: Tuple[Any, ...]) -> None:
    """Attach Apollo Federation v2 directive metadata to a type.

    Strawberry's federation support reads ``__strawberry_definition__.directives``
    when emitting SDL. We append rather than replace so the framework's own
    ``Sunset`` directive (Item 39) survives this call.
    """
    if not directives:
        return
    definition = getattr(type_class, "__strawberry_definition__", None)
    if definition is None:
        logger.warning(
            f"Cannot attach federation directives to {type_class.__name__}: "
            "type is not a Strawberry-decorated class."
        )
        return
    existing = list(getattr(definition, "directives", []) or [])
    for d in directives:
        existing.append(d)
    try:
        definition.directives = existing
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"Failed to attach federation directives to {type_class.__name__}: {e}"
        )


def _diff_signatures(
    previous: Tuple[Tuple[str, ...], ...],
    current: Tuple[Tuple[str, ...], ...],
) -> Tuple[Set[str], Set[str]]:
    """Return ``(added, removed)`` between two signature snapshots.

    Each signature is ``(query, mutation, subscription, types)``; entries are
    namespaced by kind so the diff is unambiguous.
    """
    labels = ("query", "mutation", "subscription", "type")

    def _flatten(sig: Tuple[Tuple[str, ...], ...]) -> Set[str]:
        flat: Set[str] = set()
        for label, names in zip(labels, sig):
            for name in names:
                flat.add(f"{label}:{name}")
        return flat

    prev = _flatten(previous)
    curr = _flatten(current)
    return curr - prev, prev - curr


# Removed FilterTypeGenerator - functionality moved to GraphQLManager


# Removed TypeGenerator - functionality moved to GraphQLManager


# Removed BatchResultGenerator - functionality moved to GraphQLManager


# Removed ResolverGenerator - functionality moved to GraphQLManager


# ---------------------------------------------------------------------------
# Item 46 -- composition contract for our own extensions
# ---------------------------------------------------------------------------
# Distinct from the external-federation work in ``Federation_GQL.py``: this
# block is how *our own* extensions contribute Query / Mutation / Subscription
# root fields, custom types, federation directives, and per-request DataLoaders
# into the merged schema. The CRUD-on-managers path is unchanged.
#
# Collision rule (mirrors ``CollisionDetection.FieldCollisionError``):
#   - Same root field name with byte-identical resolver + signature -> merge.
#   - Same root field name with non-identical signature -> startup-time
#     :class:`GraphQLCompositionCollisionError` naming both extensions.
#   - Same type name with non-identical body -> same error.
#   - Namespacing under the extension name is opt-in via ``namespace=True``.


class GraphQLCompositionCollisionError(Exception):
    """Two extensions registered conflicting GraphQL contributions."""

    def __init__(
        self,
        kind: str,
        name: str,
        extensions: Sequence[str],
        detail: Optional[str] = None,
    ) -> None:
        self.kind = kind
        self.name = name
        self.extensions = list(extensions)
        suffix = f" -- {detail}" if detail else ""
        super().__init__(
            f"GraphQL {kind} collision on '{name}': "
            f"contributed by {sorted(set(self.extensions))}. "
            "Either rename one contribution, opt in to namespacing "
            "(`namespace=True`), or ensure both contributions are byte-identical"
            f"{suffix}."
        )


class FieldKind(str, Enum):
    QUERY = "query"
    MUTATION = "mutation"
    SUBSCRIPTION = "subscription"


@dataclass(frozen=True)
class FederationDirective:
    """Apollo Federation v2 directive applied to a contributed type."""

    name: str
    args: Dict[str, Any] = _dc_field(default_factory=dict)


_ALLOWED_FED_DIRECTIVES: Set[str] = {
    "key",
    "external",
    "requires",
    "provides",
    "shareable",
    "inaccessible",
    "override",
    "tag",
}


@dataclass
class FieldContribution:
    """A root-field contribution from an extension."""

    extension_name: str
    kind: FieldKind
    name: str
    resolver: Callable[..., Any]
    return_type: Any
    args: Dict[str, Any] = _dc_field(default_factory=dict)
    description: Optional[str] = None
    namespace: bool = False
    priority: int = 50

    @property
    def emitted_name(self) -> str:
        if self.namespace:
            return f"{self.extension_name}_{self.name}"
        return self.name


@dataclass
class TypeContribution:
    """A custom type contribution (not backed by a BLL manager)."""

    extension_name: str
    type_class: type
    name: Optional[str] = None
    federation_directives: Tuple[FederationDirective, ...] = ()
    namespace: bool = False

    @property
    def emitted_name(self) -> str:
        base = self.name or self.type_class.__name__
        if self.namespace:
            return f"{self.extension_name.capitalize()}{base}"
        return base


DataLoaderBatchFn = Callable[[Sequence[Hashable]], Any]


@dataclass
class DataLoaderSpec:
    """Per-request DataLoader registration."""

    extension_name: str
    name: str
    batch_load_fn: DataLoaderBatchFn
    description: Optional[str] = None


RebuildCallback = Callable[[], None]


class GraphQLContributionRegistry:
    """Process-wide store of extension-contributed GraphQL surface."""

    def __init__(self) -> None:
        self._fields: Dict[FieldKind, Dict[str, List[FieldContribution]]] = {
            FieldKind.QUERY: {},
            FieldKind.MUTATION: {},
            FieldKind.SUBSCRIPTION: {},
        }
        self._types: Dict[str, List[TypeContribution]] = {}
        self._dataloaders: Dict[str, DataLoaderSpec] = {}
        self._rebuild_subscribers: List[RebuildCallback] = []
        self._suspended: bool = False

    def register_field(self, contribution: FieldContribution) -> None:
        bucket = self._fields[contribution.kind].setdefault(
            contribution.emitted_name, []
        )
        bucket.append(contribution)
        self._notify()

    def register_type(self, contribution: TypeContribution) -> None:
        for d in contribution.federation_directives:
            if d.name not in _ALLOWED_FED_DIRECTIVES:
                raise ValueError(
                    f"Unsupported federation directive '@{d.name}' on type "
                    f"'{contribution.emitted_name}'. Allowed: "
                    f"{sorted(_ALLOWED_FED_DIRECTIVES)}."
                )
        self._types.setdefault(contribution.emitted_name, []).append(contribution)
        self._notify()

    def register_dataloader(self, spec: DataLoaderSpec) -> None:
        existing = self._dataloaders.get(spec.name)
        if existing is not None and existing.batch_load_fn is not spec.batch_load_fn:
            raise GraphQLCompositionCollisionError(
                kind="dataloader",
                name=spec.name,
                extensions=[existing.extension_name, spec.extension_name],
                detail="DataLoader names are global; rename one or share the function.",
            )
        self._dataloaders[spec.name] = spec
        self._notify()

    def fields(self, kind: FieldKind) -> Dict[str, List[FieldContribution]]:
        return {k: list(v) for k, v in self._fields[kind].items()}

    def types(self) -> Dict[str, List[TypeContribution]]:
        return {k: list(v) for k, v in self._types.items()}

    def dataloaders(self) -> Dict[str, DataLoaderSpec]:
        return dict(self._dataloaders)

    def subscribe_rebuild(self, cb: RebuildCallback) -> None:
        self._rebuild_subscribers.append(cb)

    def unsubscribe_rebuild(self, cb: RebuildCallback) -> None:
        if cb in self._rebuild_subscribers:
            self._rebuild_subscribers.remove(cb)

    def _notify(self) -> None:
        if self._suspended:
            return
        for cb in list(self._rebuild_subscribers):
            try:
                cb()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"GQL rebuild subscriber failed: {e}")

    def suspend(self) -> "GraphQLContributionRegistry":
        """Defer rebuild notifications until :meth:`resume` is called."""
        self._suspended = True
        return self

    def resume(self) -> None:
        self._suspended = False
        self._notify()

    def resolve_fields(self, kind: FieldKind) -> Dict[str, FieldContribution]:
        winners: Dict[str, FieldContribution] = {}
        for emitted_name, contributions in self._fields[kind].items():
            if len(contributions) == 1:
                winners[emitted_name] = contributions[0]
                continue
            if all(_field_identical(c, contributions[0]) for c in contributions[1:]):
                winners[emitted_name] = contributions[0]
                continue
            raise GraphQLCompositionCollisionError(
                kind=f"{kind.value} field",
                name=emitted_name,
                extensions=[c.extension_name for c in contributions],
            )
        return winners

    def resolve_types(self) -> Dict[str, TypeContribution]:
        winners: Dict[str, TypeContribution] = {}
        for emitted_name, contributions in self._types.items():
            if len(contributions) == 1:
                winners[emitted_name] = contributions[0]
                continue
            if all(_type_identical(c, contributions[0]) for c in contributions[1:]):
                winners[emitted_name] = contributions[0]
                continue
            raise GraphQLCompositionCollisionError(
                kind="type",
                name=emitted_name,
                extensions=[c.extension_name for c in contributions],
            )
        return winners

    def reset(self) -> None:
        for kind in FieldKind:
            self._fields[kind].clear()
        self._types.clear()
        self._dataloaders.clear()
        self._rebuild_subscribers.clear()
        self._suspended = False


def _field_identical(a: FieldContribution, b: FieldContribution) -> bool:
    return (
        a.resolver is b.resolver
        and a.return_type == b.return_type
        and a.args == b.args
        and a.kind == b.kind
    )


def _type_identical(a: TypeContribution, b: TypeContribution) -> bool:
    return (
        a.type_class is b.type_class
        and a.federation_directives == b.federation_directives
    )


_GLOBAL_CONTRIBUTION_REGISTRY = GraphQLContributionRegistry()


def gql_contribution_registry() -> GraphQLContributionRegistry:
    """Process-wide contribution registry singleton."""
    return _GLOBAL_CONTRIBUTION_REGISTRY


def reset_gql_contribution_registry() -> None:
    """Test helper -- clear the process-wide contribution registry."""
    _GLOBAL_CONTRIBUTION_REGISTRY.reset()


def _extension_name_for_caller(explicit: Optional[str]) -> str:
    """Best-effort extension name from the caller's module path."""
    if explicit:
        return explicit
    frame = _inspect.currentframe()
    try:
        outer = frame.f_back if frame else None
        while outer is not None:
            mod = outer.f_globals.get("__name__", "")
            parts = mod.split(".")
            if "extensions" in parts:
                idx = parts.index("extensions")
                if idx + 1 < len(parts):
                    return parts[idx + 1]  # type: ignore[no-any-return]
            outer = outer.f_back
    finally:
        del frame
    return "core"


def _make_field_decorator(kind: FieldKind):
    def decorator(
        *,
        return_type: Any,
        name: Optional[str] = None,
        args: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        extension_name: Optional[str] = None,
        namespace: bool = False,
        priority: int = 50,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            field_name = name or fn.__name__
            ext = _extension_name_for_caller(extension_name)
            _GLOBAL_CONTRIBUTION_REGISTRY.register_field(
                FieldContribution(
                    extension_name=ext,
                    kind=kind,
                    name=field_name,
                    resolver=fn,
                    return_type=return_type,
                    args=dict(args or {}),
                    description=description,
                    namespace=namespace,
                    priority=priority,
                )
            )
            return fn

        return deco

    return decorator


gql_query = _make_field_decorator(FieldKind.QUERY)
gql_mutation = _make_field_decorator(FieldKind.MUTATION)
gql_subscription = _make_field_decorator(FieldKind.SUBSCRIPTION)


def gql_type(
    *,
    name: Optional[str] = None,
    federation_directives: Iterable[FederationDirective] = (),
    extension_name: Optional[str] = None,
    namespace: bool = False,
) -> Callable[[type], type]:
    """Register a Strawberry-decorated type as an extension contribution."""

    def deco(cls: type) -> type:
        ext = _extension_name_for_caller(extension_name)
        _GLOBAL_CONTRIBUTION_REGISTRY.register_type(
            TypeContribution(
                extension_name=ext,
                type_class=cls,
                name=name,
                federation_directives=tuple(federation_directives),
                namespace=namespace,
            )
        )
        return cls

    return deco


def register_dataloader(
    name: str,
    batch_load_fn: DataLoaderBatchFn,
    *,
    extension_name: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    """Register a per-request DataLoader.

    Resolvers retrieve it via ``info.context['dataloaders'][name]`` and call
    ``.load(key)`` to enqueue a fetch; the framework batches all loads in
    the request into a single ``batch_load_fn(keys)`` call.
    """
    ext = _extension_name_for_caller(extension_name)
    _GLOBAL_CONTRIBUTION_REGISTRY.register_dataloader(
        DataLoaderSpec(
            extension_name=ext,
            name=name,
            batch_load_fn=batch_load_fn,
            description=description,
        )
    )
