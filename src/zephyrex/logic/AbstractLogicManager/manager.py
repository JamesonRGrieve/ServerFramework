from abc import ABC
from datetime import date, datetime, time, timedelta
from typing import (
    Any,
    Callable,
    cast,
    ClassVar,
    Dict,
    Generic,
    List,
    Optional,
    Set,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import and_
from sqlalchemy.orm import Session, joinedload

from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.registry import classproperty, obj_to_dict
from zephyrex.pydantic2.fastapi import AuthType

from zephyrex.logic.AbstractLogicManager.hooks import (
    HookRegistry,
    auto_register_hooks,
    discover_hookable_methods,
    wrap_method_with_hooks,
)
from zephyrex.logic.AbstractLogicManager.models import FieldComparison

ModelT = TypeVar("ModelT", bound=BaseModel)
T = TypeVar("T")


def _escape_like(v: object) -> str:
    """Escape LIKE/ILIKE wildcard characters (%, _) in user input."""
    s = str(v) if not isinstance(v, str) else v
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Resolved type hints are deterministic per class, so memoize them keyed on the
# class object itself. ``model_registry.apply(...)`` returns a distinct bound
# class per registry, so keying on that resolved class keeps different applied
# models from colliding while collapsing the per-list/search/count
# ``get_type_hints`` recomputation to one resolution per class.
_type_hints_cache: Dict[type, Dict[str, Any]] = {}


def _cached_type_hints(cls: type) -> Dict[str, Any]:
    """Return ``get_type_hints(cls)``, memoized on the class object.

    Callers must treat the returned mapping as read-only — it is shared across
    calls and never copied.
    """
    hints = _type_hints_cache.get(cls)
    if hints is None:
        hints = get_type_hints(cls)
        _type_hints_cache[cls] = hints
    return hints


class _BoundModelDescriptor:
    """Descriptor providing registry-aware model access for BLL managers."""

    def __get__(self, instance, owner):
        if owner is None:
            return self

        model = getattr(owner, "_model", None)
        if model is None:
            if owner is AbstractBLLManager:
                return None
            raise AttributeError(f"{owner.__qualname__} does not have _model set!")

        if instance is None:
            return model

        return instance.model_registry.apply(model)


_entity_cache = None


def set_entity_cache(cache) -> None:
    """Install the Valkey entity cache. Called by ``EXT_DatabaseMemory.wire_framework_backends()``."""
    global _entity_cache
    _entity_cache = cache


def get_entity_cache():
    """Return the active entity cache, or None."""
    return _entity_cache


def _cache_sync_run(coro, timeout: float | None = 2):
    """Drive an async coroutine to completion from synchronous code.

    Shared source of truth for the sync->async bridge used across the
    framework (cache/BLL read-write paths, the Valkey replay-cache and
    rate-limit backends, and the audit-retention archive callback). When a
    loop is already running in the calling thread the coroutine is driven on
    a dedicated worker thread — bounded by ``timeout`` seconds, or unbounded
    when ``timeout`` is ``None`` — so it never re-enters the running loop
    (which ``asyncio.run`` forbids); otherwise it runs directly via
    ``asyncio.run``. Returns whatever the coroutine returns.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=timeout)
    return asyncio.run(coro)


def _fire_and_forget(coro) -> None:
    """Run an async coroutine to completion in a background daemon thread,
    WITHOUT waiting for the result — the fire-and-forget counterpart to
    :func:`_cache_sync_run`. Shared so callers (non-blocking hook dispatch and
    email provider callbacks) don't each re-roll a new-event-loop-in-a-thread
    helper. The daemon thread is not joined; error handling is the coroutine's
    own responsibility.
    """
    import asyncio
    import threading

    def _run() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(coro)
        finally:
            loop.close()

    threading.Thread(target=_run, daemon=True).start()


class AbstractBLLManager(ABC, Generic[ModelT]):
    _model = None

    # Human-readable label for 404 messages (defaults to class name if None)
    _entity_label: ClassVar[Optional[str]] = None

    _cache_disabled: ClassVar[bool] = False
    _cache_index_fields: ClassVar[Set[str]] = set()
    _cache_ttl: ClassVar[Optional[int]] = None
    _response_cache_ttl: ClassVar[Optional[int]] = None
    _response_cache_vary_on_user: ClassVar[bool] = True

    # Search transformer functions
    search_transformers: Dict[str, Callable] = {}

    # Router configuration - can be overridden by subclasses
    endpoint_config: ClassVar[Dict[str, Any]] = {}
    custom_routes: ClassVar[List[Dict[str, Any]]] = []
    nested_resources: ClassVar[Dict[str, Any]] = {}
    route_auth_overrides: ClassVar[Dict[str, AuthType]] = {}

    # Manager factory configuration - can be overridden by subclasses
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = None
    requires_root_access: ClassVar[bool] = False

    # Static class-level access, must have `model_registry.apply()` run on to be registry-sensitive.
    # Instance and class-level access via descriptor. Instances receive registry-bound models,
    # while class-level access returns the raw model for validation helpers (e.g., Model.Create).
    Model = _BoundModelDescriptor()

    @classproperty
    def BaseModel(cls):
        return cls.Model

    # Instance object-level database entity, will automatically have the instance's model_registry and DB context applied before return.
    @property
    def DB(self):
        """Property that returns the SQLAlchemy model class from the Pydantic Model."""
        return self.Model.DB(self.model_registry.DB.manager.Base)

    def __init_subclass__(cls, **kwargs):
        """
        Automatically set up hooks when subclass is created.

        This method is called when a class inherits from AbstractBLLManager
        and sets up the hook system for the new class.
        """
        super().__init_subclass__(**kwargs)

        # Set up class-specific hook registry
        parent_registry = None
        for base in cls.__bases__:
            if hasattr(base, "_hook_registry"):
                parent_registry = base._hook_registry
                break

        cls._hook_registry = HookRegistry(parent_registry)

        # Auto-discover and wrap hookable methods
        auto_register_hooks(cls)

        # Wrap all hookable methods
        hookable_methods = discover_hookable_methods(cls)
        for method_name in hookable_methods:
            if hasattr(cls, method_name) and not hasattr(
                getattr(cls, method_name), "_original_method"
            ):
                wrapped = wrap_method_with_hooks(cls, method_name)
                setattr(cls, method_name, wrapped)

    def __init__(
        self,
        model_registry=None,
        requester_id: Optional[str] | None = None,
        target_id: Optional[str] | None = None,
        target_team_id: Optional[str] | None = None,
        parent: Optional[Any] | None = None,
    ):
        """
        Initialize the BLL manager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team (kept for backward compatibility)
            parent: Parent manager for nested operations (optional)
            model_registry: ModelRegistry instance for accessing registry-bound models (required)
        """
        self.model_registry = model_registry
        self.requester_id = requester_id
        self.target_id: Optional[str] = target_id
        self.target_team_id: Optional[str] = target_team_id
        self._target_user = None
        self._target_team = None
        self._target: Optional[Any] = None
        self._target_loaded = False
        self._parent = parent
        self.requester = None

        minimal_registry = not model_registry or not hasattr(
            model_registry, "is_committed"
        )

        if minimal_registry:
            self._register_search_transformers()
            return

        if not requester_id:
            raise HTTPException(status_code=400, detail="requester_id is required")

        if not model_registry.is_committed():
            raise ValueError(
                f"model_registry is required to be defined and committed in {self.__class__.__name__}."
            )

        from zephyrex.logic.BLL_Auth import TeamModel, UserModel

        cache = _entity_cache
        if cache is not None:
            try:
                cached_user = _cache_sync_run(cache.get_by_id("user", requester_id))
                if cached_user is not None:
                    self.requester = UserModel.model_validate(cached_user)
                    self._register_search_transformers()
                    return
            except Exception:
                pass

        Team = TeamModel.DB(self.model_registry.DB.manager.Base)
        User = UserModel.DB(self.model_registry.DB.manager.Base)

        session = self.model_registry.DB.session()
        try:
            self.requester = session.query(User).filter(User.id == requester_id).first()
        finally:
            try:
                session.close()
            except Exception as exc:
                logger.debug(
                    "Failed to close requester lookup session in %s: %s",
                    self.__class__.__name__,
                    exc,
                )
        if self.requester is None:
            raise HTTPException(
                status_code=404,
                detail=f"Requesting user with id {requester_id} not found.",
            )

        if cache is not None:
            try:
                dto_dict = (
                    self.requester.model_dump(mode="json")
                    if hasattr(self.requester, "model_dump")
                    else obj_to_dict(self.requester)
                )
                _cache_sync_run(
                    cache.put(
                        "user",
                        requester_id,
                        dto_dict,
                        {f: dto_dict.get(f) for f in ("email",) if dto_dict.get(f)},
                    )
                )
            except Exception:
                pass
        # Initialize any search transformers
        self._register_search_transformers()

    def _update_models_from_registry(self):
        """
        Update the class Model attributes to use registry-bound models with extensions.

        This method finds the registry-bound version of the manager's model and updates
        the class attributes so all methods use the extended models.
        """
        if not self.model_registry or not self.model_registry.is_committed():
            return

        # Get the manager's model class name (e.g., "UserModel" from UserManager)
        manager_name = self.__class__.__name__
        if manager_name.endswith("Manager"):
            model_name = manager_name[:-7] + "Model"  # Remove "Manager", add "Model"
        else:
            # Fallback: try to infer from the existing Model attribute
            model_name = (
                self.Model.__name__ if hasattr(self.Model, "__name__") else None
            )

        if not model_name:
            logger.debug(f"Could not determine model name for manager {manager_name}")
            return

        # Find the registry-bound model with the same name and module
        for bound_model in self.model_registry.bound_models:
            if (
                bound_model.__name__ == model_name
                and bound_model.__module__ == self.Model.__module__
            ):
                # Update the class attributes to use the registry-bound model
                self.__class__._model = bound_model
                logger.debug(
                    f"Updated {manager_name}.Model to use registry-bound {model_name}"
                )

                # Update Reference and Network models using the new programmatic approach
                if hasattr(bound_model, "Reference"):
                    self.__class__.ReferenceModel = bound_model.Reference
                    logger.debug(
                        f"Updated {manager_name}.ReferenceModel to use {model_name}.Reference"
                    )

                # For models with NetworkMixin, they have NetworkModel method
                if hasattr(bound_model, "NetworkModel"):
                    # Resolve the NetworkModel immediately since we have model_registry available
                    try:
                        resolved_network = bound_model.NetworkModel(self.model_registry)
                        self.__class__.NetworkModel = resolved_network
                        logger.debug(
                            f"Updated {manager_name}.NetworkModel to use resolved {model_name}.NetworkModel"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to resolve NetworkModel for {model_name}: {e}, keeping function reference"
                        )

                break

        # Also check if NetworkModel is still a callable and needs resolution
        # This handles cases where NetworkModel was set in __init_subclass__ as Model.NetworkModel
        if (
            callable(self.__class__.NetworkModel)
            and self.model_registry
            and hasattr(self.__class__.NetworkModel, "__name__")
            and self.__class__.NetworkModel.__name__ == "NetworkModel"
        ):
            try:
                resolved_network = self.__class__.NetworkModel(self.model_registry)
                self.__class__.NetworkModel = resolved_network
                logger.debug(f"Resolved callable NetworkModel for {manager_name}")
            except Exception as e:
                logger.warning(
                    f"Failed to resolve callable NetworkModel for {manager_name}: {e}"
                )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        db_manager = getattr(self.model_registry.DB, "manager", None)
        if db_manager and hasattr(db_manager, "cleanup_thread"):
            try:
                db_manager.cleanup_thread()
            except Exception as exc:
                logger.debug(
                    "Failed to cleanup database thread resources for %s: %s",
                    self.__class__.__name__,
                    exc,
                )

    @property  # type: ignore[no-redef]
    def DB(self):
        """Property that returns the SQLAlchemy model class from the Pydantic Model."""
        return self.Model.DB(self.model_registry.DB.manager.Base)

    @property
    def target(self) -> Any:
        """
        Lazy-loaded target record.

        Returns:
            The target entity record, loaded on first access
        """
        if not self._target_loaded and self.target_id:
            self._target = self.get(id=self.target_id)
            self._target_loaded = True
        return self._target

    @target.setter
    def target(self, value: Any) -> None:
        """
        Set target record and mark as loaded.

        Args:
            value: The target entity to set
        """
        self._target = value
        self._target_loaded = True
        if value and hasattr(value, "id"):
            self.target_id = value.id

    @property
    def target_team(self):
        """
        Get target team.

        Returns:
            The target team entity
        """

        return None

    @property
    def target_user_id(self) -> Optional[str]:
        """
        Get target user ID for backward compatibility.

        Returns:
            The target_id if set, otherwise the requester's ID
        """
        return self.target_id if self.target_id else self.requester.id  # type: ignore[union-attr]

    def _register_search_transformers(self):
        """
        Register custom search transformers for this manager.
        Override this method to register specific search transformers.

        Example:
            self.register_search_transformer('overdue', self._transform_overdue_search)
        """
        pass

    def register_search_transformer(self, field_name: str, transformer: Callable):
        """
        Register a search transformer function for a specific field.

        Args:
            field_name: The name of the field or concept to transform
            transformer: A function that takes a value and returns a list of filter conditions
        """
        self.search_transformers[field_name] = transformer

    def get_field_types(self):
        """Analyzes the Model class to categorize fields by type."""
        string_fields = []
        numeric_fields = []
        date_fields = []
        boolean_fields = []

        all_annotations = _cached_type_hints(self.model_registry.apply(self.Model))
        # Get all annotations from the model
        for field_name, field_info in all_annotations.items():
            # Handle Optional types
            actual_type = field_info
            origin = get_origin(field_info)

            if origin is Union:
                args = get_args(field_info)
                actual_type = args[0]

            # Categorize by type
            if actual_type == str:
                string_fields.append(field_name)
            elif actual_type in (int, float):
                numeric_fields.append(field_name)
            elif actual_type == bool:
                boolean_fields.append(field_name)
            elif actual_type in (date, datetime):
                date_fields.append(field_name)

        return string_fields, numeric_fields, date_fields, boolean_fields

    def build_search_filters(
        self,
        search_params: Dict[str, Any],
    ) -> List:
        """Build SQLAlchemy filters from search parameters."""
        filters = []
        string_fields, numeric_fields, date_fields, boolean_fields = (
            self.get_field_types()
        )

        for field_name, value in search_params.items():
            # Skip processing None values
            if value is None:
                continue

            # Check if we have a custom transformer for this field
            if field_name in self.search_transformers:
                search_transformer = self.search_transformers[field_name]
                custom_filters = self.search_transformers[field_name](value)

                # apply transformers only if they belong to self manager class
                if hasattr(search_transformer, "__self__"):
                    if self != search_transformer.__self__.__class__:
                        custom_filters = None

                if custom_filters:
                    if isinstance(custom_filters, list):
                        filters.extend(custom_filters)
                    else:
                        filters.append(custom_filters)
                    continue

            # If not a custom field, check if field exists in the model
            if not hasattr(self.DB, field_name):
                continue

            field = getattr(self.DB, field_name)

            # Handle string pattern matching operations
            if field_name in string_fields and isinstance(value, dict):
                field_processed = False

                if "inc" in value and value["inc"] is not None:
                    filters.append(
                        field.ilike(f"%{_escape_like(value['inc'])}%", escape="\\")
                    )
                    field_processed = True

                if "sw" in value and value["sw"] is not None:
                    filters.append(
                        field.ilike(f"{_escape_like(value['sw'])}%", escape="\\")
                    )
                    field_processed = True

                if "ew" in value and value["ew"] is not None:
                    filters.append(
                        field.ilike(f"%{_escape_like(value['ew'])}", escape="\\")
                    )
                    field_processed = True

                if "eq" in value and value["eq"] is not None:
                    filters.append(field == value["eq"])
                    field_processed = True

                if field_processed:
                    continue

            # Handle numeric comparison operators
            elif field_name in numeric_fields and isinstance(value, dict):
                conditions = []

                if "eq" in value and value["eq"] is not None:
                    conditions.append(field == value["eq"])
                if "neq" in value and value["neq"] is not None:
                    conditions.append(field != value["neq"])
                if "lt" in value and value["lt"] is not None:
                    conditions.append(field < value["lt"])
                if "gt" in value and value["gt"] is not None:
                    conditions.append(field.__gt__(value["gt"]))
                if "lteq" in value and value["lteq"] is not None:
                    conditions.append(field <= value["lteq"])
                if "gteq" in value and value["gteq"] is not None:
                    conditions.append(field >= value["gteq"])

                if conditions:
                    filters.append(and_(*conditions))
                    continue

            # Handle date field operations
            elif field_name in date_fields and isinstance(value, dict):
                conditions = []

                def _parse_date_value(v):
                    if isinstance(v, (datetime, date)):
                        return v
                    if isinstance(v, str):
                        for fmt in (
                            "%Y-%m-%dT%H:%M:%S",
                            "%Y-%m-%dT%H:%M:%S.%f",
                            "%Y-%m-%d",
                        ):
                            try:
                                return datetime.strptime(v, fmt)
                            except ValueError:
                                continue
                    return v

                if "before" in value and value["before"] is not None:
                    conditions.append(field.__lt__(_parse_date_value(value["before"])))
                if "after" in value and value["after"] is not None:
                    conditions.append(field.__gt__(_parse_date_value(value["after"])))
                if "eq" in value and value["eq"] is not None:
                    eq_value = _parse_date_value(value["eq"])
                    if isinstance(eq_value, datetime):
                        start_of_day = eq_value.replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                        start_of_next_day = start_of_day + timedelta(days=1)
                        conditions.append(
                            and_(field >= start_of_day, field < start_of_next_day)
                        )
                    elif isinstance(eq_value, date):
                        start_of_day = datetime.combine(eq_value, time.min)
                        start_of_next_day = datetime.combine(
                            eq_value + timedelta(days=1), time.min
                        )
                        conditions.append(
                            and_(field >= start_of_day, field < start_of_next_day)
                        )
                    else:
                        conditions.append(field == eq_value)
                if "on" in value and value["on"] is not None:
                    # For date equality, check for the entire day
                    # Check for SQLAlchemy date/datetime types
                    from sqlalchemy import Date, DateTime

                    if isinstance(field.type, (DateTime, Date)) or str(
                        field.type
                    ).upper() in ["DATETIME", "DATE"]:
                        on_value = value["on"]

                        # Convert string to date object if needed
                        if isinstance(on_value, str):
                            try:
                                if "T" in on_value:
                                    # Parse ISO datetime string
                                    on_datetime = datetime.fromisoformat(
                                        on_value.replace("Z", "+00:00")
                                    )
                                    on_date = on_datetime.date()
                                else:
                                    # Parse date string
                                    on_date = datetime.strptime(
                                        on_value, "%Y-%m-%d"
                                    ).date()
                            except ValueError:
                                continue
                        elif isinstance(on_value, datetime):
                            on_date = on_value.date()
                        elif isinstance(on_value, date):
                            on_date = on_value
                        else:
                            continue

                        # Create datetime objects for start and end of the day
                        start_of_day = datetime.combine(on_date, time.min)
                        # Use start of the *next* day for the upper bound (exclusive)
                        start_of_next_day = datetime.combine(
                            on_date + timedelta(days=1), time.min
                        )

                        conditions.append(
                            and_(field >= start_of_day, field < start_of_next_day)
                        )

                    else:
                        # Fallback for unexpected field types
                        conditions.append(field == value["on"])

                if conditions:
                    filters.append(and_(*conditions))
                    continue

            # Handle boolean field operations
            elif field_name in boolean_fields and isinstance(value, dict):
                if "eq" in value and value["eq"] is not None:
                    filters.append(field == value["eq"])
                    continue

            # For dictionaries that weren't handled by specific patterns,
            # extract the actual values rather than passing the dict directly
            if isinstance(value, dict):
                # Skip dictionaries that don't match our expected patterns
                continue

            # Handle direct value syntax for date fields (should behave like "on")
            if field_name in date_fields:
                # Parse string dates if needed
                parsed_value = value
                if isinstance(value, str):
                    try:
                        parsed_value = datetime.fromisoformat(
                            value.replace("Z", "+00:00")
                        )
                    except ValueError:
                        try:
                            parsed_value = date.fromisoformat(value)
                        except ValueError as e:
                            logger.warning(
                                f"Unparseable date filter value {value!r}: {e}"
                            )

                # Check if value is a datetime or date
                if isinstance(parsed_value, datetime):
                    # For datetime values, truncate microseconds and match exact second
                    truncated_value = parsed_value.replace(microsecond=0)
                    # Create a range from start to end of the second to handle microsecond differences
                    start_of_second = truncated_value
                    end_of_second = truncated_value + timedelta(seconds=1)
                    filters.append(
                        and_(field >= start_of_second, field < end_of_second)
                    )
                elif isinstance(parsed_value, date):
                    # For date values, create datetime range for the day
                    start_of_day = datetime.combine(parsed_value, time.min)
                    start_of_next_day = datetime.combine(
                        parsed_value + timedelta(days=1), time.min
                    )
                    filters.append(
                        and_(field >= start_of_day, field < start_of_next_day)
                    )
                else:
                    # Fallback for other types
                    filters.append(field == value)
            # Handle direct value syntax for boolean fields
            elif field_name in boolean_fields:
                # Direct boolean values should work the same as is_true
                filters.append(field == value)
            else:
                # Handle regular exact match (for non-dict values)
                filters.append(field == value)

        return filters

    def _parse_includes(self, include: Union[List[str], str]) -> List[str]:
        """Parse includes parameter into a list of relationship names.

        Args:
            include: List of relationships or CSV string of relationships

        Returns:
            List of relationship names with validation
        """
        if not include:
            return []

        if isinstance(include, str):
            # Handle CSV string - split on commas and strip whitespace
            include_list = [name.strip() for name in include.split(",") if name.strip()]
        elif isinstance(include, list):
            # Handle list - ensure all items are strings and not empty
            include_list = [str(name).strip() for name in include if str(name).strip()]
        else:
            # Invalid type
            return []

        MAX_INCLUDE_DEPTH = 4

        validated_includes = []
        for include_name in include_list:
            if include_name and all(c.isalnum() or c in "._" for c in include_name):
                depth = include_name.count(".") + 1
                if depth <= MAX_INCLUDE_DEPTH:
                    validated_includes.append(include_name)

        return validated_includes

    def _parse_fields(self, fields: Union[List[str], str]) -> List[str]:
        """Parse fields parameter into a list of field names.

        Args:
            fields: List of field names or CSV string of field names

        Returns:
            List of field names with validation
        """
        if not fields:
            return []

        if isinstance(fields, str):
            # Handle CSV string - split on commas and strip whitespace
            fields_list = [name.strip() for name in fields.split(",") if name.strip()]
        elif isinstance(fields, list):
            # Handle list - ensure all items are strings and not empty
            fields_list = [str(name).strip() for name in fields if str(name).strip()]
        else:
            # Invalid type
            return []

        # Validate field names (basic validation for now)
        validated_fields = []
        for field_name in fields_list:
            # Basic validation: ensure it contains only alphanumeric and underscore characters
            if field_name and all(c.isalnum() or c == "_" for c in field_name):
                validated_fields.append(field_name)

        return validated_fields

    def validate_fields(
        self, fields: Optional[Union[List[str], str]]
    ) -> Optional[List[str]]:
        """
        Validate that requested fields exist in the model.
        Returns the processed fields list.
        Raises HTTPException 422 if invalid fields are provided.

        Args:
            fields: List of field names or CSV string of field names

        Returns:
            Processed list of valid field names, or None/empty list if no fields provided

        Raises:
            HTTPException: 422 status if invalid fields are detected
        """
        if not fields:
            return fields  # type: ignore[return-value]

        # Parse fields - handle both CSV strings and lists
        fields_list = self._parse_fields(fields)

        if not fields_list:
            return fields_list

        # Get valid field names from the model
        valid_fields = set(self.Model.model_fields.keys())

        # Check for invalid fields
        provided_fields = set(fields_list)
        invalid_fields = provided_fields - valid_fields

        if invalid_fields:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "Invalid fields provided",
                    "invalid_fields": sorted(list(invalid_fields)),
                    "valid_fields": sorted(list(valid_fields)),
                },
            )

        return fields_list

    def validate_includes(
        self, includes: Optional[Union[List[str], str]]
    ) -> Optional[List[str]]:
        """
        Validate that requested includes exist as valid relationships for the model.

        This is a lightweight wrapper that uses generate_joins() for validation
        without actually generating the join options.
        """
        if not includes:
            return includes  # type: ignore[return-value]

        includes_list = self._parse_includes(includes)

        if not includes_list:
            return includes_list

        # Use generate_joins() for validation - it will raise HTTPException if invalid
        # We discard the result since we only care about validation here
        try:
            self.generate_joins(self.DB, includes_list)
        except HTTPException:
            # Re-raise the 422 error from generate_joins
            raise

        return includes_list

    def _resolve_load_only_columns(self, fields_list: List[str]) -> List[Any]:
        """Resolve field names to SQLAlchemy load_only compatible attributes."""
        mapper = getattr(self.DB, "__mapper__", None)
        if not mapper:
            return []

        mapper_attrs = getattr(mapper, "attrs", {})
        mapper_keys = (
            set(mapper_attrs.keys()) if hasattr(mapper_attrs, "keys") else set()
        )
        column_field_keys: Set[str] = set()
        if hasattr(mapper, "column_attrs"):
            try:
                column_field_keys = {prop.key for prop in mapper.column_attrs}
            except Exception:
                column_field_keys = set()
        relationship_keys: Set[str] = set()
        if hasattr(mapper, "relationships"):
            try:
                relationship_keys = set(mapper.relationships.keys())
            except Exception:
                relationship_keys = set()

        if not column_field_keys and mapper_keys:
            # Fallback: treat mapper attribute keys that are not relationships as columns
            column_field_keys = {
                key for key in mapper_keys if key not in relationship_keys
            }

        resolved: List[Any] = []
        invalid: List[str] = []
        seen: Set[str] = set()

        for field_name in fields_list:
            if field_name in mapper_keys and hasattr(self.DB, field_name):
                if field_name in column_field_keys:
                    if field_name not in seen:
                        resolved.append(getattr(self.DB, field_name))
                        seen.add(field_name)
                elif field_name in relationship_keys:
                    continue
                else:
                    invalid.append(field_name)
            else:
                invalid.append(field_name)

        # Ensure core audit fields remain available for DTO validation
        required_field_names = [
            "id",
            "created_at",
            "created_by_user_id",
            "updated_at",
            "updated_by_user_id",
        ]

        for required_name in required_field_names:
            if (
                required_name in mapper_keys
                and required_name in column_field_keys
                and hasattr(self.DB, required_name)
                and required_name not in seen
            ):
                resolved.append(getattr(self.DB, required_name))
                seen.add(required_name)

        # Ensure required fields from the Pydantic model remain available
        model_required_fields: Set[str] = set()
        model_class = getattr(self, "Model", None)
        model_fields = getattr(model_class, "model_fields", {}) if model_class else {}
        for field_name, field_info in model_fields.items():
            if hasattr(field_info, "is_required") and callable(field_info.is_required):
                if field_info.is_required():
                    model_required_fields.add(field_name)

        for required_name in model_required_fields:
            if (
                required_name in mapper_keys
                and required_name in column_field_keys
                and hasattr(self.DB, required_name)
                and required_name not in seen
            ):
                resolved.append(getattr(self.DB, required_name))
                seen.add(required_name)

        if invalid:
            raise ValueError(
                f"Invalid fields for {self.DB.__name__}: {', '.join(invalid)}"
            )

        return resolved

    @staticmethod
    def generate_joins(model_class, include_fields):
        """Generate join loads based on specified include fields.

        Args:
            model_class: SQLAlchemy model class
            include_fields: List of relationship names, supports dot notation for nested relationships

        Returns:
            List of SQLAlchemy joinedload options
        """
        """Generate join loads based on specified include fields."""
        from sqlalchemy.orm import RelationshipProperty
        from zephyrex.lib.Logging import logger
        from zephyrex.lib.Logging import logger

        joins = []
        invalid_includes = []
        valid_relationships = []

        # Collect all valid relationships - try multiple detection methods
        try:
            # Method 1: Check __mapper__ (SQLAlchemy 1.x and 2.x)
            if hasattr(model_class, "__mapper__"):
                mapper = model_class.__mapper__
                if hasattr(mapper, "relationships"):
                    for rel_name in mapper.relationships.keys():
                        valid_relationships.append(rel_name)
        except Exception as e:
            logger.debug(f"Could not get relationships from __mapper__: {e}")

        # Method 2: Check via dir() and property inspection (fallback)
        if not valid_relationships:
            for attr_name in dir(model_class):
                if attr_name.startswith("_"):
                    continue
                try:
                    attr = getattr(model_class, attr_name)
                    # Check if it's a SQLAlchemy relationship
                    if hasattr(attr, "property"):
                        if isinstance(attr.property, RelationshipProperty):
                            valid_relationships.append(attr_name)
                        elif hasattr(attr.property, "mapper"):
                            valid_relationships.append(attr_name)
                except Exception:
                    continue

        # Remove duplicates
        valid_relationships = sorted(list(set(valid_relationships)))

        # Helper: resolve a single attribute name to an actual relationship attribute
        def _resolve_relationship_attribute(cls, name):
            """Try to resolve a relationship attribute on cls for the given include name.

            Resolution strategy:
            1. If attribute exists on the class and is a relationship, return it.
            2. Inspect SQLAlchemy mapper relationships and try to match by relationship key.
            3. If not found, look for a relationship whose local FK column matches '{name}_id'.
            Returns the attribute descriptor or None.
            """
            # 1) direct attribute check
            try:
                if hasattr(cls, name):
                    candidate = getattr(cls, name)
                    if hasattr(candidate, "property") and hasattr(
                        candidate.property, "mapper"
                    ):
                        return candidate
            except Exception:
                # fall through to mapper-based resolution
                pass

            # 2) mapper-based resolution
            try:
                from sqlalchemy.inspection import inspect as sa_inspect

                mapper = sa_inspect(cls)
                # Try to find relationship by key first
                for rel in mapper.relationships:
                    if rel.key == name:
                        return getattr(cls, rel.key)

                # 3) Try to match by local foreign key column name (e.g., created_by_user -> created_by_user_id)
                fk_name = f"{name}_id"
                for rel in mapper.relationships:
                    # rel.local_columns is a set of Column objects
                    for col in getattr(rel, "local_columns", set()):
                        try:
                            col_name = getattr(col, "name", getattr(col, "key", None))
                        except Exception:
                            col_name = None
                        if col_name == fk_name or col_name == name:
                            return getattr(cls, rel.key)
                # 4) If no relationship found, but there is a FK column named fk_name, attempt to create a dynamic relationship
                #    that points to the referenced table's model. This creates a view-only relationship on the class
                #    so joinedload can be used for includes like 'created_by_user' when only '<name>_id' exists.
                try:
                    # Try to access table column object
                    col_obj = None
                    table = getattr(cls, "__table__", None)
                    if table is not None and fk_name in table.c:
                        col_obj = table.c[fk_name]
                    else:
                        # Try InstrumentedAttribute on class
                        candidate = getattr(cls, fk_name, None)
                        if candidate is not None and hasattr(candidate, "property"):
                            # attempt to pull column from descriptor
                            cols = getattr(candidate.property, "columns", None)
                            if cols:
                                col_obj = list(cols)[0]

                    if col_obj is not None and getattr(col_obj, "foreign_keys", None):
                        # Get referenced table name from the first FK
                        fk_iter = iter(col_obj.foreign_keys)
                        first_fk = next(fk_iter, None)
                        if first_fk is not None and hasattr(first_fk, "column"):
                            ref_table = getattr(first_fk.column, "table", None)
                            ref_table_name = getattr(ref_table, "name", None)
                            if ref_table_name:
                                try:
                                    import stringcase
                                    from zephyrex.lib.Environment import inflection

                                    # Derive candidate class names (likely Pydantic model names -> SQLAlchemy model classes)
                                    singular = (
                                        inflection.singular_noun(ref_table_name)
                                        if hasattr(inflection, "singular_noun")
                                        else None
                                    )
                                    if not singular:
                                        # fallback: strip trailing 's' if present
                                        singular = (
                                            ref_table_name[:-1]
                                            if ref_table_name.endswith("s")
                                            else ref_table_name
                                        )

                                    candidate_class = (
                                        stringcase.pascalcase(singular) + "Model"
                                    )
                                    # Create a view-only relationship using the candidate class name string
                                    from sqlalchemy.orm import (
                                        relationship as sa_relationship,
                                    )

                                    rel_attr = sa_relationship(
                                        candidate_class,
                                        foreign_keys=[getattr(cls, fk_name)],
                                        viewonly=True,
                                    )
                                    setattr(cls, name, rel_attr)
                                    return getattr(cls, name)
                                except Exception:
                                    # If dynamic relationship creation fails, ignore and continue
                                    pass
                except Exception:
                    # ignore any errors in dynamic relationship creation
                    pass
            except Exception:
                pass

            return None

        for field in include_fields:
            try:
                # Handle nested includes (e.g., 'user_teams.team.roles')
                if "." in field:
                    parts = field.split(".")

                    # Resolve first part to an attribute (relationship)
                    first_attr = _resolve_relationship_attribute(model_class, parts[0])
                    if not first_attr:
                        logger.warning(
                            f"Relationship '{parts[0]}' not found on {model_class.__name__}"
                        )
                        continue

                    current_join = joinedload(first_attr)
                    # Drill down into nested model class
                    try:
                        current_model_class = first_attr.property.mapper.class_
                    except Exception:
                        logger.warning(
                            f"Could not resolve mapper for relationship '{parts[0]}' on {model_class.__name__}"
                        )
                        continue

                    for part in parts[1:]:
                        nested_attr = _resolve_relationship_attribute(
                            current_model_class, part
                        )
                        if (
                            nested_attr
                            and hasattr(nested_attr, "property")
                            and hasattr(nested_attr.property, "mapper")
                        ):
                            current_join = current_join.joinedload(nested_attr)
                            current_model_class = nested_attr.property.mapper.class_
                        else:
                            logger.warning(
                                f"Relationship '{part}' not found on {current_model_class.__name__}"
                            )
                            break
                    else:
                        joins.append(current_join)

                else:
                    # Simple include - try to resolve to a relationship attribute
                    attr = _resolve_relationship_attribute(model_class, field)
                    if attr is not None:
                        joins.append(joinedload(attr))
                    else:
                        logger.warning(
                            f"Relationship '{field}' not found on {model_class.__name__}"
                        )

            except (AttributeError, TypeError) as e:
                logger.warning(
                    f"Error processing include field '{field}' on {model_class.__name__}: {e}"
                )
                continue

        return joins

    @property
    def db(self) -> Session:
        """Property that returns an active database session from ModelRegistry."""
        return self.model_registry.DB.session()  # type: ignore[no-any-return]

    def create_validation(self, entity):
        """Override this method to add validation logic for entity creation."""
        pass

    def update_validation(self, entity):
        """Override this method to add validation logic for entity update."""
        pass

    def delete_validation(self, entity):
        """Override this method to add validation logic for entity deletion."""
        pass

    def search_validation(self, params):
        """Override this method to add validation logic for entity search."""
        pass

    def create(self, **kwargs) -> Union[ModelT, List[ModelT]]:
        """Create one or more entities."""
        # Handle single entity or list of entities
        if "entities" in kwargs and isinstance(kwargs["entities"], list):
            entities = kwargs.pop("entities")
            results: List[ModelT] = []
            for entity_data in entities:
                # Merge entity data with remaining kwargs
                entity_kwargs = {**kwargs, **entity_data}
                results.append(self._create_single_entity(**entity_kwargs))
            return results
        else:
            return cast(ModelT, self._create_single_entity(**kwargs))

    # Fields a client must never be able to set on Create/Update bodies.
    # The server is the sole authority on identity and audit timestamps —
    # honouring them from the request would let a malicious or sloppy
    # caller spoof ownership and break the audit trail.
    _SERVER_CONTROLLED_AUDIT_FIELDS: ClassVar[tuple] = (
        "id",
        "created_at",
        "updated_at",
        "deleted_at",
        "created_by_user_id",
        "updated_by_user_id",
        "deleted_by_user_id",
    )

    # Per-manager opt-in: fields whose value must equal ``self.requester.id``
    # when supplied by a non-root caller. Subclasses override to lock down
    # ownership-claiming fields exposed in their ``Create`` schemas (e.g.
    # ``user_id`` on a notification). ROOT_ID and SYSTEM_ID may set these
    # fields freely so administrative imports keep working.
    _CALLER_OWNED_FIELDS: ClassVar[tuple] = ()

    @classmethod
    def _strip_server_controlled_fields(cls, kwargs: Dict[str, Any]) -> None:
        """Remove audit/identity fields a client should never be able to set."""
        for banned in cls._SERVER_CONTROLLED_AUDIT_FIELDS:
            kwargs.pop(banned, None)

    def _enforce_caller_owned_fields(self, kwargs: Dict[str, Any]) -> None:
        """Reject Create/Update calls that try to claim another user's ID.

        For each field in ``_CALLER_OWNED_FIELDS``: if the caller supplied
        a value that is not the requester's own id, the call is rejected
        with 403. ROOT_ID and SYSTEM_ID bypass this check so server-side
        imports and admin tooling can still set arbitrary owners.

        ``None`` values pass through (they fall back to the default
        target-id resolution path).
        """
        if not self._CALLER_OWNED_FIELDS:
            return
        try:
            from zephyrex.database.StaticPermissions import (
                is_root_id,
                is_system_id,
            )
        except ImportError:
            is_root_id = lambda _id: False  # noqa: E731
            is_system_id = lambda _id: False  # noqa: E731
        requester_id = getattr(self.requester, "id", None)
        if is_root_id(requester_id) or is_system_id(requester_id):
            return
        for field in self._CALLER_OWNED_FIELDS:
            supplied = kwargs.get(field)
            if supplied is None:
                continue
            if supplied != requester_id:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Cannot set {field}={supplied!r}: callers may only "
                        f"claim ownership for themselves"
                    ),
                )

    def _create_single_entity(self, **kwargs) -> Any:
        """Create a single entity."""
        # Store original kwargs to preserve hook modifications
        # NOTE: server-controlled audit fields are stripped before this copy
        # so hooks cannot accidentally re-introduce a client-supplied id or
        # spoofed created_by_user_id.
        self._strip_server_controlled_fields(kwargs)
        self._enforce_caller_owned_fields(kwargs)
        original_kwargs = kwargs.copy()

        args = self.model_registry.apply(self.Model).Create(**kwargs)
        self.create_validation(args)

        # Convert arguments to dictionary, excluding unset values
        create_args = {
            k: v
            for k, v in args.model_dump(exclude_unset=True).items()
            if v is not None or k == "user_id"  # Keep user_id even if None
        }

        # **CRITICAL**: Preserve hook-modified arguments that may not be in the Pydantic schema
        # This ensures attributes like 'hook_processed' added by hooks are preserved
        for key, value in original_kwargs.items():
            if key not in create_args and not hasattr(
                self.model_registry.apply(self.Model).Create, key
            ):
                # Skip hook-related parameters that shouldn't be passed to database
                if key in ["hook_processed"]:
                    continue
                # Only add if it's not already in create_args and not a valid Pydantic field
                # This preserves hook additions while avoiding conflicts
                create_args[key] = value

        # Check if the database class has a user_id column and add target_id if it does
        # Only add user_id if it wasn't explicitly set in the original kwargs
        if hasattr(self.DB, "user_id") and "user_id" not in kwargs:
            create_args["user_id"] = self.target_id

        entity = self.DB.create(
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(self.Model),
            **create_args,
        )

        cache = _entity_cache
        if cache is not None and not self._cache_disabled and entity is not None:
            try:
                dto_dict = (
                    entity.model_dump(mode="json")
                    if hasattr(entity, "model_dump")
                    else obj_to_dict(entity)
                )
                eid = dto_dict.get("id")
                if eid:
                    index_vals = {
                        f: dto_dict.get(f)
                        for f in self._cache_index_fields
                        if f in dto_dict and dto_dict.get(f)
                    }
                    _cache_sync_run(
                        cache.put(
                            self.DB.__tablename__, eid, dto_dict, index_vals or None
                        )
                    )
            except Exception:
                pass

        return entity

    def get(
        self,
        include: Optional[Union[List[str], str]] | None = None,
        fields: Optional[Union[List[str], str]] | None = None,
        **kwargs,
    ) -> ModelT:
        """Get an entity with optional included relationships.

        Validates fields/includes, generates join options, delegates to
        ``DB.get``, and raises 404 when the record is not found.  Subclasses
        that need only pre- or post-processing can call ``super().get()``
        instead of reimplementing the whole pipeline.

        The 404 detail uses ``_entity_label`` (falls back to the class name).

        Args:
            include: List of relationships to include, or CSV string of relationships.
                    Supports nested relationships with dot notation (e.g., 'user_teams.team.roles')
            fields: List of specific fields to include in response, or CSV string of field names
            **kwargs: Additional parameters to pass to the database get method

        Returns:
            Entity with included relationships loaded

        Raises:
            HTTPException: 404 when the entity does not exist
        """
        from fastapi import status

        entity_id = kwargs.get("id")
        cache = _entity_cache
        if (
            cache is not None
            and not self._cache_disabled
            and entity_id
            and not include
            and not fields
        ):
            try:
                cached = _cache_sync_run(
                    cache.get_by_id(self.DB.__tablename__, entity_id)
                )
                if cached is not None:
                    return cast(
                        ModelT,
                        self.model_registry.apply(self.Model).model_validate(cached),
                    )
            except Exception:
                pass

        options = []

        fields_list = self.validate_fields(fields)

        if include:
            include_list = self._parse_includes(include)
            if include_list:
                options = self.generate_joins(self.DB, include_list)
        if fields_list:
            from sqlalchemy.orm import load_only

            columns = self._resolve_load_only_columns(fields_list)
            if columns:
                options.append(load_only(*columns))

        db_kwargs = {k: v for k, v in kwargs.items() if k not in ["hook_processed"]}

        result = self.DB.get(
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.Model,
            options=options,
            **db_kwargs,
        )

        if result is None:
            label = self._entity_label or self.__class__.__name__.replace("Manager", "")
            eid = kwargs.get("id") or next(
                (v for k, v in kwargs.items() if k.endswith("_id")), "unknown"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{label} with ID '{eid}' not found",
            )

        if (
            cache is not None
            and not self._cache_disabled
            and entity_id
            and not include
            and not fields
        ):
            try:
                dto_dict = (
                    result.model_dump(mode="json")
                    if hasattr(result, "model_dump")
                    else obj_to_dict(result)
                )
                index_vals = {
                    f: dto_dict.get(f)
                    for f in self._cache_index_fields
                    if f in dto_dict and dto_dict.get(f)
                }
                _cache_sync_run(
                    cache.put(
                        self.DB.__tablename__, entity_id, dto_dict, index_vals or None
                    )
                )
            except Exception:
                pass

        return cast(ModelT, result)

    def count(self, **kwargs) -> int:
        """Count entities matching the given filters."""
        kwargs.pop("hook_processed", None)
        simple = {k: v for k, v in kwargs.items() if not isinstance(v, dict)}
        complex_params = {k: v for k, v in kwargs.items() if isinstance(v, dict)}
        filters = self.build_search_filters(complex_params) if complex_params else []
        return cast(
            int,
            self.DB.count(
                requester_id=self.requester.id,  # type: ignore[union-attr]
                model_registry=self.model_registry,
                filters=filters,
                **simple,
            ),
        )

    def list(
        self,
        include: Optional[Union[List[str], str]] | None = None,
        fields: Optional[Union[List[str], str]] | None = None,
        sort_by: Optional[str] | None = None,
        sort_order: Optional[str] = "asc",
        filters: Optional[List[Any]] | None = None,
        limit: Optional[int] | None = None,
        offset: Optional[int] | None = None,
        page: Optional[int] | None = None,
        page_size: Optional[int] | None = None,
        pageSize: Optional[int] | None = None,
        return_type: str = "dto",
        **kwargs,
    ) -> List[ModelT]:
        """List entities with optional included relationships.

        Pagination supports negative page numbers: page=-1 returns the
        last page, page=-2 the second-to-last, etc.
        """
        if pageSize is not None and page_size is None:
            page_size = pageSize
        if page is not None and page_size is not None:
            limit = page_size
            if page < 0:
                total = self.count(**kwargs)
                total_pages = max(1, -(-total // page_size))
                resolved = total_pages + 1 + page
                offset = max(0, (resolved - 1) * page_size)
            else:
                offset = (page - 1) * page_size

        options = []
        order_by = None
        # Separate kwargs for simple filter_by and complex dicts for build_search_filters
        simple_kwargs = {}
        complex_search_params = {}
        for key, value in kwargs.items():
            # Skip hook-related parameters
            if key in ["hook_processed"]:
                continue
            if isinstance(value, dict):
                complex_search_params[key] = value
            else:
                simple_kwargs[key] = value

        if include:
            # Parse includes - handle both CSV strings and lists
            include_list = self._parse_includes(include)
            if include_list:
                options = self.generate_joins(self.DB, include_list)
        if fields:
            from sqlalchemy.orm import load_only

            fields_list = self.validate_fields(fields)
            if fields_list:
                columns = self._resolve_load_only_columns(fields_list)
                if columns:
                    options.append(load_only(*columns))
        if sort_by:
            from sqlalchemy import asc, desc

            if hasattr(self.DB, sort_by):
                column = getattr(self.DB, sort_by)
                if sort_order.lower() == "asc":  # type: ignore[union-attr]
                    order_by = [asc(column)]
                else:
                    order_by = [desc(column)]
            else:
                valid_fields = set(self.Model.model_fields.keys())
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": f"Invalid sort_by field: '{sort_by}'",
                        "invalid_field": sort_by,
                        "valid_fields": sorted(list(valid_fields)),
                    },
                )

        # Generate filters from complex search_params only
        search_filters = self.build_search_filters(complex_search_params)
        # Combine with any explicitly passed filters
        combined_filters = filters + search_filters if filters else search_filters
        combined_filters = self._normalize_filters(combined_filters)  # type: ignore[assignment]

        self.parent_validation(simple_kwargs)

        return self.DB.list(  # type: ignore[no-any-return]
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            return_type=return_type,
            override_dto=self.model_registry.apply(self.Model),
            options=options,
            order_by=order_by,
            limit=limit,
            offset=offset,
            filters=combined_filters,  # Use combined_filters here
            **simple_kwargs,  # Pass simple_kwargs for filter_by
        )

    def search(
        self,
        include: Optional[Union[List[str], str]] | None = None,
        fields: Optional[Union[List[str], str]] | None = None,
        sort_by: Optional[str] | None = None,
        sort_order: Optional[str] = "asc",
        filters: Optional[List[Any]] | None = None,
        limit: Optional[int] | None = None,
        offset: Optional[int] | None = None,
        page: Optional[int] | None = None,
        page_size: Optional[int] | None = None,
        pageSize: Optional[int] | None = None,
        **search_params,
    ) -> List[ModelT]:
        """Search entities with optional included relationships."""
        if pageSize is not None and page_size is None:
            page_size = pageSize
        if page is not None and page_size is not None:
            limit = page_size
            offset = (page - 1) * page_size

        options = []
        order_by = None
        # Separate kwargs for simple filter_by and complex dicts for build_search_filters
        simple_kwargs = {}
        complex_search_params = {}
        for key, value in search_params.items():
            # Skip hook-related parameters
            if key in ["hook_processed"]:
                continue
            if isinstance(value, dict):
                complex_search_params[key] = value
            else:
                simple_kwargs[key] = value

        self.search_validation(simple_kwargs)

        # Extract and save return_type from simple_kwargs
        return_type = simple_kwargs.pop(
            "return_type", "dto"
        )  # Remove and save return_type

        # Convert include to SQLAlchemy joinedload options
        if include:
            # Parse includes - handle both CSV strings and lists
            include_list = self._parse_includes(include)
            if include_list:
                options = self.generate_joins(self.DB, include_list)

        # Convert fields to SQLAlchemy load_only option
        if fields:
            from sqlalchemy.orm import load_only

            fields_list = self.validate_fields(fields)
            if fields_list:
                columns = self._resolve_load_only_columns(fields_list)
                if columns:
                    options.append(load_only(*columns))

        # Convert sort_by and sort_order to SQLAlchemy order_by expression
        if sort_by:
            from sqlalchemy import asc, desc

            if hasattr(self.DB, sort_by):
                column = getattr(self.DB, sort_by)
                if sort_order.lower() == "asc":  # type: ignore[union-attr]
                    order_by = [asc(column)]
                else:
                    order_by = [desc(column)]

        # Generate filters from complex search_params only
        search_filters = self.build_search_filters(complex_search_params)
        combined_filters = filters + search_filters if filters else search_filters
        combined_filters = self._normalize_filters(combined_filters)  # type: ignore[assignment]

        # Pass the converted SQLAlchemy constructs to the DBClass.list method
        # Use combined_filters for the 'filters' arg and simple_kwargs for '**kwargs'
        return self.DB.list(  # type: ignore[no-any-return]
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            return_type=return_type,  # Use the saved value instead of hardcoding "dto"
            options=options,
            order_by=order_by,
            limit=limit,
            offset=offset,
            filters=combined_filters,  # Filters from build_search_filters
            **simple_kwargs,  # Simple equality kwargs for filter_by
        )

    def _normalize_filters(self, filters: Optional[List[Any]]) -> Optional[List[Any]]:
        if not filters:
            return filters

        normalized: List[Any] = []
        for filter_condition in filters:
            normalized.append(self._resolve_filter_condition(filter_condition))
        return normalized

    def _resolve_filter_condition(self, filter_condition: Any) -> Any:
        if isinstance(filter_condition, FieldComparison):
            column = getattr(self.DB, filter_condition.field_name)
            value = filter_condition.value
            operator = filter_condition.operator

            if operator == "eq":
                return column.is_(None) if value is None else column == value
            if operator == "ne":
                return column.is_not(None) if value is None else column != value
            if operator == "lt":
                return column < value
            if operator == "le":
                return column <= value
            if operator == "gt":
                return column > value
            if operator == "ge":
                return column >= value
            if operator == "in":
                return column.in_(value)
            if operator == "not_in":
                return (
                    column.not_in(value)
                    if hasattr(column, "not_in")
                    else ~column.in_(value)
                )
            if operator == "like":
                return column.like(_escape_like(value), escape="\\")
            if operator == "ilike":
                return column.ilike(_escape_like(value), escape="\\")
            if operator == "contains":
                return column.contains(value)
            if operator == "startswith":
                return column.startswith(value)
            if operator == "endswith":
                return column.endswith(value)
            if operator == "is":
                return column.is_(value)
            if operator == "isnot":
                return column.is_not(value)

        return filter_condition

    _require_etag: ClassVar[bool] = False

    def _compute_entity_etag(self, entity) -> str:
        """Compute a weak ETag from the entity's updated_at timestamp."""
        import hashlib

        ts = getattr(entity, "updated_at", None) or getattr(entity, "created_at", "")
        return f'W/"{hashlib.sha256(str(ts).encode()).hexdigest()[:16]}"'

    def update(self, id: str, **kwargs) -> ModelT:
        """Update an entity by ID."""
        if_match = kwargs.pop("_if_match", None)

        # Drop audit/identity fields from the inbound payload. Server-managed
        # bookkeeping (updated_at, updated_by_user_id, etc.) must not be
        # client-controllable.
        self._strip_server_controlled_fields(kwargs)
        self._enforce_caller_owned_fields(kwargs)
        logger.debug("Updating entity with ID: %s", id)
        try:
            args = self.model_registry.apply(self.Model).Update(**kwargs)
        except ValidationError as e:
            raise HTTPException(
                status_code=422,
                detail={"message": "Validation error", "details": e.errors()},
            )

        # Convert arguments to dictionary, excluding unset values
        update_args = {k: v for k, v in args.model_dump(exclude_unset=True).items()}

        # Get the entity before update (for after hooks)
        entity_before = self.get(id=id)

        if self._require_etag and not if_match:
            raise HTTPException(
                status_code=428,
                detail="Precondition Required — If-Match header missing",
            )
        if if_match and entity_before:
            current_etag = self._compute_entity_etag(entity_before)
            if if_match.strip('"') not in current_etag:
                raise HTTPException(
                    status_code=412,
                    detail="Precondition Failed — entity has been modified",
                )

        updated_entity = self.DB.update(
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(self.Model),
            new_properties=update_args,
            id=id,
        )

        cache = _entity_cache
        if cache is not None and not self._cache_disabled:
            try:
                table = self.DB.__tablename__
                old_index_vals = {
                    f: getattr(entity_before, f, None) for f in self._cache_index_fields
                }
                _cache_sync_run(
                    cache.invalidate(
                        table,
                        id,
                        {k: v for k, v in old_index_vals.items() if v} or None,
                    )
                )
                if updated_entity is not None:
                    dto_dict = (
                        updated_entity.model_dump(mode="json")
                        if hasattr(updated_entity, "model_dump")
                        else obj_to_dict(updated_entity)
                    )
                    index_vals = {
                        f: dto_dict.get(f)
                        for f in self._cache_index_fields
                        if f in dto_dict and dto_dict.get(f)
                    }
                    _cache_sync_run(cache.put(table, id, dto_dict, index_vals or None))
            except Exception:
                pass

        return cast(ModelT, updated_entity)

    def batch_update(self, items: List[Dict[str, Any]]) -> List[Any]:
        """Update multiple entities in a batch.

        Returns successfully updated entities. On partial failure,
        collects per-item errors and raises HTTPException with both
        successful and failed details so the endpoint layer can
        return 207 Multi-Status.
        """
        results = []
        errors = []

        for item in items:
            entity_id = item.get("id", "unknown")
            try:
                if not item.get("id"):
                    raise ValueError("Missing required 'id' field in batch update item")
                update_data = item.get("data", {})
                updated_entity = self.update(id=item["id"], **update_data)
                results.append(updated_entity)
            except Exception as e:
                errors.append({"id": entity_id, "error": str(e)})

        if errors:
            raise HTTPException(
                status_code=207,
                detail={
                    "message": "Partial success",
                    "successful_items": len(results),
                    "errors": errors,
                    "successful_updates": len(results),
                    "failed_updates": len(errors),
                },
            )

        return results

    def delete(self, id: str) -> None:
        """Delete an entity by ID."""
        cache = _entity_cache
        if cache is not None and not self._cache_disabled:
            try:
                old = self.get(id=id)
                old_index_vals = {
                    f: getattr(old, f, None) for f in self._cache_index_fields
                }
                _cache_sync_run(
                    cache.invalidate(
                        self.DB.__tablename__,
                        id,
                        {k: v for k, v in old_index_vals.items() if v} or None,
                    )
                )
            except Exception:
                try:
                    _cache_sync_run(cache.invalidate(self.DB.__tablename__, id))
                except Exception:
                    pass

        self.DB.delete(
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            id=id,
        )

    def batch_delete(self, ids: List[str]) -> None:
        """Delete multiple entities in a batch.

        On partial failure, raises HTTPException 207 with per-item
        results so the endpoint layer preserves successful deletes.
        """
        errors: List[Dict[str, Any]] = []
        successful = 0

        for entity_id in ids:
            try:
                self.delete(id=entity_id)
                successful += 1
            except Exception as e:
                errors.append({"id": entity_id, "error": str(e)})

        if errors:
            raise HTTPException(
                status_code=207,
                detail={
                    "message": "Partial success",
                    "errors": errors,
                    "successful_deletes": successful,
                    "failed_deletes": len(errors),
                },
            )

    # checks if parent exists by reference_id
    def parent_validation(self, args):
        """Override this method to add validation logic for parent entities."""
        if self._parent:
            ref_model = self._parent.Model.Reference
            if ref_model:
                parent_class = ref_model.__bases__[0]
                for key in parent_class.__annotations__.keys():
                    if args.get(key) is not None:
                        self._parent.get(id=args[key])


import zephyrex.logic.AbstractLogicManager.hooks as _hooks  # noqa: E402

_hooks.AbstractBLLManager = AbstractBLLManager
