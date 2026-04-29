import uuid
from typing import List, Optional

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, Field

from database.AbstractDatabaseEntity import (
    HookDict,
    HooksDescriptor,
    build_query,
    create_reference_mixin,
    db_to_return_type,
    get_hooks_for_class,
)
from database.StaticPermissions import ROOT_ID
from lib.Pydantic2SQLAlchemy import DatabaseMixin
from logic.AbstractLogicManager import ApplicationModel, UpdateMixinModel


# Define our own test model to avoid table name collisions
class AbstractDbEntityTestModel(ApplicationModel, UpdateMixinModel, DatabaseMixin):
    name: Optional[str] = Field(None, description="The name")
    description: Optional[str] = Field(None, description="description")
    user_id: Optional[str] = Field(None, description="user_id")
    team_id: Optional[str] = Field(None, description="team_id")

    class Create(BaseModel):
        name: str
        description: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = Field(None, description="name")
        description: Optional[str] = Field(None, description="description")


# Sample DTOs for testing db_to_return_type
class EntityDTOForTesting:
    def __init__(self, id=None, name=None, description=None, **kwargs):
        self.id = id
        self.name = name
        self.description = description
        for key, value in kwargs.items():
            setattr(self, key, value)

    def to_model(self):
        return EntityMockTestModeling(
            id=self.id, name=self.name, description=self.description
        )


class EntityMockTestModeling:
    def __init__(self, id=None, name=None, description=None, **kwargs):
        self.id = id
        self.name = name
        self.description = description
        for key, value in kwargs.items():
            setattr(self, key, value)


# Fixture for a test user ID
@pytest.fixture
def test_user_id():
    return str(uuid.uuid4())


# Fixture for a test entity ID
@pytest.fixture
def test_entity_id():
    return str(uuid.uuid4())


# Test HookDict and hook mechanisms


def test_hook_dict():
    """Test the HookDict class"""
    # Create a HookDict instance
    hook_dict = HookDict({"level1": {"level2": "value"}, "key": "direct_value"})

    # Test attribute access for direct values
    assert hook_dict.key == "direct_value"

    # Test attribute access for nested dictionaries (should return HookDict)
    assert isinstance(hook_dict.level1, HookDict)
    assert hook_dict.level1.level2 == "value"

    # Test setting attributes
    hook_dict.new_key = "new_value"
    assert hook_dict["new_key"] == "new_value"

    # Test attribute access for non-existent keys
    with pytest.raises(AttributeError):
        _ = hook_dict.non_existent_key


def test_get_hooks_for_class():
    """Test the get_hooks_for_class function"""

    # Create test classes
    class TestClassA:
        pass

    class TestClassB:
        pass

    # Get hooks for TestClassA
    hooks_a = get_hooks_for_class(TestClassA)

    # Should have all hook types
    for hook_type in ["create", "update", "delete", "get", "list"]:
        assert hook_type in hooks_a
        assert "before" in hooks_a[hook_type]
        assert "after" in hooks_a[hook_type]

    # Should have empty hook lists
    assert hooks_a["create"]["before"] == []
    assert hooks_a["create"]["after"] == []

    # Modify hooks
    hooks_a["create"]["before"].append(lambda: None)

    # Get hooks for TestClassB
    hooks_b = get_hooks_for_class(TestClassB)

    # Hooks for TestClassB should be different from TestClassA
    assert hooks_b["create"]["before"] == []
    assert len(hooks_a["create"]["before"]) == 1

    # Get hooks for TestClassA again - should return the same object
    hooks_a_again = get_hooks_for_class(TestClassA)
    assert hooks_a_again is hooks_a
    assert len(hooks_a_again["create"]["before"]) == 1


def test_hooks_descriptor():
    """Test the HooksDescriptor class"""

    class TestClass:
        hooks = HooksDescriptor()

    # Create instances
    instance1 = TestClass()
    instance2 = TestClass()

    # Initial hooks should be empty
    assert TestClass.hooks["create"]["before"] == []
    assert instance1.hooks["create"]["before"] == []

    # Modify class hooks
    TestClass.hooks["create"]["before"].append(lambda: "class_hook")

    # Instance hooks should reflect class hooks
    assert len(instance1.hooks["create"]["before"]) == 1
    assert len(instance2.hooks["create"]["before"]) == 1

    # Test that hooks are class-specific
    class AnotherClass:
        hooks = HooksDescriptor()

    assert AnotherClass.hooks["create"]["before"] == []


# Test using mock entities from AbstractLogicManager_test.py via the DB property


def test_register_seed_items(mock_server):
    """Test the register_seed_items class method"""
    # Clear database model cache to ensure fresh SQLAlchemy models
    AbstractDbEntityTestModel.clear_db_cache()

    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    # Reset seed list
    TestModel.seed_list = []

    # Register seed items
    seed_items = [
        {"name": "Seed 1", "description": "Description 1"},
        {"name": "Seed 2", "description": "Description 2"},
    ]
    TestModel.register_seed_items(seed_items)

    # Check seed list
    assert len(TestModel.seed_list) == 2
    assert TestModel.seed_list[0]["name"] == "Seed 1"


def test_create_foreign_key(mock_server):
    """Test the create_foreign_key method"""
    # Clear database model cache to ensure fresh SQLAlchemy models
    AbstractDbEntityTestModel.clear_db_cache()

    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    # Create a mock target entity
    class MockTargetEntity:
        __tablename__ = "target_table"

    # Test with default values (no model_registry provided)
    fk_col = TestModel.create_foreign_key(MockTargetEntity)

    assert fk_col.type.__class__.__name__ == "String"  # Should default to String
    assert fk_col.nullable is True
    assert len(fk_col.foreign_keys) == 1

    # Test with custom values and model_registry
    fk_col = TestModel.create_foreign_key(
        MockTargetEntity,
        db_manager=model_registry.DB,
        nullable=False,
        constraint_name="custom_constraint",
        ondelete="CASCADE",
    )

    assert fk_col.nullable is False
    assert len(fk_col.foreign_keys) == 1
    # Should use the database manager's PK type
    assert fk_col.type.__class__.__name__ == model_registry.DB.PK_TYPE.__name__


def test_id_column(mock_server):
    """Test that the id column is automatically generated"""
    # Clear database model cache to ensure fresh SQLAlchemy models
    AbstractDbEntityTestModel.clear_db_cache()

    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    db = model_registry.DB.get_session()
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    try:
        # Clear the table to ensure test isolation
        db.query(TestModel).delete()
        db.commit()

        # Create test entity with a unique name
        entity = TestModel(
            name="ID Column Test Entity",
            description="Testing ID column generation with isolation",
        )
        db.add(entity)
        db.commit()

        # Test that the ID was generated and is a valid UUID
        assert entity.id is not None
        assert isinstance(entity.id, str)
        assert len(entity.id) > 0

        # Verify we can retrieve the entity by its generated ID
        retrieved_entity = db.query(TestModel).filter_by(id=entity.id).first()
        assert retrieved_entity is not None
        assert retrieved_entity.name == "ID Column Test Entity"
    finally:
        db.close()


def test_timestamps_columns(mock_server):
    """Test the timestamp columns"""
    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    # Check created_at column
    created_at = TestModel.created_at
    assert created_at is not None

    # Check created_by_user_id column
    created_by = TestModel.created_by_user_id
    assert created_by is not None
    assert created_by.nullable is True


def test_user_can_create(test_user_id, mock_server):
    """Test the user_can_create method"""
    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    db = model_registry.DB.get_session()
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    try:
        # Test root user (always allowed)
        assert TestModel.user_can_create(ROOT_ID, db) is True

        # Test normal user - should work with proper permissions
        # For this test, we'll assume the user has basic create permissions
        try:
            result = TestModel.user_can_create(test_user_id, db)
            # Result should be boolean
            assert isinstance(result, bool)
        except HTTPException as e:
            # If permission is denied, that's also a valid test result
            assert e.status_code in [403, 404]
    finally:
        db.close()


def test_create_method(test_user_id, mock_server):
    """Test the create method"""
    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    db = model_registry.DB.get_session()
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    try:
        # Clear the table first
        db.query(TestModel).delete()
        db.commit()

        # Test with ROOT_ID (should always work)
        entity = TestModel.create(
            ROOT_ID, model_registry, name="Test Entity", description="Test Description"
        )

        # Check result (should be a dict by default)
        assert isinstance(entity, dict)
        assert entity["name"] == "Test Entity"
        assert entity["description"] == "Test Description"
        assert entity["created_by_user_id"] == ROOT_ID

        # Entity should be in database
        db_entity = db.query(TestModel).filter_by(name="Test Entity").first()
        assert db_entity is not None
        assert db_entity.description == "Test Description"

        # Test with different return types
        db_entity = TestModel.create(
            ROOT_ID, model_registry, return_type="db", name="DB Entity"
        )
        assert isinstance(db_entity, TestModel)
        assert db_entity.name == "DB Entity"

        # Test with fields parameter
        entity_with_fields = TestModel.create(
            ROOT_ID,
            model_registry,
            return_type="dict",
            fields=["name"],
            name="Fields Entity",
            description="Should not appear",
        )
        assert "name" in entity_with_fields
        assert "description" not in entity_with_fields
    finally:
        db.close()


def test_count_method(mock_server):
    """Test the count method of AbstractDatabaseEntity"""
    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    db = model_registry.DB.get_session()
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    try:
        # Clear the table to ensure test isolation
        db.query(TestModel).delete()
        db.commit()

        # Create test entities
        for i in range(3):
            entity = TestModel(
                name=f"Count Test Entity {i}",
                description=f"Test description {i}",
            )
            db.add(entity)
        db.commit()

        # Test count with ROOT_ID (can see all entities)
        count = TestModel.count(ROOT_ID, model_registry)
        assert count == 3
    finally:
        db.close()


def test_exists_method(test_user_id, mock_server):
    """Test the exists method"""
    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    db = model_registry.DB.get_session()
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    try:
        # Clear the table to ensure test isolation
        db.query(TestModel).delete()
        db.commit()

        # Create test entities with unique names
        entities = [
            TestModel(name="Exists Test Entity 1"),
            TestModel(name="Exists Test Entity 2"),
        ]
        db.add_all(entities)
        db.commit()

        # Test exists with ROOT_ID (can see all entities)
        exists1 = TestModel.exists(ROOT_ID, model_registry, name="Exists Test Entity 1")
        assert exists1 is True

        # Test exists with no matching record
        exists_none = TestModel.exists(ROOT_ID, model_registry, name="Does Not Exist")
        assert exists_none is False
    finally:
        db.close()


def test_get_method(test_user_id, mock_server):
    """Test the get method"""
    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    db = model_registry.DB.get_session()
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    try:
        # Clear the table first to ensure test isolation
        db.query(TestModel).delete()
        db.commit()

        # Create test entity
        entity = TestModel(name="Get Test Entity", description="Get Test Description")
        db.add(entity)
        db.commit()

        # Refresh entity to get ID
        db.refresh(entity)
        entity_id = entity.id

        # Test get with ROOT_ID (can access all entities)
        result = TestModel.get(ROOT_ID, model_registry, id=entity_id)

        assert isinstance(result, dict)
        assert result["name"] == "Get Test Entity"
        assert result["description"] == "Get Test Description"

        # Test get with non-existent ID - should raise HTTPException by default
        with pytest.raises(HTTPException) as exc_info:
            TestModel.get(ROOT_ID, model_registry, id="non-existent-id")

        assert exc_info.value.status_code == 404

        # Test get with non-existent ID and allow_nonexistent=True - should return None
        non_existent_result = TestModel.get(
            ROOT_ID,
            model_registry,
            id="non-existent-id",
            allow_nonexistent=True,
        )
        assert non_existent_result is None

        # Test get with fields
        result_with_fields = TestModel.get(
            ROOT_ID, model_registry, id=entity_id, fields=["name"]
        )

        assert isinstance(result_with_fields, dict)
        assert "name" in result_with_fields
        assert "description" not in result_with_fields
        assert result_with_fields["name"] == "Get Test Entity"
    finally:
        db.close()


def test_list_method(mock_server):
    """Test the list method of the entity"""
    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    db = model_registry.DB.get_session()
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    try:
        # Clear the table to ensure test isolation
        db.query(TestModel).delete()
        db.commit()

        # Create test entities with unique names
        entities = [
            TestModel(name=f"List Test Entity {i}", description=f"List description {i}")
            for i in range(5)
        ]
        db.add_all(entities)
        db.commit()

        # Test list with ROOT_ID (can see all entities)
        results = TestModel.list(ROOT_ID, model_registry)
        assert len(results) == 5

        # Test with filtering
        filtered_results = TestModel.list(
            ROOT_ID, model_registry, name="List Test Entity 1"
        )
        assert len(filtered_results) == 1
        assert filtered_results[0]["name"] == "List Test Entity 1"
    finally:
        db.close()


def test_update_method(test_user_id, mock_server):
    """Test the update method"""
    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    db = model_registry.DB.get_session()
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    try:
        # Clear the table first
        db.query(TestModel).delete()
        db.commit()

        # Create test entity
        entity = TestModel(name="Update Test", description="Original description")
        db.add(entity)
        db.commit()
        db.refresh(entity)
        entity_id = entity.id

        # Test update with ROOT_ID (can update all entities)
        updated = TestModel.update(
            ROOT_ID,
            model_registry,
            new_properties={
                "description": "Updated description",
                "user_id": "test_user",
            },
            id=entity_id,
        )

        # Verify the update worked
        assert updated["description"] == "Updated description"
        assert updated["user_id"] == "test_user"
        assert "updated_at" in updated
        assert updated["updated_by_user_id"] == ROOT_ID
    finally:
        db.close()


def test_delete_method(test_user_id, mock_server):
    """Test the delete method"""
    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    db = model_registry.DB.get_session()
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    try:
        # Clear the table first
        db.query(TestModel).delete()
        db.commit()

        # Create test entity
        entity = TestModel(name="Delete Test")
        db.add(entity)
        db.commit()
        db.refresh(entity)
        entity_id = entity.id

        # Delete the entity with ROOT_ID (can delete all entities)
        TestModel.delete(ROOT_ID, model_registry, id=entity_id)

        # After Item 75 the soft-delete filter applies at the DB layer for
        # every read; bypass it via include_deleted to confirm the row was
        # tombstoned rather than hard-deleted. The delete runs in its own
        # session (via @with_session), so expire_all() is required to read
        # the freshly-committed deleted_at value through this test session.
        from database.AbstractDatabaseEntity import include_deleted

        db.expire_all()
        with include_deleted(db):
            deleted_entity = db.query(TestModel).filter_by(id=entity_id).first()

        assert deleted_entity is not None  # Row should still be present
        assert deleted_entity.deleted_at is not None
    finally:
        db.close()


# Test error handling for DTO conversions
def test_dto_conversion_error_handling():
    """Test error handling in DTO conversions"""

    # Create a DTO type with type hints
    class TypedDTO:
        __annotations__ = {
            "id": str,
            "name": str,
            "count": int,
            "items": List[str],
            "optional_value": Optional[int],
        }

        def __init__(
            self, id=None, name=None, count=0, items=None, optional_value=None, **kwargs
        ):
            self.id = id
            self.name = name
            self.count = count
            self.items = items or []
            self.optional_value = optional_value

    # Test conversion with missing fields
    entity = {
        "id": "123",
        "name": "Test Entity",
        # Missing count, items, optional_value
    }

    result = db_to_return_type(entity, return_type="dto", dto_type=TypedDTO)
    assert isinstance(result, TypedDTO)
    assert result.id == "123"
    assert result.name == "Test Entity"
    assert result.count == 0  # Default value
    assert result.items == []  # Default value
    assert result.optional_value is None  # Default value


# Test for empty and None cases in utility functions
def test_utility_edge_cases(mock_server):
    """Test edge cases in utility functions"""
    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    db = model_registry.DB.get_session()
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    try:
        # Test db_to_return_type with None
        result = db_to_return_type(None, return_type="dict")
        assert result is None

        # Test db_to_return_type with empty list
        result = db_to_return_type([], return_type="dict")
        assert result == []

        # Test build_query with empty parameters
        query = build_query(db, TestModel)
        assert query is not None

        # Test build_query with empty lists instead of None
        query = build_query(
            db,
            TestModel,
            joins=[],
            options=[],
            filters=[],
            order_by=None,
            limit=None,
            offset=None,
        )
        assert query is not None
    finally:
        db.close()


def test_db_to_return_type_with_nested_objects(mock_server):
    """Test db_to_return_type properly converts nested objects to dictionaries."""
    # Get model registry from mock server
    model_registry = mock_server.app.state.model_registry
    db = model_registry.DB.get_session()
    TestModel = AbstractDbEntityTestModel.DB(model_registry.DB.Base)

    # Create all tables (including test table) if they don't exist
    model_registry.DB.Base.metadata.create_all(model_registry.DB.get_setup_engine())

    # Create mock SQLAlchemy-like objects with nested relationships
    class MockNestedUser:
        def __init__(self, user_id, name):
            self.id = user_id
            self.name = name
            self.email = f"{name.lower().replace(' ', '.')}@example.com"
            self._sa_instance_state = "should_be_ignored"

    class MockEntity:
        def __init__(self, entity_id, title):
            self.id = entity_id
            self.title = title
            self.user = MockNestedUser(f"user-{entity_id}", f"User {entity_id}")
            self.tags = ["tag1", "tag2"]
            self.metadata = {"key": "value", "nested": {"deep": "data"}}
            self._sa_instance_state = "should_be_ignored"

    # Create a mock entity with nested objects
    mock_entity = MockEntity("entity-123", "Test Entity")

    try:
        # Test conversion to dict - this is where the bug was occurring
        result = db_to_return_type(mock_entity, return_type="dict")

        # Verify the result is a dictionary
        assert isinstance(result, dict)
        assert result["id"] == "entity-123"
        assert result["title"] == "Test Entity"
        assert result["tags"] == ["tag1", "tag2"]

        # Verify nested user object is converted to dict (not left as object)
        assert isinstance(result["user"], dict)
        assert result["user"]["id"] == "user-entity-123"
        assert result["user"]["name"] == "User entity-123"
        assert result["user"]["email"] == "user.entity-123@example.com"

        # Verify metadata dict is preserved correctly
        assert isinstance(result["metadata"], dict)
        assert result["metadata"]["key"] == "value"
        assert isinstance(result["metadata"]["nested"], dict)
        assert result["metadata"]["nested"]["deep"] == "data"

        # Verify private attributes are excluded
        assert "_sa_instance_state" not in result
        assert "_sa_instance_state" not in result["user"]

        # Test with list of entities
        entities = [
            MockEntity("entity-1", "Entity One"),
            MockEntity("entity-2", "Entity Two"),
        ]

        result_list = db_to_return_type(entities, return_type="dict")

        # Verify list structure
        assert isinstance(result_list, list)
        assert len(result_list) == 2

        # Verify each entity is converted properly
        for i, entity_dict in enumerate(result_list, 1):
            assert isinstance(entity_dict, dict)
            assert entity_dict["id"] == f"entity-{i}"
            assert entity_dict["title"] == f"Entity {'One' if i == 1 else 'Two'}"

            # Verify nested user is converted to dict
            assert isinstance(entity_dict["user"], dict)
            assert entity_dict["user"]["id"] == f"user-entity-{i}"
            assert entity_dict["user"]["name"] == f"User entity-{i}"

        # Test with fields parameter - should work with nested objects
        result_with_fields = db_to_return_type(
            mock_entity, return_type="dict", fields=["id", "title", "user"]
        )

        assert isinstance(result_with_fields, dict)
        assert len(result_with_fields) == 3  # Only specified fields
        assert "id" in result_with_fields
        assert "title" in result_with_fields
        assert "user" in result_with_fields
        assert "tags" not in result_with_fields  # Should be excluded
        assert "metadata" not in result_with_fields  # Should be excluded

        # User should still be converted to dict even with field filtering
        assert isinstance(result_with_fields["user"], dict)
        assert result_with_fields["user"]["id"] == "user-entity-123"

    finally:
        db.close()


def test_reference_mixin_generation(mock_server):
    """Test that reference mixins are generated correctly."""
    from sqlalchemy import Column, String

    from database.AbstractDatabaseEntity import BaseMixin

    # Use the mock server's database base for consistency
    model_registry = mock_server.app.state.model_registry
    Base = model_registry.DB.Base

    # Create a test entity
    class TestRefEntity(Base, BaseMixin):
        __tablename__ = "test_abstract_db_ref_entities"
        name = Column(String)

    # Generate a reference mixin
    TestRefEntityRefMixin = create_reference_mixin(TestRefEntity)

    # Create a test model using the mixin
    class RefMockTestModel(Base, BaseMixin, TestRefEntityRefMixin):
        __tablename__ = "test_abstract_db_ref_models"
        name = Column(String)

    # Verify the model has the correct attributes
    assert hasattr(RefMockTestModel, "testrefentity_id")
    assert hasattr(RefMockTestModel, "testrefentity")

    # Create an optional test model
    class OptionalTestRefModel(Base, BaseMixin, TestRefEntityRefMixin.Optional):
        __tablename__ = "test_abstract_db_optional_ref_models"
        name = Column(String)

    # Check class names
    assert TestRefEntityRefMixin.__name__ == "TestRefEntityRefMixin"
    assert TestRefEntityRefMixin.Optional.__name__ == "_TestRefEntityOptional"

    # Verify the foreign key is created correctly
    col = RefMockTestModel.__table__.columns.testrefentity_id
    opt_col = OptionalTestRefModel.__table__.columns.testrefentity_id

    # Check nullability
    assert col.nullable == False
    assert opt_col.nullable == True


def test_reference_mixin_customization(mock_server):
    """Test that reference mixins can be customized."""
    from sqlalchemy import Column, String

    from database.AbstractDatabaseEntity import BaseMixin

    # Use the mock server's database base for consistency
    model_registry = mock_server.app.state.model_registry
    Base = model_registry.DB.Base

    class CustomRefEntity(Base, BaseMixin):
        __tablename__ = "test_abstract_db_custom_ref_entities"
        name = Column(String)

    # Generate a customized reference mixin with a unique backref name
    CustomRefMixin = create_reference_mixin(
        CustomRefEntity,
        comment="Custom reference",
        backref_name="custom_backref_models",
        nullable=True,
    )

    # Create a test model using the mixin
    class CustomRefModel(Base, BaseMixin, CustomRefMixin):
        __tablename__ = "test_abstract_db_custom_ref_models"
        name = Column(String)

    # Verify customizations
    assert hasattr(CustomRefModel, "customrefentity_id")
    assert hasattr(CustomRefModel, "customrefentity")

    # Check nullable is respected for the main mixin
    col = CustomRefModel.__table__.columns.customrefentity_id
    assert col.nullable == True


def test_practical_usage_example(mock_server):
    """
    Test a practical usage example for the reference mixin generator.
    """
    from sqlalchemy import Column, String

    from database.AbstractDatabaseEntity import BaseMixin

    # Use the mock server's database base for consistency
    model_registry = mock_server.app.state.model_registry
    Base = model_registry.DB.Base

    # Define example entity classes
    class Project(Base, BaseMixin):
        __tablename__ = "test_abstract_db_projects"
        name = Column(String, nullable=False)
        description = Column(String)

    class Task(Base, BaseMixin):
        __tablename__ = "test_abstract_db_tasks"
        title = Column(String, nullable=False)
        description = Column(String)
        status = Column(String, default="pending")

    # Create reference mixins with distinct backref names
    ProjectRefMixin = create_reference_mixin(
        Project, backref_name="project_assignments"
    )
    TaskRefMixin = create_reference_mixin(Task, backref_name="task_assignments")

    # Example usage in a composite entity
    class TaskAssignment(Base, BaseMixin, ProjectRefMixin, TaskRefMixin.Optional):
        """
        A composite entity that demonstrates using multiple reference mixins.
        - Required project reference
        - Optional task reference
        """

        __tablename__ = "test_abstract_db_task_assignments"
        priority = Column(String, default="medium")

    # Verify the structure
    # Task assignment must have a project
    assert hasattr(TaskAssignment, "project_id")
    assert TaskAssignment.__table__.columns.project_id.nullable == False

    # Task assignment can have an optional task
    assert hasattr(TaskAssignment, "task_id")
    assert TaskAssignment.__table__.columns.task_id.nullable == True

    # Check relationships
    assert hasattr(TaskAssignment, "project")
    assert hasattr(TaskAssignment, "task")
