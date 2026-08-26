# Add missing test model definitions and fixtures before the test classes
import asyncio
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field, create_model

# All static artifacts have been replaced with instance-based approaches
# Tests now use GraphQLManager and SchemaManager instances
from zephyrex.lib.AbstractPydantic2Test import AbstractPydanticTestMixin
from zephyrex.pydantic2.registry import ModelRegistry
from zephyrex.pydantic2.strawberry import (
    ANY_SCALAR,
    DICT_SCALAR,
    LIST_SCALAR,
    TYPE_MAPPING,
    DateScalar,
    DateTimeScalar,
    GraphQLManager,
    ModelInfo,
    enum_serializer,
    convert_field_name,
)


# Test models that are referenced throughout the tests
class MockTestModel(BaseModel):
    id: str = Field(..., description="Unique identifier")
    name: str = Field(..., description="Name field")
    description: Optional[str] = Field(None, description="Optional description")
    ref_model_id: Optional[str] = Field(None, description="Reference to another model")
    items: List["MockTestRefModel"] = Field(
        default_factory=list, description="List of items"
    )
    created_at: datetime = Field(
        default_factory=datetime.now, description="Creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=datetime.now, description="Update timestamp"
    )
    meta: Dict[str, Any] = Field(default_factory=dict, description="Metadata")

    class Create(BaseModel):
        name: str = Field(..., description="Name field")
        description: Optional[str] = Field(None, description="Optional description")
        ref_model_id: Optional[str] = Field(
            None, description="Reference to another model"
        )
        meta: Dict[str, Any] = Field(default_factory=dict, description="Metadata")

    class Update(BaseModel):
        name: Optional[str] = Field(None, description="Name field")
        description: Optional[str] = Field(None, description="Optional description")
        ref_model_id: Optional[str] = Field(
            None, description="Reference to another model"
        )
        meta: Optional[Dict[str, Any]] = Field(None, description="Metadata")


class MockTestRefModel(BaseModel):
    id: str = Field(..., description="Unique identifier")
    name: str = Field(..., description="Name field")
    created_at: datetime = Field(
        default_factory=datetime.now, description="Creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=datetime.now, description="Update timestamp"
    )


class MockTestNetworkModel(BaseModel):
    class POST(BaseModel):
        test: MockTestModel.Create = Field(..., description="Test model data")

    class PUT(BaseModel):
        test: MockTestModel.Update = Field(..., description="Test model data")

    class SEARCH(BaseModel):
        test: Optional[str] = Field(None, description="Search term")

    class Response(BaseModel):
        data: List[MockTestModel] = Field(..., description="Response data")


# Additional test models for parent-child relationships
class ParentModel(BaseModel):
    id: str = Field(..., description="Unique identifier")
    name: str = Field(..., description="Name field")
    children: List["ChildModel"] = Field(
        default_factory=list, description="Child models"
    )


class ChildModel(BaseModel):
    id: str = Field(..., description="Unique identifier")
    name: str = Field(..., description="Name field")
    parent_id: Optional[str] = Field(None, description="Parent model ID")


# Mock manager classes
class MockTestManager:
    async def get(self, id: str, context: dict) -> MockTestModel:
        return MockTestModel(id=id, name="Test")  # type: ignore[call-arg]

    async def list(self, search_params: dict, context: dict) -> List[MockTestModel]:
        return [MockTestModel(id="1", name="Test1")]  # type: ignore[call-arg]

    async def create(self, data: dict, context: dict) -> MockTestModel:
        return MockTestModel(id="new", **data)

    async def update(self, id: str, data: dict, context: dict) -> MockTestModel:
        return MockTestModel(id=id, **data)

    async def delete(self, id: str, context: dict) -> MockTestModel:
        return MockTestModel(id=id, name="Deleted")  # type: ignore[call-arg]


class ParentManager:
    async def get(self, id: str, context: dict) -> ParentModel:
        return ParentModel(id=id, name="Parent")

    async def list(self, search_params: dict, context: dict) -> List[ParentModel]:
        return [ParentModel(id="1", name="Parent1")]

    async def create(self, data: dict, context: dict) -> ParentModel:
        return ParentModel(id="new", **data)

    async def update(self, id: str, data: dict, context: dict) -> ParentModel:
        return ParentModel(id=id, **data)

    async def delete(self, id: str, context: dict) -> ParentModel:
        return ParentModel(id=id, name="Deleted")


# ---------------------------------------------------------------------------
# Real-registry helpers.
#
# A ``MagicMock(spec=ModelRegistry)`` lets the emitter run but produces only the
# placeholder Query/Mutation, so a "schema is not None" assertion cannot fail on
# a broken emitter. These helpers build a schema over a *real* bound registry
# whose Query/Mutation actually carry the model's operations, so the emitter
# tests can assert the concrete emitted fields (mirrors
# ``test_schema_includes_query_fields_when_registry_binding_missing``).
# ---------------------------------------------------------------------------


class _BoundMockManager:
    """A manager the GraphQL emitter can introspect (carries ``BaseModel``)."""

    BaseModel = MockTestModel

    def __init__(self, requester_id: str, model_registry: Any):
        self.requester_id = requester_id
        self.model_registry = model_registry

    def get(
        self,
        id: str,
        include: Optional[Any] = None,
        fields: Optional[Any] = None,
    ) -> MockTestModel:
        return MockTestModel(id=id, name="Bound")  # type: ignore[call-arg]

    def list(
        self,
        offset: int = 0,
        limit: int = 100,
        include: Optional[Any] = None,
        fields: Optional[Any] = None,
        **kwargs: Any,
    ) -> List[MockTestModel]:
        return [MockTestModel(id="1", name="Bound")]  # type: ignore[call-arg]

    def create(self, **data: Any) -> MockTestModel:
        return MockTestModel(id="created", name=data.get("name", "Created"))  # type: ignore[call-arg]

    def update(self, id: str, **data: Any) -> MockTestModel:
        return MockTestModel(id=id, name=data.get("name", "Updated"))  # type: ignore[call-arg]

    def delete(self, id: str) -> bool:
        return True


class _RealMockRegistry:
    """A minimal real registry the emitter drives to generate the model's ops."""

    def __init__(self) -> None:
        # Annotated as Any so subclasses can bind their own model set without a
        # variance conflict on the inferred list element types.
        self.bound_models: List[Any] = [MockTestModel]
        self.model_relationships: List[Any] = [
            (MockTestModel, MockTestRefModel, MockTestNetworkModel, _BoundMockManager)
        ]

    def apply(self, model: Any) -> Any:
        raise TypeError("No matching type found in registry")


def _real_schema_manager() -> "GraphQLManager":
    """A GraphQLManager whose emitted schema carries MockTestModel's operations."""
    return GraphQLManager(_RealMockRegistry())


def _query_field_names(schema: Any) -> List[str]:
    return [field.name for field in schema.get_type_by_name("Query").fields]


def _mutation_field_names(schema: Any) -> List[str]:
    mutation = schema.get_type_by_name("Mutation")
    return [field.name for field in mutation.fields] if mutation else []


def _enum_value_names(gql_type: Any) -> List[str]:
    """Return the value names of an emitted Strawberry enum type."""
    definition = getattr(gql_type, "_type_definition", None) or getattr(
        gql_type, "__strawberry_definition__", None
    )
    assert definition is not None, f"not a Strawberry enum type: {gql_type!r}"
    return [value.name for value in definition.values]


# Abstract test mixin for GraphQL tests
class AbstractGraphQLTestMixin(AbstractPydanticTestMixin):
    """Base test mixin for GraphQL-related tests"""

    def get_test_generator(self):
        """Get a test SchemaManager instance with minimal setup"""
        registry = ModelRegistry()
        registry.bind(MockTestModel)
        registry.bind(MockTestRefModel)
        registry._locked = True
        registry.model_relationships = [
            (MockTestModel, MockTestRefModel, MockTestNetworkModel, MockTestManager)
        ]
        return GraphQLManager(registry)


class TestSchemaManager(AbstractPydanticTestMixin):
    """Test the SchemaManager (GraphQLManager) functionality"""

    def test_initialize_schema_manager(self):
        """Test that SchemaManager initializes correctly with ModelRegistry"""
        # Create mock registry
        mock_registry = MagicMock(spec=ModelRegistry)
        mock_registry.utility = MagicMock()

        # Create schema manager
        schema_manager = GraphQLManager(mock_registry)

        # Verify initialization
        assert schema_manager.model_registry == mock_registry
        assert schema_manager.broadcast is not None
        assert hasattr(schema_manager, "filter_generator")
        assert hasattr(schema_manager, "type_generator")
        assert hasattr(schema_manager, "resolver_generator")
        assert schema_manager._query_fields == {}
        assert schema_manager._mutation_fields == {}
        assert schema_manager._subscription_fields == {}

    def test_filter_type_generator(self):
        """Test filter type generation via GraphQLManager"""
        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)

        # Since FilterTypeGenerator is now internal, test via GraphQLManager
        assert hasattr(schema_manager, "filter_generator")

        # Test that filter generator exists and is functional
        if hasattr(schema_manager.filter_generator, "create_string_filter"):
            string_filter = schema_manager.filter_generator.create_string_filter()
            assert string_filter is not None

    def test_type_generator(self):
        """Test type generation via GraphQLManager"""
        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)

        # Since TypeGenerator is now internal, test via GraphQLManager
        assert hasattr(schema_manager, "type_generator")

        # With no model info the type generator yields nothing...
        if hasattr(schema_manager.type_generator, "create_type_for_model"):
            gql_type = schema_manager.type_generator.create_type_for_model(
                MockTestModel
            )
            assert gql_type is None

        # ...but driven by a real bound registry it emits the model's object
        # type. Assert the concrete emitted type is registered.
        real_manager = _real_schema_manager()
        real_manager.create_schema()
        assert MockTestModel in real_manager._type_registry

    def test_batch_result_generator(self):
        """Test batch result generation via GraphQLManager"""
        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)

        # Since BatchResultGenerator is now internal, test via GraphQLManager
        assert hasattr(schema_manager, "batch_result_generator") or hasattr(
            schema_manager, "_batch_result_generator"
        )

        # Drive a real registry so the schema carries the model's operations,
        # not just the placeholder Query. An empty/placeholder schema fails here.
        schema = _real_schema_manager().create_schema()
        query_fields = _query_field_names(schema)
        assert "mockTest" in query_fields
        assert "mockTests" in query_fields

    @pytest.mark.asyncio
    async def test_resolver_generator(self):
        """Test resolver generation via GraphQLManager"""
        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)

        # Since ResolverGenerator is now internal, test via GraphQLManager
        assert hasattr(schema_manager, "resolver_generator")

        # Test that resolver generator exists and is functional
        if hasattr(schema_manager.resolver_generator, "create_get_resolver"):
            get_resolver = schema_manager.resolver_generator.create_get_resolver(
                "TestModel", MockTestManager
            )
            assert callable(get_resolver)

        # Driven by a real registry, the resolvers wire into a schema whose Query
        # exposes the model's get/list fields -- proving resolver generation
        # actually produced operations, not an empty placeholder schema.
        schema = _real_schema_manager().create_schema()
        query_fields = _query_field_names(schema)
        assert "mockTest" in query_fields
        assert "mockTests" in query_fields

    def test_schema_creation_with_models(self):
        """Test schema creation emits the model's query fields and CRUD mutations."""
        schema = _real_schema_manager().create_schema()

        query_fields = _query_field_names(schema)
        assert "mockTest" in query_fields
        assert "mockTests" in query_fields

        mutation_fields = _mutation_field_names(schema)
        assert "createMockTest" in mutation_fields
        assert "updateMockTest" in mutation_fields
        assert "deleteMockTest" in mutation_fields

    def test_schema_includes_query_fields_when_registry_binding_missing(self):
        """GraphQL schema should expose query fields even without registry bindings."""

        class DummyManager:
            BaseModel = MockTestModel

            def __init__(self, requester_id: str, model_registry: Any):
                self.requester_id = requester_id
                self.model_registry = model_registry

            def get(
                self,
                id: str,
                include: Optional[Any] = None,
                fields: Optional[Any] = None,
            ) -> MockTestModel:
                return MockTestModel(id=id, name="Dummy")  # type: ignore[call-arg]

            def list(
                self,
                offset: int = 0,
                limit: int = 100,
                include: Optional[Any] = None,
                fields: Optional[Any] = None,
                **kwargs: Any,
            ) -> List[MockTestModel]:
                return [MockTestModel(id="1", name="Dummy")]  # type: ignore[call-arg]

            def create(self, **data: Any) -> MockTestModel:
                return MockTestModel(id="created", name=data.get("name", "Created"))  # type: ignore[call-arg]

            def update(self, id: str, **data: Any) -> MockTestModel:
                return MockTestModel(id=id, name=data.get("name", "Updated"))  # type: ignore[call-arg]

            def delete(self, id: str) -> bool:
                return True

        class DummyRegistry:
            def __init__(self) -> None:
                self.bound_models = [MockTestModel]
                self.model_relationships = [
                    (
                        MockTestModel,
                        MockTestRefModel,
                        MockTestNetworkModel,
                        DummyManager,
                    )
                ]

            def apply(self, model: Any) -> Any:
                raise TypeError("No matching type found in registry")

        schema_manager = GraphQLManager(DummyRegistry())
        schema = schema_manager.create_schema()

        query_fields = [field.name for field in schema.get_type_by_name("Query").fields]

        assert "mockTest" in query_fields
        assert "mockTests" in query_fields
        assert MockTestModel in schema_manager._type_registry

    def test_reuses_existing_type_for_duplicate_model_objects(self):
        """Ensure duplicate class objects reuse existing GraphQL types"""
        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)

        first_model = create_model(
            "DuplicateGraphQLModel",
            __base__=BaseModel,
            __module__="zephyrex.pydantic2.strawberry_test",
            id=(str, Field(..., description="Primary identifier")),
        )

        second_model = create_model(
            "DuplicateGraphQLModel",
            __base__=BaseModel,
            __module__="zephyrex.pydantic2.strawberry_test",
            id=(str, Field(..., description="Replacement identifier")),
        )

        first_type = schema_manager._create_gql_type_from_model(first_model)
        second_type = schema_manager._create_gql_type_from_model(second_model)

        assert second_type is first_type
        assert schema_manager._type_registry[second_model] is first_type

    def test_graphql_manager_wrapper(self):
        """Test GraphQLManager wrapper class"""
        mock_registry = MagicMock(spec=ModelRegistry)

        graphql_manager = GraphQLManager(mock_registry)
        assert isinstance(graphql_manager, GraphQLManager)
        assert hasattr(graphql_manager, "create_schema")
        assert callable(graphql_manager.create_schema)

    def test_schema_creation(self):
        """An empty registry still yields a queryable schema (placeholder Query)."""
        mock_registry = MagicMock(spec=ModelRegistry)
        mock_registry.model_relationships = []

        schema_manager = GraphQLManager(mock_registry)
        schema = schema_manager.create_schema()

        # Even with no models the schema must expose a valid, non-empty Query
        # type (Strawberry rejects an empty Query), so a broken create_schema
        # that returned an unusable schema fails here.
        query = schema.get_type_by_name("Query")
        assert query is not None
        assert len(query.fields) >= 1

    def test_input_type_conversion(self):
        """Test input type generation and conversion"""
        mock_registry = MagicMock(spec=ModelRegistry)

        schema_manager = GraphQLManager(mock_registry)

        # Test create input type generation
        create_type = schema_manager._get_input_type(MockTestModel, "Create")
        assert create_type is not None

        # Test update input type generation
        update_type = schema_manager._get_input_type(MockTestModel, "Update")
        assert update_type is not None

    def test_filter_conversion(self):
        """Test filter to search params conversion"""
        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)
        resolver_gen = schema_manager.resolver_generator

        # Test with None filter object
        search_params = resolver_gen._convert_filter_to_search_params(None)
        assert search_params == {}

        # Test with empty filter object
        class EmptyFilter:
            pass

        empty_filter = EmptyFilter()
        search_params = resolver_gen._convert_filter_to_search_params(empty_filter)
        assert search_params == {}

    def test_nested_data_extraction(self):
        """Test nested data extraction for relationships"""
        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)
        resolver_gen = schema_manager.resolver_generator

        # Test data with nested relationships
        data_dict = {
            "name": "Test",
            "description": "Test description",
            "related_items": [{"name": "Item1"}, {"name": "Item2"}],
            "parent_ref": {"name": "Parent"},
        }

        nested_data = resolver_gen._extract_nested_data(data_dict)

        # Should extract nested relationships
        assert "related_items" in nested_data
        assert "parent_ref" in nested_data

        # Main data should only contain non-nested fields
        assert "name" in data_dict
        assert "description" in data_dict
        assert "related_items" not in data_dict
        assert "parent_ref" not in data_dict


class TestUtilityFunctions(AbstractPydanticTestMixin):
    """Test utility functions used throughout the GraphQL system"""

    def test_enum_serializer(self):
        """Test enum serialization function"""

        # Test with enum-like object with name
        class MockEnum:
            name = "TEST_VALUE"

        result = enum_serializer(MockEnum())
        assert result == "TEST_VALUE"

        # Test with enum-like object with value
        class MockEnumValue:
            value = "test_value"

        result = enum_serializer(MockEnumValue())
        assert result == "test_value"

        # Test with regular string
        result = enum_serializer("regular_string")
        assert result == "regular_string"

    def test_convert_field_name(self):
        """Test field name conversion to camelCase"""
        # Test regular field name
        assert convert_field_name("field_name") == "fieldName"

        # Test special fields that shouldn't be converted
        assert convert_field_name("id") == "id"
        assert convert_field_name("__typename") == "__typename"

        # Test None input
        assert convert_field_name(None) is None

    def test_scalar_type_detection(self):
        """Test scalar type detection via TYPE_MAPPING"""
        # Test basic scalar types are in TYPE_MAPPING
        assert str in TYPE_MAPPING
        assert int in TYPE_MAPPING
        assert float in TYPE_MAPPING
        assert bool in TYPE_MAPPING


class TestScalarTypes(AbstractPydanticTestMixin):
    """Test GraphQL scalar type definitions"""

    def test_scalar_type_definitions(self):
        """Test that scalar types are properly defined"""
        assert ANY_SCALAR is not None
        assert DICT_SCALAR is not None
        assert LIST_SCALAR is not None
        assert DateTimeScalar is not None
        assert DateScalar is not None

    def test_type_mapping(self):
        """Test that type mapping contains expected types"""
        assert str in TYPE_MAPPING
        assert int in TYPE_MAPPING
        assert float in TYPE_MAPPING
        assert bool in TYPE_MAPPING
        assert datetime in TYPE_MAPPING
        assert TYPE_MAPPING[datetime] == DateTimeScalar


class TestModelInfo(AbstractPydanticTestMixin):
    """Test ModelInfo dataclass functionality"""

    def test_model_info_creation(self):
        """Test creating ModelInfo instances"""
        model_info = ModelInfo(
            model_class=MockTestModel,
            ref_model_class=MockTestRefModel,
            network_model_class=MockTestNetworkModel,
            manager_class=MockTestManager,
            plural_name="tests",
            singular_name="test",
        )

        assert model_info.model_class == MockTestModel
        assert model_info.ref_model_class == MockTestRefModel
        assert model_info.network_model_class == MockTestNetworkModel
        assert model_info.manager_class == MockTestManager
        assert model_info.plural_name == "tests"
        assert model_info.singular_name == "test"


class TestIntegrationWithModelRegistry(AbstractGraphQLTestMixin):
    """Test integration with actual ModelRegistry instances"""

    def test_with_real_model_registry(self):
        """Test GraphQLManager with real ModelRegistry"""
        # Create real registry
        registry = ModelRegistry()
        registry.bind(MockTestModel)
        registry.bind(MockTestRefModel)
        registry._locked = True
        registry.model_relationships = [
            (MockTestModel, MockTestRefModel, MockTestNetworkModel, MockTestManager)
        ]

        # Create GraphQLManager
        graphql_manager = GraphQLManager(registry)

        # Verify it uses the registry's utility and data
        assert graphql_manager.model_registry == registry

    def test_no_global_state_pollution(self):
        """Different manager instances do not share emitted type state."""

        # A second, independently-bound model + manager.
        class IsolatedModel(BaseModel):
            id: str = Field(..., description="ID")
            tag: str = Field(..., description="Tag")

            class Create(BaseModel):
                tag: str

            class Update(BaseModel):
                tag: Optional[str] = None

        class IsolatedManager(_BoundMockManager):
            BaseModel = IsolatedModel

            def get(self, id, include=None, fields=None):  # type: ignore[override]
                return IsolatedModel(id=id, tag="t")

            def list(self, offset=0, limit=100, include=None, fields=None, **kwargs):  # type: ignore[override]
                return [IsolatedModel(id="1", tag="t")]

        class IsolatedRegistry(_RealMockRegistry):
            def __init__(self) -> None:
                self.bound_models = [IsolatedModel]
                self.model_relationships = [
                    (IsolatedModel, IsolatedModel, None, IsolatedManager)
                ]

        manager1 = _real_schema_manager()
        manager2 = GraphQLManager(IsolatedRegistry())

        # Build both schemas, then prove emitted types do NOT bleed across
        # managers: manager1 knows MockTestModel, manager2 knows IsolatedModel,
        # and neither sees the other's. Two distinct-object registries would pass
        # the old `!=` check trivially; this fails if generation mutated shared
        # type state.
        manager1.create_schema()
        manager2.create_schema()

        assert MockTestModel in manager1._type_registry
        assert MockTestModel not in manager2._type_registry
        assert IsolatedModel in manager2._type_registry
        assert IsolatedModel not in manager1._type_registry


class TestProgrammaticSchemaGeneration(AbstractGraphQLTestMixin):
    """Test that schemas are generated entirely programmatically"""

    def test_no_hardcoded_types(self):
        """Types are emitted from the registered model, not hardcoded."""

        # A model whose field name appears nowhere in the emitter source: if the
        # schema exposes it, the type was generated from the model, proving the
        # emitter is not returning a fixed/hardcoded type set.
        class DynamicModel(BaseModel):
            id: str = Field(..., description="ID")
            dynamic_field: str = Field(..., description="Dynamic field")

            class Create(BaseModel):
                dynamic_field: str

            class Update(BaseModel):
                dynamic_field: Optional[str] = None

        class DynamicManager(_BoundMockManager):
            BaseModel = DynamicModel

            def get(self, id, include=None, fields=None):  # type: ignore[override]
                return DynamicModel(id=id, dynamic_field="x")

            def list(self, offset=0, limit=100, include=None, fields=None, **kwargs):  # type: ignore[override]
                return [DynamicModel(id="1", dynamic_field="x")]

        class DynamicRegistry(_RealMockRegistry):
            def __init__(self) -> None:
                self.bound_models = [DynamicModel]
                self.model_relationships = [
                    (DynamicModel, DynamicModel, None, DynamicManager)
                ]

        schema = GraphQLManager(DynamicRegistry()).create_schema()

        # The dynamically-named query field must be present -- a hardcoded type
        # set could never contain "dynamic" (the "Model" suffix is stripped and
        # the name camelCased, as MockTestModel -> mockTest).
        query_fields = _query_field_names(schema)
        assert "dynamic" in query_fields, query_fields
        assert "dynamics" in query_fields, query_fields

    def test_comprehensive_operation_generation(self):
        """Every CRUD operation is generated for the registered model."""
        schema = _real_schema_manager().create_schema()

        query_fields = _query_field_names(schema)
        mutation_fields = _mutation_field_names(schema)

        # get + list on Query, create/update/delete on Mutation -- the full CRUD
        # surface, not merely that a query/mutation attribute exists (which is
        # true for any Strawberry schema).
        assert "mockTest" in query_fields, query_fields
        assert "mockTests" in query_fields, query_fields
        assert "createMockTest" in mutation_fields, mutation_fields
        assert "updateMockTest" in mutation_fields, mutation_fields
        assert "deleteMockTest" in mutation_fields, mutation_fields


class TestEnumHandling(AbstractPydanticTestMixin):
    """Test enum handling in GraphQL conversion"""

    def test_string_enum_conversion(self):
        """Test that string-based enums are converted correctly"""

        # Create a string-based enum like ConversationVisibility
        class TestStringEnum(str, Enum):
            OPTION_A = "option_a"
            OPTION_B = "option_b"
            OPTION_C = "option_c"

        # Create a model with the string enum
        class TestModelWithStringEnum(BaseModel):
            status: TestStringEnum = Field(..., description="Status field")
            optional_status: Optional[TestStringEnum] = Field(
                None, description="Optional status"
            )

        # Create schema manager
        mock_registry = MagicMock(spec=ModelRegistry)
        mock_registry.model_relationships = [
            (TestModelWithStringEnum, MagicMock(), MagicMock(), MagicMock())
        ]

        schema_manager = GraphQLManager(mock_registry)

        # A string-based enum must convert to a real GraphQL enum carrying its
        # members -- not silently fall back to the String scalar (which is what
        # a `type(name, (Enum,), dict)` build did before the emitter fix).
        gql_type = schema_manager._convert_python_type_to_gql(TestStringEnum)
        assert gql_type is not TYPE_MAPPING[str], "string enum fell back to String"
        assert set(_enum_value_names(gql_type)) == {
            "OPTION_A",
            "OPTION_B",
            "OPTION_C",
        }

        # Create schema - should not fail
        schema = schema_manager.create_schema()
        assert schema is not None

    def test_regular_enum_conversion(self):
        """Test that regular enums are converted correctly"""

        # Create a regular enum like ChainRunStatus
        class TestRegularEnum(Enum):
            PENDING = "pending"
            RUNNING = "running"
            COMPLETED = "completed"
            FAILED = "failed"

        # Create a model with the regular enum
        class TestModelWithRegularEnum(BaseModel):
            state: TestRegularEnum = Field(..., description="State field")
            optional_state: Optional[TestRegularEnum] = Field(
                None, description="Optional state"
            )

        # Create schema manager
        mock_registry = MagicMock(spec=ModelRegistry)
        mock_registry.model_relationships = [
            (TestModelWithRegularEnum, MagicMock(), MagicMock(), MagicMock())
        ]

        schema_manager = GraphQLManager(mock_registry)

        # A regular enum converts to a GraphQL enum carrying its four members.
        gql_type = schema_manager._convert_python_type_to_gql(TestRegularEnum)
        assert gql_type is not TYPE_MAPPING[str]
        assert set(_enum_value_names(gql_type)) == {
            "PENDING",
            "RUNNING",
            "COMPLETED",
            "FAILED",
        }

        # Create schema - should not fail
        schema = schema_manager.create_schema()
        assert schema is not None

    def test_extension_enum_conversion(self):
        """Test that enums from extensions get prefixed correctly"""

        # Create an enum that simulates being from an extension
        class ExtensionEnum(str, Enum):
            STATE_A = "state_a"
            STATE_B = "state_b"

        # Simulate it being from an extension module
        ExtensionEnum.__module__ = "zephyrex.extensions.test_extension.models"

        # Create schema manager
        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)

        # A str-based extension enum converts to a real GraphQL enum carrying its
        # members (not the String fallback). NOTE: the extension-name *prefix*
        # (module_parts[0] == "extensions") never fires for real
        # `zephyrex.extensions.*` modules -- that dead-branch fix belongs to the
        # emitter-DRY/introspection-SSOT work in #225, not this test-hardening
        # pass; here we pin the conversion + member contract.
        gql_type = schema_manager._convert_python_type_to_gql(ExtensionEnum)
        assert gql_type is not TYPE_MAPPING[str]
        assert set(_enum_value_names(gql_type)) == {"STATE_A", "STATE_B"}

    def test_problematic_enum_fallback(self, monkeypatch):
        """When enum conversion raises, the emitter falls back to the String scalar."""

        class TestEnumWithIssue(str, Enum):
            VALUE1 = "value1"
            VALUE2 = "value2"

        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)

        # Force the actual failure path: make Strawberry's enum builder raise, so
        # the emitter's `except -> TYPE_MAPPING[str]` fallback is exercised. The
        # contract is a concrete one -- the returned type IS the String scalar,
        # not merely "not None".
        import zephyrex.pydantic2.strawberry.manager as manager_module

        def _boom(*args, **kwargs):
            raise RuntimeError("enum build failed")

        monkeypatch.setattr(manager_module.strawberry, "enum", _boom)

        gql_type = schema_manager._convert_python_type_to_gql(TestEnumWithIssue)
        assert gql_type is TYPE_MAPPING[str]


# ----------------------------------------------------------------------
# Security: explicit-deny / negative-path GraphQL tests.
#
# These run against the live `server` fixture so they exercise the actual
# Strawberry schema, not a hand-rolled mock.  Most are EXPECTED FAIL today
# because Pydantic2Strawberry.py:250 builds the schema with no depth or
# introspection limits.  Surfacing the gaps is the point.
# ----------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.gql
class TestGraphQLDenyPaths:
    """Negative-path tests for the auto-generated Strawberry schema."""

    GQL_PATH = "/graphql"

    def _post(self, server, body, headers=None):
        return server.post(self.GQL_PATH, json={"query": body}, headers=headers or {})

    def test_unauthenticated_query_rejected(self, server):
        """A query without an Authorization header must not leak data.

        Acceptable outcomes:
          - 401/403 from the transport layer
          - 200 with `errors` (resolver-level rejection)
          - 200 with empty/None data (permission-filtered to nothing)
        """
        response = self._post(server, "{ users { id email } }")
        if response.status_code == 200:
            body = response.json()
            data = body.get("data") or {}
            # Any non-empty top-level value indicates a leak.
            non_empty = [k for k, v in data.items() if v not in (None, [], {}, "")]
            assert "errors" in body or not non_empty, (
                f"Unauthenticated GraphQL query must not return data; "
                f"got non-empty fields {non_empty}"
            )
        else:
            assert response.status_code in (
                401,
                403,
            ), f"Got {response.status_code}"

    def test_introspection_disabled_in_production(self, server, monkeypatch):
        """`__schema` introspection must be disabled when ENVIRONMENT=production.

        The ``server`` fixture is session-scoped, so the running schema was
        built with the test-time ``ENVIRONMENT=local``. Build a *fresh*
        schema under production and validate against it directly — that is
        the production code path being audited.
        """
        from graphql import parse, validate

        monkeypatch.setenv("ENVIRONMENT", "production")
        registry = server.app.state.model_registry
        prod_schema = GraphQLManager(registry).create_schema()
        # The validation rules attached by GraphQLManager in production
        # include ``NoSchemaIntrospectionCustomRule``; ``validate`` will
        # surface the violation.
        from strawberry.schema.config import StrawberryConfig  # noqa: F401

        document = parse("{ __schema { types { name } } }")
        try:
            errors = validate(prod_schema._schema, document)
        except Exception:
            errors = []
        if not errors:
            # Fall back to executing the query and asserting no schema data.
            result = prod_schema.execute_sync("{ __schema { types { name } } }")
            data = result.data or {}
            errors = result.errors or []
            assert (
                errors or "__schema" not in data
            ), "GraphQL introspection must be disabled in production"

    def test_query_depth_limit_enforced(self, server, admin_a):
        """A maliciously-deep query must be rejected, not OOM the server.

        Backed by ``QueryDepthLimiter`` wired into ``Pydantic2Strawberry.create_schema``.
        ``GQL_DEPTH`` defaults to 10; a 30-level nested query is well over that.
        """
        # Build a 30-level nested query.  ``users { teams { users { ... } } }``
        depth = 30
        nested = "id"
        for _ in range(depth):
            nested = f"users {{ teams {{ {nested} }} }}"
        body = "{ " + nested + " }"
        response = self._post(
            server,
            body,
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        if response.status_code == 200:
            data = response.json()
            assert "errors" in data, (
                f"30-level nested query must be rejected by depth-limit; "
                f"got data={data.get('data') is not None}"
            )

    def test_secret_field_not_queryable(self, server, admin_a):
        """A field named `password_hash` (or similar) must not be queryable."""
        response = self._post(
            server,
            '{ user(id: "%s") { id passwordHash } }' % admin_a.id,
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        # Either schema rejects the field outright (errors) or returns null.
        if response.status_code == 200:
            data = response.json()
            errors = data.get("errors") or []
            field_unknown = any(
                "passwordHash" in (e.get("message") or "") for e in errors
            )
            user_blob = (data.get("data") or {}).get("user") or {}
            assert field_unknown or user_blob.get("passwordHash") in (
                None,
                "",
            ), "GraphQL response must not surface password_hash"


class _FakeInfo:
    """Minimal stand-in for ``strawberry.types.Info`` exposing a mutable,
    per-request ``context`` dict (as the FastAPI ``context_getter`` produces),
    so the reverse-nav DataLoader cache can be shared across parent resolvers.
    """

    def __init__(self, context: Dict[str, Any]) -> None:
        self.context = context


class TestReverseNavigationBatching(AbstractGraphQLTestMixin):
    """Reverse-navigation must batch its reads through a per-request DataLoader.

    Issue #230 finding E1: the auto-generated reverse-navigation resolver used
    to call ``manager.list(parent_id=<one id>)`` once per parent, so
    ``{ parents { children } }`` over N parents fired N queries. It now routes
    every sibling parent through a single per-request DataLoader that issues one
    ``manager.list(parent_id IN (...))`` and buckets the rows back per parent.
    """

    def _build(self, children: List["ChildModel"]):
        """Wire a GraphQLManager whose ChildModel manager counts ``list`` calls.

        Returns ``(manager, reverse_resolver, call_log)`` where ``call_log`` gets
        one entry per ``manager.list`` invocation — the query-count harness.
        """
        call_log: List[Dict[str, Any]] = []

        class CountingChildManager:
            def __init__(self, model_registry=None, requester_id=None):
                self.model_registry = model_registry
                self.requester_id = requester_id

            def list(self, filters=None, limit=None, offset=None, **kwargs):
                call_log.append(
                    {
                        "filters": filters,
                        "limit": limit,
                        "offset": offset,
                        "kwargs": kwargs,
                    }
                )
                # Emulate the DB: a single WHERE parent_id IN (...) scan whose
                # per-parent row order matches the old per-parent equality query.
                assert filters and len(filters) == 1
                comparison = filters[0]
                assert comparison.operator == "in"
                assert comparison.field_name == "parent_id"
                keyset = set(comparison.value)
                return [c for c in children if c.parent_id in keyset]

        registry = ModelRegistry()
        registry.bind(ParentModel)
        registry.bind(ChildModel)
        registry._locked = True
        registry.model_relationships = [
            (ChildModel, ChildModel, None, CountingChildManager),
        ]
        manager = GraphQLManager(registry)
        resolver = manager._create_reverse_navigation_resolver(
            ParentModel, ChildModel, "parent", "children"
        )
        return manager, resolver, call_log

    def test_reverse_nav_over_n_parents_issues_one_batched_query(self):
        """N parents resolving ``children`` must collapse to ONE list() call."""
        parents = [ParentModel(id=f"p{i}", name=f"P{i}") for i in range(3)]
        children = [
            ChildModel(id="c1", name="c1", parent_id="p0"),
            ChildModel(id="c2", name="c2", parent_id="p0"),
            ChildModel(id="c3", name="c3", parent_id="p1"),
            ChildModel(id="c4", name="c4", parent_id="p2"),
            ChildModel(id="c5", name="c5", parent_id="p2"),
        ]
        _manager, resolver, call_log = self._build(children)
        info = _FakeInfo({"requester_id": "req-1"})

        async def run():
            # A shared context + concurrent gather is exactly how graphql-core
            # completes a list field's item resolvers, so this reproduces the
            # real coalescing path.
            return await asyncio.gather(*(resolver(parent, info) for parent in parents))

        results = asyncio.run(run())

        # The whole point: one batched query, not one per parent.
        assert len(call_log) == 1
        batch_filter = call_log[0]["filters"][0]
        assert batch_filter.operator == "in"
        assert set(batch_filter.value) == {"p0", "p1", "p2"}

        # Every parent still gets exactly its own children, in order — identical
        # to the old ``manager.list(parent_id=<id>)`` per-parent result.
        by_parent = {parent.id: result for parent, result in zip(parents, results)}
        assert [c.id for c in by_parent["p0"]] == ["c1", "c2"]
        assert [c.id for c in by_parent["p1"]] == ["c3"]
        assert [c.id for c in by_parent["p2"]] == ["c4", "c5"]

    def test_reverse_nav_matches_old_per_parent_path(self):
        """Batched buckets must equal the old one-query-per-parent results."""
        parents = [ParentModel(id=f"p{i}", name=f"P{i}") for i in range(4)]
        children = [
            ChildModel(id=f"c{p}_{n}", name=f"c{p}_{n}", parent_id=f"p{p}")
            for p in range(4)
            for n in range(p)  # p0->0, p1->1, p2->2, p3->3 children
        ]
        _manager, resolver, call_log = self._build(children)
        info = _FakeInfo({"requester_id": "req-1"})

        async def run():
            return await asyncio.gather(*(resolver(parent, info) for parent in parents))

        results = asyncio.run(run())

        assert len(call_log) == 1  # still a single batch
        for parent, result in zip(parents, results):
            expected = [c.id for c in children if c.parent_id == parent.id]
            assert [c.id for c in result] == expected

    def test_reverse_nav_applies_per_parent_limit_and_offset(self):
        """limit/offset slice each parent's bucket, not the whole batch."""
        parents = [ParentModel(id="p0", name="P0"), ParentModel(id="p1", name="P1")]
        children = [
            ChildModel(id=f"c{n}", name=f"c{n}", parent_id="p0") for n in range(5)
        ] + [ChildModel(id=f"d{n}", name=f"d{n}", parent_id="p1") for n in range(5)]
        _manager, resolver, call_log = self._build(children)
        info = _FakeInfo({"requester_id": "req-1"})

        async def run():
            # limit=2, offset=1 applied per parent.
            return await asyncio.gather(
                resolver(parents[0], info, 2, 1),
                resolver(parents[1], info, 2, 1),
            )

        result0, result1 = asyncio.run(run())

        assert len(call_log) == 1  # one batch despite per-parent slicing
        assert [c.id for c in result0] == ["c1", "c2"]
        assert [c.id for c in result1] == ["d1", "d2"]

    def test_reverse_nav_missing_requester_returns_empty(self):
        """No requester_id in context short-circuits to [] without querying."""
        children = [ChildModel(id="c1", name="c1", parent_id="p0")]
        _manager, resolver, call_log = self._build(children)
        info = _FakeInfo({})

        result = asyncio.run(resolver(ParentModel(id="p0", name="P0"), info))

        assert result == []
        assert call_log == []
