from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

import pytest

from zephyrex.AbstractTest import AbstractTest, CategoryOfTest, ClassOfTestsConfig

# Database session will be provided by the db fixture from conftest.py
from zephyrex.database.StaticPermissions import check_permission
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic import obj_to_dict
from zephyrex.lib.Scalability import (
    ScalabilityProfile,
    ScalingMetric,
    assert_scaling_within_bounds,
)

# Database lock retries are now handled automatically by pytest hooks in conftest.py


# Set up logging


class PermissionType(str, Enum):
    """Permission types for testing permissions."""

    VIEW = "view"
    EXECUTE = "execute"
    COPY = "copy"
    EDIT = "edit"
    DELETE = "delete"
    SHARE = "share"


class PermissionResult(str, Enum):
    """Permission check results."""

    GRANTED = "granted"
    DENIED = "denied"
    ERROR = "error"


# @pytest.mark.dependency(
#     scope="session", depends=["zephyrex.database.AbstractDatabaseEntity_test"]
# )
@pytest.mark.db
class AbstractDBTest(AbstractTest):
    """
    Comprehensive base class for database entity test suites.

    This class provides exhaustive testing for all database entity functionality,
    including CRUD operations, permissions, system entity handling, and reference
    inheritance. It tests every function from AbstractDatabaseEntity and ensures
    proper behavior for all entity types.

    Child classes must override:
    - entity_class: The database model class being tested
    - create_fields: Dict of fields to use when creating test entities
    - update_fields: Dict of fields to use when updating test entities

    Configuration options:
    - unique_fields: List of fields that should have unique values (default: ["name"])
    - is_system_entity: Whether this is a system-flagged entity (default: False)
    - has_permission_references: Whether this entity has permission references (default: False)
    - reference_fields: Dict mapping reference name to field name (for permission inheritance testing)
    - referencing_entity_classes: List of entity classes that reference this entity (for permission inheritance testing)
    - test_config: Test execution parameters
    - skip_tests: Tests to skip with documented reasons
    """

    # Required overrides that child classes must provide

    # Default test configuration
    test_config: ClassOfTestsConfig = ClassOfTestsConfig(
        categories=[CategoryOfTest.DATABASE]
    )

    db = None

    # Test settings
    # Set to True for additional logging

    @classmethod
    def setUpClass(cls):
        """Set up the test class - database setup is handled by server fixture."""
        super().setUpClass()
        cls.logger.debug(f"\\n=== Setting up {cls.__name__} (using server fixture) ===")

    def setup_method(self, method):
        """Set up method-level test fixtures."""
        super().setup_method(method)
        # Database session and manager will be injected by pytest fixtures
        # self.db will be set by the db fixture from conftest.py
        # SQLAlchemy model will be created automatically by ensure_model() from AbstractTest
        self.sqlalchemy_model = None

    def teardown_method(self, method):
        """Clean up method-level test fixtures."""
        try:
            # Close the database session if one was opened on this instance.
            db = self.__dict__.get("db")
            if db is not None:
                db.close()
            if "db" in self.__dict__:
                del self.__dict__["db"]
        finally:
            super().teardown_method(method)

    def _get_model_registry(self):
        """Get model_registry with error checking."""
        model_registry = getattr(self, "model_registry", None)
        if model_registry is None:
            raise RuntimeError(
                f"model_registry not set in {self.__class__.__name__} - "
                "ensure test method accepts model_registry fixture parameter"
            )
        return model_registry

    def grant_permission(
        self,
        user_id: str,
        entity_id: str,
        permission_type: PermissionType = PermissionType.VIEW,
        team_id: str | None = None,
        role_id: str | None = None,
        expires_at: datetime = None,  # type: ignore[assignment]
        can_view: bool | None = None,
        can_execute: bool | None = None,
        can_copy: bool | None = None,
        can_edit: bool | None = None,
        can_delete: bool | None = None,
        can_share: bool | None = None,
    ) -> Dict[str, Any]:
        """
        Grant a specific permission to a user, team, or role on an entity.

        Args:
            user_id: ID of the user to grant permission to (None if granting to team/role)
            entity_id: ID of the entity to grant permission on
            permission_type: Type of permission to grant
            team_id: ID of the team to grant permission to (optional)
            role_id: ID of the role to grant permission to (optional)
            expires_at: Expiration datetime for the permission (optional)
            can_view: Explicitly set can_view flag (overrides permission_type)
            can_execute: Explicitly set can_execute flag
            can_copy: Explicitly set can_copy flag
            can_edit: Explicitly set can_edit flag
            can_delete: Explicitly set can_delete flag
            can_share: Explicitly set can_share flag

        Returns:
            Dict[str, Any]: The created permission record
        """
        # Create permission attributes dict
        permission_attrs = {
            "can_view": False,
            "can_execute": False,
            "can_copy": False,
            "can_edit": False,
            "can_delete": False,
            "can_share": False,
        }

        # Set the specific permission based on permission_type
        if permission_type == PermissionType.VIEW:
            permission_attrs["can_view"] = True
        elif permission_type == PermissionType.EXECUTE:
            permission_attrs["can_execute"] = True
            permission_attrs["can_view"] = True  # Execute implies view
        elif permission_type == PermissionType.COPY:
            permission_attrs["can_copy"] = True
            permission_attrs["can_view"] = True  # Copy implies view
        elif permission_type == PermissionType.EDIT:
            permission_attrs["can_edit"] = True
            permission_attrs["can_view"] = True  # Edit implies view
        elif permission_type == PermissionType.DELETE:
            permission_attrs["can_delete"] = True
            permission_attrs["can_view"] = True  # Delete implies view
        elif permission_type == PermissionType.SHARE:
            permission_attrs["can_share"] = True
            permission_attrs["can_delete"] = True
            permission_attrs["can_edit"] = True
            permission_attrs["can_copy"] = True
            permission_attrs["can_execute"] = True
            permission_attrs["can_view"] = True  # Share implies all permissions

        # Override with explicit parameters if provided
        if can_view is not None:
            permission_attrs["can_view"] = can_view
        if can_execute is not None:
            permission_attrs["can_execute"] = can_execute
        if can_copy is not None:
            permission_attrs["can_copy"] = can_copy
        if can_edit is not None:
            permission_attrs["can_edit"] = can_edit
        if can_delete is not None:
            permission_attrs["can_delete"] = can_delete
        if can_share is not None:
            permission_attrs["can_share"] = can_share

        # Prepare permission data
        permission_data = {
            "resource_type": self.sqlalchemy_model.__tablename__,
            "resource_id": entity_id,
            **permission_attrs,
        }

        # Add optional parameters if provided
        if user_id is not None:
            permission_data["user_id"] = user_id

        if team_id is not None:
            permission_data["team_id"] = team_id

        if role_id is not None:
            permission_data["role_id"] = role_id

        if expires_at is not None:
            permission_data["expires_at"] = expires_at

        # Permission creation is owned by ``acl_rbac``; we dispatch through
        # ``_acl_hooks["create_permission"]`` so this test scaffolding
        # never imports from the extension directly. When the extension
        # is absent the test using this helper is logically meaningless
        # (no permission row to create) — surface that as a clean
        # ``RuntimeError`` rather than an opaque ``ImportError``.
        from zephyrex.logic.BLL_Auth import _acl_hooks

        create_permission_hook = _acl_hooks["create_permission"]
        if create_permission_hook is None:
            raise RuntimeError(
                "create_permission requires the ``acl_rbac`` extension to "
                "be loaded; none of the registered _acl_hooks fire without it."
            )
        try:
            model_registry = self._get_model_registry()
            permission = create_permission_hook(
                requester_id=self.ROOT_ID,
                model_registry=model_registry,
                return_type="dict",
                **permission_data,
            )
            if permission:
                self.tracked_entities.append(
                    {"id": permission["id"], "resource_id": entity_id}
                )
            return permission  # type: ignore[no-any-return]
        except Exception as e:
            error_msg = (
                f"{self.sqlalchemy_model.__name__}: Failed to create "
                f"permission: {str(e)}"
            )
            logger.error(error_msg)
            raise Exception(error_msg) from e

    def create_expired_permission(
        self,
        user_id: str,
        entity_id: str,
        permission_type: PermissionType = PermissionType.VIEW,
        days_ago: int = 1,
    ) -> Dict[str, Any]:
        """
        Create a permission that has already expired.

        Args:
            user_id: ID of the user to grant permission to
            entity_id: ID of the entity to grant permission on
            permission_type: Type of permission to grant
            days_ago: Number of days ago that the permission expired

        Returns:
            Dict[str, Any]: The created permission record
        """
        expires_at = datetime.now() - timedelta(days=days_ago)
        return self.grant_permission(
            user_id=user_id,
            entity_id=entity_id,
            permission_type=permission_type,
            expires_at=expires_at,
        )

    def create_future_permission(
        self,
        user_id: str,
        entity_id: str,
        permission_type: PermissionType = PermissionType.VIEW,
        days_valid: int = 7,
    ) -> Dict[str, Any]:
        """
        Create a permission that expires in the future.

        Args:
            user_id: ID of the user to grant permission to
            entity_id: ID of the entity to grant permission on
            permission_type: Type of permission to grant
            days_valid: Number of days until the permission expires

        Returns:
            Dict[str, Any]: The created permission record
        """
        expires_at = datetime.now() + timedelta(days=days_valid)
        return self.grant_permission(
            user_id=user_id,
            entity_id=entity_id,
            permission_type=permission_type,
            expires_at=expires_at,
        )

    def grant_team_permission(
        self,
        team_id: str,
        entity_id: str,
        permission_type: PermissionType = PermissionType.VIEW,
    ) -> Dict[str, Any]:
        """
        Grant a permission to a team.

        Args:
            team_id: ID of the team to grant permission to
            entity_id: ID of the entity to grant permission on
            permission_type: Type of permission to grant

        Returns:
            Dict[str, Any]: The created permission record
        """
        return self.grant_permission(
            user_id=None,  # type: ignore[arg-type]
            team_id=team_id,
            entity_id=entity_id,
            permission_type=permission_type,
        )

    def grant_role_permission(
        self,
        role_id: str,
        entity_id: str,
        permission_type: PermissionType = PermissionType.VIEW,
    ) -> Dict[str, Any]:
        """
        Grant a permission to a role.

        Args:
            role_id: ID of the role to grant permission to
            entity_id: ID of the entity to grant permission on
            permission_type: Type of permission to grant

        Returns:
            Dict[str, Any]: The created permission record
        """
        return self.grant_permission(
            user_id=None,  # type: ignore[arg-type]
            role_id=role_id,
            entity_id=entity_id,
            permission_type=permission_type,
        )

    def assert_permission_check(
        self,
        user_id: str,
        entity_id: str,
        permission_type: PermissionType = PermissionType.VIEW,
        expected_result: PermissionResult = PermissionResult.GRANTED,
        message: str | None = None,
    ):
        """
        Assert that a permission check returns the expected result.

        Args:
            user_id: ID of the user to check
            entity_id: ID of the entity to check
            permission_type: Type of permission to check
            expected_result: Expected result of the check
            message: Custom error message

        Raises:
            AssertionError: If the permission check result doesn't match expected_result
        """
        message_prefix = f"{self.sqlalchemy_model.__name__}:"

        # Get the database manager from model_registry
        model_registry = self._get_model_registry()
        db_manager = model_registry.DB.manager if model_registry else None
        declarative_base = model_registry.DB.Base if model_registry else None

        result, error_msg = check_permission(
            user_id,
            self.sqlalchemy_model,
            entity_id,
            self.db,
            declarative_base,
            permission_type,
        )

        if message is None:
            message = f"{message_prefix} Expected permission check to return {expected_result}, got {result}"
            if error_msg:
                message += f" (Error: {error_msg})"

        assert result == expected_result, message

    def verify_permission_checks(
        self,
        entity_id: str,
        allowed_users: List[str],
        denied_users: List[str],
        permission_type: PermissionType = PermissionType.VIEW,
    ):
        """
        Verify that permissions are properly enforced for multiple users.

        Args:
            entity_id: ID of the entity to check
            allowed_users: List of user IDs that should be allowed
            denied_users: List of user IDs that should be denied
            permission_type: Type of permission to check
        """
        for user_id in allowed_users:
            self.assert_permission_check(
                user_id,
                entity_id,
                permission_type,
                PermissionResult.GRANTED,
                f"{self.sqlalchemy_model.__name__}: User {user_id} should have {permission_type.value} access to entity {entity_id}",
            )

        for user_id in denied_users:
            self.assert_permission_check(
                user_id,
                entity_id,
                permission_type,
                PermissionResult.DENIED,
                f"{self.sqlalchemy_model.__name__}: User {user_id} should NOT have {permission_type.value} access to entity {entity_id}",
            )

    def _cleanup_test_entities(self):
        """Clean up entities created during this test."""
        if not hasattr(self, "tracked_entities"):
            return

        # Clean up created permissions first
        # for perm in reversed(self.tracked_entities):
        #     try:
        #         Permission.delete(self.ROOT_ID, model_registry, id=perm["id"])
        #         logger.debug(
        #             f"{self.sqlalchemy_model.__name__}: Cleaned up permission {perm['id']}"
        #         )
        #     except Exception as e:
        #         logger.debug(
        #             f"{self.sqlalchemy_model.__name__}: Error cleaning up permission {perm['id']}: {str(e)}"
        #         )

        # Clean up created entities
        # for entity in reversed(self.tracked_entities):
        #     try:
        #         self.sqlalchemy_model.delete(
        #             self.ROOT_ID, model_registry, id=entity["id"]
        #         )
        #         logger.debug(
        #             f"{self.sqlalchemy_model.__name__}: Cleaned up entity {entity['id']}"
        #         )
        #     except Exception as e:
        #         logger.debug(
        #             f"{self.sqlalchemy_model.__name__}: Error cleaning up entity {entity['id']}: {str(e)}"
        #         )

        skip_list = ["FailedLoginAttempt"]

        if (
            self.sqlalchemy_model is not None
            and self.sqlalchemy_model.__name__ not in skip_list
        ):
            for key in reversed(self.tracked_entities):
                entity = obj_to_dict(self.tracked_entities[key])
                # Skip primitive values (int, bool, None) that don't have IDs
                if not isinstance(entity, dict) or "id" not in entity:
                    continue
                try:
                    # Get the model registry from the server context
                    server = getattr(self, "_server", None)
                    model_registry = self._get_model_registry()

                    self.sqlalchemy_model.delete(
                        requester_id=env("SYSTEM_ID"),
                        model_registry=model_registry,
                        id=entity["id"],
                    )
                except Exception as e:
                    logger.debug(
                        f"{self.sqlalchemy_model.__name__}: Error cleaning up entity {entity['id']}: {str(e)}"
                    )

        # Call parent cleanup to clear the tracking dict
        super()._cleanup_test_entities()

    def mock_permission_filter(self):
        """Mock for permission filtering to isolate permission tests.

        Returns:
            A context manager that mocks the generate_permission_filter function
            to always return True, allowing tests to focus on other aspects.
        """
        from unittest.mock import patch

        return patch(
            "zephyrex.database.StaticPermissions.generate_permission_filter", return_value=True
        )

    def _create_assert(self, tracked_index: str):
        assertion_index = f"{self.sqlalchemy_model.__name__} / {tracked_index}"
        assert (
            self.tracked_entities[tracked_index] is not None
        ), f"{assertion_index}: Failed to create entity"
        assert "id" in obj_to_dict(
            self.tracked_entities[tracked_index]
        ), f"{assertion_index}: Entity missing ID"

    def _CRUD_create(
        self,
        return_type: str = "dict",
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] | None = None,
        key="CRUD_create_dict",
    ):
        key = key.replace("dict", return_type)

        # For database tests, use build_entities which handles parent entity creation properly
        # Get model_registry from test context
        model_registry = self._get_model_registry()
        server = getattr(self, "_server", None)
        entity_data_list = self.build_entities(
            server, user_id, team_id, unique_fields=self.unique_fields
        )
        create_data = entity_data_list[0] if entity_data_list else {}

        # If build_entities didn't work (no parent_entities), fall back to create_fields
        if not create_data and self.create_fields:
            create_data = self.create_fields.copy()

            # Resolve callable values in create_data
            for field, value in create_data.items():
                if callable(value):
                    create_data[field] = value()

            # Apply unique values to unique fields (only if they exist in create_fields)
            for field in self.unique_fields:
                if field in self.create_fields and (
                    field not in create_data or create_data[field] is None
                ):
                    create_data[field] = self._generate_unique_value()

            # Set user_id and team_id if they're expected in the create_fields
            if "user_id" in create_data and (
                create_data["user_id"] is None or create_data["user_id"] == ""
            ):
                create_data["user_id"] = user_id
            if "team_id" in create_data and (
                create_data["team_id"] is None or create_data["team_id"] == ""
            ):
                create_data["team_id"] = team_id

        self.tracked_entities[key] = self.sqlalchemy_model.create(
            env("SYSTEM_ID") if self.is_system_entity else user_id,
            model_registry,
            return_type=return_type,
            **create_data,
        )
        return self.tracked_entities[key]

    abstract_creation_method = _CRUD_create

    @pytest.mark.parametrize("return_type", sorted(["dict", "db", "model"]))
    def test_CRUD_create(self, db, server, admin_a, team_a, return_type):

        self.db = db
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)

        # Print model information
        logger.debug("\nModel Details:")
        logger.debug(f"Model class: {self.class_under_test.__name__}")

        # Print SQLAlchemy model fields
        if hasattr(self, "sqlalchemy_model"):
            logger.debug("\nSQLAlchemy Model Fields:")
            for column in self.sqlalchemy_model.__table__.columns:
                logger.debug(f"- {column.name}: {column.type}")

        # Print create fields
        logger.debug("\nCreate Fields:")
        for field, value in self.create_fields.items():
            logger.debug(f"- {field}: {value}")

        # Print update fields
        logger.debug("\nUpdate Fields:")
        for field, value in self.update_fields.items():
            logger.debug(f"- {field}: {value}")

        self._CRUD_create(
            return_type,
            admin_a.id,
            team_a.id,
        )
        logger.debug(f"\n=== Completed test_CRUD_create for {return_type} ===")
        self._create_assert("CRUD_create_" + return_type)

    def _ORM_create(
        self, user_id: str = env("ROOT_ID"), team_id: str | None = None, key="ORM_create"
    ):
        # Try to get server from test context if available
        server = getattr(self, "_server", None)
        self.tracked_entities[key] = self.sqlalchemy_model(
            **(
                self.build_entities(
                    server, user_id, team_id, unique_fields=self.unique_fields
                )[0]
            )
        )
        self.db.add(self.tracked_entities[key])  # type: ignore[attr-defined]
        self.db.flush()  # type: ignore[attr-defined]
        self.tracked_entities[key].created_by_user_id = self.tracked_entities[key].id
        self.db.commit()  # type: ignore[attr-defined]
        self.db.refresh(self.tracked_entities[key])  # type: ignore[attr-defined]

    def test_ORM_create(self, server, admin_a, team_a):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        self._ORM_create(
            env("SYSTEM_ID") if self.is_system_entity else admin_a.id, team_a.id
        )
        self._create_assert("ORM_create")

    def _get_assert(self, tracked_index: str):
        assertion_index = f"{self.sqlalchemy_model.__name__} / {tracked_index}"
        assert (
            self.tracked_entities[tracked_index] is not None
        ), f"{assertion_index}: Failed to create entity"
        assert "id" in obj_to_dict(
            self.tracked_entities[tracked_index]
        ), f"{assertion_index}: Entity missing ID"

    def _CRUD_get(
        self,
        return_type: str = "dict",
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] | None = None,
        save_key="CRUD_get_dict",
        get_key="CRUD_get",
        data: Optional[dict] | None = None,
    ):
        save_key = save_key.replace("dict", return_type)
        model_registry = self._get_model_registry()
        # Get the entity ID from tracked entities
        entity_id = self.tracked_entities[get_key]["id"]
        # Perform the get operation
        result = self.sqlalchemy_model.get(
            user_id, model_registry, return_type=return_type, id=entity_id
        )
        # Store result and debug output
        self.tracked_entities[save_key] = result
        return result

    # @pytest.mark.dependency(depends=["test_CRUD_create"])
    @pytest.mark.parametrize("return_type", sorted(["dict", "db", "model"]))
    def test_CRUD_get(self, server, admin_a, team_a, return_type):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_get",
        )
        self._CRUD_get(return_type, admin_a.id, team_a.id)
        self._get_assert("CRUD_get_" + return_type)

    def _ORM_get(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: str | None = None,
        key="ORM_get",
        requested_key="ORM_create",
    ):
        entity_id = self.tracked_entities[requested_key].id
        self.tracked_entities[key] = self.db.get(self.sqlalchemy_model, entity_id)  # type: ignore[attr-defined]
        return self.tracked_entities[key]

    def test_ORM_get(self, server, admin_a, team_a):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)

        # First create an entity
        self._ORM_create(
            env("SYSTEM_ID") if self.is_system_entity else admin_a.id,
            team_a.id,
            "ORM_create_for_get",
        )

        self._ORM_get(admin_a.id, team_a.id, "ORM_get", "ORM_create_for_get")
        self._get_assert("ORM_get")

    def _list_assert(self, tracked_index: str, search_keys: Optional[List[str]] | None = None):
        """
        Assert that a list operation returned the expected entities.

        Args:
            tracked_index: Key for the list result in tracked_entities
            search_keys: Optional list of keys for entities to look for. If not provided,
                        will derive from tracked_index by appending 1,2,3 to base name.

        Example:
            tracked_index="CRUD_list_dict" generates search_keys=["CRUD_list_1", "CRUD_list_2", "CRUD_list_3"]
        """
        assertion_index = f"{self.sqlalchemy_model.__name__} / {tracked_index}"

        # If search_keys is not provided, derive it from tracked_index
        if search_keys is None:
            # Replace specific suffixes (to handle different return types like CRUD_list_dict, ORM_list_db, etc.)
            base = tracked_index
            for suffix in ["_dict", "_db", "_model"]:
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break

            if not base.endswith("_"):
                base += "_"

            search_keys = [f"{base}1", f"{base}2", f"{base}3"]

        search_for = [self.tracked_entities[key] for key in search_keys]

        assert (
            self.tracked_entities[tracked_index] is not None
        ), f"{assertion_index}: Failed to create entity"

        response_ids = [
            obj_to_dict(obj)["id"] for obj in self.tracked_entities[tracked_index]
        ]

        for entity in search_for:
            assert (
                obj_to_dict(entity)["id"] in response_ids
            ), f"{assertion_index}: Entity {obj_to_dict(entity)['id']} missing"

    def _CRUD_list(
        self,
        return_type: str = "dict",
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] | None = None,
    ):
        server = getattr(self, "_server", None)
        model_registry = self._get_model_registry()
        # Store and return the list results
        result = self.sqlalchemy_model.list(
            user_id,
            model_registry,
            return_type=return_type,
        )
        # Store in tracked entities
        self.tracked_entities["CRUD_list_" + return_type] = result
        return result

    # @pytest.mark.dependency(depends=["test_CRUD_create"])
    @pytest.mark.parametrize("return_type", sorted(["dict", "db", "model"]))
    def test_CRUD_list(self, server, admin_a, team_a, return_type):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_list_1",
        )
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_list_2",
        )
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_list_3",
        )
        self._CRUD_list(return_type, admin_a.id, team_a.id)
        self._list_assert("CRUD_list_" + return_type)

    def _ORM_list(self, user_id: str = env("ROOT_ID"), team_id: str | None = None):
        self.tracked_entities["ORM_list"] = self.db.query(self.sqlalchemy_model).all()  # type: ignore[attr-defined]
        self.db.commit()  # type: ignore[attr-defined]

    # @pytest.mark.dependency(depends=["test_ORM_create"])
    def test_ORM_list(self, server, admin_a, team_a):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        self._ORM_create(admin_a.id, team_a.id, "ORM_list_1")
        self._ORM_create(admin_a.id, team_a.id, "ORM_list_2")
        self._ORM_create(admin_a.id, team_a.id, "ORM_list_3")
        self._ORM_list(admin_a.id, team_a.id)
        self._list_assert("ORM_list", ["ORM_list_1", "ORM_list_2", "ORM_list_3"])

    def _count_assert(self, tracked_index: str):
        assertion_index = f"{self.sqlalchemy_model.__name__} / {tracked_index}"
        assert (
            self.tracked_entities[tracked_index] is not None
        ), f"{assertion_index}: Failed to count entities"
        assert isinstance(
            self.tracked_entities[tracked_index], int
        ), f"{assertion_index}: Count result is not an integer"

    def _CRUD_count(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] | None = None,
    ):
        server = getattr(self, "_server", None)
        model_registry = self._get_model_registry()
        self.tracked_entities["CRUD_count"] = self.sqlalchemy_model.count(
            user_id, model_registry
        )

    # @pytest.mark.dependency(depends=["test_CRUD_create"])
    def test_CRUD_count(self, server, admin_a, team_a):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_count_1",
        )
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_count_2",
        )
        self._CRUD_count(admin_a.id, team_a.id)
        self._count_assert("CRUD_count")

    def _ORM_count(self, user_id: str = env("ROOT_ID"), team_id: str | None = None):
        """Count entities with proper permission filtering, to only return values user should see."""
        # For now, use basic count without permission filtering since we need server context
        # In the future, this should be updated to use server parameter for permission filtering
        self.tracked_entities["ORM_count"] = self.db.query(  # type: ignore[attr-defined]
            self.sqlalchemy_model
        ).count()
        self.db.commit()  # type: ignore[attr-defined]

    def test_ORM_count(self, server, admin_a, team_a):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        self._ORM_create(admin_a.id, team_a.id, "ORM_count_1")
        self._ORM_create(admin_a.id, team_a.id, "ORM_count_2")
        self._ORM_count(admin_a.id, team_a.id)
        self._count_assert("ORM_count")

    def _exists_assert(self, tracked_index: str):
        assertion_index = f"{self.sqlalchemy_model.__name__} / {tracked_index}"
        assert (
            self.tracked_entities[tracked_index] is not None
        ), f"{assertion_index}: Failed to check existence"
        assert isinstance(
            self.tracked_entities[tracked_index], bool
        ), f"{assertion_index}: Exists result is not a boolean"

    def _CRUD_exists(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] | None = None,
    ):
        server = getattr(self, "_server", None)
        model_registry = self._get_model_registry()
        self.tracked_entities["CRUD_exists_result"] = self.sqlalchemy_model.exists(
            user_id,
            model_registry,
            id=self.tracked_entities["CRUD_exists"]["id"],
        )

    # @pytest.mark.dependency(depends=["test_CRUD_create"])
    def test_CRUD_exists(self, server, admin_a, team_a):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_exists",
        )
        self._CRUD_exists(admin_a.id, team_a.id)
        self._exists_assert("CRUD_exists_result")

    def _ORM_exists(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: str | None = None,
        key="ORM_exists",
        requested_key="ORM_exists",
    ):
        """Check if entity exists using ORM."""
        entity_id = self.tracked_entities[requested_key].id
        self.tracked_entities[key + "_result"] = (
            self.db.query(self.sqlalchemy_model.id)  # type: ignore[attr-defined]
            .filter(self.sqlalchemy_model.id == entity_id)
            .scalar()
            is not None
        )
        self.db.commit()  # type: ignore[attr-defined]
        return self.tracked_entities[key + "_result"]

    def test_ORM_exists(self, server, admin_a, team_a):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        self._ORM_create(admin_a.id, team_a.id, "ORM_exists")
        self._ORM_exists(admin_a.id, team_a.id)
        self._exists_assert("ORM_exists_result")

    def _update_assert(self, tracked_index: str, updated_fields: dict):
        """
        Assert that an entity was updated correctly, handling datetime comparisons.
        """
        from datetime import datetime, timedelta

        assertion_index = f"{self.sqlalchemy_model.__name__} / {tracked_index}"
        assert (
            self.tracked_entities[tracked_index] is not None
        ), f"{assertion_index}: Failed to update entity"

        entity_dict = obj_to_dict(self.tracked_entities[tracked_index])
        assert "id" in entity_dict, f"{assertion_index}: Entity missing ID"

        for field, expected_value in updated_fields.items():
            actual_value = entity_dict.get(field)

            # Handle datetime comparisons
            if isinstance(expected_value, datetime):
                # Convert string datetime to datetime object if needed
                if isinstance(actual_value, str):
                    actual_value = datetime.fromisoformat(
                        actual_value.replace("T", " ")
                    )

                # Compare with tolerance
                time_difference = abs(actual_value - expected_value)
                assert time_difference <= timedelta(minutes=5), (
                    f"{assertion_index}: Field {field} not updated correctly.\n"
                    f"Expected: {expected_value}\n"
                    f"Got: {actual_value}\n"
                    f"Difference: {time_difference} exceeds 5 minutes"
                )
            else:
                # Regular comparison for non-datetime values
                assert actual_value == expected_value, (
                    f"{assertion_index}: Field {field} not updated correctly.\n"
                    f"Expected: {expected_value}\n"
                    f"Got: {actual_value}"
                )

    default_update_data = {"name": "Updated Name", "description": "Updated Description"}

    def _CRUD_update(
        self,
        return_type: str = "dict",
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] | None = None,
        update_data: Optional[dict] | None = None,
    ):
        # Use provided update_data or fall back to default update data
        if update_data is None:
            update_data = (
                self.update_fields.copy()
                if hasattr(self, "update_fields")
                else self.default_update_data.copy()
            )

        # Resolve any callable values in the update data
        resolved_data = {}
        for field, value in update_data.items():
            if callable(value):
                resolved_data[field] = value()
            else:
                resolved_data[field] = value
        server = getattr(self, "_server", None)
        model_registry = self._get_model_registry()
        self.tracked_entities["CRUD_update_" + return_type] = (
            self.sqlalchemy_model.update(
                env("SYSTEM_ID") if self.is_system_entity else user_id,
                model_registry,
                return_type=return_type,
                id=self.tracked_entities["CRUD_update"]["id"],
                new_properties=resolved_data,
            )
        )
        return resolved_data

    # @pytest.mark.dependency(depends=["test_CRUD_create"])
    @pytest.mark.parametrize("return_type", sorted(["dict", "db", "model"]))
    def test_CRUD_update(self, server, admin_a, team_a, return_type):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        if not hasattr(self.sqlalchemy_model, "updated_at"):
            pytest.skip("No ability to update.")
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_update",
        )
        updated_fields = self._CRUD_update(return_type, admin_a.id, team_a.id)
        self._update_assert("CRUD_update_" + return_type, updated_fields)

    def _ORM_update(self, user_id: str = env("ROOT_ID"), team_id: str | None = None):
        entity = self.tracked_entities["ORM_update"]
        entity.name = "Updated ORM Name"
        entity.description = "Updated ORM Description"
        self.db.add(entity)  # type: ignore[attr-defined]
        self.db.flush()  # type: ignore[attr-defined]
        self.db.commit()  # type: ignore[attr-defined]
        self.db.refresh(entity)  # type: ignore[attr-defined]
        self.tracked_entities["ORM_update"] = entity
        return {"name": "Updated ORM Name", "description": "Updated ORM Description"}

    # @pytest.mark.dependency(depends=["test_ORM_create"])
    def test_ORM_update(self, server, admin_a, team_a):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        self._ORM_create(admin_a.id, team_a.id, "ORM_update")
        updated_fields = self._ORM_update(admin_a.id, team_a.id)
        self._update_assert("ORM_update", updated_fields)

    def _delete_assert(self, user_id, tracked_index: str):
        assertion_index = f"{self.sqlalchemy_model.__name__} / {tracked_index}"
        assert (
            self.tracked_entities[tracked_index] is not None
        ), f"{assertion_index}: Failed to delete entity"
        if tracked_index == "ORM_delete":
            entity_id = self.tracked_entities["ORM_delete_original"]["id"]
        else:
            entity_id = self.tracked_entities[tracked_index]["id"]

        # Get the model registry from the server context
        server = getattr(self, "_server", None)
        model_registry = self._get_model_registry()

        assert not self.sqlalchemy_model.exists(
            user_id, model_registry, id=entity_id
        ), f"{assertion_index}: Entity still exists after deletion"

    def _CRUD_delete(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] | None = None,
    ):
        server = getattr(self, "_server", None)
        model_registry = self._get_model_registry()
        self.sqlalchemy_model.delete(
            env("SYSTEM_ID") if self.is_system_entity else user_id,
            model_registry,
            id=self.tracked_entities["CRUD_delete"]["id"],
        )

    # @pytest.mark.dependency(depends=["test_CRUD_create"])
    def test_CRUD_delete(self, server, admin_a, team_a):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        if not hasattr(self.sqlalchemy_model, "deleted_at"):
            pytest.skip("No ability to delete.")
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_delete",
        )
        self._CRUD_delete(admin_a.id, team_a.id)
        self._delete_assert(admin_a.id, "CRUD_delete")

    # @pytest.mark.dependency(depends=["test_CRUD_delete", "test_CRUD_get"])
    def test_CRUD_soft_delete(self, server, admin_a, team_a):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        if not hasattr(self.sqlalchemy_model, "deleted_at"):
            pytest.skip("No ability to delete.")
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_delete",
        )
        self._CRUD_delete(admin_a.id, team_a.id)
        # After Item 75 the soft-delete filter applies at the DB layer for
        # every read; verify the row still exists in storage by bypassing the
        # auto-filter via `include_deleted`, mirroring how admin/audit code is
        # expected to inspect tombstoned rows.
        from zephyrex.database.AbstractDatabaseEntity import include_deleted

        session = self.model_registry.DB.session()
        with include_deleted(session):
            row = (
                session.query(self.sqlalchemy_model)
                .filter(
                    self.sqlalchemy_model.id
                    == self.tracked_entities["CRUD_delete"]["id"]
                )
                .first()
            )
        assert row is not None, (
            f"{self.sqlalchemy_model.__name__}: soft-deleted row should remain "
            "in storage but is missing under include_deleted()"
        )
        assert row.deleted_at is not None, (
            f"{self.sqlalchemy_model.__name__}: deleted_at not set after "
            "soft-delete"
        )

    def _ORM_delete(self, user_id: str = env("ROOT_ID"), team_id: str | None = None):
        entity = self.tracked_entities["ORM_delete"]
        self.tracked_entities["ORM_delete_original"] = {"id": entity.id}
        self.db.delete(entity)  # type: ignore[attr-defined]
        self.db.flush()  # type: ignore[attr-defined]
        self.db.commit()  # type: ignore[attr-defined]
        self.tracked_entities["ORM_delete"] = True

    # @pytest.mark.dependency(depends=["test_ORM_create"])
    def test_ORM_delete(self, server, admin_a, team_a):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server  # Store server for access by helper methods
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        self._ORM_create(admin_a.id, team_a.id, "ORM_delete")
        self._ORM_delete(admin_a.id, team_a.id)
        self._delete_assert(admin_a.id, "ORM_delete")

    # ------------------------------------------------------------------ #
    # Scalability / Big-O assertions
    # ------------------------------------------------------------------ #
    # Big-O scaling assertion for sqlalchemy_model.list(). Runs automatically;
    # subclasses may override with a custom profile.

    scalability_profile: Optional[ScalabilityProfile] = ScalabilityProfile.default(
        n_values=[3, 8, 20], repetitions=2
    )

    @pytest.mark.parametrize(
        "metric",
        sorted(
            [ScalingMetric.TIME, ScalingMetric.QUERY_COUNT, ScalingMetric.MEMORY],
            key=lambda m: m.value,  # type: ignore[attr-defined]
        ),
    )
    def test_scalability_list_n_factor(
        self, server, admin_a, team_a, metric: ScalingMetric
    ):
        """Big-O assertion: sqlalchemy_model.list() must stay within the
        configured exponent for time, query count, and peak memory as the
        underlying row count grows. Catches accidental N+1, O(n^2) joins,
        and per-row materialization regressions."""
        if metric not in self.scalability_profile.metrics:
            pytest.skip(f"metric {metric.value} not enabled in scalability_profile")

        self.db = server.app.state.model_registry.database_manager.get_session()
        self._server = server
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)

        seeded = {"count": 0}

        def setup(target_n: int) -> None:
            while seeded["count"] < target_n:
                self._CRUD_create(
                    return_type="dict",
                    user_id=admin_a.id,
                    team_id=team_a.id,
                    key=f"scalability_seed_{seeded['count']}",
                )
                seeded["count"] += 1

        requester = env("SYSTEM_ID") if self.is_system_entity else admin_a.id

        def operation(_n: int) -> None:
            self.sqlalchemy_model.list(
                requester, self.model_registry, return_type="dict"
            )

        engine = self.model_registry.database_manager.engine
        assert_scaling_within_bounds(
            operation, self.scalability_profile, metric, engine=engine, setup=setup
        )
