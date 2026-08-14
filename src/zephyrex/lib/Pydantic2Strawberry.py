import asyncio
import inspect as _inspect
import json
import sys
from dataclasses import dataclass, field as _dc_field
from datetime import date, datetime
from enum import Enum, IntEnum
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    Hashable,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
)

from types import ModuleType

import strawberry
import stringcase
from broadcaster import Broadcast
from pydantic import BaseModel
from strawberry.schema_directive import Location
from strawberry.types import Info

from zephyrex.lib.AbstractPydantic2 import (
    TypeIntrospector,
    CacheManager,
    RelationshipAnalyzer,
    ErrorHandlerMixin,
)
from zephyrex.lib.Environment import inflection
from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic import ModelRegistry
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager


def enum_serializer(value: Any) -> str:
    """Serialize enum values to their string representation"""
    if hasattr(value, "name"):
        return value.name  # type: ignore[no-any-return]
    elif hasattr(value, "value"):
        return value.value  # type: ignore[no-any-return]
    return str(value)


# Configure GraphQL scalar types
@strawberry.scalar(
    description="DateTime scalar",
    serialize=lambda v: v.isoformat() if v else None,
    parse_value=lambda v: datetime.fromisoformat(v) if v else None,
)
class DateTimeScalar:
    pass


@strawberry.scalar(
    description="Date scalar",
    serialize=lambda v: v.isoformat() if v else None,
    parse_value=lambda v: date.fromisoformat(v) if v else None,
)
class DateScalar:
    pass


# Define scalar types for complex data
@strawberry.scalar(
    description="Any JSON-serializable value",
    serialize=lambda v: (
        v
        if isinstance(v, str)
        else (
            enum_serializer(v)
            if hasattr(v, "name") or hasattr(v, "value")
            else json.dumps(v) if v is not None else None
        )
    ),
    parse_value=lambda v: (
        v if isinstance(v, str) else json.loads(v) if v is not None else None
    ),
)
class AnyScalar:
    pass


ANY_SCALAR = AnyScalar


@strawberry.scalar(
    description="JSON object",
    serialize=lambda v: json.dumps(v) if v is not None else None,
    parse_value=lambda v: json.loads(v) if v is not None else None,
)
class DictScalar:
    pass


DICT_SCALAR = DictScalar


@strawberry.scalar(
    description="JSON array",
    serialize=lambda v: json.dumps(v) if v is not None else None,
    parse_value=lambda v: json.loads(v) if v is not None else None,
)
class ListScalar:
    pass


LIST_SCALAR = ListScalar

# Remove generic type - not needed

# Map Python types to GraphQL scalar types
TYPE_MAPPING = {
    str: strawberry.scalar(
        str,
        description="String value",
        serialize=lambda v: v if v is not None else None,
        parse_value=lambda v: v if v is not None else None,
    ),
    int: strawberry.scalar(int, description="Integer value"),
    float: strawberry.scalar(float, description="Float value"),
    bool: strawberry.scalar(bool, description="Boolean value"),
    datetime: DateTimeScalar,
    date: DateScalar,
    dict: DICT_SCALAR,
    list: LIST_SCALAR,
    Any: ANY_SCALAR,
}


# Create a shared type introspector instance
_type_introspector: TypeIntrospector = TypeIntrospector()


def convert_field_name(
    field_name: Optional[str], use_camelcase: bool = True
) -> Optional[str]:
    """Convert field names to camelCase."""
    if field_name is None:
        return None
    if field_name in ["id", "__typename"]:
        return field_name
    return stringcase.camelcase(field_name)  # type: ignore[no-any-return]


@dataclass
class ModelInfo:
    """Information about a model and its relationships"""

    model_class: Type[BaseModel]
    ref_model_class: Type[BaseModel]
    network_model_class: Type[BaseModel]
    manager_class: Type[AbstractBLLManager]
    gql_type: Optional[Type] = None
    plural_name: str = ""
    singular_name: str = ""


# -- Item 39 — per-resource versioning / deprecation in GraphQL ---------------


@strawberry.schema_directive(locations=[Location.FIELD_DEFINITION])
class Sunset:
    """Custom GraphQL ``@sunset(date: ...)`` directive.

    Attached to fields whose source ``RouterMixin`` manager declares
    ``sunset_in``. The companion ``@deprecated`` directive is the built-in
    one (driven by Strawberry's ``deprecation_reason`` argument), so we
    only need a custom directive for the sunset half of the contract.
    """

    date: str


def _versioning_metadata_for(manager_class: Any) -> Tuple[Optional[str], Optional[str]]:
    """Read ``deprecated_in`` / ``sunset_in`` from a manager class.

    Returns ``(deprecated_in, sunset_in)``. Either or both may be ``None``.
    Tolerates managers that aren't ``RouterMixin``-tagged (returns ``(None, None)``).
    """
    if manager_class is None:
        return None, None
    return (
        getattr(manager_class, "deprecated_in", None),
        getattr(manager_class, "sunset_in", None),
    )


def _build_field_kwargs(manager_class: Any) -> Dict[str, Any]:
    """Build the kwargs forwarded to ``strawberry.field`` for a manager.

    When ``deprecated_in`` is set, the field gains a ``deprecation_reason``
    that includes the date plus the sunset date (when also set). When
    ``sunset_in`` is set, the field also gains the custom ``Sunset``
    schema directive. Returns an empty dict when neither is set so the
    caller's existing ``strawberry.field(resolver)`` call is byte-identical.
    """
    deprecated_in, sunset_in = _versioning_metadata_for(manager_class)
    kwargs: Dict[str, Any] = {}
    if deprecated_in:
        if sunset_in:
            kwargs["deprecation_reason"] = (
                f"Deprecated in {deprecated_in}; sunset in {sunset_in}"
            )
        else:
            kwargs["deprecation_reason"] = f"Deprecated in {deprecated_in}"
    if sunset_in:
        kwargs["directives"] = [Sunset(date=sunset_in)]
    return kwargs


def _versioned_field(resolver: Any, manager_class: Any) -> Any:
    """Wrap ``strawberry.field`` so deprecation/sunset metadata is applied."""
    kwargs = _build_field_kwargs(manager_class)
    if not kwargs:
        return strawberry.field(resolver)
    return strawberry.field(resolver, **kwargs)


# -- Item 48 — GraphQL conversion of degradation sentinels --------------------


@strawberry.type(
    description=(
        "Item 48 — typed GraphQL surface for QUEUE_AND_RETRY rotation "
        "exhaustion. Equivalent to the REST 202 response."
    )
)
class QueuedForRetryGQL:
    """GraphQL projection of the QueuedForRetry sentinel."""

    status: str = "accepted"
    tracking_id: str = ""


@strawberry.type(
    description=(
        "Item 48 — typed GraphQL surface for SILENT_DROP rotation "
        "exhaustion. Carries the (provider, ability) pair so operators can "
        "correlate with the provider_silent_drop_total metric."
    )
)
class SilentDroppedGQL:
    """GraphQL projection of the SilentDropped sentinel."""

    status: str = "silent_dropped"
    provider: Optional[str] = None
    ability: Optional[str] = None


def render_degradation_sentinel_gql(result: Any) -> Optional[Any]:
    """Item 48 — convert a rotation degradation sentinel into a typed
    Strawberry GraphQL result.

    Returns the Strawberry type instance for ``QueuedForRetry`` /
    ``SilentDropped``, or ``None`` when ``result`` is neither sentinel so
    the caller can fall through to its regular response.

    Mirrors :func:`Pydantic2FastAPI._render_degradation_sentinel` on the
    GraphQL side: the schema author opts in by declaring the resolver's
    return type as a union of the payload type and these GQL types
    (typically via ``Annotated[Union[..., QueuedForRetryGQL, SilentDroppedGQL]]``)
    and calling this helper before returning.
    """
    try:
        from zephyrex.extensions.ExternalErrors import (
            QueuedForRetry,
            SilentDropped,
        )
    except Exception:  # pragma: no cover - defensive
        return None

    if isinstance(result, QueuedForRetry):
        return QueuedForRetryGQL(
            status=result.status,
            tracking_id=result.tracking_id,
        )
    if isinstance(result, SilentDropped):
        return SilentDroppedGQL(
            status="silent_dropped",
            provider=result.provider,
            ability=result.ability,
        )
    return None


def degradation_aware(resolver: Callable[..., Any]) -> Callable[..., Any]:
    """Item 48 — decorator that converts a resolver's degradation sentinel
    return values into typed Strawberry GraphQL results.

    Wraps both sync and async resolvers. The wrapped resolver signature is
    preserved so Strawberry's introspection still sees the original ``Info``
    parameter and any declared arguments. The resolver's declared return
    type should be a union of its happy-path payload, ``QueuedForRetryGQL``,
    and ``SilentDroppedGQL`` to surface the degradation arms in the SDL.
    """
    if asyncio.iscoroutinefunction(resolver):

        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            result = await resolver(*args, **kwargs)
            converted = render_degradation_sentinel_gql(result)
            return converted if converted is not None else result

        async_wrapper.__name__ = resolver.__name__
        async_wrapper.__qualname__ = resolver.__qualname__
        async_wrapper.__doc__ = resolver.__doc__
        async_wrapper.__wrapped__ = resolver  # type: ignore[attr-defined]
        return async_wrapper

    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        result = resolver(*args, **kwargs)
        converted = render_degradation_sentinel_gql(result)
        return converted if converted is not None else result

    sync_wrapper.__name__ = resolver.__name__
    sync_wrapper.__qualname__ = resolver.__qualname__
    sync_wrapper.__doc__ = resolver.__doc__
    sync_wrapper.__wrapped__ = resolver  # type: ignore[attr-defined]
    return sync_wrapper


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


class RequestDataLoader:
    """Minimal per-request DataLoader.

    ``load(key)`` returns an awaitable that resolves once the deferred batch
    fires. Batches fire on the next event-loop tick after the first ``load``,
    so all parallel resolutions of ``thing.related`` collapse into a single
    ``batch_load_fn(keys)`` call.
    """

    def __init__(self, batch_load_fn: DataLoaderBatchFn) -> None:
        self._batch_load_fn = batch_load_fn
        self._queue: List[Hashable] = []
        self._futures: Dict[Hashable, "asyncio.Future[Any]"] = {}
        self._scheduled: bool = False

    def load(self, key: Hashable) -> "asyncio.Future[Any]":
        loop = asyncio.get_event_loop()
        if key in self._futures:
            return self._futures[key]
        fut: "asyncio.Future[Any]" = loop.create_future()
        self._futures[key] = fut
        self._queue.append(key)
        if not self._scheduled:
            self._scheduled = True
            loop.call_soon(lambda: asyncio.ensure_future(self._fire()))
        return fut

    async def _fire(self) -> None:
        keys = list(self._queue)
        self._queue.clear()
        self._scheduled = False
        try:
            result = self._batch_load_fn(keys)
            if _inspect.isawaitable(result):
                result = await result
        except Exception as e:  # noqa: BLE001
            for key in keys:
                fut = self._futures.pop(key, None)
                if fut is not None and not fut.done():
                    fut.set_exception(e)
            return
        if not isinstance(result, (list, tuple)):
            err = TypeError(
                f"DataLoader batch_load_fn must return a sequence aligned with"
                f" keys; got {type(result).__name__}."
            )
            for key in keys:
                fut = self._futures.pop(key, None)
                if fut is not None and not fut.done():
                    fut.set_exception(err)
            return
        if len(result) != len(keys):
            err = ValueError(  # type: ignore[assignment]
                f"DataLoader batch_load_fn returned {len(result)} results for"
                f" {len(keys)} keys; lengths must match."
            )
            for key in keys:
                fut = self._futures.pop(key, None)
                if fut is not None and not fut.done():
                    fut.set_exception(err)
            return
        for key, value in zip(keys, result):
            fut = self._futures.pop(key, None)
            if fut is not None and not fut.done():
                fut.set_result(value)


def build_request_dataloaders(
    registry: Optional[GraphQLContributionRegistry] = None,
) -> Dict[str, RequestDataLoader]:
    """Build a fresh per-request DataLoader for every registered spec."""
    reg = registry or _GLOBAL_CONTRIBUTION_REGISTRY
    return {name: RequestDataLoader(spec.batch_load_fn) for name, spec in reg.dataloaders().items()}


class GraphQLManager(ErrorHandlerMixin):
    """Main GraphQL schema manager that generates schemas from ModelRegistry"""

    def __init__(
        self,
        model_registry: ModelRegistry,
        contribution_registry: Optional[GraphQLContributionRegistry] = None,
    ) -> None:
        """Initialize SchemaManager with ModelRegistry.

        ``contribution_registry`` defaults to the process-wide singleton from
        :mod:`zephyrex.lib.GraphQLContribution`. Item 46 -- extension-
        contributed Query/Mutation/Subscription roots, custom types,
        federation directives, and DataLoaders are merged into the schema
        on every :meth:`create_schema` call. The manager subscribes to
        registry mutations so a subsequent extension install/uninstall
        rebuilds the schema with a logged diff.
        """
        if not model_registry:
            raise ValueError("ModelRegistry instance is required")

        self.model_registry = model_registry
        self.broadcast = Broadcast("memory://")
        self._contributions: GraphQLContributionRegistry = (
            contribution_registry or gql_contribution_registry()
        )
        self._last_schema_signature: Optional[Tuple[Tuple[str, ...], ...]] = None
        self._contributions.subscribe_rebuild(self._on_contributions_changed)

        # Legacy generator references for backward compatibility with tests
        class MockGenerator:
            def _convert_filter_to_search_params(
                self, filter_obj: Optional[Any]
            ) -> Dict[str, Any]:
                return {} if filter_obj is None else {}

            def _extract_nested_data(self, data_dict: Dict[str, Any]) -> Dict[str, Any]:
                if not data_dict:
                    return {}
                # Extract nested fields (fields with dict/list values)
                nested_data: Dict[str, Any] = {}
                keys_to_remove: List[str] = []
                for key, value in data_dict.items():
                    if isinstance(value, (dict, list)):
                        nested_data[key] = value
                        keys_to_remove.append(key)
                # Remove nested fields from original dict
                for key in keys_to_remove:
                    del data_dict[key]
                return nested_data

        self.filter_generator = MockGenerator()
        self.type_generator = MockGenerator()
        self.resolver_generator = MockGenerator()
        self.batch_result_generator = MockGenerator()

        # Schema caches
        self._query_fields: Dict[str, Any] = {}
        self._mutation_fields: Dict[str, Any] = {}
        self._subscription_fields: Dict[str, Any] = {}

        # Type registry to ensure types are created only once
        self._type_registry: Dict[Type[BaseModel], Type] = (
            {}
        )  # model_class -> GraphQL type
        self._input_type_registry: Dict[Tuple[Type[BaseModel], str], Type] = (
            {}
        )  # (model_class, suffix) -> GraphQL input type

        # Add relationship tracking
        self._forward_relationships: Dict[
            Type[BaseModel], Dict[str, Type[BaseModel]]
        ] = {}  # model -> {field: target_model}
        self._reverse_relationships: Dict[
            Type[BaseModel], Dict[str, Tuple[Type[BaseModel], str]]
        ] = {}  # model -> {field: (source_model, source_field)}
        self._analyzed_models: Set[Type[BaseModel]] = set()

        # Track types currently being created to prevent infinite recursion
        self._types_being_created: Set[Type[BaseModel]] = set()

    def create_schema(self) -> strawberry.Schema:
        """Create complete GraphQL schema from ModelRegistry"""
        # Generate all types, queries, mutations, and subscriptions
        self._generate_all_components()

        # Item 46 -- merge extension-contributed roots, custom types, and
        # federation directives. Collisions raise here at build time.
        self._apply_contributions()

        # Create Query, Mutation, and Subscription types
        query_type = self._create_query_type()
        mutation_type = self._create_mutation_type()
        subscription_type = self._create_subscription_type()

        # In production, disable schema introspection so an unauthenticated
        # client can't enumerate every type/field and a depth-bomb attacker
        # can't iterate the catalogue cheaply. Routed through is_production
        # so APP_ENV / ENVIRONMENT cannot diverge (C-2).
        from zephyrex.lib.Environment import env as _env, is_production

        schema_extensions: List[Any] = []
        if is_production():
            try:
                from strawberry.extensions import DisableIntrospection

                schema_extensions.append(DisableIntrospection())
            except Exception:
                pass

        # Always enforce a query-depth limit. An attacker who nests 30+ levels
        # of selection sets can otherwise force quadratic-or-worse work in the
        # resolver layer. GQL_DEPTH default aligns with AppSettings (L-1).
        try:
            from strawberry.extensions import QueryDepthLimiter

            depth_raw = _env("GQL_DEPTH") or "10"
            try:
                max_depth = max(1, int(depth_raw))
            except (TypeError, ValueError):
                max_depth = 10
            schema_extensions.append(QueryDepthLimiter(max_depth=max_depth))
        except Exception as e:
            logger.warning(f"Could not install QueryDepthLimiter: {e}")

        schema = strawberry.Schema(
            query=query_type,
            mutation=mutation_type,
            subscription=subscription_type,
            extensions=schema_extensions,
        )

        self._last_schema_signature = self._snapshot_signature()
        return schema

    # -- Item 46 ------------------------------------------------------------

    def _apply_contributions(self) -> None:
        """Merge extension-contributed roots/types/directives into the schema.

        Walks the contribution registry, applies the three-stage collision
        rule, and folds the winners into ``_query_fields`` /
        ``_mutation_fields`` / ``_subscription_fields``. Custom types are
        registered as known type names so collision detection in
        ``_create_gql_type_from_model`` doesn't double-register the same name.
        """
        if not hasattr(self, "_global_type_names"):
            self._global_type_names: Dict[str, str] = {}

        for kind, bucket in (
            (FieldKind.QUERY, self._query_fields),
            (FieldKind.MUTATION, self._mutation_fields),
            (FieldKind.SUBSCRIPTION, self._subscription_fields),
        ):
            winners = self._contributions.resolve_fields(kind)
            for emitted_name, contribution in winners.items():
                if emitted_name in bucket:
                    logger.warning(
                        f"Extension contribution '{emitted_name}' overrides an"
                        f" auto-generated {kind.value} field; the extension"
                        f" version wins."
                    )
                bucket[emitted_name] = self._wrap_contribution(contribution)

        for emitted_name, contribution in self._contributions.resolve_types().items():  # type: ignore[assignment]
            self._global_type_names.setdefault(
                emitted_name,
                f"{contribution.type_class.__module__}.{contribution.type_class.__name__}",  # type: ignore[attr-defined]
            )
            if contribution.federation_directives:  # type: ignore[attr-defined]
                _apply_federation_directives(
                    contribution.type_class, contribution.federation_directives  # type: ignore[attr-defined]
                )

    def _wrap_contribution(self, contribution: FieldContribution) -> Any:
        """Wrap a contribution's resolver as a Strawberry field/subscription.

        Resolvers receive the Strawberry ``Info`` plus any declared ``args``.
        Subscriptions are wrapped with ``strawberry.subscription``; queries
        and mutations with ``strawberry.field``.
        """
        resolver = contribution.resolver
        if contribution.kind == FieldKind.SUBSCRIPTION:
            return strawberry.subscription(resolver, description=contribution.description)
        return strawberry.field(resolver, description=contribution.description)

    def _on_contributions_changed(self) -> None:
        """Subscriber callback for registry mutations.

        Logs the diff. The actual schema rebuild is performed lazily on the
        next :meth:`create_schema` call (so a flurry of extension installs
        only triggers one rebuild). Callers that need an immediate rebuild
        (extension install/uninstall hot path) call :meth:`rebuild`.
        """
        new_sig = self._snapshot_signature()
        if self._last_schema_signature is None:
            return
        added, removed = _diff_signatures(self._last_schema_signature, new_sig)
        if added or removed:
            logger.info(
                f"GraphQL contribution registry changed -- added: {sorted(added)},"
                f" removed: {sorted(removed)}. Schema will rebuild on next access."
            )

    def rebuild(self) -> strawberry.Schema:
        """Rebuild the merged schema and log the structural diff.

        Called by the extension install/uninstall path (Item 20) when the
        operator wants the new contributions visible immediately rather than
        on the next scheduled rebuild.
        """
        previous = self._last_schema_signature
        # Reset the per-call buckets so a rebuild is a clean recomputation
        # rather than an additive overlay.
        self._query_fields.clear()
        self._mutation_fields.clear()
        self._subscription_fields.clear()
        schema = self.create_schema()
        if previous is not None:
            added, removed = _diff_signatures(previous, self._last_schema_signature or ())
            if added or removed:
                logger.info(
                    f"GraphQL schema rebuilt -- added: {sorted(added)},"
                    f" removed: {sorted(removed)}."
                )
        return schema

    def _snapshot_signature(self) -> Tuple[Tuple[str, ...], ...]:
        """Return a structural signature for diffing across rebuilds."""
        return (
            tuple(sorted(self._query_fields.keys())),
            tuple(sorted(self._mutation_fields.keys())),
            tuple(sorted(self._subscription_fields.keys())),
            tuple(sorted(getattr(self, "_global_type_names", {}).keys())),
        )

    def build_request_context(self, base_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build the per-request GraphQL context with DataLoaders attached.

        FastAPI/Strawberry callers pass the result as ``context_getter`` so
        every resolver finds ``info.context['dataloaders']``.
        """
        ctx: Dict[str, Any] = dict(base_context or {})
        ctx["dataloaders"] = build_request_dataloaders(self._contributions)
        return ctx

    def _generate_all_components(self) -> None:
        """Generate all GraphQL components from models"""
        # Use model_relationships from the registry
        if (
            not hasattr(self.model_registry, "model_relationships")
            or not self.model_registry.model_relationships
        ):
            logger.warning("No model relationships found in registry")
            return

        successful_models: List[str] = []
        failed_models: List[Tuple[str, str, str]] = []

        for relationship in self.model_registry.model_relationships:
            if len(relationship) == 3:
                model_class, ref_model_class, manager_class = relationship
            elif len(relationship) >= 4:
                model_class, ref_model_class, _network_model_class, manager_class = (
                    relationship[:4]
                )
            else:
                continue
            model_name = model_class.__name__ if model_class else "Unknown"
            try:
                self._generate_components_for_model(model_class, manager_class)
                self._register_custom_routes_for_manager(manager_class)
                successful_models.append(model_name)
            except Exception as e:
                module_name = model_class.__module__ if model_class else "unknown"
                failed_models.append((model_name, module_name, str(e)))
                logger.error(
                    f"Failed to generate GraphQL components for {model_name} "
                    f"(module: {module_name}): {str(e)}. "
                    f"Continuing with other models..."
                )

        # Log summary
        logger.info(
            f"GraphQL generation complete. "
            f"Successful: {len(successful_models)} models, "
            f"Failed: {len(failed_models)} models"
        )

        if failed_models:
            logger.error("Failed models:")
            for model_name, module_name, error in failed_models:
                logger.error(f"  - {model_name} ({module_name}): {error}")

    def _register_custom_routes_for_manager(self, manager_class: Any) -> None:
        """Item 40 GraphQL half: project ``@custom_route`` methods into the
        contribution registry so they appear in the merged Query/Mutation.

        Defensive — any failure during registration is logged but never
        breaks CRUD generation, mirroring the REST-side hook.
        """
        try:
            from zephyrex.lib.CustomRoute import (
                register_custom_routes_to_graphql,
            )

            register_custom_routes_to_graphql(
                manager_class,
                contribution_registry=self._contributions,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                f"Item 40 GraphQL custom-route registration skipped for "
                f"{getattr(manager_class, '__name__', manager_class)}: {exc}"
            )

    def _generate_components_for_model(
        self, model_class: Type[BaseModel], manager_class: Type[AbstractBLLManager]
    ) -> None:
        """Generate all components for a specific model class"""
        if not model_class or not manager_class:
            return

        # Skip extension models - they enhance existing types, don't create new ones
        if (
            hasattr(model_class, "_is_extension_model")
            and model_class._is_extension_model
        ):
            logger.debug(
                f"Skipping GraphQL generation for extension model {model_class.__name__} "  # type: ignore[union-attr]
                f"(extends {getattr(model_class, '_extension_target', 'Unknown').__name__})"
            )
            return

        model_name = model_class.__name__
        logger.debug(f"Generating GraphQL components for {model_name}")

        # Strip "Model" suffix for GraphQL field names and convert to camelCase
        base_name = model_name.removesuffix("Model")
        model_name_camel = convert_field_name(base_name)
        model_name_plural = inflection.plural(model_name_camel)

        try:
            # Apply model registry to get extension-enhanced version
            # Handle both core managers (BaseModel) and extension managers (Model)
            base_model: Optional[Type[BaseModel]] = None
            if hasattr(manager_class, "BaseModel"):
                base_model = manager_class.BaseModel
            elif hasattr(manager_class, "Model"):
                base_model = manager_class.Model
            else:
                logger.error(
                    f"Manager {manager_class.__name__} has neither BaseModel nor Model attribute"
                )
                return

            try:
                registry_model = self.model_registry.apply(base_model)
            except Exception as e:
                logger.warning(
                    f"Model {base_model.__name__} not found in registry during GraphQL generation "
                    f"({e.__class__.__name__}: {e})"
                )
                logger.debug(
                    f"Available models in registry: {[m.__name__ for m in self.model_registry.bound_models]}"
                )

                # Fallback to the original model to ensure GraphQL fields are generated
                registry_model = base_model

            # Generate GraphQL types using standardized error handling
            type_operations: Dict[str, Callable[[], Type]] = {
                "graphql_type": lambda: self._create_gql_type_from_model(
                    registry_model
                ),
                "create_input": lambda: self._create_input_type_from_model(
                    registry_model, "Create"
                ),
                "update_input": lambda: self._create_input_type_from_model(
                    registry_model, "Update"
                ),
                "filter_input": lambda: self._create_filter_type_from_model(
                    registry_model
                ),
            }

            type_results: Dict[str, Type] = self.batch_safe_operation(
                type_operations, strict=True
            )
            gql_type: Type = type_results["graphql_type"]
            create_input: Type = type_results["create_input"]
            update_input: Type = type_results["update_input"]
            filter_input: Type = type_results["filter_input"]

            # Generate resolvers using safe operations
            self.safe_operation(
                lambda: (
                    self._add_query_resolver(model_name_camel, gql_type, manager_class),  # type: ignore[func-returns-value, arg-type]
                    self._add_list_query_resolver(  # type: ignore[func-returns-value]
                        model_name_plural, gql_type, manager_class, filter_input
                    ),
                ),
                f"query resolvers for {model_name}",
                strict=True,
            )

            self.safe_operation(
                lambda: (
                    self._add_create_mutation_resolver(  # type: ignore[func-returns-value]
                        f"create{base_name}", gql_type, manager_class, create_input
                    ),
                    self._add_update_mutation_resolver(  # type: ignore[func-returns-value]
                        f"update{base_name}", gql_type, manager_class, update_input
                    ),
                    self._add_delete_mutation_resolver(  # type: ignore[func-returns-value]
                        f"delete{base_name}", manager_class
                    ),
                ),
                f"mutation resolvers for {model_name}",
                strict=True,
            )

            self.safe_operation(
                lambda: (
                    self._add_subscription_resolver(  # type: ignore[func-returns-value]
                        f"{model_name_camel}Created", model_name
                    ),
                    self._add_subscription_resolver(  # type: ignore[func-returns-value]
                        f"{model_name_camel}Updated", model_name
                    ),
                    self._add_subscription_resolver(  # type: ignore[func-returns-value]
                        f"{model_name_camel}Deleted", model_name
                    ),
                ),
                f"subscription resolvers for {model_name}",
                strict=True,
            )

            logger.log("SQL", f"Generated GraphQL components for {model_name}")

        except Exception as e:
            # Include module information for better debugging
            module_info = f" (module: {model_class.__module__})" if model_class else ""
            logger.error(
                f"Failed to generate components for {model_name}{module_info}: {e}"
            )
            # Re-raise to be caught by the outer try-catch for proper error isolation
            raise

    def _create_gql_type_from_model(self, model_class: Type[BaseModel]) -> Type:
        """Create a GraphQL type from a Pydantic model"""
        # Ensure we are using the registry-applied model (with extensions) when available
        try:
            if hasattr(self, "model_registry") and self.model_registry:
                apply_fn = getattr(self.model_registry, "apply", None)
                if callable(apply_fn):
                    try:
                        applied_model = apply_fn(model_class)
                        # Validate the returned object before using it
                        if isinstance(applied_model, type) and hasattr(
                            applied_model, "model_fields"
                        ):
                            model_class = applied_model
                    except Exception:
                        # If apply fails for any reason, continue with the original model
                        pass
        except Exception:
            # Defensive: if anything unexpected happens, ignore and proceed
            pass

        # Check if type already exists in registry
        if model_class in self._type_registry:
            return self._type_registry[model_class]

        # Check if we're already creating this type (prevent infinite recursion)
        if model_class in self._types_being_created:
            # Return a lazy type reference to break circular dependencies
            type_name = self._get_type_name_for_model(model_class)

            # Use strawberry.lazy to create a forward reference
            def get_type():
                # By the time this is called, the type should be in the registry
                if model_class in self._type_registry:
                    return self._type_registry[model_class]
                else:
                    # Fallback - create a minimal type
                    @strawberry.type
                    class MinimalType:
                        id: Optional[str] = None

                    MinimalType.__name__ = type_name
                    return MinimalType

            # Return a reference that will be resolved later
            # Instead of strawberry.lazy, return the actual type from registry
            return self._type_registry.get(model_class, ANY_SCALAR)

        # Mark this type as being created
        self._types_being_created.add(model_class)

        try:
            # Analyze relationships first
            self._analyze_model_relationships(model_class)

            # Debug log relationships for this model
            if model_class in self._reverse_relationships:
                logger.debug(
                    f"Reverse relationships for {model_class.__name__}: {list(self._reverse_relationships[model_class].keys())}"
                )

            base_name = model_class.__name__.removesuffix("Model")

            module_name = model_class.__module__ or __name__

            if module_name not in sys.modules:
                placeholder_module = ModuleType(module_name)
                sys.modules[module_name] = placeholder_module

            # Check if this is from an extension and add prefix to avoid collisions
            module_parts = module_name.split(".")
            if len(module_parts) > 1 and module_parts[0] == "extensions":
                # For extension models, prefix with the extension name
                extension_name = module_parts[1]
                type_name = f"{extension_name.title()}{base_name}Type"
            else:
                type_name = f"{base_name}Type"

            # Create field annotations for the class
            annotations: Dict[str, Type] = {}
            # Track field name mappings for resolvers (camelCase -> snake_case)
            field_name_mappings: Dict[str, str] = {}

            for field_name, field_info in model_class.model_fields.items():
                field_type = field_info.annotation

                # Debug logging for ActivityState field
                if "state" in field_name and "Activity" in model_class.__name__:
                    logger.debug(
                        f"Processing field {field_name} in {model_class.__name__}: "
                        f"field_type={field_type}, type={type(field_type)}, "
                        f"is_dict={isinstance(field_type, dict)}"
                    )

                gql_field_type = self._convert_python_type_to_gql(field_type)  # type: ignore[arg-type]
                # Convert snake_case field names to camelCase for GraphQL (GraphQL convention)
                gql_field_name = convert_field_name(field_name, use_camelcase=True)
                annotations[gql_field_name] = gql_field_type  # type: ignore[index]
                # Store mapping for resolver if names differ
                if gql_field_name != field_name:
                    field_name_mappings[gql_field_name] = field_name  # type: ignore[index]

            # Add reverse navigation properties
            if model_class in self._reverse_relationships:
                for reverse_field_name, (
                    source_model,
                    source_field,
                ) in self._reverse_relationships[model_class].items():
                    # Add annotation for the reverse field using the lazy type
                    source_gql_type: Type = self._get_or_create_type(source_model)
                    annotations[reverse_field_name] = List[source_gql_type]  # type: ignore[valid-type]

            # Always add at least one field to avoid empty type error
            if not annotations:
                annotations["_dummy"] = Optional[str]  # type: ignore[assignment]

            # Check if a type with this name already exists
            # This is a global registry to track all type names
            if not hasattr(self, "_global_type_names"):
                self._global_type_names: Dict[str, str] = {}  # type: ignore[no-redef]

            if type_name in self._global_type_names:
                existing_model = self._global_type_names[type_name]
                current_model = f"{model_class.__module__}.{model_class.__name__}"

                if existing_model == current_model:
                    logger.debug(
                        f"Type '{type_name}' already created for model {existing_model}, returning existing type"
                    )
                    existing_type = self._type_registry.get(model_class)
                    if existing_type is None:
                        for registered_model, gql_type in self._type_registry.items():
                            if (
                                registered_model.__module__ == model_class.__module__
                                and registered_model.__name__ == model_class.__name__
                            ):
                                existing_type = gql_type
                                self._type_registry[model_class] = gql_type
                                break
                    if existing_type is not None:
                        return existing_type
                    logger.warning(
                        "Stale GraphQL type registry entry detected for %s; regenerating",
                        current_model,
                    )
                    del self._global_type_names[type_name]
                else:
                    # Different models with same name - need to make it unique
                    logger.warning(
                        f"Type name collision: '{type_name}' already exists for {existing_model}. "
                        f"Current model: {current_model}"
                    )

                    # Generate a unique name by including module information
                    module_parts = model_class.__module__.split(".")
                    if len(module_parts) > 1:
                        if module_parts[0] == "extensions":
                            # For extensions, use extension name as prefix
                            unique_prefix = module_parts[1].title()
                        elif module_parts[0] == "logic":
                            # For logic models, use "Core" as prefix
                            unique_prefix = "Core"
                        else:
                            # For other modules, use the first part
                            unique_prefix = module_parts[0].title()

                        original_type_name = type_name
                        type_name = f"{unique_prefix}{type_name}"

                        # Check if the prefixed name also collides
                        counter: int = 1
                        while type_name in self._global_type_names:
                            existing = self._global_type_names[type_name]
                            if existing == current_model:
                                # Found our own type with this name, return it
                                logger.debug(
                                    f"Found existing type '{type_name}' for model {current_model}"
                                )
                                return self._type_registry[model_class]
                            # Still colliding, add a counter
                            type_name = f"{unique_prefix}{original_type_name}{counter}"
                            counter += 1

                        logger.info(f"Using unique type name: {type_name}")

            # Register the type name
            self._global_type_names[type_name] = (
                f"{model_class.__module__}.{model_class.__name__}"
            )

            # Create fields dict to hold strawberry fields with resolvers
            fields_dict: Dict[str, Any] = {"__annotations__": annotations}

            # Add field resolvers for camelCase -> snake_case mapping
            # Strawberry needs explicit resolvers when GraphQL field names differ from Python attribute names
            for gql_field_name, pydantic_field_name in field_name_mappings.items():
                # Create a resolver that maps the camelCase GraphQL field to the snake_case Pydantic attribute
                def make_resolver(py_field_name: str):
                    def resolver(root) -> Any:
                        # root is the Pydantic model instance
                        return getattr(root, py_field_name, None)

                    return resolver

                # Use strawberry.field with a resolver to map GraphQL field name to Python attribute
                fields_dict[gql_field_name] = strawberry.field(
                    resolver=make_resolver(pydantic_field_name)
                )

            # Add navigation resolver methods for reverse relationships
            if model_class in self._reverse_relationships:
                for reverse_field_name, (
                    source_model,
                    source_field,
                ) in self._reverse_relationships[model_class].items():
                    # Create a resolver method for this reverse relationship
                    resolver = self._create_reverse_navigation_resolver(
                        model_class, source_model, source_field, reverse_field_name
                    )
                    # Add the resolver as a method on the type using strawberry.field
                    # This creates a proper GraphQL field with the resolver
                    fields_dict[reverse_field_name] = strawberry.field(
                        resolver=resolver,
                        description=f"List of related {source_model.__name__} objects",
                    )

            # Create the type class with proper annotations and methods
            type_class = type(type_name, (), fields_dict)

            # Add module information to avoid name collisions
            # Store the module path in the type for debugging
            type_class.__module__ = module_name

            gql_type = strawberry.type(type_class)

            # Register the type
            self._type_registry[model_class] = gql_type

            logger.debug(
                f"Created GraphQL type '{type_name}' for model {model_class.__module__}.{model_class.__name__}"
            )

            return gql_type
        finally:
            # Remove from types being created
            self._types_being_created.discard(model_class)

    def _create_input_type_from_model(
        self, model_class: Type[BaseModel], suffix: str
    ) -> Type:
        """Create a GraphQL input type from a Pydantic model"""
        # Check if input type already exists in registry
        registry_key = (model_class, suffix)
        if registry_key in self._input_type_registry:
            return self._input_type_registry[registry_key]

        base_name = model_class.__name__.removesuffix("Model")

        # Check if this is from an extension and add prefix to avoid collisions
        module_parts = model_class.__module__.split(".")
        if len(module_parts) > 1 and module_parts[0] == "extensions":
            # For extension models, prefix with the extension name
            extension_name = module_parts[1]
            input_name = f"{extension_name.title()}{base_name}{suffix}Input"
        else:
            input_name = f"{base_name}{suffix}Input"

        # Check if model has nested Create/Update classes
        if suffix == "Create" and hasattr(model_class, "Create"):
            base_model = model_class.Create
        elif suffix == "Update" and hasattr(model_class, "Update"):
            base_model = model_class.Update
        else:
            base_model = model_class

        # Extract fields from model_fields (includes inherited fields from mixins)
        annotations: Dict[str, Type] = {}
        for field_name, field_info in base_model.model_fields.items():
            field_type = field_info.annotation

            # Debug logging for ActivityState field
            if "state" in field_name and "Activity" in base_model.__name__:
                logger.debug(
                    f"Processing input field {field_name} in {base_model.__name__}: "
                    f"field_type={field_type}, type={type(field_type)}, "
                    f"is_dict={isinstance(field_type, dict)}"
                )

            # Make ALL fields optional for GraphQL input types to avoid validation errors
            # This is a common pattern in GraphQL where mutations are more permissive
            if not self._is_already_optional(field_type):
                field_type = Optional[field_type]

            # Convert snake_case field names to camelCase for GraphQL input types
            gql_field_name = convert_field_name(field_name, use_camelcase=True)

            gql_field_type = self._convert_python_type_to_gql(field_type)
            annotations[gql_field_name] = gql_field_type  # type: ignore[index]

        # Always add at least one field to avoid empty input type error
        if not annotations:
            annotations["_dummy"] = Optional[str]  # type: ignore[assignment]

        # Create the input type class with proper annotations
        # Use strawberry.field with default=None to make all fields truly optional
        input_fields = {}
        for field_name, field_type in annotations.items():
            input_fields[field_name] = strawberry.field(
                default=None, description=f"Optional {field_name}"
            )

        # Create input class dynamically with all fields defaulting to None
        input_class = type(input_name, (), input_fields)
        input_class.__annotations__ = annotations

        input_type = strawberry.input(input_class)

        # Store in registry for future use
        self._input_type_registry[registry_key] = input_type

        return input_type

    def _create_filter_type_from_model(self, model_class: Type[BaseModel]) -> Type:
        """Create a filter input type for a model"""
        base_name = model_class.__name__.removesuffix("Model")

        # Check if this is from an extension and add prefix to avoid collisions
        module_parts = model_class.__module__.split(".")
        if len(module_parts) > 1 and module_parts[0] == "extensions":
            # For extension models, prefix with the extension name
            extension_name = module_parts[1]
            filter_name = f"{extension_name.title()}{base_name}FilterInput"
        else:
            filter_name = f"{base_name}FilterInput"

        # Create basic filter fields for string/numeric fields
        annotations: Dict[str, Type] = {}
        for field_name, field_info in model_class.model_fields.items():
            field_type = field_info.annotation
            if field_type == str:
                annotations[f"{field_name}_contains"] = Optional[str]  # type: ignore[assignment]
                annotations[f"{field_name}_equals"] = Optional[str]  # type: ignore[assignment]
            elif field_type in [int, float]:
                annotations[f"{field_name}_equals"] = Optional[field_type]  # type: ignore[assignment]
                annotations[f"{field_name}_gt"] = Optional[field_type]  # type: ignore[assignment]
                annotations[f"{field_name}_lt"] = Optional[field_type]  # type: ignore[assignment]

        # Always add at least one field to avoid empty input type error
        if not annotations:
            annotations["_dummy"] = Optional[str]  # type: ignore[assignment]

        filter_class = type(filter_name, (), {"__annotations__": annotations})
        return strawberry.input(filter_class)

    def _is_already_optional(self, python_type: Type) -> bool:
        """Check if a type is already Optional (Union with None)"""
        return _type_introspector.is_optional_type(python_type)  # type: ignore[no-any-return]

    def _convert_python_type_to_gql(self, python_type: Type) -> Type:
        """Convert Python type to GraphQL type"""
        try:
            # Handle Optional types
            if get_origin(python_type) is Union:
                args = get_args(python_type)
                if len(args) == 2 and type(None) in args:
                    inner_type = next(arg for arg in args if arg is not type(None))
                    return Optional[self._convert_python_type_to_gql(inner_type)]  # type: ignore[return-value]

            # Handle List types
            if get_origin(python_type) is list:
                args = get_args(python_type)
                return (
                    List[self._convert_python_type_to_gql(args[0])]  # type: ignore[misc, return-value]
                    if args
                    else List[str]
                )

            # Handle Dict types
            if get_origin(python_type) is dict:
                return DICT_SCALAR

            # Use type mapping for basic types
            if python_type in TYPE_MAPPING:
                return TYPE_MAPPING[python_type]

            # Handle non-type objects
            if not isinstance(python_type, type):
                return TYPE_MAPPING[str]

            # Handle Enum types
            if _type_introspector.is_enum_type(python_type):
                try:
                    # Check if it's an IntEnum
                    if (
                        hasattr(python_type, "__mro__")
                        and IntEnum in python_type.__mro__
                    ):
                        return TYPE_MAPPING[int]

                    # Handle string-based enums
                    if str in python_type.__bases__:
                        enum_values: Dict[str, str] = {
                            member.name: member.name
                            for member in python_type.__members__.values()
                        }
                        enum_name = python_type.__name__

                        # Add prefix for extension enums
                        if hasattr(python_type, "__module__") and isinstance(
                            python_type.__module__, str
                        ):
                            module_parts = python_type.__module__.split(".")
                            if (
                                len(module_parts) > 1
                                and module_parts[0] == "extensions"
                            ):
                                enum_name = f"{module_parts[1].title()}{enum_name}"

                        new_enum = type(enum_name, (Enum,), enum_values)
                        new_enum.__module__ = python_type.__module__
                        return strawberry.enum(new_enum)  # type: ignore[call-overload, no-any-return]

                    # Try direct strawberry conversion
                    return strawberry.enum(python_type)  # type: ignore[call-overload, no-any-return]
                except Exception:
                    return TYPE_MAPPING[str]

            # Handle constants classes and ProviderType classes
            if (
                hasattr(python_type, "values")
                and callable(getattr(python_type, "values"))
            ) or python_type.__name__.endswith("ProviderType"):
                return TYPE_MAPPING[str]

            # Handle Pydantic models (nested types)
            if hasattr(python_type, "__bases__") and any(
                base.__name__ == "BaseModel" for base in python_type.__mro__
            ):
                # Skip extension models
                if (
                    hasattr(python_type, "_is_extension_model")
                    and python_type._is_extension_model
                ):
                    return ANY_SCALAR

                # For nested Pydantic models, create a GraphQL type
                try:
                    return self._get_or_create_type(python_type)
                except Exception:
                    # Return fallback instead of strawberry.lazy
                    return self._type_registry.get(python_type, ANY_SCALAR)

            # Handle extension Type classes
            if (
                hasattr(python_type, "__module__")
                and isinstance(python_type.__module__, str)
                and (python_type.__module__.startswith("zephyrex.extensions.") or python_type.__module__.startswith("extensions."))
                and python_type.__name__.endswith("Type")
            ):
                return TYPE_MAPPING[str]

            # Default fallback
            return ANY_SCALAR

        except Exception as e:
            type_name = getattr(python_type, "__name__", str(python_type))
            module_name = getattr(python_type, "__module__", "unknown")
            logger.warning(
                f"Error converting type {module_name}.{type_name}: {str(e)}. Using ANY_SCALAR."
            )
            return ANY_SCALAR

    def _apply_field_acl(self, manager: Any, result: Any) -> Any:
        """Item 45 — apply field-level ACL to a GraphQL resolver result.

        Walks the manager → requester chain to resolve ``has_permission``
        and removes (or masks) restricted fields from the serialized
        payload before Strawberry hands it back to the client. Returns
        ``result`` unchanged when no permission resolver is present
        (framework-internal callers, system-key audit jobs) so the same
        resolver code paths work end-to-end with and without ABAC.

        The model class for the ACL lookup is derived from
        ``manager.BaseModel`` / ``manager.Model`` (the same source
        ``_generate_components_for_model`` consults). The helper lazy-
        imports the FieldACL primitives to keep the GraphQL stack
        importable even if the FieldACL module is absent in a stripped
        deployment build.
        """
        if result is None:
            return result
        try:
            from zephyrex.lib.FieldACL import apply_field_acl_to_response
            from zephyrex.lib.Pydantic2FastAPI import _resolve_has_permission
        except Exception:
            return result

        has_perm = _resolve_has_permission(manager)
        if has_perm is None:
            return result

        model_cls: Optional[Type[BaseModel]] = None
        if hasattr(manager, "BaseModel"):
            model_cls = manager.BaseModel
        elif hasattr(manager, "Model"):
            model_cls = manager.Model
        if model_cls is None:
            return result

        import os

        sentinel_mode = os.getenv("FIELD_ACL_SENTINEL")
        kwargs: Dict[str, Any] = {}
        if sentinel_mode:
            kwargs["sentinel_mode"] = sentinel_mode
        return apply_field_acl_to_response(result, model_cls, has_perm, **kwargs)

    def _add_query_resolver(
        self,
        field_name: str,
        return_type: Type,
        manager_class: Type[AbstractBLLManager],
    ) -> None:
        """Add a query resolver for getting a single item"""
        # Special handling for user queries - users can only query themselves
        if "User" in manager_class.__name__:

            async def user_resolver(info: Info, **kwargs: Optional[str]) -> return_type:  # type: ignore[valid-type]
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # For users, always query the requester (no ID parameter allowed)
                    result = manager.get(id=requester_id, include=None, fields=None)
                    return self._apply_field_acl(manager, result)  # type: ignore[no-any-return]
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    raise

            self._query_fields[field_name] = _versioned_field(
                user_resolver, manager_class
            )
        else:
            # Create resolver with only ID parameter to avoid unknown argument errors
            async def resolver(id: str, info: Info) -> return_type:  # type: ignore[valid-type]
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # Call manager.get with just the ID
                    result = manager.get(id=id, include=None, fields=None)
                    return self._apply_field_acl(manager, result)  # type: ignore[no-any-return]
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    raise

            self._query_fields[field_name] = _versioned_field(
                resolver, manager_class
            )

    def _add_list_query_resolver(
        self,
        field_name: str,
        return_type: Type,
        manager_class: Type[AbstractBLLManager],
        filter_type: Type,
    ) -> None:
        """Add a query resolver for listing items with filtering and pagination"""
        # Special handling for user list queries
        if "User" in manager_class.__name__:

            async def user_list_resolver(
                teamId: Optional[str] = None,
                limit: Optional[int] = 100,
                offset: Optional[int] = 0,
                info: Info = None,  # type: ignore[assignment]
                **kwargs: Optional[str],
            ) -> List[return_type]:  # type: ignore[valid-type]
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # If teamId is provided, return users in that team
                    if teamId:
                        # Convert camelCase teamId to snake_case team_id
                        filter_params = {"team_id": teamId}
                        for key, value in kwargs.items():
                            snake_key = stringcase.snakecase(key)
                            filter_params[snake_key] = value  # type: ignore[assignment]

                        result = manager.list(
                            offset=offset or 0,
                            limit=limit or 100,
                            include=None,
                            fields=None,
                            **filter_params,
                        )
                        return self._apply_field_acl(manager, result)  # type: ignore[no-any-return]
                    else:
                        # No teamId provided - return only the requester
                        user = manager.get(id=requester_id, include=None, fields=None)
                        if user is None:
                            return []
                        return [self._apply_field_acl(manager, user)]
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    raise

            self._query_fields[field_name] = _versioned_field(
                user_list_resolver, manager_class
            )
        else:

            async def resolver(
                filter: Optional[filter_type] = None,  # type: ignore[valid-type]
                limit: Optional[int] = 100,
                offset: Optional[int] = 0,
                info: Info = None,  # type: ignore[assignment]
            ) -> List[return_type]:  # type: ignore[valid-type]
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # Call manager.list with pagination support
                    result = manager.list(
                        offset=offset or 0,
                        limit=limit or 100,
                        include=None,
                        fields=None,
                    )
                    return self._apply_field_acl(manager, result)  # type: ignore[no-any-return]
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    raise

            self._query_fields[field_name] = _versioned_field(
                resolver, manager_class
            )

    def _add_create_mutation_resolver(
        self,
        field_name: str,
        return_type: Type,
        manager_class: Type[AbstractBLLManager],
        input_type: Type,
    ) -> None:
        """Add a mutation resolver for creating items"""

        async def resolver(input: input_type, info: Info) -> return_type:  # type: ignore[valid-type]
            try:
                context = self._get_context_from_info(info)
                requester_id = context.get("requester_id")
                if not requester_id:
                    raise Exception(
                        "Unable to authenticate user for GraphQL query - no requester_id found in context"
                    )
                manager = manager_class(
                    model_registry=self.model_registry, requester_id=requester_id
                )
                data = self._convert_input_to_dict(input)

                # Call manager.create with same signature as REST API
                # Special-case User creation which uses a register flow
                if "User" in manager_class.__name__:
                    # Use the static register method on the manager class to perform registration
                    try:
                        result = manager_class.register(
                            data, model_registry=self.model_registry
                        )
                    except TypeError:
                        # Fallback to pass kwargs style if the register signature expects named args
                        result = manager_class.register(
                            registration_data=data, model_registry=self.model_registry
                        )
                else:
                    result = manager.create(**data)

                # Broadcast subscription (convert to dict for JSON serialization)
                try:
                    if hasattr(result, "model_dump"):
                        result_data = result.model_dump()
                    elif hasattr(result, "dict"):
                        result_data = result.dict()
                    else:
                        result_data = str(result)

                    await self.broadcast.publish(
                        channel=f"{return_type.__name__.lower()}_created",
                        message=json.dumps({"action": "created", "data": result_data}),
                    )
                except Exception as e:
                    logger.log("SQL", f"Failed to broadcast create event: {e}")

                return self._apply_field_acl(manager, result)  # type: ignore[no-any-return]
            except Exception as e:
                logger.error(f"Error in {field_name} resolver: {e}")
                raise

        self._mutation_fields[field_name] = _versioned_field(
            resolver, manager_class
        )

    def _add_update_mutation_resolver(
        self,
        field_name: str,
        return_type: Type,
        manager_class: Type[AbstractBLLManager],
        input_type: Type,
    ) -> None:
        """Add a mutation resolver for updating items"""
        # Special handling for user update mutations - users can only update themselves
        if "User" in manager_class.__name__:

            async def user_update_resolver(
                input: input_type, info: Info  # type: ignore[valid-type]
            ) -> return_type:  # type: ignore[valid-type]
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )
                    logger.info(f"GraphQL update input: {input}")
                    logger.info(f"GraphQL update input type: {type(input)}")
                    logger.info(
                        f"GraphQL update input dict: {input.__dict__ if hasattr(input, '__dict__') else 'No __dict__'}"  # type: ignore[attr-defined]
                    )
                    data = self._convert_input_to_dict(input)

                    logger.info(f"GraphQL update data: {data}")

                    # For users, always update the requester (no ID parameter allowed)
                    result = manager.update(requester_id, **data)

                    # Broadcast subscription (convert to dict for JSON serialization)
                    try:
                        if hasattr(result, "model_dump"):
                            result_data = result.model_dump()
                        elif hasattr(result, "dict"):
                            result_data = result.dict()
                        else:
                            result_data = str(result)

                        await self.broadcast.publish(
                            channel=f"{return_type.__name__.lower()}_updated",
                            message=json.dumps(
                                {"action": "updated", "data": result_data}
                            ),
                        )
                    except Exception as e:
                        logger.log("SQL", f"Failed to broadcast update event: {e}")

                    return self._apply_field_acl(manager, result)  # type: ignore[no-any-return]
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    raise

            self._mutation_fields[field_name] = _versioned_field(
                user_update_resolver, manager_class
            )
        else:

            async def resolver(id: str, input: input_type, info: Info) -> return_type:  # type: ignore[valid-type]
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )
                    data = self._convert_input_to_dict(input)

                    # Call manager.update with same signature as REST API
                    result = manager.update(id, **data)

                    # Broadcast subscription (convert to dict for JSON serialization)
                    try:
                        if hasattr(result, "model_dump"):
                            result_data = result.model_dump()
                        elif hasattr(result, "dict"):
                            result_data = result.dict()
                        else:
                            result_data = str(result)

                        await self.broadcast.publish(
                            channel=f"{return_type.__name__.lower()}_updated",
                            message=json.dumps(
                                {"action": "updated", "data": result_data}
                            ),
                        )
                    except Exception as e:
                        logger.log("SQL", f"Failed to broadcast update event: {e}")

                    return self._apply_field_acl(manager, result)  # type: ignore[no-any-return]
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    raise

            self._mutation_fields[field_name] = _versioned_field(
                resolver, manager_class
            )

    def _add_delete_mutation_resolver(
        self, field_name: str, manager_class: Type[AbstractBLLManager]
    ) -> None:
        """Add a mutation resolver for deleting items"""
        # Special handling for user delete mutations - users can only delete themselves
        if "User" in manager_class.__name__:

            async def user_delete_resolver(info: Info) -> bool:
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # For users, always delete the requester (no ID parameter allowed)
                    result = manager.delete(id=requester_id)

                    # Broadcast subscription (convert to dict for JSON serialization)
                    try:
                        await self.broadcast.publish(
                            channel=f"{manager_class.__name__.lower()}_deleted",
                            message=json.dumps(
                                {"action": "deleted", "id": requester_id}
                            ),
                        )
                    except Exception as e:
                        logger.log("SQL", f"Failed to broadcast delete event: {e}")

                    return True
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    return False

            self._mutation_fields[field_name] = _versioned_field(
                user_delete_resolver, manager_class
            )
        else:

            async def resolver(id: str, info: Info) -> bool:
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # Call manager.delete with same signature as REST API
                    result = manager.delete(id=id)

                    # Broadcast subscription (convert to dict for JSON serialization)
                    try:
                        await self.broadcast.publish(
                            channel=f"{manager_class.__name__.lower()}_deleted",
                            message=json.dumps({"action": "deleted", "id": id}),
                        )
                    except Exception as e:
                        logger.log("SQL", f"Failed to broadcast delete event: {e}")

                    return True
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    return False

            self._mutation_fields[field_name] = _versioned_field(
                resolver, manager_class
            )

    def _add_subscription_resolver(self, field_name: str, model_name: str) -> None:
        """Add a subscription resolver for model events"""

        async def resolver() -> AsyncGenerator[str, None]:
            channel = f"{model_name.lower()}_created"  # Simplified for now
            async with self.broadcast.subscribe(channel=channel) as subscriber:
                async for event in subscriber:  # type: ignore[union-attr]
                    yield event.message  # type: ignore[union-attr]

        self._subscription_fields[field_name] = strawberry.subscription(resolver)

    def _convert_filter_to_params(self, filter_obj: Optional[Any]) -> Dict[str, Any]:
        """Convert filter object to search parameters"""
        if not filter_obj:
            return {}

        params: Dict[str, Any] = {}
        for attr_name in dir(filter_obj):
            if not attr_name.startswith("_"):
                value = getattr(filter_obj, attr_name)
                if value is not None:
                    params[attr_name] = value

        return params

    def _get_context_from_info(self, info: Info) -> Dict[str, Any]:
        """Extract context from GraphQL Info object"""
        context: Dict[str, Any] = {}
        requester_id: Optional[str] = None

        if info and hasattr(info, "context"):
            ctx = info.context

            # Only extract clean context data, avoid FastAPI internals
            if isinstance(ctx, dict):
                # Extract only safe context fields
                for key, value in ctx.items():
                    if key not in ["request", "background_tasks", "response"]:
                        context[key] = value
                        if key == "requester_id":
                            requester_id = value

            # Try to get requester_id from FastAPI request if not found
            if not requester_id:
                request: Optional[Any] = None

                # Try different ways to get the request
                if hasattr(ctx, "request"):
                    request = ctx.request
                elif isinstance(ctx, dict) and "request" in ctx:
                    request = ctx["request"]

                if request:
                    # Check if request has user information
                    if hasattr(request, "state") and hasattr(request.state, "user"):
                        user = request.state.user
                        if hasattr(user, "id"):
                            requester_id = user.id

                    # Fallback: try to authenticate from Authorization header or API key
                    elif hasattr(request, "headers"):
                        auth_header = request.headers.get("authorization")
                        api_key = request.headers.get("x-api-key")

                        # Check for API key first (for system entities)
                        if api_key:
                            from zephyrex.lib.InboundSecurity import (
                                resolve_principal_from_api_key,
                            )

                            resolved = resolve_principal_from_api_key(api_key)
                            if resolved is not None:
                                requester_id = resolved
                        # Fall back to JWT authentication
                        elif auth_header:
                            try:
                                from zephyrex.logic.BLL_Auth import UserManager

                                if self.model_registry:
                                    # Use the static auth method with model_registry parameter
                                    user = UserManager.auth(
                                        model_registry=self.model_registry,
                                        authorization=auth_header,
                                        request=request,
                                    )
                                    if user and hasattr(user, "id"):
                                        requester_id = user.id
                            except Exception as e:
                                logger.log(
                                    "SQL",
                                    f"Failed to authenticate user from GraphQL context: {e}",
                                )

        # Set requester_id in context if found
        if requester_id:
            context["requester_id"] = requester_id

        return context

    def _convert_input_to_dict(self, input_obj: Any) -> Dict[str, Any]:
        """Convert input object to dictionary, converting camelCase keys to snake_case for Python/Pydantic compatibility"""
        if hasattr(input_obj, "model_dump"):
            data = input_obj.model_dump(exclude_none=True)
        elif hasattr(input_obj, "__dict__"):
            # Convert from input object, excluding None values
            data: Dict[str, Any] = {}  # type: ignore[no-redef]
            for k, v in input_obj.__dict__.items():
                if v is not None and not k.startswith("_"):
                    data[k] = v
        else:
            return {}

        # Convert camelCase keys to snake_case for Python/Pydantic compatibility
        result: Dict[str, Any] = {}
        for key, value in data.items():
            # Convert camelCase to snake_case
            snake_key = stringcase.snakecase(key)
            result[snake_key] = value

        return result

    def _get_create_input_type(self, model_class: Type[BaseModel]) -> Type:
        """Get or create the Create input type for a model"""
        if hasattr(model_class, "Create"):
            return self._convert_pydantic_to_input(model_class.Create)
        # For now, return a simple placeholder to avoid complex dependencies
        return self._create_simple_input_type(model_class, "Create")

    def _get_update_input_type(self, model_class: Type[BaseModel]) -> Type:
        """Get or create the Update input type for a model"""
        if hasattr(model_class, "Update"):
            return self._convert_pydantic_to_input(model_class.Update)
        # For now, return a simple placeholder to avoid complex dependencies
        return self._create_simple_input_type(model_class, "Update")

    def _convert_pydantic_to_input(self, pydantic_model: Type[BaseModel]) -> Type:
        """Convert Pydantic model to Strawberry input type"""
        # For now, return a simple placeholder
        return self._create_simple_input_type(pydantic_model, "Input")

    def _create_simple_input_type(
        self, model_class: Type[BaseModel], suffix: str = "Input"
    ) -> Type:
        """Create a simple input type to avoid complex dependencies"""
        input_name = f"{model_class.__name__}{suffix}"

        # Get fields from the model
        annotations: Dict[str, Type] = {}
        for field_name, field_info in model_class.model_fields.items():
            field_type = field_info.annotation
            # Skip read-only fields for input types
            if field_name in [
                "id",
                "created_at",
                "updated_at",
                "created_by_user_id",
                "updated_by_user_id",
                "deleted_at",
                "deleted_by_user_id",
            ]:
                continue

            # Convert field type to optional GraphQL type
            gql_type = self._convert_python_type_to_gql(field_type)  # type: ignore[arg-type]
            if not self._is_already_optional(gql_type):
                gql_type = Optional[gql_type]  # type: ignore[assignment]
            # Convert snake_case field names to camelCase for GraphQL input types
            gql_field_name = convert_field_name(field_name, use_camelcase=True)
            annotations[gql_field_name] = gql_type  # type: ignore[index]

        # Always add at least one field to avoid empty input type error
        if not annotations:
            annotations["_dummy"] = Optional[str]  # type: ignore[assignment]

        # Create the input type class with proper annotations
        input_fields: Dict[str, Any] = {}
        for field_name, field_type in annotations.items():
            input_fields[field_name] = strawberry.field(
                default=None, description=f"Optional {field_name}"
            )

        # Create input class dynamically with all fields defaulting to None
        input_class = type(input_name, (), input_fields)
        input_class.__annotations__ = annotations

        return strawberry.input(input_class)

    def _create_query_type(self) -> Type:
        """Create Query type with all query fields"""
        fields: Dict[str, Any] = self._query_fields.copy()

        # Add a default field if no fields were generated
        if not fields:

            @strawberry.field
            def hello() -> str:
                return "Hello from GraphQL!"

            fields["hello"] = hello

        # Create the Query class dynamically
        Query = type("Query", (), fields)
        return strawberry.type(Query)

    def _create_mutation_type(self) -> Type:
        """Create Mutation type with all mutation fields"""
        fields: Dict[str, Any] = self._mutation_fields.copy()

        # Add a default field if no fields were generated
        if not fields:

            @strawberry.field
            def noop() -> str:
                return "No mutations available"

            fields["noop"] = noop

        # Create the Mutation class dynamically
        Mutation = type("Mutation", (), fields)
        return strawberry.type(Mutation)

    def _create_subscription_type(self) -> Type:
        """Create Subscription type with all subscription fields"""
        fields: Dict[str, Any] = self._subscription_fields.copy()

        # Add a default field if no fields were generated
        if not fields:

            @strawberry.subscription
            async def noop() -> AsyncGenerator[str, None]:
                yield "No subscriptions available"

            fields["noop"] = noop

        # Create the Subscription class dynamically
        Subscription = type("Subscription", (), fields)
        return strawberry.type(Subscription)

    def _analyze_model_relationships(self, model_class: Type[BaseModel]) -> None:
        """Analyze a model's relationships and register them."""
        if model_class in self._analyzed_models:
            return

        self._analyzed_models.add(model_class)

        # Analyze fields for references
        for field_name, field_info in model_class.model_fields.items():
            field_type = field_info.annotation

            # Handle Optional types
            if get_origin(field_type) is Union:
                args = get_args(field_type)
                if len(args) == 2 and type(None) in args:
                    field_type = next(arg for arg in args if arg is not type(None))

            # Check for foreign key relationships (fields ending with _id)
            if field_name.endswith("_id") and field_name != "id":
                # Try to find the corresponding model
                base_field_name = field_name[:-3]  # Remove '_id'

                # Debug logging for UserTeamModel
                if "UserTeam" in model_class.__name__:
                    logger.debug(
                        f"UserTeamModel field: {field_name} -> base_field_name: {base_field_name}"
                    )
                    logger.debug(
                        f"Available fields: {list(model_class.model_fields.keys())}"
                    )

                # Look for a corresponding object field
                if base_field_name in model_class.model_fields:
                    object_field_type: Type = model_class.model_fields[  # type: ignore[assignment]
                        base_field_name
                    ].annotation

                    # Extract the actual model type
                    if get_origin(object_field_type) is Union:
                        args = get_args(object_field_type)
                        if len(args) == 2 and type(None) in args:
                            object_field_type = next(
                                arg for arg in args if arg is not type(None)
                            )

                    if self._is_pydantic_model(object_field_type):
                        # Register forward relationship
                        if model_class not in self._forward_relationships:
                            self._forward_relationships[model_class] = {}
                        self._forward_relationships[model_class][
                            base_field_name
                        ] = object_field_type

                        # Register reverse relationship
                        if object_field_type not in self._reverse_relationships:
                            self._reverse_relationships[object_field_type] = {}

                        # Generate plural field name for reverse relationship
                        model_name = model_class.__name__.removesuffix("Model")
                        reverse_field_name = inflection.plural(
                            stringcase.snakecase(model_name)
                        )

                        self._reverse_relationships[object_field_type][
                            reverse_field_name
                        ] = (model_class, base_field_name)
                        logger.debug(
                            f"Registered reverse relationship: {object_field_type.__name__}.{reverse_field_name} "
                            f"-> List[{model_class.__name__}] (via {base_field_name}_id)"
                        )

    def _is_pydantic_model(self, field_type: Any) -> bool:
        """Check if a type is a Pydantic model."""
        return _type_introspector.is_pydantic_model(field_type)  # type: ignore[no-any-return]

    def _get_type_name_for_model(self, model_class: Type[BaseModel]) -> str:
        """Get the GraphQL type name for a model."""
        base_name = model_class.__name__.removesuffix("Model")

        # Check if this is from an extension and add prefix to avoid collisions
        module_parts = model_class.__module__.split(".")
        if len(module_parts) > 1 and module_parts[0] == "extensions":
            # For extension models, prefix with the extension name
            extension_name = module_parts[1]
            return f"{extension_name.title()}{base_name}Type"
        else:
            return f"{base_name}Type"

    def _get_or_create_type(self, model_class: Type[BaseModel]) -> Type:
        """Get or create a GraphQL type with lazy resolution for circular dependencies."""
        if model_class in self._type_registry:
            return self._type_registry[model_class]

        # For circular dependencies, we need to create the type immediately
        # This will recursively create any dependent types
        return self._create_gql_type_from_model(model_class)

    def _get_manager_for_model(self, model_class: Type[BaseModel]) -> Optional[Type]:
        """Find the manager class for a given model."""
        # Look through model relationships
        for relationship in self.model_registry.model_relationships:
            if len(relationship) == 3:
                rel_model, _, manager_class = relationship
            elif len(relationship) >= 4:
                rel_model, _, _, manager_class = relationship[:4]
            else:
                continue
            if rel_model == model_class:
                return manager_class  # type: ignore[no-any-return]

        return None

    def _create_navigation_resolver(
        self,
        source_model: Type[BaseModel],
        target_model: Type[BaseModel],
        field_name: str,
        is_reverse: bool = False,
    ) -> Callable:
        """Create a resolver for navigation properties."""

        if is_reverse:
            # Reverse navigation (one-to-many)
            async def reverse_resolver(
                self, info: Info, limit: Optional[int] = 100, offset: Optional[int] = 0
            ) -> List[target_model]:  # type: ignore[valid-type]
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")

                    if not requester_id:
                        raise Exception("Authentication required")

                    # Get the manager for the target model
                    manager_class = self._get_manager_for_model(target_model)
                    if not manager_class:
                        return []

                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # Build filter based on the foreign key
                    foreign_key_field = f"{field_name}_id"
                    filter_params = {foreign_key_field: self.id}

                    # Get the related items
                    results = manager.list(limit=limit, offset=offset, **filter_params)

                    return results  # type: ignore[no-any-return]

                except Exception as e:
                    logger.error(f"Error in reverse navigation resolver: {e}")
                    return []

            return reverse_resolver
        else:
            # Forward navigation (many-to-one)
            async def forward_resolver(self, info: Info) -> Optional[target_model]:  # type: ignore[valid-type]
                try:
                    # Check if we already have the object loaded
                    if (
                        hasattr(self, field_name)
                        and getattr(self, field_name) is not None
                    ):
                        return getattr(self, field_name)  # type: ignore[no-any-return]

                    # Get the foreign key value
                    foreign_key = getattr(self, f"{field_name}_id", None)
                    if not foreign_key:
                        return None

                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")

                    if not requester_id:
                        raise Exception("Authentication required")

                    # Get the manager for the target model
                    manager_class = self._get_manager_for_model(target_model)
                    if not manager_class:
                        return None

                    manager = manager_class(
                        requester_id=requester_id, model_registry=self.model_registry
                    )

                    # Get the related item
                    result = manager.get(id=foreign_key)

                    # Cache it on the object for future access
                    setattr(self, field_name, result)

                    return result  # type: ignore[no-any-return]

                except Exception as e:
                    logger.error(f"Error in forward navigation resolver: {e}")
                    return None

            return forward_resolver

    def _create_reverse_navigation_resolver(
        self,
        target_model: Type[BaseModel],
        source_model: Type[BaseModel],
        source_field: str,
        reverse_field_name: str,
    ) -> Callable:
        """Create a resolver for reverse navigation properties."""
        # Store the manager reference for use in the resolver
        manager_ref: "GraphQLManager" = self

        async def resolver(
            self, info: Info, limit: Optional[int] = 100, offset: Optional[int] = 0
        ):
            try:
                context = manager_ref._get_context_from_info(info)
                requester_id = context.get("requester_id")

                if not requester_id:
                    logger.error("No requester_id found in GraphQL context")
                    return []

                # Get the manager for the source model
                manager_class = manager_ref._get_manager_for_model(source_model)
                if not manager_class:
                    logger.error(f"No manager found for model {source_model}")
                    return []

                manager = manager_class(
                    model_registry=manager_ref.model_registry, requester_id=requester_id
                )

                # Build filter based on the foreign key
                foreign_key_field = f"{source_field}_id"
                filter_params = {foreign_key_field: self.id}

                # Get the related items
                results = manager.list(limit=limit, offset=offset, **filter_params)

                return results

            except Exception as e:
                logger.error(
                    f"Error in reverse navigation resolver for {reverse_field_name}: {e}"
                )
                return []

        # Set the return type annotation dynamically
        source_gql_type: Type = manager_ref._get_or_create_type(source_model)
        resolver.__annotations__["return"] = List[source_gql_type]  # type: ignore[valid-type]

        return resolver
