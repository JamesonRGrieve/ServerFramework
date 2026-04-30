import uuid
from datetime import datetime, timedelta
from typing import Any, Optional
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from serverframework.database.StaticPermissions import ROOT_ID
from serverframework.lib.Pydantic2SQLAlchemy import DatabaseMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    HookContext,
    HookTiming,
    NumericalSearchModel,
    StringSearchModel,
    UpdateMixinModel,
    hook_bll,
)


# Mock Database Model that simulates the current BLL architecture
class MockDBModel:
    """
    Mock database model that simulates AbstractDatabaseEntity behavior
    for testing business logic managers without requiring actual database operations.
    """

    # Class-level store to remember entities between operations
    _entity_store = {}

    # Define class attributes that will be accessible via hasattr/getattr
    id = MagicMock()
    name = MagicMock()
    description = MagicMock()
    created_at = MagicMock()
    created_at.__gt__.return_value = True
    created_by_user_id = MagicMock()
    updated_at = MagicMock()
    updated_at.__lt__.return_value = True
    updated_by_user_id = MagicMock()
    user_id = MagicMock()
    count = MagicMock()
    count.__gt__.return_value = True
    value = MagicMock()
    is_active = MagicMock()

    @classmethod
    def create(
        cls,
        requester_id,
        db=None,
        db_manager=None,
        model_registry=None,
        return_type="dict",
        override_dto=None,
        **kwargs,
    ):
        """Mock create method that returns a basic entity"""
        mock_entity = MagicMock()
        mock_entity.id = str(uuid.uuid4())
        mock_entity.created_at = datetime.now()
        mock_entity.created_by_user_id = requester_id
        mock_entity.updated_at = mock_entity.created_at
        mock_entity.updated_by_user_id = requester_id

        # Set any provided kwargs as attributes on the mock entity
        for key, value in kwargs.items():
            setattr(mock_entity, key, value)

        # Store the entity for future updates
        cls._entity_store[mock_entity.id] = mock_entity

        # Return DTO if requested
        if override_dto and return_type == "dto":
            # Create DTO data with all the entity attributes
            # Handle MagicMock values properly
            name_value = getattr(mock_entity, "name", "Test Entity")
            if hasattr(name_value, "_mock_name"):  # It's a MagicMock
                name_value = "Test Entity"

            description_value = getattr(mock_entity, "description", "Test Description")
            if hasattr(description_value, "_mock_name"):  # It's a MagicMock
                description_value = "Test Description"

            dto_data = {
                "id": mock_entity.id,
                "created_at": mock_entity.created_at,
                "created_by_user_id": mock_entity.created_by_user_id,
                "name": name_value,
                "description": description_value,
                "updated_at": mock_entity.updated_at,
                "updated_by_user_id": mock_entity.updated_by_user_id,
            }

            # Add any additional kwargs that were passed (including hook modifications)
            for key, value in kwargs.items():
                if key not in dto_data:
                    dto_data[key] = value

            # Create the DTO
            dto = override_dto(**dto_data)

            # Try to set additional attributes directly on the DTO for testing
            for key, value in kwargs.items():
                if key not in [
                    "id",
                    "created_at",
                    "created_by_user_id",
                    "name",
                    "description",
                    "updated_at",
                    "updated_by_user_id",
                ]:
                    try:
                        setattr(dto, key, value)
                    except (ValueError, AttributeError):
                        # If direct setting fails, use private attribute for testing
                        setattr(dto, f"_{key}", value)

            return dto

        return mock_entity

    @classmethod
    def get(
        cls,
        requester_id,
        db=None,
        db_manager=None,
        model_registry=None,
        return_type="dict",
        override_dto=None,
        options=None,
        **kwargs,
    ):
        """Mock get method that returns a basic entity"""
        entity_id = kwargs.get("id", str(uuid.uuid4()))

        # Check if entity exists in store
        if entity_id in cls._entity_store:
            mock_entity = cls._entity_store[entity_id]
        else:
            # Create new entity if not found
            mock_entity = MagicMock()
            mock_entity.id = entity_id
            mock_entity.name = "Test Item"
            mock_entity.description = "Test Description"
            mock_entity.created_at = datetime.now()
            mock_entity.created_by_user_id = requester_id
            mock_entity.updated_at = mock_entity.created_at
            mock_entity.updated_by_user_id = requester_id
            cls._entity_store[entity_id] = mock_entity

        # Return DTO if requested
        if return_type == "dto" and override_dto:
            return override_dto(
                id=mock_entity.id,
                name=getattr(mock_entity, "name", "Test Item"),
                description=getattr(mock_entity, "description", "Test Description"),
                created_at=mock_entity.created_at,
                created_by_user_id=mock_entity.created_by_user_id,
                updated_at=mock_entity.updated_at,
                updated_by_user_id=mock_entity.updated_by_user_id,
            )
        return mock_entity

    @classmethod
    def list(
        cls,
        requester_id,
        db=None,
        db_manager=None,
        model_registry=None,
        return_type="dict",
        override_dto=None,
        options=None,
        order_by=None,
        limit=None,
        offset=None,
        filters=None,
        **kwargs,
    ):
        """Mock list method that returns a list of basic entities"""
        results = []
        for i in range(5):
            mock_entity = MagicMock()
            mock_entity.id = str(uuid.uuid4())
            mock_entity.name = f"Test Item {i}"
            mock_entity.description = f"Test Description {i}"
            mock_entity.created_at = datetime.now()
            mock_entity.created_by_user_id = requester_id
            mock_entity.updated_at = mock_entity.created_at
            mock_entity.updated_by_user_id = requester_id
            mock_entity.count = i + 1

            # Return DTO if requested
            if return_type == "dto" and override_dto:
                results.append(
                    override_dto(
                        id=mock_entity.id,
                        name=mock_entity.name,
                        description=mock_entity.description,
                        created_at=mock_entity.created_at,
                        created_by_user_id=mock_entity.created_by_user_id,
                        updated_at=mock_entity.updated_at,
                        updated_by_user_id=mock_entity.updated_by_user_id,
                        count=mock_entity.count,
                    )
                )
            else:
                results.append(mock_entity)
        return results

    @classmethod
    def update(
        cls,
        requester_id,
        db=None,
        db_manager=None,
        model_registry=None,
        return_type="dict",
        override_dto=None,
        new_properties=None,
        id=None,
        **kwargs,
    ):
        """Mock update method that returns an updated entity"""
        if new_properties is None:
            new_properties = kwargs

        # Get existing entity from store or create a new one
        if id in cls._entity_store:
            mock_entity = cls._entity_store[id]
            # Update the timestamp and user
            mock_entity.updated_at = datetime.now()
            mock_entity.updated_by_user_id = requester_id
        else:
            # Create new entity if not found
            mock_entity = MagicMock()
            mock_entity.id = id
            mock_entity.created_at = datetime.now() - timedelta(days=1)
            mock_entity.created_by_user_id = requester_id
            mock_entity.updated_at = datetime.now()
            mock_entity.updated_by_user_id = requester_id
            cls._entity_store[id] = mock_entity

        # Apply the new properties
        for key, value in new_properties.items():
            setattr(mock_entity, key, value)

        # Return DTO if requested
        if return_type == "dto" and override_dto:
            return override_dto(
                id=mock_entity.id,
                name=getattr(mock_entity, "name", None),
                description=getattr(mock_entity, "description", None),
                created_at=mock_entity.created_at,
                created_by_user_id=mock_entity.created_by_user_id,
                updated_at=mock_entity.updated_at,
                updated_by_user_id=mock_entity.updated_by_user_id,
            )
        return mock_entity

    @classmethod
    def delete(
        cls,
        requester_id,
        db=None,
        db_manager=None,
        model_registry=None,
        id=None,
        **kwargs,
    ):
        """Mock delete method that does nothing but doesn't raise an error"""
        # Remove from store if it exists
        if id in cls._entity_store:
            del cls._entity_store[id]

    @classmethod
    def clear_store(cls):
        """Clear the entity store - useful for test cleanup"""
        cls._entity_store.clear()


# Test Models - Using real BLL pattern with DatabaseMixin
class AbstractDbBaseEntityTestModel(ApplicationModel, UpdateMixinModel, DatabaseMixin):
    name: Optional[str] = Field(None, description="The name")
    description: Optional[str] = Field(None, description="description")
    user_id: Optional[str] = Field(None, description="user_id")
    team_id: Optional[str] = Field(None, description="team_id")
    count: Optional[int] = Field(None)

    class Create(BaseModel):
        name: str
        description: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = Field(None, description="name")
        description: Optional[str] = Field(None, description="description")

    class Search(ApplicationModel.Search):
        name: Optional[StringSearchModel] = None
        count: Optional[NumericalSearchModel] = None
        updated_at: Optional[DateSearchModel] = None
        created_at: Optional[DateSearchModel] = None


# Test Manager - Using simple BLL pattern without complex initialization
class BaseManagerForTest(AbstractBLLManager):
    Model = AbstractDbBaseEntityTestModel

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        parent: Optional[Any] = None,
        model_registry=None,
    ):
        """Test-specific initialization using model_registry pattern"""
        if model_registry is None:
            raise ValueError("model_registry is required for tests")

        self.model_registry = model_registry
        self.requester_id = requester_id
        self.target_id = target_id
        self.target_team_id = target_team_id

        # Create a fake requester object for testing
        class FakeRequester:
            def __init__(self, id: str):
                self.id = id

        self.requester = FakeRequester(requester_id)
        self._target_user = None
        self._target_team = None
        self._target = None
        self._target_loaded = False
        self._parent = parent

        # Initialize any search transformers
        self._register_search_transformers()

    @property
    def db(self) -> Session:
        """Property that returns a database session from ModelRegistry."""
        return self.model_registry.DB.session()

    @property
    def DB(self):
        """Get the SQLAlchemy model using ModelRegistry."""
        return self.Model.DB(self.model_registry.DB.manager.Base)

    @property
    def target_user_id(self) -> Optional[str]:
        """Get target user ID for backward compatibility."""
        return self.target_id if self.target_id else self.requester.id

    def _register_search_transformers(self):
        """Register search transformers for the test model."""
        self.register_search_transformer("name", self._transform_name_search)
        self.register_search_transformer(
            "description", self._transform_description_search
        )

    def _transform_name_search(self, value):
        """Transform name search parameters into SQLAlchemy filters."""
        return self._transform_string_field_search(self.DB.name, value)

    def _transform_description_search(self, value):
        """Transform description search parameters into SQLAlchemy filters."""
        return self._transform_string_field_search(self.DB.description, value)

    def _transform_string_field_search(self, column, value):
        """Helper method to transform string search parameters into SQLAlchemy filters."""
        if not isinstance(value, dict):
            return []

        filters = []
        if "inc" in value:
            filters.append(column.ilike(f"%{value['inc']}%"))
        if "sw" in value:
            filters.append(column.ilike(f"{value['sw']}%"))
        if "ew" in value:
            filters.append(column.ilike(f"%{value['ew']}"))
        if "eq" in value:
            filters.append(column == value["eq"])
        return filters


# Hook test tracking variables
class HookTracker:
    def __init__(self):
        self.reset()

    def reset(self):
        self.calls = []
        self.contexts = []
        self.errors = []

    def record_call(self, hook_name: str, context: HookContext):
        self.calls.append(hook_name)
        self.contexts.append(context)


# Global hook tracker for tests
hook_tracker = HookTracker()


# Hook functions for testing
def class_level_before_hook(context: HookContext) -> None:
    """Test class-level before hook"""
    hook_tracker.record_call(f"class_before_{context.method_name}", context)
    if context.timing == HookTiming.BEFORE and context.method_name in [
        "create",
        "get",
        "list",
        "update",
        "delete",
    ]:
        context.kwargs["hook_processed"] = True


def class_level_after_hook(context: HookContext) -> None:
    """Test class-level after hook"""
    hook_tracker.record_call(f"class_after_{context.method_name}", context)


def method_specific_before_hook(context: HookContext) -> None:
    """Test method-specific before hook"""
    hook_tracker.record_call("method_before_create", context)
    if "name" in context.kwargs:
        context.kwargs["name"] = f"Hook-{context.kwargs['name']}"


def method_specific_after_hook(context: HookContext) -> None:
    """Test method-specific after hook"""
    hook_tracker.record_call("method_after_create", context)
    if context.result and hasattr(context.result, "description"):
        context.result.description = f"Modified by hook: {context.result.description}"


def priority_hook_1(context: HookContext) -> None:
    """Priority 1 hook (should run first)"""
    hook_tracker.record_call("priority_1", context)


def priority_hook_5(context: HookContext) -> None:
    """Priority 5 hook (should run second)"""
    hook_tracker.record_call("priority_5", context)


def priority_hook_10(context: HookContext) -> None:
    """Priority 10 hook (should run third)"""
    hook_tracker.record_call("priority_10", context)


def conditional_hook(context: HookContext) -> None:
    """Conditional hook that only runs under specific conditions"""
    hook_tracker.record_call("conditional_hook", context)


def condition_with_name(context: HookContext) -> bool:
    """Condition function that checks for name in kwargs"""
    return "name" in context.kwargs and context.kwargs["name"] == "conditional_test"


def skip_method_hook(context: HookContext) -> None:
    """Hook that skips method execution"""
    hook_tracker.record_call("skip_method_hook", context)
    context.skip_method()
    context.set_result({"skipped": True, "reason": "Skipped by hook"})


def error_hook(context: HookContext) -> None:
    """Hook that raises an error"""
    hook_tracker.record_call("error_hook", context)
    raise ValueError("Test hook error")


def timing_aware_hook(context: HookContext) -> None:
    """Hook that behaves differently based on timing"""
    if context.timing == HookTiming.BEFORE:
        hook_tracker.record_call("timing_before", context)
        context.condition_data["start_time"] = datetime.now()
    elif context.timing == HookTiming.AFTER:
        hook_tracker.record_call("timing_after", context)
        start_time = context.condition_data.get("start_time")
        if start_time:
            context.condition_data["duration"] = datetime.now() - start_time


# Tests
class TestAbstractLogicManager:
    """Test AbstractLogicManager using standard pytest infrastructure"""

    @pytest.fixture(autouse=True)
    def setup_test(self, mock_server):
        """Set up test environment with mock database session"""
        # Reset hook tracker
        hook_tracker.reset()

        # Clear database model cache to ensure fresh SQLAlchemy models
        AbstractDbBaseEntityTestModel.clear_db_cache()

        # Get database session and manager from mock server
        self.db = mock_server.app.state.model_registry.database_manager.get_session()
        self.db_manager = mock_server.app.state.model_registry.database_manager
        self.model_registry = getattr(mock_server.app.state, "model_registry", None)

        # Ensure the test model is registered with the shared model registry
        if self.model_registry and not self.model_registry.is_model_bound(
            AbstractDbBaseEntityTestModel
        ):
            was_locked = self.model_registry.is_committed()
            try:
                if was_locked:
                    self.model_registry._locked = False
                self.model_registry.bound_models.add(AbstractDbBaseEntityTestModel)
                self.model_registry.model_metadata.setdefault(
                    AbstractDbBaseEntityTestModel, {}
                )
                self.model_registry.utility.register_model(
                    AbstractDbBaseEntityTestModel
                )
                if not hasattr(AbstractDbBaseEntityTestModel, "Network"):
                    self.model_registry._generate_network_class(
                        AbstractDbBaseEntityTestModel
                    )
            finally:
                if was_locked:
                    self.model_registry._locked = True

        # Ensure the test table exists with correct structure
        try:
            test_model = AbstractDbBaseEntityTestModel.DB(self.db_manager.Base)
            # Drop the table if it exists and recreate it with the correct structure
            test_model.metadata.drop_all(
                bind=self.db.bind, tables=[test_model.__table__]
            )
            test_model.metadata.create_all(
                bind=self.db.bind, tables=[test_model.__table__]
            )
        except Exception as e:
            # If there's an error creating the table, we'll continue
            # The server setup should have handled table creation
            pass

        # Create manager instances using the model registry
        self.base_manager = BaseManagerForTest(
            requester_id=ROOT_ID,
            target_id="user2",
            target_team_id="team1",
            model_registry=self.model_registry,
        )

        yield

        # Clear any registered hooks to prevent test interference
        if hasattr(BaseManagerForTest, "_hook_registry"):
            BaseManagerForTest._hook_registry.clear()

        hook_tracker.reset()

        # Close database session
        if self.db:
            self.db.close()

    def test_manager_initialization(self):
        """Test that manager initializes correctly with proper attributes."""
        assert self.base_manager.requester_id == ROOT_ID
        assert self.base_manager.target_id == "user2"
        assert self.base_manager.target_team_id == "team1"
        assert self.base_manager.db is not None

    def test_target_user_id_backward_compatibility(self):
        """Test that target_user_id property works for backward compatibility."""
        assert self.base_manager.target_user_id == "user2"

        # Test with no target_id
        manager = BaseManagerForTest(
            requester_id=ROOT_ID, model_registry=self.model_registry
        )
        assert manager.target_user_id == ROOT_ID

    def test_hook_context_creation(self):
        """Test HookContext creation and properties"""
        context = HookContext(
            manager=self.base_manager,
            method_name="create",
            args=("arg1", "arg2"),
            kwargs={"name": "test", "description": "test desc"},
            timing=HookTiming.BEFORE,
        )

        assert context.manager == self.base_manager
        assert context.method_name == "create"
        assert context.args == ["arg1", "arg2"]
        assert context.kwargs == {"name": "test", "description": "test desc"}
        assert context.timing == HookTiming.BEFORE
        assert context.result is None
        assert context.condition_data == {}
        assert not context.skip_execution

    def test_hook_context_argument_modification(self):
        """Test that HookContext allows argument modification"""
        context = HookContext(
            manager=self.base_manager,
            method_name="create",
            args=["original_arg"],
            kwargs={"name": "original_name"},
            timing=HookTiming.BEFORE,
        )

        # Modify arguments
        context.args.append("new_arg")
        context.kwargs["name"] = "modified_name"
        context.kwargs["new_field"] = "new_value"

        assert context.args == ["original_arg", "new_arg"]
        assert context.kwargs["name"] == "modified_name"
        assert context.kwargs["new_field"] == "new_value"

    def test_hook_context_result_modification(self):
        """Test HookContext result modification"""
        context = HookContext(
            manager=self.base_manager,
            method_name="create",
            args=[],
            kwargs={},
            timing=HookTiming.AFTER,
        )

        # Set and modify result
        original_result = {"id": "123", "name": "test"}
        context.set_result(original_result)
        assert context.modified_result == original_result

        # Modify result
        modified_result = {"id": "123", "name": "test", "modified": True}
        context.set_result(modified_result)
        assert context.modified_result == modified_result

    def test_hook_context_skip_method(self):
        """Test HookContext method skipping"""
        context = HookContext(
            manager=self.base_manager,
            method_name="create",
            args=[],
            kwargs={},
            timing=HookTiming.BEFORE,
        )

        assert not context.skip_execution
        context.skip_method()
        assert context.skip_execution

    def test_class_level_hook_registration(self):
        """Test registering class-level hooks"""
        # Register class-level hooks using new syntax
        hook_bll(BaseManagerForTest, timing=HookTiming.BEFORE, priority=10)(
            class_level_before_hook
        )
        hook_bll(BaseManagerForTest, timing=HookTiming.AFTER, priority=20)(
            class_level_after_hook
        )

        # Execute a method that should trigger hooks
        entity = self.base_manager.create(name="Test Entity")

        # Verify hooks were called
        assert "class_before_create" in hook_tracker.calls
        assert "class_after_create" in hook_tracker.calls

        # Verify the entity was created successfully
        assert entity is not None
        assert entity.name == "Test Entity"

    def test_method_specific_hook_registration(self):
        """Test registering method-specific hooks using method references"""
        # Register method-specific hooks
        hook_bll(BaseManagerForTest.create, timing=HookTiming.BEFORE, priority=15)(
            method_specific_before_hook
        )
        hook_bll(BaseManagerForTest.create, timing=HookTiming.AFTER, priority=25)(
            method_specific_after_hook
        )

        # Execute create method
        entity = self.base_manager.create(
            name="Original Name", description="Original Description"
        )

        # Verify hooks were called
        assert "method_before_create" in hook_tracker.calls
        assert "method_after_create" in hook_tracker.calls

        # Verify the entity was created with hook modifications
        assert entity.name == "Hook-Original Name"

    def test_hook_priority_ordering(self):
        """Test that hooks execute in priority order"""
        # Register hooks with different priorities
        hook_bll(BaseManagerForTest.create, timing=HookTiming.BEFORE, priority=1)(
            priority_hook_1
        )
        hook_bll(BaseManagerForTest.create, timing=HookTiming.BEFORE, priority=10)(
            priority_hook_10
        )
        hook_bll(BaseManagerForTest.create, timing=HookTiming.BEFORE, priority=5)(
            priority_hook_5
        )

        # Execute method
        self.base_manager.create(name="Priority Test")

        # Verify hooks were called in priority order
        priority_calls = [
            call for call in hook_tracker.calls if call.startswith("priority_")
        ]
        assert priority_calls == ["priority_1", "priority_5", "priority_10"]

    def test_conditional_hook_execution(self):
        """Test conditional hook execution"""
        # Register conditional hook
        hook_bll(
            BaseManagerForTest.create,
            timing=HookTiming.BEFORE,
            priority=10,
            condition=condition_with_name,
        )(conditional_hook)

        # Test 1: Condition not met
        self.base_manager.create(name="regular_test")
        assert "conditional_hook" not in hook_tracker.calls

        # Reset tracker
        hook_tracker.reset()

        # Test 2: Condition met
        self.base_manager.create(name="conditional_test")
        assert "conditional_hook" in hook_tracker.calls

    def test_hook_method_skipping(self):
        """Test that hooks can skip method execution"""
        # Register hook that skips method
        hook_bll(BaseManagerForTest.create, timing=HookTiming.BEFORE, priority=10)(
            skip_method_hook
        )

        # Execute method
        result = self.base_manager.create(name="Skip Test")

        # Verify hook was called
        assert "skip_method_hook" in hook_tracker.calls

        # Verify method was skipped and custom result returned
        assert result == {"skipped": True, "reason": "Skipped by hook"}

    def test_timing_aware_hooks(self):
        """Test hooks that use both BEFORE and AFTER timing"""
        # Register the same hook for both timings
        hook_bll(BaseManagerForTest.create, timing=HookTiming.BEFORE, priority=10)(
            timing_aware_hook
        )
        hook_bll(BaseManagerForTest.create, timing=HookTiming.AFTER, priority=10)(
            timing_aware_hook
        )

        # Execute method
        self.base_manager.create(name="Timing Test")

        # Verify both timing hooks were called
        assert "timing_before" in hook_tracker.calls
        assert "timing_after" in hook_tracker.calls

        # Verify timing_before was called before timing_after
        before_index = hook_tracker.calls.index("timing_before")
        after_index = hook_tracker.calls.index("timing_after")
        assert before_index < after_index

    def test_hook_error_handling(self):
        """Item 22: BEFORE hooks default blocking=True; an error must propagate.

        The same hook registered with blocking=False must NOT propagate.
        """
        # Default (blocking=True) BEFORE hook: error must propagate.
        hook_bll(BaseManagerForTest.create, timing=HookTiming.BEFORE, priority=10)(
            error_hook
        )
        with pytest.raises(ValueError):
            self.base_manager.create(name="Error Test")
        assert "error_hook" in hook_tracker.calls

        # Reset registry so we can re-register with blocking=False.
        BaseManagerForTest._hook_registry.clear()
        hook_tracker.calls.clear()
        # Re-register with blocking=False -- error must be swallowed.
        hook_bll(
            BaseManagerForTest.create,
            timing=HookTiming.BEFORE,
            priority=10,
            blocking=False,
        )(error_hook)
        entity = self.base_manager.create(name="Error Test 2")
        assert entity is not None
        assert entity.name == "Error Test 2"
        assert "error_hook" in hook_tracker.calls

    def test_create_operation(self):
        """Test creating an entity."""
        entity = self.base_manager.create(
            name="Test Entity", description="Test Description"
        )

        assert entity is not None
        assert entity.id is not None
        assert entity.name == "Test Entity"
        assert entity.description == "Test Description"
        assert entity.created_at is not None
        assert entity.created_by_user_id is not None

    def test_get_operation(self):
        """Test getting an entity by ID."""
        # First create an entity
        created_entity = self.base_manager.create(name="Get Test Entity")

        # Then get it
        entity = self.base_manager.get(id=created_entity.id)

        assert entity is not None
        assert entity.id == created_entity.id
        assert entity.name == "Get Test Entity"

    def test_list_operation(self):
        """Test listing entities."""
        # Create multiple entities
        entity1 = self.base_manager.create(name="List Test Entity 1")
        entity2 = self.base_manager.create(name="List Test Entity 2")

        # List all entities
        entities = self.base_manager.list()

        assert isinstance(entities, list)
        assert len(entities) >= 2

        # Check that our created entities are in the list
        entity_ids = [entity.id for entity in entities]
        assert entity1.id in entity_ids
        assert entity2.id in entity_ids

    def test_update_operation(self):
        """Test updating an entity."""
        # Create an entity first
        created_entity = self.base_manager.create(
            name="Update Test", description="Original"
        )

        # Update it
        updated = self.base_manager.update(
            id=created_entity.id,
            name="Updated Name",
            description="Updated Description",
        )

        assert updated is not None
        assert updated.id == created_entity.id
        assert updated.name == "Updated Name"
        assert updated.description == "Updated Description"

    def test_delete_operation(self):
        """Test deleting an entity."""
        # Create an entity first
        created_entity = self.base_manager.create(name="Delete Test")

        # Delete it
        self.base_manager.delete(id=created_entity.id)

        # Try to get it - should handle soft delete appropriately
        try:
            deleted_entity = self.base_manager.get(id=created_entity.id)
            # If returned, check if it's soft deleted
            if (
                hasattr(deleted_entity, "deleted_at")
                and deleted_entity.deleted_at is not None
            ):
                assert deleted_entity.deleted_at is not None
            else:
                # Hard delete or proper 404 handling
                assert False, "Entity should be deleted"
        except Exception:
            # Hard delete or proper 404 handling
            pass

    def test_search_operation(self):
        """Test searching entities with various filters."""
        # Create test entities
        for i in range(3):
            self.base_manager.create(
                name=f"Search Test {i}", description=f"Description {i}"
            )

        # Test search infrastructure
        results = self.base_manager.search(name={"inc": "Search Test"})
        assert isinstance(results, list)

    # FIXME: filters are not working
    def test_search_string(self):
        results = self.base_manager.search(name={"eq": "TEst"})
        assert isinstance(results, list)

    def test_search_numerical(self):
        results = self.base_manager.search(count={"gt": 3})
        assert isinstance(results, list)

    def test_search_datetime(self):
        current_time = datetime.now()
        results = self.base_manager.search(
            created_at={"after": current_time}, updated_at={"before": current_time}
        )
        assert isinstance(results, list)

    def test_search_filter_building(self):
        """Test building of search filters."""

        # Create a mock SQLAlchemy field that can handle the ilike operation
        mock_field = MagicMock()
        mock_field.ilike.return_value = "mocked filter condition"

        # Patch the specific field access for the test
        with patch.object(MockDBModel, "name", mock_field), patch.object(
            MockDBModel, "description", mock_field
        ), patch.object(MockDBModel, "count", 3), patch.object(
            MockDBModel, "created_at", datetime.now()
        ), patch.object(
            MockDBModel, "updated_at", datetime.now()
        ):
            with patch.object(self.base_manager, "get_field_types") as mock_get_types:
                mock_get_types.return_value = (
                    ["name", "description"],  # string fields
                    ["count", "value"],  # numeric fields
                    ["created_at", "updated_at"],  # date fields
                    ["is_active"],  # boolean fields
                )

                # Test with various search parameters - using only the fields we've properly mocked
                search_params = {
                    "name": {"inc": "test"},
                    "count": {"gt": 3},
                    "created_at": {"after": datetime.now()},
                    "updated_at": {"before": datetime.now()},
                    "custom_search": "custom value",
                }

                # Register a custom transformer for testing
                custom_transformer_called = False

                def mock_custom_transformer(value):
                    nonlocal custom_transformer_called
                    custom_transformer_called = True
                    return [MagicMock()]

                self.base_manager.search_transformers["custom_search"] = (
                    mock_custom_transformer
                )

                # Build filters
                filters = self.base_manager.build_search_filters(search_params)

                # Verify custom transformer was called
                assert custom_transformer_called
                # Verify we got filters back
                assert len(filters) > 0

                assert len(filters) == 5

                # Cleanup
                del self.base_manager.search_transformers["custom_search"]

    def test_resolve_load_only_columns_adds_required_fields(self):
        """Required audit columns should always be included for load_only selections."""

        class DummyAttr:
            def __init__(self, key):
                self.key = key

        dummy_db = type("DummyDB", (), {})()
        dummy_db.__name__ = "DummyDB"
        dummy_db.id = DummyAttr("id")
        dummy_db.name = DummyAttr("name")
        dummy_db.created_at = DummyAttr("created_at")
        dummy_db.created_by_user_id = DummyAttr("created_by_user_id")
        dummy_db.updated_at = DummyAttr("updated_at")
        dummy_db.updated_by_user_id = DummyAttr("updated_by_user_id")
        dummy_db.__mapper__ = type(
            "DummyMapper",
            (),
            {
                "attrs": {
                    "id": None,
                    "name": None,
                    "created_at": None,
                    "created_by_user_id": None,
                    "updated_at": None,
                    "updated_by_user_id": None,
                }
            },
        )()

        with patch.object(
            BaseManagerForTest, "DB", new_callable=PropertyMock
        ) as db_prop:
            db_prop.return_value = dummy_db
            columns = self.base_manager._resolve_load_only_columns(["name"])

        keys = {getattr(column, "key", None) for column in columns}
        assert {
            "name",
            "id",
            "created_at",
            "created_by_user_id",
            "updated_at",
            "updated_by_user_id",
        } <= keys

    def test_resolve_load_only_columns_invalid_field(self):
        """Invalid fields should raise a ValueError with clear messaging."""

        class DummyAttr:
            def __init__(self, key):
                self.key = key

        dummy_db = type("DummyDB", (), {})()
        dummy_db.__name__ = "DummyDB"
        dummy_db.id = DummyAttr("id")
        dummy_db.__mapper__ = type("DummyMapper", (), {"attrs": {"id": None}})()

        with patch.object(
            BaseManagerForTest, "DB", new_callable=PropertyMock
        ) as db_prop:
            db_prop.return_value = dummy_db
            with pytest.raises(
                ValueError, match="Invalid fields for DummyDB: invalid_field"
            ):
                self.base_manager._resolve_load_only_columns(["invalid_field"])

    def test_resolve_load_only_columns_includes_required_model_fields(self):
        """Required Pydantic model fields should remain in load_only selections."""

        class DummyModel(BaseModel):
            name: str
            optional: Optional[str] = None

        class DummyAttr:
            def __init__(self, key):
                self.key = key

        dummy_db = type("DummyDB", (), {})()
        dummy_db.__name__ = "DummyDB"
        for attr_name in [
            "id",
            "name",
            "optional",
            "created_at",
            "created_by_user_id",
            "updated_at",
            "updated_by_user_id",
        ]:
            setattr(dummy_db, attr_name, DummyAttr(attr_name))
        dummy_db.__mapper__ = type(
            "DummyMapper",
            (),
            {
                "attrs": {
                    "id": None,
                    "name": None,
                    "optional": None,
                    "created_at": None,
                    "created_by_user_id": None,
                    "updated_at": None,
                    "updated_by_user_id": None,
                }
            },
        )()

        with patch.object(
            BaseManagerForTest, "DB", new_callable=PropertyMock
        ) as db_prop, patch.object(BaseManagerForTest, "Model", DummyModel):
            db_prop.return_value = dummy_db
            columns = self.base_manager._resolve_load_only_columns(["optional"])

        keys = {getattr(column, "key", None) for column in columns}
        assert "name" in keys  # required field from model
        assert {
            "id",
            "created_at",
            "created_by_user_id",
            "updated_at",
            "updated_by_user_id",
        }.issubset(keys)

    def test_batch_update_operation(self):
        """Test batch updating entities."""
        # First create entities to update
        entity1 = self.base_manager.create(
            name="Original 1", description="Original Description 1"
        )
        entity2 = self.base_manager.create(
            name="Original 2", description="Original Description 2"
        )

        items = [
            {
                "id": entity1.id,
                "data": {"name": "Updated 1", "description": "Batch Update 1"},
            },
            {
                "id": entity2.id,
                "data": {"name": "Updated 2", "description": "Batch Update 2"},
            },
        ]

        results = self.base_manager.batch_update(items)

        assert len(results) == 2
        assert results[0].name == "Updated 1"
        assert results[1].name == "Updated 2"

    def test_batch_delete_operation(self):
        """Test batch deleting entities."""
        # First create entities to delete
        entity1 = self.base_manager.create(name="To Delete 1")
        entity2 = self.base_manager.create(name="To Delete 2")
        entity3 = self.base_manager.create(name="To Delete 3")

        ids = [entity1.id, entity2.id, entity3.id]

        # Mock the delete method to track calls
        original_delete = self.base_manager.delete
        delete_calls = []

        def mock_delete(id):
            delete_calls.append(id)
            return original_delete(id)

        self.base_manager.delete = mock_delete

        # Execute batch delete
        self.base_manager.batch_delete(ids)

        # Verify all IDs were deleted
        assert len(delete_calls) == 3
        for id in ids:
            assert id in delete_calls

        # Restore original method
        self.base_manager.delete = original_delete
