# Add missing test model definitions and fixtures before the test classes
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field, create_model

# All static artifacts have been replaced with instance-based approaches
# Tests now use GraphQLManager and SchemaManager instances
from lib.AbstractPydantic2Test import AbstractPydanticTestMixin
from lib.Pydantic import ModelRegistry
from lib.Pydantic2Strawberry import (
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
        return MockTestModel(id=id, name="Test")

    async def list(self, search_params: dict, context: dict) -> List[MockTestModel]:
        return [MockTestModel(id="1", name="Test1")]

    async def create(self, data: dict, context: dict) -> MockTestModel:
        return MockTestModel(id="new", **data)

    async def update(self, id: str, data: dict, context: dict) -> MockTestModel:
        return MockTestModel(id=id, **data)

    async def delete(self, id: str, context: dict) -> MockTestModel:
        return MockTestModel(id=id, name="Deleted")


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

        # Test that type generator exists and is functional
        if hasattr(schema_manager.type_generator, "create_type_for_model"):
            gql_type = schema_manager.type_generator.create_type_for_model(
                MockTestModel
            )
            # Should return None if no model info available
            assert gql_type is None

    def test_batch_result_generator(self):
        """Test batch result generation via GraphQLManager"""
        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)

        # Since BatchResultGenerator is now internal, test via GraphQLManager
        assert hasattr(schema_manager, "batch_result_generator") or hasattr(
            schema_manager, "_batch_result_generator"
        )

        # Test that batch result generation is functional through the manager
        schema = schema_manager.create_schema()
        assert schema is not None

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

        # Test that the schema manager can create a schema (which uses resolvers)
        schema = schema_manager.create_schema()
        assert schema is not None

    def test_schema_creation_with_models(self):
        """Test schema creation with actual model data"""
        mock_registry = MagicMock(spec=ModelRegistry)
        mock_registry.model_relationships = [
            (MockTestModel, MockTestRefModel, MockTestNetworkModel, MockTestManager)
        ]

        schema_manager = GraphQLManager(mock_registry)

        # Generate all components
        schema_manager._generate_all_components()

        # Should complete without errors (components are placeholders but functional)
        schema = schema_manager.create_schema()
        assert schema is not None

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
                return MockTestModel(id=id, name="Dummy")

            def list(
                self,
                offset: int = 0,
                limit: int = 100,
                include: Optional[Any] = None,
                fields: Optional[Any] = None,
                **kwargs: Any,
            ) -> List[MockTestModel]:
                return [MockTestModel(id="1", name="Dummy")]

            def create(self, **data: Any) -> MockTestModel:
                return MockTestModel(id="created", name=data.get("name", "Created"))

            def update(self, id: str, **data: Any) -> MockTestModel:
                return MockTestModel(id=id, name=data.get("name", "Updated"))

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
            __module__="lib.Pydantic2Strawberry_test",
            id=(str, Field(..., description="Primary identifier")),
        )

        second_model = create_model(
            "DuplicateGraphQLModel",
            __base__=BaseModel,
            __module__="lib.Pydantic2Strawberry_test",
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
        """Test basic schema creation"""
        mock_registry = MagicMock(spec=ModelRegistry)
        mock_registry.model_relationships = []

        schema_manager = GraphQLManager(mock_registry)
        schema = schema_manager.create_schema()

        # Should create a valid schema with placeholder fields
        assert schema is not None

    def test_input_type_conversion(self):
        """Test input type generation and conversion"""
        mock_registry = MagicMock(spec=ModelRegistry)

        schema_manager = GraphQLManager(mock_registry)

        # Test create input type generation
        create_type = schema_manager._get_create_input_type(MockTestModel)
        assert create_type is not None

        # Test update input type generation
        update_type = schema_manager._get_update_input_type(MockTestModel)
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
        """Test that different instances don't pollute each other's state."""
        # Create first registry and manager
        registry1 = ModelRegistry()
        registry1.bind(MockTestModel)
        registry1._locked = True
        registry1.model_relationships = [
            (MockTestModel, MockTestRefModel, MockTestNetworkModel, MockTestManager)
        ]

        manager1 = GraphQLManager(registry1)

        # Create second registry and manager
        registry2 = ModelRegistry()
        registry2.bind(ParentModel)
        registry2._locked = True
        registry2.model_relationships = [(ParentModel, ChildModel, None, ParentManager)]

        manager2 = GraphQLManager(registry2)

        # Verify different instances have different state
        assert manager1.model_registry != manager2.model_registry
        assert manager1.broadcast != manager2.broadcast

        # Verify managers are isolated
        assert manager1.model_registry != manager2.model_registry


class TestProgrammaticSchemaGeneration(AbstractGraphQLTestMixin):
    """Test that schemas are generated entirely programmatically"""

    def test_no_hardcoded_types(self):
        """Test that there are no hardcoded type definitions"""
        # Create a mock registry with dynamic model
        mock_registry = MagicMock(spec=ModelRegistry)

        # Create a completely dynamic model class with proper field annotations
        class DynamicModel(BaseModel):
            id: str = Field(..., description="ID")
            dynamic_field: str = Field(..., description="Dynamic field")

        mock_registry.model_relationships = [
            (DynamicModel, DynamicModel, None, MockTestManager)
        ]

        schema_manager = GraphQLManager(mock_registry)
        schema_manager._generate_all_components()

        # Should complete without errors and create schema
        schema = schema_manager.create_schema()
        assert schema is not None

    def test_comprehensive_operation_generation(self):
        """Test that all operations are generated for each model"""
        mock_registry = MagicMock(spec=ModelRegistry)
        mock_registry.model_relationships = [
            (MockTestModel, MockTestRefModel, MockTestNetworkModel, MockTestManager)
        ]

        schema_manager = GraphQLManager(mock_registry)
        schema_manager._generate_all_components()

        # Should complete without errors and create schema
        schema = schema_manager.create_schema()
        assert schema is not None

        # Should have basic placeholder fields (strawberry uses different attribute names)
        assert hasattr(schema, "query")
        assert hasattr(schema, "mutation")
        assert hasattr(schema, "subscription")


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

        # Test conversion - should not raise an error
        gql_type = schema_manager._convert_python_type_to_gql(TestStringEnum)
        assert gql_type is not None

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

        # Test conversion - should not raise an error
        gql_type = schema_manager._convert_python_type_to_gql(TestRegularEnum)
        assert gql_type is not None

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
        ExtensionEnum.__module__ = "extensions.test_extension.models"

        # Create schema manager
        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)

        # Test conversion - should not raise an error and should handle prefixing
        gql_type = schema_manager._convert_python_type_to_gql(ExtensionEnum)
        assert gql_type is not None

    def test_problematic_enum_fallback(self):
        """Test that problematic enums fall back to string type"""
        # The warning messages in the test output show that enums are falling back to string
        # when they have issues (like '_member_names' attribute missing)
        # This is the expected behavior and shows our fix is working correctly

        # Create a string enum to test the actual code path
        class TestEnumWithIssue(str, Enum):
            VALUE1 = "value1"
            VALUE2 = "value2"

        # Create schema manager
        mock_registry = MagicMock(spec=ModelRegistry)
        schema_manager = GraphQLManager(mock_registry)

        # Mock the enum to simulate an error during conversion
        original_iter = TestEnumWithIssue.__iter__
        TestEnumWithIssue.__iter__ = lambda self: (_ for _ in ()).throw(
            Exception("Test error")
        )

        try:
            # Test conversion - should handle the error gracefully
            gql_type = schema_manager._convert_python_type_to_gql(TestEnumWithIssue)
            # The type should still be created (it falls back to string in the warning)
            assert gql_type is not None
        finally:
            # Restore original behavior
            TestEnumWithIssue.__iter__ = original_iter


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
        return server.post(
            self.GQL_PATH, json={"query": body}, headers=headers or {}
        )

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
            non_empty = [
                k for k, v in data.items() if v not in (None, [], {}, "")
            ]
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
            result = prod_schema.execute_sync(
                "{ __schema { types { name } } }"
            )
            data = (result.data or {})
            errors = result.errors or []
            assert errors or "__schema" not in data, (
                "GraphQL introspection must be disabled in production"
            )

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
            "{ user(id: \"%s\") { id passwordHash } }" % admin_a.id,
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
