# """
# Pydantic to SDK Handler Generator

# This module provides functionality to automatically generate SDK handler classes
# from Pydantic BLL models, similar to how Pydantic2FastAPI generates FastAPI routers.

# The generated SDK handlers extend AbstractSDKHandler and provide methods for
# CRUD operations, search, and other resource management operations.
# """

# from typing import Any, Dict, List, Optional, Type

# import stringcase
# from fastapi import APIRouter
# from pydantic import BaseModel

# from lib.AbstractPydantic2Test import get_all_models_for_testing
# from lib.Environment import inflection
# from lib.Logging import logger
# from sdk.AbstractSDKHandler import AbstractSDKHandler, ResourceConfig

# # Global registries for SDK handlers
# BLL_MODEL_REGISTRY: Dict[str, Type[BaseModel]] = {}
# SDK_HANDLER_REGISTRY: Dict[str, Type[AbstractSDKHandler]] = {}


# def clear_sdk_registry_cache():
#     """Clear the SDK handler registry cache."""
#     global BLL_MODEL_REGISTRY, SDK_HANDLER_REGISTRY
#     BLL_MODEL_REGISTRY.clear()
#     SDK_HANDLER_REGISTRY.clear()


# def discover_bll_models_for_sdk() -> Dict[str, Type[BaseModel]]:
#     """
#     Discover BLL models that can be used for SDK generation.

#     Returns:
#         Dict mapping model names to model classes
#     """
#     global BLL_MODEL_REGISTRY

#     if not BLL_MODEL_REGISTRY:
#         # Use the existing discovery function from AbstractPydanticTest
#         models = get_all_models_for_testing()

#         # Convert list of tuples to dictionary and filter for models that inherit from ApplicationModel
#         for model_name, model_class in models:
#             if hasattr(model_class, "__mro__"):
#                 # Check if ApplicationModel is in the inheritance chain
#                 has_base_mixin = any(
#                     base.__name__ == "ApplicationModel" for base in model_class.__mro__
#                 )

#                 if has_base_mixin:
#                     BLL_MODEL_REGISTRY[model_name] = model_class

#     return BLL_MODEL_REGISTRY


# def infer_resource_name_from_model(model_class: Type[BaseModel]) -> str:
#     """
#     Infer the resource name from a BLL model class.

#     Args:
#         model_class: The BLL model class

#     Returns:
#         Resource name in snake_case format
#     """
#     class_name = model_class.__name__

#     # Remove common suffixes
#     if class_name.endswith("Model"):
#         class_name = class_name[:-5]

#     # Convert CamelCase to snake_case
#     return stringcase.snakecase(class_name)


# def infer_endpoint_prefix_from_model(model_class: Type[BaseModel]) -> str:
#     """
#     Infer the API endpoint prefix from a BLL model class.

#     Args:
#         model_class: The BLL model class

#     Returns:
#         API endpoint prefix (e.g., "/v1/user")
#     """
#     resource_name = infer_resource_name_from_model(model_class)
#     return f"/v1/{resource_name}"


# def get_model_reference_fields(model_class: Type[BaseModel]) -> Dict[str, Any]:
#     """
#     Extract reference fields from a BLL model's ReferenceID class.

#     Args:
#         model_class: The BLL model class

#     Returns:
#         Dict containing required and optional reference fields
#     """
#     reference_info = {"required_refs": [], "optional_refs": [], "has_references": False}

#     if hasattr(model_class, "ReferenceID"):
#         reference_info["has_references"] = True
#         ref_class = model_class.ReferenceID

#         # Get required reference fields
#         if hasattr(ref_class, "__annotations__"):
#             for field_name, field_type in ref_class.__annotations__.items():
#                 reference_info["required_refs"].append(field_name)

#         # Get optional reference fields
#         if hasattr(ref_class, "Optional") and hasattr(
#             ref_class.Optional, "__annotations__"
#         ):
#             for field_name, field_type in ref_class.Optional.__annotations__.items():
#                 reference_info["optional_refs"].append(field_name)

#     return reference_info


# def generate_sdk_method_create(
#     resource_name: str,
#     resource_name_plural: str,
#     model_class: Type[BaseModel],
#     endpoint_prefix: str,
#     reference_info: Dict[str, Any],
# ) -> str:
#     """Generate the create method for an SDK handler."""

#     # Build method parameters based on model fields
#     params = []
#     body_fields = []

#     # Add reference fields as parameters
#     for ref_field in reference_info["required_refs"]:
#         params.append(f"{ref_field}: str")
#         body_fields.append(f'"{ref_field}": {ref_field}')

#     for ref_field in reference_info["optional_refs"]:
#         params.append(f"{ref_field}: Optional[str] = None")
#         body_fields.append(f"if {ref_field} is not None else None")

#     # Add **kwargs for additional fields
#     params.append("**kwargs")

#     params_str = ", ".join(params)

#     method_code = f'''    def create_{resource_name}(self, {params_str}) -> Dict[str, Any]:
#         """Create a new {resource_name}.

#         Args:
#             {chr(10).join(f"            {param.split(':')[0].strip()}: {param.split(':')[1].strip() if ':' in param else 'Additional fields'}" for param in params[:-1])}
#             **kwargs: Additional {resource_name} data

#         Returns:
#             New {resource_name} information
#         """
#         data = {{
#             "{resource_name}": {{
#                 **kwargs,
#             }}
#         }}

#         # Add reference fields
#         {chr(10).join(f'        if {ref}: data["{resource_name}"]["{ref}"] = {ref}' for ref in reference_info["required_refs"])}
#         {chr(10).join(f'        if {ref}: data["{resource_name}"]["{ref}"] = {ref}' for ref in reference_info["optional_refs"])}

#         return self.post("{endpoint_prefix}", data, resource_name="{resource_name}")'''

#     return method_code


# def generate_sdk_method_get(resource_name: str, endpoint_prefix: str) -> str:
#     """Generate the get method for an SDK handler."""

#     return f'''    def get_{resource_name}(self, {resource_name}_id: str) -> Dict[str, Any]:
#         """Get a {resource_name} by ID.

#         Args:
#             {resource_name}_id: {stringcase.titlecase(resource_name)} ID

#         Returns:
#             {stringcase.titlecase(resource_name)} information
#         """
#         return self.get(f"{endpoint_prefix}/{{{resource_name}_id}}", resource_name="{resource_name}")'''


# def generate_sdk_method_list(
#     resource_name: str, resource_name_plural: str, endpoint_prefix: str
# ) -> str:
#     """Generate the list method for an SDK handler."""

#     return f'''    def list_{resource_name_plural}(
#         self,
#         offset: int = 0,
#         limit: int = 100,
#         sort_by: Optional[str] = None,
#         sort_order: str = "asc",
#     ) -> Dict[str, Any]:
#         """List {resource_name_plural} with pagination.

#         Args:
#             offset: Number of items to skip
#             limit: Maximum number of items to return
#             sort_by: Field to sort by
#             sort_order: Sort order (asc or desc)

#         Returns:
#             List of {resource_name_plural}
#         """
#         params = {{
#             "offset": offset,
#             "limit": limit,
#             "sort_by": sort_by,
#             "sort_order": sort_order,
#         }}

#         return self.get("{endpoint_prefix}", query_params=params, resource_name="{resource_name_plural}")'''


# def generate_sdk_method_search(
#     resource_name: str, resource_name_plural: str, endpoint_prefix: str
# ) -> str:
#     """Generate the search method for an SDK handler."""

#     return f'''    def search_{resource_name_plural}(
#         self, criteria: Dict[str, Any], offset: int = 0, limit: int = 100
#     ) -> Dict[str, Any]:
#         """Search for {resource_name_plural}.

#         Args:
#             criteria: Search criteria
#             offset: Number of items to skip
#             limit: Maximum number of items to return

#         Returns:
#             List of matching {resource_name_plural}
#         """
#         params = {{
#             "offset": offset,
#             "limit": limit,
#         }}

#         return self.post(
#             "{endpoint_prefix}/search", criteria, query_params=params, resource_name="{resource_name_plural}"
#         )'''


# def generate_sdk_method_update(resource_name: str, endpoint_prefix: str) -> str:
#     """Generate the update method for an SDK handler."""

#     return f'''    def update_{resource_name}(self, {resource_name}_id: str, **{resource_name}_data) -> Dict[str, Any]:
#         """Update a {resource_name}.

#         Args:
#             {resource_name}_id: {stringcase.titlecase(resource_name)} ID
#             **{resource_name}_data: {stringcase.titlecase(resource_name)} data to update

#         Returns:
#             Updated {resource_name} information
#         """
#         data = {{"{resource_name}": {resource_name}_data}}
#         return self.put(f"{endpoint_prefix}/{{{resource_name}_id}}", data, resource_name="{resource_name}")'''


# def generate_sdk_method_delete(resource_name: str, endpoint_prefix: str) -> str:
#     """Generate the delete method for an SDK handler."""

#     return f'''    def delete_{resource_name}(self, {resource_name}_id: str) -> None:
#         """Delete a {resource_name}.

#         Args:
#             {resource_name}_id: {stringcase.titlecase(resource_name)} ID
#         """
#         self.delete(f"{endpoint_prefix}/{{{resource_name}_id}}", resource_name="{resource_name}")'''


# def generate_sdk_handler_class(
#     model_class: Type[BaseModel],
#     class_name: Optional[str] = None,
#     methods_to_include: Optional[List[str]] = None,
# ) -> str:
#     """
#     Generate a complete SDK handler class for a BLL model.

#     Args:
#         model_class: The BLL model class
#         class_name: Optional custom class name
#         methods_to_include: List of methods to include (default: all)

#     Returns:
#         Generated SDK handler class code
#     """
#     if methods_to_include is None:
#         methods_to_include = ["create", "get", "list", "search", "update", "delete"]

#     # Infer names and properties
#     resource_name = infer_resource_name_from_model(model_class)
#     resource_name_plural = inflection.plural(resource_name)  # Use inflection
#     endpoint_prefix = infer_endpoint_prefix_from_model(model_class)
#     reference_info = get_model_reference_fields(model_class)

#     if class_name is None:
#         class_name = f"{model_class.__name__.replace('Model', '')}SDK"

#     # Generate class header
#     class_code = f'''from typing import Any, Dict, Optional

# from sdk.AbstractSDKHandler import AbstractSDKHandler


# class {class_name}(AbstractSDKHandler):
#     """SDK for {resource_name} management.

#     This class provides methods for {resource_name} CRUD operations,
#     search, and other management functions.
#     """

# '''

#     # Generate methods
#     method_generators = {
#         "create": lambda: generate_sdk_method_create(
#             resource_name,
#             resource_name_plural,
#             model_class,
#             endpoint_prefix,
#             reference_info,
#         ),
#         "get": lambda: generate_sdk_method_get(resource_name, endpoint_prefix),
#         "list": lambda: generate_sdk_method_list(
#             resource_name, resource_name_plural, endpoint_prefix
#         ),
#         "search": lambda: generate_sdk_method_search(
#             resource_name, resource_name_plural, endpoint_prefix
#         ),
#         "update": lambda: generate_sdk_method_update(resource_name, endpoint_prefix),
#         "delete": lambda: generate_sdk_method_delete(resource_name, endpoint_prefix),
#     }

#     for method_name in methods_to_include:
#         if method_name in method_generators:
#             class_code += method_generators[method_name]() + "\n\n"

#     return class_code


# def create_sdk_handler_class(
#     model_class: Type[BaseModel],
#     class_name: Optional[str] = None,
#     methods_to_include: Optional[List[str]] = None,
# ) -> Type[AbstractSDKHandler]:
#     """
#     Create a dynamic SDK handler class for a BLL model.

#     Args:
#         model_class: The BLL model class
#         class_name: Optional custom class name
#         methods_to_include: List of methods to include (default: all)

#     Returns:
#         Generated SDK handler class
#     """
#     if methods_to_include is None:
#         methods_to_include = ["create", "get", "list", "search", "update", "delete"]

#     # Infer names and properties
#     resource_name = infer_resource_name_from_model(model_class)
#     resource_name_plural = inflection.plural(resource_name)  # Use inflection
#     endpoint_prefix = infer_endpoint_prefix_from_model(model_class)
#     reference_info = get_model_reference_fields(model_class)

#     if class_name is None:
#         class_name = f"{model_class.__name__.replace('Model', '')}SDK"

#     # Create the class using a class definition approach to avoid metaclass conflicts
#     class_namespace = {
#         "__doc__": f"""SDK for {resource_name} management.

#     This class provides methods for {resource_name} CRUD operations,
#     search, and other management functions.
#     """,
#         "_resource_name": resource_name,
#         "_resource_name_plural": resource_name_plural,
#         "_endpoint_prefix": endpoint_prefix,
#         "_reference_info": reference_info,
#     }

#     # Create method functions with proper closure handling
#     if "create" in methods_to_include:

#         def make_create_method(res_name, endpoint):
#             def create_method(self, **kwargs):
#                 data = {res_name: kwargs}
#                 return self.post(endpoint, data, resource_name=res_name)

#             create_method.__name__ = f"create_{res_name}"
#             create_method.__doc__ = f"Create a new {res_name}."
#             return create_method

#         class_namespace[f"create_{resource_name}"] = make_create_method(
#             resource_name, endpoint_prefix
#         )

#     if "get" in methods_to_include:

#         def make_get_method(res_name, endpoint):
#             def get_method(self, resource_id: str):
#                 return self.get(f"{endpoint}/{resource_id}", resource_name=res_name)

#             get_method.__name__ = f"get_{res_name}"
#             get_method.__doc__ = f"Get a {res_name} by ID."
#             return get_method

#         class_namespace[f"get_{resource_name}"] = make_get_method(
#             resource_name, endpoint_prefix
#         )

#     if "list" in methods_to_include:

#         def make_list_method(res_name_plural, endpoint):
#             def list_method(
#                 self,
#                 offset: int = 0,
#                 limit: int = 100,
#                 sort_by: Optional[str] = None,
#                 sort_order: str = "asc",
#             ):
#                 params = {
#                     "offset": offset,
#                     "limit": limit,
#                     "sort_by": sort_by,
#                     "sort_order": sort_order,
#                 }
#                 return self.get(
#                     endpoint, query_params=params, resource_name=res_name_plural
#                 )

#             list_method.__name__ = f"list_{res_name_plural}"
#             list_method.__doc__ = f"List {res_name_plural} with pagination."
#             return list_method

#         class_namespace[f"list_{resource_name_plural}"] = make_list_method(
#             resource_name_plural, endpoint_prefix
#         )

#     if "search" in methods_to_include:

#         def make_search_method(res_name_plural, endpoint):
#             def search_method(
#                 self, criteria: Dict[str, Any], offset: int = 0, limit: int = 100
#             ):
#                 params = {"offset": offset, "limit": limit}
#                 return self.post(
#                     f"{endpoint}/search",
#                     criteria,
#                     query_params=params,
#                     resource_name=res_name_plural,
#                 )

#             search_method.__name__ = f"search_{res_name_plural}"
#             search_method.__doc__ = f"Search for {res_name_plural}."
#             return search_method

#         class_namespace[f"search_{resource_name_plural}"] = make_search_method(
#             resource_name_plural, endpoint_prefix
#         )

#     if "update" in methods_to_include:

#         def make_update_method(res_name, endpoint):
#             def update_method(self, resource_id: str, **resource_data):
#                 data = {res_name: resource_data}
#                 return self.put(
#                     f"{endpoint}/{resource_id}", data, resource_name=res_name
#                 )

#             update_method.__name__ = f"update_{res_name}"
#             update_method.__doc__ = f"Update a {res_name}."
#             return update_method

#         class_namespace[f"update_{resource_name}"] = make_update_method(
#             resource_name, endpoint_prefix
#         )

#     if "delete" in methods_to_include:

#         def make_delete_method(res_name, endpoint):
#             def delete_method(self, resource_id: str):
#                 return self.delete(f"{endpoint}/{resource_id}", resource_name=res_name)

#             delete_method.__name__ = f"delete_{res_name}"
#             delete_method.__doc__ = f"Delete a {res_name}."
#             return delete_method

#         class_namespace[f"delete_{resource_name}"] = make_delete_method(
#             resource_name, endpoint_prefix
#         )

#     # Create the class using type() with proper namespace
#     try:
#         sdk_class = type(class_name, (AbstractSDKHandler,), class_namespace)
#         return sdk_class
#     except TypeError as e:
#         # If there's still a metaclass conflict, create a simple class manually
#         class DynamicSDKHandler(AbstractSDKHandler):
#             pass

#         # Set the class name
#         DynamicSDKHandler.__name__ = class_name
#         DynamicSDKHandler.__qualname__ = class_name

#         # Add the methods to the class
#         for method_name, method_func in class_namespace.items():
#             if callable(method_func):
#                 setattr(DynamicSDKHandler, method_name, method_func)
#             elif not method_name.startswith("_"):
#                 setattr(DynamicSDKHandler, method_name, method_func)

#         # Set the docstring
#         DynamicSDKHandler.__doc__ = class_namespace.get("__doc__", "")

#         return DynamicSDKHandler


# def scaffold_all_sdk_handlers() -> Dict[str, Type[AbstractSDKHandler]]:
#     """
#     Generate SDK handler classes for all discovered BLL models.

#     Returns:
#         Dict mapping handler names to handler classes
#     """
#     global SDK_HANDLER_REGISTRY

#     # Discover BLL models
#     bll_models = discover_bll_models_for_sdk()

#     # Generate SDK handlers for each model
#     for model_name, model_class in bll_models.items():
#         try:
#             handler_class = create_sdk_handler_class(model_class)
#             handler_name = f"{model_name.replace('Model', '')}SDK"
#             SDK_HANDLER_REGISTRY[handler_name] = handler_class

#         except Exception as e:
#             logger.debug(f"Error creating SDK handler for {model_name}: {e}")

#     return SDK_HANDLER_REGISTRY


# def get_scaffolded_sdk_handler(handler_name: str) -> Optional[Type[AbstractSDKHandler]]:
#     """
#     Get a scaffolded SDK handler by name.

#     Args:
#         handler_name: Name of the SDK handler

#     Returns:
#         SDK handler class or None if not found
#     """
#     return SDK_HANDLER_REGISTRY.get(handler_name)


# def list_scaffolded_sdk_handlers() -> List[str]:
#     """
#     List all scaffolded SDK handler names.

#     Returns:
#         List of SDK handler names
#     """
#     return list(SDK_HANDLER_REGISTRY.keys())


# def analyze_model_sdk_requirements() -> Dict[str, Dict[str, Any]]:
#     """
#     Analyze BLL models and their SDK generation requirements.

#     Returns:
#         Dict mapping model names to their analysis
#     """
#     bll_models = discover_bll_models_for_sdk()
#     analysis = {}

#     for model_name, model_class in bll_models.items():
#         model_analysis = {
#             "model_class": model_class,
#             "resource_name": infer_resource_name_from_model(model_class),
#             "endpoint_prefix": infer_endpoint_prefix_from_model(model_class),
#             "reference_info": get_model_reference_fields(model_class),
#             "has_references": False,
#             "reference_fields": [],
#             "suggested_methods": [
#                 "create",
#                 "get",
#                 "list",
#                 "search",
#                 "update",
#                 "delete",
#             ],
#         }

#         # Analyze reference fields
#         ref_info = model_analysis["reference_info"]
#         if ref_info["has_references"]:
#             model_analysis["has_references"] = True
#             model_analysis["reference_fields"] = (
#                 ref_info["required_refs"] + ref_info["optional_refs"]
#             )

#         analysis[model_name] = model_analysis

#     return analysis


# def generate_sdk_documentation() -> Dict[str, Any]:
#     """
#     Generate comprehensive documentation for the SDK generation system.

#     Returns:
#         Dict containing documentation and analysis
#     """
#     # Analyze models
#     model_analysis = analyze_model_sdk_requirements()

#     # Generate handlers
#     handlers = scaffold_all_sdk_handlers()

#     # Create documentation
#     documentation = {
#         "summary": {
#             "total_models": len(model_analysis),
#             "total_handlers": len(handlers),
#             "successful_generations": len(handlers),
#             "failed_generations": len(model_analysis) - len(handlers),
#         },
#         "models": model_analysis,
#         "handlers": {},
#         "analysis": model_analysis,
#     }

#     # Document each handler
#     for handler_name, handler_class in handlers.items():
#         handler_methods = [
#             method
#             for method in dir(handler_class)
#             if not method.startswith("_") and callable(getattr(handler_class, method))
#         ]

#         documentation["handlers"][handler_name] = {
#             "class_name": handler_class.__name__,
#             "methods": handler_methods,
#             "method_count": len(handler_methods),
#             "docstring": getattr(handler_class, "__doc__", ""),
#         }

#     return documentation


# class SDKGenerator:
#     """
#     Legacy class for backward compatibility.
#     Provides static methods for SDK generation.
#     """

#     @staticmethod
#     def discover_models() -> Dict[str, Type[BaseModel]]:
#         """Discover BLL models for SDK generation."""
#         return discover_bll_models_for_sdk()

#     @staticmethod
#     def create_handler_from_model(
#         model_class: Type[BaseModel], **kwargs
#     ) -> Type[AbstractSDKHandler]:
#         """Create an SDK handler from a BLL model."""
#         return create_sdk_handler_class(model_class, **kwargs)

#     @staticmethod
#     def scaffold_all() -> Dict[str, Type[AbstractSDKHandler]]:
#         """Generate all SDK handlers."""
#         return scaffold_all_sdk_handlers()

#     @staticmethod
#     def generate_documentation() -> Dict[str, Any]:
#         """Generate SDK documentation."""
#         return generate_sdk_documentation()


# # ===== ROUTER SDK EXTENSION =====


# class RouterWithSDK:
#     """
#     Enhanced router wrapper that extends APIRouter with SDK functionality.

#     This class wraps the original APIRouter and adds a .SDK property that provides
#     AbstractSDKHandler functionality for interfacing with the router's endpoints.
#     """

#     def __init__(self, api_router: APIRouter, manager_class: Type, model_registry):
#         """
#         Initialize the enhanced router.

#         Args:
#             api_router: The original FastAPI router
#             manager_class: The BLL manager class that created this router
#             model_registry: The model registry for schema generation
#         """
#         self._router = api_router
#         self._manager_class = manager_class
#         self._model_registry = model_registry
#         self._sdk_handler_class = None

#         # Forward all APIRouter attributes to maintain compatibility
#         self.__dict__.update(api_router.__dict__)

#     def __getattr__(self, name):
#         """Forward any missing attributes to the wrapped APIRouter."""
#         return getattr(self._router, name)

#     def __setattr__(self, name, value):
#         """Set attributes on either this wrapper or the wrapped router as appropriate."""
#         if name.startswith("_") or name in ["SDK"]:
#             # Private attributes and SDK stay on the wrapper
#             super().__setattr__(name, value)
#         else:
#             # Public attributes go to the wrapped router
#             if hasattr(self, "_router"):
#                 setattr(self._router, name, value)
#             else:
#                 super().__setattr__(name, value)

#     @property
#     def SDK(self) -> Type[AbstractSDKHandler]:
#         """
#         Get the SDK handler class for this router's endpoints.

#         Returns:
#             A dynamically generated AbstractSDKHandler subclass configured
#             to interface with this router's endpoints.
#         """
#         if self._sdk_handler_class is None:
#             self._sdk_handler_class = self._create_sdk_handler_class()
#         return self._sdk_handler_class

#     def _create_sdk_handler_class(self) -> Type[AbstractSDKHandler]:
#         """
#         Create an SDK handler class based on the router configuration.

#         Returns:
#             Generated AbstractSDKHandler subclass
#         """
#         # Extract router metadata
#         router_prefix = getattr(self._router, "prefix", "") or getattr(
#             self._manager_class, "prefix", ""
#         )
#         router_tags = getattr(self._router, "tags", []) or getattr(
#             self._manager_class, "tags", []
#         )
#         auth_type = getattr(self._manager_class, "auth_type", None)

#         # Determine resource name from the manager class
#         if hasattr(self._manager_class, "Model") and hasattr(
#             self._manager_class.Model, "__name__"
#         ):
#             model_name = self._manager_class.Model.__name__
#             # Remove 'Mock' prefix and 'Model' suffix for test classes
#             clean_name = model_name.replace("Mock", "").replace("Model", "")
#             resource_name = clean_name.lower() if clean_name else "resource"
#         else:
#             # Fallback to manager class name
#             manager_name = self._manager_class.__name__
#             # Remove 'Mock' prefix and 'Manager' suffix for test classes
#             clean_name = manager_name.replace("Mock", "").replace("Manager", "")
#             resource_name = clean_name.lower() if clean_name else "resource"

#         # Ensure resource_name is not empty and create plural form
#         if not resource_name:
#             resource_name = "resource"
#         resource_name_plural = inflection.plural(resource_name)

#         # Clean up the prefix to get the endpoint
#         endpoint_prefix = router_prefix.rstrip("/")
#         if not endpoint_prefix.startswith("/"):
#             endpoint_prefix = f"/{endpoint_prefix}"

#         # Create the SDK handler class name
#         sdk_class_name = f"{resource_name.title()}RouterSDK"

#         # Define the SDK handler class
#         class GeneratedRouterSDK(AbstractSDKHandler):
#             def _configure_resources(self) -> Dict[str, ResourceConfig]:
#                 return {
#                     resource_name: ResourceConfig(
#                         name=resource_name,
#                         name_plural=resource_name_plural,
#                         endpoint=endpoint_prefix,
#                         supports_search=True,
#                         supports_batch=True,
#                     )
#                 }

#         # Set the class name for better debugging
#         GeneratedRouterSDK.__name__ = sdk_class_name
#         GeneratedRouterSDK.__qualname__ = sdk_class_name

#         # Store router metadata for potential future use
#         GeneratedRouterSDK._router_metadata = {
#             "prefix": router_prefix,
#             "tags": router_tags,
#             "auth_type": auth_type,
#             "manager_class": self._manager_class,
#             "model_registry": self._model_registry,
#         }

#         return GeneratedRouterSDK

#     # Forward common APIRouter methods while maintaining the wrapper
#     def include_router(self, *args, **kwargs):
#         """Forward include_router to the wrapped router."""
#         return self._router.include_router(*args, **kwargs)

#     def add_api_route(self, *args, **kwargs):
#         """Forward add_api_route to the wrapped router."""
#         return self._router.add_api_route(*args, **kwargs)

#     def get(self, *args, **kwargs):
#         """Forward get decorator to the wrapped router."""
#         return self._router.get(*args, **kwargs)

#     def post(self, *args, **kwargs):
#         """Forward post decorator to the wrapped router."""
#         return self._router.post(*args, **kwargs)

#     def put(self, *args, **kwargs):
#         """Forward put decorator to the wrapped router."""
#         return self._router.put(*args, **kwargs)

#     def patch(self, *args, **kwargs):
#         """Forward patch decorator to the wrapped router."""
#         return self._router.patch(*args, **kwargs)

#     def delete(self, *args, **kwargs):
#         """Forward delete decorator to the wrapped router."""
#         return self._router.delete(*args, **kwargs)


# def extend_router_with_sdk(
#     api_router: APIRouter, manager_class: Type, model_registry
# ) -> RouterWithSDK:
#     """
#     Extend an APIRouter with SDK functionality.

#     Args:
#         api_router: The FastAPI router to extend
#         manager_class: The BLL manager class that created this router
#         model_registry: The model registry for schema generation

#     Returns:
#         RouterWithSDK instance with .SDK property
#     """
#     return RouterWithSDK(api_router, manager_class, model_registry)


# # Monkey patch RouterMixin to automatically extend routers with SDK functionality
# def _patch_router_mixin():
#     """
#     Monkey patch RouterMixin.Router to automatically return RouterWithSDK instances.
#     """
#     try:
#         from lib.Pydantic2FastAPI import RouterMixin

#         # Store the original Router method
#         original_router_method = RouterMixin.Router

#         @classmethod
#         def enhanced_router_method(cls, model_registry):
#             """Enhanced Router method that returns RouterWithSDK."""
#             # Call the original Router method to get the APIRouter
#             api_router = original_router_method.__func__(cls, model_registry)

#             # Extend it with SDK functionality
#             return extend_router_with_sdk(api_router, cls, model_registry)

#         # Replace the Router method
#         RouterMixin.Router = enhanced_router_method

#         logger.debug(
#             "Successfully patched RouterMixin.Router to include SDK functionality"
#         )

#     except ImportError as e:
#         logger.debug(f"Could not patch RouterMixin (import error): {e}")
#     except Exception as e:
#         logger.error(f"Error patching RouterMixin: {e}")


# # Apply the monkey patch when this module is imported
# _patch_router_mixin()
