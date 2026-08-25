import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple, Type

import strawberry
import stringcase
from pydantic import BaseModel
from strawberry.schema_directive import Location

# ``Info`` is not referenced directly in this module, but ``degradation_aware``'s
# wrapper closures are defined here, so Strawberry resolves the wrapped resolver's
# ``info: Info`` argument annotation against this module's globals. Re-exported
# (``as Info``) to keep it in the namespace the original monolithic module carried.
from strawberry.types import Info as Info

from zephyrex.pydantic2.manager_contract import ManagerContract


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
    manager_class: Type[ManagerContract]
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
