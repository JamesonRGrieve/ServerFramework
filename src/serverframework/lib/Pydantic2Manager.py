# """
# Pydantic to Manager Generator

# This module provides functionality to discover, analyze, and generate BLL managers
# from Pydantic models. It follows the same patterns as Pydantic2SQLAlchemy and
# Pydantic2FastAPI for consistency.

# The module can:
# 1. Discover existing BLL managers from the logic directory
# 2. Analyze manager structures and dependencies
# 3. Generate new managers based on Pydantic models
# 4. Validate manager implementations against AbstractBLLManager
# """

# import inspect
# from typing import Any, Dict, List, Optional, Type, get_type_hints

# from pydantic import BaseModel

# from lib.AbstractPydantic2 import (
#     ErrorHandlerMixin,
#     ModelDiscoveryMixin,
#     RelationshipAnalyzer,
#     default_cache_manager,
# )
# from lib.Logging import logger
# from lib.Pydantic import ModelRegistry
# from lib.Pydantic2FastAPI import AuthType, RouterMixin
# from logic.AbstractLogicManager import AbstractBLLManager


# class ManagerMixin:
#     """
#     Enhanced mixin class that provides programmatic manager generation for Pydantic models.

#     This mixin should be inherited by Pydantic models to provide the .Manager property
#     that returns a factory function for creating manager instances, similar to how
#     DatabaseMixin.DB and RouterMixin.Router work.

#     Usage:
#         class ProductModel(BaseModel, ManagerMixin):
#             id: str
#             name: str
#             price: float

#             class ManagerConfig:
#                 prefix = "/v1/product"
#                 tags = ["Products"]
#                 auth_type = AuthType.JWT

#         # Get the manager factory
#         ProductManagerFactory = ProductModel.Manager

#         # Use the factory to create manager instances
#         product_manager = ProductManagerFactory(
#             model_registry=registry,
#             requester_id=user_id
#         )

#         # Or use it directly
#         product_manager = ProductModel.Manager(
#             model_registry=registry,
#             requester_id=user_id
#         )
#     """

#     @classmethod
#     @property
#     def Manager(cls):
#         """
#         Get a factory function for creating BLL manager instances for this model.

#         Returns:
#             Factory function that creates AbstractBLLManager instances configured for this model
#         """

#         def manager_factory(model_registry, requester_id: str, **kwargs):
#             """
#             Factory function for creating manager instances.

#             Args:
#                 model_registry: ModelRegistry instance (required)
#                 requester_id: ID of the user making the request (required)
#                 **kwargs: Additional arguments to pass to manager constructor

#             Returns:
#                 AbstractBLLManager instance configured for this model
#             """
#             # First try to find an existing manager
#             existing_manager_class = get_manager_for_model(cls)

#             if existing_manager_class is not None:
#                 # Use the existing manager
#                 return existing_manager_class(
#                     requester_id=requester_id, model_registry=model_registry, **kwargs
#                 )

#             # No existing manager found, generate one programmatically
#             manager_class = _get_or_generate_manager_class(cls, model_registry)

#             # Return instance
#             return manager_class(
#                 requester_id=requester_id, model_registry=model_registry, **kwargs
#             )

#         return manager_factory


# # Global registry for discovered managers
# BLL_MANAGER_REGISTRY: Dict[str, Type] = {}

# # Cache for manager analysis
# MANAGER_ANALYSIS_CACHE: Dict[str, Dict[str, Any]] = {}


# def clear_manager_registry_cache():
#     """Clear the manager registry and analysis cache."""
#     global BLL_MANAGER_REGISTRY, MANAGER_ANALYSIS_CACHE
#     BLL_MANAGER_REGISTRY.clear()
#     MANAGER_ANALYSIS_CACHE.clear()


# def discover_bll_managers() -> Dict[str, Type]:
#     """
#     Dynamically discover all BLL manager classes by importing BLL files.

#     Returns:
#         Dict[str, Type]: Dictionary mapping manager names to manager classes
#     """
#     global BLL_MANAGER_REGISTRY

#     # Clear existing registry
#     BLL_MANAGER_REGISTRY.clear()

#     # Use the enhanced discovery mixin
#     class ManagerDiscoverer(ModelDiscoveryMixin, ErrorHandlerMixin):
#         def __init__(self):
#             self.model_registry = ModelRegistry()

#         def _get_model_suffix(self) -> str:
#             return "Manager"

#         def _is_valid_model_class(self, attr: Any, attr_name: str) -> bool:
#             """Check if an attribute is a valid manager class."""
#             import inspect

#             is_manager_class = (
#                 inspect.isclass(attr)
#                 and attr_name.endswith("Manager")
#                 and not attr_name.startswith("_")
#             )

#             if not is_manager_class:
#                 return False

#             # Check if it's a real BLL manager or a mock manager
#             is_real_manager = (
#                 issubclass(attr, AbstractBLLManager) and attr != AbstractBLLManager
#             )

#             is_mock_manager = (
#                 "Mock" in attr_name
#                 and hasattr(attr, "Model")
#                 and hasattr(attr, "__init__")
#                 and callable(getattr(attr, "create", None))
#             )

#             return is_real_manager or is_mock_manager

#     discoverer = ManagerDiscoverer()

#     # Discover managers using the mixin
#     discovered = discoverer.safe_operation(
#         lambda: discoverer.discover_models_in_modules(["logic"], "BLL"),
#         "BLL managers",
#         fallback={},
#         log_success=True,
#     )

#     # Update the global registry
#     BLL_MANAGER_REGISTRY.update(discovered)

#     return BLL_MANAGER_REGISTRY


# def _discover_managers_from_module(module, module_name):
#     """
#     Discover BLL manager classes from a module.

#     Args:
#         module: The imported module
#         module_name: Name of the module
#     """
#     for attr_name in dir(module):
#         attr = getattr(module, attr_name)

#         # Check if it's a manager class (not instance)
#         if (
#             inspect.isclass(attr)
#             and attr_name.endswith("Manager")
#             and not attr_name.startswith("_")
#         ):
#             # Check if it's a real BLL manager or a mock manager
#             is_real_manager = (
#                 issubclass(attr, AbstractBLLManager) and attr != AbstractBLLManager
#             )

#             is_mock_manager = (
#                 "Mock" in attr_name
#                 and hasattr(attr, "Model")
#                 and hasattr(attr, "__init__")
#                 and callable(getattr(attr, "create", None))
#             )

#             if is_real_manager or is_mock_manager:
#                 # Store in registry with full qualification
#                 full_name = f"{module_name}.{attr_name}"
#                 BLL_MANAGER_REGISTRY[full_name] = attr
#                 BLL_MANAGER_REGISTRY[attr_name] = attr  # Also store simple name

#                 manager_type = "mock" if is_mock_manager else "BLL"
#                 logger.debug(
#                     f"Discovered {manager_type} manager: {attr_name} from {module_name}"
#                 )


# def get_manager_class(manager_name: str) -> Optional[Type]:
#     """
#     Get a manager class by name from the registry.

#     Args:
#         manager_name: Name of the manager class

#     Returns:
#         Manager class if found, None otherwise
#     """
#     if not BLL_MANAGER_REGISTRY:
#         discover_bll_managers()

#     return BLL_MANAGER_REGISTRY.get(manager_name)


# def get_manager_for_model(model_class: Type[BaseModel]) -> Optional[Type]:
#     """
#     Get the BLL manager class for a given Pydantic model.

#     Args:
#         model_class: The Pydantic model class

#     Returns:
#         Manager class if found, None otherwise
#     """
#     if not BLL_MANAGER_REGISTRY:
#         discover_bll_managers()

#     model_name = model_class.__name__

#     # Try different naming patterns for the manager
#     possible_manager_names = [
#         f"{model_name}Manager",
#         f"{model_name.replace('Model', '')}Manager",
#         f"{model_name.replace('NetworkModel', '')}Manager",
#     ]

#     # Look for manager by checking if any manager has this model as its Model attribute
#     for manager_class in BLL_MANAGER_REGISTRY.values():
#         if hasattr(manager_class, "Model") and manager_class.Model == model_class:
#             return manager_class

#     # If not found by Model attribute, try name-based lookup
#     for manager_name in possible_manager_names:
#         manager_class = BLL_MANAGER_REGISTRY.get(manager_name)
#         if manager_class:
#             return manager_class

#     return None


# def list_discovered_managers() -> List[str]:
#     """
#     List all discovered manager names.

#     Returns:
#         List of manager names
#     """
#     if not BLL_MANAGER_REGISTRY:
#         discover_bll_managers()

#     # Return only simple names (not fully qualified)
#     return [name for name in BLL_MANAGER_REGISTRY.keys() if "." not in name]


# def analyze_manager_structure(manager_class: Type) -> Dict[str, Any]:
#     """
#     Analyze the structure of a BLL manager class.

#     Args:
#         manager_class: The manager class to analyze

#     Returns:
#         Dictionary with analysis results
#     """
#     manager_name = manager_class.__name__

#     # Check cache first
#     if manager_name in MANAGER_ANALYSIS_CACHE:
#         return MANAGER_ANALYSIS_CACHE[manager_name]

#     analysis = {
#         "name": manager_name,
#         "module": manager_class.__module__,
#         "has_model": False,
#         "has_reference_model": False,
#         "has_network_model": False,
#         "has_db_class": False,
#         "model_class": None,
#         "reference_model_class": None,
#         "network_model_class": None,
#         "db_class": None,
#         "methods": [],
#         "properties": [],
#         "search_transformers": [],
#         "hooks": [],
#         "inheritance_chain": [],
#         "validation_methods": [],
#         "custom_methods": [],
#         "required_attributes": [],
#         "missing_attributes": [],
#     }

#     # Analyze inheritance chain
#     analysis["inheritance_chain"] = [cls.__name__ for cls in manager_class.__mro__]

#     # Check for required class attributes
#     required_attrs = ["Model", "ReferenceModel", "NetworkModel", "DBClass"]
#     for attr in required_attrs:
#         if hasattr(manager_class, attr):
#             analysis["required_attributes"].append(attr)
#             if attr == "Model":
#                 analysis["has_model"] = True
#                 analysis["model_class"] = getattr(manager_class, attr)
#             elif attr == "ReferenceModel":
#                 analysis["has_reference_model"] = True
#                 analysis["reference_model_class"] = getattr(manager_class, attr)
#             elif attr == "NetworkModel":
#                 analysis["has_network_model"] = True
#                 analysis["network_model_class"] = getattr(manager_class, attr)
#             elif attr == "DBClass":
#                 analysis["has_db_class"] = True
#                 analysis["db_class"] = getattr(manager_class, attr)
#         else:
#             analysis["missing_attributes"].append(attr)

#     # Analyze methods
#     for method_name in dir(manager_class):
#         if not method_name.startswith("_"):
#             method = getattr(manager_class, method_name)
#             if callable(method):
#                 analysis["methods"].append(method_name)

#                 # Categorize special methods
#                 if method_name.endswith("_validation"):
#                     analysis["validation_methods"].append(method_name)
#                 elif method_name not in [
#                     "create",
#                     "get",
#                     "list",
#                     "search",
#                     "update",
#                     "delete",
#                     "batch_update",
#                     "batch_delete",
#                 ]:
#                     analysis["custom_methods"].append(method_name)

#     # Analyze properties
#     for prop_name in dir(manager_class):
#         if not prop_name.startswith("_"):
#             prop = getattr(manager_class, prop_name)
#             if isinstance(prop, property):
#                 analysis["properties"].append(prop_name)

#     # Check for search transformers
#     if hasattr(manager_class, "search_transformers"):
#         analysis["search_transformers"] = list(
#             getattr(manager_class, "search_transformers", {}).keys()
#         )

#     # Check for hooks
#     if hasattr(manager_class, "hooks"):
#         hooks = getattr(manager_class, "hooks", {})
#         if hasattr(hooks, "keys"):
#             analysis["hooks"] = list(hooks.keys())

#     # Cache the analysis
#     MANAGER_ANALYSIS_CACHE[manager_name] = analysis

#     return analysis


# def validate_manager_implementation(manager_class: Type) -> Dict[str, Any]:
#     """
#     Validate that a manager class properly implements AbstractBLLManager.

#     Args:
#         manager_class: The manager class to validate

#     Returns:
#         Dictionary with validation results
#     """
#     validation = {
#         "is_valid": True,
#         "errors": [],
#         "warnings": [],
#         "missing_methods": [],
#         "missing_attributes": [],
#         "invalid_signatures": [],
#     }

#     # Check if this is a mock manager (special handling)
#     is_mock_manager = "Mock" in manager_class.__name__

#     # Check inheritance (more lenient for mock managers)
#     if not is_mock_manager and not issubclass(manager_class, AbstractBLLManager):
#         validation["is_valid"] = False
#         validation["errors"].append(
#             f"{manager_class.__name__} does not inherit from AbstractBLLManager"
#         )
#         return validation
#     elif is_mock_manager and not issubclass(manager_class, AbstractBLLManager):
#         validation["warnings"].append(
#             f"Mock manager {manager_class.__name__} does not inherit from AbstractBLLManager (this is acceptable for testing)"
#         )

#     # Check required class attributes
#     required_attrs = ["Model", "ReferenceModel", "NetworkModel", "DBClass"]
#     for attr in required_attrs:
#         if not hasattr(manager_class, attr):
#             validation["missing_attributes"].append(attr)
#             # For mock managers, missing ReferenceModel and DBClass are acceptable
#             if is_mock_manager and attr in ["ReferenceModel", "DBClass"]:
#                 validation["warnings"].append(
#                     f"Mock manager missing {attr} (acceptable for testing)"
#                 )
#             else:
#                 validation["warnings"].append(f"Missing required attribute: {attr}")

#     # Check required methods (from AbstractBLLManager)
#     required_methods = [
#         "create",
#         "get",
#         "list",
#         "search",
#         "update",
#         "delete",
#         "batch_update",
#         "batch_delete",
#     ]

#     for method_name in required_methods:
#         if not hasattr(manager_class, method_name):
#             validation["missing_methods"].append(method_name)
#             validation["errors"].append(f"Missing required method: {method_name}")
#         else:
#             # Check method signature
#             method = getattr(manager_class, method_name)
#             if callable(method):
#                 try:
#                     sig = inspect.signature(method)
#                     # Basic signature validation could be added here
#                 except Exception as e:
#                     validation["invalid_signatures"].append(f"{method_name}: {str(e)}")

#     # Check __init__ method signature (more lenient for mock managers)
#     if hasattr(manager_class, "__init__"):
#         try:
#             init_sig = inspect.signature(manager_class.__init__)
#             params = list(init_sig.parameters.keys())
#             expected_params = [
#                 "self",
#                 "requester_id",
#                 "target_user_id",
#                 "target_team_id",
#                 "db",
#             ]

#             # Check if all expected parameters are present
#             for param in expected_params:
#                 if param not in params:
#                     if is_mock_manager and param in [
#                         "target_user_id",
#                         "target_team_id",
#                         "db",
#                     ]:
#                         # Mock managers can have simpler __init__ signatures
#                         validation["warnings"].append(
#                             f"Mock manager __init__ missing parameter: {param} (acceptable for testing)"
#                         )
#                     else:
#                         validation["warnings"].append(
#                             f"__init__ missing expected parameter: {param}"
#                         )

#         except Exception as e:
#             validation["invalid_signatures"].append(f"__init__: {str(e)}")

#     # Set overall validity (mock managers are valid if they have the basic structure)
#     if validation["errors"]:
#         validation["is_valid"] = False
#     elif is_mock_manager:
#         # Mock managers are considered valid if they have Model and NetworkModel and basic methods
#         has_model = hasattr(manager_class, "Model")
#         has_network_model = hasattr(manager_class, "NetworkModel")
#         has_basic_methods = all(
#             hasattr(manager_class, method) for method in ["create", "get", "list"]
#         )

#         if has_model and has_network_model and has_basic_methods:
#             validation["is_valid"] = True
#         else:
#             validation["is_valid"] = False
#             validation["errors"].append(
#                 "Mock manager missing essential attributes or methods"
#             )

#     return validation


# def generate_manager_template(
#     model_class: Type[BaseModel],
#     manager_name: Optional[str] = None,
#     db_class_name: Optional[str] = None,
# ) -> str:
#     """
#     Generate a template for a BLL manager based on a Pydantic model.

#     Args:
#         model_class: The Pydantic model class
#         manager_name: Name for the manager (auto-generated if not provided)
#         db_class_name: Name of the DB class (auto-generated if not provided)

#     Returns:
#         String containing the manager template code
#     """
#     if manager_name is None:
#         model_name = model_class.__name__
#         if model_name.endswith("Model"):
#             manager_name = model_name.removesuffix("Model") + "Manager"
#         else:
#             manager_name = model_name + "Manager"

#     if db_class_name is None:
#         model_name = model_class.__name__
#         if model_name.endswith("Model"):
#             db_class_name = model_name.removesuffix("Model")
#         else:
#             db_class_name = model_name

#     # Extract model information
#     model_name = model_class.__name__
#     reference_model_name = model_name.removesuffix("Model") + "ReferenceModel"
#     network_model_name = model_name.removesuffix("Model") + "NetworkModel"

#     # Generate the template
#     template = f'''"""
# {manager_name} - Generated BLL Manager

# This manager was auto-generated based on the {model_name} Pydantic model.
# You may need to customize the implementation based on your specific requirements.
# """

# from typing import Any, Dict, List, Optional
# from sqlalchemy.orm import Session

# from logic.AbstractLogicManager import AbstractBLLManager
# from database.DB_YourModule import {db_class_name}  # Update import path


# class {manager_name}(AbstractBLLManager):
#     """Manager for {model_name} operations."""

#     Model = {model_name}
#     ReferenceModel = {reference_model_name}
#     NetworkModel = {network_model_name}
#     DBClass = {db_class_name}

#     def __init__(
#         self,
#         requester_id: str,
#         target_user_id: Optional[str] = None,
#         target_team_id: Optional[str] = None,
#         db: Optional[Session] = None,
#     ):
#         """Initialize the {manager_name}."""
#         super().__init__(
#             requester_id=requester_id,
#             target_user_id=target_user_id,
#             target_team_id=target_team_id,
#             db=db,
#         )

#     def _register_search_transformers(self):
#         """Register custom search transformers for this manager."""
#         # Add custom search transformers here
#         # Example:
#         # self.register_search_transformer('custom_field', self._transform_custom_field)
#         pass

#     def create_validation(self, entity):
#         """Validate entity before creation."""
#         # Add custom validation logic here
#         pass

#     def search_validation(self, params):
#         """Validate search parameters."""
#         # Add custom search validation logic here
#         pass

#     # Add custom methods here as needed
#     # Example:
#     # def custom_method(self, param: str) -> Dict[str, Any]:
#     #     """Custom method for specific business logic."""
#     #     pass
# '''

#     return template


# def analyze_manager_dependencies() -> Dict[str, Dict[str, Any]]:
#     """
#     Analyze dependencies between discovered managers.

#     Returns:
#         Dictionary mapping manager names to their dependency information
#     """
#     if not BLL_MANAGER_REGISTRY:
#         discover_bll_managers()

#     dependencies = {}

#     for manager_name, manager_class in BLL_MANAGER_REGISTRY.items():
#         if "." in manager_name:  # Skip fully qualified names
#             continue

#         deps = {
#             "depends_on": [],
#             "depended_by": [],
#             "model_references": [],
#             "db_references": [],
#             "circular_dependencies": [],
#         }

#         # Analyze model dependencies
#         if hasattr(manager_class, "Model"):
#             model_class = manager_class.Model
#             # Check for ReferenceID classes that might reference other models
#             if hasattr(model_class, "ReferenceID"):
#                 ref_class = model_class.ReferenceID
#                 # Analyze fields in ReferenceID for foreign key references
#                 if hasattr(ref_class, "__annotations__"):
#                     for field_name, field_type in ref_class.__annotations__.items():
#                         if field_name.endswith("_id"):
#                             referenced_model = field_name.removesuffix("_id")
#                             deps["model_references"].append(referenced_model)

#         # Analyze property dependencies (managers that reference other managers)
#         for prop_name in dir(manager_class):
#             if not prop_name.startswith("_"):
#                 prop = getattr(manager_class, prop_name)
#                 if isinstance(prop, property):
#                     # Check if property returns another manager
#                     try:
#                         prop_doc = inspect.getdoc(prop.fget) if prop.fget else ""
#                         if "Manager" in prop_doc:
#                             deps["depends_on"].append(prop_name)
#                     except Exception:
#                         pass

#         dependencies[manager_name] = deps

#     # Find circular dependencies
#     for manager_name, deps in dependencies.items():
#         for dep in deps["depends_on"]:
#             if dep in dependencies:
#                 if manager_name in dependencies[dep]["depends_on"]:
#                     deps["circular_dependencies"].append(dep)

#     return dependencies


# def generate_manager_documentation() -> Dict[str, Any]:
#     """
#     Generate comprehensive documentation for all discovered managers.

#     Returns:
#         Dictionary containing documentation for all managers
#     """
#     if not BLL_MANAGER_REGISTRY:
#         discover_bll_managers()

#     documentation = {
#         "managers": {},
#         "summary": {
#             "total_managers": 0,
#             "valid_managers": 0,
#             "invalid_managers": 0,
#             "managers_with_custom_methods": 0,
#             "managers_with_search_transformers": 0,
#         },
#         "dependencies": analyze_manager_dependencies(),
#     }

#     for manager_name, manager_class in BLL_MANAGER_REGISTRY.items():
#         if "." in manager_name:  # Skip fully qualified names
#             continue

#         analysis = analyze_manager_structure(manager_class)
#         validation = validate_manager_implementation(manager_class)

#         doc = {
#             "analysis": analysis,
#             "validation": validation,
#             "docstring": inspect.getdoc(manager_class) or "No documentation available",
#         }

#         documentation["managers"][manager_name] = doc
#         documentation["summary"]["total_managers"] += 1

#         if validation["is_valid"]:
#             documentation["summary"]["valid_managers"] += 1
#         else:
#             documentation["summary"]["invalid_managers"] += 1

#         if analysis["custom_methods"]:
#             documentation["summary"]["managers_with_custom_methods"] += 1

#         if analysis["search_transformers"]:
#             documentation["summary"]["managers_with_search_transformers"] += 1

#     return documentation


# def _get_or_generate_manager_class(
#     model_class: Type[BaseModel], model_registry
# ) -> Type[AbstractBLLManager]:
#     """
#     Get or generate a cached manager class for the given model.

#     Args:
#         model_class: The Pydantic model class
#         model_registry: ModelRegistry instance for caching

#     Returns:
#         Manager class (not instance)
#     """
#     # Check for cached generated manager
#     cache_key = f"{model_class.__module__}.{model_class.__name__}_GeneratedManager"

#     if model_registry is None:
#         # Create a temporary registry for caching
#         model_registry = type("TempRegistry", (), {"_generated_managers": {}})()
#     elif not hasattr(model_registry, "_generated_managers"):
#         model_registry._generated_managers = {}

#     if cache_key in model_registry._generated_managers:
#         # Return cached manager class
#         return model_registry._generated_managers[cache_key]

#     # Generate new manager class
#     manager_class = _generate_manager_class(model_class)

#     # Cache the generated class
#     model_registry._generated_managers[cache_key] = manager_class

#     # Register in the global BLL_MANAGER_REGISTRY for discovery
#     BLL_MANAGER_REGISTRY[manager_class.__name__] = manager_class

#     return manager_class


# def _generate_manager_class(model_class: Type[BaseModel]) -> Type[AbstractBLLManager]:
#     """
#     Programmatically generate a BLL manager class for a Pydantic model.

#     Args:
#         model_class: The Pydantic model class to generate a manager for

#     Returns:
#         Generated manager class that inherits from AbstractBLLManager
#     """
#     # Extract configuration from model
#     config = getattr(model_class, "ManagerConfig", None)

#     # Determine names
#     model_name = model_class.__name__
#     base_name = (
#         model_name.replace("Model", "") if model_name.endswith("Model") else model_name
#     )
#     manager_name = f"{base_name}Manager"

#     # Find or infer related models
#     reference_model = _find_reference_model(model_class, base_name)
#     network_model = _find_network_model(model_class, base_name)

#     # Build class attributes
#     class_attrs = {
#         "Model": model_class,
#         "ReferenceModel": reference_model,
#         "NetworkModel": network_model,
#         "__module__": model_class.__module__,
#         "__qualname__": manager_name,
#     }

#     # Add RouterMixin configuration if config exists
#     if config:
#         # Extract router configuration
#         if hasattr(config, "prefix"):
#             class_attrs["prefix"] = config.prefix
#         else:
#             class_attrs["prefix"] = f"/v1/{base_name.lower()}"

#         if hasattr(config, "tags"):
#             class_attrs["tags"] = config.tags
#         else:
#             class_attrs["tags"] = [f"{base_name} Management"]

#         if hasattr(config, "auth_type"):
#             class_attrs["auth_type"] = config.auth_type
#         else:
#             class_attrs["auth_type"] = AuthType.JWT

#         # Copy other config attributes
#         for attr in [
#             "factory_params",
#             "custom_routes",
#             "nested_resources",
#             "route_auth_overrides",
#             "routes_to_register",
#             "requires_root_access",
#         ]:
#             if hasattr(config, attr):
#                 class_attrs[attr] = getattr(config, attr)
#     else:
#         # Default router configuration
#         class_attrs["prefix"] = f"/v1/{base_name.lower()}"
#         class_attrs["tags"] = [f"{base_name} Management"]
#         class_attrs["auth_type"] = AuthType.JWT

#     # Add default ClassVars that may be missing
#     class_attrs.setdefault("factory_params", [])
#     class_attrs.setdefault("custom_routes", [])
#     class_attrs.setdefault("nested_resources", {})
#     class_attrs.setdefault("route_auth_overrides", {})
#     class_attrs.setdefault("auth_dependency", None)
#     class_attrs.setdefault("requires_root_access", False)

#     # Generate custom methods from config
#     if config and hasattr(config, "custom_methods"):
#         for method_name, method_func in config.custom_methods.items():
#             class_attrs[method_name] = method_func

#     # Generate nested resource properties from config
#     if config and hasattr(config, "nested_resources"):
#         for prop_name, resource_config in config.nested_resources.items():
#             if isinstance(resource_config, dict) and "model" in resource_config:
#                 # Create a property that returns a manager for the nested resource
#                 class_attrs[prop_name] = _create_nested_manager_property(
#                     prop_name, resource_config["model"]
#                 )

#     # Auto-generate navigation properties using RelationshipAnalyzer
#     analyzer = RelationshipAnalyzer(default_cache_manager)
#     relationships = analyzer.analyze_model_relationships(model_class)
#     navigation_properties = _generate_navigation_properties_from_relationships(
#         relationships, base_name
#     )
#     for prop_name, prop_func in navigation_properties.items():
#         if prop_name not in class_attrs:  # Don't override existing properties
#             class_attrs[prop_name] = prop_func

#     # Generate __init__ method with navigation property initialization
#     class_attrs["__init__"] = _create_manager_init_method(
#         navigation_properties, base_name
#     )

#     # Create the manager class
#     bases = (AbstractBLLManager, RouterMixin)
#     generated_class = type(manager_name, bases, class_attrs)

#     # Add a marker to indicate this is a generated class
#     generated_class._is_generated = True

#     return generated_class


# def _find_reference_model(
#     model_class: Type[BaseModel], base_name: str
# ) -> Optional[Type[BaseModel]]:
#     """
#     Find or create a reference model for the given model class.
#     """
#     # Try to find in the same module
#     module = inspect.getmodule(model_class)
#     reference_model_name = f"{base_name}ReferenceModel"

#     if module and hasattr(module, reference_model_name):
#         return getattr(module, reference_model_name)

#     # Check if model has a nested ReferenceModel class
#     if hasattr(model_class, "ReferenceModel"):
#         return model_class.ReferenceModel

#     # Generate a basic reference model
#     from logic.AbstractLogicManager import ApplicationModel

#     # Create field names
#     id_field_name = f"{base_name.lower()}_id"
#     model_field_name = base_name.lower()

#     ref_attrs = {
#         "__module__": model_class.__module__,
#         "__qualname__": reference_model_name,
#         "__annotations__": {
#             id_field_name: Optional[str],
#             model_field_name: Optional[model_class],
#         },
#         # Add the fields as class attributes too for hasattr() to work
#         id_field_name: None,
#         model_field_name: None,
#     }

#     return type(reference_model_name, (ApplicationModel,), ref_attrs)


# def _find_network_model(model_class: Type[BaseModel], base_name: str) -> Optional[Type]:
#     """
#     Find or create a network model for the given model class.
#     """
#     # Try to find in the same module
#     module = inspect.getmodule(model_class)
#     network_model_name = f"{base_name}NetworkModel"

#     if module and hasattr(module, network_model_name):
#         return getattr(module, network_model_name)

#     # Check if model has a nested NetworkModel class
#     if hasattr(model_class, "NetworkModel"):
#         return model_class.NetworkModel

#     # Generate a basic network model
#     from logic.AbstractLogicManager import ApplicationModel

#     # Create POST/PUT/SEARCH/Response classes
#     class POST(BaseModel):
#         __module__ = model_class.__module__
#         __qualname__ = f"{network_model_name}.POST"

#     # Add model fields to POST (excluding id and audit fields)
#     if hasattr(model_class, "model_fields"):
#         for field_name, field_info in model_class.model_fields.items():
#             if field_name not in [
#                 "id",
#                 "created_at",
#                 "created_by_user_id",
#                 "updated_at",
#                 "updated_by_user_id",
#             ]:
#                 setattr(POST, field_name, field_info)

#     class PUT(BaseModel):
#         __module__ = model_class.__module__
#         __qualname__ = f"{network_model_name}.PUT"

#     # PUT has optional versions of all fields
#     if hasattr(model_class, "model_fields"):
#         for field_name, field_info in model_class.model_fields.items():
#             if field_name not in ["id", "created_at", "created_by_user_id"]:
#                 # Make field optional
#                 field_type = (
#                     field_info.outer_type_
#                     if hasattr(field_info, "outer_type_")
#                     else Any
#                 )
#                 setattr(PUT, field_name, Optional[field_type])

#     class SEARCH(BaseModel):
#         __module__ = model_class.__module__
#         __qualname__ = f"{network_model_name}.SEARCH"
#         # Add search fields based on model
#         # This would need more sophisticated logic for real search models

#     class ResponseSingle(BaseModel):
#         __module__ = model_class.__module__
#         __qualname__ = f"{network_model_name}.ResponseSingle"

#     # Add the model as a field
#     setattr(ResponseSingle, base_name.lower(), model_class)

#     class ResponsePlural(BaseModel):
#         __module__ = model_class.__module__
#         __qualname__ = f"{network_model_name}.ResponsePlural"

#     # Add list of models as a field
#     setattr(ResponsePlural, f"{base_name.lower()}s", List[model_class])

#     # Create the network model class
#     network_attrs = {
#         "__module__": model_class.__module__,
#         "__qualname__": network_model_name,
#         "POST": POST,
#         "PUT": PUT,
#         "SEARCH": SEARCH,
#         "ResponseSingle": ResponseSingle,
#         "ResponsePlural": ResponsePlural,
#     }

#     return type(network_model_name, (), network_attrs)


# def _generate_navigation_properties_from_relationships(
#     relationships: Dict[str, Any], base_name: str
# ) -> Dict[str, property]:
#     """
#     Generate navigation properties from relationship analysis.

#     Args:
#         relationships: Relationship information from RelationshipAnalyzer
#         base_name: Base name for the model (e.g., "Team" from "TeamModel")

#     Returns:
#         Dict mapping property names to property objects
#     """
#     navigation_properties = {}

#     # Generate properties from foreign keys
#     for fk in relationships.get("foreign_keys", []):
#         related_name = fk["related_name"]
#         if related_name != base_name.lower():  # Skip self-references
#             navigation_properties[related_name] = _create_navigation_property(
#                 related_name, single=True
#             )

#     # Generate properties from direct model references
#     for ref in relationships.get("references", []):
#         field_name = ref["field_name"]
#         model_class = ref["model_class"]
#         base_related_name = (
#             model_class.__name__.replace("Model", "")
#             if model_class.__name__.endswith("Model")
#             else model_class.__name__
#         )

#         navigation_properties[field_name] = _create_navigation_property(
#             base_related_name, single=True
#         )

#     # Generate properties from collection fields
#     for collection in relationships.get("collections", []):
#         field_name = collection["field_name"]
#         item_model = collection["item_model"]
#         base_related_name = (
#             item_model.__name__.replace("Model", "")
#             if item_model.__name__.endswith("Model")
#             else item_model.__name__
#         )

#         navigation_properties[field_name] = _create_navigation_property(
#             base_related_name, single=False
#         )

#     return navigation_properties


# def _generate_navigation_properties(
#     model_class: Type[BaseModel],
#     reference_model: Optional[Type[BaseModel]],
#     base_name: str,
# ) -> Dict[str, property]:
#     """
#     Generate navigation properties based on foreign keys in the reference model.

#     Args:
#         model_class: The main Pydantic model
#         reference_model: The reference model containing foreign keys
#         base_name: Base name for the model (e.g., "Team" from "TeamModel")

#     Returns:
#         Dict mapping property names to property objects
#     """
#     navigation_properties = {}

#     if reference_model is None:
#         return navigation_properties

#     # Get type hints from the reference model
#     try:
#         type_hints = (
#             get_type_hints(reference_model)
#             if hasattr(reference_model, "__annotations__")
#             else {}
#         )
#     except Exception:
#         type_hints = getattr(reference_model, "__annotations__", {})

#     # Look for foreign key patterns and related models
#     for field_name, field_type in type_hints.items():
#         # Skip if this is the self-reference
#         if field_name == f"{base_name.lower()}_id" or field_name == base_name.lower():
#             continue

#         # Check for foreign key pattern (ending with _id)
#         if field_name.endswith("_id"):
#             # Extract the related model name
#             related_model_name = field_name.removesuffix("_id")

#             # Try to find the corresponding manager
#             manager_prop_name = f"{related_model_name}s"  # Pluralize for collection
#             single_prop_name = related_model_name

#             # Create property for single related entity access
#             navigation_properties[single_prop_name] = _create_navigation_property(
#                 related_model_name, single=True
#             )

#         # Check for direct model references (navigation properties)
#         elif (
#             inspect.isclass(field_type)
#             and issubclass(field_type, BaseModel)
#             and field_type != model_class
#         ):
#             # This is a direct reference to another model
#             model_name = field_type.__name__
#             base_related_name = (
#                 model_name.replace("Model", "")
#                 if model_name.endswith("Model")
#                 else model_name
#             )

#             # Create property for the related manager
#             navigation_properties[field_name] = _create_navigation_property(
#                 base_related_name, single=True
#             )

#         # Check for List[SomeModel] patterns (one-to-many relationships)
#         elif hasattr(field_type, "__origin__") and field_type.__origin__ in (
#             list,
#             List,
#         ):
#             args = getattr(field_type, "__args__", ())
#             if args and inspect.isclass(args[0]) and issubclass(args[0], BaseModel):
#                 related_model = args[0]
#                 model_name = related_model.__name__
#                 base_related_name = (
#                     model_name.replace("Model", "")
#                     if model_name.endswith("Model")
#                     else model_name
#                 )

#                 # Create property for the collection manager
#                 navigation_properties[field_name] = _create_navigation_property(
#                     base_related_name, single=False
#                 )

#     return navigation_properties


# def _create_navigation_property(
#     related_model_name: str, single: bool = True
# ) -> property:
#     """
#     Create a navigation property that returns a manager for a related model.

#     Args:
#         related_model_name: Name of the related model (e.g., "User" from "UserModel")
#         single: Whether this is a single entity or collection relationship

#     Returns:
#         Property that lazily creates and returns a manager
#     """
#     private_attr = f"_{related_model_name.lower()}_manager"

#     def getter(self):
#         if not hasattr(self, private_attr):
#             # Try to find the related manager class
#             possible_manager_names = [
#                 f"{related_model_name}Manager",
#                 f"{related_model_name.title()}Manager",
#             ]

#             manager_class = None
#             for manager_name in possible_manager_names:
#                 manager_class = get_manager_class(manager_name)
#                 if manager_class:
#                     break

#             if manager_class:
#                 # Create manager instance
#                 manager = manager_class(
#                     requester_id=self.requester.id,
#                     target_id=getattr(self, "target_id", None),
#                     target_team_id=getattr(self, "target_team_id", None),
#                     model_registry=self.model_registry,
#                     parent=self,
#                 )
#             else:
#                 # Try to find the model and use its Manager mixin
#                 possible_model_names = [
#                     f"{related_model_name}Model",
#                     f"{related_model_name.title()}Model",
#                 ]

#                 related_model = None
#                 for model_name in possible_model_names:
#                     # Search for the model in discovered modules
#                     if not BLL_MANAGER_REGISTRY:
#                         discover_bll_managers()

#                     # Look through manager registry to find models
#                     for manager_class in BLL_MANAGER_REGISTRY.values():
#                         if (
#                             hasattr(manager_class, "Model")
#                             and manager_class.Model.__name__ == model_name
#                         ):
#                             related_model = manager_class.Model
#                             break

#                     if related_model:
#                         break

#                 if related_model and hasattr(related_model, "Manager"):
#                     # Use the model's Manager mixin
#                     manager = related_model.Manager(
#                         model_registry=self.model_registry,
#                         requester_id=self.requester.id,
#                         target_id=getattr(self, "target_id", None),
#                         target_team_id=getattr(self, "target_team_id", None),
#                         parent=self,
#                     )
#                 else:
#                     # Log warning and return None
#                     logger.warning(
#                         f"Could not find manager for related model: {related_model_name}"
#                     )
#                     return None

#             setattr(self, private_attr, manager)

#         return getattr(self, private_attr)

#     return property(getter)


# def _create_manager_init_method(
#     navigation_properties: Dict[str, property], base_name: str
# ):
#     """
#     Create an __init__ method that initializes navigation property private attributes.

#     Args:
#         navigation_properties: Dict of navigation properties
#         base_name: Base name for the model

#     Returns:
#         __init__ method for the generated manager class
#     """

#     def __init__(
#         self,
#         requester_id: str,
#         target_id: Optional[str] = None,
#         target_team_id: Optional[str] = None,
#         model_registry: Optional[Any] = None,
#         parent: Optional[Any] = None,
#     ):
#         # Call parent __init__
#         super(self.__class__, self).__init__(
#             requester_id=requester_id,
#             target_id=target_id,
#             target_team_id=target_team_id,
#             model_registry=model_registry,
#             parent=parent,
#         )

#         # Initialize private attributes for navigation properties
#         for prop_name in navigation_properties.keys():
#             related_name = (
#                 prop_name.replace("s", "") if prop_name.endswith("s") else prop_name
#             )
#             private_attr = f"_{related_name.lower()}_manager"
#             setattr(self, private_attr, None)

#     return __init__


# def _create_nested_manager_property(
#     property_name: str, resource_model: Type[BaseModel]
# ) -> property:
#     """
#     Create a property that returns a manager for a nested resource.

#     Args:
#         property_name: Name of the property
#         resource_model: The model class for the nested resource

#     Returns:
#         Property that lazily creates and returns a manager
#     """
#     private_attr = f"_{property_name}_manager"

#     def getter(self):
#         if not hasattr(self, private_attr):
#             # Create manager for the nested resource
#             if hasattr(resource_model, "Manager"):
#                 # Use the model's Manager method
#                 manager = resource_model.Manager(
#                     model_registry=self.model_registry,
#                     requester_id=self.requester.id,
#                     target_id=self.target_id,
#                     parent=self,
#                 )
#             else:
#                 # Try to find existing manager
#                 manager_class = get_manager_for_model(resource_model)
#                 if manager_class:
#                     manager = manager_class(
#                         requester_id=self.requester.id,
#                         target_id=self.target_id,
#                         model_registry=self.model_registry,
#                         parent=self,
#                     )
#                 else:
#                     raise ValueError(
#                         f"No manager found for nested resource {resource_model.__name__}"
#                     )

#             setattr(self, private_attr, manager)

#         return getattr(self, private_attr)

#     return property(getter)


# def scaffold_all_managers() -> Dict[str, Type]:
#     """
#     Scaffold all discovered managers and return the registry.

#     Returns:
#         Dictionary of all scaffolded managers
#     """
#     return discover_bll_managers()


# def get_scaffolded_manager(manager_name: str) -> Optional[Type]:
#     """
#     Get a specific scaffolded manager by name.

#     Args:
#         manager_name: Name of the manager

#     Returns:
#         Manager class if found, None otherwise
#     """
#     if not BLL_MANAGER_REGISTRY:
#         discover_bll_managers()

#     return BLL_MANAGER_REGISTRY.get(manager_name)


# class ManagerConverter:
#     """
#     Utility class for converting between different manager representations.
#     """

#     @staticmethod
#     def manager_to_dict(manager_instance: AbstractBLLManager) -> Dict[str, Any]:
#         """
#         Convert a manager instance to a dictionary representation.

#         Args:
#             manager_instance: The manager instance

#         Returns:
#             Dictionary representation of the manager
#         """
#         return {
#             "class_name": manager_instance.__class__.__name__,
#             "module": manager_instance.__class__.__module__,
#             "requester_id": getattr(manager_instance, "requester_id", None),
#             "target_user_id": getattr(manager_instance, "target_user_id", None),
#             "target_team_id": getattr(manager_instance, "target_team_id", None),
#             "has_db_session": hasattr(manager_instance, "_db")
#             and manager_instance._db is not None,
#         }

#     @staticmethod
#     def analyze_manager_performance(manager_class: Type) -> Dict[str, Any]:
#         """
#         Analyze performance characteristics of a manager class.

#         Args:
#             manager_class: The manager class to analyze

#         Returns:
#             Dictionary with performance analysis
#         """
#         analysis = {
#             "method_count": 0,
#             "property_count": 0,
#             "complex_methods": [],
#             "database_methods": [],
#             "validation_methods": [],
#         }

#         for method_name in dir(manager_class):
#             if not method_name.startswith("_") and callable(
#                 getattr(manager_class, method_name)
#             ):
#                 analysis["method_count"] += 1

#                 # Analyze method complexity (basic heuristic)
#                 try:
#                     method = getattr(manager_class, method_name)
#                     source = inspect.getsource(method)
#                     line_count = len(source.split("\n"))

#                     if line_count > 20:  # Arbitrary threshold
#                         analysis["complex_methods"].append(
#                             {"name": method_name, "lines": line_count}
#                         )

#                     # Check for database operations
#                     if any(
#                         keyword in source.lower()
#                         for keyword in ["query", "filter", "join", "session"]
#                     ):
#                         analysis["database_methods"].append(method_name)

#                     # Check for validation methods
#                     if "validation" in method_name.lower():
#                         analysis["validation_methods"].append(method_name)

#                 except Exception:
#                     pass  # Skip if source is not available

#             elif isinstance(getattr(manager_class, method_name), property):
#                 analysis["property_count"] += 1

#         return analysis


# # Legacy compatibility class
# class ManagerGenerator:
#     """
#     Legacy compatibility class for manager generation.
#     Provides static methods that delegate to the module-level functions.
#     """

#     @staticmethod
#     def discover_managers() -> Dict[str, Type]:
#         """Discover all BLL managers."""
#         return discover_bll_managers()

#     @staticmethod
#     def analyze_manager(manager_class: Type) -> Dict[str, Any]:
#         """Analyze a manager class structure."""
#         return analyze_manager_structure(manager_class)

#     @staticmethod
#     def validate_manager(manager_class: Type) -> Dict[str, Any]:
#         """Validate a manager implementation."""
#         return validate_manager_implementation(manager_class)

#     @staticmethod
#     def generate_template(
#         model_class: Type[BaseModel], manager_name: Optional[str] = None
#     ) -> str:
#         """Generate a manager template."""
#         return generate_manager_template(model_class, manager_name)

#     @staticmethod
#     def scaffold_all() -> Dict[str, Type]:
#         """Scaffold all managers."""
#         return scaffold_all_managers()
