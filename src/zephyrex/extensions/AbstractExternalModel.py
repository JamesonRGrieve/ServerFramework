from __future__ import annotations

import contextvars
import hashlib
import inspect
import json
import os
import threading
import typing
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple, Type, TypeVar, Union

from fastapi import HTTPException
from pydantic import BaseModel, Field

from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
    NavigationNotIncludedError,
    PermanentExternalError,
)
from zephyrex.extensions.FieldMappings import (
    FieldMapping,
    MappingsLike,
    apply_from_external,
    apply_to_external,
    normalize_mappings,
)
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Idempotency primitive (Item 4)
# ---------------------------------------------------------------------------


def idempotent(fn: Callable) -> Callable:
    """Mark a `*_via_provider` method as requiring an idempotency key.

    The framework's RotationManager (Batch B) inspects this marker to
    decide whether to mint a key, persist it on the active outbox row,
    and inject it into the outbound request. The decorator itself does
    not modify the call shape; it is a pure marker that survives
    inheritance.

    Usage:
        class Stripe_ChargeModel(AbstractExternalModel):
            raises_typed_errors: ClassVar[bool] = True

            @staticmethod
            @idempotent
            def create_via_provider(provider_instance, **kwargs):
                ...
    """

    setattr(fn, "_idempotent", True)
    return fn


def is_idempotent(fn: Callable) -> bool:
    """Return True if `fn` was decorated with `@idempotent`."""

    return bool(getattr(fn, "_idempotent", False))


def _canonicalize(value: Any) -> Any:
    """Canonicalize values for stable idempotency-key hashing.

    - `Decimal`        -> string (preserves exact representation).
    - `datetime`/`date` -> `.isoformat()`.
    - `set`/`frozenset` -> sorted list of canonicalized elements.
    - `dict`            -> dict of canonicalized values, key-sorted by `json.dumps`.
    - `list`/`tuple`    -> list of canonicalized values, order preserved.
    - Pydantic models   -> `.model_dump()` then canonicalized.
    """

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted([_canonicalize(v) for v in value], key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(value, dict):
        return {k: _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump())
    return value


# ---------------------------------------------------------------------------
# Bulk operation result (Item 12)
# ---------------------------------------------------------------------------


@dataclass
class BatchResult:
    """Result of a bulk external operation.

    Per-item failures surface here as typed exceptions paired with the
    input index so callers can map a partial failure back to the original
    items.

    Attributes:
        successes: List of `(input_index, returned_payload)` pairs.
        failures:  List of `(input_index, BaseExternalError)` pairs.
    """

    successes: List[Tuple[int, Any]] = field(default_factory=list)
    failures: List[Tuple[int, BaseExternalError]] = field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return not self.failures

    @property
    def count(self) -> int:
        return len(self.successes) + len(self.failures)


def derive_batch_idempotency_key(per_item_keys: List[str]) -> str:
    """Derive a batch-level idempotency key from the per-item keys.

    Sorting yields a deterministic key independent of input order so a
    retry of the same logical batch (regardless of internal ordering)
    produces the same key.
    """

    joined = "|".join(sorted(per_item_keys))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Navigation-include plumbing (Item 9)
# ---------------------------------------------------------------------------

# Request-scoped set of include directives. Populated by an inbound
# middleware (or test fixture) via `set_navigation_includes`; read by
# `ExternalNavigationProperty.__get__` to decide whether a navigation
# may resolve, and by `BatchedNavigationResolver` to know which fields
# to eager-load.
_navigation_includes_var: contextvars.ContextVar[Set[str]] = contextvars.ContextVar(
    "external_navigation_includes", default=frozenset()  # type: ignore[arg-type]
)


def set_navigation_includes(includes: Set[str]) -> contextvars.Token:
    """Set the request-scoped include set; returns a reset token.

    Usage:
        token = set_navigation_includes({"customer", "subscription"})
        try:
            ...
        finally:
            reset_navigation_includes(token)
    """

    return _navigation_includes_var.set(frozenset(includes or ()))  # type: ignore[arg-type]


def reset_navigation_includes(token: contextvars.Token) -> None:
    """Reset the navigation-include context to its previous value."""

    _navigation_includes_var.reset(token)


def get_navigation_includes() -> Set[str]:
    """Return the request-scoped include set (frozenset; empty if unset)."""

    return _navigation_includes_var.get()


def _strict_navigation_mode() -> bool:
    """Return True if `STRICT_NAVIGATION_INCLUDE` env var is truthy.

    In strict mode, accessing a navigation property whose name is not
    in the active include set raises `NavigationNotIncludedError`
    instead of falling back to lazy resolution.
    """

    return os.environ.get("STRICT_NAVIGATION_INCLUDE", "").lower() in {"1", "true", "yes", "on"}


class BatchedNavigationResolver:
    """Per-request batched resolver for external navigation properties.

    Keys are `(external_model_class, frozenset(ids))`; once a key is
    resolved, subsequent lookups by id within the same request are
    served from the cache rather than issuing additional upstream calls.

    The resolver is process-local and thread-safe via a simple lock;
    request scoping is provided by storing the active resolver on a
    contextvar (one resolver per request). Tests can construct a
    resolver directly and bind it via `bind_resolver`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Map of external_model_class -> {external_id: payload}
        self._cache: Dict[type, Dict[Any, Any]] = {}

    def get(self, external_model_class: type, external_id: Any) -> Any:
        with self._lock:
            return self._cache.get(external_model_class, {}).get(external_id)

    def has(self, external_model_class: type, external_id: Any) -> bool:
        with self._lock:
            return external_id in self._cache.get(external_model_class, {})

    def warm(self, external_model_class: type, items_by_id: Dict[Any, Any]) -> None:
        """Populate the cache with a batch of `id -> payload` entries."""

        with self._lock:
            bucket = self._cache.setdefault(external_model_class, {})
            bucket.update(items_by_id)

    def resolve_many(
        self,
        external_model_class: Type["AbstractExternalModel"],
        external_ids: List[Any],
        *,
        manager_factory: Callable[[], Any],
    ) -> Dict[Any, Any]:
        """Eagerly resolve a set of ids in one upstream call.

        `manager_factory` returns a manager instance whose `list_via_provider`
        we delegate to. Items already cached are skipped.
        """

        unresolved = [
            i for i in external_ids if not self.has(external_model_class, i)
        ]
        if not unresolved:
            return {i: self.get(external_model_class, i) for i in external_ids}

        manager = manager_factory()
        try:
            # Prefer ids=[...] when supported; this is the canonical
            # contract for upstreams that support batched fetch.
            results = manager.list(ids=unresolved)
        except TypeError:
            # Fall back to per-item lookup for upstreams that do not
            # support id batching.
            results = []
            for i in unresolved:
                try:
                    results.append(manager.get(id=i))
                except Exception as exc:  # noqa: BLE001 - defensive
                    logger.debug(
                        "BatchedNavigationResolver per-item fallback for %s id=%s failed: %s",
                        external_model_class.__name__,
                        i,
                        exc,
                    )

        items_by_id: Dict[Any, Any] = {}
        for item in results:
            item_id = (
                getattr(item, "id", None)
                if not isinstance(item, dict)
                else item.get("id")
            )
            if item_id is not None:
                items_by_id[item_id] = item
        self.warm(external_model_class, items_by_id)
        return {i: self.get(external_model_class, i) for i in external_ids}


_resolver_var: contextvars.ContextVar[Optional[BatchedNavigationResolver]] = (
    contextvars.ContextVar("external_navigation_resolver", default=None)
)


def bind_resolver(resolver: Optional[BatchedNavigationResolver]) -> contextvars.Token:
    """Bind a request-scoped batched resolver; returns a reset token."""

    return _resolver_var.set(resolver)


def reset_resolver(token: contextvars.Token) -> None:
    _resolver_var.reset(token)


def get_resolver() -> Optional[BatchedNavigationResolver]:
    """Return the currently-bound resolver, or None if no request scope."""

    return _resolver_var.get()


class ExternalNavigationProperty:
    """
    Descriptor for external navigation properties.
    Automatically resolves relationships to external API models.
    """

    def __init__(
        self,
        external_model_class: Type["AbstractExternalModel"],
        local_field: str,
        external_field: str = "id",
        manager_class: Optional[Type["AbstractExternalManager"]] = None,
        is_list: bool = False,
        cache_key: Optional[str] = None,
    ):
        """
        Initialize external navigation property.

        Args:
            external_model_class: The external model class to navigate to
            local_field: The local field containing the external ID (e.g., 'external_payment_id')
            external_field: The field in the external model to match against (default: 'id')
            manager_class: Optional specific manager class to use
            is_list: Whether this navigation returns a list of items
            cache_key: Optional cache key for performance
        """
        self.external_model_class = external_model_class
        self.local_field = local_field
        self.external_field = external_field
        self.manager_class = manager_class
        self.is_list = is_list
        self.cache_key = cache_key
        self.attr_name = None  # Set by __set_name__

    def __set_name__(self, owner, name):
        """Called when the descriptor is assigned to a class attribute."""
        self.attr_name = name

    def __get__(self, instance, owner):
        """Resolve the navigation property when accessed.

        Honors the request-scoped include set (Item 9). If the active
        request did not opt in to this navigation:

        - Strict mode (`STRICT_NAVIGATION_INCLUDE` env var truthy):
          raise `NavigationNotIncludedError`.
        - Lenient mode (default): fall back to lazy resolution as before.

        When the navigation IS included and a `BatchedNavigationResolver`
        is bound to the request, prefer the resolver cache over a fresh
        upstream call.
        """
        if instance is None:
            return self

        # Check if already cached on the instance
        cache_attr = f"_cached_{self.attr_name}"
        if hasattr(instance, cache_attr):
            return getattr(instance, cache_attr)

        # Get the local field value (the external ID)
        local_value = getattr(instance, self.local_field, None)
        if not local_value:
            return [] if self.is_list else None

        # Item 9: gate on the request-scoped include set.
        includes = get_navigation_includes()
        included = self.attr_name in includes if self.attr_name else False
        if not included:
            if _strict_navigation_mode():
                raise NavigationNotIncludedError(
                    f"Navigation property {self.attr_name!r} accessed without being "
                    f"named in the request's include set. Add it to `include` to "
                    f"opt in.",
                )
            # Lenient mode: continue to lazy resolution below.

        # Item 9: consult the request-scoped batched resolver first.
        resolver = get_resolver()
        if resolver is not None and not self.is_list:
            cached = resolver.get(self.external_model_class, local_value)
            if cached is not None:
                setattr(instance, cache_attr, cached)
                return cached

        try:
            # Get the manager for the external model
            manager_class = self.manager_class or self._get_default_manager_class()

            # Create manager instance - we need a requester_id context
            # For now, use a system context, but in practice this should come from the current request context
            from zephyrex.lib.Environment import env

            manager = manager_class(requester_id=env("SYSTEM_ID"))

            # Resolve the relationship
            if self.is_list:
                # For list relationships, search by the external field
                search_params = {self.external_field: local_value}
                result = manager.list(**search_params)
            else:
                # For single relationships, get by the external field
                if self.external_field == "id":
                    result = manager.get(id=local_value)
                else:
                    search_params = {self.external_field: local_value}
                    results = manager.list(**search_params)
                    result = results[0] if results else None

            # Cache the result on the instance
            setattr(instance, cache_attr, result)
            # Warm the request-scoped resolver too if applicable.
            if resolver is not None and not self.is_list and result is not None:
                resolver.warm(self.external_model_class, {local_value: result})
            return result

        except NavigationNotIncludedError:
            raise
        except Exception as e:
            logger.warning(
                f"Failed to resolve navigation property {self.attr_name}: {e}"
            )
            return [] if self.is_list else None

    def _get_default_manager_class(self) -> Type["AbstractExternalManager"]:
        """Get the default manager class for the external model."""
        # Look for a manager class with the same name pattern
        model_name = self.external_model_class.__name__
        if model_name.endswith("Model"):
            base_name = model_name[:-5]  # Remove "Model"
            manager_name = f"{base_name}Manager"

            # Try to find the manager class in the same module
            import sys

            for module in sys.modules.values():
                if hasattr(module, manager_name):
                    manager_class = getattr(module, manager_name)
                    if issubclass(manager_class, AbstractExternalManager):
                        return manager_class  # type: ignore[no-any-return]

        raise ValueError(
            f"Could not find manager class for {self.external_model_class.__name__}"
        )


def external_navigation_property(
    external_model_class: Type["AbstractExternalModel"],
    local_field: str,
    external_field: str = "id",
    manager_class: Optional[Type["AbstractExternalManager"]] = None,
    is_list: bool = False,
) -> Any:
    """
    Decorator/factory function for creating external navigation properties.

    Usage:
        class UserModel(BaseModel):
            external_payment_id: Optional[str] = None

            customer: Optional[StripeCustomerModel] = external_navigation_property(
                StripeCustomerModel,
                local_field="external_payment_id"
            )
    """
    return ExternalNavigationProperty(
        external_model_class=external_model_class,
        local_field=local_field,
        external_field=external_field,
        manager_class=manager_class,
        is_list=is_list,
    )


def _unwrap_provider_call(model_class: type, method_name: str, fn: Callable, *args, **kwargs) -> Any:
    """Invoke a `*_via_provider` method and return the unwrapped payload.

    Implements the back-compat shim from Item 1: providers may either
    raise typed `BaseExternalError` exceptions on failure (the new
    contract) or return a `{success, data, error}` envelope (the legacy
    contract). The shim detects the envelope shape and translates it to
    the equivalent typed exception.

    The `model_class.raises_typed_errors` ClassVar opts a model into the
    new contract eagerly so providers can migrate atomically per-class.
    """

    try:
        result = fn(*args, **kwargs)
    except BaseExternalError:
        raise
    except HTTPException:
        raise
    except Exception as exc:
        # Unknown exception: when the model opts in, surface as a
        # generic typed error rather than letting the raw exception
        # escape. Otherwise re-raise as-is for legacy callers.
        if getattr(model_class, "raises_typed_errors", False):
            raise PermanentExternalError(
                str(exc), provider=getattr(fn, "__qualname__", str(fn)), ability=method_name, cause=exc
            ) from exc
        raise

    # New contract: the method returned the payload directly.
    # Legacy envelope detection: a dict carrying a `success` key and at least
    # one of `data` / `error`. Both keys present together is the canonical
    # legacy shape, but providers in the wild sometimes omit one or the other.
    if (
        not isinstance(result, dict)
        or "success" not in result
        or not ({"data", "error"} & set(result.keys()))
    ):
        return result

    # Legacy envelope shim.
    if result.get("success", False):
        return result.get("data")
    err_msg = result.get("error", "Unknown external error")
    # Map "Not found" sentinel to InvalidInputExternalError per the
    # historical contract (the API client further translates it to a 404
    # for the legacy callers).
    if err_msg == "Not found":
        exc: BaseExternalError = InvalidInputExternalError(err_msg, ability=method_name)  # type: ignore[no-redef]
        exc.upstream_status = 404  # type: ignore[misc]
        raise exc  # type: ignore[misc]
    raise PermanentExternalError(err_msg, ability=method_name)


class AbstractExternalAPIClient(ABC):
    """
    Abstract base class for external API clients.
    Provides database-like interface for external APIs via Provider Rotation System.
    """

    def __init__(
        self, provider_rotation_manager, model_class: Type["AbstractExternalModel"]
    ):
        """
        Initialize the API client.

        Args:
            provider_rotation_manager: RotationManager instance for provider rotation
            model_class: The external model class this client manages
        """
        self.rotation_manager = provider_rotation_manager
        self.model_class = model_class

    def create(
        self,
        requester_id: str,
        db=None,  # Ignored for external APIs
        return_type: str = "dto",
        override_dto: Optional[Type] = None,
        **kwargs,
    ) -> Any:
        """
        Create an entity via external API using provider rotation.

        Args:
            requester_id: ID of requesting user
            db: Ignored for external APIs
            return_type: Type of return value ("dto" or "dict")
            override_dto: Optional DTO class override
            **kwargs: Creation parameters

        Returns:
            Created entity
        """
        try:
            # Convert internal format to external API format
            external_data = self.model_class.to_external_format(kwargs)

            # Call external API via provider rotation. The rotation manager
            # forwards either a payload (new contract) or a `{success, data}`
            # dict (legacy); `_unwrap_provider_call` normalizes both shapes.
            payload = _unwrap_provider_call(
                self.model_class,
                "create",
                self.rotation_manager.rotate,
                self.model_class.create_via_provider,
                **external_data,
            )

            # Convert external format back to internal format
            internal_data = self.model_class.from_external_format(payload or {})

            # Return as DTO or dict
            if return_type == "dto":
                dto_class = override_dto or self.model_class
                return dto_class(**internal_data)
            else:
                return internal_data

        except BaseExternalError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error creating {self.model_class.__name__}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def get(
        self,
        requester_id: str,
        db=None,  # Ignored for external APIs
        return_type: str = "dto",
        override_dto: Optional[Type] = None,
        options: Optional[List] = None,
        **kwargs,
    ) -> Any:
        """
        Get an entity by ID via external API.

        Args:
            requester_id: ID of requesting user
            db: Ignored for external APIs
            return_type: Type of return value
            override_dto: Optional DTO class override
            options: Ignored for external APIs (no joins)
            **kwargs: Query parameters (must include 'id')

        Returns:
            Retrieved entity
        """
        if "id" not in kwargs:
            raise HTTPException(status_code=400, detail="ID parameter required")

        try:
            payload = _unwrap_provider_call(
                self.model_class,
                "get",
                self.rotation_manager.rotate,
                self.model_class.get_via_provider,
                external_id=kwargs["id"],
            )

            # Convert external format to internal format
            internal_data = self.model_class.from_external_format(payload or {})

            # Return as DTO or dict
            if return_type == "dto":
                dto_class = override_dto or self.model_class
                return dto_class(**internal_data)
            else:
                return internal_data

        except InvalidInputExternalError as exc:
            if exc.upstream_status == 404:
                raise HTTPException(
                    status_code=404, detail=f"{self.model_class.__name__} not found"
                ) from exc
            raise
        except BaseExternalError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting {self.model_class.__name__}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def list(
        self,
        requester_id: str,
        db=None,  # Ignored for external APIs
        return_type: str = "dto",
        override_dto: Optional[Type] = None,
        options: Optional[List] = None,
        order_by: Optional[List] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        filters: Optional[List] = None,
        **kwargs,
    ) -> List[Any]:
        """
        List entities via external API.

        Args:
            requester_id: ID of requesting user
            db: Ignored for external APIs
            return_type: Type of return value
            override_dto: Optional DTO class override
            options: Ignored for external APIs
            order_by: Sorting parameters
            limit: Maximum number of results
            offset: Pagination offset
            filters: Ignored - use kwargs for filtering
            **kwargs: Query parameters for filtering

        Returns:
            List of entities
        """
        try:
            # Convert internal filters to external API format
            external_params = self.model_class.to_external_query_format(
                kwargs, limit=limit, offset=offset, order_by=order_by
            )

            payload = _unwrap_provider_call(
                self.model_class,
                "list",
                self.rotation_manager.rotate,
                self.model_class.list_via_provider,
                **external_params,
            )

            # Convert external format to internal format
            items = []
            for item_data in (payload or []):
                internal_data = self.model_class.from_external_format(item_data)

                if return_type == "dto":
                    dto_class = override_dto or self.model_class
                    items.append(dto_class(**internal_data))
                else:
                    items.append(internal_data)

            return items

        except BaseExternalError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error listing {self.model_class.__name__}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def update(
        self,
        requester_id: str,
        db=None,  # Ignored for external APIs
        return_type: str = "dto",
        override_dto: Optional[Type] = None,
        new_properties: Optional[Dict] = None,
        **kwargs,
    ) -> Any:
        """
        Update an entity via external API.

        Args:
            requester_id: ID of requesting user
            db: Ignored for external APIs
            return_type: Type of return value
            override_dto: Optional DTO class override
            new_properties: Properties to update
            **kwargs: Must include 'id'

        Returns:
            Updated entity
        """
        if "id" not in kwargs:
            raise HTTPException(status_code=400, detail="ID parameter required")

        try:
            # Convert internal format to external API format
            update_data = self.model_class.to_external_format(new_properties or {})

            payload = _unwrap_provider_call(
                self.model_class,
                "update",
                self.rotation_manager.rotate,
                self.model_class.update_via_provider,
                external_id=kwargs["id"],
                **update_data,
            )

            internal_data = self.model_class.from_external_format(payload or {})

            # Return as DTO or dict
            if return_type == "dto":
                dto_class = override_dto or self.model_class
                return dto_class(**internal_data)
            else:
                return internal_data

        except InvalidInputExternalError as exc:
            if exc.upstream_status == 404:
                raise HTTPException(
                    status_code=404, detail=f"{self.model_class.__name__} not found"
                ) from exc
            raise
        except BaseExternalError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating {self.model_class.__name__}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def delete(self, requester_id: str, db=None, **kwargs) -> None:
        """
        Delete an entity via external API.

        Args:
            requester_id: ID of requesting user
            db: Ignored for external APIs
            **kwargs: Must include 'id'
        """
        if "id" not in kwargs:
            raise HTTPException(status_code=400, detail="ID parameter required")

        try:
            _unwrap_provider_call(
                self.model_class,
                "delete",
                self.rotation_manager.rotate,
                self.model_class.delete_via_provider,
                external_id=kwargs["id"],
            )

        except InvalidInputExternalError as exc:
            if exc.upstream_status == 404:
                raise HTTPException(
                    status_code=404, detail=f"{self.model_class.__name__} not found"
                ) from exc
            raise
        except BaseExternalError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting {self.model_class.__name__}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def exists(self, requester_id: str, db=None, **kwargs) -> bool:
        """
        Check if an entity exists via external API.

        Args:
            requester_id: ID of requesting user
            db: Ignored for external APIs
            **kwargs: Query parameters

        Returns:
            True if entity exists, False otherwise
        """
        try:
            self.get(requester_id=requester_id, **kwargs)
            return True
        except HTTPException as e:
            if e.status_code == 404:
                return False
            raise


class DirectProviderAPIClient(AbstractExternalAPIClient):
    """
    Direct API client that works with provider classes for testing.

    This client bypasses the rotation system and calls provider methods directly,
    making it suitable for testing scenarios where no rotation manager is available.
    """

    def __init__(self, provider_class, model_class: Type["AbstractExternalModel"]):
        """Initialize direct provider API client."""
        self.provider_class = provider_class
        self.model_class = model_class
        logger.debug(
            f"Initialized DirectProviderAPIClient for {provider_class.__name__}"
        )

    def create(
        self,
        requester_id: str,
        db=None,  # Ignored for external APIs
        return_type: str = "dto",
        override_dto: Optional[Type] = None,
        **kwargs,
    ) -> Any:
        """Create via direct provider call."""
        try:
            # Call the model's create_via_provider method directly
            result = self.model_class.create_via_provider(self.provider_class, **kwargs)

            if result.get("success"):
                return result.get("data", result)
            else:
                logger.error(
                    f"Provider create failed: {result.get('error', 'Unknown error')}"
                )
                return None
        except Exception as e:
            logger.error(f"Direct provider create failed: {e}")
            return None

    def get(
        self,
        requester_id: str,
        db=None,  # Ignored for external APIs
        return_type: str = "dto",
        override_dto: Optional[Type] = None,
        options: Optional[List] = None,
        **kwargs,
    ) -> Any:
        """Get via direct provider call."""
        try:
            # Extract the ID from kwargs
            external_id = kwargs.get("id")
            if not external_id:
                logger.error("No ID provided for get operation")
                return None

            # Call the model's get_via_provider method directly
            result = self.model_class.get_via_provider(self.provider_class, external_id)

            if result.get("success"):
                return result.get("data", result)
            else:
                logger.error(
                    f"Provider get failed: {result.get('error', 'Unknown error')}"
                )
                return None
        except Exception as e:
            logger.error(f"Direct provider get failed: {e}")
            return None

    def list(
        self,
        requester_id: str,
        db=None,  # Ignored for external APIs
        return_type: str = "dto",
        override_dto: Optional[Type] = None,
        options: Optional[List] = None,
        order_by: Optional[List] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        filters: Optional[List] = None,
        **kwargs,
    ) -> List[Any]:
        """List via direct provider call."""
        try:
            # Call the model's list_via_provider method directly
            result = self.model_class.list_via_provider(self.provider_class, **kwargs)

            if result.get("success"):
                data = result.get("data", [])
                return data if isinstance(data, list) else [data]
            else:
                logger.error(
                    f"Provider list failed: {result.get('error', 'Unknown error')}"
                )
                return []
        except Exception as e:
            logger.error(f"Direct provider list failed: {e}")
            return []

    def update(
        self,
        requester_id: str,
        db=None,  # Ignored for external APIs
        return_type: str = "dto",
        override_dto: Optional[Type] = None,
        new_properties: Optional[Dict] = None,
        **kwargs,
    ) -> Any:
        """Update via direct provider call."""
        try:
            # Extract the ID from kwargs
            external_id = kwargs.get("id")
            if not external_id:
                logger.error("No ID provided for update operation")
                return None

            # Merge new_properties with kwargs for update data
            update_data = {**kwargs}
            if new_properties:
                update_data.update(new_properties)

            # Remove ID from update data
            update_data.pop("id", None)

            # Call the model's update_via_provider method directly
            result = self.model_class.update_via_provider(
                self.provider_class, external_id, **update_data
            )

            if result.get("success"):
                return result.get("data", result)
            else:
                logger.error(
                    f"Provider update failed: {result.get('error', 'Unknown error')}"
                )
                return None
        except Exception as e:
            logger.error(f"Direct provider update failed: {e}")
            return None

    def delete(self, requester_id: str, db=None, **kwargs) -> None:
        """Delete via direct provider call."""
        try:
            # Extract the ID from kwargs
            external_id = kwargs.get("id")
            if not external_id:
                logger.error("No ID provided for delete operation")
                return

            # Call the model's delete_via_provider method directly
            result = self.model_class.delete_via_provider(
                self.provider_class, external_id
            )

            if not result.get("success"):
                logger.error(
                    f"Provider delete failed: {result.get('error', 'Unknown error')}"
                )
        except Exception as e:
            logger.error(f"Direct provider delete failed: {e}")

    def exists(self, requester_id: str, db=None, **kwargs) -> bool:
        """Check existence via direct provider call."""
        try:
            # Try to get the entity to check if it exists
            result = self.get(requester_id, db=db, **kwargs)
            return result is not None
        except Exception as e:
            logger.error(f"Direct provider exists check failed: {e}")
            return False


class AbstractExternalModel(BaseModel, ABC):
    """
    Abstract base class for models representing external API resources.

    Provides similar interface to internal database models but operates via
    external APIs through the Provider Rotation System.
    """

    # External API resource identifier (e.g., "products", "customers")
    external_resource: ClassVar[str] = ""

    # Field mappings between internal format and external API format.
    # Accepts either the legacy `Dict[str, str]` rename-only shape (back-compat)
    # or the new typed `List[FieldMapping]` shape from `extensions.FieldMappings`.
    # Normalization happens lazily inside `to_external_format`/`from_external_format`.
    field_mappings: ClassVar[Union[Dict[str, str], List[FieldMapping]]] = {}

    # Item 1: opt-in flag declaring this model's `*_via_provider` methods raise
    # typed `BaseExternalError` exceptions on failure rather than returning the
    # legacy `{success, data, error}` envelope. Providers may migrate per-class.
    raises_typed_errors: ClassVar[bool] = False

    # Provider method mappings for CRUD operations
    provider_methods: ClassVar[Dict[str, str]] = {
        "create": "create_via_provider",
        "get": "get_via_provider",
        "list": "list_via_provider",
        "update": "update_via_provider",
        "delete": "delete_via_provider",
    }

    # External API client instance (set by manager)
    _api_client: ClassVar[Optional[AbstractExternalAPIClient]] = None

    # ------------------------------------------------------------------
    # Subclass hook (Item 1)
    # ------------------------------------------------------------------

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Warn (do not raise) when a `*_via_provider` method is annotated
        as returning a `dict`/`Dict`.

        The legacy contract returned envelopes; the new contract returns
        the unwrapped payload. A `dict` return annotation on a method
        opted into the new contract (`raises_typed_errors = True` or
        `@idempotent`) is almost certainly a leftover from migration and
        deserves a warning.
        """

        super().__init_subclass__(**kwargs)
        for name in (
            "create_via_provider",
            "get_via_provider",
            "list_via_provider",
            "update_via_provider",
            "delete_via_provider",
            "batch_create_via_provider",
            "batch_update_via_provider",
            "batch_delete_via_provider",
        ):
            method = cls.__dict__.get(name)
            if method is None:
                continue
            try:
                hints = typing.get_type_hints(method)
            except Exception:
                continue
            return_type = hints.get("return")
            if return_type is dict or (
                getattr(return_type, "__origin__", None) is dict
            ):
                opted_in = getattr(cls, "raises_typed_errors", False) or getattr(
                    method, "_idempotent", False
                )
                if opted_in:
                    warnings.warn(
                        f"{cls.__name__}.{name} is annotated as returning `dict`, but "
                        f"this class opts into the typed-raise contract. The new "
                        f"contract returns the unwrapped payload (any type) on "
                        f"success and raises BaseExternalError on failure. Consider "
                        f"updating the return annotation.",
                        UserWarning,
                        stacklevel=2,
                    )

    @property
    def API(self) -> AbstractExternalAPIClient:
        """Get the API client for this model."""
        if self._api_client is None:
            raise RuntimeError(
                f"API client not initialized for {self.__class__.__name__}"
            )
        return self._api_client

    @classmethod
    def set_api_client(cls, api_client: AbstractExternalAPIClient):
        """Set the API client for this model class."""
        cls._api_client = api_client

    @classmethod
    def _normalized_field_mappings(cls) -> List[FieldMapping]:
        """Return `field_mappings` as a list of typed `FieldMapping` objects.

        Memoization is per-class on `_field_mappings_cache`.
        """

        cached = cls.__dict__.get("_field_mappings_cache")
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        normalized = normalize_mappings(cls.field_mappings)
        cls._field_mappings_cache = normalized  # type: ignore[attr-defined]
        return normalized  # type: ignore[no-any-return]

    @classmethod
    def to_external_format(cls, internal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert internal data format to external API format.

        Default implementation runs the typed `field_mappings` pipeline
        from `extensions.FieldMappings`. Subclasses may override for
        provider-specific shapes that the pipeline cannot express.
        """

        return apply_to_external(cls._normalized_field_mappings(), internal_data)  # type: ignore[no-any-return]

    @classmethod
    def from_external_format(cls, external_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert external API format to internal data format."""

        return apply_from_external(cls._normalized_field_mappings(), external_data)  # type: ignore[no-any-return]

    @classmethod
    def to_external_query_format(
        cls,
        query_params: Dict[str, Any],
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[List] = None,
    ) -> Dict[str, Any]:
        """
        Convert internal query parameters to external API query format.

        Args:
            query_params: Internal query parameters
            limit: Maximum results
            offset: Pagination offset
            order_by: Sorting parameters

        Returns:
            Query parameters in external API format
        """
        # Default implementation - override in subclasses for API-specific formatting.
        # Reuse the typed field-mapping pipeline so query parameters undergo the
        # same renames / transformations as payload bodies.
        external_params = apply_to_external(
            cls._normalized_field_mappings(), query_params
        )

        # Add pagination
        if limit is not None:
            external_params["limit"] = limit
        if offset is not None:
            external_params["offset"] = offset

        # Add ordering - format depends on API
        if order_by:
            # This is API-specific - override in subclasses
            external_params["order"] = order_by

        return external_params  # type: ignore[no-any-return]

    @staticmethod
    @abstractmethod
    def create_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """
        Create resource via provider instance.
        Called by Provider Rotation System.

        Args:
            provider_instance: ProviderInstanceModel with API credentials
            **kwargs: Creation parameters in external format

        Returns:
            Dict with success status and data/error
        """
        pass

    @staticmethod
    @abstractmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """
        Get resource via provider instance.
        Called by Provider Rotation System.

        Args:
            provider_instance: ProviderInstanceModel with API credentials
            external_id: External resource ID

        Returns:
            Dict with success status and data/error
        """
        pass

    @staticmethod
    @abstractmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """
        List resources via provider instance.
        Called by Provider Rotation System.

        Args:
            provider_instance: ProviderInstanceModel with API credentials
            **kwargs: Query parameters in external format

        Returns:
            Dict with success status and data/error
        """
        pass

    @staticmethod
    @abstractmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        """
        Update resource via provider instance.
        Called by Provider Rotation System.

        Args:
            provider_instance: ProviderInstanceModel with API credentials
            external_id: External resource ID
            **kwargs: Update parameters in external format

        Returns:
            Dict with success status and data/error
        """
        pass

    @staticmethod
    @abstractmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """
        Delete resource via provider instance.
        Called by Provider Rotation System.

        Args:
            provider_instance: ProviderInstanceModel with API credentials
            external_id: External resource ID

        Returns:
            Dict with success status and data/error
        """
        pass

    # ------------------------------------------------------------------
    # Optional bulk slots (Item 12)
    # ------------------------------------------------------------------
    #
    # These default to NotImplementedError. Providers whose upstream
    # supports a bulk endpoint override one or more to take advantage of
    # the round-trip savings; `AbstractExternalManager.batch_*` checks
    # for the override via `hasattr(...) and method != AbstractExternalModel.method`.

    @staticmethod
    def batch_create_via_provider(provider_instance, items: List[Dict[str, Any]]) -> List[Any]:
        """Optional bulk create. Default raises `NotImplementedError`.

        On success, return one payload per input item in the same order;
        per-item failures should appear in-band as `BaseExternalError`
        instances (rather than being raised) so the caller can pair them
        with the original input index.
        """

        raise NotImplementedError("batch_create_via_provider not implemented")

    @staticmethod
    def batch_update_via_provider(
        provider_instance, updates: List[Tuple[str, Dict[str, Any]]]
    ) -> List[Any]:
        """Optional bulk update. Each `(external_id, fields)` tuple
        describes one update."""

        raise NotImplementedError("batch_update_via_provider not implemented")

    @staticmethod
    def batch_delete_via_provider(provider_instance, external_ids: List[str]) -> List[Any]:
        """Optional bulk delete."""

        raise NotImplementedError("batch_delete_via_provider not implemented")

    @classmethod
    def supports_bulk(cls, op: str) -> bool:
        """Return True if `cls` overrides `batch_{op}_via_provider`."""

        method_name = f"batch_{op}_via_provider"
        cls_method = getattr(cls, method_name, None)
        base_method = getattr(AbstractExternalModel, method_name, None)
        if cls_method is None or base_method is None:
            return False
        return cls_method is not base_method

    # Pydantic model classes for different operations
    class Create(BaseModel):
        """Base create model for external resources."""

        pass

    class Update(BaseModel):
        """Base update model for external resources."""

        pass

    class Search(BaseModel):
        """Base search model for external resources."""

        pass


def create_external_reference_model(
    external_model_class: Type[AbstractExternalModel],
    reference_field_name: str,
    local_field_name: str,
    manager_class: Optional[Type["AbstractExternalManager"]] = None,
) -> Type[BaseModel]:
    """
    Factory function to create external reference models that work with the standard pattern.

    Args:
        external_model_class: The external model to reference
        reference_field_name: The name of the reference field (e.g., "customer_id")
        local_field_name: The local field containing the external ID
        manager_class: Optional specific manager class

    Returns:
        A reference model class with Optional and Search variants
    """

    # Create the navigation property descriptor
    nav_property = ExternalNavigationProperty(
        external_model_class=external_model_class,
        local_field=local_field_name,
        manager_class=manager_class,
    )

    # Extract the navigation property name from the model class name
    model_name = external_model_class.__name__
    if model_name.endswith("Model"):
        nav_property_name = model_name[:-5].lower()  # Remove "Model" and lowercase
    else:
        nav_property_name = model_name.lower()

    # Create the ReferenceID class
    class ReferenceID(BaseModel):
        """ReferenceID for external model."""

        pass

    # Add the reference field dynamically
    setattr(
        ReferenceID,
        reference_field_name,
        Field(..., description=f"The ID of the related {nav_property_name}"),
    )

    class Optional(BaseModel):
        """Optional ReferenceID for external model."""

        pass

    # Add optional reference field
    setattr(
        Optional,
        reference_field_name,
        Field(None, description=f"The ID of the related {nav_property_name}"),
    )

    class Search(BaseModel):
        """Search ReferenceID for external model."""

        pass

    # Add search reference field
    setattr(
        Search,
        reference_field_name,
        Field(None, description=f"Search for {nav_property_name} ID"),
    )

    # Set the nested classes
    ReferenceID.Optional = Optional  # type: ignore[attr-defined]
    ReferenceID.Search = Search  # type: ignore[attr-defined]

    # Create the main reference model class
    class ExternalReferenceModel(ReferenceID):
        """Reference model for external resource."""

        pass

    # Add the navigation property
    setattr(ExternalReferenceModel, nav_property_name, nav_property)

    class OptionalReferenceModel(Optional):
        """Optional reference model for external resource."""

        pass

    # Add optional navigation property
    setattr(OptionalReferenceModel, nav_property_name, nav_property)

    # Set nested classes
    ExternalReferenceModel.Optional = OptionalReferenceModel  # type: ignore[attr-defined]

    return ExternalReferenceModel


class AbstractExternalManager(AbstractBLLManager):
    """
    Abstract base class for managers that work with external API models.

    Inherits from AbstractBLLManager to leverage hooks, validation, and other
    existing functionality while overriding the database operations to work
    with external APIs via the Provider Rotation System.
    """

    # External model class
    Model: Type[AbstractExternalModel] = None  # type: ignore[assignment]

    # Reference model class (for relationships)
    ReferenceModel: Type = None  # type: ignore[assignment]

    # Network model class (for API schemas)
    NetworkModel: Type = None  # type: ignore[assignment]

    def __init__(
        self,
        requester_id: str,
        rotation_manager=None,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db=None,  # Ignored for external managers
        parent=None,
    ):
        """
        Initialize the external manager.

        Args:
            requester_id: ID of the user making the request
            rotation_manager: RotationManager instance for provider rotation
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team
            db: Ignored for external managers
            parent: Parent manager for nested operations
        """
        # Initialize basic attributes without calling parent __init__
        # since external managers don't need database access
        self.requester_id = requester_id
        self.target_id = target_id
        self.target_team_id = target_team_id
        self._parent = parent
        self.rotation_manager = rotation_manager

        # External managers don't need database connections
        self.db = None
        self.requester = None  # External managers don't need user lookup

        # Set up API client for the model
        if self.Model and rotation_manager:
            self._api_client = AbstractExternalAPIClient(rotation_manager, self.Model)
        elif self.Model and hasattr(self, "provider_class"):
            # For testing or when no rotation manager is available,
            # create a direct API client that uses the provider class directly
            logger.debug(
                f"No rotation manager provided, creating direct API client for {self.provider_class.__name__}"
            )
            self._api_client = DirectProviderAPIClient(self.provider_class, self.Model)
        else:
            self._api_client = None  # type: ignore[assignment]

    @property
    def DB(self) -> AbstractExternalAPIClient:
        """
        Override DB property to return external API client instead of database model.
        This allows all existing AbstractBLLManager methods to work seamlessly
        with external APIs.
        """
        if self._api_client is None:
            raise RuntimeError(
                f"API client not initialized for {self.__class__.__name__}"
            )
        return self._api_client

    @property
    def db(self):
        """
        Provide db property for compatibility with AbstractBLLManager.
        External managers don't use database connections.
        """
        return None

    @db.setter
    def db(self, value):
        """
        Setter for db property that ignores assignments.
        External managers don't use database connections.
        """
        pass  # Ignore database assignments for external managers

    # ------------------------------------------------------------------
    # Mirror lifecycle storage (Item 14)
    # ------------------------------------------------------------------
    # Concrete subclasses decorated with `@mirror_on_create` / `@mirror_on_update`
    # / `@mirror_on_delete` accumulate their declarations here. The list
    # is owned per-class (not shared across siblings) by the decorator
    # implementation in `extensions.MirrorLifecycle`.
    _mirror_specs: ClassVar[List[Any]] = []

    # ------------------------------------------------------------------
    # Idempotency primitive (Item 4)
    # ------------------------------------------------------------------

    def idempotency_key(self, operation: str, requester_id: str, args: Dict[str, Any]) -> str:
        """Return a deterministic idempotency key for an operation+args.

        Default implementation: SHA-256 over
        ``f"{requester_id}|{operation}|{json.dumps(canonicalize(args), sort_keys=True)}"``.

        Canonicalization rules (see `_canonicalize`):
        - `Decimal` -> string
        - `datetime` / `date` -> `.isoformat()`
        - `set` / `frozenset` -> sorted list

        Subclasses may override when an upstream demands a particular key
        shape (e.g., Stripe limits keys to 255 chars; some upstreams
        require UUIDs).
        """

        canonical = _canonicalize(args)
        material = (
            f"{requester_id}|{operation}|{json.dumps(canonical, sort_keys=True, default=str)}"
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Bulk operations (Item 12)
    # ------------------------------------------------------------------

    def batch_create(self, items: List[Dict[str, Any]]) -> "BatchResult":
        """Create multiple entities, preferring a bulk upstream when available.

        When `Model.batch_create_via_provider` is overridden, delegates to
        it in one upstream call. Otherwise falls back to per-item
        `create()` calls. Per-item failures surface as
        `BaseExternalError` instances on `BatchResult.failures`, paired
        with the original input index.
        """

        result = BatchResult()
        if self.Model is not None and self.Model.supports_bulk("create"):
            external_items = [self.Model.to_external_format(it) for it in items]
            try:
                payloads = self.rotation_manager.rotate(  # type: ignore[union-attr]
                    self.Model.batch_create_via_provider,
                    items=external_items,
                )
            except BaseExternalError as exc:
                # Whole-batch failure: every item is a failure with the same exc.
                for idx in range(len(items)):
                    result.failures.append((idx, exc))
                return result
            for idx, payload in enumerate(payloads or []):
                if isinstance(payload, BaseExternalError):
                    result.failures.append((idx, payload))
                else:
                    result.successes.append((idx, self.Model.from_external_format(payload or {})))
            return result

        # Fallback loop.
        for idx, item in enumerate(items):
            try:
                created = self.create(**item) if hasattr(self, "create") else self.DB.create(  # type: ignore[attr-defined]
                    requester_id=self.requester_id, **item
                )
                result.successes.append((idx, created))
            except BaseExternalError as exc:
                result.failures.append((idx, exc))
            except HTTPException as exc:
                result.failures.append((idx, PermanentExternalError(str(exc.detail), upstream_status=exc.status_code)))
            except Exception as exc:  # noqa: BLE001
                result.failures.append((idx, PermanentExternalError(str(exc), cause=exc)))
        return result

    def batch_update(self, items: List[Dict[str, Any]]) -> "BatchResult":  # type: ignore[override]
        """Update multiple entities, preferring a bulk upstream when available.

        Each item is `{"id": ..., "data": {...}}`.
        """

        result = BatchResult()
        if self.Model is not None and self.Model.supports_bulk("update"):
            updates = [
                (it["id"], self.Model.to_external_format(it.get("data", {})))
                for it in items
            ]
            try:
                payloads = self.rotation_manager.rotate(  # type: ignore[union-attr]
                    self.Model.batch_update_via_provider,
                    updates=updates,
                )
            except BaseExternalError as exc:
                for idx in range(len(items)):
                    result.failures.append((idx, exc))
                return result
            for idx, payload in enumerate(payloads or []):
                if isinstance(payload, BaseExternalError):
                    result.failures.append((idx, payload))
                else:
                    result.successes.append((idx, self.Model.from_external_format(payload or {})))
            return result

        for idx, item in enumerate(items):
            try:
                updated = self.update(  # type: ignore[attr-defined]
                    id=item["id"], **(item.get("data") or {})
                )
                result.successes.append((idx, updated))
            except BaseExternalError as exc:
                result.failures.append((idx, exc))
            except HTTPException as exc:
                result.failures.append((idx, PermanentExternalError(str(exc.detail), upstream_status=exc.status_code)))
            except Exception as exc:  # noqa: BLE001
                result.failures.append((idx, PermanentExternalError(str(exc), cause=exc)))
        return result

    def batch_delete(self, ids: List[str]) -> "BatchResult":  # type: ignore[override]
        """Delete multiple entities, preferring a bulk upstream when available."""

        result = BatchResult()
        if self.Model is not None and self.Model.supports_bulk("delete"):
            try:
                payloads = self.rotation_manager.rotate(  # type: ignore[union-attr]
                    self.Model.batch_delete_via_provider,
                    external_ids=ids,
                )
            except BaseExternalError as exc:
                for idx in range(len(ids)):
                    result.failures.append((idx, exc))
                return result
            for idx, payload in enumerate(payloads or [None] * len(ids)):
                if isinstance(payload, BaseExternalError):
                    result.failures.append((idx, payload))
                else:
                    result.successes.append((idx, payload))
            return result

        for idx, entity_id in enumerate(ids):
            try:
                self.delete(id=entity_id)  # type: ignore[attr-defined]
                result.successes.append((idx, None))
            except BaseExternalError as exc:
                result.failures.append((idx, exc))
            except HTTPException as exc:
                result.failures.append((idx, PermanentExternalError(str(exc.detail), upstream_status=exc.status_code)))
            except Exception as exc:  # noqa: BLE001
                result.failures.append((idx, PermanentExternalError(str(exc), cause=exc)))
        return result

    # All other methods (create, get, list, search, update, delete,
    # hooks, validation, etc.) are inherited from AbstractBLLManager and
    # work automatically with the overridden DB property.
