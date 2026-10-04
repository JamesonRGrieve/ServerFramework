# SPDX-License-Identifier: AGPL-3.0-or-later
import inspect
from enum import Enum as PyEnum  # Import Python Enum
from typing import Any, Optional, Type, TypeVar

import stringcase
from sqlalchemy import (  # Import inspect and Integer
    Integer,
    and_,
    exists,
    false,
    func,
    literal,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.expression import CTE

# REMOVED: from database.DB_Auth import Permission, Role, Team, UserTeam # Assuming these are the correct locations
from zephyrex.database.HookRegistries import _acl_hooks, _invitation_hooks
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

# Type variable for generic models
T = TypeVar("T")

# Define system IDs with different permission levels
ROOT_ID = env("ROOT_ID")
SYSTEM_ID = env("SYSTEM_ID")
TEMPLATE_ID = env("TEMPLATE_ID")

# The accounts table, whose read rule is its own (see the users rule in
# generate_permission_filter).
USERS_TABLE = "users"


def _active(model: Any) -> Any:
    """The predicate for a permission, membership or invitation row that
    still grants access: not expired (no expiry, or one still ahead) and
    not deleted. Encoded once because it is security-critical.

    The deletion half matters because these rows are read through core
    ``select()`` subqueries, which the ORM's automatic ``deleted_at IS NULL``
    filter does not reach: a revoked grant or a removed team membership
    would otherwise go on granting access.
    """
    not_expired = or_(model.expires_at.is_(None), model.expires_at > func.now())
    if hasattr(model, "deleted_at"):
        return and_(not_expired, model.deleted_at.is_(None))
    return not_expired


def is_any_internal_id(user_id: str) -> bool:
    """Check if the user ID is any of the system IDs."""
    return user_id in (ROOT_ID, SYSTEM_ID, TEMPLATE_ID)


def is_root_id(user_id: str) -> bool:
    """Check if the user ID is the ROOT_ID."""
    return user_id == ROOT_ID


def is_system_id(user_id: str) -> bool:
    """Check if the user ID is the SYSTEM_ID."""
    return user_id == SYSTEM_ID


def is_system_user_id(user_id: str) -> bool:
    """Alias of :func:`is_system_id`. Some callers prefer the
    `_user_` form; both names resolve to the same SYSTEM_ID check so
    that authorization helpers do not fork on naming."""
    return user_id == SYSTEM_ID


def referenced_class(db_cls: Any, ref_name: str) -> Optional[Any]:
    """The mapped class ``db_cls`` references as ``ref_name``, found through
    its relationship of that name, else the foreign key on its
    ``<ref_name>_id`` column, else the table the reference names by the
    builder's convention (``conversation`` → ``conversations``): models
    built from Pydantic declare references as ``_id`` columns, often with
    neither a relationship nor a constraint. None when the class has no such
    column or no table answers to it."""
    ref_attr = getattr(db_cls, ref_name, None)
    if (
        ref_attr is not None
        and hasattr(ref_attr, "property")
        and hasattr(ref_attr.property, "mapper")
    ):
        return ref_attr.property.mapper.class_
    table = getattr(db_cls, "__table__", None)
    column = table.c.get(f"{ref_name}_id") if table is not None else None
    if column is None:
        return None
    if column.foreign_keys:
        target_name = next(iter(column.foreign_keys)).column.table.name
    else:
        from zephyrex.lib.AbstractPydantic2 import default_name_processor

        target_name = default_name_processor.generate_resource_name(
            stringcase.pascalcase(ref_name), use_plural=True
        )
    for mapper in db_cls.registry.mappers:
        if mapper.local_table.name == target_name:
            return mapper.class_
    return None


def declarative_base_of(db_cls: Any) -> Any:
    """The declarative base a mapped class was built on: what the
    permission checks resolve the auth tables (roles, teams) through."""
    for base in db_cls.__mro__:
        if "registry" in base.__dict__ and "__table__" not in base.__dict__:
            return base
    raise ValueError(f"{db_cls.__name__} is not a declaratively mapped class")


def is_template_id(user_id: str) -> bool:
    """Check if the user ID is the TEMPLATE_ID."""
    return user_id == TEMPLATE_ID


def inherits_access(db_cls: Any) -> bool:
    """Whether records of ``db_cls`` take their access only from the
    records they reference (its ``permission_references``) and from
    Permission rows. Such a record's own owner, team and creator columns
    grant nothing: a user who wrote a message keeps it only while they can
    see its conversation, and a row planted on someone else's parent is
    not its planter's."""
    return bool(getattr(db_cls, "permission_references", None))


def server_row_modifiable_by(user_id: str, created_by_user_id: Optional[str]) -> bool:
    """Whether ``user_id`` may change a row ``created_by_user_id`` wrote,
    as far as its author goes: what ROOT wrote is ROOT's, and what SYSTEM
    or the template account wrote is ROOT's and SYSTEM's. Anyone may
    change what a user wrote, given access."""
    if created_by_user_id == ROOT_ID:
        return is_root_id(user_id)
    if created_by_user_id in (SYSTEM_ID, TEMPLATE_ID):
        return is_root_id(user_id) or is_system_id(user_id)
    return True


def can_access_system_record(
    user_id: str, record_user_id: str, minimum_role: Optional[str] | None = None
) -> bool:
    """
    Determine if a user can access a system-owned record based on the system ID type.

    Args:
        user_id: The ID of the user requesting access
        record_user_id: The system ID that owns the record
        minimum_role: The minimum role required (if applicable)

    Returns:
        bool: True if access is granted, False otherwise
    """
    # ROOT_ID records are only accessible by ROOT_ID
    if record_user_id == ROOT_ID:
        return user_id == ROOT_ID

    # SYSTEM_ID records are readable by anyone, but only ROOT_ID and SYSTEM_ID can modify
    if record_user_id == SYSTEM_ID:
        if user_id in (ROOT_ID, SYSTEM_ID):
            return True
        # Regular users can only VIEW
        return minimum_role in (None, "user")

    # TEMPLATE_ID records are viewable, copyable, shareable and executable by all
    # but only ROOT_ID and SYSTEM_ID can modify (EDIT/DELETE)
    if record_user_id == TEMPLATE_ID:
        if user_id in (ROOT_ID, SYSTEM_ID, TEMPLATE_ID):
            return True
        # For regular users, allow basic access
        return minimum_role in (None, "user")

    # Default: system records can only be accessed by system users
    return False


def gen_not_found_msg(classname):
    """Generate a standard 'not found' message for a given class.

    Delegates to the canonical implementation in
    :mod:`zephyrex.logic.AbstractLogicManager.models` so the message format
    lives in exactly one place. The import is deferred to call time to avoid a
    circular import (``StaticPermissions`` is imported by the BLL layer during
    that package's own initialization).
    """
    from zephyrex.logic.AbstractLogicManager.models import (
        gen_not_found_msg as _canonical_gen_not_found_msg,
    )

    return _canonical_gen_not_found_msg(classname)


def validate_columns(cls, updated=None, **kwargs):
    """
    Validate that the provided column names exist in the model.

    Args:
        cls: The model class
        updated: Dictionary of fields to update
        **kwargs: Additional filter parameters

    Raises:
        ValueError: If invalid columns are provided
    """
    valid_columns = {column.name for column in cls.__table__.columns}
    invalid_keys = [key for key in kwargs if key not in valid_columns]
    logger.debug(f"Valid columns for {cls.__name__}: {valid_columns}")
    logger.debug(f"Invalid keys for {cls.__name__}: {invalid_keys}")
    invalid_update = []  # Initialize here
    if updated:
        invalid_update = [key for key in updated if key not in valid_columns]
        logger.debug(f"Invalid update for {cls.__name__}: {invalid_update}")
    if invalid_keys or (updated and invalid_update):
        raise ValueError(
            f"Invalid keys for {cls.__name__} in validation: {invalid_keys}"
            if not updated
            else f"Invalid keys for {cls.__name__} in validation: (keys, update) {invalid_keys, invalid_update}"
        )


def get_referenced_records(record, visited=None):
    """
    Follow all permission_references chains to find the records that hold the actual permissions.
    (Still needed for hybrid permission reference checks if full SQL is too complex, and for create checks)

    Args:
        record: The record to start from
        visited: Set of already visited records to prevent infinite recursion

    Returns:
        list: All records that hold actual permissions (with user_id and team_id)
    """
    if visited is None:
        visited = set()
        result = [record]  # Always include the starting record in the results
    else:
        result = [record]  # Include this record in all recursive calls as well

    # Create a unique identifier for the record to prevent infinite recursion
    # Using class name and id for the identifier
    record_class_name = type(record).__name__
    record_id = getattr(record, "id", None)
    record_identifier = (record_class_name, record_id)

    # If we've already visited this record, we have a circular reference
    if record_identifier in visited:
        raise ValueError(
            f"Circular permission reference detected for {record_identifier}"
        )

    # Add this record to the visited set - must be before recursion to detect cycles
    visited.add(record_identifier)

    # Check for both permission_references (plural) and permission_reference (singular)
    has_plural_refs = (
        hasattr(record, "permission_references") and record.permission_references
    )
    has_singular_ref = (
        hasattr(record, "permission_reference") and record.permission_reference
    )

    # If record doesn't have any permission references, this is a leaf record
    if not has_plural_refs and not has_singular_ref:
        return result

    referenced_records = []

    # Follow each reference in permission_references (plural)
    if has_plural_refs:
        for ref_name in record.permission_references:
            # Get the reference attribute (relationship)
            ref_attr = getattr(record, ref_name, None)

            # Only follow this reference if it's populated
            if ref_attr is not None:
                try:
                    # Create a new visited set that includes all previously visited records
                    # This ensures circular references are detected across different branches
                    new_visited = visited.copy()
                    referenced_records.extend(
                        get_referenced_records(ref_attr, new_visited)
                    )
                except ValueError as e:
                    # Re-raise the error to propagate circular reference detection upward
                    raise ValueError(
                        f"Circular reference detected via {ref_name}: {str(e)}"
                    )

    # Follow permission_reference (singular) for backward compatibility
    elif has_singular_ref:
        ref_name = record.permission_reference
        ref_attr = getattr(record, ref_name, None)

        if ref_attr is not None:
            try:
                # Create a new visited set that includes all previously visited records
                new_visited = visited.copy()
                referenced_records.extend(get_referenced_records(ref_attr, new_visited))
            except ValueError as e:
                # Re-raise the error to propagate circular reference detection upward
                raise ValueError(
                    f"Circular reference detected via {ref_name}: {str(e)}"
                )

    # Combine results - note that we now include all records regardless of whether they're
    # at the start of the chain or in the middle
    result.extend(referenced_records)

    # Return all collected records
    return result


def find_create_permission_reference_chain(cls, db, visited=None):
    """
    Follow create_permission_reference chain to find the class that determines create permissions.
    (Still needed for create checks)

    Args:
        cls: The model class to start from
        db: Database session
        visited: Set of already visited classes to prevent infinite recursion

    Returns:
        tuple: (final_class, ref_attr_name) tuple with the class that determines permissions
               and the attribute name that references it
    """
    if visited is None:
        visited = set()

    # Prevent infinite recursion
    if cls in visited:
        raise ValueError(
            f"Circular create_permission_reference detected for {cls.__name__}"
        )

    visited.add(cls)

    # Determine the create_permission_reference to follow
    create_perm_ref = None

    # First check for explicit create_permission_reference
    if hasattr(cls, "create_permission_reference") and cls.create_permission_reference:
        create_perm_ref = cls.create_permission_reference
    # If not defined but has exactly one permission_reference, use that
    elif hasattr(cls, "permission_references") and len(cls.permission_references) == 1:
        create_perm_ref = cls.permission_references[0]
    # If multiple references and no create_permission_reference, raise error
    elif hasattr(cls, "permission_references") and len(cls.permission_references) > 1:
        # Log warning but continue to avoid breaking existing code
        logger.warning(
            f"Multiple permission references in {cls.__name__} but no create_permission_reference defined: {cls.permission_references}"
        )
        # We'll return this class as the final class since we can't determine which reference to follow
        return (cls, None)

    # If no create_permission_reference to follow, this class is the final one
    if not create_perm_ref:
        return (cls, None)

    # If the class has a create_permission_reference, follow it
    ref_name = create_perm_ref

    ref_model = referenced_class(cls, ref_name)
    if ref_model is not None:
        # Recursively follow the chain, creating a new copy of the visited set
        # This ensures proper detection of circular references across different branches
        new_visited = visited.copy()
        return find_create_permission_reference_chain(ref_model, db, new_visited)
    else:
        raise ValueError(
            f"Invalid relationship attribute '{ref_name}' in {cls.__name__}"
        )


def check_permission_table_access(user_id, cls, db, operation=None, **kwargs):
    """
    Special permission check for the Permission table.
    Users need SHARE permission or admin access to the resource they're trying to manage permissions for.

    Args:
        user_id: The ID of the user to check
        cls: The model class being checked (should be Permission)
        db: Database session
        operation: The operation being performed (create, update, delete)
        **kwargs: Permission properties, including resource_type and resource_id

    Returns:
        tuple: (True/False, error_message) indicating if access is granted and why not if denied
    """
    # If not dealing with a Permission record, don't do special checks
    if cls.__name__ != "Permission":
        return (True, None)

    # For Permission entities, ensure user can manage permissions on the target resource
    resource_type = kwargs.get("resource_type")
    resource_id = kwargs.get("resource_id")

    if not resource_type or not resource_id:
        return (False, "Missing resource_type or resource_id for permission assignment")

    # Special handling for system users
    if is_root_id(user_id):
        return (True, None)  # ROOT_ID can manage all permissions

    if is_system_id(user_id):
        return (True, None)  # SYSTEM_ID can also manage all permissions

    # Check if the user can manage permissions for this resource
    return can_manage_permissions(user_id, resource_type, resource_id, db, operation)


def check_access_to_all_referenced_entities(
    user_id, cls, db, minimum_role=None, **kwargs
):
    """
    Check if the user has access to all referenced entities specified by foreign keys.
    (Still needed for create checks, uses optimized permission checks internally)

    Args:
        user_id: The ID of the user to check
        cls: The model class
        db: Database session
        minimum_role: Minimum role name required
        **kwargs: Foreign key values to check

    Returns:
        tuple: (True/False, missing_entity_info) tuple indicating if access is granted
               and which entity is missing if access is denied
    """
    # Special handling for Permission table
    if cls.__name__ == "Permission":  # Use name check
        can_access, error_msg = check_permission_table_access(
            user_id, cls, db, **kwargs
        )
        if not can_access:
            return (
                False,
                ("Permission", "resource", kwargs.get("resource_id"), error_msg),
            )

    # Get all the foreign key relationships
    # Check if permission_references attribute exists and is not empty/None
    permission_refs = getattr(cls, "permission_references", None)
    if not permission_refs:
        return (True, None)

    # Check each reference from permission_references
    for ref_name in permission_refs:
        ref_id_field = f"{ref_name}_id"

        # SECURITY FIX: Missing reference IDs are treated as permission denials
        # This prevents skipping permission checks for entities referenced through foreign keys
        if ref_id_field not in kwargs or kwargs[ref_id_field] is None:
            logger.warning(
                f"Missing required reference '{ref_id_field}' for {cls.__name__}, denying access"
            )
            return (
                False,
                (cls.__name__, ref_id_field, None, "missing_required_reference"),
            )

        ref_model = referenced_class(cls, ref_name)
        if ref_model is None:
            # A declared reference that leads nowhere grants nothing.
            logger.warning(
                f"Invalid permission reference attribute '{ref_name}' in class '{cls.__name__}'"
            )
            return (False, (cls.__name__, ref_id_field, None, "invalid_reference"))

        # Get the referenced record
        ref_id = kwargs[ref_id_field]

        # Check if user has access to this record using the optimized check.
        # Defaulting to VIEW access unless minimum_role is specified. The role
        # goes to the ``minimum_role`` param, not the ``declarative_base`` slot.
        access_result, error_msg = check_permission(
            user_id,
            ref_model,
            ref_id,
            db,
            declarative_base=declarative_base_of(ref_model),
            minimum_role=minimum_role,
        )

        if access_result == PermissionResult.NOT_FOUND:
            return (False, (ref_model.__name__, ref_id_field, ref_id, "not_found"))
        elif access_result != PermissionResult.GRANTED:
            return (False, (ref_model.__name__, ref_id_field, ref_id, "no_access"))

    # If all checks pass, user has access to all referenced entities
    return (True, None)


# Cache of ``{__tablename__: SQLAlchemy model}`` maps keyed by the exact tuple of
# BLL module paths scanned. That tuple is a pure function of the loaded extension
# set, so a changed extension set yields a new key and rebuilds the map. Building
# the map is an O(modules x members) reflective import + ``inspect.getmembers``
# scan; memoizing turns the per-call scan into a dict lookup.
_resource_model_map_cache: dict[tuple[str, ...], dict[str, Any]] = {}


def _resource_type_model_map(bll_modules: tuple[str, ...]) -> dict[str, Any]:
    """Build (and cache) a ``{__tablename__: SQLAlchemy model}`` map for the given
    BLL modules.

    Reproduces the historical first-match scan exactly: modules are scanned in
    order and members in ``inspect.getmembers`` order, so the first model that
    claims a given table name wins (``setdefault``). Errors reading a candidate's
    ``.DB`` are swallowed and unimportable modules are skipped, matching the
    previous inline behavior.
    """
    cached = _resource_model_map_cache.get(bll_modules)
    if cached is not None:
        return cached

    import importlib

    from sqlalchemy.ext.declarative import DeclarativeMeta

    model_map: dict[str, Any] = {}
    for module_name in bll_modules:
        try:
            module = importlib.import_module(module_name)
        except (ImportError, ModuleNotFoundError) as e:
            logger.debug(
                f"Could not import BLL module {module_name} for permission check: {e}"
            )
            continue
        for name, obj in inspect.getmembers(module):
            # Check if it's a BLL model class with DatabaseMixin
            if (
                hasattr(obj, "__bases__")
                and any("DatabaseMixin" in str(base) for base in obj.__bases__)
                and hasattr(obj, "DB")
            ):
                try:
                    # Get the SQLAlchemy model from the .DB property
                    db_model = obj.DB
                    if isinstance(db_model, DeclarativeMeta) and hasattr(
                        db_model, "__tablename__"
                    ):
                        model_map.setdefault(db_model.__tablename__, db_model)
                except Exception as e:
                    logger.debug(f"Error accessing .DB property of {name}: {e}")
                    continue

    _resource_model_map_cache[bll_modules] = model_map
    return model_map


def can_manage_permissions(
    user_id, resource_type, resource_id, db, operation_type=None
):
    """
    Check if a user can manage permissions for a resource.
    Users need SHARE permission or admin access to manage permissions.

    Args:
        user_id: The ID of the user to check
        resource_type: The table name (string) of the resource type to check
        resource_id: The ID of the resource to check
        db: Database session
        operation_type: Type of operation (create, edit, delete)

    Returns:
        tuple: (bool, error_message) indicating if user can manage permissions and why not if they can't
    """
    # Special handling for system users
    if is_root_id(user_id):
        return (True, None)  # ROOT_ID can manage all permissions

    if is_system_id(user_id):
        return (True, None)  # SYSTEM_ID can also manage all permissions

    # Validate resource_type to prevent injection
    if not isinstance(resource_type, str) or not resource_type.isalnum():
        # Allow underscores in table names but nothing else
        if isinstance(resource_type, str) and any(
            c != "_" for c in resource_type if not c.isalnum()
        ):
            return (False, f"Invalid resource type: {resource_type}")
        if not isinstance(resource_type, str):
            return (False, "Resource type must be a string")

    # Find the model class for this resource type. The set of BLL modules to scan
    # is a pure function of the loaded extension set (core modules plus one per
    # APP_EXTENSIONS entry), so the resolved table->model map is memoized on it.
    bll_modules = [
        "zephyrex.logic.BLL_Auth",
        "zephyrex.logic.BLL_Providers",
        "zephyrex.logic.BLL_Extensions",
    ]

    # Also check extension BLL modules if APP_EXTENSIONS is set
    app_extensions_str = env("APP_EXTENSIONS")
    if app_extensions_str:
        from zephyrex.app import parse_extension_csv

        extension_names = parse_extension_csv(app_extensions_str)
        for ext_name in extension_names:
            bll_modules.append(
                f"zephyrex.extensions.{ext_name}.BLL_{stringcase.pascalcase(ext_name)}"
            )

    model_class = _resource_type_model_map(tuple(bll_modules)).get(resource_type)

    if not model_class:
        return (False, f"Could not find model class for resource type: {resource_type}")

    # System tables can only be managed by system users (already checked above)
    if hasattr(model_class, "system") and getattr(model_class, "system", False):
        return (False, f"Non-system users cannot manage permissions for system tables")

    # Check if the record exists and isn't deleted
    record = db.query(model_class).filter(model_class.id == resource_id).first()
    if not record:
        return (False, f"Resource {resource_type} with ID {resource_id} not found")

    # Check for deleted records
    if hasattr(record, "deleted_at") and record.deleted_at is not None:
        return (False, f"Cannot manage permissions for deleted records")

    # Check for explicit SHARE permission using check_permission. The
    # PermissionType goes to ``required_level``, not the ``declarative_base`` slot.
    base = declarative_base_of(model_class)
    result, _ = check_permission(
        user_id,
        model_class,
        resource_id,
        db,
        declarative_base=base,
        required_level=PermissionType.SHARE,
    )
    if result == PermissionResult.GRANTED:
        return (True, None)

    # Check if the user has EDIT permission to the resource
    if user_can_edit(user_id, model_class, resource_id, db, declarative_base=base):
        # For deletion, check if they have DELETE permission as well
        if (
            operation_type == "delete"
            and not check_permission(
                user_id,
                model_class,
                resource_id,
                db,
                declarative_base=base,
                required_level=PermissionType.DELETE,
            )[0]
            == PermissionResult.GRANTED
        ):
            return (
                False,
                f"User {user_id} does not have DELETE permission for {resource_type} {resource_id}",
            )
        return (True, None)

    return (
        False,
        f"User {user_id} does not have permission to manage permissions for {resource_type} {resource_id}",
    )


def user_can_create_referenced_entity(cls, user_id, db, minimum_role=None, **kwargs):
    """
    Check if user can create an entity based on create_permission_reference.
    (Still needed for create checks, uses optimized permission checks internally)

    Args:
        cls: The model class
        user_id: The ID of the user requesting to create
        db: Database session
        minimum_role: Minimum role required
        **kwargs: Foreign key values and other parameters

    Returns:
        tuple: (True/False, error_message) indicating if the user can create and why not if they can't
    """
    # Special handling for system users
    if is_root_id(user_id):
        return (True, None)  # ROOT_ID can create anything

    # If no create_permission_reference, default to standard permission check
    create_perm_ref = None
    if hasattr(cls, "create_permission_reference") and cls.create_permission_reference:
        create_perm_ref = cls.create_permission_reference
    elif hasattr(cls, "permission_references") and cls.permission_references:
        # Auto-determine the create_permission_reference if not explicitly defined
        if len(cls.permission_references) == 1:
            # If only one reference, use it automatically
            create_perm_ref = cls.permission_references[0]
        elif len(cls.permission_references) > 1:
            # If multiple references but no create_permission_reference, raise error
            return (
                False,
                f"Multiple permission references found in {cls.__name__} but no create_permission_reference defined: {cls.permission_references}",
            )

    if not create_perm_ref:
        # No create_permission_reference and no permission_references
        return (True, None)

    # Special handling for Permission table
    if cls.__name__ == "Permission":  # Use name check
        resource_type = kwargs.get("resource_type")
        resource_id = kwargs.get("resource_id")

        if not resource_type or not resource_id:
            return (False, "Missing resource_type or resource_id for permission")

        # Check if the user can manage permissions for this resource
        can_manage_result, error_msg = can_manage_permissions(
            user_id, resource_type, resource_id, db
        )
        return (can_manage_result, error_msg)

    try:
        # Find the class that determines create permissions
        target_cls, _ = find_create_permission_reference_chain(cls, db)

        # If the target class is this class, no need for special checks
        if target_cls == cls:
            return (True, None)

        # Check if the user has sufficient permissions on the referenced entity
        ref_name = create_perm_ref
        ref_id_field = f"{ref_name}_id"

        # If the reference ID isn't provided, we can't check permissions
        if ref_id_field not in kwargs or kwargs[ref_id_field] is None:
            return (False, f"Missing required reference: {ref_id_field}")

        ref_id = kwargs[ref_id_field]

        # Get the referenced entity model
        ref_model = referenced_class(cls, ref_name)
        if ref_model is None:
            return (False, f"Invalid reference attribute: {ref_name}")

        # Check if the user has admin access to the referenced entity
        # Admin access (EDIT permission) is required to create entities that reference this entity
        if not user_can_edit(
            user_id,
            ref_model,
            ref_id,
            db,
            declarative_base=declarative_base_of(ref_model),
        ):
            return (
                False,
                f"User {user_id} does not have admin access to {ref_model.__name__} {ref_id}",
            )

        return (True, None)

    except Exception as e:
        return (False, f"Error checking create permissions: {str(e)}")


class PermissionType(PyEnum):  # Inherit from Python Enum
    """Enum representing the type of permission."""

    VIEW = "can_view"
    EXECUTE = "can_execute"
    COPY = "can_copy"
    EDIT = "can_edit"
    DELETE = "can_delete"
    SHARE = "can_share"


# The levels at which a server-written row stays the server's
# (server_row_modifiable_by), as the write paths hold it.
SERVER_ROW_MODIFYING_LEVELS = frozenset({PermissionType.EDIT, PermissionType.DELETE})


class PermissionResult(PyEnum):  # Inherit from Python Enum
    """Enum representing the result of a permission check."""

    GRANTED = "granted"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    ERROR = "error"


def check_permission(
    user_id,
    record_cls,
    record_id,
    db,
    declarative_base,
    required_level=None,
    minimum_role=None,
    db_manager=None,
):
    """
    Check if a user has permission to access a record using DB-level filtering logic.
    Determines the required PermissionType based on minimum_role or uses the provided required_level.

    Args:
        user_id: The ID of the user requesting access
        record_cls: The model class
        record_id: The ID of the record to check
        db: Database session
        declarative_base: The declarative base to use for accessing SQLAlchemy models
        required_level: Specific PermissionType required (takes precedence over minimum_role)
        minimum_role: Minimum role required (e.g., 'user', 'admin', 'superadmin')

    Returns:
        tuple: (PermissionResult, error_message) indicating the result and any error message
    """
    try:
        # Validate inputs to prevent null dereference
        if user_id is None:
            return (
                PermissionResult.ERROR,
                "User ID cannot be null",
            )

        if record_cls is None:
            return (
                PermissionResult.ERROR,
                "Record class cannot be null",
            )

        if record_id is None:
            return (
                PermissionResult.ERROR,
                "Record ID cannot be null",
            )

        if db is None:
            return (
                PermissionResult.ERROR,
                "Database session cannot be null",
            )

        # Root user has access to everything
        if is_root_id(user_id):
            return (PermissionResult.GRANTED, None)

        # Determine required permission level from minimum_role if not explicitly provided
        if required_level is None:
            if minimum_role == "superadmin":
                required_level = PermissionType.SHARE
            elif minimum_role == "admin":
                required_level = PermissionType.EDIT
            else:  # Default to VIEW for None, 'user', or anything else
                required_level = PermissionType.VIEW

        # Get the SQLAlchemy model for the record class. Use the shared resolver
        # so an already-mapped ORM class is returned as-is and ``.DB(base)`` is
        # only invoked for Pydantic (DatabaseMixin) inputs. Calling
        # ``record_cls.DB(declarative_base)`` unconditionally raised when
        # ``record_cls`` was already an ORM/DeclarativeMeta model (issue #229).
        record_db_cls = _resolve_db_class(record_cls, declarative_base)

        # Fetch the record. A single round-trip is sufficient: a ``None`` result
        # means the record does not exist (NOT_FOUND), and the row is needed
        # immediately below for the deleted/system/ownership checks. (A prior
        # ``exists()`` probe here duplicated this lookup with no added signal.)
        record = db.query(record_db_cls).filter(record_db_cls.id == record_id).first()
        if not record:
            return (
                PermissionResult.NOT_FOUND,
                gen_not_found_msg(record_cls.__name__),
            )

        # Check if the record is deleted - only ROOT_ID can see deleted records
        if hasattr(record, "deleted_at") and record.deleted_at is not None:
            if not is_root_id(user_id):
                return (
                    PermissionResult.DENIED,
                    f"User {user_id} cannot access deleted record {record_cls.__name__} {record_id}",
                )

        # Check system flag - only ROOT_ID and SYSTEM_ID can access system tables
        if hasattr(record_cls, "system") and getattr(record_cls, "system", False):
            # For VIEW operations, allow all users to access system entities
            if required_level == PermissionType.VIEW:
                return (PermissionResult.GRANTED, None)
            # For all other operations, only allow system users
            if not (is_root_id(user_id) or is_system_id(user_id)):
                return (
                    PermissionResult.DENIED,
                    f"User {user_id} cannot modify system table {record_cls.__name__}",
                )

        # A record that inherits its access answers only to its parents and
        # to Permission rows (both in the filter below). Who wrote it grants
        # nothing; it only keeps a row the server wrote the server's to change.
        if inherits_access(record_db_cls):
            if required_level in SERVER_ROW_MODIFYING_LEVELS and not (
                server_row_modifiable_by(
                    user_id, getattr(record, "created_by_user_id", None)
                )
            ):
                return (
                    PermissionResult.DENIED,
                    f"User {user_id} cannot modify {record_cls.__name__} {record_id}, which the server wrote",
                )
        # Check if the user is the creator of the record
        elif (
            hasattr(record, "created_by_user_id")
            and record.created_by_user_id == user_id
        ):
            return (PermissionResult.GRANTED, None)

        # Check for records created by ROOT_ID - only ROOT_ID can access them
        elif hasattr(record, "created_by_user_id") and record.created_by_user_id == env(
            "ROOT_ID"
        ):
            if not is_root_id(user_id):
                return (
                    PermissionResult.DENIED,
                    f"User {user_id} cannot access records created by ROOT_ID",
                )
            return (PermissionResult.GRANTED, None)

        # A user record's visibility is the users rule in
        # generate_permission_filter, whoever created it. Each account is
        # stamped as its own creator, so the grants below made the SYSTEM
        # and template accounts readable to anyone, as they would any user
        # row recorded as SYSTEM's; the filter excludes users from them too.
        creator_grants_apply = record_db_cls.__tablename__ != USERS_TABLE and not (
            inherits_access(record_db_cls)
        )

        # Check for records created by SYSTEM_ID - all users can view, only ROOT_ID and SYSTEM_ID can modify
        if (
            creator_grants_apply
            and hasattr(record, "created_by_user_id")
            and record.created_by_user_id == env("SYSTEM_ID")
        ):
            # For view operations, allow access
            if required_level == PermissionType.VIEW:
                return (PermissionResult.GRANTED, None)
            # For other operations, only ROOT_ID and SYSTEM_ID
            if not (is_root_id(user_id) or is_system_id(user_id)):
                return (
                    PermissionResult.DENIED,
                    f"User {user_id} cannot modify records created by SYSTEM_ID",
                )
            return (PermissionResult.GRANTED, None)

        # Check for records created by TEMPLATE_ID
        if (
            creator_grants_apply
            and hasattr(record, "created_by_user_id")
            and record.created_by_user_id == env("TEMPLATE_ID")
        ):
            # For view/copy/execute/share operations, all users can access
            if required_level in [
                PermissionType.VIEW,
                PermissionType.COPY,
                PermissionType.EXECUTE,
                PermissionType.SHARE,
            ]:
                return (PermissionResult.GRANTED, None)
            # For edit/delete, only ROOT_ID and SYSTEM_ID can modify
            if not (is_root_id(user_id) or is_system_id(user_id)):
                return (
                    PermissionResult.DENIED,
                    f"User {user_id} cannot modify records created by TEMPLATE_ID",
                )
            return (PermissionResult.GRANTED, None)

        # Now check for direct permissions in the Permission table.
        # The permission row is owned by the ``acl_rbac`` extension; without
        # it loaded, only the creator-only / team-membership paths above
        # grant access. Skipping this block is a fail-closed default:
        # absent the extension, a user without team admin rights never
        # sees a record they did not create.
        permission_db_class_hook = _acl_hooks["permission_db_class"]
        if permission_db_class_hook is not None:
            permission_db_cls = permission_db_class_hook(declarative_base)
            direct_permission = (
                db.query(permission_db_cls)
                .filter(
                    and_(
                        permission_db_cls.resource_type == record_db_cls.__tablename__,
                        permission_db_cls.resource_id == record_id,
                        permission_db_cls.user_id == user_id,
                        _active(permission_db_cls),
                        getattr(permission_db_cls, required_level.value) == True,
                    )
                )
                .first()
            )
            if direct_permission is not None:
                return (PermissionResult.GRANTED, None)

        # If no direct permission, generate the permission filter and check
        permission_filter = generate_permission_filter(
            user_id, record_cls, db, declarative_base, required_level
        )

        # Combine with the specific record ID
        final_filter = and_(record_db_cls.id == record_id, permission_filter)

        # Check if a record exists matching the combined filter
        has_access = db.query(exists().where(final_filter)).scalar()

        if has_access:
            return (PermissionResult.GRANTED, None)
        else:
            return (
                PermissionResult.DENIED,
                f"User {user_id} does not have {minimum_role or required_level.name.lower()} access to {record_cls.__name__} {record_id}",
            )

    except Exception as e:
        logger.error(
            f"Error checking permission for {record_cls.__name__} {record_id}: {str(e)}"
        )
        return (PermissionResult.ERROR, str(e))


def _get_admin_accessible_team_ids_cte(
    user_id: str,
    db: Optional[Session],
    declarative_base,
    max_depth: int = 5,
    unique_suffix: str = "",
    memberships_only: bool = False,
) -> CTE:
    """
    Generates a recursive CTE to find all team IDs accessible by a user,
    including teams they are directly a member of and parent teams.

    Args:
        user_id: The ID of the user
        db: Database session (the CTE is built without one; may be None)
        declarative_base: The declarative base to use for accessing SQLAlchemy models
        max_depth: Maximum depth for recursion (default: 5)
        unique_suffix: Optional suffix to make CTE name unique (default: "")
        memberships_only: Only the user's live memberships count; a pending
            invitation is not a membership.

    A deleted team grants nothing either way: no membership in or invitation
    to one counts, and the walk to parents stops at a deleted one, so its
    members lose its records with it.

    Returns:
        CTE: Common table expression with accessible team IDs
    """
    # Local import to break cycle
    from zephyrex.database.DatabaseManager import DatabaseManager
    from zephyrex.logic.BLL_Auth import (
        RoleModel,
        TeamModel,
        UserModel,
        UserTeamModel,
    )

    # Get SQLAlchemy models using the declarative base
    UserModel.DB(declarative_base)
    RoleModel.DB(declarative_base)
    team_db_cls = TeamModel.DB(declarative_base)
    user_team_db_cls = UserTeamModel.DB(declarative_base)

    # Create a unique CTE name using the suffix if provided
    cte_name = f"admin_accessible_teams_cte{unique_suffix}"

    # Aliased, so the subquery never correlates to a teams table the
    # enclosing query reads.
    live_team = aliased(team_db_cls, name=f"{cte_name}_live")
    live_team_ids = select(live_team.id).where(live_team.deleted_at.is_(None))
    membership_select = (
        select(
            user_team_db_cls.team_id.label("id"),
            user_team_db_cls.role_id.label("role_id"),
            func.cast(1, Integer).label("depth"),  # type: ignore[arg-type]
        )
        .where(user_team_db_cls.user_id == user_id)
        .where(user_team_db_cls.enabled == True)
        .where(_active(user_team_db_cls))
        .where(user_team_db_cls.team_id.in_(live_team_ids))
    )
    base_selects = [membership_select]

    # Invitations that target the user directly should also expose the team
    # hierarchy. The invitation entity is owned by the ``auth_invitations``
    # extension; without it, the only path into a team is via UserTeam
    # rows, which the ``base_selects`` above already cover.
    invitation_db_class_hook = _invitation_hooks["invitation_db_class"]
    invitee_db_class_hook = _invitation_hooks["invitee_db_class"]
    if (
        not memberships_only
        and invitation_db_class_hook is not None
        and invitee_db_class_hook is not None
    ):
        invitation_db_cls = invitation_db_class_hook(declarative_base)
        invitee_db_cls = invitee_db_class_hook(declarative_base)

        invitation_filters = [
            invitation_db_cls.team_id.isnot(None),
            invitation_db_cls.team_id.in_(live_team_ids),
        ]
        if hasattr(invitation_db_cls, "deleted_at"):
            invitation_filters.append(invitation_db_cls.deleted_at.is_(None))
        if hasattr(invitation_db_cls, "expires_at"):
            invitation_filters.append(_active(invitation_db_cls))

        direct_invitation_query = select(
            invitation_db_cls.team_id.label("id"),
            invitation_db_cls.role_id.label("role_id"),
            func.cast(1, Integer).label("depth"),  # type: ignore[arg-type]
        ).where(invitation_db_cls.user_id == user_id)
        for condition in invitation_filters:
            direct_invitation_query = direct_invitation_query.where(condition)
        base_selects.append(direct_invitation_query)

        invitee_invitation_query = (
            select(
                invitation_db_cls.team_id.label("id"),
                invitation_db_cls.role_id.label("role_id"),
                func.cast(1, Integer).label("depth"),  # type: ignore[arg-type]
            )
            .select_from(invitation_db_cls)
            .join(invitee_db_cls, invitee_db_cls.invitation_id == invitation_db_cls.id)
            .where(invitee_db_cls.user_id == user_id)
        )
        for condition in invitation_filters:
            invitee_invitation_query = invitee_invitation_query.where(condition)
        if hasattr(invitee_db_cls, "deleted_at"):
            invitee_invitation_query = invitee_invitation_query.where(
                invitee_db_cls.deleted_at.is_(None)
            )
        invitee_invitation_query = invitee_invitation_query.where(
            invitee_db_cls.declined_at.is_(None)
        )
        base_selects.append(invitee_invitation_query)

    if len(base_selects) == 1:
        combined_base = base_selects[0]
    else:
        base_union = union_all(*base_selects).subquery()
        combined_base = select(
            base_union.c.id, base_union.c.role_id, base_union.c.depth
        )

    recursive_cte = combined_base.cte(cte_name, recursive=True)

    # Named after the CTE, so filters nested through permission references
    # (each with its own CTE) do not collide.
    cte_alias = aliased(recursive_cte, name=f"{cte_name}_alias")
    team_alias = aliased(team_db_cls, name=f"{cte_name}_team")

    recursive_term = (
        select(
            team_alias.parent_id.label("id"),
            cte_alias.c.role_id,
            cte_alias.c.depth + 1,
        )
        .select_from(team_alias)
        .join(cte_alias, team_alias.id == cte_alias.c.id)
        .where(team_alias.parent_id.isnot(None))
        .where(cte_alias.c.depth < max_depth)
    )
    parent_alias = aliased(team_db_cls, name=f"{cte_name}_parent")
    recursive_term = recursive_term.where(
        team_alias.parent_id.in_(
            select(parent_alias.id).where(parent_alias.deleted_at.is_(None))
        )
    )

    recursive_cte = recursive_cte.union(recursive_term)

    return recursive_cte


def _live_sub_team_ids_cte(
    user_id: str, declarative_base: Any, unique_suffix: str
) -> CTE:
    """A recursive CTE of the teams at or below the user's live memberships:
    each team the user holds an enabled, unexpired, undeleted membership in
    (when that team is not deleted), and every sub-team beneath one at any
    depth. A deleted team ends the walk down through it, so its sub-teams
    are reached only through another live path.

    Only the ``id`` column is carried and the recursion is a ``UNION``, so a
    team already reached is not revisited: the walk ends on any hierarchy,
    a cyclic one included, without a depth bound."""
    from zephyrex.logic.BLL_Auth import TeamModel, UserTeamModel

    team_db_cls = TeamModel.DB(declarative_base)
    user_team_db_cls = UserTeamModel.DB(declarative_base)
    cte_name = f"live_sub_teams_cte{unique_suffix}"

    live_team = aliased(team_db_cls, name=f"{cte_name}_live")
    memberships = (
        select(user_team_db_cls.team_id.label("id"))
        .where(user_team_db_cls.user_id == user_id)
        .where(user_team_db_cls.enabled == True)
        .where(_active(user_team_db_cls))
        .where(
            user_team_db_cls.team_id.in_(
                select(live_team.id).where(live_team.deleted_at.is_(None))
            )
        )
    )
    sub_teams = memberships.cte(cte_name, recursive=True)

    reached = aliased(sub_teams, name=f"{cte_name}_alias")
    child = aliased(team_db_cls, name=f"{cte_name}_child")
    return sub_teams.union(
        select(child.id.label("id"))
        .select_from(child)
        .join(reached, child.parent_id == reached.c.id)
        .where(child.deleted_at.is_(None))
    )


def _in_live_team_hierarchy(
    team_id_column: Any, user_id: str, declarative_base: Any, unique_suffix: str
) -> Any:
    """Whether ``team_id_column`` names a team the user reaches through their
    live memberships, in either direction: a team they belong to, its live
    parents (up to the depth team-scoped records reach), or its live
    sub-teams at any depth. Siblings and cousins are not reached: the walk
    goes up from a membership or down from one, never up and then down.

    This is the reach the users rule gives a requester, kept in one place so
    every read of team members (the users rule, a team's member list) names
    exactly the people that rule shows."""
    parents = _get_admin_accessible_team_ids_cte(
        user_id,
        None,
        declarative_base,
        max_depth=5,
        unique_suffix=f"{unique_suffix}_up",
        memberships_only=True,
    )
    sub_teams = _live_sub_team_ids_cte(
        user_id, declarative_base, unique_suffix=f"{unique_suffix}_down"
    )
    return or_(
        team_id_column.in_(select(parents.c.id)),
        team_id_column.in_(select(sub_teams.c.id)),
    )


def admin_role_ids(declarative_base: Any, unique_suffix: str = "") -> CTE:
    """A recursive CTE of the roles that administer a team: the admin role
    (``ADMIN_ROLE_ID``) and every role that extends it, by ``parent_id``
    ancestry by id (superadmin among them). This is the one admin rule:
    the team-record filter and ``TeamAuthority`` both ask it.

    A role's name and its depth in the tree rank nothing. Names are not
    unique, and every team's admins create roles of their own, so ranking by
    either let a team's ``mod`` (extending ``user``, at the admin's depth)
    administer the team, and a role named ``user`` one level down lift every
    plain member of every team.

    A deleted or expired role extends nothing, and ends the walk down
    through it. The recursion is a ``UNION`` over ids alone, so a cyclic
    tree (which the role manager refuses, but a direct write could make)
    ends the walk too."""
    from zephyrex.logic.BLL_Auth import RoleModel

    role_db_cls = RoleModel.DB(declarative_base)
    cte_name = f"admin_roles_cte{unique_suffix}"
    admin = aliased(role_db_cls, name=f"{cte_name}_admin")
    admin_roles = (
        select(admin.id.label("id"))
        .where(admin.id == env("ADMIN_ROLE_ID"))
        .where(_active(admin))
        .cte(cte_name, recursive=True)
    )
    reached = aliased(admin_roles, name=f"{cte_name}_alias")
    child = aliased(role_db_cls, name=f"{cte_name}_child")
    return admin_roles.union(
        select(child.id.label("id"))
        .select_from(child)
        .join(reached, child.parent_id == reached.c.id)
        .where(_active(child))
    )


def role_extends_admin(db: Session, declarative_base: Any, role_id: str) -> bool:
    """Whether ``role_id`` is the admin role or extends it (``admin_role_ids``)."""
    admin_roles = admin_role_ids(declarative_base)
    found = db.execute(
        select(admin_roles.c.id).where(admin_roles.c.id == role_id).limit(1)
    )
    return found.first() is not None


def _resolve_db_class(resource_cls: Type[Any], declarative_base) -> Type[Any]:
    """Resolve a resource class to its SQLAlchemy model class.

    Handles three cases:
    - Already a SQLAlchemy model (has ``__tablename__``): returned as-is.
    - Pydantic model with ``DatabaseMixin`` (has ``.DB()``): resolved via
      ``resource_cls.DB(declarative_base)``.
    - Fallback: returned as-is (assumed SQLAlchemy).
    """
    if hasattr(resource_cls, "__tablename__"):
        return resource_cls
    elif hasattr(resource_cls, "DB"):
        return resource_cls.DB(declarative_base)  # type: ignore[no-any-return]
    else:
        return resource_cls


def _build_direct_permission_filter(
    user_id: str,
    resource_cls: Type[Any],
    accessible_team_ids_cte: CTE,
    db: Session,
    required_permission_level: "PermissionType",
    declarative_base,
):
    """
    Generates a SQLAlchemy filter expression to check for direct permissions
    assigned via the Permission table (user, team, or role-based).

    Args:
        user_id: The ID of the user requesting access
        resource_cls: The model class being accessed
        accessible_team_ids_cte: CTE with accessible team IDs (from _get_admin_accessible_team_ids_cte)
        db: Database session
        required_permission_level: Required permission type
        declarative_base: The declarative base to use for accessing SQLAlchemy models

    Returns:
        SQLAlchemy expression for direct permission checks
    """
    # Local imports to break cycle
    from zephyrex.database.DatabaseManager import DatabaseManager
    from zephyrex.logic.BLL_Auth import UserTeamModel

    # The Permission row is owned by the ``acl_rbac`` extension. Without
    # it loaded, no direct/team/role permission rows exist — every branch
    # below evaluates to ``False`` against an empty exists()-style
    # ``SELECT 1 WHERE FALSE``, so the caller's overall permission
    # filter is fail-closed by construction.
    permission_db_class_hook = _acl_hooks["permission_db_class"]
    if permission_db_class_hook is None:
        from sqlalchemy import literal

        return literal(False)
    permission_db_cls = permission_db_class_hook(declarative_base)
    user_team_db_cls = UserTeamModel.DB(declarative_base)

    resource_db_cls = _resolve_db_class(resource_cls, declarative_base)

    permission_field = getattr(permission_db_cls, required_permission_level.value)

    # 1. Direct User Permission
    user_perm_exists = exists().where(
        and_(
            permission_db_cls.resource_type == resource_db_cls.__tablename__,
            permission_db_cls.resource_id == resource_db_cls.id,
            permission_db_cls.user_id == user_id,
            permission_field == True,
            _active(permission_db_cls),  # Check expiration
        )
    )

    # 2. Team Permission (User must be on an accessible team that has the permission)
    # Join Permission with accessible_team_ids_cte
    team_perm_exists = exists().where(
        and_(
            permission_db_cls.resource_type == resource_db_cls.__tablename__,
            permission_db_cls.resource_id == resource_db_cls.id,
            permission_db_cls.team_id.in_(
                select(accessible_team_ids_cte.c.id)
            ),  # Check against accessible teams
            permission_field == True,
            _active(permission_db_cls),  # Check expiration
        )
    )

    # 3. Role Permission (User must have a role on an accessible team, and that role has the permission)
    # Get user's roles on accessible teams
    user_roles_on_accessible_teams = (
        select(user_team_db_cls.role_id)
        .distinct()
        .join(
            accessible_team_ids_cte,
            user_team_db_cls.team_id == accessible_team_ids_cte.c.id,
        )
        .where(user_team_db_cls.user_id == user_id)
        .where(user_team_db_cls.enabled == True)
        .where(_active(user_team_db_cls))
    )  # Subquery for user's relevant role IDs

    # Check if any of *those* roles have the required permission assigned
    role_perm_exists_specific = exists().where(
        and_(
            permission_db_cls.resource_type == resource_db_cls.__tablename__,
            permission_db_cls.resource_id == resource_db_cls.id,
            permission_db_cls.role_id.in_(
                user_roles_on_accessible_teams
            ),  # Role must be one the user holds on an accessible team
            permission_db_cls.user_id
            == None,  # Role permission (not user/team specific)
            permission_db_cls.team_id == None,
            permission_field == True,
            _active(permission_db_cls),
        )
    )

    return or_(
        user_perm_exists,
        team_perm_exists,
        role_perm_exists_specific,
    )


def _inherited_permission_filter(
    user_id: str,
    resource_cls: Type[Any],
    resource_db_cls: Type[Any],
    accessible_team_ids_cte: CTE,
    db: Session,
    declarative_base: Any,
    required_permission_level: PermissionType,
    visited_classes: set,
    minimum_role: Optional[str],
    db_manager: Any,
) -> Any:
    """The filter for a class that inherits its access (``inherits_access``),
    for a requester who is neither ROOT nor SYSTEM: a record is reachable at
    the level one of its referenced records is (a message, through its
    conversation), or through a Permission row on it. Its own owner, team
    and creator columns grant nothing. It is live (not deleted), and a row
    the server wrote is reachable for EDIT or DELETE only by the server
    (``server_row_modifiable_by``), as the write paths hold it."""
    grants = [
        _build_direct_permission_filter(
            user_id,
            resource_cls,
            accessible_team_ids_cte,
            db,
            required_permission_level,
            declarative_base,
        )
    ]
    for ref_name in resource_db_cls.permission_references:
        ref_cls = referenced_class(resource_db_cls, ref_name)
        ref_column = getattr(resource_db_cls, f"{ref_name}_id", None)
        if ref_cls is None or ref_column is None:
            logger.warning(
                f"Invalid permission reference '{ref_name}' on {resource_cls.__name__}"
            )
            continue
        ref_filter = generate_permission_filter(
            user_id,
            ref_cls,
            db,
            declarative_base,
            required_permission_level,
            _visited_classes=set(visited_classes),
            minimum_role=minimum_role,
            db_manager=db_manager,
        )
        grants.append(ref_column.in_(select(ref_cls.id).where(ref_filter)))

    restrictions = []
    if hasattr(resource_db_cls, "deleted_at"):
        restrictions.append(resource_db_cls.deleted_at.is_(None))
    if (
        hasattr(resource_db_cls, "created_by_user_id")
        and required_permission_level in SERVER_ROW_MODIFYING_LEVELS
    ):
        # The requester is neither ROOT nor SYSTEM (both returned earlier).
        restrictions.append(
            or_(
                resource_db_cls.created_by_user_id.is_(None),
                resource_db_cls.created_by_user_id.notin_(
                    [ROOT_ID, SYSTEM_ID, TEMPLATE_ID]
                ),
            )
        )
    return and_(or_(*grants), *restrictions)


def generate_permission_filter(
    user_id: str,
    resource_cls: Type[Any],
    db: Session,
    declarative_base,
    required_permission_level: "PermissionType" = None,  # type: ignore[assignment]
    _visited_classes: Optional[set] | None = None,
    minimum_role: Optional[str] | None = None,
    db_manager=None,
):
    """
    Generate a SQLAlchemy filter expression to filter query results based on permissions.

    This is the main entry point for permission-based filtering at the SQL level.

    Args:
        user_id: The ID of the user requesting access
        resource_cls: The resource class being queried
        db: Database session
        declarative_base: The declarative base to use for accessing SQLAlchemy models
        required_permission_level: The permission level required (default: PermissionType.VIEW)
        _visited_classes: Internal tracking of visited classes to prevent infinite recursion
        minimum_role: Minimum role required (e.g., "user", "admin", "superadmin")

    Returns:
        A SQLAlchemy filter expression to be used in query.filter()
    """
    # Ensure PermissionType is imported and default is set
    # Local imports to break cycle
    from zephyrex.database.StaticPermissions import (
        PermissionType,
    )  # Local import if needed
    from zephyrex.logic.BLL_Auth import TeamModel, UserTeamModel

    if required_permission_level is None:
        required_permission_level = PermissionType.VIEW

    # Get SQLAlchemy models using the declarative base
    resource_db_cls = _resolve_db_class(resource_cls, declarative_base)

    team_db_cls = TeamModel.DB(declarative_base)
    user_team_db_cls = UserTeamModel.DB(declarative_base)

    # 0. Root/System User Check
    if is_root_id(user_id):
        # Root can see everything, including deleted records
        return true()
    if is_system_id(user_id):
        # SYSTEM is used for internal operations (extension hooks, validation
        # lookups, automated record management) and must be able to view any
        # record. Modifications go through manager-layer checks.
        return true()

    # Initialize recursion guard
    if _visited_classes is None:
        _visited_classes = set()

    # Prevent infinite recursion
    if resource_cls in _visited_classes:
        logger.warning(
            f"Recursive permission check detected and stopped for {resource_cls.__name__}"
        )
        return false()  # Prevent cycles by returning false

    _visited_classes.add(resource_cls)

    # Default behavior is to deny permission
    conditions = []

    # Check system flag - only ROOT_ID and SYSTEM_ID can access system tables
    if hasattr(resource_cls, "system") and getattr(resource_cls, "system", False):
        # For VIEW operations, allow all users to access system entities
        if required_permission_level == PermissionType.VIEW:
            return true()  # Allow all users to view system entities
        # For all other operations, only allow system users
        if not (is_root_id(user_id) or is_system_id(user_id)):
            return false()  # Non-system users can't modify system-flagged tables

    # Create a unique suffix for the CTE based on resource class name and a unique identifier
    # This prevents naming conflicts when multiple CTEs are used in the same query
    import uuid

    unique_suffix = f"_{resource_cls.__name__}_{str(uuid.uuid4())[-8:]}"

    # Get accessible teams CTE with depth limit and unique name
    accessible_team_ids_cte = _get_admin_accessible_team_ids_cte(
        user_id,
        db,
        declarative_base,
        max_depth=5,
        unique_suffix=unique_suffix,
    )

    if inherits_access(resource_db_cls):
        return _inherited_permission_filter(
            user_id,
            resource_cls,
            resource_db_cls,
            accessible_team_ids_cte,
            db,
            declarative_base,
            required_permission_level,
            _visited_classes,
            minimum_role,
            db_manager,
        )

    # Deleted records are visible only to ROOT. This is an AND restriction
    # applied at the end; placing it in ``conditions`` (OR'd) would grant
    # access to every non-deleted record.

    # 1. Direct Ownership Check
    if hasattr(resource_db_cls, "user_id") and resource_db_cls.__tablename__ not in [
        "invitations",
        "Invitees",
    ]:
        conditions.append(resource_db_cls.user_id == user_id)

    # Record Creator Check - Grant access to users who created the record
    if hasattr(
        resource_db_cls, "created_by_user_id"
    ) and resource_db_cls.__tablename__ not in ["invitations", "Invitees"]:
        conditions.append(resource_db_cls.created_by_user_id == user_id)

    # 2. Team Membership Check with role sufficiency
    if hasattr(resource_db_cls, "team_id") and resource_db_cls.__tablename__ not in [
        "invitations",
        "Invitees",
    ]:
        team_filter = resource_db_cls.team_id.in_(select(accessible_team_ids_cte.c.id))

        # For VIEW level, team membership alone grants visibility on team-scoped
        # records. For modifying levels, an additional admin-role check is layered
        # below.
        if required_permission_level == PermissionType.VIEW:
            conditions.append(team_filter)

        # Add role sufficiency check if level > VIEW
        if required_permission_level in [
            PermissionType.EDIT,
            PermissionType.DELETE,
            PermissionType.SHARE,
        ]:
            # A live member of the record's own team whose role administers
            # it (admin_role_ids, the rule TeamAuthority applies).
            admin_roles = admin_role_ids(declarative_base, unique_suffix)
            live_team = aliased(
                team_db_cls, name=f"admin_role_live_team{unique_suffix}"
            )
            conditions.append(
                exists().where(
                    and_(
                        user_team_db_cls.user_id == user_id,
                        user_team_db_cls.team_id == resource_db_cls.team_id,
                        user_team_db_cls.role_id.in_(select(admin_roles.c.id)),
                        user_team_db_cls.enabled == True,
                        _active(user_team_db_cls),
                        # A deleted team's admins keep no hold on its
                        # records, as its members keep no view of them.
                        resource_db_cls.team_id.in_(
                            select(live_team.id).where(live_team.deleted_at.is_(None))
                        ),
                    )
                )
            )

    # 3. System Record Access Logic - Apply to both user_id and created_by_user_id
    # ROOT_ID-created records are restricted to ROOT_ID only. The non-ROOT viewer
    # is filtered out via a final AND restriction below; these rules must NOT be
    # appended to ``conditions`` (which is OR'd) since doing so would grant access
    # to every non-ROOT record.
    if hasattr(resource_db_cls, "user_id") and resource_db_cls.__tablename__ not in [
        "invitations",
        "Invitees",
    ]:
        # SYSTEM_ID records viewable by all, but only modifiable by ROOT_ID and SYSTEM_ID
        if resource_db_cls.user_id == SYSTEM_ID:
            if not (is_root_id(user_id) or is_system_id(user_id)):
                if required_permission_level != PermissionType.VIEW:
                    return false()

        # TEMPLATE_ID records viewable, copyable, executable, shareable by all
        # but only modifiable (EDIT/DELETE) by ROOT_ID and SYSTEM_ID
        if resource_db_cls.user_id == TEMPLATE_ID:
            if not (is_root_id(user_id) or is_system_id(user_id)):
                if required_permission_level in [
                    PermissionType.EDIT,
                    PermissionType.DELETE,
                ]:
                    return false()

    # The users table has its own complete VIEW rule (section 5); the system
    # accounts are SYSTEM-created, so this grant would expose them.
    if hasattr(
        resource_db_cls, "created_by_user_id"
    ) and resource_db_cls.__tablename__ not in ["invitations", "Invitees", USERS_TABLE]:
        # SYSTEM_ID-created records: viewable by all (grant); EDIT/DELETE restricted
        # to ROOT_ID and SYSTEM_ID via the universal-deny return below.
        if required_permission_level == PermissionType.VIEW:
            conditions.append(resource_db_cls.created_by_user_id == SYSTEM_ID)
            conditions.append(resource_db_cls.created_by_user_id == TEMPLATE_ID)
        else:
            # For modifying operations, non-system users cannot edit SYSTEM/TEMPLATE
            # records. Don't add a positive grant here, and don't add a deny that
            # blocks unrelated records.
            pass

    # 5. Special Table Logic for Users. A user sees themselves, and for VIEW
    # the users they share a live team hierarchy with: both memberships
    # enabled, unexpired and not deleted, in a team that is not deleted, the
    # requester's side reaching up through live parent teams (as team-scoped
    # records do) and down through live sub-teams at any depth. A pending
    # invitation is not a shared team. ROOT and SYSTEM returned above; an
    # explicit Permission row on the user (section 4) also grants. Anyone
    # else is invisible, so a server-side lookup of an arbitrary account
    # (login, registration, invitation acceptance) runs as ROOT or SYSTEM,
    # never as the requester.
    if resource_db_cls.__tablename__ == USERS_TABLE:
        conditions.append(resource_db_cls.id == user_id)

        if required_permission_level == PermissionType.VIEW:
            conditions.append(
                exists().where(
                    and_(
                        user_team_db_cls.user_id == resource_db_cls.id,
                        _in_live_team_hierarchy(
                            user_team_db_cls.team_id,
                            user_id,
                            declarative_base,
                            unique_suffix=f"{unique_suffix}_members",
                        ),
                        user_team_db_cls.enabled == True,
                        _active(user_team_db_cls),
                    )
                )
            )

    # Special Table Logic for Teams
    if resource_db_cls.__tablename__ == "teams":
        team_conditions = []

        # Users can see parent teams of teams they're members of
        parent_team_access = resource_db_cls.id.in_(
            select(accessible_team_ids_cte.c.id)
        )
        team_conditions.append(parent_team_access)

        # Users can see system-created teams
        team_conditions.append(resource_db_cls.created_by_user_id == SYSTEM_ID)

        # Users can see teams they created
        team_conditions.append(resource_db_cls.created_by_user_id == user_id)

        # For teams, return only these conditions (no default permission logic)
        return or_(*team_conditions) if team_conditions else false()

    # Special Table Logic for Invitations
    if resource_db_cls.__tablename__ == "invitations":
        # For invitations, we use restrictive logic instead of additive logic
        invitation_conditions = []

        # Users can see invitations to teams they're members of
        # But only if the invitation has a team_id (not public invitations with team_id=NULL)
        team_invitations_filter = and_(
            resource_db_cls.team_id.isnot(
                None
            ),  # Only team invitations, not public ones
            resource_db_cls.team_id.in_(select(accessible_team_ids_cte.c.id)),
        )
        invitation_conditions.append(team_invitations_filter)

        # Users can see public invitations (no team_id) only if they created them
        public_invitations_created_filter = and_(
            resource_cls.team_id.is_(None),  # Only public invitations
            resource_cls.created_by_user_id == user_id,  # That they created
        )
        invitation_conditions.append(public_invitations_created_filter)

        # Users can see invitations if they have an Invitee record for that
        # invitation. The invitee table is owned by ``auth_invitations``;
        # this branch only runs when the resource table itself is
        # ``invitations``, which the extension creates — so the hook is
        # guaranteed to be registered before we reach this code.
        invitee_db_class_hook = _invitation_hooks["invitee_db_class"]
        if invitee_db_class_hook is not None:
            Invitee_db_cls = invitee_db_class_hook(declarative_base)
            user_invited_filter = exists().where(
                and_(
                    Invitee_db_cls.invitation_id == resource_db_cls.id,
                    Invitee_db_cls.user_id == user_id,
                )
            )
            invitation_conditions.append(user_invited_filter)

        # For invitations, return only these conditions (no default permission logic)
        return or_(*invitation_conditions) if invitation_conditions else false()

    # Special Table Logic for Invitees
    if resource_db_cls.__tablename__ == "Invitees":
        # Invitation row is owned by ``auth_invitations``; resolved
        # through the hook so core never imports the extension directly.
        invitation_db_class_hook = _invitation_hooks["invitation_db_class"]
        if invitation_db_class_hook is None:
            return false()
        invitation_db_cls = invitation_db_class_hook(declarative_base)

        # Users can see invitation invitees if they have access to the associated invitation
        # This means: invitations they created OR invitations to teams they're members of
        # Subquery for invitations the user created
        user_created_invitations = select(invitation_db_cls.id).where(
            invitation_db_cls.user_id == user_id
        )

        # Subquery for team invitations to teams the user is a member of
        team_invitations = select(invitation_db_cls.id).where(
            and_(
                invitation_db_cls.team_id.isnot(None),
                invitation_db_cls.team_id.in_(select(accessible_team_ids_cte.c.id)),
            )
        )

        # Invitee records are accessible if their invitation_id is in either subquery
        accessible_invitations = user_created_invitations.union(team_invitations)
        conditions.append(resource_db_cls.invitation_id.in_(accessible_invitations))

    # 4. Direct Permissions via Permission Table (``acl_rbac`` extension).
    # Without the extension, no direct/team/role permission rows exist,
    # so the framework restricts access to the creator-only / team-
    # membership / invitation-driven branches above. This is the
    # documented fail-closed default.
    permission_db_class_hook = _acl_hooks["permission_db_class"]
    if permission_db_class_hook is not None:
        direct_permissions = _build_direct_permission_filter(
            user_id,
            resource_cls,
            accessible_team_ids_cte,
            db,
            required_permission_level,
            declarative_base,
        )
        conditions.append(direct_permissions)

    # Combine all conditions with OR
    if not conditions:
        # If no conditions could be generated (e.g., class has no user_id, team_id, permissions)
        # Default to denying access unless root? Or allowing? Let's deny for safety.
        logger.warning(
            f"No permission conditions generated for {resource_cls.__name__} and user {user_id}. Denying access."
        )
        return false()

    final_filter = or_(*conditions)

    # AND restrictions layered on top of the OR'd grants above. Placing these
    # inside the OR list would invert the meaning and grant universal access.
    # Inequality comparisons against NULL evaluate to NULL (treated as FALSE in
    # WHERE), so the ``ROOT_ID`` denials are wrapped to allow NULL values.
    if not is_root_id(user_id):
        if hasattr(resource_db_cls, "deleted_at"):
            final_filter = and_(final_filter, resource_db_cls.deleted_at.is_(None))
        if hasattr(
            resource_db_cls, "user_id"
        ) and resource_db_cls.__tablename__ not in ["invitations", "Invitees"]:
            final_filter = and_(
                final_filter,
                or_(
                    resource_db_cls.user_id.is_(None),
                    resource_db_cls.user_id != ROOT_ID,
                ),
            )
        if hasattr(
            resource_db_cls, "created_by_user_id"
        ) and resource_db_cls.__tablename__ not in ["invitations", "Invitees"]:
            # ROOT-created records are restricted from non-ROOT viewers, EXCEPT
            # when the row has direct user_id ownership pointing at the viewer.
            # This covers records that ROOT legitimately creates on behalf of a
            # specific user (e.g. a credential created during a root-driven
            # password reset) — without this exception, the viewer would be
            # locked out of their own user-scoped row.
            allow_viewer_owned = []
            if hasattr(resource_db_cls, "user_id"):
                allow_viewer_owned.append(resource_db_cls.user_id == user_id)
            final_filter = and_(
                final_filter,
                or_(
                    resource_db_cls.created_by_user_id.is_(None),
                    resource_db_cls.created_by_user_id != ROOT_ID,
                    *allow_viewer_owned,
                ),
            )
            # SYSTEM/TEMPLATE-owned records are not modifiable by non-system users.
            if not is_system_id(user_id) and required_permission_level in [
                PermissionType.EDIT,
                PermissionType.DELETE,
            ]:
                final_filter = and_(
                    final_filter,
                    or_(
                        resource_db_cls.created_by_user_id.is_(None),
                        and_(
                            resource_db_cls.created_by_user_id != SYSTEM_ID,
                            resource_db_cls.created_by_user_id != TEMPLATE_ID,
                        ),
                    ),
                )

    return final_filter


def live_team_members_filter(
    requester_id: str, team_id: str, users_db_cls: Any, declarative_base: Any
) -> Any:
    """A filter on ``users_db_cls`` for the live members of ``team_id`` the
    requester may list: users with an enabled, unexpired, undeleted
    membership in it, when the team is live and the requester holds a live
    membership in it, in one of its sub-teams, or in one of its parent
    teams (the reach the users rule gives a requester, in both directions,
    so the list names no one that rule hides); ROOT and SYSTEM list any
    team's. Anyone else matches no one, so membership of a team outside the
    requester's hierarchy never shows."""
    from zephyrex.logic.BLL_Auth import TeamModel, UserTeamModel

    team_db_cls = TeamModel.DB(declarative_base)
    user_team_db_cls = UserTeamModel.DB(declarative_base)
    live_team = aliased(team_db_cls, name="team_members_live_team")
    membership = and_(
        user_team_db_cls.user_id == users_db_cls.id,
        user_team_db_cls.team_id == team_id,
        user_team_db_cls.enabled == True,
        _active(user_team_db_cls),
        user_team_db_cls.team_id.in_(
            select(live_team.id).where(live_team.deleted_at.is_(None))
        ),
    )
    if is_root_id(requester_id) or is_system_id(requester_id):
        return exists().where(membership)
    return and_(
        exists().where(membership),
        _in_live_team_hierarchy(
            literal(team_id), requester_id, declarative_base, "_team_members"
        ),
    )


def user_has_permission(
    user_id,
    record_cls,
    record_id,
    db,
    permission_type: PermissionType,
    declarative_base=None,
    db_manager=None,
) -> bool:
    """
    Check if a user has a specific permission for a record.

    This is the single parameterized entry point for permission-type checks.
    ``user_can_edit``, ``user_can_share``, and the ADE ``user_has_*`` helpers
    all delegate here.

    Args:
        user_id: The ID of the user requesting access
        record_cls: The model class
        record_id: The ID of the record to check
        db: Database session
        permission_type: The PermissionType to check (EDIT, SHARE, etc.)
        declarative_base: The declarative base to use for accessing SQLAlchemy models
        db_manager: Database manager instance (optional, takes precedence over declarative_base)

    Returns:
        bool: True if user has the requested permission, False otherwise
    """
    if db_manager and hasattr(db_manager, "Base"):
        declarative_base = db_manager.Base
    elif declarative_base is None:
        raise ValueError(
            "Either declarative_base or db_manager is required for permission checks"
        )

    result, _ = check_permission(
        user_id, record_cls, record_id, db, declarative_base, permission_type
    )
    return bool(result == PermissionResult.GRANTED)


def user_can_edit(
    user_id, record_cls, record_id, db, declarative_base=None, db_manager=None
):
    """Check if a user has edit permission for a record."""
    return user_has_permission(
        user_id,
        record_cls,
        record_id,
        db,
        PermissionType.EDIT,
        declarative_base,
        db_manager,
    )


def user_can_share(
    user_id, record_cls, record_id, db, declarative_base=None, db_manager=None
):
    """Check if a user has share permission for a record."""
    return user_has_permission(
        user_id,
        record_cls,
        record_id,
        db,
        PermissionType.SHARE,
        declarative_base,
        db_manager,
    )


def auto_determine_create_permission_reference(cls):
    """
    Automatically determine and set create_permission_reference for a class
    if it has exactly one permission reference.

    Args:
        cls: The model class to update
    """
    if hasattr(cls, "permission_references") and len(cls.permission_references) == 1:
        cls.create_permission_reference = cls.permission_references[0]
    elif hasattr(cls, "permission_references") and len(cls.permission_references) > 1:
        logger.warning(
            f"Multiple permission references in {cls.__name__} but no create_permission_reference defined: {cls.permission_references}"
        )
    return cls


@classmethod  # type: ignore[misc]
def user_has_read_access(
    cls, user_id, record, db, declarative_base=None, minimum_role=None, referred=False
):
    """
    Check if a user has read access to a user record.

    Args:
        user_id: The ID of the user requesting access
        record: The User record to check (can be ID or User instance)
        db: Database session
        declarative_base: The declarative base to use for accessing SQLAlchemy models
        minimum_role: Minimum role required (if applicable)
        referred: Whether this check is part of a referred access check

    Returns:
        bool: True if access is granted, False otherwise
    """
    from zephyrex.database.StaticPermissions import (
        PermissionResult,
        PermissionType,
        check_permission,
        is_root_id,
        is_system_id,
    )

    # ROOT_ID can access everything
    if is_root_id(user_id):
        return True

    # SYSTEM_ID can access most things
    if is_system_id(user_id):
        return True

    # If record is a string (ID), retrieve the actual record
    if isinstance(record, str):
        record_id = record
        record = db.query(cls).filter(cls.id == record_id).first()
        if record is None:
            return False

    # Check for deleted records - only ROOT_ID can see them
    if hasattr(record, "deleted_at") and record.deleted_at is not None:
        return is_root_id(user_id)

    # Users can see their own records
    if user_id == record.id:
        return True

    # For non-referred checks, use the unified permission system
    if not referred and declarative_base is not None:
        result, _ = check_permission(
            user_id, cls, record.id, db, declarative_base, PermissionType.VIEW
        )
        return result == PermissionResult.GRANTED

    return False
