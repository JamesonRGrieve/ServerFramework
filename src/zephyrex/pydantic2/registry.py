import inspect
from datetime import time
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    get_args,
)

from zephyrex.pydantic2.fastapi import generate_routers_from_model_registry

from ordered_set import OrderedSet
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import configure_mappers

from zephyrex.database.migrations.Migration import MigrationManager
from zephyrex.lib.Paths import (
    extensions_dir as _resolve_extensions_dir,
    src_dir as _resolve_src_dir,
)
from zephyrex.lib.AbstractPydantic2 import CacheManager
from zephyrex.lib.Environment import AbstractRegistry, env
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.scoped_importer import ScopedModuleImporter
from zephyrex.pydantic2.registry_utils import (
    BaseNetworkModel as BaseNetworkModel,
    PydanticUtility as PydanticUtility,
    classproperty as classproperty,
    obj_to_dict as obj_to_dict,
    validate_entity_fields as validate_entity_fields,
    validate_entity_includes as validate_entity_includes,
)


class ModelRegistry(AbstractRegistry):
    """
    Registry for managing Pydantic models and their database/API bindings.

    This class provides encapsulated model registration that allows for:
    - Per-application model sets (avoiding global state contamination)
    - Deferred processing of model extensions and dependencies
    - Isolated testing environments with different model configurations
    - Clean separation between model binding and schema generation

    The registry operates in two phases:
    1. Bind phase: Models are registered but not processed
    2. Commit phase: All models are processed, dependencies resolved, and schemas generated
    """

    def __init__(
        self,
        app_instance=None,
        database_manager=None,
        extension_registry=None,
        auto_bind_models=False,
        extensions_str="",
    ):
        """Initialize a new model registry.

        Args:
            app_instance: Optional FastAPI app instance to associate with this registry
            database_manager: Optional DatabaseManager instance to use for this registry
            extension_registry: Optional ExtensionRegistry instance containing extension models
            auto_bind_models: If True, automatically import and bind core and extension models
            extensions_str: Comma-separated list of extensions to load (used with auto_bind_models)
        """
        self.app = app_instance
        self.database_manager = database_manager
        self.extension_registry = extension_registry
        self.utility = PydanticUtility()

        # Model storage
        self.bound_models: OrderedSet[Type[BaseModel]] = OrderedSet()
        # Name -> model index kept in lockstep with ``bound_models`` so the
        # duplicate-name / duplicate-class guards in
        # ``_add_model_with_dependencies`` resolve in O(1) instead of a linear
        # scan of every bound model per add (which made binding O(B^2) overall).
        # Mutated only where ``bound_models`` is: the add in
        # ``_add_model_with_dependencies`` and the reset in ``clear``.
        self._bound_model_names: Dict[str, Type[BaseModel]] = {}
        self.extension_models: Dict[Type[BaseModel], List[Type]] = (
            {}
        )  # target -> [extensions]
        self.model_metadata: Dict[Type[BaseModel], Dict[str, Any]] = {}
        self._model_dependencies: Dict[Type[BaseModel], Set[Type[BaseModel]]] = (
            {}
        )  # model -> set of models it depends on

        # Initialize cache using CacheManager
        self._cache_manager = CacheManager()
        self._import_cache = self._cache_manager.get_cache("imports")
        # Filesystem module-discovery + dependency-ordered import subsystem
        # (#226): the registry delegates _scoped_import/_build_dependency_graph/
        # _parse_imports_and_dependencies here and keeps the "bind models" concern.
        self._scoped_importer = ScopedModuleImporter(self._import_cache)

        # Committed state
        self._locked = False
        self.db_models: Dict[Type[BaseModel], Type] = {}
        self.model_relationships: List[Tuple] = []
        self.dependency_order: List[Type[BaseModel]] = []
        self.declarative_base = None  # Will be set during commit()

        # Router and schema storage
        self.ep_routers = []
        self.gql = None

        logger.debug("Initialized new ModelRegistry")

        # Auto-bind models if requested
        if auto_bind_models:
            self._auto_bind_models(extensions_str)

    def apply(self, type: Type) -> Type:
        if type is None:
            raise TypeError(f"Cannot apply registry to None type")
        # print(f"BEFORE APPLY: {list(type.model_fields.keys())}")
        new_type = next(
            (
                possible_type
                for possible_type in self.bound_models
                if possible_type.__name__ == type.__name__
            ),
            None,
        )
        # print(f"AFTER APPLY: {list(new_type.model_fields.keys())}")
        if not new_type:
            raise TypeError(f"No matching type found in registry for {type.__name__}!")
        return new_type

    def _generate_network_class(self, model):
        """
        Generate the static Network class for this Pydantic model.

        The Network class contains inner classes for different REST operations:
        - POST: For creating new entities (with include/fields support)
        - PUT: For updating existing entities (with include/fields support)
        - PATCH: For partial updates (with include/fields support, if model has Patch class)
        - SEARCH: For search/filter operations (with include/fields support)
        - GET: For single entity query parameters (include/fields validation)
        - LIST: For list/pagination query parameters (include/fields validation)
        - ResponseSingle: For single entity responses
        - ResponsePlural: For list responses
        """
        from typing import List, Optional, Union, get_origin

        import stringcase
        from pydantic import BaseModel, Field

        from zephyrex.lib.Environment import inflection
        from zephyrex.lib.Logging import logger

        # Get the model name for the network fields (snake_case)
        model_name = model.__name__
        if model_name.endswith("Model"):
            model_name = model_name[:-5]  # Remove 'Model' suffix
        field_name = stringcase.snakecase(model_name)
        logger.debug(f"_generate_network_class: model={model}, field_name={field_name}")

        # Create the Network class statically
        network_model_name = f"{model.__name__.replace('Model', '')}Network"
        network_attrs = {}

        create_model = (
            getattr(model, "Create", model) if hasattr(model, "Create") else model
        )
        network_attrs["POST"] = type(
            "POST",
            (BaseNetworkModel,),
            {
                "__annotations__": {field_name: create_model},
                "__module__": model.__module__,
            },
        )

        update_model = (
            getattr(model, "Update", model) if hasattr(model, "Update") else model
        )
        network_attrs["PUT"] = type(
            "PUT",
            (BaseNetworkModel,),
            {
                "__annotations__": {field_name: update_model},
                "__module__": model.__module__,
            },
        )

        if hasattr(model, "Patch"):
            patch_model = getattr(model, "Patch")
            network_attrs["PATCH"] = type(
                "PATCH",
                (BaseNetworkModel,),
                {
                    "__annotations__": {field_name: patch_model},
                    "__module__": model.__module__,
                },
            )

        # SEARCH class - uses Search if available, otherwise create a basic search
        if hasattr(model, "Search"):
            search_model = getattr(model, "Search")
        else:
            # Create a basic search model with optional versions of main fields
            search_annotations = {}
            if hasattr(model, "model_fields"):
                for field_name_inner, field_info in model.model_fields.items():
                    # Make all search fields optional
                    field_type = field_info.annotation
                    # Handle Union types (Optional fields)
                    if get_origin(field_type) is Union:
                        search_annotations[field_name_inner] = field_type
                    else:
                        search_annotations[field_name_inner] = Optional[field_type]

            search_model = type(
                "Search",
                (BaseModel,),
                {
                    "__annotations__": search_annotations,
                    "__module__": model.__module__,
                },
            )

        network_attrs["SEARCH"] = type(
            "SEARCH",
            (BaseNetworkModel,),
            {
                "__annotations__": {field_name: search_model},
                "__module__": model.__module__,
            },
        )

        # GET class - for single entity query parameters (no id field - that's a path param)
        network_attrs["GET"] = type(
            "GET",
            (BaseNetworkModel,),
            {
                "__annotations__": {},  # Only include/fields from BaseNetworkModel
                "__module__": model.__module__,
            },
        )

        # LIST class - for list/pagination query parameters
        # Build annotations and class attributes for LIST
        list_annotations = {
            "offset": int,
            "limit": int,
            "page": Optional[int],
            "page_size": Optional[int],
            "sort_by": Optional[str],
            "sort_order": Optional[str],
        }
        list_attrs = {
            "__module__": model.__module__,
            "model_config": ConfigDict(populate_by_name=True, extra="ignore"),
            "offset": Field(
                0, ge=0, description="Number of items to skip for pagination"
            ),
            "limit": Field(
                1000, ge=1, le=1000, description="Maximum number of items to return"
            ),
            "page": Field(
                None, ge=1, description="Page number (1-indexed) for pagination"
            ),
            "page_size": Field(
                None,
                ge=1,
                le=1000,
                description="Number of items per page (alternative to limit/offset)",
                validation_alias="pageSize",
            ),
            "sort_by": Field(None, description="Field to sort results by"),
            "sort_order": Field(
                "asc",
                pattern="^(asc|desc)$",
                description="Sort direction (asc or desc)",
            ),
        }

        # Add model fields as optional filter parameters
        # This allows filtering like GET /entity?id=xxx&name=yyy
        model_fields = getattr(model, "model_fields", {})

        # Define types that are safe for query parameter filtering
        # These are simple scalar types that can be easily parsed from query strings
        def is_filterable_type(field_type) -> bool:
            """Check if a field type is suitable for query parameter filtering."""
            # Get the origin type for generic types (e.g., Optional[str] -> Union)
            origin = get_origin(field_type)

            # Handle Optional types (Union[X, None])
            if origin is Union:
                args = get_args(field_type)
                # Check if it's Optional (Union with None)
                non_none_args = [arg for arg in args if arg is not type(None)]
                if len(non_none_args) == 1:
                    # It's Optional[X], check the inner type
                    return is_filterable_type(non_none_args[0])
                return False

            # Handle List types - not filterable via simple query params
            if origin in (list, List):
                return False

            # Handle Dict types - not filterable via simple query params
            if origin in (dict, Dict):
                return False

            # Simple scalar types that are safe for filtering
            filterable_types = (str, int, float, bool)

            # Check if it's a simple filterable type
            if field_type in filterable_types:
                return True

            # Check for string subclasses (like UUID which is often str)
            try:
                if isinstance(field_type, type) and issubclass(field_type, str):
                    return True
            except TypeError:
                pass

            return False

        for field_name_inner, field_info in model_fields.items():
            # Skip fields that are already defined (like offset, limit, etc.)
            if field_name_inner in list_annotations:
                continue

            field_type = field_info.annotation

            # Only add filterable types
            if is_filterable_type(field_type):
                # Make the field Optional for filtering
                if get_origin(field_type) is Union:
                    # Already Optional, use as-is
                    list_annotations[field_name_inner] = field_type
                else:
                    # Wrap in Optional
                    list_annotations[field_name_inner] = Optional[field_type]

                # Add Field with description
                field_description = (
                    field_info.description
                    if field_info.description
                    else f"Filter by {field_name_inner}"
                )
                list_attrs[field_name_inner] = Field(
                    None, description=field_description
                )

        list_attrs["__annotations__"] = list_annotations
        network_attrs["LIST"] = type(
            "LIST",
            (BaseNetworkModel,),
            list_attrs,
        )

        # ResponseSingle class - wraps the base model
        logger.debug(f"ResponseSingle creation: field_name={field_name}, model={model}")
        network_attrs["ResponseSingle"] = type(
            "ResponseSingle",
            (BaseModel,),
            {
                "__annotations__": {field_name: model},
                "__module__": model.__module__,
            },
        )
        logger.debug(
            f"ResponseSingle created: {network_attrs['ResponseSingle'].model_fields}"
        )

        # ResponsePlural class - wraps a list of the base model
        # Use inflection for proper pluralization
        plural_field_name = inflection.plural(field_name)
        network_attrs["ResponsePlural"] = type(
            "ResponsePlural",
            (BaseModel,),
            {
                "__annotations__": {plural_field_name: List[model]},
                "__module__": model.__module__,
            },
        )

        # Create the main Network class and attach it to the model
        network_class = type(
            network_model_name,
            (),
            {
                **network_attrs,
                "__module__": model.__module__,
            },
        )

        # Attach the Network class to the model
        model.Network = network_class

        logger.debug(
            f"Generated Network class for {model.__name__}: {network_model_name}"
        )

    def _auto_bind_models(self, extensions_str: str = ""):
        """Automatically import and bind core and extension models.

        Args:
            extensions_str: Comma-separated list of extensions to load
        """
        import sys

        # Prepare scopes - always include core logic. Scope identifiers
        # are bare-rooted ("logic", "extensions.auth_mfa") because
        # ``_scoped_import`` joins them onto ``src_dir`` (which already
        # IS ``<src>/zephyrex`` after Item 60). The canonical
        # ``zephyrex.`` prefix is added back at module-name
        # construction time for sys.modules registration.
        scopes = ["logic"]

        # Add extension scopes if extensions are configured
        if extensions_str:
            extension_names = [
                name.strip() for name in extensions_str.split(",") if name.strip()
            ]
            if extension_names:
                extension_scopes = [
                    f"extensions.{ext_name}" for ext_name in extension_names
                ]
                scopes.extend(extension_scopes)
                logger.debug(f"Auto-binding models for scopes: {scopes}")

        # Use _scoped_import to import BLL modules
        try:
            imported_modules, import_errors = self._scoped_import(
                file_type="BLL", scopes=scopes
            )

            if import_errors:
                logger.warning(f"Errors importing BLL modules: {import_errors}")

            # Process imported modules and bind models
            for module_name in imported_modules:
                try:
                    module = sys.modules.get(module_name)
                    if module:
                        # Determine if this is an extension module
                        is_extension_module = "extensions." in module_name

                        # Look for Pydantic models and manager classes in the module
                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)

                            # Check for domain models - must inherit from ApplicationModel and have ModelMeta metaclass
                            # but exclude ApplicationModel itself and other mixin base classes
                            if (
                                hasattr(attr, "__bases__")
                                and any(
                                    base.__name__ == "ApplicationModel"
                                    for base in attr.__mro__
                                )
                                and hasattr(attr, "__annotations__")
                                and hasattr(attr, "__class__")
                                and hasattr(attr.__class__, "__name__")
                                and attr.__class__.__name__ == "ModelMeta"
                                and attr.__name__ != "ApplicationModel"
                                and not attr.__name__.endswith("MixinModel")
                            ):
                                # Check if this is an extension model (for extension modules)
                                if (
                                    is_extension_module
                                    and hasattr(attr, "_is_extension_model")
                                    and hasattr(attr, "_extension_target")
                                ):
                                    # This is an extension model - bind it as an extension
                                    try:
                                        target_model = attr._extension_target
                                        self.bind_extension(target_model, attr)
                                        logger.debug(
                                            f"Bound extension {attr.__name__} to {target_model.__name__} from {module_name}"
                                        )
                                    except Exception as e:
                                        logger.debug(
                                            f"Could not bind extension {attr.__name__}: {e}"
                                        )
                                else:
                                    # This is a regular Pydantic model - bind it normally
                                    try:
                                        logger.debug(
                                            f"Attempting to bind {attr.__name__} from {module_name}"
                                        )
                                        self.bind(attr)
                                        logger.debug(
                                            f"Bound {'extension' if is_extension_module else 'core'} model {attr.__name__} from {module_name}"
                                        )
                                    except Exception as e:
                                        logger.debug(
                                            f"Could not bind model {attr.__name__}: {e}"
                                        )

                            # Check for manager classes that have Model attributes to bind
                            elif (
                                inspect.isclass(attr)
                                and attr_name.endswith("Manager")
                                and hasattr(attr, "BaseModel")
                                and hasattr(attr.BaseModel, "__bases__")
                                and any(
                                    base.__name__ == "BaseModel"
                                    for base in attr.BaseModel.__mro__
                                )
                            ):
                                # This is a manager class with a Pydantic model - bind the model
                                try:
                                    self.bind(attr.BaseModel)
                                    logger.debug(
                                        f"Bound model {attr.BaseModel.__name__} from manager {attr.__name__} in {module_name}"
                                    )
                                except Exception as e:
                                    logger.debug(
                                        f"Could not bind model {attr.BaseModel.__name__} from manager {attr.__name__}: {e}"
                                    )
                except Exception as e:
                    logger.error(f"Error processing module {module_name}: {e}")

            logger.debug(
                f"Successfully auto-bound models for scopes: {scopes} (total models: {len(self.bound_models)})"
            )
        except Exception as e:
            logger.error(f"Error auto-binding models: {e}")

    @property
    def DB(self):
        """Provide direct access to database operations via the attached DatabaseManager.

        This allows any method that receives a model_registry to access database functionality
        without requiring separate db_manager and db parameters.

        Returns:
            DatabaseManager instance with convenient session access
        """
        if not self.database_manager:
            raise RuntimeError("No DatabaseManager attached to this ModelRegistry")

        # Return a wrapper that provides convenient session access
        class DatabaseProxy:
            def __init__(self, db_manager):
                self._db_manager = db_manager

            def session(self):
                """Get a new database session."""
                return self._db_manager.get_session()

            def get_session(self):
                """Get a new database session (alias for session())."""
                return self._db_manager.get_session()

            @property
            def manager(self):
                """Direct access to the underlying DatabaseManager."""
                return self._db_manager

            def __getattr__(self, name):
                """Delegate all other attributes to the DatabaseManager."""
                return getattr(self._db_manager, name)

        return DatabaseProxy(self.database_manager)

    def bind(self, model: Type[BaseModel], **metadata) -> None:
        """
        Bind a model to this registry with dependency analysis.

        Args:
            model: The Pydantic model to bind
            **metadata: Additional metadata for the model
        """
        if self._locked:
            raise RuntimeError("Cannot bind models after registry has been committed")

        if not (inspect.isclass(model) and issubclass(model, BaseModel)):
            raise ValueError(f"Model must be a Pydantic BaseModel subclass: {model}")

        # Skip binding extension models directly - they only extend existing models
        if hasattr(model, "_is_extension_model"):
            return

        # Analyze dependencies before adding
        dependencies = self._analyze_model_dependencies(model)
        self._model_dependencies[model] = dependencies

        # Add the model using topological ordering
        self._add_model_with_dependencies(model, metadata)

        # Extensions are no longer tracked globally - they will be discovered
        # and bound when extension modules are imported via scoped_import
        # or explicitly bound via bind_extension() method

        logger.debug(f"Bound model {model.__name__} to registry")

        # Mark registry as needing commit
        self._locked = False

    def _analyze_model_dependencies(
        self, model: Type[BaseModel]
    ) -> Set[Type[BaseModel]]:
        """
        Analyze a model's fields to find dependencies on other models.

        Args:
            model: The model to analyze

        Returns:
            Set of models that this model depends on
        """
        dependencies = set()

        # Get model fields
        model_fields = self.utility.get_model_fields(model)

        for field_name, field_type in model_fields.items():
            # Skip internal fields
            if self.utility.field_processor.should_skip_field(field_name):
                continue

            # Check if field references another model
            referenced_model = self.utility.get_model_for_field(
                field_name, field_type, model
            )

            if referenced_model and referenced_model != model:
                # Check if it's a model we manage (BaseModel subclass)
                if inspect.isclass(referenced_model) and issubclass(
                    referenced_model, BaseModel
                ):
                    dependencies.add(referenced_model)
                    logger.debug(
                        f"Model {model.__name__} depends on {referenced_model.__name__} via field {field_name}"
                    )

        return dependencies

    def _add_model_with_dependencies(
        self, model: Type[BaseModel], metadata: Dict[str, Any]
    ) -> None:
        """
        Add a model to bound_models ensuring all its dependencies are added first.

        This maintains topological ordering in the OrderedSet.

        Args:
            model: The model to add
            metadata: Model metadata
        """
        # If model is already bound, nothing to do
        if model in self.bound_models:
            logger.debug(f"Model {model.__name__} already bound, skipping")
            return

        # Check for duplicate model names with different class objects (this should never happen).
        # ``_bound_model_names`` holds at most one model per name, so an O(1)
        # lookup surfaces the same conflicting model the linear scan would find.
        existing_model = self._bound_model_names.get(model.__name__)
        if existing_model is not None and existing_model is not model:
            raise RuntimeError(
                f"CRITICAL ERROR: Duplicate model class detected! "
                f"Model '{model.__name__}' exists with different class objects:\n"
                f"  Existing: {existing_model} (ID: {id(existing_model)}) from {existing_model.__module__}\n"
                f"  New: {model} (ID: {id(model)}) from {model.__module__}\n"
                f"This indicates a module import or class loading issue that must be fixed."
            )

        # First, ensure all dependencies are added
        dependencies = self._model_dependencies.get(model, set())
        for dep_model in dependencies:
            if dep_model not in self.bound_models:
                # Check if we have metadata for the dependency
                dep_metadata = self.model_metadata.get(dep_model, {})
                # Recursively add the dependency
                self._add_model_with_dependencies(dep_model, dep_metadata)

        # Now add this model
        # Check for name duplicates before adding - this should never happen.
        # A dependency added by the recursion above may have claimed this name,
        # so re-check the index (O(1)) exactly as the prior linear scan did.
        existing_model = self._bound_model_names.get(model.__name__)
        if existing_model is not None:
            raise RuntimeError(
                f"CRITICAL ERROR: Duplicate model name detected!\n"
                f"  Existing: {existing_model.__name__} (ID: {id(existing_model)}) from {existing_model.__module__}\n"
                f"  New: {model.__name__} (ID: {id(model)}) from {model.__module__}\n"
                f"This indicates a module import or class loading issue that must be fixed."
            )

        self.bound_models.add(model)
        self._bound_model_names[model.__name__] = model
        self.model_metadata[model] = metadata

        logger.debug(
            f"Added model {model.__name__} to bound_models at position {len(self.bound_models)}"
        )

    def bind_extension(
        self, target_model: Type[BaseModel], extension_model: Type
    ) -> None:
        """
        Bind an extension model to its target.

        Args:
            target_model: The model being extended
            extension_model: The extension model
        """
        if not hasattr(extension_model, "_is_extension_model"):
            raise ValueError(
                f"Model {extension_model} is not marked as an extension model"
            )

        if not hasattr(extension_model, "_extension_target"):
            raise ValueError(f"Extension model {extension_model} has no target model")

        # Store the extension for processing during commit
        if target_model not in self.extension_models:
            self.extension_models[target_model] = []
        self.extension_models[target_model].append(extension_model)

        # Mark registry as needing commit
        self._locked = False

    def commit(self, extensions=None, database_manager=None) -> None:
        """Process all bound models and generate schemas.

        This method:
        1. Resolves model dependencies and extension relationships
        2. Creates a database
        3. Runs migrations
        4. Creates SQLAlchemy table objects and metadata
        5. Generates FastAPI routers
        6. Creates GraphQL schema
        7. Locks the registry against further changes

        Args:
            database_manager: Optional DatabaseManager instance for SQLAlchemy integration
        """
        logger.debug(
            f"ModelRegistry.commit() called with extensions={extensions}, database_manager={database_manager}"
        )

        if self._locked:
            logger.warning("Registry already committed, skipping")
            return

        logger.debug(f"Committing registry with {len(self.bound_models)} models")

        # Use provided database manager or the one attached to the registry
        if database_manager:
            self.database_manager = database_manager
        elif not self.database_manager:
            # Create a default instance if none provided
            # Use test prefix to avoid touching production database
            # (production code always provides a database_manager via app.instance())
            from zephyrex.database.DatabaseManager import DatabaseManager

            # DatabaseManager.__init__ already calls init_engine_config(db_prefix)
            # so we don't need to call it again here
            self.database_manager = DatabaseManager("test.registry_fallback")

        # Phase 1: Process extensions
        self._process_extensions()

        # Phase 1.6 — external federation (Item 16). Lifts external GraphQL
        # and REST upstreams into Pydantic models, synthesizes managers, and
        # binds them with this registry so the existing Pydantic2{Strawberry,
        # FastAPI} pipelines project them onto BOTH inbound surfaces. Errors
        # surface via provider health checks; an unreachable upstream MUST
        # NOT prevent the registry from committing.
        from zephyrex.lib.Environment import env as _env

        federation_enabled = (
            _env("GQL_FEDERATION", default="true") or "true"
        ).lower() == "true"
        # Federation is owned by the ``federation`` extension. Core never
        # imports from it; the extension registers a callable on
        # ``_registry_hooks["bootstrap_federation"]`` at on_load time and
        # we dispatch through that. Without the extension, the registry
        # commits as a single-app deployment.
        from zephyrex.lib.Hooks import _registry_hooks

        bootstrap = _registry_hooks["bootstrap_federation"]
        if federation_enabled and bootstrap is not None:
            try:
                self._federation_report = bootstrap(model_registry=self)
                if self._federation_report is not None and getattr(
                    self._federation_report, "models", None
                ):
                    logger.info(
                        "Federation lifted %d external types: %s",
                        len(self._federation_report.models),
                        ", ".join(sorted(self._federation_report.models.keys())),
                    )
            except Exception as exc:
                logger.warning("Federation bootstrap failed during commit: %s", exc)
                self._federation_report = None
        else:
            self._federation_report = None

        # Phase 1.5: Generate Network classes for all bound models
        logger.debug(
            f"Generating Network classes for {len(self.bound_models)} bound models"
        )
        for model in self.bound_models:
            if not hasattr(model, "Network"):
                try:
                    self._generate_network_class(model)
                    logger.debug(f"Generated Network class for {model.__name__}")
                except Exception as e:
                    logger.warning(
                        f"Failed to generate Network class for {model.__name__}: {e}"
                    )

        # Phase 2: Resolve dependencies
        self._resolve_dependencies()

        # Database migration
        if hasattr(self.database_manager, "Base") and self.database_manager.Base:
            self.database_manager.Base._model_registry = self
            logger.debug(
                "Attached ModelRegistry to DatabaseManager.Base for migration access"
            )

        engine = self.database_manager.get_setup_engine()
        db_type = self.database_manager.DATABASE_TYPE
        db_name = self.database_manager.DATABASE_NAME

        if db_type != "sqlite":
            logger.info("Connecting to database...")
            for retry_count in range(5):
                try:
                    connection = engine.connect()
                    connection.close()
                    break
                except Exception as e:
                    logger.error(
                        f"Error connecting to database (attempt {retry_count+1}/5)",
                        exc_info=True,
                    )
                    if retry_count == 4:
                        raise Exception(
                            "Failed to connect to database after maximum retries"
                        )
                    time.sleep(5)  # type: ignore[attr-defined]

        import os

        run_migrations = os.environ.get("RUN_MIGRATIONS", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        if not run_migrations:
            logger.info("RUN_MIGRATIONS=false — skipping automatic migrations")
        else:
            custom_db_info = {
                "type": self.database_manager.DATABASE_TYPE,
                "name": self.database_manager.DATABASE_NAME,
                "url": self.database_manager.DATABASE_URI,
                "file_path": getattr(
                    self.database_manager, "_database_file_path", None
                ),
            }
            logger.info(f"Migration target database: {custom_db_info}")

            migration_manager = MigrationManager(
                custom_db_info=custom_db_info, model_registry=self
            )
            extensions_csv = (
                self.extension_registry.csv if self.extension_registry else ""
            )

            result = migration_manager.run_all_migrations(
                "upgrade",
                "head",
                extensions=extensions_csv.split(",") if extensions_csv else [],
            )
            if not result:
                worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
                db_info_str = f"db_name={custom_db_info['name']}, db_type={custom_db_info['type']}"
                logger.error(
                    f"Failed to apply migrations. Worker={worker_id}, {db_info_str}, Result={result}"
                )
                raise Exception(
                    f"Failed to apply migrations. Worker={worker_id}, {db_info_str}"
                )
        logger.info(f"Successfully verified database migrations for {db_name}")

        configure_mappers()

        # Phase 3: Create SQLAlchemy models
        self._create_sqlalchemy_models()

        # Phase 4: Generate routers
        self._generate_routers()

        # All extensions are bound at this point — surface any conflicting
        # ``(model, field)`` declarations from ``@extension_model``-tagged
        # classes before the registry locks. Identical declarations across
        # extensions are accepted; non-equivalent ones raise immediately
        # so an operator sees the conflict at startup, not at request time.
        try:
            from zephyrex.extensions.CollisionDetection import (
                detect_extension_field_collisions,
            )

            detect_extension_field_collisions()
        except ImportError:
            # CollisionDetection ships with the framework, but tolerate a
            # stripped-down install rather than block boot.
            pass

        # Lock the registry before creating schema (required for schema generation)
        self._locked = True

        # Deterministically resolve forward references on every registered
        # Pydantic model before GraphQL schema generation. Relying on import-
        # time model rebuilding is order-dependent and races under xdist: a
        # worker that runs only a subset of tests can reach schema generation
        # with a model still not fully defined, surfacing as a strawberry
        # ``UnresolvedFieldTypeError`` -> ``StartupError``. Rebuilding here makes
        # it deterministic regardless of which tests share the worker.
        for _model in list(self.model_metadata):
            try:
                _model.model_rebuild(force=True)
            except Exception:  # noqa: BLE001 - a model that cannot rebuild
                pass  # here will surface its own error at schema generation

        from zephyrex.pydantic2.strawberry import GraphQLManager

        # Create schema using the new instance-based GraphQLManager
        self.graphql_manager = GraphQLManager(self)
        self.gql = self.graphql_manager.create_schema()

        # Phase 5: Seed the database with initial data
        seed_db_value = env("SEED_DATA")
        logger.info(f"SEED_DATA env var value: '{seed_db_value}'")
        if seed_db_value.lower() == "true":
            logger.info("Calling _seed() method...")
            self._seed()
        else:
            logger.info(f"Skipping seeding because SEED_DATA='{seed_db_value}'")

        # SDK generation is opt-in and owned by the ``meta_sdk_<lang>``
        # extensions, each of which registers a language generator on
        # ``_registry_hooks["generate_sdk"]`` at on_load. Every registered
        # generator emits a typed client SDK (py/ts/rs) for this registry's
        # RouterMixin-tagged managers; each self-gates on its configured output
        # target, so enabling an extension without a destination is a no-op.
        # Core never imports a generator — this mirrors ``bootstrap_federation``.
        from zephyrex.lib.Hooks import _registry_hooks as _sdk_reg_hooks

        for _sdk_language, _generate_sdk in _sdk_reg_hooks["generate_sdk"].items():
            try:
                _generate_sdk(model_registry=self)
            except Exception as exc:  # noqa: BLE001 - never block boot on SDK gen
                logger.warning(
                    f"SDK generation ({_sdk_language}) failed during " f"commit: {exc}"
                )

        logger.debug("Registry committed successfully")
        return self  # type: ignore[return-value]

    def _process_extensions(self) -> None:
        """Process all registered model extensions."""
        from zephyrex.lib.Logging import logger
        from zephyrex.pydantic2.sqlalchemy import _apply_model_extension

        logger.debug(
            f"_process_extensions called with extension_registry: {self.extension_registry}"
        )

        # First, discover extension models from BLL and PRV files
        if self.extension_registry:
            extension_names = [ext.name for ext in self.extension_registry.extensions]
            logger.debug(
                f"Discovering extension models for extensions: {extension_names}"
            )
            self.extension_registry.discover_extension_models(extension_names)
            logger.debug(
                f"Discovered extension models: {list(self.extension_registry.extension_models.keys())}"
            )
        else:
            logger.debug("No ExtensionRegistry available for model discovery")

        # Register external models from PRV files
        if not self.extension_registry:
            logger.debug(
                "No ExtensionRegistry available for external model registration"
            )
        else:
            logger.debug("Registering external models from PRV files")
            external_model_count = 0
            for key, models in self.extension_registry.extension_models.items():
                if key.startswith("external."):
                    for model in models:
                        try:
                            model_name = model.__name__
                            base_name = (
                                model_name[:-5].lower()
                                if model_name.endswith("Model")
                                else model_name.lower()
                            )
                            self.utility.register_model(model, base_name)
                            fields = self.utility.get_model_fields(model)
                            self.utility._model_fields_cache[model] = fields
                            logger.debug(
                                f"Registered external model {model_name} as '{base_name}'"
                            )
                            external_model_count += 1
                        except Exception as e:
                            logger.error(
                                f"Failed to register external model {model}: {e}"
                            )
            logger.debug(f"Registered {external_model_count} external models")

        # Process extensions from the extension registry if available
        if self.extension_registry:
            logger.debug(
                f"Processing extensions from ExtensionRegistry with {len(self.bound_models)} bound models"
            )
            logger.debug(
                f"ExtensionRegistry has extensions: {list(self.extension_registry.extension_models.keys())}"
            )

            for target_model in self.bound_models:
                target_key = f"{target_model.__module__}.{target_model.__name__}"
                logger.debug(
                    f"Processing bound model: {target_model.__name__} with key: {target_key}"
                )
                if target_model.__name__ == "UserModel":
                    logger.debug(
                        f"Found UserModel - checking for extensions with key {target_key}"
                    )

                extension_models = (
                    self.extension_registry.get_extension_models_for_target(
                        target_model
                    )
                )
                logger.debug(
                    f"Found {len(extension_models)} extensions for {target_model.__name__}"
                )
                if target_model.__name__ == "UserModel":
                    logger.debug(
                        f"UserModel extensions: {[ext.__name__ for ext in extension_models]}"
                    )
                    logger.debug(
                        f"Found {len(extension_models)} extensions for UserModel"
                    )
                    if extension_models:
                        for ext in extension_models:
                            logger.debug(f"UserModel extension: {ext.__name__}")

                for extension_model in extension_models:
                    try:
                        # Apply the extension
                        logger.debug(
                            f"Applying extension {extension_model.__name__} to {target_model.__name__}"
                        )
                        if target_model.__name__ == "UserModel":
                            logger.debug(
                                f"UserModel fields before extension: {list(target_model.model_fields.keys())}"
                            )
                            logger.debug(f"UserModel ID before: {id(target_model)}")
                        _apply_model_extension(target_model, extension_model)
                        if target_model.__name__ == "UserModel":
                            logger.debug(
                                f"UserModel fields after extension: {list(target_model.model_fields.keys())}"
                            )
                            logger.debug(f"UserModel ID after: {id(target_model)}")
                        logger.debug(
                            f"Successfully applied extension {extension_model.__name__} to {target_model.__name__}"
                        )
                        # Also store in local extension_models for backward compatibility
                        if target_model not in self.extension_models:
                            self.extension_models[target_model] = []
                        self.extension_models[target_model].append(extension_model)
                    except Exception as e:
                        logger.debug(
                            f"Failed to apply extension {extension_model.__name__} to {target_model.__name__}: {e}"
                        )
                        logger.error(
                            f"Failed to apply extension {extension_model.__name__} to {target_model.__name__}: {e}"
                        )
                        raise
        else:
            logger.warning("No ExtensionRegistry available for processing extensions")

        # Also process any manually registered extensions
        for target_model, extensions in self.extension_models.items():
            for extension_model in extensions:
                try:
                    # Apply the extension
                    _apply_model_extension(target_model, extension_model)
                    logger.debug(
                        f"Applied extension {extension_model.__name__} to {target_model.__name__}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to apply extension {extension_model.__name__} to {target_model.__name__}: {e}"
                    )
                    raise

    def _resolve_dependencies(self) -> None:
        """Resolve model dependencies and determine processing order."""
        # Discover relationships between models
        bll_modules = {}

        # Group models by module to match existing discovery pattern
        for model in self.bound_models:
            module_name = model.__module__
            if module_name not in bll_modules:
                import sys

                module = sys.modules.get(module_name)
                if module:
                    bll_modules[module_name] = module
                    logger.debug(
                        f"Added BLL module for bound model {model.__name__}: {module_name}"
                    )

        logger.debug(f"BLL modules for discovery: {list(bll_modules.keys())}")

        # Use utility to discover relationships
        self.model_relationships = self.utility.discover_model_relationships(
            bll_modules
        )

        logger.debug(f"Discovered {len(self.model_relationships)} model relationships")

        # Collect all model fields for relationship processing
        model_fields_mapping = self.utility.collect_model_fields(
            self.model_relationships
        )

        # Enhance model discovery
        self.utility.enhance_model_discovery(model_fields_mapping)

        # Build dependency order from our already-sorted OrderedSet
        # Since we've been adding models in dependency order during bind(),
        # the OrderedSet already maintains the correct order
        self.dependency_order = list(self.bound_models)

        logger.debug(f"Resolved dependencies for {len(self.dependency_order)} models")
        logger.debug(f"Dependency order: {[m.__name__ for m in self.dependency_order]}")

    def _seed(self) -> None:
        """
        Seed the database with initial data from all models.
        Uses the dependency order already established in bound_models.
        """
        from zephyrex.database.StaticSeeder import seed_model

        logger.log("SQL", "Starting database seeding process...")
        logger.log("SQL", f"Total bound models: {len(self.bound_models)}")

        session = self.database_manager.get_session()
        try:
            # Use the already sorted models from our OrderedSet
            models_to_seed = []

            # Process models in dependency order
            for pydantic_model in self.bound_models:
                logger.log(
                    "SQL", f"Checking model {pydantic_model.__name__} for seed_data..."
                )
                # Check if model has seed data
                if hasattr(pydantic_model, "seed_data"):
                    logger.log(
                        "SQL",
                        f"Model {pydantic_model.__name__} HAS seed_data attribute",
                    )
                    # Get the SQLAlchemy model
                    if hasattr(pydantic_model, "DB") and callable(pydantic_model.DB):
                        db_model = pydantic_model.DB(self.database_manager.Base)
                        models_to_seed.append(db_model)
                        logger.log(
                            "SQL",
                            f"Found model with seed_data: {pydantic_model.__name__}",
                        )
                else:
                    logger.log(
                        "SQL",
                        f"Model {pydantic_model.__name__} does NOT have seed_data",
                    )

            logger.log("SQL", f"Found {len(models_to_seed)} models to seed")
            logger.log(
                "SQL",
                f"Models in dependency order: {[model.__name__ for model in models_to_seed]}",
            )

            # Seed all models in dependency order
            for model in models_to_seed:
                seed_model(model, session, self.database_manager, self)

            session.commit()
            logger.log("SQL", "Database seeding completed successfully")

        except Exception as e:
            logger.error(f"Error during database seeding: {e}")
            session.rollback()
            raise
        finally:
            session.close()

    def _create_sqlalchemy_models(self):
        """
        Create SQLAlchemy models for all bound Pydantic models.

        This method generates SQLAlchemy models using the declarative base
        and stores them in the registry for later access.
        """
        logger.debug(
            f"Creating SQLAlchemy models for {len(self.bound_models)} bound models"
        )

        # Use the DatabaseManager's declarative base if available, otherwise create one
        if self.database_manager and hasattr(self.database_manager, "Base"):
            self.declarative_base = self.database_manager.Base
            logger.debug(
                f"Using DatabaseManager's declarative_base: {self.declarative_base}"
            )
        elif self.declarative_base is None:
            from sqlalchemy.ext.declarative import declarative_base

            self.declarative_base = declarative_base()
            logger.debug(f"Created isolated declarative_base: {self.declarative_base}")

        # Attach the registry to the declarative base for model creation
        self.declarative_base._model_registry = self

        for model_class in self.bound_models:
            try:
                logger.debug(f"Processing model: {model_class.__name__}")

                # Use the new .DB(declarative_base) method to get/create the SQLAlchemy model
                if hasattr(model_class, "DB"):
                    sqlalchemy_model = model_class.DB(self.declarative_base)

                    if sqlalchemy_model is not None:
                        # Store in our registry for quick access
                        self.db_models[model_class] = sqlalchemy_model
                        logger.debug(
                            f"✓ SQLAlchemy model created and stored for {model_class.__name__}"
                        )
                    else:
                        logger.warning(
                            f"⚠ No SQLAlchemy model returned for {model_class.__name__}"
                        )
                else:
                    try:
                        from zephyrex.pydantic2.sqlalchemy import (
                            create_sqlalchemy_model,
                        )

                        sa_model = create_sqlalchemy_model(
                            model_class,
                            model_registry=self,
                            base_model=self.declarative_base,
                        )
                        if sa_model:
                            self.db_models[model_class] = sa_model
                            logger.info(
                                f"✓ SQLAlchemy model created via fallback for {model_class.__name__}"
                            )
                    except Exception as fallback_err:
                        logger.debug(
                            f"⚠ Model {model_class.__name__} has no .DB method and "
                            f"fallback failed: {fallback_err}"
                        )

            except Exception as e:
                logger.error(
                    f"✗ Failed to create SQLAlchemy model for {model_class.__name__}: {e}"
                )
                logger.debug(f"Model class: {model_class}")
                logger.debug(f"Error details: {e}", exc_info=True)

        logger.debug(
            f"SQLAlchemy model creation complete. Created {len(self.db_models)} models"
        )
        logger.debug(f"Models in registry: {list(self.db_models.keys())}")

        self._stamp_extension_table_ownership()

    def _stamp_extension_table_ownership(self) -> None:
        """Mark each SA table with its owning extension(s) via Table.info.

        - table.info["extension"] = "<name>"   for tables defined in an
          extension module (e.g. multifactor_methods owned by auth_mfa).
        - table.info["extensions"] = {"<name>", ...}   for *core* tables
          extended by extensions via @extension_model (e.g. payment adds
          columns onto users).

        Migration.py reads these to decide which tables belong to which
        extension's autogenerate run, replacing the prior 100-line fuzzy
        stringcase-based fallback.
        """

        def _ext_name(module_path: str) -> Optional[str]:
            parts = (module_path or "").split(".")
            # Canonical post-Item-60 form: ``zephyrex.extensions.<name>...``
            if len(parts) >= 3 and parts[0] == "zephyrex" and parts[1] == "extensions":
                return parts[2]
            # Legacy alias kept by ExtensionLoader: ``extensions.<name>...``
            if len(parts) >= 2 and parts[0] == "extensions":
                return parts[1]
            return None

        for pydantic_model, sa_model in self.db_models.items():
            table = getattr(sa_model, "__table__", None)
            if table is None:
                continue
            owner = _ext_name(pydantic_model.__module__)
            if owner:
                table.info["extension"] = owner

        for target_pydantic, extension_pydantics in self.extension_models.items():
            sa_model = self.db_models.get(target_pydantic)  # type: ignore[assignment]
            if sa_model is None or not hasattr(sa_model, "__table__"):
                continue
            extending = {
                _ext_name(ext.__module__)
                for ext in extension_pydantics
                if _ext_name(ext.__module__)
            }
            if extending:
                sa_model.__table__.info.setdefault("extensions", set()).update(
                    extending
                )

    def _generate_routers(self) -> None:
        """Generate FastAPI routers for bound models."""
        from zephyrex.lib.Environment import env

        if env("REST").strip().lower() != "true":
            logger.debug("REST endpoints disabled, skipping router generation")
            return

        self.ep_routers = generate_routers_from_model_registry(self)

    def _scoped_import(
        self, file_type="DB", scopes=["database", "extensions"], clean=False
    ):
        """Delegate to the ScopedModuleImporter collaborator (see #226)."""
        return self._scoped_importer._scoped_import(
            file_type=file_type, scopes=scopes, clean=clean
        )

    def _build_dependency_graph(self, files_by_scope):
        """Delegate to the ScopedModuleImporter collaborator."""
        return self._scoped_importer._build_dependency_graph(files_by_scope)

    def _parse_imports_and_dependencies(self, file_path, scope="database"):
        """Delegate to the ScopedModuleImporter collaborator."""
        return self._scoped_importer._parse_imports_and_dependencies(file_path, scope)

    def build_routers(self):
        """
        Build FastAPI routers using RouterMixin from BLL managers.

        This method uses the RouterMixin approach exclusively - NO EP files are used.
        We are now completely decoupled from EP_Auth, EP_Extensions, and EP_Providers.

        Returns:
            List of router information dictionaries
        """
        if not self._locked:
            raise RuntimeError("Registry must be committed before building routers")

        logger.info("Building routers using RouterMixin approach - NO EP files")

        # Use the RouterMixin approach exclusively
        router_instances = self.build_all_routers_from_managers()

        # Add special root authorization verification endpoint
        root_auth_router = self._create_root_auth_router()
        if root_auth_router:
            router_instances.append(root_auth_router)

        # Convert to the format expected by the application
        routers = []
        for i, router in enumerate(router_instances):
            # Extract router information from the router instance
            router_prefix = getattr(router, "prefix", f"/unknown_{i}")
            router_name = router_prefix.replace("/v1/", "").replace("/", "_")
            if router_name.startswith("_"):
                router_name = router_name[1:]

            routers.append(
                {
                    "router": router,
                    "model_name": router_name,
                    "module_name": f"RouterMixin_{router_name}",
                }
            )

            # Include nested routers if they exist
            if hasattr(router, "nested_routers") and router.nested_routers:
                logger.info(
                    f"Found {len(router.nested_routers)} nested routers for {router_name}"
                )
                for j, nested_router in enumerate(router.nested_routers):
                    nested_prefix = getattr(nested_router, "prefix", f"/nested_{i}_{j}")
                    nested_name = (
                        nested_prefix.replace("/v1/", "")
                        .replace("/", "_")
                        .replace("{", "")
                        .replace("}", "")
                    )
                    if nested_name.startswith("_"):
                        nested_name = nested_name[1:]

                    routers.append(
                        {
                            "router": nested_router,
                            "model_name": nested_name,
                            "module_name": f"RouterMixin_nested_{nested_name}",
                        }
                    )
                    logger.info(f"Added nested router: {nested_prefix}")
            else:
                logger.debug(f"No nested routers found for {router_name}")

        # Add static routes from extensions
        extension_static_routes = [
            item for item in self.extension_registry.extensions_static_routes.values()
        ]
        if extension_static_routes:
            routers.extend(extension_static_routes)
            logger.info(
                f"Added {len(extension_static_routes)} extension static route routers"
            )

        logger.info(
            f"Built {len(routers)} total routers (including nested) using RouterMixin approach"
        )
        return routers

    def router_managers(self) -> list:
        """Return the RouterMixin-tagged manager classes for this registry.

        Read-only discovery: scoped-imports the BLL modules for the loaded
        scopes and collects every ``*Manager`` class that subclasses
        ``RouterMixin``, is defined in its own module, and carries both a
        ``Router`` and a ``BaseModel`` — the exact predicate
        ``build_all_routers_from_managers`` uses to decide what gets a router,
        minus the router instantiation and its side effects.

        The opt-in SDK emitters (``meta_sdk_py`` / ``meta_sdk_ts`` /
        ``meta_sdk_rs``) consume this via ``sdk.SDKModel.extract_resources``
        (``sdk.SDKGenerator._iter_manager_classes`` auto-detects the
        ``router_managers`` accessor), so a generated client covers precisely
        the mounted routes and cannot drift from them. Kept deliberately
        separate from the builder: the builder re-raises on any
        router-construction failure, which a read-only enumeration must never
        do. Only invoked when SDK generation is actually requested (the emitters
        self-gate on their output directory before calling this), so a normal
        boot never pays for the scan.
        """
        import sys

        from zephyrex.pydantic2.fastapi import RouterMixin

        managers: list = []
        seen: set = set()
        try:
            imported_modules, _ = self._scoped_import(
                file_type="BLL", scopes=["logic", "extensions"]
            )
        except Exception as exc:  # noqa: BLE001 - discovery must not break callers
            logger.debug(f"router_managers discovery import failed: {exc}")
            return managers

        for module_name in imported_modules:
            if ".BLL_" not in module_name:
                continue
            module = sys.modules.get(module_name)
            if not module:
                continue
            for attr_name in dir(module):
                if not attr_name.endswith("Manager"):
                    continue
                attr = getattr(module, attr_name, None)
                if (
                    inspect.isclass(attr)
                    and issubclass(attr, RouterMixin)
                    and attr is not RouterMixin
                    and hasattr(attr, "Router")
                    and attr.__module__ == module_name
                    and getattr(attr, "BaseModel", None) is not None
                ):
                    key = (attr.__module__, attr.__qualname__)
                    if key not in seen:
                        seen.add(key)
                        managers.append(attr)

        managers.sort(key=lambda c: (c.__module__, c.__qualname__))
        return managers

    def build_all_routers_from_managers(self):
        """
        Build FastAPI routers from all managers using RouterMixin.

        This is the new approach that generates routers directly from BLL managers
        without requiring separate EP_ files.

        Returns:
            List of APIRouter instances
        """
        if not self._locked:
            raise RuntimeError("Registry must be committed before building routers")

        routers = []

        try:
            # Import BLL managers using scoped import
            imported_modules, _ = self._scoped_import(
                file_type="BLL", scopes=["logic", "extensions"]
            )

            import sys

            from zephyrex.pydantic2.fastapi import RouterMixin

            # Find all BLL manager classes with RouterMixin
            for module_name in imported_modules:
                try:
                    module = sys.modules.get(module_name)
                    if not module:
                        continue

                    # Only process modules that are actually BLL files (not imported dependencies)
                    if ".BLL_" not in module_name:
                        # Skip modules that don't match the BLL_ pattern
                        logger.debug(f"Skipping non-BLL module: {module_name}")
                        continue

                    # Look for manager classes in the module
                    for attr_name in dir(module):
                        if attr_name.endswith("Manager"):
                            attr = getattr(module, attr_name)

                            # Check if it's a class that inherits RouterMixin
                            if (
                                inspect.isclass(attr)
                                and issubclass(attr, RouterMixin)
                                and attr != RouterMixin
                                and hasattr(attr, "Router")
                            ):

                                # Additional check: make sure this class is actually defined in this module
                                if attr.__module__ == module_name:
                                    try:
                                        # Get the model used by this manager
                                        base_model = getattr(attr, "BaseModel", None)
                                        if base_model is None:
                                            logger.debug(
                                                f"Skipping {attr_name} - no BaseModel attribute"
                                            )
                                            continue
                                        model = self.apply(base_model)
                                        if model and hasattr(model, "model_fields"):
                                            logger.debug(
                                                f"{attr.__name__} router uses model {model.__name__} with fields: {list(model.model_fields.keys())}"
                                            )
                                            # Generate router using RouterMixin
                                        router = attr.Router(model_registry=self)
                                        routers.append(router)
                                        logger.debug(
                                            f"Generated router for {attr_name} from {module_name}"
                                        )
                                    except Exception as e:
                                        import traceback

                                        logger.error(
                                            f"Failed to generate router for {attr_name}: {traceback.print_exc(e)}"
                                        )
                                        raise (e)

                                else:
                                    logger.debug(
                                        f"Skipping {attr_name} as it's defined in {attr.__module__}, not {module_name}"
                                    )

                except Exception as e:
                    logger.error(f"Failed to process module {module_name}: {e}")
                    raise (e)

        except Exception as e:
            logger.error(f"Error building routers from managers: {e}")
            raise (e)

        logger.debug(f"Generated {len(routers)} routers from managers")
        return routers

    def _create_root_auth_router(self):
        """Create the root authorization verification router (/v1)."""
        try:
            from typing import Optional

            from fastapi import (
                APIRouter,
                Depends,
                Header,
                HTTPException,
                Request,
                Response,
                status,
            )

            router = APIRouter(prefix="/v1", tags=["Authentication"])

            # Define dependency for model registry
            def get_model_registry(request: Request):
                """Get the model registry from app state."""
                model_registry = getattr(request.app.state, "model_registry", None)
                if model_registry is None:
                    raise HTTPException(
                        status_code=500, detail="Model registry not available"
                    )
                return model_registry

            @router.get(
                "",
                summary="Verify authorization",
                description="Verifies if the provided JWT token or API Key is valid.",
                status_code=status.HTTP_204_NO_CONTENT,
                responses={
                    status.HTTP_204_NO_CONTENT: {
                        "description": "Authorization is valid"
                    },
                    status.HTTP_401_UNAUTHORIZED: {
                        "description": "Invalid authorization"
                    },
                },
            )
            async def verify_authorization(
                authorization: Optional[str] = Header(
                    None,
                    description="Authorization header with Bearer token or API Key",
                ),
                model_registry=Depends(get_model_registry),
            ):
                if not authorization:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Authorization header is missing",
                    )
                token = authorization.replace("Bearer ", "").strip()
                if not token:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Token is missing or empty",
                    )

                # Verify the token via the registered identity/auth provider
                # (issue #221 — no concrete UserManager import in lib/).
                try:
                    from zephyrex.lib.AuthProvider import get_auth_provider

                    get_auth_provider().verify_token(
                        token=token, model_registry=model_registry
                    )
                    return Response(status_code=status.HTTP_204_NO_CONTENT)
                except (ImportError, RuntimeError):
                    logger.error("No auth provider available for token verification")
                    raise HTTPException(
                        status_code=500, detail="Authentication service unavailable"
                    )
                except Exception as e:
                    logger.debug(f"Token verification failed: {e}")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
                    )

            logger.debug("Created root authorization verification router at /v1")
            return router

        except Exception as e:
            logger.error(f"Failed to create root auth router: {e}")
            return None

    def _load_extension_ep_files(self, extension_names):
        """Load EP (endpoint) files for specified extensions."""
        import glob
        import os
        import sys

        # Get the source directory
        src_dir = _resolve_src_dir()

        imported_modules = []

        # Load EP files for each extension. Resolve through Paths so a
        # configured external extensions root is honored.
        extensions_root = _resolve_extensions_dir()
        for ext_name in extension_names:
            scope_dir = os.path.join(extensions_root, ext_name)
            files_pattern = os.path.join(scope_dir, "EP_*.py")
            matching_files = glob.glob(files_pattern)

            # Filter out test files
            ep_files = [
                f
                for f in matching_files
                if not os.path.basename(f).endswith("_test.py")
            ]

            for file_path in ep_files:
                module_name = (
                    f"zephyrex.extensions.{ext_name}.{os.path.basename(file_path)[:-3]}"
                )

                # Skip if already imported
                if module_name in sys.modules:
                    logger.debug(f"EP module already imported: {module_name}")
                    imported_modules.append(module_name)
                    continue

                try:
                    logger.debug(f"Importing extension EP module: {module_name}")
                    # Item 61: route through the canonical loader so
                    # out-of-tree extension EP files load correctly and
                    # are registered under both legacy and synthesized
                    # names.
                    from zephyrex.extensions.ExtensionLoader import (
                        load_extension_module,
                    )

                    file_stem = os.path.basename(file_path)[:-3]
                    module = load_extension_module(extensions_root, ext_name, file_stem)
                    imported_modules.append(module_name)
                    logger.debug(f"Successfully imported EP module: {module_name}")
                except Exception as e:
                    logger.error(f"Failed to import EP module {module_name}: {e}")

        return imported_modules

    def get_sqlalchemy_model(
        self, pydantic_model: Type[BaseModel], for_generation: bool = False
    ) -> Optional[Type]:
        """Get the SQLAlchemy model for a given Pydantic model.

        Args:
            pydantic_model: The Pydantic model class
            for_generation: True if checking before generation (INFO log), False if expecting existing model (WARNING log)

        Returns:
            The corresponding SQLAlchemy model class, or None if not found
        """
        logger.debug(f"Looking for SQLAlchemy model for {pydantic_model.__name__}")
        logger.debug(
            f"Available models in registry: {[model.__name__ for model in self.db_models.keys()]}"
        )
        result = self.db_models.get(pydantic_model)
        if result is None:
            if for_generation:
                logger.debug(
                    f"Generating SQLAlchemy model for {pydantic_model.__name__} (first time)"
                )
            else:
                logger.warning(
                    f"No SQLAlchemy model found for {pydantic_model.__name__}"
                )
        return result

    def get_bound_models(self) -> Set[Type[BaseModel]]:
        """Get all models bound to this registry."""
        return self.bound_models.copy()  # type: ignore[return-value]

    def is_model_bound(self, model: Type[BaseModel]) -> bool:
        """Check if a model is bound to this registry."""
        return model in self.bound_models

    def is_committed(self) -> bool:
        """Check if this registry has been committed (schema generated)."""
        return self._locked  # type: ignore[no-any-return]

    def clear(self) -> None:
        """Clear the registry (for testing purposes)."""
        self.bound_models.clear()
        self._bound_model_names.clear()
        self.extension_models.clear()
        self.model_metadata.clear()
        self.db_models.clear()
        self.model_relationships.clear()
        self.dependency_order.clear()
        self.ep_routers.clear()
        self.gql = None
        self._locked = False

        # Clear utility caches
        self.utility.clear_caches()

        logger.debug("Cleared model registry")

    def attach_to_app(self, app) -> None:
        """Attach this registry to a FastAPI app instance.

        Args:
            app: FastAPI application instance
        """
        self.app = app
        app.state.model_registry = self

        # Include any generated routers
        for router in self.ep_routers:
            app.include_router(router)

        # Add GraphQL if available
        if self.gql:
            from strawberry.fastapi import GraphQLRouter

            graphql_app = GraphQLRouter(schema=self.gql)
            app.include_router(graphql_app, prefix="/graphql")

        logger.debug("Attached registry to FastAPI app")

    @classmethod
    def from_scoped_import(
        cls, file_type="BLL", scopes=None, app_instance=None
    ) -> "ModelRegistry":
        """Create a ModelRegistry by importing models using scoped_import.

        This provides backward compatibility with the existing scoped_import system
        while providing the benefits of the registry pattern.

        Args:
            file_type: Type of files to import (e.g., "BLL")
            scopes: List of scopes to search (e.g., ["logic", "zephyrex.extensions.payment"])
            app_instance: Optional FastAPI app to associate with

        Returns:
            Configured ModelRegistry instance
        """
        import sys

        if scopes is None:
            scopes = ["logic"]

        registry = cls(app_instance)

        # Import modules using our private scoped_import method
        imported_modules, import_errors = registry._scoped_import(
            file_type=file_type, scopes=scopes
        )

        if import_errors:
            logger.warning(f"Import errors during registry creation: {import_errors}")

        # Discover and bind models from imported modules
        for module_name in imported_modules:
            module = sys.modules.get(module_name)
            if not module:
                continue

            # Look for BaseModel subclasses in the module
            for attr_name in dir(module):
                attr = getattr(module, attr_name)

                if (
                    inspect.isclass(attr)
                    and issubclass(attr, BaseModel)
                    and attr.__module__ == module_name
                ):

                    # Check if this is an extension model
                    if hasattr(attr, "_is_extension_model") and hasattr(
                        attr, "_extension_target"
                    ):
                        # This is an extension model - bind it to its target
                        target_model = attr._extension_target
                        registry.bind_extension(target_model, attr)
                        logger.debug(
                            f"Discovered and bound extension {attr.__name__} to {target_model.__name__}"
                        )
                    else:
                        # This is a regular model - bind it normally
                        # Extract metadata if available
                        metadata = {}
                        if hasattr(attr, "table_comment"):
                            metadata["table_comment"] = attr.table_comment

                        registry.bind(attr, **metadata)

        logger.debug(
            f"Created registry from scoped import with {len(registry.bound_models)} models"
        )
        return registry
