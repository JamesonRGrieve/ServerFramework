import inspect
import random
import string
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import Any, Callable, ClassVar, Dict, List, Optional, Type

import pytest
from faker import Faker
from pydantic import BaseModel, Field

from lib.Environment import env
from lib.Logging import logger
from lib.Pydantic import obj_to_dict


class ParentEntity(BaseModel):
    """Model for parent entity configuration"""

    name: str
    foreign_key: str
    path_level: Optional[int] = None  # 1 for first level nesting, 2 for second level
    is_path: bool = False  # Whether this parent is used in URL paths
    test_class: Any
    nullable: bool = False

    @property
    def path_key(self) -> str:
        """Convenience property for the path parameter key used in endpoints.

        For a parent with name 'team' this returns 'team_id', which is the
        expected path parameter name used throughout the tests and routing
        helpers.
        """
        return f"{self.name}_id"


class CategoryOfTest(str, Enum):
    """Categories of tests for organization and selective execution."""

    UNIT = "unit"
    DATABASE = "database"
    LOGIC = "business_logic"
    ENDPOINT = "endpoint"
    REST = "rest"
    GRAPHQL = "graphql"
    PERFORMANCE = "performance"
    INTEGRATION = "integration"
    SMOKE = "smoke"
    REGRESSION = "regression"
    SECURITY = "security"
    SEED = "seed_data"
    EXTENSION = "extension"
    SDK = "sdk"
    MIGRATION = "migration"


class SkipReason(str, Enum):
    IRRELEVANT = "irrelevant"
    NOT_IMPLEMENTED = "not_implemented"


class SkipThisTest(BaseModel):
    """Model for a skipped test with a reason."""

    name: str = Field(..., description="Name of the test method to skip")
    reason: SkipReason = Field(
        SkipReason.IRRELEVANT, description="Reason for skipping the test"
    )
    details: str = Field(None, description="Additional details about the test to skip")
    gh_issue_number: Optional[int] = Field(
        None, description="Optional GitHub ticket reference number"
    )


class ClassOfTestsConfig(BaseModel):
    """Configuration for test execution and behavior."""

    categories: List[CategoryOfTest] = Field(
        default=[CategoryOfTest.UNIT], description="Categories this test belongs to"
    )
    timeout: Optional[int] = Field(
        None, description="Optional timeout in seconds for tests in this class"
    )
    parallel: bool = Field(
        False, description="Whether tests in this class can be run in parallel"
    )
    cleanup: bool = Field(
        True, description="Whether to clean up resources after each test"
    )
    gh_action_skip: bool = Field(
        False,
        description="Whether to skip these tests in GitHub action CI/CD environments",
    )


class AbstractTest:
    """
    Base class for all abstract test suites in the application.

    Provides common utilities for test organization, categorization,
    skipping logic, and other shared test functionality.

    Features:
    - Test categorization
    - Test skipping with reasons
    - Test configuration
    - Common assertion utilities
    - Required fixture documentation
    - Automatic SQLAlchemy model creation for database tests

    To use this class, extend it and override the class attributes as needed.

    Available centralized fixtures (defined in conftest.py):
    - db: Session-wide database fixture
    - db: Database session for testing
    - standard_user_ids: Dictionary of standard user IDs
    - standard_team_ids: Dictionary of standard team IDs
    - standard_role_ids: Dictionary of standard role IDs
    - standard_users: Dictionary of standard user objects
    - standard_teams: Dictionary of standard team objects
    - requester_id, test_user_id, test_team_id: Commonly used test IDs
    - seed_database: Fixture to seed database with common test data
    - server: TestClient instance for endpoint testing
    - bll_test_data_generator, bll_test_validator: Helpers for BLL tests
    - create_test_entity: Helper for creating test entities
    """

    class_under_test: Type = None
    debug: bool = False
    # Tests to skip - List of SkippedTest objects, should be overridden by subclasses
    _skip_tests: List[SkipThisTest] = []
    __skip_lookup__: ClassVar[Dict[str, SkipThisTest]] = {}

    # Test configuration - should be overridden by subclasses if needed
    test_config: ClassOfTestsConfig = ClassOfTestsConfig()

    # Create a faker instance for generating test data
    faker = Faker()
    create_fields: Dict[str, Any] = None
    update_fields: Dict[str, Any] = None
    unique_fields: List[str] = []
    parent_entities: List[ParentEntity] = []  # List of ParentEntity objects
    # A dict of entities to clean up
    tracked_entities: Dict[str, Any] = {}
    abstract_creation_method: Callable

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._refresh_skip_lookup()
        cls._wrap_test_methods()

    @classmethod
    def _refresh_skip_lookup(cls) -> None:
        cls.__skip_lookup__ = {}
        for base in reversed(cls.__mro__):
            for skip in getattr(base, "_skip_tests", ()):
                cls.__skip_lookup__[skip.name] = skip

    @classmethod
    def _wrap_test_methods(cls) -> None:
        """Automatically add skip handling to all pytest test methods."""

        for name, attr in list(cls.__dict__.items()):
            if not name.startswith("test_"):
                continue

            # Only wrap standard instance methods to avoid descriptor issues
            if isinstance(attr, (staticmethod, classmethod)):
                continue

            if not callable(attr):
                continue

            if getattr(attr, "__skip_wrapper__", False):
                continue

            setattr(cls, name, cls._wrap_instance_test_method(attr))

    @staticmethod
    def _wrap_instance_test_method(func: Callable) -> Callable:
        """Wrap a test method to apply skip logic before execution."""

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(self, *args, **kwargs):
                self.reason_to_skip(func.__name__)
                return await func(self, *args, **kwargs)

            async_wrapper.__skip_wrapper__ = True
            return async_wrapper

        @wraps(func)
        def wrapper(self, *args, **kwargs):
            self.reason_to_skip(func.__name__)
            return func(self, *args, **kwargs)

        wrapper.__skip_wrapper__ = True
        return wrapper

    def ensure_model(self, server):
        """
        Ensure the appropriate model is created based on test category.

        For DATABASE tests, creates the SQLAlchemy model using the server's database manager.
        For other test types, returns the appropriate model class.

        Args:
            server: TestClient from the server fixture (required for DATABASE tests)
        """
        if CategoryOfTest.DATABASE in self.test_config.categories:
            # For database tests, create SQLAlchemy model if not already created
            if not hasattr(self, "sqlalchemy_model") or self.sqlalchemy_model is None:
                if server is None:
                    pytest.fail(
                        f"{self.__class__.__name__}: server fixture is required for database tests"
                    )

                if not hasattr(self.class_under_test, "DB"):
                    pytest.fail(
                        f"Model {self.class_under_test.__name__} does not have .DB method - ensure it inherits from DatabaseMixin"
                    )

                # Create SQLAlchemy model using the server's database manager
                self.sqlalchemy_model = self.class_under_test.DB(
                    server.app.state.model_registry.database_manager.Base
                )

                # Validate required fields are set
                assert (
                    self.sqlalchemy_model is not None
                ), f"{self.__class__.__name__}: sqlalchemy_model must be defined"
                assert (
                    self.create_fields is not None
                ), f"{self.__class__.__name__}: create_fields must be defined"
                assert (
                    self.update_fields is not None
                ), f"{self.__class__.__name__}: update_fields must be defined"

            return self.sqlalchemy_model
        else:
            # For non-database tests, return the model class
            return self.model_class

    @property
    def skip_tests(self) -> List[SkipThisTest]:
        """
        Get the combined list of tests to skip from this class and all parent classes.

        This property allows child classes to add their own skip_tests while inheriting
        skip_tests from parent classes. The inheritance chain is traversed to collect
        all _skip_tests attributes.

        Returns:
            List[SkipThisTest]: Combined list of tests to skip
        """
        return list(self.__class__.__skip_lookup__.values())

    def _generate_unique_value(self, prefix: str = "Test") -> str:
        """Generate a unique value for the entity being tested."""
        return f"{prefix} {self.faker.word().capitalize()} {self.faker.random_int(min=1000, max=9999)}"

    def _get_unique_entity_data(self, **kwargs) -> Dict[str, Any]:
        """
        Generate unique entity data for testing.

        Combines the create_fields from the class with any provided overrides.
        Ensures that the unique_fields have unique values if not already provided.

        Args:
            **kwargs: Field overrides for the entity data

        Returns:
            Dict with field values for entity creation
        """
        data = self.create_fields.copy() if self.create_fields else {}

        # Evaluate any callable fields
        for field, value in data.items():
            if callable(value):
                data[field] = value()

        # Apply unique value to unique fields if not provided
        for field in self.unique_fields:
            if field not in kwargs and field not in data:
                data[field] = self._generate_unique_value()

        # Apply kwargs as overrides
        data.update(kwargs)

        return data

    def build_entities(
        self,
        server,
        user_id: str = "",
        team_id: str = "",
        count=1,
        unique_fields: List[str] = None,
    ):
        entities = []
        unique_fields = unique_fields or []

        for i in range(count):
            entity_data = self.create_fields.copy()
            for field in self.create_fields:
                if callable(self.create_fields[field]):
                    entity_data[field] = self.create_fields[field]()
            # Handle multiple unique fields
            for field in unique_fields:
                if field in self.create_fields:
                    base_value = entity_data[field]
                    if field == "email":
                        random_part = "".join(
                            random.choices(string.ascii_lowercase + string.digits, k=8)
                        )
                        timestamp = datetime.now().strftime("%H%M%S%f")
                        entity_data[field] = (
                            f"test_{random_part}_{timestamp}@example.com"
                        )
                    elif field == "username":
                        random_part = "".join(
                            random.choices(string.ascii_lowercase + string.digits, k=8)
                        )
                        entity_data[field] = f"{base_value}_{random_part}_{i}"
                    else:
                        # Generic uniqueness for other fields
                        entity_data[field] = f"{base_value}-{i}"

            if user_id and "user_id" in entity_data:
                entity_data["user_id"] = user_id
            if team_id and "team_id" in entity_data:
                entity_data["team_id"] = team_id
            for parent in self.parent_entities:
                # Handle both callable test_class (lambda) and direct class reference
                if callable(parent.test_class):
                    parent_entity_test_class = parent.test_class()
                else:
                    parent_entity_test_class = parent.test_class

                # Create an instance if we have a class, or use the instance if it's already instantiated
                if isinstance(parent_entity_test_class, type):
                    # It's a class, so instantiate it
                    parent_instance = parent_entity_test_class()
                else:
                    # It's already an instance, use it directly
                    parent_instance = parent_entity_test_class

                # Set up the parent instance with necessary attributes
                if hasattr(self, "db"):
                    parent_instance.db = self.db
                parent_instance.tracked_entities = {}  # Initialize tracked entities

                # Determine the actual server to use
                server_to_use = (
                    getattr(self, "_server", None)
                    or getattr(self, "server", None)
                    or server
                )

                # Check if server_to_use is a fixture definition rather than actual server
                is_fixture_definition = (
                    server_to_use is not None
                    and hasattr(server_to_use, "__name__")
                    and "fixture" in str(type(server_to_use))
                )

                # Set server attributes on parent instance if we have a valid server
                if server_to_use is not None and not is_fixture_definition:
                    parent_instance.server = server_to_use
                    parent_instance._server = server_to_use

                if hasattr(self, "model_registry") and self.model_registry is not None:
                    parent_instance.model_registry = self.model_registry

                # Ensure the parent instance has its model set up if it's a database test
                if (
                    hasattr(parent_instance, "ensure_model")
                    and not is_fixture_definition
                ):
                    if server_to_use is not None:
                        parent_instance.ensure_model(server_to_use)

                # Call the creation method with the appropriate parameters based on the method signature
                import inspect

                creation_method = parent_instance.abstract_creation_method
                sig = inspect.signature(creation_method)

                # Build kwargs based on what the method accepts
                kwargs = {
                    "user_id": user_id,
                    "team_id": team_id,
                    "key": f"parent_{parent.name}_{id(parent_instance)}_{i}",  # Add index for uniqueness
                }

                # Only include parameters that the method actually accepts
                filtered_kwargs = {}
                for param_name, param_value in kwargs.items():
                    if param_name in sig.parameters:
                        filtered_kwargs[param_name] = param_value

                # Add return_type if the method accepts it (DB tests)
                if "return_type" in sig.parameters:
                    filtered_kwargs["return_type"] = "dict"

                # Pass the current database session if available to avoid session conflicts
                if hasattr(self, "db") and self.db is not None:
                    parent_instance.db = self.db

                # Pass server if available and the method accepts it
                if (
                    server_to_use is not None
                    and not is_fixture_definition
                    and "server" in sig.parameters
                ):
                    filtered_kwargs["server"] = server_to_use

                # Pass model_registry if available and the method accepts it (for DB tests)
                if (
                    hasattr(self, "model_registry")
                    and self.model_registry is not None
                    and "model_registry" in sig.parameters
                ):
                    filtered_kwargs["model_registry"] = self.model_registry

                # Pass db_manager if available and the method accepts it (for BLL tests)
                if (
                    server_to_use is not None
                    and not is_fixture_definition
                    and "db_manager" in sig.parameters
                ):
                    filtered_kwargs["db_manager"] = (
                        server_to_use.app.state.model_registry.database_manager
                    )

                new_parent_entity = creation_method(**filtered_kwargs)

                # Handle both dictionary and model objects
                entity_data[parent.foreign_key] = (
                    new_parent_entity.id
                    if hasattr(new_parent_entity, "id")
                    else new_parent_entity["id"]
                )
            entities.append(entity_data)
        return entities

    @property
    def model_class(self) -> BaseModel:
        if CategoryOfTest.DATABASE in self.test_config.categories:
            return self.class_under_test
        elif CategoryOfTest.LOGIC in self.test_config.categories:
            return self.class_under_test.Model
        elif CategoryOfTest.ENDPOINT in self.test_config.categories:
            return self.class_under_test.manager_class.Model
        else:
            return None

    @property
    def is_system_entity(self) -> bool:
        """
        Check if the test entity is a system entity.
        """
        if (
            CategoryOfTest.DATABASE in self.test_config.categories
            or CategoryOfTest.LOGIC in self.test_config.categories
            or CategoryOfTest.ENDPOINT in self.test_config.categories
        ):
            model = self.model_class
            if model is None:
                return False

            # For DATABASE tests, model_class returns SQLAlchemy model which has 'system' attribute
            if CategoryOfTest.DATABASE in self.test_config.categories:
                return getattr(model, "system", False)
            # For LOGIC and ENDPOINT tests, model_class returns Pydantic model which has 'is_system_entity' attribute
            else:
                return getattr(model, "is_system_entity", False)
        else:
            return False

    def reason_to_skip(self, test_name: str) -> Optional[SkipReason]:
        """
        Check if a specific test method should be skipped based on the skip_tests list of the enclosing class.

        Args:
            test_name: The name of the test method.

        Returns:
            True if the test should be skipped, False otherwise. If True, pytest.skip()
            will be called with the reason.
        """
        if skip := self.__class__.__skip_lookup__.get(test_name):
            reason = skip.details + (
                (f" (GitHub: {env('APP_REPOSITORY')}/issues/{skip.gh_issue_number})")
                if skip.gh_issue_number
                else ""
            )
            (
                pytest.skip(reason)
                if skip.reason == SkipReason.IRRELEVANT
                else pytest.xfail(reason)
            )

    def _cleanup_test_entities(self):
        """
        Clean up entities created during this test.

        This is a generic cleanup method that can be overridden by subclasses
        for specialized cleanup (e.g., database-specific cleanup).
        """
        # Base implementation just clears the tracking dict
        # Subclasses can override this for more complex cleanup
        if hasattr(self, "tracked_entities"):
            self.tracked_entities = {}

    @classmethod
    def setup_class(cls):
        """
        Set up resources for the entire test class.

        This method is called once before any tests in the class are run.
        Override in subclasses to set up shared resources.
        """
        logger.debug(f"Setting up test class: {cls.__name__}")

        # Apply timeout if configured
        if cls.test_config.timeout:
            pytest.mark.timeout(cls.test_config.timeout)

        # Skip in CI if configured
        if cls.test_config.gh_action_skip:
            pytest.mark.skipif(
                "env('ENVIRONMENT') == 'ci'",
                reason="Test configured to skip in CI environment",
            )

    @classmethod
    def teardown_class(cls):
        """
        Clean up resources for the entire test class.

        This method is called once after all tests in the class have run.
        Override in subclasses to clean up shared resources.
        """
        logger.debug(f"Tearing down test class: {cls.__name__}")

    def setup_method(self, method):
        """
        Set up resources for each test method.

        This method is called before each test method in the class.
        Override in subclasses to set up test-specific resources.

        Args:
            method: The test method that will be executed
        """
        logger.debug(f"Setting up method: {method.__name__}")

        # Check if test should be skipped
        self.reason_to_skip(method.__name__)

        # Reset tracked entities for this test
        self.tracked_entities = {}

    def teardown_method(self, method):
        """
        Clean up resources for each test method.

        This method is called after each test method in the class.
        Override in subclasses to clean up test-specific resources.

        Args:
            method: The test method that was executed
        """
        if self.test_config.cleanup:
            logger.debug(f"Tearing down method: {method.__name__}")
            # Clean up any entities created during this test
            self._cleanup_test_entities()

        # Clean up any database connections that might be lingering
        try:
            if hasattr(self, "db") and self.db:
                self.db.close()
        except Exception as e:
            logger.debug(f"Error closing database session: {e}")

        # Clean up any database manager thread-local resources
        try:
            from database.DatabaseManager import DatabaseManager

            # Try to cleanup the singleton instance if it exists
            try:
                singleton_instance = DatabaseManager.get_instance()
                if singleton_instance and hasattr(singleton_instance, "cleanup_thread"):
                    singleton_instance.cleanup_thread()
            except Exception:
                pass  # Singleton might not exist

            # Also try to cleanup any database manager accessible through test fixtures
            # This is important for isolated test instances
            if hasattr(self, "_db_manager_cleanup_list"):
                for db_manager in self._db_manager_cleanup_list:
                    if hasattr(db_manager, "cleanup_thread"):
                        db_manager.cleanup_thread()
        except Exception as e:
            logger.debug(f"Error cleaning up database manager: {e}")

    # Common assertion methods
    def assert_objects_equal(
        self, actual: Any, expected: Any, fields_to_check: List[str] = None
    ):
        """
        Assert that two objects have equal values for specified fields.

        Args:
            actual: The actual object or dictionary
            expected: The expected object or dictionary
            fields_to_check: Optional list of fields to check, if None checks all fields in expected
        """

        # Convert objects to dictionaries if they're not already
        if not isinstance(actual, dict):
            actual = obj_to_dict(actual)

        if not isinstance(expected, dict):
            expected = obj_to_dict(expected)

        # Determine which fields to check
        if fields_to_check is None:
            fields_to_check = expected.keys()

        # Check each field
        for field in fields_to_check:
            if field in expected:
                assert field in actual, f"Field '{field}' missing from actual object"
                assert actual[field] == expected[field], (
                    f"Field '{field}' value mismatch: "
                    f"expected {expected[field]}, got {actual[field]}"
                )

    def assert_has_audit_fields(self, obj: Any, updated: bool = False):
        """
        Assert that an object has the required audit fields.

        Args:
            obj: The object or dictionary to check
            updated: Whether to check update audit fields too
        """

        # Convert object to dictionary if it's not already
        obj = obj_to_dict(obj)

        # Check created fields
        assert (
            "created_at" in obj and obj["created_at"] is not None
        ), "created_at missing or None"
        assert (
            "created_by_user_id" in obj and obj["created_by_user_id"] is not None
        ), "created_by_user_id missing or None"

        # Check updated fields if requested
        if updated:
            assert (
                "updated_at" in obj and obj["updated_at"] is not None
            ), "updated_at missing or None"
            assert (
                "updated_by_user_id" in obj and obj["updated_by_user_id"] is not None
            ), "updated_by_user_id missing or None"

    @staticmethod
    def verify_permissions(
        manager, operation: str, entity_id: str, should_succeed: bool, **kwargs
    ):
        """
        Verify that a permission check works as expected.

        Args:
            manager: The BLL manager instance
            operation: The operation to test ('get', 'update', 'delete')
            entity_id: The entity ID to operate on
            should_succeed: Whether the operation should succeed
            **kwargs: Additional arguments for the operation
        """
        try:
            if operation == "get":
                result = manager.get(id=entity_id)
            elif operation == "update":
                result = manager.update(id=entity_id, **kwargs)
            elif operation == "delete":
                manager.delete(id=entity_id)
                result = None
            else:
                raise ValueError(f"Unknown operation: {operation}")

            # If we expected failure but got success
            if not should_succeed:
                raise AssertionError(
                    f"Operation '{operation}' succeeded but should have failed"
                )
            return result

        except Exception as e:
            # If we expected success but got failure
            if should_succeed:
                raise AssertionError(
                    f"Operation '{operation}' failed but should have succeeded: {e}"
                )
            # Expected failure - all good
            return None

    # @classmethod
    # def create_test_entities(
    #     cls,
    #     manager,
    #     count: int,
    #     data_generator,
    #     field_overrides: Optional[Dict[str, Any]] = None,
    # ) -> List[Any]:
    #     entities = []
    #     for i in range(count):
    #         if field_overrides and callable(field_overrides):
    #             overrides = field_overrides(i)
    #         elif field_overrides:
    #             overrides = field_overrides.copy()
    #             # If there's a field that should be unique per entity
    #             if "name" in overrides:
    #                 overrides["name"] = f"{overrides['name']} {i}"
    #         else:
    #             overrides = {
    #                 "name": f"{cls.faker.word().capitalize()} {cls.faker.random_int(min=1000, max=9999)}"
    #             }

    #         entity_data = data_generator.generate_for_model(
    #             manager.Model.Create, overrides=overrides
    #         )
    #         entity = manager.create(**entity_data)
    #         entities.append(entity)

    #     return entities

    @staticmethod
    def validate_required_fields(
        entity: Dict[str, Any], required_fields: List[str]
    ) -> List[str]:
        """
        Validate that all required fields are present in an entity.

        Args:
            entity: The entity to validate
            required_fields: List of required field names

        Returns:
            List of missing field names (empty if all present)
        """
        missing_fields = []
        for field in required_fields:
            if field not in entity or entity[field] is None:
                missing_fields.append(field)
        return missing_fields

    @staticmethod
    def validate_entity_matches(
        entity: Dict[str, Any],
        expected_data: Dict[str, Any],
        fields_to_check: Optional[List[str]] = None,
    ) -> List[str]:
        """
        Validate that entity fields match expected values.

        Args:
            entity: The entity to validate
            expected_data: Dictionary of expected field values
            fields_to_check: Optional list of specific fields to check (if None, checks all fields in expected_data)

        Returns:
            List of mismatched field names (empty if all match)
        """
        mismatched_fields = []

        # Determine which fields to check
        fields = fields_to_check if fields_to_check else expected_data.keys()

        for field in fields:
            if field in expected_data:
                if field not in entity:
                    mismatched_fields.append(field)
                elif entity[field] != expected_data[field]:
                    mismatched_fields.append(field)

        return mismatched_fields

    @staticmethod
    def validate_audit_fields(
        entity: Dict[str, Any],
        created_by: Optional[str] = None,
        updated_by: Optional[str] = None,
    ) -> List[str]:
        """
        Validate audit fields in an entity.

        Args:
            entity: The entity to validate
            created_by: Expected user ID for created_by_user_id field
            updated_by: Expected user ID for updated_by_user_id field

        Returns:
            List of invalid audit field names (empty if all valid)
        """
        invalid_fields = []

        # Check created_at and created_by_user_id
        if "created_at" not in entity or not entity["created_at"]:
            invalid_fields.append("created_at")

        if created_by and (
            "created_by_user_id" not in entity
            or entity["created_by_user_id"] != created_by
        ):
            invalid_fields.append("created_by_user_id")

        # Check updated_at and updated_by_user_id if provided
        if updated_by:
            if "updated_at" not in entity or not entity["updated_at"]:
                invalid_fields.append("updated_at")

            if (
                "updated_by_user_id" not in entity
                or entity["updated_by_user_id"] != updated_by
            ):
                invalid_fields.append("updated_by_user_id")

        return invalid_fields
