# """
# Test cases for the enhanced ManagerMixin functionality.

# Tests the programmatic generation of BLL managers from Pydantic models,
# caching mechanisms, and integration with the existing manager system.
# """

# import pytest
# from typing import Optional, List
# from unittest.mock import Mock, MagicMock
# from pydantic import BaseModel

# from lib.Pydantic2Manager import (
#     ManagerMixin,
#     _generate_manager_class,
#     _find_reference_model,
#     _find_network_model,
#     _create_nested_manager_property,
#     clear_manager_registry_cache,
#     BLL_MANAGER_REGISTRY,
# )
# from lib.Pydantic import ModelRegistry
# from logic.AbstractLogicManager import AbstractBLLManager, ApplicationModel
# from lib.Pydantic2FastAPI import RouterMixin, AuthType


# class TestManagerMixin:
#     """Test cases for the enhanced ManagerMixin functionality."""

#     def setup_method(self):
#         """Set up test fixtures."""
#         # Clear caches before each test
#         clear_manager_registry_cache()

#         # Create a mock model registry
#         self.mock_registry = Mock(spec=ModelRegistry)
#         self.mock_registry._generated_managers = {}
#         self.mock_registry.bound_models = []
#         self.mock_registry.is_committed.return_value = False

#         # Mock DB manager for session creation
#         self.mock_registry.DB = Mock()
#         self.mock_registry.DB.manager = Mock()
#         self.mock_registry.DB.manager.Base = Mock()
#         self.mock_registry.DB.session.return_value = Mock()

#         # Mock user query
#         mock_user = Mock()
#         mock_user.id = "test-user-id"
#         self.mock_registry.DB.session.return_value.query.return_value.filter.return_value.first.return_value = (
#             mock_user
#         )

#     def test_basic_model_with_manager_mixin(self):
#         """Test basic model with ManagerMixin generates a working manager."""

#         class ProductModel(BaseModel, ManagerMixin):
#             id: str
#             name: str
#             price: float

#         # Use the Manager method
#         manager = ProductModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         # Verify manager was created
#         assert manager is not None
#         assert isinstance(manager, AbstractBLLManager)
#         assert hasattr(manager, "Model")
#         assert manager.Model == ProductModel

#         # Verify it has router functionality
#         assert isinstance(manager, RouterMixin)
#         assert hasattr(manager, "prefix")
#         assert manager.prefix == "/v1/product"

#     def test_model_with_manager_config(self):
#         """Test model with ManagerConfig customizes the generated manager."""

#         class CustomProductModel(BaseModel, ManagerMixin):
#             id: str
#             name: str
#             price: float

#             class ManagerConfig:
#                 prefix = "/v2/custom-product"
#                 tags = ["Custom Products"]
#                 auth_type = AuthType.API_KEY
#                 factory_params = ["target_product_id"]

#         manager = CustomProductModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         # Verify custom configuration was applied
#         assert manager.prefix == "/v2/custom-product"
#         assert manager.tags == ["Custom Products"]
#         assert manager.auth_type == AuthType.API_KEY
#         assert "target_product_id" in manager.factory_params

#     def test_manager_caching(self):
#         """Test that generated managers are properly cached."""

#         class CachedModel(BaseModel, ManagerMixin):
#             id: str
#             name: str

#         # Create first manager
#         manager1 = CachedModel.Manager(
#             model_registry=self.mock_registry, requester_id="user1"
#         )

#         # Create second manager - should use cached class
#         manager2 = CachedModel.Manager(
#             model_registry=self.mock_registry, requester_id="user2"
#         )

#         # Should be different instances but same class
#         assert manager1 is not manager2
#         assert type(manager1) is type(manager2)
#         assert manager1.__class__._is_generated is True

#     def test_existing_manager_priority(self):
#         """Test that existing managers take priority over generated ones."""

#         # Create a test model
#         class ExistingModel(BaseModel, ManagerMixin):
#             id: str
#             name: str

#         # Mock an existing manager in the registry
#         class ExistingModelManager(AbstractBLLManager):
#             Model = ExistingModel
#             prefix = "/existing"

#         BLL_MANAGER_REGISTRY["ExistingModelManager"] = ExistingModelManager

#         # Use the Manager method - should get existing manager
#         manager = ExistingModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         # Should be the existing manager, not generated
#         assert isinstance(manager, ExistingModelManager)
#         assert not hasattr(manager.__class__, "_is_generated")

#     def test_nested_resources_generation(self):
#         """Test generation of nested resource properties."""

#         class OrderItemModel(BaseModel, ManagerMixin):
#             id: str
#             product_id: str
#             quantity: int

#         class OrderModel(BaseModel, ManagerMixin):
#             id: str
#             customer_id: str

#             class ManagerConfig:
#                 nested_resources = {"items": {"model": OrderItemModel}}

#         manager = OrderModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         # Verify nested resources configuration
#         assert hasattr(manager, "items")
#         assert "items" in manager.nested_resources

#     def test_generate_manager_class_function(self):
#         """Test the _generate_manager_class function directly."""

#         class DirectTestModel(BaseModel):
#             id: str
#             name: str
#             description: Optional[str] = None

#         # Generate manager class
#         manager_class = _generate_manager_class(DirectTestModel)

#         # Verify generated class properties
#         assert manager_class.__name__ == "DirectTestManager"
#         assert manager_class.Model == DirectTestModel
#         assert hasattr(manager_class, "ReferenceModel")
#         assert hasattr(manager_class, "NetworkModel")
#         assert manager_class.prefix == "/v1/directtest"
#         assert manager_class._is_generated is True

#     def test_find_reference_model(self):
#         """Test reference model finding/generation."""

#         class TestModel(BaseModel):
#             id: str
#             name: str

#         # Test generating reference model
#         ref_model = _find_reference_model(TestModel, "Test")

#         assert ref_model is not None
#         assert ref_model.__name__ == "TestReferenceModel"
#         # In Pydantic v2, fields are accessible via model_fields, not hasattr()
#         assert "test_id" in ref_model.model_fields
#         assert "test" in ref_model.model_fields

#     def test_find_network_model(self):
#         """Test network model finding/generation."""

#         class TestModel(BaseModel):
#             id: str
#             name: str
#             price: float

#         # Test generating network model
#         network_model = _find_network_model(TestModel, "Test")

#         assert network_model is not None
#         assert network_model.__name__ == "TestNetworkModel"
#         assert hasattr(network_model, "POST")
#         assert hasattr(network_model, "PUT")
#         assert hasattr(network_model, "SEARCH")
#         assert hasattr(network_model, "ResponseSingle")
#         assert hasattr(network_model, "ResponsePlural")

#     def test_create_nested_manager_property(self):
#         """Test creation of nested manager properties."""

#         class ChildModel(BaseModel, ManagerMixin):
#             id: str
#             parent_id: str
#             name: str

#         # Create a property
#         prop = _create_nested_manager_property("children", ChildModel)

#         assert isinstance(prop, property)
#         assert prop.fget is not None

#     def test_manager_with_custom_methods(self):
#         """Test manager generation with custom methods in config."""

#         def custom_business_method(self, param: str):
#             return {"custom": param}

#         class BusinessModel(BaseModel, ManagerMixin):
#             id: str
#             name: str

#             class ManagerConfig:
#                 custom_methods = {"custom_business_method": custom_business_method}

#         manager = BusinessModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         # Verify custom method was added
#         assert hasattr(manager, "custom_business_method")
#         assert callable(manager.custom_business_method)

#     def test_manager_inherits_all_crud_methods(self):
#         """Test that generated managers inherit all CRUD methods."""

#         class CrudModel(BaseModel, ManagerMixin):
#             id: str
#             name: str

#         manager = CrudModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         # Verify all standard CRUD methods exist
#         crud_methods = [
#             "create",
#             "get",
#             "list",
#             "search",
#             "update",
#             "delete",
#             "batch_update",
#             "batch_delete",
#         ]
#         for method_name in crud_methods:
#             assert hasattr(manager, method_name)
#             assert callable(getattr(manager, method_name))

#     def test_registry_integration(self):
#         """Test that generated managers are properly registered."""

#         class RegistryModel(BaseModel, ManagerMixin):
#             id: str
#             name: str

#         # Generate a manager
#         manager = RegistryModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         # Verify manager class was registered
#         manager_class_name = manager.__class__.__name__
#         assert manager_class_name in BLL_MANAGER_REGISTRY
#         assert BLL_MANAGER_REGISTRY[manager_class_name] is manager.__class__

#     def test_manager_error_handling(self):
#         """Test error handling in manager generation."""

#         class ErrorModel(BaseModel, ManagerMixin):
#             id: str

#         # Test with None model_registry
#         with pytest.raises(ValueError, match="model_registry is required"):
#             ErrorModel.Manager(model_registry=None, requester_id="test-user-id")

#     def test_model_without_manager_config(self):
#         """Test model without ManagerConfig uses sensible defaults."""

#         class DefaultModel(BaseModel, ManagerMixin):
#             id: str
#             name: str

#         manager = DefaultModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         # Verify default configuration
#         assert manager.prefix == "/v1/default"
#         assert manager.tags == ["Default Management"]
#         assert manager.auth_type == AuthType.JWT
#         assert manager.factory_params == []

#     def test_complex_nested_model_structure(self):
#         """Test complex nested model structures."""

#         class UserModel(BaseModel, ManagerMixin):
#             id: str
#             email: str

#         class TeamModel(BaseModel, ManagerMixin):
#             id: str
#             name: str

#             class ManagerConfig:
#                 nested_resources = {
#                     "users": {"model": UserModel},
#                 }

#         class ProjectModel(BaseModel, ManagerMixin):
#             id: str
#             name: str
#             team_id: str

#             class ManagerConfig:
#                 nested_resources = {"team": {"model": TeamModel}}

#         project_manager = ProjectModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         # Verify nested structure
#         assert hasattr(project_manager, "team")
#         assert "team" in project_manager.nested_resources


# class TestManagerMixinIntegration:
#     """Integration tests for ManagerMixin with the broader system."""

#     def setup_method(self):
#         """Set up integration test fixtures."""
#         clear_manager_registry_cache()

#         # Create more realistic mock registry
#         self.mock_registry = Mock(spec=ModelRegistry)
#         self.mock_registry._generated_managers = {}
#         self.mock_registry.bound_models = []
#         self.mock_registry.is_committed.return_value = False

#         # Mock DB components more thoroughly
#         self.mock_registry.DB = Mock()
#         self.mock_registry.DB.manager = Mock()
#         self.mock_registry.DB.manager.Base = Mock()

#         # Mock session and user query
#         mock_session = Mock()
#         self.mock_registry.DB.session.return_value = mock_session

#         mock_user = Mock()
#         mock_user.id = "test-user-id"
#         mock_session.query.return_value.filter.return_value.first.return_value = (
#             mock_user
#         )

#     def test_manager_router_integration(self):
#         """Test that generated managers integrate with RouterMixin."""

#         class ApiModel(BaseModel, ManagerMixin):
#             id: str
#             name: str

#         manager = ApiModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         # Should be able to generate a router
#         assert hasattr(manager, "Router")
#         assert callable(manager.Router)

#         # Test router generation (this may require mocking FastAPI components)
#         try:
#             router = manager.Router(self.mock_registry)
#             assert router is not None
#         except Exception as e:
#             # Router generation may fail due to missing dependencies in test environment
#             # but the method should exist
#             assert "Router" in str(e) or "FastAPI" in str(e)

#     def test_multiple_models_with_relationships(self):
#         """Test multiple related models using ManagerMixin."""

#         class CategoryModel(BaseModel, ManagerMixin):
#             id: str
#             name: str

#         class ProductModel(BaseModel, ManagerMixin):
#             id: str
#             name: str
#             category_id: str
#             price: float

#             class ManagerConfig:
#                 nested_resources = {"category": {"model": CategoryModel}}

#         # Create managers for both models
#         category_manager = CategoryModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         product_manager = ProductModel.Manager(
#             model_registry=self.mock_registry, requester_id="test-user-id"
#         )

#         # Both should be properly generated
#         assert category_manager.__class__._is_generated
#         assert product_manager.__class__._is_generated

#         # Product manager should have category nested resource
#         assert hasattr(product_manager, "category")

#         # Both should be registered
#         assert category_manager.__class__.__name__ in BLL_MANAGER_REGISTRY
#         assert product_manager.__class__.__name__ in BLL_MANAGER_REGISTRY


# if __name__ == "__main__":
#     pytest.main([__file__, "-v"])
