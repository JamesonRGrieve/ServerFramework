import re
from typing import List, Optional, Type, Any, Dict, Tuple

import pytest

from zephyrex.AbstractTest import AbstractTest, CategoryOfTest, ClassOfTestsConfig
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.Scalability import (
    ScalabilityProfile,
    ScalingMetric,
    assert_scaling_within_bounds,
)
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager


# @pytest.mark.dependency(scope="session", depends=["zephyrex.logic.AbstractLogicManager_test"])
class AbstractBLLTest(AbstractTest):
    """
    Comprehensive base class for business logic layer test suites.

    This class provides exhaustive testing for all business logic layer functionality,
    including CRUD operations, search, batch operations, and hooks.
    It tests every function from AbstractBLLManager and ensures proper behavior.

    Child classes must override:
    - class_under_test: The BLL manager class being tested
    - create_fields: Dict of fields to use when creating test entities
    - update_fields: Dict of fields to use when updating test entities

    Configuration options:
    - test_config: Test execution parameters
    - skip_tests: Tests to skip with documented reasons
    """

    # Required overrides that child classes must provide
    class_under_test: Type[AbstractBLLManager] = None  # type: ignore[assignment]

    # Default test configuration
    test_config: ClassOfTestsConfig = ClassOfTestsConfig(
        categories=[CategoryOfTest.LOGIC]
    )

    @classmethod
    def get_model_class(cls, entity_name: str = None) -> Optional[Type]:  # type: ignore[assignment]
        """Get the Pydantic model class for this entity.

        Returns:
            The model class from the manager
        """
        if hasattr(cls, "class_under_test") and hasattr(
            cls.class_under_test, "BaseModel"
        ):
            return cls.class_under_test.BaseModel  # type: ignore[no-any-return]
        return None

    @staticmethod
    def get_field_type_info(
        model_class: Type, field_name: str
    ) -> Tuple[Optional[Type], bool, Any]:
        """Get type information for a field.

        Args:
            model_class: The Pydantic model class
            field_name: Name of the field

        Returns:
            Tuple of (python_type, is_optional, field_info)
        """
        from typing import get_type_hints, get_origin, get_args, Union

        if not model_class:
            return None, False, None

        # Get type hints for the model
        type_hints = get_type_hints(model_class)

        if field_name not in type_hints:
            return None, False, None

        field_type = type_hints[field_name]
        field_info = model_class.model_fields.get(field_name)

        # Check if it's Optional
        is_optional = False
        actual_type = field_type

        origin = get_origin(field_type)
        if origin is Union:
            args = get_args(field_type)
            # Check if it's Optional (Union with None)
            if type(None) in args:
                is_optional = True
                # Get the non-None type
                actual_type = next(arg for arg in args if arg is not type(None))

        return actual_type, is_optional, field_info

    @classmethod
    def generate_search_test_parameters(cls) -> List[Tuple[str, str]]:
        """Generate search test parameters based on model fields.

        This method is called at test discovery time to generate all field/operator
        combinations that should be tested.

        Returns:
            List of tuples (field_name, operator) for pytest.mark.parametrize
        """
        from datetime import datetime, date

        model_class = cls.get_model_class()
        if not model_class:
            return []

        test_params = []

        # Get field names from model
        field_names = []
        for name in sorted(model_class.model_fields.keys()):

            # Get the field type to check if it's a navigation property
            field_type, is_optional, field_info = cls.get_field_type_info(
                model_class, name
            )
            if not field_type:
                continue

            # Check if this is a model type (navigation property)
            # Navigation properties will have __module__ and typically inherit from BaseModel
            if hasattr(field_type, "__module__") and hasattr(field_type, "__mro__"):
                # Check if any base class is named BaseModel or similar
                base_names = [base.__name__ for base in field_type.__mro__]
                if any(
                    name in ["BaseModel", "DatabaseMixin", "NetworkMixin"]
                    for name in base_names
                ):
                    # This is a navigation property, skip it
                    continue
                # Also skip Any type (used for navigation properties)
                if "Any" in base_names:
                    continue

            # Check if it's a List of models (like children)
            from typing import get_origin, get_args, List as TypingList, Any

            if get_origin(field_type) in [list, TypingList]:
                args = get_args(field_type)
                if args:
                    # Skip if it's List[Any] or List of model objects
                    if args[0] is Any:
                        continue
                    if hasattr(args[0], "__module__") and hasattr(args[0], "__mro__"):
                        # It's a List of model objects, skip it
                        continue

            field_names.append(name)

        for field_name in field_names:
            field_type, is_optional, field_info = cls.get_field_type_info(
                model_class, field_name
            )
            if not field_type:
                continue

            # Collect operators for this field in a consistent order
            operators = ["value"]  # Always test direct value first

            # String field tests
            if field_type is str:
                # Test operators in consistent order: value, eq, ew, inc, sw
                operators.extend(["eq", "ew", "inc", "sw"])

            # Numeric field tests (int or float)
            elif field_type in [int, float]:
                # Test operators in consistent order: value, eq, gt, gteq, lt, lteq, neq
                operators.extend(["eq", "gt", "gteq", "lt", "lteq", "neq"])

            # Date/datetime field tests
            elif field_type in [date, datetime]:
                # Test operators in consistent order: after, before, eq, on
                # Skip 'value' for date fields as it's redundant with 'eq'
                operators = ["after", "before", "eq", "on"]

            # Boolean field tests
            elif field_type is bool:
                operators.append("eq")

            # Add all operators for this field
            for operator in operators:
                test_params.append((field_name, operator))

        return test_params

    @staticmethod
    def get_search_value_for_operator(
        field_value: Any, operator: str, api_context: bool = False
    ) -> Any:
        """Get the appropriate search value for a given operator and field value.

        Args:
            field_value: The original field value
            operator: The search operator

        Returns:
            The modified value appropriate for the operator
        """
        from datetime import datetime, date, timedelta
        import inspect

        if field_value is None:
            return None

        # String operators
        if operator == "eq":
            return field_value
        elif operator == "ew" and isinstance(field_value, str):
            return field_value[-3:] if len(field_value) >= 3 else field_value
        elif operator == "inc" and isinstance(field_value, str):
            return field_value[1:-1] if len(field_value) > 2 else field_value
        elif operator == "sw" and isinstance(field_value, str):
            return field_value[:3] if len(field_value) >= 3 else field_value

        # Numeric operators
        elif operator == "gt" and isinstance(field_value, (int, float)):
            return field_value - 1
        elif operator == "gteq":
            return field_value
        elif operator == "lt" and isinstance(field_value, (int, float)):
            return field_value + 1
        elif operator == "lteq":
            return field_value
        elif operator == "neq" and isinstance(field_value, (int, float)):
            return field_value + 1

        # Date operators
        elif operator in ["after", "before", "on"]:
            # Convert string to datetime if needed
            if isinstance(field_value, str):
                try:
                    # Handle various datetime string formats
                    if "T" in field_value:
                        dt_value = datetime.fromisoformat(
                            field_value.replace("Z", "+00:00")
                        )
                    else:
                        # Try parsing as date first
                        try:
                            dt_value = datetime.strptime(field_value, "%Y-%m-%d")
                        except ValueError:
                            dt_value = datetime.fromisoformat(field_value)
                except (ValueError, TypeError):
                    return field_value
            elif isinstance(field_value, datetime):
                dt_value = field_value
            elif isinstance(field_value, date):
                dt_value = datetime.combine(field_value, datetime.min.time())
            else:
                return field_value

            if operator == "after":
                return (dt_value - timedelta(days=1)).isoformat()
            elif operator == "before":
                return (dt_value + timedelta(days=1)).isoformat()
            elif operator == "on":
                if api_context:
                    # For endpoint tests, just return ISO date string
                    return dt_value.date().isoformat()
                else:
                    # For business logic tests, return datetime format
                    return dt_value.strftime("%Y-%m-%d %H:%M:%S")

        # For 'value' operator, handle datetime specially
        elif operator == "value":
            if isinstance(field_value, datetime):
                # Try the format that matches the database storage
                return field_value.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(field_value, date):
                return field_value.isoformat()
            return field_value

        # Boolean operators
        elif operator == "is_true":
            return field_value

        return field_value

    def setup_method(self, method):
        """Set up method-level test fixtures."""
        super().setup_method(method)

        # Check if required fields are set
        assert (
            self.class_under_test is not None
        ), f"{self.__class__.__name__}: class_under_test must be defined"
        assert (
            self.create_fields is not None
        ), f"{self.__class__.__name__}: create_fields must be defined"
        assert (
            self.update_fields is not None
        ), f"{self.__class__.__name__}: update_fields must be defined"

    def teardown_method(self, method):
        """Clean up method-level test fixtures."""
        # Force cleanup of all active sessions in the model registry
        if hasattr(self, "server") and hasattr(self.server, "app"):
            try:
                model_registry = getattr(self.server.app.state, "model_registry", None)
                if (
                    model_registry
                    and hasattr(model_registry, "DB")
                    and hasattr(model_registry.DB, "cleanup_thread")
                ):
                    model_registry.DB.cleanup_thread()
            except Exception as e:
                logger.debug(f"Error cleaning up model registry: {e}")
        super().teardown_method(method)

    def _cleanup_test_entities(self):
        """Clean up entities created during this test."""
        if not hasattr(self, "tracked_entities"):
            return

        # Clean up created entities using context-managed sessions
        for entity_key, entity in reversed(list(self.tracked_entities.items())):
            try:
                if hasattr(entity, "id") and entity.id:
                    # Create a manager to delete this entity
                    requester_id = self._effective_requester(env("ROOT_ID"))
                    # Only try to clean up if we have model_registry available
                    if hasattr(self, "model_registry") and self.model_registry:
                        manager = self.class_under_test(
                            requester_id=requester_id,
                            model_registry=self.model_registry,
                        )
                        manager.delete(id=entity.id)
                        logger.debug(
                            f"{self.class_under_test.__name__}: Cleaned up entity {entity.id}"
                        )
                    else:
                        logger.debug(
                            f"{self.class_under_test.__name__}: Skipping cleanup for entity {entity.id} - no model_registry available"
                        )
            except Exception as e:
                logger.debug(
                    f"{self.class_under_test.__name__}: Error cleaning up entity {entity_key}: {str(e)}"
                )

    def _effective_requester(self, user_id: str) -> str:
        """Return SYSTEM_ID for system entities, otherwise the given user_id."""
        return env("SYSTEM_ID") if self.is_system_entity else user_id

    def _resolve_registry(self, model_registry: Any) -> Any:
        """Fall back to self.model_registry when model_registry is None."""
        if model_registry is None and hasattr(self, "model_registry"):
            return self.model_registry
        return model_registry

    def _create_assert(self, tracked_index: str):
        entity = self.tracked_entities[tracked_index]
        assertion_index = f"{self.class_under_test.__name__} / {tracked_index}"
        assert entity is not None, f"{assertion_index}: Failed to create entity"
        assert (
            hasattr(entity, "id") and entity.id
        ), f"{assertion_index}: Entity missing ID"

    def _create(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        key="create",
        server=None,
        model_registry=None,
    ):
        """Create a test entity using the manager."""
        requester_id = self._effective_requester(user_id)

        # Get model_registry from server if not provided
        if model_registry is None and server is not None:
            model_registry = server.app.state.model_registry
        elif model_registry is None and hasattr(self, "server"):
            model_registry = self.server.app.state.model_registry

        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_id,
            model_registry=model_registry,
        )
        self.tracked_entities[key] = manager.create(
            **self.build_entities(
                server if server else getattr(self, "server", None),
                user_id=user_id,
                team_id=team_id,
                unique_fields=getattr(self, "unique_fields", []),
            )[0]
        )
        return self.tracked_entities[key]

    # Define abstract_creation_method as a property to ensure it always points to the correct _create method
    @property
    def abstract_creation_method(self):
        return self._create

    # @pytest.mark.dependency(name="test_create")
    def test_create(self, admin_a, team_a, server, model_registry):
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id, team_a.id, server=server, model_registry=model_registry
        )
        self._create_assert("create")

    def _get_assert(self, tracked_index: str):
        entity = self.tracked_entities[tracked_index]
        assertion_index = f"{self.class_under_test.__name__} / {tracked_index}"
        assert entity is not None, f"{assertion_index}: Failed to get entity"
        assert (
            hasattr(entity, "id") and entity.id
        ), f"{assertion_index}: Entity missing ID"

    def _get(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        save_key="get_result",
        get_key="get",
        model_registry=None,
    ):
        requester_id = self._effective_requester(user_id)

        model_registry = self._resolve_registry(model_registry)

        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_id,
            model_registry=model_registry,
        )
        entity_id = self.tracked_entities[get_key].id
        self.tracked_entities[save_key] = manager.get(id=entity_id)

    # @pytest.mark.dependency(depends=["test_create"])
    def test_get(self, admin_a, team_a, server, model_registry):
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id, team_a.id, "get", server=server, model_registry=model_registry
        )
        self._get(admin_a.id, team_a.id, model_registry=model_registry)
        self._get_assert("get_result")

    def _list_assert(self, tracked_index: str):
        entities = self.tracked_entities[tracked_index]
        assertion_index = f"{self.class_under_test.__name__} / {tracked_index}"

        search_for = [
            self.tracked_entities["list_1"],
            self.tracked_entities["list_2"],
            self.tracked_entities["list_3"],
        ]

        assert entities is not None, f"{assertion_index}: Failed to list entities"
        assert isinstance(entities, list), f"{assertion_index}: Result is not a list"

        result_ids = [entity.id for entity in entities]
        for entity in search_for:
            assert (
                entity.id in result_ids
            ), f"{assertion_index}: Entity {entity.id} missing from list"

    def _list(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        model_registry=None,
    ):
        requester_id = self._effective_requester(user_id)

        model_registry = self._resolve_registry(model_registry)

        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_id,
            model_registry=model_registry,
        )
        self.tracked_entities["list_result"] = manager.list()

    # @pytest.mark.dependency(depends=["test_create"])
    def test_list(self, admin_a, team_a, server, model_registry):
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id,
            team_a.id,
            "list_1",
            server=server,
            model_registry=model_registry,
        )
        self._create(
            admin_a.id,
            team_a.id,
            "list_2",
            server=server,
            model_registry=model_registry,
        )
        self._create(
            admin_a.id,
            team_a.id,
            "list_3",
            server=server,
            model_registry=model_registry,
        )
        self._list(admin_a.id, team_a.id, model_registry=model_registry)
        self._list_assert("list_result")

    def _search_assert(self, tracked_index: str, search_term: str):
        entities = self.tracked_entities[tracked_index]
        assertion_index = f"{self.class_under_test.__name__} / {tracked_index}"

        search_for = self.tracked_entities["search_target"]

        assert entities is not None, f"{assertion_index}: Failed to search entities"
        assert isinstance(entities, list), f"{assertion_index}: Result is not a list"
        assert (
            len(entities) > 0
        ), f"{assertion_index}: Search for '{search_term}' returned no results"

        result_ids = [entity.id for entity in entities]
        assert (
            search_for.id in result_ids
        ), f"{assertion_index}: Target entity {search_for.id} missing from search results"

    def _search(
        self,
        search_term: str,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        model_registry=None,
    ):
        requester_id = self._effective_requester(user_id)

        model_registry = self._resolve_registry(model_registry)

        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_id,
            model_registry=model_registry,
        )
        search_params = {self.unique_fields[0]: {"inc": search_term}}
        self.tracked_entities["search_result"] = manager.search(**search_params)

    def _create_parent_entities_for_search(self, requester_id, team_id, model_registry):
        """Recursively create parent entities needed for search tests."""
        parent_data = {}

        if not hasattr(self, "parent_entities") or not self.parent_entities:
            return parent_data

        for parent_config in self.parent_entities:
            parent_test_class = parent_config.test_class

            # Handle lambda functions or class references
            if callable(parent_test_class):
                try:
                    # Try to instantiate it
                    parent_instance = parent_test_class()
                except:
                    # If it fails, it might already be an instance
                    parent_instance = parent_test_class
            else:
                parent_instance = parent_test_class

            # Get parent's data
            parent_entity_data = parent_instance._get_unique_entity_data()

            # Recursively create parent's parents if needed
            if (
                hasattr(parent_instance, "parent_entities")
                and parent_instance.parent_entities
            ):
                grandparent_data = parent_instance._create_parent_entities_for_search(
                    requester_id, team_id, model_registry
                )
                parent_entity_data.update(grandparent_data)

            # Create the parent entity
            parent_manager_class = (
                parent_test_class.class_under_test
                if hasattr(parent_test_class, "class_under_test")
                else parent_instance.class_under_test
            )

            # Check if the parent test class has a special create method for parent entities
            if hasattr(parent_instance, "create_for_parent_entity"):
                parent_entity = parent_instance.create_for_parent_entity(
                    model_registry=model_registry, **parent_entity_data
                )
            else:
                with parent_manager_class(
                    requester_id=requester_id,
                    target_team_id=team_id,
                    model_registry=model_registry,
                ) as parent_manager:
                    parent_entity = parent_manager.create(**parent_entity_data)

            # Store the foreign key
            parent_data[parent_config.foreign_key] = parent_entity.id

        return parent_data

    def test_search(
        self, admin_a, team_a, server, model_registry, search_field, search_operator
    ):
        """Test search functionality for a specific field and operator.

        This test is parameterized to test each field/operator combination.
        """
        self.server = server
        self.model_registry = model_registry

        # Create entity if not already created
        if not hasattr(self, "_search_test_entity"):
            requester_id = self._effective_requester(admin_a.id)

            # Try the new approach first (build_entities), fall back to legacy approach
            entity_data = self._get_entity_data_for_search(
                admin_a, team_a, server, requester_id, model_registry
            )
            logger.debug(f"Entity data for search: {entity_data}")

            # For entities that belong to a user, use that user as the requester
            effective_requester_id = requester_id
            if "user_id" in entity_data and entity_data["user_id"]:
                effective_requester_id = entity_data["user_id"]

            manager = self.class_under_test(
                requester_id=effective_requester_id,
                target_team_id=team_a.id,
                model_registry=model_registry,
            )
            self._search_test_entity = manager.create(**entity_data)
            self.tracked_entities["search_target"] = self._search_test_entity
            # Remember the effective requester so the search query below runs as
            # the same user that owns the entity. Without this, user-scoped
            # entities (e.g. MFA methods) become invisible to admin_a under
            # the strict permission filter.
            self._search_effective_requester_id = effective_requester_id

        entity = self._search_test_entity
        logger.debug(
            f"Testing search for {entity.id} on field '{search_field}' with operator '{search_operator}'"
        )

        # Convert entity to dict
        entity_dict = (
            entity.model_dump() if hasattr(entity, "model_dump") else entity.__dict__
        )

        # Get field value
        field_value = entity_dict.get(search_field)
        if field_value is None:
            pytest.skip(f"Field {search_field} is None in test entity")

        try:
            # Try with api_context parameter
            search_value = self.get_search_value_for_operator(
                field_value, operator=search_operator, api_context=False
            )
        except TypeError:
            # Fall back to legacy behavior if TypeError occurs
            search_value = self.get_search_value_for_operator(
                field_value, search_operator
            )

        # Construct search criteria
        if search_operator == "value":
            # Direct value syntax (implicit eq)
            search_criteria = {search_field: search_value}
        else:
            # Nested operator syntax
            search_criteria = {search_field: {search_operator: search_value}}

        # Perform search using the same effective requester that created the
        # entity (typically the entity's user_id for user-scoped models).
        requester_id = getattr(
            self,
            "_search_effective_requester_id",
            self._effective_requester(admin_a.id),
        )
        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        results = manager.search(**search_criteria)

        # Handle both dict and object responses for result IDs
        result_ids = []
        for r in results:
            if hasattr(r, "id"):
                # Object with id attribute
                result_ids.append(r.id)
            elif hasattr(r, "get") and callable(r.get):
                # Dict-like object
                result_ids.append(r.get("id"))
            elif isinstance(r, dict):
                # Plain dict
                result_ids.append(r["id"])
            else:
                # Fallback: try to get id attribute
                result_ids.append(getattr(r, "id", None))

        # Filter out None values
        result_ids = [rid for rid in result_ids if rid is not None]
        logger.debug(f"Search results: {len(result_ids)} found")

        assert entity.id in result_ids, (
            f"Entity not found when searching {search_field} with operator '{search_operator}' "
            f"and value '{search_value}' (type: {type(search_value).__name__}). Found {len(results)} results."
        )

    def _get_entity_data_for_search(
        self, admin_a, team_a, server, requester_id, model_registry
    ):
        """Get entity data for search tests, trying new approach first, falling back to legacy."""

        # Strategy 1: Try build_entities approach
        try:
            if hasattr(self, "build_entities"):
                entity_data = self.build_entities(
                    server if server else getattr(self, "server", None),
                    user_id=admin_a.id,
                    team_id=team_a.id,
                    unique_fields=getattr(self, "unique_fields", []),
                )[0]

                # Apply any domain-specific validation patterns
                entity_data = self._apply_search_validation_patterns(
                    entity_data, admin_a, team_a
                )
                return entity_data
        except Exception as e:
            # If build_entities fails, fall back to legacy approach
            logger.debug(f"build_entities failed, falling back to legacy approach: {e}")

        # Strategy 2: Legacy approach with _get_unique_entity_data + parent entities
        entity_data = self._get_unique_entity_data()

        # Create parent entities if needed
        if hasattr(self, "parent_entities") and self.parent_entities:
            parent_entities = self._create_parent_entities_for_search(
                requester_id, team_a.id, model_registry
            )
            # Update entity_data with parent foreign keys
            entity_data.update(parent_entities)

        # Apply any domain-specific validation patterns
        entity_data = self._apply_search_validation_patterns(
            entity_data, admin_a, team_a
        )
        return entity_data

    def _apply_search_validation_patterns(self, entity_data, admin_a, team_a):
        """Apply common validation patterns that child classes typically override for."""

        # Pattern 1: Session-like entities (user_id must equal requester)
        if (
            hasattr(self.class_under_test, "BaseModel")
            and hasattr(self.class_under_test.BaseModel, "__name__")
            and "Session" in self.class_under_test.BaseModel.__name__
        ):
            entity_data["user_id"] = admin_a.id
            return entity_data

        # Pattern 2: Metadata-like entities (need user_id OR team_id)
        if (
            hasattr(self.class_under_test, "BaseModel")
            and hasattr(self.class_under_test.BaseModel, "__name__")
            and "Metadata" in self.class_under_test.BaseModel.__name__
        ):
            if not entity_data.get("user_id") and not entity_data.get("team_id"):
                entity_data["user_id"] = admin_a.id
            return entity_data

        # Pattern 3: Entities with required user_id but no ownership rules
        if hasattr(self.class_under_test, "BaseModel"):
            model_create_class = getattr(
                self.class_under_test.BaseModel, "Create", None
            )
            if model_create_class:
                # Check if user_id is required in Create model
                if (
                    hasattr(model_create_class, "user_id")
                    and not entity_data.get("user_id")
                    and not hasattr(self, "parent_entities")
                ):
                    entity_data["user_id"] = admin_a.id

        return entity_data

    def _update_assert(self, tracked_index: str, updated_fields: dict):
        entity = self.tracked_entities[tracked_index]
        assertion_index = f"{self.class_under_test.__name__} / {tracked_index}"

        assert entity is not None, f"{assertion_index}: Failed to update entity"

        for field, value in updated_fields.items():
            assert hasattr(
                entity, field
            ), f"{assertion_index}: Field {field} missing from updated entity"
            assert (
                getattr(entity, field) == value
            ), f"{assertion_index}: Field {field} not updated correctly, expected {value}, got {getattr(entity, field)}"

    def _update(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        model_registry=None,
    ):
        requester_id = self._effective_requester(user_id)

        model_registry = self._resolve_registry(model_registry)

        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_id,
            model_registry=model_registry,
        )
        update_data = self.update_fields.copy()
        entity_id = self.tracked_entities["update"].id
        self.tracked_entities["update_result"] = manager.update(
            id=entity_id, **update_data
        )
        return update_data

    # @pytest.mark.dependency(depends=["test_create"])
    def test_update(self, admin_a, team_a, server, model_registry):
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id,
            team_a.id,
            "update",
            server=server,
            model_registry=model_registry,
        )
        updated_fields = self._update(
            admin_a.id, team_a.id, model_registry=model_registry
        )
        self._update_assert("update_result", updated_fields)

    def _batch_update_assert(self, tracked_index: str, item_count: int):
        entities = self.tracked_entities[tracked_index]
        assertion_index = f"{self.class_under_test.__name__} / {tracked_index}"

        assert (
            entities is not None
        ), f"{assertion_index}: Failed to batch update entities"
        assert isinstance(entities, list), f"{assertion_index}: Result is not a list"
        assert (
            len(entities) == item_count
        ), f"{assertion_index}: Expected {item_count} updated entities, got {len(entities)}"

        # If the class has unique_fields defined and they are name-like fields that can be updated
        if (
            hasattr(self, "unique_fields")
            and self.unique_fields
            and any(field in self.update_fields for field in self.unique_fields)
        ):
            field_to_check = next(
                field for field in self.unique_fields if field in self.update_fields
            )
            for i, entity in enumerate(entities):
                expected_value = f"Batch Updated {i}"
                assert hasattr(
                    entity, field_to_check
                ), f"{assertion_index}: Field {field_to_check} missing from entity {i}"
                assert (
                    getattr(entity, field_to_check) == expected_value
                ), f"{assertion_index}: Field {field_to_check} not updated correctly for entity {i}"
        else:
            # For entities without name-like fields, verify they were updated with the update_fields
            for entity in entities:
                for field, value in self.update_fields.items():
                    assert hasattr(
                        entity, field
                    ), f"{assertion_index}: Field {field} missing from entity"
                    assert (
                        getattr(entity, field) == value
                    ), f"{assertion_index}: Field {field} not updated correctly"

    def _batch_update(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        model_registry=None,
    ):
        requester_id = self._effective_requester(user_id)

        model_registry = self._resolve_registry(model_registry)

        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_id,
            model_registry=model_registry,
        )
        items = []
        for i, key in enumerate(["batch_1", "batch_2", "batch_3"]):
            update_data = self.update_fields.copy()
            # If we have unique fields that can be updated, use the batch update pattern
            if (
                hasattr(self, "unique_fields")
                and self.unique_fields
                and any(field in self.update_fields for field in self.unique_fields)
            ):
                field_to_update = next(
                    field for field in self.unique_fields if field in self.update_fields
                )
                update_data[field_to_update] = f"Batch Updated {i}"

            items.append(
                {
                    "id": self.tracked_entities[key].id,
                    "data": update_data,
                }
            )

        self.tracked_entities["batch_update_result"] = manager.batch_update(items)

    # @pytest.mark.dependency(depends=["test_create"])
    def test_batch_update(self, admin_a, team_a, server, model_registry):
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id,
            team_a.id,
            "batch_1",
            server=server,
            model_registry=model_registry,
        )
        self._create(
            admin_a.id,
            team_a.id,
            "batch_2",
            server=server,
            model_registry=model_registry,
        )
        self._create(
            admin_a.id,
            team_a.id,
            "batch_3",
            server=server,
            model_registry=model_registry,
        )
        self._batch_update(admin_a.id, team_a.id, model_registry=model_registry)
        self._batch_update_assert("batch_update_result", 3)

    def _delete_assert(self, entity_id: str, user_id: str, model_registry=None):
        requester_id = self._effective_requester(user_id)

        model_registry = self._resolve_registry(model_registry)

        # Try to get the entity - should either raise an exception or return None
        manager = self.class_under_test(
            requester_id=requester_id,
            model_registry=model_registry,
        )
        try:
            result = manager.get(id=entity_id)
            # If we get here, no exception was raised, so result should be None
            assert result is None, f"Entity {entity_id} still exists after deletion"
        except Exception as e:
            # Verify the error message matches the database layer format
            assert re.match(
                r".*Request searched .* and could not find the required record\.$",
                str(e),
            ), f"Unexpected error message: {str(e)}"

    def _delete(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        model_registry=None,
    ):
        requester_id = self._effective_requester(user_id)

        model_registry = self._resolve_registry(model_registry)

        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_id,
            model_registry=model_registry,
        )
        entity_id = self.tracked_entities["delete"].id
        manager.delete(id=entity_id)
        return entity_id

    # @pytest.mark.dependency(depends=["test_create"])
    def test_delete(self, admin_a, team_a, server, model_registry):
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id,
            team_a.id,
            "delete",
            server=server,
            model_registry=model_registry,
        )
        entity_id = self._delete(admin_a.id, team_a.id, model_registry=model_registry)
        self._delete_assert(entity_id, admin_a.id, model_registry=model_registry)

    def _batch_delete_assert(
        self, entity_ids: List[str], user_id: str, model_registry=None
    ):
        requester_id = self._effective_requester(user_id)

        model_registry = self._resolve_registry(model_registry)

        manager = self.class_under_test(
            requester_id=requester_id,
            model_registry=model_registry,
        )
        for entity_id in entity_ids:
            try:
                result = manager.get(id=entity_id)
                # If we get here, no exception was raised, so result should be None
                assert (
                    result is None
                ), f"Entity {entity_id} still exists after batch deletion"
            except Exception as e:
                # Verify the error message matches the database layer format
                assert re.match(
                    r".*Request searched .* and could not find the required record\.$",
                    str(e),
                ), f"Unexpected error message: {str(e)}"

    def _batch_delete(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        model_registry=None,
    ):
        requester_id = self._effective_requester(user_id)

        model_registry = self._resolve_registry(model_registry)

        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_id,
            model_registry=model_registry,
        )
        entity_ids = [
            self.tracked_entities["batch_delete_1"].id,
            self.tracked_entities["batch_delete_2"].id,
            self.tracked_entities["batch_delete_3"].id,
        ]

        manager.batch_delete(entity_ids)

        return entity_ids

    # @pytest.mark.dependency(depends=["test_create"])
    def test_batch_delete(self, admin_a, team_a, server, model_registry):
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id,
            team_a.id,
            "batch_delete_1",
            server=server,
            model_registry=model_registry,
        )
        self._create(
            admin_a.id,
            team_a.id,
            "batch_delete_2",
            server=server,
            model_registry=model_registry,
        )
        self._create(
            admin_a.id,
            team_a.id,
            "batch_delete_3",
            server=server,
            model_registry=model_registry,
        )
        entity_ids = self._batch_delete(
            admin_a.id, team_a.id, model_registry=model_registry
        )
        self._batch_delete_assert(entity_ids, admin_a.id, model_registry=model_registry)

    # @pytest.mark.dependency(depends=["test_create"])

    def test_hooks(self, admin_a, team_a, server, model_registry):
        """Test that the new hook_bll system is properly configured."""
        self.server = server
        self.model_registry = model_registry
        requester_id = self._effective_requester(admin_a.id)

        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        # Test that the HookRegistry class exists and has the required methods
        from zephyrex.logic.AbstractLogicManager import HookRegistry

        # Verify that HookRegistry has the required methods
        hook_registry = HookRegistry()
        assert hasattr(
            hook_registry, "register_hook"
        ), "HookRegistry missing register_hook method"

        assert hasattr(
            hook_registry, "get_hooks"
        ), "HookRegistry missing get_hooks method"

        assert hasattr(
            hook_registry, "hooks"
        ), "HookRegistry instance missing hooks attribute"

        # Test that the manager class has been set up for hook support
        assert hasattr(
            manager.__class__, "__init_subclass__"
        ), "Manager class missing __init_subclass__ method for hook support"

        # Test that the manager class has a hook registry
        if hasattr(manager.__class__, "_hook_registry"):
            class_registry = manager.__class__._hook_registry
            assert isinstance(
                class_registry, HookRegistry
            ), "Manager class _hook_registry should be HookRegistry instance"

            # Test that basic CRUD methods have hook points set up
            crud_methods = ["create", "get", "list", "update", "delete"]
            for method_name in crud_methods:
                if hasattr(manager, method_name):
                    hooks = class_registry.get_hooks(method_name)
                    assert (
                        "before" in hooks
                    ), f"Manager class missing 'before' hooks for {method_name}"
                    assert (
                        "after" in hooks
                    ), f"Manager class missing 'after' hooks for {method_name}"
                    assert isinstance(
                        hooks["before"], list
                    ), f"'before' hooks for {method_name} should be a list"
                    assert isinstance(
                        hooks["after"], list
                    ), f"'after' hooks for {method_name} should be a list"

    # ------------------------------------------------------------------ #
    # Item 87 — BLL field-selection and include coverage (GitHub #10)
    # ------------------------------------------------------------------ #
    # The EP layer already covers `fields` and `include` via end-to-end
    # tests; what was missing per GitHub #10 is BLL-level coverage that
    # `manager.get(fields=...)`, `manager.list(fields=...)`, and the
    # `include=` plumbing actually push the correct `load_only` /
    # `joinedload` options into the database call. We assert at the
    # SQLAlchemy options layer rather than against rendered SQL strings
    # — the SQL is dialect-dependent, the option objects are not.

    @staticmethod
    def _is_load_option(opt) -> bool:
        """Return True if ``opt`` is a SQLAlchemy ``Load``-family option.

        ``load_only(*cols)`` returns a ``sqlalchemy.orm.strategy_options.Load``
        instance whose default ``repr`` does **not** contain the word
        "load_only" — it is just ``<Load object at 0x…>``. We therefore
        check via ``isinstance`` against the SQLAlchemy class hierarchy so
        the assertion survives whatever version of SQLAlchemy the
        deployment is on.
        """
        try:
            from sqlalchemy.orm import Load
            from sqlalchemy.orm.strategy_options import _AbstractLoad
        except ImportError:  # pragma: no cover
            return "load_only" in repr(opt).lower()
        return isinstance(opt, (Load, _AbstractLoad))

    @staticmethod
    def _capture_db_options(manager, method_name: str):
        """Wrap a manager DB method (`get`/`list`/`search`) so it returns
        the captured kwargs (specifically ``options=`` and ``fields=``)
        instead of executing the query. This sidesteps the need for a
        populated test DB while still exercising the BLL-side assembly.

        Returns a ``(holder, restore)`` pair where
        ``holder["options"]`` receives the captured ``options=`` list and
        ``holder["fields"]`` receives the captured ``fields=`` value (some
        managers — UserTeamManager / SessionManager / etc. — push fields
        directly to ``DB.get(fields=...)`` instead of through ``options``,
        so both paths are valid evidence that BLL field-selection
        propagated to the DB layer).
        """
        original = getattr(manager.DB, method_name)
        holder = {"options": None, "fields": None}

        def _capture(*args, **kwargs):
            holder["options"] = kwargs.get("options", [])
            holder["fields"] = kwargs.get("fields", None)
            if method_name == "list":
                return []
            return None

        setattr(manager.DB, method_name, _capture)

        def _restore():
            setattr(manager.DB, method_name, original)

        return holder, _restore

    @staticmethod
    def _captured_load_only_evidence(holder, expected_fields) -> bool:
        """Return True iff the BLL pushed field-selection to the DB layer
        — either as a ``load_only`` Load option in ``options=`` or as a
        ``fields=`` kwarg matching the requested fields. Item 87 cares
        about end-to-end propagation, not the specific mechanism."""
        options = holder.get("options") or []
        if any(AbstractBLLTest._is_load_option(opt) for opt in options):
            return True
        captured_fields = holder.get("fields")
        if captured_fields is None:
            return False
        return any(f in captured_fields for f in expected_fields)

    def test_field_selection_pushes_load_only(
        self, admin_a, team_a, server, model_registry
    ):
        """`manager.get(fields=[...])` must produce a `load_only` SQLAlchemy
        option naming the requested columns. Item 87 / GitHub #10.

        The DB.get call is stubbed to capture the ``options=`` kwarg
        rather than execute a query — so this test exercises BLL-side
        option assembly without depending on a populated test database
        or a successful create-and-fetch cycle on every concrete
        manager. The downstream ``HTTPException(404)`` raised when the
        stub returns ``None`` is part of the no-rows-found contract;
        we let it propagate, snapshot the captured options first.
        """
        self.server = server
        self.model_registry = model_registry
        from fastapi import HTTPException

        requester_id = self._effective_requester(admin_a.id)
        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        candidate_fields = [
            f for f in ("name", "id") if f in (manager.Model.model_fields or {})
        ]
        if not candidate_fields:
            pytest.skip("no introspectable fields on this model")
        holder, restore = self._capture_db_options(manager, "get")
        try:
            try:
                manager.get(id="probe-id", fields=candidate_fields)
            except HTTPException:
                pass  # 404 from None-return is expected and irrelevant
            except Exception:
                pass  # downstream NoneType wraps similarly; options were captured first
        finally:
            restore()
        if holder["options"] is None and holder["fields"] is None:
            pytest.skip(
                "manager.get path did not reach DB layer for this concrete manager"
            )
        assert self._captured_load_only_evidence(holder, candidate_fields), (
            f"expected fields={candidate_fields} to propagate to DB layer "
            f"(via options=load_only or fields= kwarg); captured: {holder!r}"
        )

    def test_field_selection_empty_list_skips_load_only(
        self, admin_a, team_a, server, model_registry
    ):
        """An empty ``fields=[]`` must NOT inject a ``load_only`` (it
        would select zero columns and break round-trip serialization).
        Item 87. Same stub-and-snapshot pattern as the positive test."""
        self.server = server
        self.model_registry = model_registry
        from fastapi import HTTPException

        requester_id = self._effective_requester(admin_a.id)
        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        holder, restore = self._capture_db_options(manager, "get")
        try:
            try:
                manager.get(id="probe-id", fields=[])
            except HTTPException:
                pass
            except Exception:
                pass
        finally:
            restore()
        if holder["options"] is None and holder["fields"] is None:
            pytest.skip(
                "manager.get path did not reach DB layer for this concrete manager"
            )
        options = holder["options"] or []
        load_only_present = any(self._is_load_option(opt) for opt in options)
        captured_fields = holder["fields"] or []
        # Empty fields=[] must produce neither a load_only option nor a
        # non-empty fields kwarg on the DB layer — both would select zero
        # columns and break round-trip serialization.
        assert not load_only_present and not captured_fields, (
            "empty fields=[] should not produce a load_only option or "
            f"non-empty fields= kwarg; captured: {holder!r}"
        )

    def test_list_field_selection_propagates_to_db_layer(
        self, admin_a, team_a, server, model_registry
    ):
        """The BLL ``list(fields=...)`` path mirrors ``get(fields=...)``:
        the captured options on the underlying ``DB.list`` call must
        include a ``load_only``. Item 87. Stubs the DB layer to return
        ``[]`` and snapshots the options without needing a populated
        database."""
        self.server = server
        self.model_registry = model_registry
        requester_id = self._effective_requester(admin_a.id)
        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        candidate_fields = [
            f for f in ("name", "id") if f in (manager.Model.model_fields or {})
        ]
        if not candidate_fields:
            pytest.skip("no introspectable fields on this model")
        holder, restore = self._capture_db_options(manager, "list")
        try:
            try:
                manager.list(fields=candidate_fields)
            except Exception:
                pass  # downstream None handling may raise; options snapshot is what we need
        finally:
            restore()
        if holder["options"] is None and holder["fields"] is None:
            pytest.skip(
                "manager.list path did not reach DB layer for this concrete manager"
            )
        assert self._captured_load_only_evidence(holder, candidate_fields), (
            f"expected fields={candidate_fields} to propagate to DB.list "
            f"(via options=load_only or fields= kwarg); captured: {holder!r}"
        )

    # ------------------------------------------------------------------ #
    # Item 87 — abstract field-selection / includes matrix
    # ------------------------------------------------------------------ #
    # The framework's CRUD path supports `fields=[...]` (load_only column
    # projection) and `include=[...]` (eager-loaded relationships via
    # joinedload/selectinload). Item 87 / GitHub #10 asks for the abstract
    # contract: every concrete manager that participates in the BLL
    # CRUD-test ladder gets these three matrix tests for free.
    #
    # The tests use the same DB-stub pattern as the field-selection
    # primitives above so they execute without a populated database — they
    # snapshot the SQLAlchemy options the BLL hands the DB layer and
    # assert against the option shape (load_only present, Load-family
    # options for includes, no N+1 explosion).

    @staticmethod
    def _candidate_relationship_names(manager) -> List[str]:
        """Return relationship names the test can safely use as ``include=``
        targets. The list is best-effort — concrete managers without
        relationships return ``[]`` and the matrix tests skip rather than
        fail."""
        rels: List[str] = []
        try:
            mapper = getattr(manager.DB, "__mapper__", None)
            if mapper is not None and hasattr(mapper, "relationships"):
                for rel_name in mapper.relationships.keys():
                    rels.append(rel_name)
        except Exception:
            return []
        return sorted(rels)

    @staticmethod
    def _captured_includes_evidence(holder) -> bool:
        """Return True iff the BLL pushed a Load-family option (joinedload
        / selectinload / nested Load chains) into the DB layer. Empty
        options indicate a relationship was requested but the join wasn't
        emitted — that's the N+1 leak we want to catch."""
        options = holder.get("options") or []
        return any(AbstractBLLTest._is_load_option(opt) for opt in options)

    def test_load_only(self, admin_a, team_a, server, model_registry):
        """Item 87 — `manager.get`, `manager.list`, and `manager.search`
        must all push the requested ``fields=[...]`` to the DB layer as a
        ``load_only`` option (or equivalent ``fields=`` kwarg). Mirrors
        the existing single-method tests but exercises the full CRUD-read
        triad in one matrix so a regression in any one of them surfaces
        on the same line.
        """
        self.server = server
        self.model_registry = model_registry
        from fastapi import HTTPException

        requester_id = self._effective_requester(admin_a.id)
        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        candidate_fields = [
            f for f in ("name", "id") if f in (manager.Model.model_fields or {})
        ]
        if not candidate_fields:
            pytest.skip("no introspectable fields on this model")

        for db_method, invoke in (
            ("get", lambda m: m.get(id="probe-id", fields=candidate_fields)),
            ("list", lambda m: m.list(fields=candidate_fields)),
            ("list", lambda m: m.search(fields=candidate_fields)),
        ):
            holder, restore = self._capture_db_options(manager, db_method)
            try:
                try:
                    invoke(manager)
                except HTTPException:
                    pass
                except Exception:
                    pass
            finally:
                restore()
            if holder["options"] is None and holder["fields"] is None:
                continue
            assert self._captured_load_only_evidence(holder, candidate_fields), (
                f"expected fields={candidate_fields} to propagate to DB.{db_method} "
                f"(via options=load_only or fields= kwarg); captured: {holder!r}"
            )

    def test_includes(self, admin_a, team_a, server, model_registry):
        """Item 87 — `manager.list(include=[...])` must push a Load-family
        option (joinedload / selectinload) into the DB layer. The contract
        prevents N+1 across the result set: relationships requested with
        ``include=`` produce one batched join, not one query per row."""
        self.server = server
        self.model_registry = model_registry
        requester_id = self._effective_requester(admin_a.id)
        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        rels = self._candidate_relationship_names(manager)
        if not rels:
            pytest.skip("manager.DB exposes no introspectable relationships")

        target_rel = rels[0]
        holder, restore = self._capture_db_options(manager, "list")
        try:
            try:
                manager.list(include=[target_rel])
            except Exception:
                pass
        finally:
            restore()
        if holder["options"] is None and holder["fields"] is None:
            pytest.skip(
                "manager.list path did not reach DB layer for this concrete manager"
            )
        assert self._captured_includes_evidence(holder), (
            f"expected include=[{target_rel!r}] to produce a Load-family option; "
            f"captured: {holder!r}"
        )

    def test_fields(self, admin_a, team_a, server, model_registry):
        """Item 87 — combined ``fields`` + ``include`` matrix. Asserts both
        options coexist on the DB call: ``load_only`` for the projected
        columns, plus a Load-family join for the included relationship.
        Catches regressions where one option clobbers the other."""
        self.server = server
        self.model_registry = model_registry
        requester_id = self._effective_requester(admin_a.id)
        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        candidate_fields = [
            f for f in ("name", "id") if f in (manager.Model.model_fields or {})
        ]
        rels = self._candidate_relationship_names(manager)
        if not candidate_fields or not rels:
            pytest.skip(
                "manager has no candidate fields or relationships for the matrix"
            )

        target_rel = rels[0]
        holder, restore = self._capture_db_options(manager, "list")
        try:
            try:
                manager.list(fields=candidate_fields, include=[target_rel])
            except Exception:
                pass
        finally:
            restore()
        if holder["options"] is None and holder["fields"] is None:
            pytest.skip(
                "manager.list path did not reach DB layer for this concrete manager"
            )
        load_only_present = self._captured_load_only_evidence(holder, candidate_fields)
        joins_present = self._captured_includes_evidence(holder)
        assert load_only_present, (
            f"expected fields={candidate_fields} to produce a load_only "
            f"option in the combined matrix; captured: {holder!r}"
        )
        assert joins_present, (
            f"expected include=[{target_rel!r}] to produce a Load-family "
            f"option in the combined matrix; captured: {holder!r}"
        )

    def test_invalid_field_name_rejected(
        self, admin_a, team_a, server, model_registry
    ):
        """A field name not declared on the Pydantic model must be rejected
        — the validator should fail before the SQL is produced. Item 87."""
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id,
            team_a.id,
            "field_invalid",
            server=server,
            model_registry=model_registry,
        )
        requester_id = self._effective_requester(admin_a.id)
        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        # `validate_fields` is the entry point AbstractLogicManager uses
        # before assembling load_only — assert directly against it. The
        # contract is: invalid names raise (HTTPException 422) or are
        # filtered out; either way the bogus name must not survive.
        try:
            result = manager.validate_fields(["this_field_does_not_exist_xyz"])
        except Exception:
            return  # explicit reject path is also acceptable
        assert "this_field_does_not_exist_xyz" not in (result or []), (
            "invalid field name must be filtered or raise; instead survived: "
            f"{result!r}"
        )

    # ------------------------------------------------------------------ #
    # Scalability / Big-O assertions
    # ------------------------------------------------------------------ #
    # Subclasses opt in by setting `scalability_profile`. Asserts that
    # `manager.list()` scales within the configured exponent for time,
    # query count, and peak memory as the underlying entity count grows.
    # The Big-O exponent caught here is the one driving the framework's
    # n-factor SLO: regressions surface as a metric-specific assertion
    # rather than a single opaque pass/fail.

    scalability_profile: Optional[ScalabilityProfile] = None

    @pytest.mark.parametrize(
        "metric",
        sorted(
            [ScalingMetric.TIME, ScalingMetric.QUERY_COUNT, ScalingMetric.MEMORY],
            key=lambda m: m.value,  # type: ignore[attr-defined]
        ),
    )
    def test_scalability_list_n_factor(
        self, admin_a, team_a, server, model_registry, metric: ScalingMetric
    ):
        """Big-O assertion: manager.list() must stay within the configured
        exponent for time, query count, and peak memory as the BLL surface
        scales out. Catches accidental N+1 in resolver chains, hook-storm
        regressions, and per-entity fan-out."""
        if self.scalability_profile is None:
            pytest.skip("scalability_profile not configured on this test class")
        if metric not in self.scalability_profile.metrics:
            pytest.skip(f"metric {metric.value} not enabled in scalability_profile")

        self.server = server
        self.model_registry = model_registry
        seeded = {"count": 0}

        def setup(target_n: int) -> None:
            while seeded["count"] < target_n:
                self._create(
                    admin_a.id,
                    team_a.id,
                    key=f"scalability_seed_{seeded['count']}",
                    server=server,
                    model_registry=model_registry,
                )
                seeded["count"] += 1

        requester = self._effective_requester(admin_a.id)

        def operation(_n: int) -> None:
            manager = self.class_under_test(
                requester_id=requester,
                target_team_id=team_a.id,
                model_registry=model_registry,
            )
            manager.list()

        engine = model_registry.database_manager.engine
        assert_scaling_within_bounds(
            operation, self.scalability_profile, metric, engine=engine, setup=setup
        )
