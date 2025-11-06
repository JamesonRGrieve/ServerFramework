import functools
import uuid
from typing import (
    Any,
    List,
    Literal,
    Optional,
    Type,
    TypeVar,
    Union,
    get_args,
    get_origin,
)

from fastapi import HTTPException, Request
from sqlalchemy import Column, DateTime, ForeignKey, String, func, inspect
from sqlalchemy.orm import Session, declared_attr, relationship
from sqlalchemy.orm.exc import MultipleResultsFound, NoResultFound

from database.DatabaseManager import DatabaseManager
from database.StaticPermissions import (
    PermissionType,
    check_permission,
    gen_not_found_msg,
    generate_permission_filter,
    validate_columns,
)
from lib.Environment import env
from lib.Logging import logger
from lib.Pydantic import obj_to_dict


def get_db_manager(
    request: Request = None, db: Session = None
) -> Optional[DatabaseManager]:
    """Get the database manager from app state, database session, or return None."""
    if request and hasattr(request.app.state, "DB"):
        return request.app.state.model_registry.database_manager
    if db and hasattr(db, "bind") and hasattr(db.bind, "_db_manager"):
        return db.bind._db_manager
    # Try to get from session's bind if it has a db_manager attribute
    if db and hasattr(db, "bind") and hasattr(db.bind.engine, "_db_manager"):
        return db.bind.engine._db_manager
    return None


def get_db_manager_from_session(db: Session) -> Optional[DatabaseManager]:
    """Extract database manager from a session."""
    if not db:
        return None

    # Check if the session has a direct reference to the database manager
    if hasattr(db, "_db_manager"):
        return db._db_manager

    # Check the bound engine
    if hasattr(db, "bind") and db.bind:
        engine = db.bind
        if hasattr(engine, "_db_manager"):
            return engine._db_manager

    # No fallbacks - return None if not properly configured
    return None


def with_session(func):
    """
    Decorator to handle session creation, commit, rollback, and closing.
    Uses ModelRegistry.DB for database access.
    """

    @functools.wraps(func)
    def wrapper(
        cls,
        requester_id: String,
        model_registry,
        *args,
        **kwargs,
    ):
        if model_registry is None:
            raise ValueError("model_registry parameter is required")

        # Get session from ModelRegistry.DB
        session = model_registry.DB.session()
        db_manager = model_registry.DB.manager

        logger.debug(f"Executing {func.__name__} on {cls.__name__}: {str(kwargs)}")
        try:
            # Inject db and db_manager into the method implementation
            # This allows the method body to use 'db' and 'db_manager' variables
            # while keeping the public API clean with only model_registry
            kwargs["db"] = session
            kwargs["db_manager"] = db_manager

            result = func(cls, requester_id, model_registry, *args, **kwargs)
            return result
        except Exception as e:
            logger.error(e)
            logger.debug(f"Rolling back {func.__name__}...")
            session.rollback()
            raise e
        finally:
            logger.debug("Closing session...")
            session.close()

    return wrapper


def get_dto_class(cls, override_dto=None):
    """
    Determine which DTO class to use based on provided override or class default.
    """
    if override_dto:
        return override_dto
    elif hasattr(cls, "dto") and cls.dto is not None:
        return cls.dto
    return None


def get_declarative_base_from_db(db: Session) -> Any:
    """
    Get the declarative base from a database session.

    Args:
        db: Database session that must have a model registry attached

    Returns:
        The declarative base from the model registry's database manager

    Raises:
        RuntimeError: If no model registry is available
    """
    # Get database manager from the session's model registry
    db_manager = get_db_manager_from_session(db)
    if db_manager and hasattr(db_manager, "Base"):
        return db_manager.Base

    raise RuntimeError(
        "No DatabaseManager found in session. "
        "Ensure the session was created from a properly configured ModelRegistry."
    )


T = TypeVar("T")
DtoT = TypeVar("DtoT")
ModelT = TypeVar("ModelT")


def validate_fields(cls, fields):
    """
    Validate that the fields exist on the model class.

    Args:
        cls: The model class
        fields: List of field names to validate

    Raises:
        HTTPException: If any field is invalid
    """
    if not fields:
        return

    # Get all column names from the model
    mapper = inspect(cls)
    valid_columns = set(column.key for column in mapper.columns)

    # Check for invalid fields
    invalid_fields = [field for field in fields if field not in valid_columns]
    if invalid_fields:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid field(s) requested: {', '.join(invalid_fields)}",
        )


def build_query(
    session,
    cls,
    joins=[],
    options=[],
    filters=[],
    order_by=None,
    limit=None,
    offset=None,
    **kwargs,
):
    to_return = session.query(cls)

    if len(joins) > 0:
        for join in joins:
            to_return = to_return.join(join)
    if len(filters) > 0:
        for filter_condition in filters:
            to_return = to_return.filter(filter_condition)
    to_return = to_return.filter_by(**kwargs)
    if len(options) > 0:
        for option in options:
            to_return = to_return.options(option)
    if order_by:
        to_return = to_return.order_by(*order_by)
    if limit:
        to_return = to_return.limit(limit)
    if offset:
        to_return = to_return.offset(offset)
    return to_return


def db_to_return_type(
    entity: Union[T, List[T]],
    return_type: Literal["db", "dict", "dto", "model"] = "dict",
    dto_type: Optional[Type[DtoT]] = None,
    fields: List[str] = [],
) -> Union[T, DtoT, ModelT, List[Union[T, DtoT, ModelT]]]:
    """
    Convert database entity to specified return type, handling nested objects and relationships.
    When return_type is "dict" and fields is provided, only the specified fields will be included.

    Args:
        entity: The database entity or list of entities to convert
        return_type: The desired return type format
        dto_type: The DTO type class to convert to
        fields: List of fields to include in the response (only for return_type="dict")

    Returns:
        The converted entity in the requested format
    """
    if entity is None:
        return None

    if return_type == "db":
        return entity

    elif return_type == "dict":
        # Convert to dictionary
        if isinstance(entity, list):
            if not entity:
                return []

            dict_entities = [obj_to_dict(item) for item in entity]
            # Filter fields if specified
            if fields:
                for entity_dict in dict_entities:
                    # Keep only requested fields
                    keys_to_remove = [
                        key for key in list(entity_dict.keys()) if key not in fields
                    ]
                    for key in keys_to_remove:
                        del entity_dict[key]

            return dict_entities
        else:
            # First convert the entire entity to dict to avoid expired attribute issues
            entity_dict = obj_to_dict(entity)
            # Filter fields if specified
            if fields:
                # Keep only requested fields
                keys_to_remove = [
                    key for key in list(entity_dict.keys()) if key not in fields
                ]
                for key in keys_to_remove:
                    del entity_dict[key]

            return entity_dict

    elif return_type in ["dto", "model"] and dto_type:
        if fields:
            # Fields parameter is only valid for dict return type
            raise HTTPException(
                status_code=400,
                detail="Fields parameter can only be used with return_type='dict'",
            )

        # Convert to DTO or Model
        if isinstance(entity, list):
            dto_instances = []
            for item in entity:
                # Convert each entity to dict first
                item_dict = obj_to_dict(item)
                # Process nested objects in the dict
                item_dict = _process_nested_objects(item_dict, dto_type)
                # Create DTO instance
                dto_instance = dto_type(**item_dict)
                dto_instances.append(dto_instance)

            return dto_instances
        else:
            # Convert entity to dict
            entity_dict = obj_to_dict(entity)
            # Process nested objects in the dict
            entity_dict = _process_nested_objects(entity_dict, dto_type)
            # Create DTO instance
            dto_instance = dto_type(**entity_dict)

            return dto_instance

    # Default return the original entity
    return entity


def _process_nested_objects(data_dict, parent_dto_type):
    """
    Process nested objects in a dictionary based on parent DTO type annotations.
    Handles recursive conversion of nested objects and lists.
    """
    result = {}

    # Get type hints from the DTO class and all its parent classes
    from typing import get_type_hints

    type_hints = get_type_hints(parent_dto_type)

    for key, value in data_dict.items():
        if key not in type_hints:
            # Keep original value if no type hint
            result[key] = value
            continue

        expected_type = type_hints[key]
        result[key] = _convert_based_on_type_hint(value, expected_type)

    return result


def _convert_based_on_type_hint(value, type_hint):
    """
    Convert a value based on its type hint.
    Handles primitive types, lists, optionals, nested objects, and enums.
    """
    # Handle None values
    if value is None:
        return None

    # If the type hint is typing.Any, just return the value as-is.
    # Attempting to instantiate typing.Any (or calling Any(**value)) causes
    # TypeError: "Any cannot be instantiated". Guard here to keep behavior
    # resilient when DTO annotations use `Any` or Optional[Any].
    try:
        from typing import Any as _TypingAny
    except Exception:
        _TypingAny = Any
    if type_hint is _TypingAny:
        return value

    # Get the origin type (for generics like List, Optional)
    origin = get_origin(type_hint)

    # Handle Optional types (Union with NoneType)
    if origin is Union:
        args = get_args(type_hint)
        if type(None) in args:
            # Find the non-None type
            for arg in args:
                if arg is not type(None):
                    return _convert_based_on_type_hint(value, arg)
            return value

    # Handle List types
    if origin is list:
        if not isinstance(value, list):
            return []

        # Get the list item type
        item_type = get_args(type_hint)[0]
        return [_convert_based_on_type_hint(item, item_type) for item in value]

    # Handle Dict types
    if origin is dict:
        if not isinstance(value, dict):
            return {}
        return value

    # Handle Enum types specifically
    if hasattr(type_hint, "__mro__") and "Enum" in [
        c.__name__ for c in type_hint.__mro__
    ]:
        # For enum types, handle differently
        if isinstance(value, type_hint):
            return value

        # If the value is already a valid enum value (like an int or string)
        try:
            # Try direct conversion first
            return type_hint(value)
        except (ValueError, TypeError):
            pass

        # Try to find by name if it's a string
        if isinstance(value, str):
            try:
                return getattr(type_hint, value)
            except (AttributeError, TypeError):
                pass

        # Return as-is if conversion fails
        return value

    # Handle primitive types
    if type_hint in (str, int, float, bool):
        return value

    # Check if the value is a SQLAlchemy object and type_hint is a BLL model
    if hasattr(value, "__dict__") and hasattr(value, "__table__"):
        # This is a SQLAlchemy object
        # Check if type_hint is a BLL model (has DB attribute)
        if hasattr(type_hint, "DB"):
            # Convert SQLAlchemy object to dictionary first
            value_dict = obj_to_dict(value)
            # Process nested objects recursively for the BLL model
            value_dict = _process_nested_objects(value_dict, type_hint)
            # Create BLL model instance
            return type_hint(**value_dict)
        else:
            # Convert to dict and process as before
            value_dict = obj_to_dict(value)
            # For model types with nested fields, process recursively
            if hasattr(type_hint, "__annotations__"):
                value_dict = _process_nested_objects(value_dict, type_hint)
            # Create an instance of the target type
            return type_hint(**value_dict)

    # Handle model types (custom classes)
    if isinstance(value, dict):
        # Already a dict, convert directly to the target type
        return type_hint(**value)

    if hasattr(value, "__dict__"):
        # Convert to dict first
        value_dict = obj_to_dict(value)

        # For model types with nested fields, process recursively
        if hasattr(type_hint, "__annotations__"):
            value_dict = _process_nested_objects(value_dict, type_hint)

        # Create an instance of the target type
        return type_hint(**value_dict)

    # Default case: return value as is
    return value


class HookDict(dict):
    """Dictionary subclass that allows attribute access to dictionary items"""

    def __getattr__(self, name):
        if name in self:
            value = self[name]
            if isinstance(value, dict):
                return HookDict(value)
            return value
        raise AttributeError(
            f"'{self.__class__.__name__}' object has no attribute '{name}'"
        )

    def __setattr__(self, name, value):
        self[name] = value


# Global hooks registry to properly handle inheritance
_hooks_registry = {}
hook_types = ["create", "update", "delete", "get", "list"]


def get_hooks_for_class(cls):
    """Get or create hooks for a class"""
    if cls not in _hooks_registry:
        # Create a new hooks dictionary for this class with all required hook types
        _hooks_registry[cls] = HookDict(
            {
                hook_type: HookDict({"before": [], "after": []})
                for hook_type in hook_types
            }
        )

    # Ensure all required hook types exist
    hooks = _hooks_registry[cls]

    for hook_type in hook_types:
        if hook_type not in hooks:
            hooks[hook_type] = HookDict({"before": [], "after": []})

    return _hooks_registry[cls]


# Descriptor for class-level hooks
class HooksDescriptor:
    def __get__(self, obj, objtype=None):
        if objtype is None:
            objtype = type(obj)
        return get_hooks_for_class(objtype)


# TODO Add a generator that will create `cls.RefMixin` and `cls.RefMixin.Optional`.
class BaseMixin:
    system = False
    seed_list = []

    @classmethod
    def DB(cls, declarative_base=None):
        raise RuntimeError(
            "Attempted to convert a model to a DB object but it was already a DB object!"
        )

    @classmethod
    def register_seed_items(cls, items):
        cls.seed_list.extend(items)

    # Class-level hooks property
    hooks = HooksDescriptor()

    @classmethod
    def create_foreign_key(cls, target_entity, model_registry=None, **kwargs):
        """
        Create a foreign key column that references the target entity's id.

        Args:
            target_entity: The target entity class to reference
            model_registry: ModelRegistry instance to get PK type from (optional)
            **kwargs: Additional column arguments

        Returns:
            SQLAlchemy Column with foreign key constraint
        """
        constraint_name = kwargs.pop("constraint_name", None)
        if not constraint_name:
            constraint_name = f"fk_{cls.__tablename__}_{target_entity.__tablename__}_id"
        else:
            constraint_name = constraint_name.format(
                source=cls.__tablename__, target=target_entity.__tablename__
            )

        logger.debug(
            f"Creating FK for {cls.__tablename__} -> {target_entity.__tablename__}: {constraint_name}"
        )
        ondelete = kwargs.pop("ondelete", None)
        fk = ForeignKey(
            f"{target_entity.__tablename__}.id", ondelete=ondelete, name=constraint_name
        )

        # Use provided model registry or default to String type
        pk_type = model_registry.DB.manager.PK_TYPE if model_registry else String
        return Column(pk_type, fk, **kwargs)

    @declared_attr
    def id(cls):
        # Default to String type for ID column - individual models can override if needed
        def generate_id():
            # Always generate string UUIDs for maximum compatibility
            return str(uuid.uuid4())

        return Column(
            String,
            primary_key=True,
            default=generate_id,
        )

    @declared_attr
    def created_at(cls):
        return Column(DateTime, default=func.now())

    @declared_attr
    def created_by_user_id(cls):
        # Use String type for user ID references for consistency
        return Column(String, nullable=True)

    @classmethod
    def user_has_read_access(
        cls,
        user_id,
        id,
        db,
        db_manager: DatabaseManager,
        minimum_role=None,
        referred=False,
    ):
        """
        Checks if a user has read access to a specific record.

        Args:
            user_id (str): The ID of the user requesting access
            id (str): The ID of the record to check
            db (Session): Database session
            db_manager (DatabaseManager, optional): Database manager instance
            minimum_role (str, optional): Minimum role required. Defaults to None.
            referred (bool, optional): Whether this check is for a referenced entity.

        Returns:
            bool: True if access is granted, False otherwise
        """
        from database.StaticPermissions import (
            PermissionResult,
            PermissionType,
            check_permission,
            is_root_id,
            is_system_id,
        )

        # Use the provided database session and manager
        # db parameter is already provided
        declarative_base = db_manager.Base

        # cls is already the SQLAlchemy model class
        db_cls = cls

        # Get the record
        record = db.query(db_cls).filter(db_cls.id == id).first()
        if not record:
            return False

        # Check for deleted records - only ROOT_ID can see them
        if hasattr(record, "deleted_at") and record.deleted_at is not None:
            return is_root_id(user_id)

        # Check system flag - only ROOT_ID and SYSTEM_ID can access system-flagged tables
        if hasattr(cls, "system") and getattr(cls, "system", False):
            if not is_system_id(user_id):
                return False

        # Check for records created by ROOT_ID - only ROOT_ID can access them
        if hasattr(record, "created_by_user_id") and record.created_by_user_id == env(
            "ROOT_ID"
        ):
            return user_id == env("ROOT_ID")

        # For non-referred checks, use the unified permission system
        if not referred:
            result, _ = check_permission(
                user_id,
                cls,
                id,
                db,
                declarative_base,
                PermissionType.VIEW if minimum_role is None else None,
                minimum_role=minimum_role,
            )
            return result == PermissionResult.GRANTED

        return False

    @classmethod
    def user_has_admin_access(cls, user_id, id, db, db_manager: DatabaseManager):
        """
        Check admin access using the optimized DB-level check (user_can_edit).
        (Consider removing this if not directly used elsewhere)

        Args:
            user_id: The ID of the user to check
            id: The ID of the record to check
            model_registry: ModelRegistry instance for database access

        Returns:
            bool: True if user has admin access, False otherwise
        """
        # Use the provided database session and manager
        # db parameter is already provided
        declarative_base = db_manager.Base

        has_access = check_permission(user_id, cls, id, db, declarative_base, "admin")
        return has_access[0].value == "granted"

    @classmethod
    def user_has_all_access(cls, user_id, id, db, db_manager: DatabaseManager):
        """
        Check highest level access using the optimized DB-level check (user_can_share).
        (Consider removing this if not directly used elsewhere)

        Args:
            user_id: The ID of the user to check
            id: The ID of the record to check
            model_registry: ModelRegistry instance for database access

        Returns:
            bool: True if user has all access, False otherwise
        """
        from database.StaticPermissions import (
            PermissionResult,
            PermissionType,
            check_permission,
        )

        # Use the provided database session and manager
        # db parameter is already provided
        declarative_base = db_manager.Base

        result, _ = check_permission(
            user_id, cls, id, db, declarative_base, PermissionType.SHARE
        )
        return result == PermissionResult.GRANTED

    @classmethod
    def user_can_create(cls, user_id, db, team_id=None, minimum_role="user", **kwargs):
        """
        Checks if a user can create an entity of this class.

        Args:
            user_id: ID of the user
            db: Database session
            team_id: Team ID if relevant
            minimum_role: Minimum role required
            **kwargs: Additional parameters

        Returns:
            bool: True if the user can create, False otherwise
        """
        from database.StaticPermissions import (
            check_access_to_all_referenced_entities,
            is_root_id,
            is_system_user_id,
            user_can_create_referenced_entity,
        )

        # Root user can create anything
        if is_root_id(user_id):
            return True

        # Check system flag - only ROOT_ID and SYSTEM_ID can create in system-flagged tables
        if hasattr(cls, "system") and getattr(cls, "system", False):
            return is_root_id(user_id) or is_system_user_id(user_id)

        # Check if user has access to all referenced entities
        can_access, missing_entity = check_access_to_all_referenced_entities(
            user_id, cls, db, minimum_role, **kwargs
        )
        if not can_access:
            return False

        # Check create permissions based on create_permission_reference
        can_create, _ = user_can_create_referenced_entity(
            cls, user_id, db, minimum_role, **kwargs
        )
        return can_create

    @classmethod
    @with_session
    def create(
        cls: Type[T],
        requester_id: str,
        model_registry,
        return_type: Literal["db", "dict", "dto", "model"] = "dict",
        fields=[],
        override_dto: Optional[Type[DtoT]] = None,
        **kwargs,
    ) -> T:
        """Create a new database entity."""
        from database.StaticPermissions import (
            PermissionResult,
            PermissionType,
            is_root_id,
            is_system_user_id,
        )

        # Extract db and db_manager from kwargs (injected by decorator)
        db = kwargs.pop("db")
        db_manager = kwargs.pop("db_manager")

        # Validate fields parameter
        validate_fields(cls, fields)

        # Check system flag - only ROOT_ID and SYSTEM_ID can create in system-flagged tables
        if hasattr(cls, "system") and getattr(cls, "system", False):
            if not (is_root_id(requester_id) or is_system_user_id(requester_id)):
                raise HTTPException(
                    status_code=403,
                    detail=f"Only system users can create {cls.__name__} records",
                )

        # Check if the user can create this entity
        # Remove user_id from kwargs if it exists to prevent duplicate parameters
        create_kwargs = kwargs.copy()
        if "user_id" in create_kwargs:
            create_kwargs.pop("user_id")

        if not cls.user_can_create(requester_id, db, **create_kwargs):
            raise HTTPException(
                status_code=403, detail=f"Not authorized to create {cls.__name__}"
            )

        # Generate a UUID if not provided
        data = dict(kwargs)
        if "id" not in data:
            data["id"] = str(uuid.uuid4())

        # Add created_by_user_id if the entity has this column
        if hasattr(cls, "created_by_user_id"):
            data["created_by_user_id"] = requester_id

        # Get hooks for before_create
        hooks = cls.hooks
        if "create" in hooks and "before" in hooks["create"]:
            before_hooks = hooks["create"]["before"]
            if before_hooks:
                hook_dict = HookDict(data)
                for hook in before_hooks:
                    hook(hook_dict, db)
                # Extract data from the hook dict
                data = {k: v for k, v in hook_dict.items()}

        # Create the entity
        entity = cls(**data)
        db.add(entity)
        db.flush()
        if cls.__tablename__ == "users":
            entity.created_by_user_id = entity.id
        db.commit()
        db.refresh(entity)

        # Get hooks for after_create
        if "create" in hooks and "after" in hooks["create"]:
            after_hooks = hooks["create"]["after"]
            if after_hooks:
                for hook in after_hooks:
                    hook(entity, db)

        # Convert to requested return type
        return db_to_return_type(entity, return_type, override_dto, fields)

    @classmethod
    @with_session
    def count(
        cls: Type[T],
        requester_id: str,
        model_registry,
        joins=[],
        options=[],
        filters=[],
        check_permissions=True,
        minimum_role=None,
        **kwargs,
    ) -> int:
        """
        Count records with permission filtering.

        Args:
            requester_id: The ID of the user making the request
            db: Database session
            joins: List of join conditions
            options: List of query options
            filters: List of filter conditions
            check_permissions: Whether to apply permission filtering
            minimum_role: Minimum role required for access
            **kwargs: Additional filter criteria

        Returns:
            int: Number of matching records
        """
        # Extract db and db_manager from kwargs (injected by decorator)
        db = kwargs.pop("db")
        db_manager = kwargs.pop("db_manager")

        validate_columns(cls, **kwargs)

        # Use injected database session and manager from decorator
        # db and db_manager are already available from @with_session
        declarative_base = db_manager.Base

        # Get the SQLAlchemy model
        db_cls = cls

        # Add permission filtering
        if check_permissions:
            # Import here to avoid circular imports
            from database.StaticPermissions import (
                PermissionType,
                generate_permission_filter,
                is_root_id,
            )

            perm_filter = generate_permission_filter(
                requester_id,
                cls,
                db,
                declarative_base,
                PermissionType.VIEW,
                minimum_role=minimum_role,
                db_manager=db_manager,
            )
            if filters:
                filters.append(perm_filter)
            else:
                filters = [perm_filter]

            # Only add deleted_at filter for non-ROOT users
            if hasattr(db_cls, "deleted_at") and not is_root_id(requester_id):
                filters.append(db_cls.deleted_at == None)

        # Build query with all filters
        query = build_query(db, db_cls, joins, options, filters, **kwargs)

        # Get count
        return query.count()

    @classmethod
    @with_session
    def exists(
        cls: Type[T],
        requester_id: str,
        model_registry,
        joins=[],
        options=[],
        filters=[],
        **kwargs,
    ) -> bool:
        """Check if at least one record matching the criteria exists and is accessible."""
        from database.StaticPermissions import PermissionType, is_root_id

        # Extract db and db_manager from kwargs (injected by decorator)
        db = kwargs.pop("db")
        db_manager = kwargs.pop("db_manager")

        # Validate kwargs
        validate_columns(cls, **kwargs)

        # Use injected database session and manager from decorator
        # db and db_manager are already available from @with_session
        declarative_base = db_manager.Base

        # Get the SQLAlchemy model
        db_cls = cls

        # Apply proper permission filtering
        if "id" in kwargs:
            # Specific ID lookup - use direct permission check
            record_id = kwargs["id"]

            # Get the record to check if it exists
            record = db.query(db_cls).filter(db_cls.id == record_id).first()
            if record is None:
                return False

            # For User model, handle special cases
            if cls.__tablename__ == "users":
                # Check if the user is looking up their own record
                if (
                    hasattr(record, "deleted_at")
                    and record.deleted_at is not None
                    and not is_root_id(requester_id)
                ):
                    return False

                if requester_id == record_id:
                    return True

                # Check if record was created by ROOT_ID - only ROOT_ID can access
                if (
                    hasattr(record, "created_by_user_id")
                    and record.created_by_user_id == env("ROOT_ID")
                    and not is_root_id(requester_id)
                ):
                    return False

            # Otherwise, use permission system with a special direct check for User model
            if cls.__tablename__ == "users":
                # Use the model's user_has_read_access method if available
                return cls.user_has_read_access(
                    requester_id, record_id, db, db_manager=db_manager
                )
            else:
                # Add permission filter
                perm_filter = generate_permission_filter(
                    requester_id,
                    cls,
                    db,
                    declarative_base,
                    PermissionType.VIEW,
                    db_manager=db_manager,
                )
                if filters:
                    filters.append(perm_filter)
                else:
                    filters = [perm_filter]

                # Add deleted_at filter for non-ROOT users
                if hasattr(db_cls, "deleted_at") and not is_root_id(requester_id):
                    filters.append(db_cls.deleted_at == None)

                # Build a query with the filters
                query = build_query(
                    db, db_cls, joins=joins, options=options, filters=filters, **kwargs
                )

                # Check if any results exist with permission filtering
                return query.first() is not None
        else:
            # Collection lookup - use standard permission filter
            # Add permission filter
            perm_filter = generate_permission_filter(
                requester_id, cls, db, declarative_base, PermissionType.VIEW
            )
            if filters:
                filters.append(perm_filter)
            else:
                filters = [perm_filter]

            # Add deleted_at filter for non-ROOT users
            if hasattr(db_cls, "deleted_at") and not is_root_id(requester_id):
                filters.append(db_cls.deleted_at == None)

            # Build a query with the filters
            query = build_query(
                db, db_cls, joins=joins, options=options, filters=filters, **kwargs
            )

            # Check if any results exist with permission filtering
            return query.first() is not None

    @classmethod
    @with_session
    def get(
        cls: Type[T],
        requester_id: str,
        model_registry,
        return_type: Literal["db", "dict", "dto", "model"] = "dict",
        joins=[],
        options=[],
        filters=[],
        fields=[],
        allow_nonexistent=False,
        override_dto: Optional[Type[DtoT]] = None,
        **kwargs,
    ) -> T:
        # Extract db and db_manager from kwargs (injected by decorator)
        db = kwargs.pop("db")
        db_manager = kwargs.pop("db_manager")

        validate_columns(cls, **kwargs)

        # Validate that fields exist on the model
        validate_fields(cls, fields)

        # Use injected database session and manager from decorator
        # db and db_manager are already available from @with_session
        declarative_base = db_manager.Base

        # Get the SQLAlchemy model
        db_cls = cls

        # Only add deleted_at filter for non-ROOT users
        from database.StaticPermissions import is_root_id

        if hasattr(db_cls, "deleted_at") and not is_root_id(requester_id):
            filters = filters + [db_cls.deleted_at == None]

        # Apply permission filter
        perm_filter = generate_permission_filter(
            requester_id,
            cls,
            db,
            declarative_base,
            PermissionType.VIEW,
            db_manager=db_manager,
        )  # Default VIEW for get
        if perm_filter is not None:
            if filters:
                filters.append(perm_filter)
            else:
                filters = [perm_filter]

        # Build query with permission filter included
        query = build_query(db, db_cls, joins, options, filters, **kwargs)

        # Get the single result
        try:
            result = query.one()
            to_return = db_to_return_type(
                result,
                return_type,
                get_dto_class(cls, override_dto),
                fields=fields,
            )

            if to_return is None:
                logger.warning(
                    f"None is about to be returned from {cls.__name__} get(): return type {return_type}/{override_dto}, {joins}, {options}, {filters}, {kwargs}"
                )
            else:
                logger.debug(
                    f"Returning from {cls.__name__} get: {to_return} ({return_type})"
                )
            return to_return
        except NoResultFound:
            # Special case for entity_id="self" and providing self_entity
            if kwargs.get("entity_id") == "self" and "self_entity" in kwargs:
                return db_to_return_type(
                    kwargs["self_entity"],
                    return_type,
                    get_dto_class(cls, override_dto),
                    fields=fields,
                )
            if allow_nonexistent:
                return None
            raise HTTPException(status_code=404, detail=gen_not_found_msg(cls.__name__))
        except MultipleResultsFound:
            raise HTTPException(
                status_code=409,
                detail=f"Request uncovered multiple {cls.__name__} when only one was expected.",
            )

    @classmethod
    @with_session
    def list(
        cls: Type[T],
        requester_id: str,
        model_registry,
        return_type: Literal["db", "dict", "dto", "model"] = "dict",
        joins=[],
        options=[],
        filters=[],
        order_by=None,
        limit=None,
        offset=None,
        fields=[],
        override_dto: Optional[Type[DtoT]] = None,
        check_permissions=True,
        minimum_role=None,
        **kwargs,
    ) -> List[T]:
        """
        List records with permission filtering.

        Args:
            requester_id: The ID of the user making the request
            db: Database session
            return_type: The return type format ("db", "dict", "dto", "model")
            joins: List of join conditions
            options: List of query options
            filters: List of filter conditions
            order_by: Order by criteria
            limit: Maximum number of records to return
            offset: Number of records to skip
            fields: List of fields to include in the response (only for return_type="dict")
            override_dto: Optional DTO class override
            check_permissions: Whether to apply permission filtering (defaults to True)
            minimum_role: Minimum role required for team access (defaults to None)
            **kwargs: Additional filter criteria

        Returns:
            List of records in the specified return format
        """
        # Extract db and db_manager from kwargs (injected by decorator)
        db = kwargs.pop("db")
        db_manager = kwargs.pop("db_manager")

        validate_columns(cls, **kwargs)

        # Validate fields parameter
        if fields and return_type != "dict":
            raise HTTPException(
                status_code=400,
                detail="Fields parameter can only be used with return_type='dict'",
            )

        # Validate that fields exist on the model
        validate_fields(cls, fields)

        # Use injected database session and manager from decorator
        # db and db_manager are already available from @with_session
        declarative_base = db_manager.Base

        # cls is already the SQLAlchemy model class
        db_cls = cls

        # Only add deleted_at filter for non-ROOT users
        from database.StaticPermissions import is_root_id

        if hasattr(db_cls, "deleted_at") and not is_root_id(requester_id):
            if filters:
                filters.append(db_cls.deleted_at == None)
            else:
                filters = [db_cls.deleted_at == None]

        # Apply permission filter
        perm_filter = generate_permission_filter(
            requester_id,
            cls,
            db,
            declarative_base,
            PermissionType.VIEW,
            db_manager=db_manager,
        )  # Default VIEW for list
        if perm_filter is not None:
            if filters:
                filters.append(perm_filter)
            else:
                filters = [perm_filter]

        query = build_query(
            db, db_cls, joins, options, filters, order_by, limit, offset, **kwargs
        )

        # Fetch records based on filtered query
        to_return = query.all()

        logger.debug(f"To return: {', '.join([str(item) for item in to_return])}")
        if to_return is None:
            logger.warning(
                f"None is about to be returned from {cls.__name__} list(): return type {return_type}/{override_dto}, {joins}, {options}, {filters}, {kwargs}"
            )
        else:
            logger.debug(
                f"Returning from {cls.__name__} list: {to_return} ({return_type})"
            )

        return db_to_return_type(
            to_return,
            return_type,
            get_dto_class(cls, override_dto),
            fields=fields,
        )


class UpdateMixin:
    """Adds update and delete hooks to the hooks registry"""

    # Initialize hooks for update and delete
    @classmethod
    def _initialize_update_hooks(cls):
        """Initialize update and delete hooks for this class if not already present"""
        hooks = get_hooks_for_class(cls)

        # Only add the keys if they don't exist yet
        if "update" not in hooks:
            hooks["update"] = HookDict({"before": [], "after": []})
        if "delete" not in hooks:
            hooks["delete"] = HookDict({"before": [], "after": []})

        return hooks

    @declared_attr
    def updated_at(cls):
        return Column(DateTime, default=func.now(), onupdate=func.now())

    @declared_attr
    def updated_by_user_id(cls):
        # Use String type for user ID references for consistency
        return Column(String, nullable=True)

    @classmethod
    @with_session
    def update(
        cls: Type[T],
        requester_id: str,
        model_registry,
        new_properties=None,
        return_type: Literal["db", "dict", "dto", "model"] = "dict",
        filters=[],
        fields=[],
        override_dto: Optional[Type[DtoT]] = None,
        check_permissions=True,
        allow_nonexistent: bool = False,
        **kwargs,
    ) -> T:
        """Update a database entity with new properties."""
        from database.StaticPermissions import (
            PermissionResult,
            PermissionType,
            is_root_id,
            is_system_id,
            is_system_user_id,
        )

        # Extract db and db_manager from kwargs (injected by decorator)
        db = kwargs.pop("db")
        db_manager = kwargs.pop("db_manager")

        # Validate fields parameter
        validate_fields(cls, fields)

        # Use injected database session and manager from decorator
        # db and db_manager are already available from @with_session
        declarative_base = db_manager.Base

        # cls is already the SQLAlchemy model class
        db_cls = cls

        # Build the query to find the entity
        additional_filters = []
        if check_permissions:
            # Generate permission filter for EDIT access
            from database.StaticPermissions import generate_permission_filter

            permission_filter = generate_permission_filter(
                requester_id,
                cls,
                db,
                declarative_base,
                PermissionType.EDIT,
                db_manager=db_manager,
            )
            additional_filters.append(permission_filter)

        query = build_query(
            db,
            db_cls,
            filters=filters + additional_filters,
            **kwargs,
        )

        try:
            entity = query.one()
        except NoResultFound:
            if allow_nonexistent:
                logger.debug(f"{cls.__name__} not found for update. Skipping.")
                return None
            raise HTTPException(status_code=404, detail=f"{cls.__name__} not found")
        except MultipleResultsFound:
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: Multiple {cls.__name__} matched query criteria",
            )

        # Check for system flag - only ROOT_ID and SYSTEM_ID can modify system-flagged tables
        if hasattr(cls, "system") and getattr(cls, "system", False):
            if not (is_root_id(requester_id) or is_system_user_id(requester_id)):
                raise HTTPException(
                    status_code=403,
                    detail=f"Only system users can modify {cls.__name__} records",
                )

        # Check if the record was created by ROOT_ID or SYSTEM_ID
        if hasattr(entity, "created_by_user_id"):
            if entity.created_by_user_id == env("ROOT_ID") and not is_root_id(
                requester_id
            ):
                raise HTTPException(
                    status_code=403,
                    detail=f"Only ROOT can modify records created by ROOT",
                )

            if entity.created_by_user_id == env("SYSTEM_ID") and not (
                is_root_id(requester_id) or is_system_user_id(requester_id)
            ):
                raise HTTPException(
                    status_code=403,
                    detail=f"Only system users can modify records created by SYSTEM",
                )

        # Copy updated properties to avoid modifying the input
        updated = dict(new_properties)

        # Ensure created_by_user_id and id cannot be modified
        if "created_by_user_id" in updated:
            del updated["created_by_user_id"]
        if "id" in updated:
            del updated["id"]

        # Set updated_by_user_id and updated_at
        if hasattr(cls, "updated_by_user_id"):
            updated["updated_by_user_id"] = requester_id
        if hasattr(cls, "updated_at"):
            updated["updated_at"] = func.now()

        # Get hooks for before_update
        hooks = cls.hooks
        if "update" in hooks and "before" in hooks["update"]:
            before_hooks = hooks["update"]["before"]
            if before_hooks:
                hook_dict = HookDict(updated)
                for hook in before_hooks:
                    hook(hook_dict, db)
                # Extract updates from the hook dict
                updated = {k: v for k, v in hook_dict.items()}

        # Apply updates
        for key, value in updated.items():
            setattr(entity, key, value)

        # Commit changes
        db.commit()
        db.refresh(entity)

        # Get hooks for after_update
        hooks = cls.hooks["update"]["after"]
        if hooks:
            for hook in hooks:
                hook(entity, updated, db)

        # Convert to requested return type
        return db_to_return_type(entity, return_type, override_dto, fields)

    @declared_attr
    def deleted_at(cls):
        return Column(DateTime, default=None)

    @declared_attr
    def deleted_by_user_id(cls):
        # Use String type for user ID references for consistency
        return Column(String, nullable=True)

    @classmethod
    @with_session
    def delete(
        cls: Type[T],
        requester_id: str,
        model_registry,
        filters=[],
        check_permissions=True,
        allow_nonexistent: bool = False,
        **kwargs,
    ):
        """
        Soft delete a database entity by setting deleted_at and deleted_by_user_id.
        Enforces permission checks and system flag restrictions.
        """
        from database.StaticPermissions import (
            PermissionResult,
            PermissionType,
            is_root_id,
            is_system_id,
            is_system_user_id,
        )

        # Extract db and db_manager from kwargs (injected by decorator)
        db = kwargs.pop("db")
        db_manager = kwargs.pop("db_manager")

        # Use injected database session and manager from decorator
        # db and db_manager are already available from @with_session
        declarative_base = db_manager.Base

        # Get the SQLAlchemy model
        db_cls = cls

        # Build the query with permission checks
        additional_filters = []
        if check_permissions:
            # Generate permission filter for DELETE access
            from database.StaticPermissions import generate_permission_filter

            permission_filter = generate_permission_filter(
                requester_id,
                cls,
                db,
                declarative_base,
                PermissionType.DELETE,
                db_manager=db_manager,
            )
            additional_filters.append(permission_filter)

        query = build_query(
            db,
            db_cls,
            filters=filters + additional_filters,
            **kwargs,
        )

        try:
            entity = query.one()
        except NoResultFound:
            if allow_nonexistent:
                logger.debug(f"{cls.__name__} not found for deletion. Skipping.")
                return
            raise HTTPException(status_code=404, detail=f"{cls.__name__} not found")
        except MultipleResultsFound:
            raise HTTPException(
                status_code=500, detail=f"Multiple {cls.__name__} found"
            )

        # Check for system flag - only ROOT_ID and SYSTEM_ID can delete from system-flagged tables
        if hasattr(cls, "system") and getattr(cls, "system", False):
            if not (is_root_id(requester_id) or is_system_user_id(requester_id)):
                raise HTTPException(
                    status_code=403,
                    detail=f"Only system users can delete {cls.__name__} records",
                )

        # Check if the record was created by ROOT_ID or SYSTEM_ID
        if hasattr(entity, "created_by_user_id"):
            if entity.created_by_user_id == env("ROOT_ID") and not is_root_id(
                requester_id
            ):
                raise HTTPException(
                    status_code=403,
                    detail=f"Only ROOT can delete records created by ROOT",
                )

            if entity.created_by_user_id == env("SYSTEM_ID") and not (
                is_root_id(requester_id) or is_system_user_id(requester_id)
            ):
                raise HTTPException(
                    status_code=403,
                    detail=f"Only system users can delete records created by SYSTEM",
                )
        if hasattr(entity, "created_by_user_id"):
            if (
                entity.created_by_user_id is not None
                and entity.created_by_user_id != requester_id
                and not (is_root_id(requester_id) or is_system_user_id(requester_id))
            ):
                raise HTTPException(
                    status_code=403,
                    detail=f"Only the creator can delete this record",
                )
        # Get hooks for before_delete
        hooks = cls.hooks
        if "delete" in hooks and "before" in hooks["delete"]:
            before_hooks = hooks["delete"]["before"]
            if before_hooks:
                for hook in before_hooks:
                    hook(entity, db)

        # Set deleted fields
        if hasattr(cls, "deleted_at"):
            from datetime import datetime, timezone

            setattr(entity, "deleted_at", datetime.now(timezone.utc))
        if hasattr(cls, "deleted_by_user_id"):
            setattr(entity, "deleted_by_user_id", requester_id)

        # Commit changes
        db.commit()

        # Get hooks for after_delete
        hooks = cls.hooks["delete"]["after"]
        if hooks:
            for hook in hooks:
                hook(entity, db)


class ParentMixin:
    @declared_attr
    def parent_id(cls):
        # Use String type for self-referential parent IDs for consistency
        return Column(
            String,
            ForeignKey(f"{cls.__tablename__}.id"),
            nullable=True,
            index=True,
        )

    @declared_attr
    def parent(cls):
        return relationship(
            cls,
            remote_side=[cls.id],
            backref="children",
            primaryjoin=lambda: cls.id == cls.parent_id,
        )


class ImageMixin:
    @declared_attr
    def image_url(cls):
        return Column(String, nullable=True, comment="URL of the image")


class _OptionalRefMixin:
    """Base class for optional reference mixins"""

    pass


def create_reference_mixin(target_entity, **kwargs):
    """
    Create a reference mixin class for a target entity.

    This function dynamically generates a reference mixin class for an entity,
    which provides a standard way to create foreign key relationships and
    associated SQLAlchemy relationships. Each generated mixin includes both
    a required version (non-nullable foreign key) and an Optional inner class
    (nullable foreign key).

    Args:
        target_entity: The target entity class to reference
        **kwargs: Additional parameters:
            - comment: Optional comment for the foreign key
            - backref_name: Custom backref name (defaults to tablename)
            - nullable: Whether the non-Optional version should be nullable (defaults to False)

    Returns:
        type: A reference mixin class with an Optional inner class

    Example:
        ```python
        # Create a reference mixin for the User entity
        UserRefMixin = create_reference_mixin(User)

        # Use the mixin in a new entity class (required user relationship)
        class Document(Base, BaseMixin, UserRefMixin):
            __tablename__ = "documents"
            title = Column(String, nullable=False)
            content = Column(String)

        # Use the Optional version for nullable relationships
        class Comment(Base, BaseMixin, UserRefMixin.Optional):
            __tablename__ = "comments"
            text = Column(String, nullable=False)

        # Create a customized reference mixin
        CustomUserRefMixin = create_reference_mixin(
            User,
            comment="Reference to the document owner",
            backref_name="owned_documents",
            nullable=True  # Even the non-Optional version will be nullable
        )
        ```
    """
    entity_name = target_entity.__name__
    lower_name = entity_name.lower()
    comment = kwargs.get("comment", None)
    backref_name = kwargs.get("backref_name", None)
    nullable = kwargs.get("nullable", False)

    # Define the main mixin class
    class RefMixin:
        """Reference mixin for providing a relationship to another entity."""

        pass

    # Create and set the ID attribute
    @declared_attr
    def id_attr(cls):
        fk_kwargs = {}
        if comment:
            fk_kwargs["comment"] = comment
        return cls.create_foreign_key(target_entity, nullable=nullable, **fk_kwargs)

    # Set the foreign key attribute
    setattr(RefMixin, f"{lower_name}_id", id_attr)

    # Create and set the relationship attribute
    @declared_attr
    def rel_attr(cls):
        # Use provided backref_name or generate one based on the class's tablename
        # This will be evaluated at class definition time
        if backref_name is not None:
            backref_val = backref_name
        else:
            # Get the tablename from the class that's using this mixin
            backref_val = cls.__tablename__ if hasattr(cls, "__tablename__") else None

        return relationship(
            target_entity.__name__,
            backref=backref_val,
        )

    # Set the relationship attribute
    setattr(RefMixin, lower_name, rel_attr)

    # Set proper class name and module
    RefMixin.__name__ = f"{entity_name}RefMixin"
    RefMixin.__module__ = "database.AbstractDatabaseEntity"

    # Define the optional variant
    class OptionalRefMixin:
        """Optional reference mixin where the foreign key is nullable."""

        pass

    # Create and set the nullable ID attribute
    @declared_attr
    def opt_id_attr(cls):
        fk_kwargs = {}
        if comment:
            fk_kwargs["comment"] = comment
        return cls.create_foreign_key(
            target_entity, **fk_kwargs
        )  # nullable=True by default

    # Set the foreign key attribute for the optional variant
    setattr(OptionalRefMixin, f"{lower_name}_id", opt_id_attr)

    # Create the relationship for the optional variant
    @declared_attr
    def opt_rel_attr(cls):
        # Use provided backref_name or generate one based on the class's tablename
        if backref_name is not None:
            backref_val = backref_name
        else:
            # Get the tablename from the class that's using this mixin
            backref_val = cls.__tablename__ if hasattr(cls, "__tablename__") else None

        return relationship(
            target_entity.__name__,
            backref=backref_val,
        )

    # Set the relationship attribute for the optional variant
    setattr(OptionalRefMixin, lower_name, opt_rel_attr)

    # Set proper class name and module
    OptionalRefMixin.__name__ = f"_{entity_name}Optional"
    OptionalRefMixin.__module__ = "database.AbstractDatabaseEntity"

    # Attach the optional variant to the main mixin
    RefMixin.Optional = OptionalRefMixin

    return RefMixin


# TODO Why is this function here?
def get_reference_mixin(entity_name, **kwargs):
    """
    Dynamically import an entity and create a reference mixin.

    This function allows you to create reference mixins without
    having to import the target entity directly, which can help
    avoid circular import issues.

    Args:
        entity_name: The name of the entity class (e.g., "User", "Team")
        **kwargs: Additional parameters for create_reference_mixin:
            - comment: Optional comment for the foreign key
            - backref_name: Custom backref name (defaults to tablename)
            - nullable: Whether the non-Optional version should be nullable (defaults to False)

    Returns:
        type: A reference mixin class with an Optional inner class

    Raises:
        ValueError: If entity_name is not in the known entities mapping
        ImportError: If the module or entity cannot be imported

    Example:
        ```python
        # Get a reference mixin for User without importing it directly
        UserRefMixin = get_reference_mixin("User")

        # Use the mixin in a new entity class
        class Document(Base, BaseMixin, UserRefMixin):
            __tablename__ = "documents"
            title = Column(String, nullable=False)
            content = Column(String)

        # Get a customized reference mixin
        TeamRefMixin = get_reference_mixin(
            "Team",
            backref_name="team_documents",
            nullable=True
        )
        ```
    """
    # Map entity names to their module paths
    entity_modules = {
        # Add other entities as needed
    }

    if entity_name not in entity_modules:
        raise ValueError(f"Unknown entity: {entity_name}")

    # Import the entity dynamically
    import importlib

    module = importlib.import_module(entity_modules[entity_name])
    entity = getattr(module, entity_name)

    # Create and return the reference mixin
    return create_reference_mixin(entity, **kwargs)
