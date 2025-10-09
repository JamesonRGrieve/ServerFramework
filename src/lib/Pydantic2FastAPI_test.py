from typing import ClassVar, List, Optional, Type

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

# Import AbstractBLLManager for proper inheritance
from logic.AbstractLogicManager import AbstractBLLManager

from lib.Pydantic2FastAPI import (
    AuthType,
    CustomRouteConfig,
    ExampleGenerator,
    HTTPMethod,
    NestedResourceConfig,
    RouteType,
    RouterMixin,
    create_manager_factory,
    create_router_from_manager,
    extract_body_data,
    generate_routers_from_model_registry,
    get_auth_dependency,
    handle_resource_operation_error,
    register_custom_route,
    register_route,
    serialize_for_response,
    static_route,
)


# Test Models and Managers for real functionality testing
class TestModel(BaseModel):
    __test__ = False
    id: Optional[str] = None
    name: str
    value: Optional[int] = None

    # Model.Manager pattern
    Manager: ClassVar[Optional[Type]] = None


class TestNetworkModel:
    """Network model for testing."""

    class POST(BaseModel):
        name: str
        value: Optional[int] = None

    class PUT(BaseModel):
        name: Optional[str] = None
        value: Optional[int] = None

    class GET(BaseModel):
        include: Optional[List[str]] = None
        fields: Optional[List[str]] = None

    class LIST(BaseModel):
        include: Optional[List[str]] = None
        fields: Optional[List[str]] = None
        offset: Optional[int] = None
        limit: Optional[int] = None
        sort_by: Optional[str] = None
        sort_order: Optional[str] = None

    class SEARCH(BaseModel):
        name: Optional[str] = None
        value: Optional[int] = None
        include: Optional[List[str]] = None
        fields: Optional[List[str]] = None
        offset: Optional[int] = None
        limit: Optional[int] = None
        sort_by: Optional[str] = None
        sort_order: Optional[str] = None

    class ResponseSingle(BaseModel):
        test: TestModel

    class ResponsePlural(BaseModel):
        tests: List[TestModel]


class TestManager(RouterMixin, AbstractBLLManager):
    __test__ = False
    """Test manager with RouterMixin."""

    _model = TestModel

    # RouterMixin configuration
    prefix = "/v1/test"
    tags = ["Test API"]
    auth_type = AuthType.JWT
    routes_to_register = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.CREATE,
        RouteType.UPDATE,
        RouteType.DELETE,
    ]
    custom_routes = [
        CustomRouteConfig(
            path="/custom",
            method=HTTPMethod.GET,
            function="custom_method",
            summary="Custom endpoint",
            description="A custom test endpoint",
            status_code=200,
        )
    ]

    def __init__(self, requester_id: Optional[str] = None, model_registry=None):
        super().__init__(requester_id=requester_id, model_registry=model_registry)
        self._data_store = {}  # Simple in-memory store for testing

    def create(self, **kwargs):
        """Create a test entity."""
        entity_id = f"test_{len(self._data_store) + 1}"
        entity = TestModel(id=entity_id, **kwargs)
        self._data_store[entity_id] = entity
        return entity

    def get(self, id: str, include=None, fields=None):
        """Get a test entity."""
        return self._data_store.get(id)

    def list(
        self,
        include=None,
        fields=None,
        offset=0,
        limit=100,
        sort_by=None,
        sort_order="asc",
        **filters,
    ):
        """List test entities."""
        return list(self._data_store.values())[offset : offset + limit]

    def update(self, id: str, **kwargs):
        """Update a test entity."""
        if id in self._data_store:
            entity = self._data_store[id]
            for key, value in kwargs.items():
                setattr(entity, key, value)
            return entity
        return None

    def delete(self, id: str):
        """Delete a test entity."""
        if id in self._data_store:
            del self._data_store[id]

    def search(
        self,
        include=None,
        fields=None,
        offset=0,
        limit=100,
        sort_by=None,
        sort_order="asc",
        **criteria,
    ):
        """Search test entities."""
        results = []
        for entity in self._data_store.values():
            if criteria.get("name") and entity.name != criteria["name"]:
                continue
            if criteria.get("value") and entity.value != criteria["value"]:
                continue
            results.append(entity)
        return results[offset : offset + limit]

    def batch_update(self, items):
        """Batch update test entities."""
        updated = []
        for item in items:
            entity = self.update(item["id"], **item["data"])
            if entity:
                updated.append(entity)
        return updated

    def batch_delete(self, ids):
        """Batch delete test entities."""
        for id in ids:
            self.delete(id)

    def custom_method(self):
        """Custom method for testing."""
        return {"message": "Custom method called"}

    @static_route("/static", method=HTTPMethod.GET, auth_type=AuthType.NONE)
    def static_method(cls, model_registry):
        """Static method for testing."""
        return {
            "message": "Static method called",
            "has_registry": model_registry is not None,
        }


# Set up Model.Manager relationship
TestModel.Manager = TestManager


class TestModelRegistry:
    __test__ = False
    """Simple test model registry."""

    def __init__(self):
        self._models = {
            "TestModel": TestModel,
        }

    def apply(self, model_class):
        """Apply registry to model class."""
        # For testing, just add Network attribute
        if not hasattr(model_class, "Network"):
            model_class.Network = TestNetworkModel
        return model_class


# Pytest Fixtures
@pytest.fixture
def model_registry():
    """Create a test model registry."""
    return TestModelRegistry()


@pytest.fixture
def test_manager():
    """Create a test manager instance."""
    return TestManager(requester_id="test_user")


@pytest.fixture
def router(model_registry):
    """Create a router from test manager."""
    return create_router_from_manager(TestManager, model_registry)


# Tests for Core Functions
class TestAuthType:
    """Test AuthType enum."""

    def test_auth_types_exist(self):
        """Test that all auth types are defined."""
        assert AuthType.NONE.value == "none"
        assert AuthType.JWT.value == "jwt"
        assert AuthType.API_KEY.value == "api_key"
        assert AuthType.BASIC.value == "basic"


class TestStaticRouteDecorator:
    """Test static_route decorator."""

    def test_static_route_decorator(self):
        """Test that static_route decorator adds configuration."""

        @static_route("/test", method=HTTPMethod.POST, auth_type=AuthType.NONE)
        def test_func():
            return {"test": "data"}

        assert hasattr(test_func, "_static_route_config")
        config = test_func._static_route_config[0]
        assert config.path == "/test"
        assert config.method == HTTPMethod.POST
        assert config.auth_type == AuthType.NONE
        assert config.is_static is True


class TestRouterMixin:
    """Test RouterMixin functionality."""

    def test_router_mixin_configuration(self):
        """Test that RouterMixin class vars are properly defined."""
        assert TestManager.prefix == "/v1/test"
        assert TestManager.tags == ["Test API"]
        assert TestManager.auth_type == AuthType.JWT
        assert len(TestManager.custom_routes) > 0

    def test_router_method(self, model_registry):
        """Test RouterMixin.Router() method."""
        router = TestManager.Router(model_registry)
        assert isinstance(router, APIRouter)
        assert router.prefix == "/v1/test"
        assert "Test API" in router.tags


class TestAuthDependencies:
    """Test authentication dependencies."""

    def test_get_auth_dependency_jwt(self):
        """Test JWT auth dependency."""
        dep = get_auth_dependency(AuthType.JWT)
        assert dep is not None
        assert hasattr(dep, "dependency")

    def test_get_auth_dependency_api_key(self):
        """Test API key auth dependency."""
        dep = get_auth_dependency(AuthType.API_KEY)
        assert dep is not None
        assert hasattr(dep, "dependency")

    def test_get_auth_dependency_none(self):
        """Test NONE auth dependency."""
        dep = get_auth_dependency(AuthType.NONE)
        assert dep is None


class TestDataHandling:
    """Test data extraction and serialization."""

    def test_extract_body_data_dict(self):
        """Test extracting data from dictionary."""
        body = {"test": {"name": "example", "value": 42}}
        data = extract_body_data(body, "test", "tests")
        assert data == {"name": "example", "value": 42}

    def test_extract_body_data_list(self):
        """Test extracting data from list."""
        body = [{"name": "item1"}, {"name": "item2"}]
        data = extract_body_data(body, "test", "tests")
        assert len(data) == 2
        assert data[0]["name"] == "item1"

    def test_extract_body_data_format_mismatch(self):
        """Test format mismatch raises exception."""
        body = {"test": [{"name": "item"}]}  # Singular key with array
        with pytest.raises(HTTPException) as exc_info:
            extract_body_data(body, "test", "tests")
        assert exc_info.value.status_code == 422

    def test_serialize_for_response_none(self):
        """Test serializing None."""
        assert serialize_for_response(None) is None

    def test_serialize_for_response_dict(self):
        """Test serializing dictionary."""
        data = {"key": "value"}
        assert serialize_for_response(data) == data

    def test_serialize_for_response_model(self):
        """Test serializing Pydantic model."""
        model = TestModel(id="1", name="test")
        serialized = serialize_for_response(model)
        assert serialized == {"id": "1", "name": "test", "value": None}

    def test_serialize_for_response_list(self):
        """Test serializing list of models."""
        models = [TestModel(id="1", name="test1"), TestModel(id="2", name="test2")]
        serialized = serialize_for_response(models)
        assert len(serialized) == 2
        assert serialized[0]["name"] == "test1"


class TestManagerFactory:
    """Test manager factory creation."""

    def test_create_manager_factory_jwt(self, model_registry):
        """Test creating JWT manager factory."""
        factory = create_manager_factory(TestManager, model_registry, AuthType.JWT)
        assert callable(factory)

        # Test factory without request (should fail for JWT)
        with pytest.raises(HTTPException) as exc_info:
            factory()
        assert exc_info.value.status_code == 401

    def test_create_manager_factory_none(self, model_registry):
        """Test creating NONE auth manager factory."""
        factory = create_manager_factory(TestManager, model_registry, AuthType.NONE)
        assert callable(factory)

        # Test factory without request (should work for NONE)
        manager = factory()
        assert isinstance(manager, TestManager)
        assert manager.requester_id is None


class TestErrorHandling:
    """Test error handling functionality."""

    def test_handle_validation_error(self):
        """Test handling ValidationError."""
        error = ValidationError.from_exception_data(
            "test", [{"type": "value_error", "loc": ("field",), "msg": "Invalid value"}]
        )
        with pytest.raises(HTTPException) as exc_info:
            handle_resource_operation_error(error)
        assert exc_info.value.status_code == 422
        assert "Validation error" in exc_info.value.detail["message"]

    def test_handle_value_error(self):
        """Test handling ValueError."""
        error = ValueError("Invalid input")
        with pytest.raises(HTTPException) as exc_info:
            handle_resource_operation_error(error)
        assert exc_info.value.status_code == 422
        assert "Invalid input" in exc_info.value.detail["details"]

    def test_handle_http_exception(self):
        """Test handling HTTPException (passthrough)."""
        error = HTTPException(status_code=404, detail="Not found")
        with pytest.raises(HTTPException) as exc_info:
            handle_resource_operation_error(error)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Not found"

    def test_handle_generic_exception(self):
        """Test handling generic Exception."""
        error = Exception("Something went wrong")
        with pytest.raises(HTTPException) as exc_info:
            handle_resource_operation_error(error)
        assert exc_info.value.status_code == 500
        assert "An unexpected error occurred" in exc_info.value.detail["message"]


class TestRouteRegistration:
    """Test route registration functionality."""

    @pytest.mark.parametrize(
        "route_type",
        [
            RouteType.GET,
            RouteType.LIST,
            RouteType.CREATE,
            RouteType.UPDATE,
            RouteType.DELETE,
            RouteType.SEARCH,
        ],
    )
    def test_register_route(self, route_type, model_registry):
        """Test registering different route types."""
        router = APIRouter()

        register_route(
            router=router,
            route_type=route_type,
            manager_class=TestManager,
            model_registry=model_registry,
            auth_type=AuthType.JWT,
            route_auth_overrides={},
            examples={},
        )

        # Check that route was added
        assert len(router.routes) > 0

        # Verify route properties based on type
        route = router.routes[-1]
        if route_type == RouteType.GET:
            assert "/{id}" in route.path
            assert "GET" in route.methods
        elif route_type == RouteType.LIST:
            assert route.path == ""
            assert "GET" in route.methods
        elif route_type == RouteType.CREATE:
            assert route.path == ""
            assert "POST" in route.methods
        elif route_type == RouteType.UPDATE:
            assert "/{id}" in route.path
            assert "PUT" in route.methods
        elif route_type == RouteType.DELETE:
            assert "/{id}" in route.path
            assert "DELETE" in route.methods
        elif route_type == RouteType.SEARCH:
            assert "/search" in route.path
            assert "POST" in route.methods

    def test_register_custom_route(self, model_registry):
        """Test registering custom route."""
        router = APIRouter()
        custom_route = CustomRouteConfig(
            path="/custom",
            method=HTTPMethod.GET,
            function="custom_method",
            summary="Custom endpoint",
            status_code=200,
        )

        factory = create_manager_factory(TestManager, model_registry, AuthType.JWT)
        register_custom_route(router, custom_route, factory, TestManager)

        # Check that route was added
        assert len(router.routes) > 0
        route = router.routes[-1]
        assert "/custom" in route.path
        assert "GET" in route.methods

    def test_register_static_route(self, model_registry):
        """Test registering static route."""
        router = APIRouter()

        # Get static route config from decorated method
        static_config = TestManager.static_method._static_route_config[0]

        factory = create_manager_factory(TestManager, model_registry, AuthType.NONE)
        register_custom_route(router, static_config, factory, TestManager)

        # Check that route was added
        assert len(router.routes) > 0
        route = router.routes[-1]
        assert "/static" in route.path
        assert "GET" in route.methods


class TestRouterCreation:
    """Test complete router creation."""

    def test_create_router_from_manager(self, model_registry):
        """Test creating complete router from manager."""
        router = create_router_from_manager(TestManager, model_registry)

        assert isinstance(router, APIRouter)
        assert router.prefix == "/v1/test"
        assert "Test API" in router.tags

        # Check that standard routes were created
        paths = [route.path for route in router.routes if hasattr(route, "path")]
        assert any("/{id}" in path for path in paths)  # GET/PUT/DELETE routes
        assert any(path == "" for path in paths)  # LIST/CREATE routes

        # Check that custom route was created
        assert any("/custom" in path for path in paths)

        # Check that static route was created
        assert any("/static" in path for path in paths)

    def test_create_router_with_nested_resources(self, model_registry):
        """Test creating router with nested resources."""

        # Create a manager with nested resources
        class ParentManager(TestManager):
            prefix = "/v1/parent"
            nested_resources = {
                "child": NestedResourceConfig(
                    child_resource_name="child",
                    manager_property="children",
                    routes_to_register=[
                        RouteType.GET,
                        RouteType.LIST,
                        RouteType.CREATE,
                    ],
                )
            }

            @property
            def children(self):
                return TestManager()

        router = create_router_from_manager(ParentManager, model_registry)

        # Check main router
        assert router.prefix == "/v1/parent"

        # Note: Nested routers are included directly in the main router
        # so we check for nested paths
        paths = [route.path for route in router.routes if hasattr(route, "path")]
        # Should have nested paths like /{parent_id}/child
        assert any("/{parent_id}/child" in path for path in paths)


class TestModelRegistryIntegration:
    """Test model registry integration."""

    def test_generate_routers_from_registry(self, model_registry):
        """Test generating routers from model registry using Model.Manager pattern."""
        # Apply the TestModel to registry so it gets discovered
        registry = TestModelRegistry()
        applied_model = registry.apply(TestModel)

        # Temporarily patch the model registry to include our test model
        original_models = getattr(model_registry, "_models", {})
        model_registry._models = {"TestModel": applied_model}

        # Add the models list method if it doesn't exist
        if not hasattr(model_registry, "models"):
            model_registry.models = lambda: [applied_model]

        routers = generate_routers_from_model_registry(model_registry)

        # Should find and create router for TestManager via Model.Manager
        assert "TestManager" in routers
        assert isinstance(routers["TestManager"], APIRouter)

        # Cleanup
        model_registry._models = original_models


class TestFastAPIIntegration:
    """Test FastAPI application integration."""

    def test_router_in_fastapi_app(self, router):
        """Test router works in FastAPI application."""
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)

        client = TestClient(app)

        # Test that OpenAPI schema is generated
        response = client.get("/openapi.json")
        assert response.status_code == 200

        schema = response.json()
        assert "paths" in schema

        # Check that our routes are in the schema
        paths = schema["paths"]
        assert "/v1/test" in paths or any("/v1/test" in p for p in paths)

    def test_static_route_functionality(self, router):
        """Test that static routes work without authentication."""
        from fastapi import FastAPI

        app = FastAPI()
        app.state.model_registry = TestModelRegistry()
        app.include_router(router)

        client = TestClient(app)

        # Static route should work without auth
        response = client.get("/v1/test/static")
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Static method called"
        assert data["has_registry"] is True


class TestCompleteWorkflow:
    """Test complete workflow with real operations."""

    def test_crud_workflow(self, model_registry):
        """Test complete CRUD workflow."""
        # Create manager and router
        manager = TestManager()
        router = create_router_from_manager(TestManager, model_registry)

        # Test data flow through manager
        # Create
        entity = manager.create(name="Test Entity", value=42)
        assert entity.id is not None
        assert entity.name == "Test Entity"

        # Read
        retrieved = manager.get(entity.id)
        assert retrieved.name == "Test Entity"

        # Update
        updated = manager.update(entity.id, value=100)
        assert updated.value == 100

        # List
        entities = manager.list()
        assert len(entities) == 1

        # Search
        results = manager.search(name="Test Entity")
        assert len(results) == 1

        # Delete
        manager.delete(entity.id)
        assert manager.get(entity.id) is None

    def test_batch_operations(self, model_registry):
        """Test batch operations."""
        manager = TestManager()

        # Create test entities
        entity1 = manager.create(name="Entity 1", value=1)
        entity2 = manager.create(name="Entity 2", value=2)

        # Batch update
        updated = manager.batch_update(
            [
                {"id": entity1.id, "data": {"value": 10}},
                {"id": entity2.id, "data": {"value": 20}},
            ]
        )
        assert len(updated) == 2
        assert updated[0].value == 10
        assert updated[1].value == 20

        # Batch delete
        manager.batch_delete([entity1.id, entity2.id])
        assert manager.get(entity1.id) is None
        assert manager.get(entity2.id) is None


class TestExampleGenerator:
    """Test ExampleGenerator functionality."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear example cache before each test."""
        ExampleGenerator.clear_cache()
        yield
        ExampleGenerator.clear_cache()

    def test_generate_uuid(self):
        """Test UUID generation."""
        uuid1 = ExampleGenerator.generate_uuid()
        uuid2 = ExampleGenerator.generate_uuid()

        assert len(uuid1) == 36
        assert len(uuid2) == 36
        assert uuid1 != uuid2

    def test_get_example_value_string_patterns(self):
        """Test string example generation for different field patterns."""
        # ID patterns
        id_example = ExampleGenerator.get_example_value(str, "id")
        assert len(id_example) == 36  # UUID length

        user_id_example = ExampleGenerator.get_example_value(str, "user_id")
        assert len(user_id_example) == 36

        # Email patterns
        email_example = ExampleGenerator.get_example_value(str, "email")
        assert "@" in email_example

        # URL patterns - should be HTTPS
        url_example = ExampleGenerator.get_example_value(str, "url")
        assert url_example.startswith("https://")

        # Status patterns
        status_example = ExampleGenerator.get_example_value(str, "status")
        assert status_example in ["active", "inactive", "pending", "completed"]

        # Token patterns
        token_example = ExampleGenerator.get_example_value(str, "token")
        assert token_example.startswith("tk-")

    def test_get_example_value_primitive_types(self):
        """Test example generation for primitive types."""
        # Integer patterns
        age_example = ExampleGenerator.get_example_value(int, "age")
        assert 18 <= age_example <= 80

        count_example = ExampleGenerator.get_example_value(int, "count")
        assert 1 <= count_example <= 1000

        default_int = ExampleGenerator.get_example_value(int, "value")
        assert default_int == 42

        # Float patterns
        price_example = ExampleGenerator.get_example_value(float, "price")
        assert 1.99 <= price_example <= 999.99

        default_float = ExampleGenerator.get_example_value(float, "value")
        assert default_float == 42.5

    def test_get_example_value_boolean_patterns(self):
        """Test boolean example generation based on field patterns."""
        # Boolean patterns that should default to True
        assert ExampleGenerator.get_example_value(bool, "is_active") is True
        assert ExampleGenerator.get_example_value(bool, "has_permission") is True
        assert ExampleGenerator.get_example_value(bool, "enabled") is True
        assert ExampleGenerator.get_example_value(bool, "favorite") is True

        # Other boolean fields should return boolean
        other_bool = ExampleGenerator.get_example_value(bool, "deleted")
        assert isinstance(other_bool, bool)

    def test_get_example_value_optional_types(self):
        """Test handling of Optional types."""
        from typing import Optional

        optional_example = ExampleGenerator.get_example_value(Optional[str], "name")
        assert isinstance(optional_example, str)

    def test_get_example_value_list_types(self):
        """Test handling of List types."""
        list_example = ExampleGenerator.get_example_value(List[str], "tags")
        assert isinstance(list_example, list)
        assert len(list_example) == 1
        assert isinstance(list_example[0], str)

    def test_get_example_value_dict_types(self):
        """Test handling of Dict types."""
        from typing import Dict

        dict_example = ExampleGenerator.get_example_value(Dict[str, str], "metadata")
        assert isinstance(dict_example, dict)
        assert dict_example == {"key": "value"}

    def test_generate_example_for_model(self):
        """Test example generation for complete models."""
        example = ExampleGenerator.generate_example_for_model(TestModel)

        assert isinstance(example, dict)
        assert "id" in example
        assert "name" in example
        assert "value" in example

        # Verify specific field types
        assert example["id"] is None or len(str(example["id"])) == 36  # UUID or None
        assert isinstance(example["name"], str)

    def test_generate_example_skips_model_field_accessor_defaults(self):
        """Ensure models with non-serializable defaults produce safe examples."""
        from fastapi.encoders import jsonable_encoder
        from logic.BLL_Auth import TeamModel

        example = ExampleGenerator.generate_example_for_model(TeamModel)
        encoded = jsonable_encoder(example)

        assert isinstance(encoded, dict)
        assert isinstance(example["id"], str)
        assert len(example["id"]) == 36
        assert isinstance(example["created_at"], str)
        assert isinstance(example["created_by_user_id"], str)

    def test_generate_operation_examples(self):
        """Test operation example generation."""
        examples = ExampleGenerator.generate_operation_examples(
            TestNetworkModel, "test"
        )

        # Check that operation types are generated
        assert isinstance(examples, dict)
        assert "batch_delete" in examples  # Always present
        assert "target_ids" in examples["batch_delete"]
        assert isinstance(examples["batch_delete"]["target_ids"], list)
        assert len(examples["batch_delete"]["target_ids"]) == 2

        # Check that UUIDs are generated for target_ids
        for target_id in examples["batch_delete"]["target_ids"]:
            assert len(target_id) == 36

    def test_example_caching(self):
        """Test that examples are cached."""
        # Generate example twice
        example1 = ExampleGenerator.generate_example_for_model(TestModel)
        example2 = ExampleGenerator.generate_example_for_model(TestModel)

        # Should be the same due to caching
        assert example1 == example2

        # Clear cache and generate again
        ExampleGenerator.clear_cache()
        example3 = ExampleGenerator.generate_example_for_model(TestModel)

        # May be different due to random generation
        assert isinstance(example3, dict)
        assert "name" in example3

    def test_customize_example(self):
        """Test example customization."""
        base_example = {"name": "Original", "value": 42}

        # Test basic customization
        customized = ExampleGenerator.customize_example(
            base_example, {"name": "Custom Name"}
        )
        assert customized["name"] == "Custom Name"
        assert customized["value"] == 42

        # Test nested customization
        nested_example = {"user": {"name": "Original", "settings": {"theme": "light"}}}
        nested_customized = ExampleGenerator.customize_example(
            nested_example, {"user.name": "New Name", "user.settings.theme": "dark"}
        )
        assert nested_customized["user"]["name"] == "New Name"
        assert nested_customized["user"]["settings"]["theme"] == "dark"

    def test_field_generator_management(self):
        """Test adding and removing custom field generators."""
        # Add custom generator
        ExampleGenerator.add_field_generator(r"^test_field$", lambda: "custom_value")

        result = ExampleGenerator.get_example_value(str, "test_field")
        assert result == "custom_value"

        # Remove custom generator
        ExampleGenerator.remove_field_generator(r"^test_field$")

        # Should use default pattern now
        result2 = ExampleGenerator.get_example_value(str, "test_field")
        assert result2 != "custom_value"

    def test_boolean_generator_management(self):
        """Test adding and removing custom boolean generators."""
        # Add custom boolean generator
        ExampleGenerator.add_boolean_generator(r"^custom_bool$", lambda: True)

        result = ExampleGenerator.get_example_value(bool, "custom_bool")
        assert result is True

        # Remove custom generator
        ExampleGenerator.remove_boolean_generator(r"^custom_bool$")

        # Should use default pattern now
        result2 = ExampleGenerator.get_example_value(bool, "custom_bool")
        assert isinstance(result2, bool)

    def test_get_field_patterns(self):
        """Test getting field patterns."""
        patterns = ExampleGenerator.get_field_patterns()
        assert isinstance(patterns, dict)
        assert len(patterns) > 0
        assert r"^.*email.*$" in patterns

    def test_get_boolean_patterns(self):
        """Test getting boolean patterns."""
        patterns = ExampleGenerator.get_boolean_patterns()
        assert isinstance(patterns, dict)
        assert len(patterns) > 0
        assert r"^.*is_.*$" in patterns

    def test_field_name_to_example(self):
        """Test field name to example conversion."""
        assert ExampleGenerator.field_name_to_example("name") == "Example Name"
        assert ExampleGenerator.field_name_to_example("user_id") == "Example User"
        assert (
            ExampleGenerator.field_name_to_example("project_name") == "Example Project"
        )


class TestExampleGeneratorIntegration:
    """Test ExampleGenerator integration with router creation."""

    def test_router_includes_examples_in_openapi(self, model_registry):
        """Test that routers include examples in OpenAPI schema."""
        from fastapi import FastAPI

        # Create router
        router = create_router_from_manager(TestManager, model_registry)

        # Create FastAPI app and include router
        app = FastAPI()
        app.include_router(router)

        # Test client
        client = TestClient(app)

        # Get OpenAPI schema
        response = client.get("/openapi.json")
        assert response.status_code == 200

        schema = response.json()
        assert "paths" in schema

        # Check that paths exist (routes were registered)
        paths = schema["paths"]
        assert len(paths) > 0

    def test_manager_with_example_overrides(self, model_registry):
        """Test manager with custom example overrides."""

        class TestManagerWithOverrides(TestManager):
            example_overrides = {
                "get": {"test": {"name": "Custom Example Name"}},
                "create": {"test": {"name": "Custom Create Name"}},
            }

        router = create_router_from_manager(TestManagerWithOverrides, model_registry)

        # Verify router was created successfully
        assert isinstance(router, APIRouter)
        assert router.prefix == "/v1/test"

    def test_route_registration_with_examples(self, model_registry):
        """Test that route registration includes examples."""
        router = APIRouter()

        # Register route with example generation
        register_route(
            router=router,
            route_type=RouteType.GET,
            manager_class=TestManager,
            model_registry=model_registry,
            auth_type=AuthType.JWT,
            route_auth_overrides={},
            examples={},  # Will be generated automatically
        )

        # Check that route was registered
        assert len(router.routes) > 0

        # Check route properties
        route = router.routes[-1]
        assert "/{id}" in route.path
        assert "GET" in route.methods


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
