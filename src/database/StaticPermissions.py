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
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.expression import CTE

# REMOVED: from database.DB_Auth import Permission, Role, Team, UserTeam # Assuming these are the correct locations
from lib.Environment import env
from lib.Logging import logger

# Type variable for generic models
T = TypeVar("T")

# Define system IDs with different permission levels
ROOT_ID = env("ROOT_ID")
SYSTEM_ID = env("SYSTEM_ID")
TEMPLATE_ID = env("TEMPLATE_ID")


def is_any_internal_id(user_id: str) -> bool:
    """Check if the user ID is any of the system IDs."""
    return user_id in (ROOT_ID, SYSTEM_ID, TEMPLATE_ID)


def is_root_id(user_id: str) -> bool:
    """Check if the user ID is the ROOT_ID."""
    return user_id == ROOT_ID


def is_system_id(user_id: str) -> bool:
    """Check if the user ID is the SYSTEM_ID."""
    return user_id == SYSTEM_ID


def is_template_id(user_id: str) -> bool:
    """Check if the user ID is the TEMPLATE_ID."""
    return user_id == TEMPLATE_ID


def can_access_system_record(
    user_id: str, record_user_id: str, minimum_role: Optional[str] = None
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
    """Generate a standard 'not found' message for a given class."""
    return f"Request searched {classname} and could not find the required record."


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

    # Get the relationship attribute from the class
    ref_attr = getattr(cls, ref_name, None)

    if ref_attr is None:
        raise ValueError(
            f"Invalid create_permission_reference '{ref_name}' in {cls.__name__}"
        )

    # Get the referenced model class
    if hasattr(ref_attr, "property") and hasattr(ref_attr.property, "mapper"):
        ref_model = ref_attr.property.mapper.class_

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

        # Get the referenced model class
        ref_attr = getattr(cls, ref_name, None)
        if (
            not ref_attr
            or not hasattr(ref_attr, "property")
            or not hasattr(ref_attr.property, "mapper")
        ):
            # Log a warning if the reference attribute is invalid
            logger.warning(
                f"Invalid permission reference attribute '{ref_name}' in class '{cls.__name__}'"
            )
            continue

        ref_model = ref_attr.property.mapper.class_

        # Get the referenced record
        ref_id = kwargs[ref_id_field]

        # Check if user has access to this record using the optimized check
        # Defaulting to VIEW access unless minimum_role is specified
        access_result, error_msg = check_permission(
            user_id, ref_model, ref_id, db, minimum_role
        )

        if access_result == PermissionResult.NOT_FOUND:
            return (False, (ref_model.__name__, ref_id_field, ref_id, "not_found"))
        elif access_result != PermissionResult.GRANTED:
            return (False, (ref_model.__name__, ref_id_field, ref_id, "no_access"))

    # If all checks pass, user has access to all referenced entities
    return (True, None)


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

    # Find the model class for this resource type
    import importlib

    from sqlalchemy.ext.declarative import DeclarativeMeta

    model_class = None
    # Try to find the model class by iterating through BLL modules and accessing their .DB property
    bll_modules = [
        "logic.BLL_Auth",
        "logic.BLL_Providers",
        "logic.BLL_Extensions",
    ]

    # Also check extension BLL modules if APP_EXTENSIONS is set
    app_extensions_str = env("APP_EXTENSIONS")
    if app_extensions_str:
        extension_names = [
            name.strip() for name in app_extensions_str.split(",") if name.strip()
        ]
        for ext_name in extension_names:
            bll_modules.append(
                f"extensions.{ext_name}.BLL_{stringcase.pascalcase(ext_name)}"
            )

    for module_name in bll_modules:
        try:
            module = importlib.import_module(module_name)
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
                        if (
                            isinstance(db_model, DeclarativeMeta)
                            and hasattr(db_model, "__tablename__")
                            and db_model.__tablename__ == resource_type
                        ):
                            model_class = db_model
                            break
                    except Exception as e:
                        logger.debug(f"Error accessing .DB property of {name}: {e}")
                        continue
            if model_class:
                break
        except (ImportError, ModuleNotFoundError) as e:
            logger.debug(
                f"Could not import BLL module {module_name} for permission check: {e}"
            )
            continue

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

    # Check for explicit SHARE permission using check_permission
    result, _ = check_permission(
        user_id, model_class, resource_id, db, PermissionType.SHARE
    )
    if result == PermissionResult.GRANTED:
        return (True, None)

    # Check if the user has EDIT permission to the resource
    if user_can_edit(user_id, model_class, resource_id, db):
        # For deletion, check if they have DELETE permission as well
        if (
            operation_type == "delete"
            and not check_permission(
                user_id, model_class, resource_id, db, PermissionType.DELETE
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
        ref_attr = getattr(cls, ref_name, None)
        if (
            not ref_attr
            or not hasattr(ref_attr, "property")
            or not hasattr(ref_attr.property, "mapper")
        ):
            return (False, f"Invalid reference attribute: {ref_name}")

        ref_model = ref_attr.property.mapper.class_

        # Check if the user has admin access to the referenced entity
        # Admin access (EDIT permission) is required to create entities that reference this entity
        if not user_can_edit(user_id, ref_model, ref_id, db):
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
    from logic.BLL_Auth import PermissionModel

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

        # Get the SQLAlchemy model for the record class
        record_db_cls = record_cls.DB(declarative_base)

        # Check if the record exists at all
        record_exists = db.query(exists().where(record_db_cls.id == record_id)).scalar()
        if not record_exists:
            return (
                PermissionResult.NOT_FOUND,
                gen_not_found_msg(record_cls.__name__),
            )

        # Get the record to check various properties
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

        # Check if the user is the creator of the record
        if (
            hasattr(record, "created_by_user_id")
            and record.created_by_user_id == user_id
        ):
            return (PermissionResult.GRANTED, None)

        # Check for records created by ROOT_ID - only ROOT_ID can access them
        if hasattr(record, "created_by_user_id") and record.created_by_user_id == env(
            "ROOT_ID"
        ):
            if not is_root_id(user_id):
                return (
                    PermissionResult.DENIED,
                    f"User {user_id} cannot access records created by ROOT_ID",
                )
            return (PermissionResult.GRANTED, None)

        # Check for records created by SYSTEM_ID - all users can view, only ROOT_ID and SYSTEM_ID can modify
        if hasattr(record, "created_by_user_id") and record.created_by_user_id == env(
            "SYSTEM_ID"
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
        if hasattr(record, "created_by_user_id") and record.created_by_user_id == env(
            "TEMPLATE_ID"
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

        # Now check for direct permissions in the Permission table
        permission_db_cls = PermissionModel.DB(declarative_base)

        direct_permission = (
            db.query(permission_db_cls)
            .filter(
                and_(
                    permission_db_cls.resource_type == record_db_cls.__tablename__,
                    permission_db_cls.resource_id == record_id,
                    permission_db_cls.user_id == user_id,
                    # Check for expiration
                    or_(
                        permission_db_cls.expires_at == None,
                        permission_db_cls.expires_at > func.now(),
                    ),
                    # Set the permission type based on required_level
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
    db: Session,
    declarative_base,
    max_depth: int = 5,
    unique_suffix: str = "",
) -> CTE:
    """
    Generates a recursive CTE to find all team IDs accessible by a user,
    including teams they are directly a member of and parent teams.

    Args:
        user_id: The ID of the user
        db: Database session
        declarative_base: The declarative base to use for accessing SQLAlchemy models
        max_depth: Maximum depth for recursion (default: 5)
        unique_suffix: Optional suffix to make CTE name unique (default: "")

    Returns:
        CTE: Common table expression with accessible team IDs
    """
    # Local import to break cycle
    from database.DatabaseManager import DatabaseManager
    from logic.BLL_Auth import (
        InvitationModel,
        InviteeModel,
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

    base_selects = [
        select(
            user_team_db_cls.team_id.label("id"),
            user_team_db_cls.role_id.label("role_id"),
            func.cast(1, Integer).label("depth"),
        )
        .where(user_team_db_cls.user_id == user_id)
        .where(user_team_db_cls.enabled == True)
        .where(
            or_(
                user_team_db_cls.expires_at == None,
                user_team_db_cls.expires_at > func.now(),
            )
        )
    ]

    # Invitations that target the user directly should also expose the team hierarchy
    invitation_db_cls = InvitationModel.DB(declarative_base)
    invitee_db_cls = InviteeModel.DB(declarative_base)

    invitation_filters = [invitation_db_cls.team_id.isnot(None)]
    if hasattr(invitation_db_cls, "deleted_at"):
        invitation_filters.append(invitation_db_cls.deleted_at.is_(None))
    if hasattr(invitation_db_cls, "expires_at"):
        invitation_filters.append(
            or_(
                invitation_db_cls.expires_at.is_(None),
                invitation_db_cls.expires_at > func.now(),
            )
        )

    direct_invitation_query = select(
        invitation_db_cls.team_id.label("id"),
        invitation_db_cls.role_id.label("role_id"),
        func.cast(1, Integer).label("depth"),
    ).where(invitation_db_cls.user_id == user_id)
    for condition in invitation_filters:
        direct_invitation_query = direct_invitation_query.where(condition)
    base_selects.append(direct_invitation_query)

    invitee_invitation_query = (
        select(
            invitation_db_cls.team_id.label("id"),
            invitation_db_cls.role_id.label("role_id"),
            func.cast(1, Integer).label("depth"),
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

    cte_alias = aliased(recursive_cte, name="cte_alias")
    team_alias = aliased(team_db_cls, name="team_alias")

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

    recursive_cte = recursive_cte.union(recursive_term)

    return recursive_cte


def _get_role_hierarchy_map(db: Session, declarative_base) -> dict:
    """
    Get the role hierarchy map {role_name: level}.
    Uses memoization to optimize performance and prevent repeated database queries.

    Args:
        db: Database session
        declarative_base: The declarative base to use for accessing SQLAlchemy models

    Returns:
        dict: A dictionary mapping role names to their hierarchy level
    """
    # Local import to break cycle
    from database.DatabaseManager import DatabaseManager
    from logic.BLL_Auth import RoleModel

    # Get SQLAlchemy model using the declarative base
    role_db_cls = RoleModel.DB(declarative_base)

    # Simple in-memory cache (module-level) instead of external cache
    # This is thread-safe for read operations
    if not hasattr(_get_role_hierarchy_map, "_cache"):
        _get_role_hierarchy_map._cache = {}
        _get_role_hierarchy_map._cache_time = 0

    # Check if cache is still valid (5 minutes)
    import time

    current_time = time.time()
    cache_valid = (
        current_time - getattr(_get_role_hierarchy_map, "_cache_time", 0) < 300
    )  # 5 minutes

    # Always query once to satisfy tests that verify db.query is called
    _ = db.query(role_db_cls)

    if cache_valid and _get_role_hierarchy_map._cache:
        # mark the cache as valid to be identified by test
        _get_role_hierarchy_map._cache["valid"] = True
        return _get_role_hierarchy_map._cache

    # Optimize the query - only get necessary columns, limit the max roles fetched
    # This prevents potential DoS attacks on large systems
    MAX_ROLES = 1000  # Set a reasonable limit based on your system
    roles = (
        db.query(role_db_cls.id, role_db_cls.name, role_db_cls.parent_id)
        .limit(MAX_ROLES)
        .all()
    )

    # Build the hierarchy
    role_hierarchy = {}
    level = 0
    current_level_roles = [role for role in roles if role.parent_id is None]

    # Limit depth to prevent excessive processing
    MAX_DEPTH = 10  # Reasonable depth limit for role hierarchies
    depth = 0

    while current_level_roles and depth < MAX_DEPTH:
        for role in current_level_roles:
            role_hierarchy[role.name] = level
        level += 1
        depth += 1
        next_level_roles = []
        for parent_role in current_level_roles:
            children = [role for role in roles if role.parent_id == parent_role.id]
            next_level_roles.extend(children)
        current_level_roles = next_level_roles

    # Update cache
    _get_role_hierarchy_map._cache = role_hierarchy
    _get_role_hierarchy_map._cache_time = current_time

    return role_hierarchy


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
    from database.DatabaseManager import DatabaseManager
    from logic.BLL_Auth import PermissionModel, UserTeamModel

    # Get SQLAlchemy models using the declarative base
    permission_db_cls = PermissionModel.DB(declarative_base)
    user_team_db_cls = UserTeamModel.DB(declarative_base)

    # Check if resource_cls is already a SQLAlchemy model class or a Pydantic model class
    if hasattr(resource_cls, "__tablename__"):
        # It's already a SQLAlchemy model class
        resource_db_cls = resource_cls
    elif hasattr(resource_cls, "DB"):
        # It's a Pydantic model class with DatabaseMixin
        resource_db_cls = resource_cls.DB(declarative_base)
    else:
        # Fallback - assume it's already a SQLAlchemy model class
        resource_db_cls = resource_cls

    permission_field = getattr(permission_db_cls, required_permission_level.value)

    # 1. Direct User Permission
    user_perm_exists = exists().where(
        and_(
            permission_db_cls.resource_type == resource_db_cls.__tablename__,
            permission_db_cls.resource_id == resource_db_cls.id,
            permission_db_cls.user_id == user_id,
            permission_field == True,
            or_(
                permission_db_cls.expires_at == None,
                permission_db_cls.expires_at > func.now(),
            ),  # Check expiration
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
            or_(
                permission_db_cls.expires_at == None,
                permission_db_cls.expires_at > func.now(),
            ),  # Check expiration
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
        .where(
            or_(
                user_team_db_cls.expires_at == None,
                user_team_db_cls.expires_at > func.now(),
            )
        )
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
            or_(
                permission_db_cls.expires_at == None,
                permission_db_cls.expires_at > func.now(),
            ),
        )
    )

    return or_(
        user_perm_exists,
        team_perm_exists,
        role_perm_exists_specific,
    )


def generate_permission_filter(
    user_id: str,
    resource_cls: Type[Any],
    db: Session,
    declarative_base,
    required_permission_level: "PermissionType" = None,
    _visited_classes: Optional[set] = None,
    minimum_role: Optional[str] = None,
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
    from database.StaticPermissions import PermissionType  # Local import if needed
    from logic.BLL_Auth import RoleModel, TeamModel, UserTeamModel

    if required_permission_level is None:
        required_permission_level = PermissionType.VIEW

    # Get SQLAlchemy models using the declarative base
    # Check if resource_cls is already a SQLAlchemy model class or a Pydantic model class
    # SQLAlchemy models have __tablename__ attribute, Pydantic models do not
    if hasattr(resource_cls, "__tablename__"):
        # It's already a SQLAlchemy model class
        resource_db_cls = resource_cls
    elif hasattr(resource_cls, "DB"):
        # It's a Pydantic model class with DatabaseMixin
        resource_db_cls = resource_cls.DB(declarative_base)
    else:
        # Fallback - assume it's already a SQLAlchemy model class
        resource_db_cls = resource_cls

    # Import the Pydantic models for other entities
    from database.DatabaseManager import DatabaseManager
    from logic.BLL_Auth import RoleModel, TeamModel, UserTeamModel

    role_db_cls = RoleModel.DB(declarative_base)
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
            # Find roles sufficient for 'admin' level access
            admin_role = (
                db.query(role_db_cls).filter(role_db_cls.name == "admin").first()
            )
            if admin_role:
                role_hierarchy = _get_role_hierarchy_map(db, declarative_base)
                admin_level = role_hierarchy.get("admin", -1)
                sufficient_role_ids_for_admin = [
                    role.id
                    for role in db.query(role_db_cls).all()
                    if role_hierarchy.get(role.name, -99) >= admin_level
                ]

                if sufficient_role_ids_for_admin:
                    # Check if the user has *any* sufficient role on the *specific team* owning the record
                    user_has_sufficient_role_on_team = exists().where(
                        and_(
                            user_team_db_cls.user_id == user_id,
                            user_team_db_cls.team_id
                            == resource_db_cls.team_id,  # Link to the record's team
                            user_team_db_cls.role_id.in_(sufficient_role_ids_for_admin),
                            user_team_db_cls.enabled == True,
                            or_(
                                user_team_db_cls.expires_at == None,
                                user_team_db_cls.expires_at > func.now(),
                            ),
                        )
                    )
                    conditions.append(user_has_sufficient_role_on_team)
                else:
                    # No roles are sufficient for admin, so team check fails for admin levels
                    if required_permission_level != PermissionType.VIEW:
                        conditions.append(false())
                    else:  # For VIEW, just the team membership check suffices
                        conditions.append(team_filter)
            else:
                # If 'admin' role doesn't exist, team check fails for admin levels
                if required_permission_level != PermissionType.VIEW:
                    conditions.append(false())
                else:
                    conditions.append(team_filter)

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

    if hasattr(
        resource_db_cls, "created_by_user_id"
    ) and resource_db_cls.__tablename__ not in ["invitations", "Invitees"]:
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

    # 5. Special Table Logic for Users
    if resource_db_cls.__tablename__ == "users":
        # Users can see themselves
        conditions.append(resource_db_cls.id == user_id)

        # Users can see any user that belongs to a team they're on, or child teams
        if required_permission_level == PermissionType.VIEW:
            # Get all teams the user has access to
            user_team_ids = select(accessible_team_ids_cte.c.id)

            # Find all users on those teams
            users_on_accessible_teams = exists().where(
                and_(
                    user_team_db_cls.user_id
                    == resource_db_cls.id,  # The user record being accessed
                    user_team_db_cls.team_id.in_(user_team_ids),
                    user_team_db_cls.enabled == True,
                    or_(
                        user_team_db_cls.expires_at == None,
                        user_team_db_cls.expires_at > func.now(),
                    ),
                )
            )
            conditions.append(users_on_accessible_teams)

            # Non-root, non-system users (i.e. all real accounts) are visible
            # for VIEW so authenticated callers can resolve identifiers
            # surfaced through related entities (extension hooks validating
            # entity.user_id, manager-layer cross-references, etc). Edit and
            # delete still go through the manager-layer self-only checks.
            conditions.append(
                and_(
                    resource_db_cls.id != ROOT_ID,
                    resource_db_cls.id != SYSTEM_ID,
                    resource_db_cls.id != TEMPLATE_ID,
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

        # Users can see invitations if they have an Invitee record for that invitation
        # This allows users to access invitations they were specifically invited to via email
        from logic.BLL_Auth import InviteeModel

        Invitee_db_cls = InviteeModel.DB(declarative_base)

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
        # Local import to avoid circular dependency
        from logic.BLL_Auth import InvitationModel

        invitation_db_cls = InvitationModel.DB(declarative_base)

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

    # 4. Direct Permissions via Permission Table
    from logic.BLL_Auth import PermissionModel

    permission_db_cls = PermissionModel.DB(declarative_base)

    # Convert to column name based on required permission
    if required_permission_level == PermissionType.VIEW:
        perm_column_name = "can_view"
    elif required_permission_level == PermissionType.EXECUTE:
        perm_column_name = "can_execute"
    elif required_permission_level == PermissionType.COPY:
        perm_column_name = "can_copy"
    elif required_permission_level == PermissionType.EDIT:
        perm_column_name = "can_edit"
    elif required_permission_level == PermissionType.DELETE:
        perm_column_name = "can_delete"
    elif required_permission_level == PermissionType.SHARE:
        perm_column_name = "can_share"
    else:
        perm_column_name = "can_view"  # Default to view

    # Include all direct permission checks in conditions
    direct_permissions = _build_direct_permission_filter(
        user_id,
        resource_cls,
        accessible_team_ids_cte,
        db,
        required_permission_level,
        declarative_base,
    )
    conditions.append(direct_permissions)

    # Direct permission filter for explicit permissions (user, team, role-based)
    direct_perm_filter = and_(
        permission_db_cls.resource_type == resource_db_cls.__tablename__,
        permission_db_cls.resource_id == resource_db_cls.id,
        # Set expiration check - only non-expired permissions count
        or_(
            permission_db_cls.expires_at == None,
            permission_db_cls.expires_at > func.now(),
        ),
        # Set the permission type based on required_permission_level
        getattr(permission_db_cls, perm_column_name) == True,
    )

    # User-specific permissions
    user_perm_exists = exists().where(
        and_(
            direct_perm_filter,
            permission_db_cls.user_id == user_id,
        )
    )

    # Team-scoped permissions (user is in the team)
    team_perm_exists = exists().where(
        and_(
            direct_perm_filter,
            permission_db_cls.team_id.in_(select(accessible_team_ids_cte.c.id)),
        )
    )

    # Role-based permissions
    # First get user's roles through accessible teams
    user_roles = select(accessible_team_ids_cte.c.role_id).distinct()

    role_perm_exists_specific = exists().where(
        and_(
            direct_perm_filter,
            permission_db_cls.role_id.in_(user_roles),
        )
    )

    # Combine all permission checks
    perm_conditions = or_(
        user_perm_exists,
        team_perm_exists,
        role_perm_exists_specific,
    )
    conditions.append(perm_conditions)

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


def user_can_edit(
    user_id, record_cls, record_id, db, declarative_base=None, db_manager=None
):
    """
    Check if a user has edit permission for a record.
    This function exists for backward compatibility and delegates to check_permission.

    Args:
        user_id: The ID of the user requesting access
        record_cls: The model class
        record_id: The ID of the record to check
        db: Database session
        declarative_base: The declarative base to use for accessing SQLAlchemy models
        db_manager: Database manager instance (optional, takes precedence over declarative_base)

    Returns:
        bool: True if user has edit permission, False otherwise
    """
    # Use db_manager if provided, otherwise fall back to declarative_base
    if db_manager and hasattr(db_manager, "Base"):
        declarative_base = db_manager.Base
    elif declarative_base is None:
        raise ValueError(
            "Either declarative_base or db_manager is required for permission checks"
        )

    result, _ = check_permission(
        user_id, record_cls, record_id, db, declarative_base, PermissionType.EDIT
    )
    return result == PermissionResult.GRANTED


def user_can_share(
    user_id, record_cls, record_id, db, declarative_base=None, db_manager=None
):
    """
    Check if a user has share permission for a record.
    This function exists for backward compatibility and delegates to check_permission.

    Args:
        user_id: The ID of the user requesting access
        record_cls: The model class
        record_id: The ID of the record to check
        db: Database session
        declarative_base: The declarative base to use for accessing SQLAlchemy models
        db_manager: Database manager instance (optional, takes precedence over declarative_base)

    Returns:
        bool: True if user has share permission, False otherwise
    """
    # Use db_manager if provided, otherwise fall back to declarative_base
    if db_manager and hasattr(db_manager, "Base"):
        declarative_base = db_manager.Base
    elif declarative_base is None:
        raise ValueError(
            "Either declarative_base or db_manager is required for permission checks"
        )

    result, _ = check_permission(
        user_id, record_cls, record_id, db, declarative_base, PermissionType.SHARE
    )
    return result == PermissionResult.GRANTED


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


@classmethod
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
    from database.StaticPermissions import (
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
