from abc import ABC, ABCMeta, abstractmethod
from enum import Enum
from inspect import getmembers, isfunction
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple, Type

from fastapi import APIRouter
from ordered_set import OrderedSet

try:
    import pytest
except ImportError:
    pytest = None
import stringcase

from lib.Dependencies import Dependencies
from lib.Environment import AbstractRegistry, env
from lib.Logging import logger
from lib.Pydantic import classproperty
from logic.BLL_Providers import ProviderInstanceModel, RotationManager

# Imports needed for patching in tests and used in methods
try:
    from sqlalchemy import select

    from database.DatabaseManager import DatabaseManager
    from lib.Environment import inflection
except ImportError:
    # Handle case where these modules might not be available during testing
    # Removed inflect_engine - using shared inflection from Environment
    select = None


def get_session(db_manager: Optional[DatabaseManager] = None):
    """
    Get database session for extension operations.

    Args:
        db_manager: Optional DatabaseManager instance. If not provided, will attempt to get from environment.

    Returns:
        Database session instance or None if not available
    """
    try:
        if db_manager:
            return db_manager.get_session()
        else:
            # Try to get from singleton if available (for backward compatibility)
            from database.DatabaseManager import DatabaseManager as DBM

            if hasattr(DBM, "get_instance"):
                instance = DBM.get_instance()
                if instance:
                    return instance.get_session()
            logger.warning("No database manager available for session")
            return None
    except (ImportError, NameError, AttributeError, RuntimeError) as e:
        logger.warning(f"Database not available - returning None for session: {e}")
        return None


# Define type for hook structure
HookPath = Tuple[str, str, str, str, str]  # layer, domain, entity, function, time
HookRegistry = Dict[HookPath, List[Callable]]


class ExtensionType(Enum):
    """Types of extensions based on their components."""

    ENDPOINTS = "endpoints"  # Has EP files or routers
    DATABASE = "database"  # Has DB files
    EXTERNAL = "external"  # Has PRV files or external models


def ability(name: Optional[str] = None, enabled: bool = True) -> Callable:
    """
    Decorator to mark a static method as an extension ability.

    The type of ability is determined by context:
    - If applied to a method in the extension class itself, it's a meta ability
    - If applied to a method in the AbstractProvider inner class, it's an abstract ability
    - If applied to a method in a provider implementation, it's a concrete ability
    """

    def decorator(method: Callable) -> Callable:
        ability_name = name or method.__name__

        # The ability type will be determined at runtime based on which class it's defined in
        method._ability_info = {
            "name": ability_name,
            "enabled": enabled,
        }
        logger.debug(
            f"Decorated static method {method.__name__} as ability '{ability_name}'"
        )
        return method

    return decorator


class ExtensionRegistry(AbstractRegistry):
    """Registry for managing static extension classes and their models."""

    extensions_static_routes: ClassVar[Dict[str, Dict]] = {}

    @classmethod
    def register_route(cls, extension_name: str, method: Callable):
        router_info = cls.extensions_static_routes.get(extension_name)
        if router_info is None:
            router = APIRouter(
                prefix=f"/extensions/{extension_name}",
                tags=[f"{extension_name} Extension"],
            )
            router_info = {
                "router": router,
                "model_name": f"ext_{extension_name}_static",
                "module_name": f"Extension_{extension_name}_StaticRoutes",
            }
            cls.extensions_static_routes[extension_name] = router_info
        else:
            router = router_info["router"]

        method_name = method.__name__
        for config in method._static_route_config:
            # Extract route configuration
            path = getattr(config, "path", f"/{method_name}")
            http_method = (
                getattr(config, "method", "POST").value.lower()
                if hasattr(getattr(config, "method", "POST"), "value")
                else str(getattr(config, "method", "POST")).lower()
            )
            summary = getattr(config, "summary", f"{extension_name}.{method_name}")
            description = getattr(config, "description", method.__doc__)

            # Add the route to the router
            router_method = getattr(router, http_method)
            router_method(
                path,
                summary=summary,
                description=description,
                response_model=None,  # Let FastAPI infer from return type
            )(method)

            logger.debug(
                f"Added static route: {http_method.upper()} "
                f"/extensions/{extension_name}{path} -> {method_name}"
            )

    def __init__(self, extensions_csv: str):
        import glob
        import importlib
        import inspect
        import os
        import sys

        from lib.Logging import logger

        self.extensions: OrderedSet[Type[AbstractStaticExtension]] = (
            OrderedSet()
        )  # Extension classes in dependency order
        self._extension_name_map = (
            {}
        )  # Maps extension name to extension class for quick lookup
        self.loaded_extensions = {}  # Maps extension name to version string
        self.extension_models = (
            {}
        )  # Maps target model class path to list of extension models
        self.extension_abilities = (
            {}
        )  # Maps extension name to list of abilities (both meta and provider)
        self.extension_providers = {}  # Maps extension name to list of provider classes
        self.provider_abilities = {}  # Maps provider class to list of abilities

        # Load extensions from CSV
        if not extensions_csv:
            logger.debug("No extensions configured for registry")
            return

        extension_names = [
            name.strip() for name in extensions_csv.split(",") if name.strip()
        ]

        if not extension_names:
            logger.debug("No valid extension names found")
            return

        # Get the source directory
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Register each requested extension - dependencies will be handled automatically
        for extension_name in extension_names:
            try:
                # Find the extension module
                scope_dir = os.path.join(src_dir, "extensions", extension_name)
                if not os.path.exists(scope_dir):
                    logger.warning(f"Extension directory not found: {scope_dir}")
                    continue

                files_pattern = os.path.join(scope_dir, "EXT_*.py")
                matching_files = glob.glob(files_pattern)

                # Filter out test files
                ext_files = [
                    f
                    for f in matching_files
                    if not os.path.basename(f).endswith("_test.py")
                ]

                extension_class = None
                for file_path in ext_files:
                    module_name = f"extensions.{extension_name}.{os.path.basename(file_path)[:-3]}"

                    try:
                        # Create extension router
                        router_info = ExtensionRegistry.extensions_static_routes.get(
                            extension_name
                        )
                        if router_info is None:
                            ExtensionRegistry.extensions_static_routes[
                                extension_name
                            ] = {
                                "router": APIRouter(
                                    prefix=f"/extensions/{extension_name}",
                                    tags=[f"{extension_name} Extension"],
                                ),
                                "model_name": f"ext_{extension_name}_static",
                                "module_name": f"Extension_{extension_name}_StaticRoutes",
                            }

                        # Import the module
                        if module_name not in sys.modules:
                            spec = importlib.util.spec_from_file_location(
                                module_name, file_path
                            )
                            if spec and spec.loader:
                                module = importlib.util.module_from_spec(spec)
                                sys.modules[module_name] = module
                                spec.loader.exec_module(module)
                        else:
                            module = sys.modules[module_name]

                        # Find AbstractStaticExtension subclass
                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)
                            if (
                                inspect.isclass(attr)
                                and issubclass(attr, AbstractStaticExtension)
                                and attr != AbstractStaticExtension
                                and hasattr(attr, "name")
                                and attr.name == extension_name
                            ):
                                extension_class = attr
                                break

                        if extension_class:
                            break

                    except Exception as e:
                        logger.error(f"Error importing {module_name}: {e}")

                if extension_class:
                    # Register will handle dependencies automatically
                    self.register_extension(extension_class)
                else:
                    logger.warning(
                        f"Could not find extension class for {extension_name}"
                    )
            except Exception as e:
                logger.error(f"Failed to load extension {extension_name}: {e}")

    @property
    def csv(self) -> str:
        """Get CSV string of extension names in dependency order."""
        return ",".join(ext_class.name for ext_class in self.extensions)

    def register_extension(self, extension_class: Type["AbstractStaticExtension"]):
        """Register a static extension class and automatically handle recursive dependencies."""
        import inspect

        from lib.Logging import logger

        extension_name = extension_class.name

        # Skip if already registered
        if extension_name in self._extension_name_map:
            logger.debug(f"Extension {extension_name} already registered")
            return

        # First, recursively register all dependencies
        self._register_dependencies(extension_class)

        # Now register this extension (dependencies are already in the OrderedSet)
        self.extensions.add(extension_class)
        self._extension_name_map[extension_name] = extension_class

        # Track loaded extension with version
        version = getattr(extension_class, "version", "0.1.0")
        self.loaded_extensions[extension_name] = version

        # Discover and track abilities
        self._discover_extension_abilities(extension_class)

        # Discover and track providers
        self._discover_extension_providers(extension_class)

        logger.debug(f"Registered extension: {extension_name} (version: {version})")

    def _register_dependencies(self, extension_class: Type["AbstractStaticExtension"]):
        """Recursively register all dependencies of an extension."""
        import glob
        import importlib
        import inspect
        import os
        import sys

        from lib.Dependencies import EXT_Dependency
        from lib.Logging import logger

        # Check if this extension has dependencies
        if (
            not hasattr(extension_class, "dependencies")
            or not extension_class.dependencies
        ):
            return

        # Get extension dependencies
        ext_deps = []
        if hasattr(extension_class.dependencies, "ext"):
            # Dependencies object with .ext property
            ext_deps = [
                dep
                for dep in extension_class.dependencies.ext
                if isinstance(dep, EXT_Dependency) and not dep.optional
            ]
        elif hasattr(extension_class.dependencies, "__iter__"):
            # Direct list/iterable of EXT_Dependency objects
            ext_deps = [
                dep
                for dep in extension_class.dependencies
                if isinstance(dep, EXT_Dependency) and not dep.optional
            ]

        # Process each dependency
        for dep in ext_deps:
            dep_name = dep.name

            # Skip if already registered
            if dep_name in self._extension_name_map:
                continue

            # Try to load the dependency extension
            try:
                # Import the dependency extension module
                src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                dep_module_pattern = os.path.join(
                    src_dir, "extensions", dep_name, "EXT_*.py"
                )
                dep_files = glob.glob(dep_module_pattern)

                dep_class = None
                for dep_file in dep_files:
                    if dep_file.endswith("_test.py"):
                        continue

                    module_name = (
                        f"extensions.{dep_name}.{os.path.basename(dep_file)[:-3]}"
                    )

                    # Import the module if not already imported
                    if module_name not in sys.modules:
                        spec = importlib.util.spec_from_file_location(
                            module_name, dep_file
                        )
                        if spec and spec.loader:
                            module = importlib.util.module_from_spec(spec)
                            sys.modules[module_name] = module
                            spec.loader.exec_module(module)
                    else:
                        module = sys.modules[module_name]

                    # Find the extension class
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (
                            inspect.isclass(attr)
                            and issubclass(attr, AbstractStaticExtension)
                            and attr != AbstractStaticExtension
                            and hasattr(attr, "name")
                            and attr.name == dep_name
                        ):
                            dep_class = attr
                            break

                    if dep_class:
                        break

                if dep_class:
                    # Recursively register the dependency
                    logger.debug(
                        f"Loading dependency {dep_name} for {extension_class.name}"
                    )
                    self.register_extension(dep_class)
                else:
                    logger.warning(
                        f"Could not find extension class for dependency {dep_name}"
                    )

            except Exception as e:
                logger.error(f"Failed to load dependency {dep_name}: {e}")

    def discover_extension_models(self, extension_names: List[str]):
        """Discover and register extension models from the specified extensions, filtering by extension type."""
        import glob
        import importlib
        import os
        import sys

        from lib.Logging import logger

        for extension_name in extension_names:
            extension_scope = f"extensions.{extension_name}"

            try:
                # Check extension type
                extension_class = self._extension_name_map.get(extension_name)
                if extension_class:
                    ext_types = extension_class.types

                    # Skip if not database or external type
                    if not (
                        ExtensionType.DATABASE in ext_types
                        or ExtensionType.EXTERNAL in ext_types
                    ):
                        logger.debug(
                            f"Skipping model discovery for {extension_name} (types: {ext_types})"
                        )
                        continue

                # Get the source directory to make the pattern absolute
                src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

                # Find both BLL and PRV files in the extension directory
                file_patterns = [
                    (
                        "BLL",
                        os.path.join(src_dir, f"extensions/{extension_name}/BLL_*.py"),
                    ),
                    (
                        "PRV",
                        os.path.join(src_dir, f"extensions/{extension_name}/PRV_*.py"),
                    ),
                ]

                for file_type, pattern in file_patterns:
                    files = glob.glob(pattern)

                    logger.debug(
                        f"Extension discovery for {extension_name} ({file_type}): pattern={pattern}, files={files}"
                    )

                    for file_path in files:
                        # Skip test files
                        if file_path.endswith("_test.py"):
                            continue

                        # Convert file path to module name
                        relative_path = os.path.relpath(file_path, src_dir)
                        module_path = (
                            relative_path.replace("/", ".")
                            .replace("\\", ".")
                            .replace(".py", "")
                        )

                        logger.debug(f"Processing {file_type} file: {file_path}")
                        logger.debug(f"Module path: {module_path}")

                        try:
                            # Import the module
                            module = importlib.import_module(module_path)
                            logger.debug(f"Successfully imported module: {module_path}")

                            # Process the module to find models
                            for attr_name in dir(module):
                                attr = getattr(module, attr_name)

                                # Skip if not a class
                                if not hasattr(attr, "__bases__"):
                                    continue

                                logger.debug(
                                    f"Checking attribute: {attr_name}, type: {type(attr)}"
                                )

                                # Check for extension models (BLL)
                                if (
                                    any(
                                        base.__name__ == "BaseModel"
                                        for base in attr.__mro__
                                    )
                                    and hasattr(attr, "_is_extension_model")
                                    and hasattr(attr, "_extension_target")
                                ):
                                    # This is an extension model
                                    target_model = attr._extension_target
                                    target_key = f"{target_model.__module__}.{target_model.__name__}"
                                    if target_key not in self.extension_models:
                                        self.extension_models[target_key] = []
                                    self.extension_models[target_key].append(attr)
                                    logger.debug(
                                        f"Found extension model {attr.__name__} for {target_model.__name__} (target_key: {target_key})"
                                    )

                                # Check for external models (PRV)
                                elif (
                                    file_type == "PRV"
                                    and any(
                                        "AbstractExternalModel" in base.__name__
                                        for base in attr.__mro__
                                    )
                                    and not attr.__name__.startswith("Abstract")
                                ):
                                    # This is an external model
                                    # Store it in a special key for external models
                                    external_key = (
                                        f"external.{extension_name}.{attr.__name__}"
                                    )
                                    if external_key not in self.extension_models:
                                        self.extension_models[external_key] = []
                                    self.extension_models[external_key].append(attr)
                                    logger.debug(
                                        f"Found external model {attr.__name__} in {extension_name}"
                                    )

                        except ImportError as import_err:
                            logger.debug(
                                f"Could not import {module_path}: {import_err}"
                            )
                        except Exception as module_err:
                            logger.debug(
                                f"Error processing module {module_path}: {module_err}"
                            )

            except Exception as e:
                logger.error(
                    f"Error discovering extension models for {extension_name}: {e}"
                )

    def get_extension_models_for_target(self, target_model):
        """Get all extension models for a given target model."""
        target_key = f"{target_model.__module__}.{target_model.__name__}"
        return self.extension_models.get(target_key, [])

    def check_dependencies(
        self, extension_class: Type["AbstractStaticExtension"]
    ) -> Dict[str, bool]:
        """
        Check if all dependencies for an extension are satisfied.

        Args:
            extension_class: The extension class to check dependencies for

        Returns:
            Dict mapping dependency names to satisfaction status
        """
        from lib.Dependencies import Dependencies

        # Get dependencies from the extension
        dependencies = getattr(extension_class, "dependencies", None)
        if not dependencies or not isinstance(dependencies, Dependencies):
            return {}

        # Check all dependencies
        return dependencies.check(self.loaded_extensions)

    def are_optional_dependencies_met(
        self, extension_class: Type["AbstractStaticExtension"]
    ) -> bool:
        """
        Check if all optional EXT_Dependencies for an extension are met.

        Args:
            extension_class: The extension class to check optional dependencies for

        Returns:
            bool: True if all optional extension dependencies are satisfied
        """
        from lib.Dependencies import Dependencies, EXT_Dependency

        # Get dependencies from the extension
        dependencies = getattr(extension_class, "dependencies", None)
        if not dependencies or not isinstance(dependencies, Dependencies):
            return True

        # Check only optional extension dependencies
        for dep in dependencies.ext:
            if dep.optional and dep.name not in self.loaded_extensions:
                return False

        return True

    def resolve_extension_dependencies(
        self, available_extensions: Dict[str, Type["AbstractStaticExtension"]]
    ) -> List[str]:
        """
        Resolve loading order for extensions based on their dependencies using topological sort.

        Args:
            available_extensions: Dictionary mapping extension names to extension classes

        Returns:
            List of extension names in loading order

        Raises:
            ValueError: If circular dependencies are detected
        """
        from lib.Dependencies import EXT_Dependency

        # Build dependency graph
        dependency_graph = {}
        for ext_name, ext_class in available_extensions.items():
            deps = []
            if hasattr(ext_class, "dependencies") and ext_class.dependencies:
                # Handle both Dependencies object and list of dependencies
                if hasattr(ext_class.dependencies, "ext"):
                    # Dependencies object with .ext property
                    for dep in ext_class.dependencies.ext:
                        if not dep.optional:  # Only consider required dependencies
                            deps.append(dep.name)
                elif hasattr(ext_class.dependencies, "__iter__"):
                    # Direct list/iterable of EXT_Dependency objects
                    for dep in ext_class.dependencies:
                        if isinstance(dep, EXT_Dependency) and not dep.optional:
                            deps.append(dep.name)
            dependency_graph[ext_name] = deps

        # Topological sort using Kahn's algorithm
        in_degree = {ext: 0 for ext in dependency_graph}
        for ext_name, deps in dependency_graph.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[ext_name] += 1

        # Start with extensions that have no dependencies
        queue = [ext for ext, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            current = queue.pop(0)
            result.append(current)

            # For each extension that depends on the current extension
            for ext_name, deps in dependency_graph.items():
                if current in deps:
                    in_degree[ext_name] -= 1
                    if in_degree[ext_name] == 0:
                        queue.append(ext_name)

        # Check for circular dependencies
        if len(result) != len(dependency_graph):
            remaining = set(dependency_graph.keys()) - set(result)
            raise ValueError(
                f"Circular dependency detected among extensions: {remaining}"
            )
        return result

    def install_extension_dependencies(
        self, extension_names: List[str]
    ) -> Dict[str, bool]:
        """
        Install PIP dependencies for the specified extensions.

        Args:
            extension_names: List of extension names to install dependencies for

        Returns:
            Dict mapping dependency names to installation success status
        """
        import importlib
        import os

        from lib.Dependencies import Dependencies
        from lib.Logging import logger

        results = {}

        for extension_name in extension_names:
            try:
                # Try to import the extension module to get its dependencies
                extension_module_name = (
                    f"extensions.{extension_name}.EXT_{extension_name}"
                )

                try:
                    extension_module = importlib.import_module(extension_module_name)
                except ImportError:
                    logger.debug(
                        f"No EXT module found for {extension_name}, skipping dependency installation"
                    )
                    continue

                # Look for extension class with dependencies
                for attr_name in dir(extension_module):
                    attr = getattr(extension_module, attr_name)
                    if (
                        hasattr(attr, "__bases__")
                        and hasattr(attr, "dependencies")
                        and hasattr(attr, "name")
                        and attr.name == extension_name
                    ):

                        dependencies = attr.dependencies
                        if isinstance(dependencies, Dependencies):
                            # Install PIP and system dependencies
                            dep_results = dependencies.install()
                            results.update(dep_results)
                            logger.debug(
                                f"Installed dependencies for {extension_name}: {dep_results}"
                            )
                        break

            except Exception as e:
                logger.error(
                    f"Error installing dependencies for extension {extension_name}: {e}"
                )
                results[f"{extension_name}_error"] = False

        return results

    def _discover_extension_abilities(
        self, extension_class: Type["AbstractStaticExtension"]
    ):
        """Discover and track abilities from an extension class."""
        import inspect

        from lib.Logging import logger

        extension_name = extension_class.name
        abilities = []

        # Get meta abilities from methods decorated with @ability in the extension class
        for name, method in inspect.getmembers(
            extension_class, predicate=inspect.isfunction
        ):
            if hasattr(method, "_ability_info"):
                ability_info = method._ability_info
                abilities.append(
                    {
                        "name": ability_info["name"],
                        "meta": True,  # Extension-level abilities are always meta
                        "extension_name": extension_name,
                        "type": "meta",
                    }
                )
                logger.debug(
                    f"Found meta ability {ability_info['name']} for extension {extension_name}"
                )

        # Check for inner AbstractProvider class and its abstract abilities
        if hasattr(extension_class, "AbstractProvider") or hasattr(
            extension_class, "__dict__"
        ):
            # Look for inner classes that define abstract abilities
            for attr_name, attr_value in extension_class.__dict__.items():
                if (
                    inspect.isclass(attr_value)
                    and "Abstract" in attr_name
                    and "Provider" in attr_name
                ):
                    # Found abstract provider class, scan its abilities
                    for method_name, method in inspect.getmembers(
                        attr_value, predicate=inspect.isfunction
                    ):
                        if hasattr(method, "_ability_info"):
                            ability_info = method._ability_info
                            abilities.append(
                                {
                                    "name": ability_info["name"],
                                    "meta": False,  # Provider abilities are not meta
                                    "extension_name": extension_name,
                                    "type": "abstract",
                                    "provider_class": attr_name,
                                }
                            )
                            logger.debug(
                                f"Found abstract ability {ability_info['name']} in {attr_name} for extension {extension_name}"
                            )

        # Also check the _abilities set for any additional abilities
        if hasattr(extension_class, "_abilities") and extension_class._abilities:
            # Get decorated ability names to avoid duplicates
            decorated_ability_names = {a["name"] for a in abilities}

            for ability_name in extension_class._abilities:
                if ability_name not in decorated_ability_names:
                    abilities.append(
                        {
                            "name": ability_name,
                            "meta": True,  # Extension-level abilities are meta by default
                            "extension_name": extension_name,
                            "type": "meta",
                        }
                    )
                    logger.debug(
                        f"Found non-decorated meta ability {ability_name} for extension {extension_name}"
                    )

        self.extension_abilities[extension_name] = abilities
        logger.debug(f"Total abilities for {extension_name}: {len(abilities)}")

    def _discover_extension_providers(
        self, extension_class: Type["AbstractStaticExtension"]
    ):
        """Discover and track providers from an extension."""
        import glob
        import importlib
        import inspect
        import os
        import sys

        from lib.Logging import logger

        extension_name = extension_class.name
        providers = []

        # Get the source directory
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        extension_dir = os.path.join(src_dir, "extensions", extension_name)

        # Find PRV_*.py files
        pattern = os.path.join(extension_dir, "PRV_*.py")
        prv_files = glob.glob(pattern)

        for prv_file in prv_files:
            if prv_file.endswith("_test.py"):
                continue

            # Convert to module name
            relative_path = os.path.relpath(prv_file, src_dir)
            module_path = relative_path.replace(os.sep, ".").replace(".py", "")

            try:
                # Import the module
                module = importlib.import_module(module_path)

                # Find provider classes
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        inspect.isclass(attr)
                        and hasattr(attr, "__module__")
                        and attr.__module__ == module_path
                        and hasattr(
                            AbstractStaticProvider, "__name__"
                        )  # Check it exists first
                        and issubclass(attr, AbstractStaticProvider)
                        and attr != AbstractStaticProvider
                    ):
                        providers.append(attr)

                        # Track provider abilities
                        if hasattr(attr, "_abilities"):
                            provider_abilities = []

                            # Check decorated methods
                            for name, method in inspect.getmembers(
                                attr, predicate=inspect.isfunction
                            ):
                                if hasattr(method, "_ability_info"):
                                    ability_info = method._ability_info
                                    provider_abilities.append(
                                        {
                                            "name": ability_info["name"],
                                            "provider_class": attr,
                                            "extension_name": extension_name,
                                        }
                                    )

                            # Check _abilities set
                            for ability_name in attr._abilities:
                                if not any(
                                    a["name"] == ability_name
                                    for a in provider_abilities
                                ):
                                    provider_abilities.append(
                                        {
                                            "name": ability_name,
                                            "provider_class": attr,
                                            "extension_name": extension_name,
                                        }
                                    )

                            self.provider_abilities[attr] = provider_abilities
                            logger.debug(
                                f"Found provider {attr.__name__} with {len(provider_abilities)} abilities"
                            )

            except Exception as e:
                logger.error(f"Error importing provider module {module_path}: {e}")

        self.extension_providers[extension_name] = providers
        logger.debug(f"Total providers for {extension_name}: {len(providers)}")


class AbstractStaticExtensionSystemComponent(ABC):
    name: str = "abstract"
    description: str = "Abstract extension base class"
    # Environment variables that this extension needs
    _env: Dict[str, Any] = {}

    # Unified dependencies of this extension using the Dependencies class
    dependencies: Dependencies = Dependencies([])

    # Hooks registered by this extension
    _hooks: Dict[HookPath, List[Callable]] = {}

    @classproperty
    def root(cls) -> Any:
        return None

    @classproperty
    def hooks(cls) -> Set[str]:
        return cls._hooks.copy()

    # Abilities of this extension
    _abilities: Set[str] = set()

    @classproperty
    def abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def __init_subclass__(cls, **kwargs):
        """Automatically register abilities when extension class is defined."""
        super().__init_subclass__(**kwargs)

        # Skip registration for the abstract base classes themselves
        if cls.__name__ in [
            "AbstractStaticExtensionSystemComponent",
            "AbstractStaticExtension",
            "AbstractStaticProvider",
        ]:
            return

        # Initialize class-specific attributes if not already set
        if not hasattr(cls, "_abilities"):
            cls._abilities = set()

        # Discover and register abilities from this class with validation
        cls._discover_static_abilities_with_validation()

        # Register environment variables
        cls._register_env_vars()

    @classmethod
    def _discover_static_abilities_with_validation(cls) -> None:
        """Discover and register static ability methods."""
        for name, method in getmembers(cls, predicate=isfunction):
            if hasattr(method, "_ability_info"):
                ability_info = method._ability_info
                ability_name = ability_info["name"]

                # Determine if this is a meta ability based on class hierarchy
                is_extension = any(
                    base.__name__ == "AbstractStaticExtension" for base in cls.__mro__
                )
                is_provider = any(
                    "Provider" in base.__name__
                    and base.__name__ != "AbstractStaticExtension"
                    for base in cls.__mro__
                )

                # Meta abilities are only on extensions, not providers
                is_meta = is_extension and not is_provider

                # Store the computed meta status
                ability_info["meta"] = is_meta
                ability_info["abstract"] = "Abstract" in cls.__name__

                cls._abilities.add(ability_name)
                logger.debug(
                    f"Registered static ability {ability_name} -> {method.__name__} "
                    f"(meta={is_meta}, abstract={ability_info.get('abstract', False)})"
                )

    @classmethod
    def _register_env_vars(cls) -> None:
        """Register environment variables based on detected extension type."""
        # Get extension types - use extension.types for providers, cls.types for extensions
        if hasattr(cls, "extension") and cls.extension:
            ext_types = cls.extension.types
        elif hasattr(cls, "types"):
            ext_types = cls.types
        else:
            # Base classes like AbstractStaticProvider don't need env var registration
            return

        # Register environment variables based on type
        if ExtensionType.EXTERNAL in ext_types:
            # External extensions need API keys and external service configuration
            provider_prefix = cls.name.upper()
            cls._env[f"{provider_prefix}_API_KEY"] = ""
            cls._env[f"{provider_prefix}_SECRET_KEY"] = ""
            cls._env[f"{provider_prefix}_WEBHOOK_SECRET"] = ""
            cls._env[f"{provider_prefix}_CURRENCY"] = "USD"
            cls._env[f"{provider_prefix}_TIMEOUT"] = "30"
            cls._env[f"{provider_prefix}_RETRY_COUNT"] = "3"
        elif ExtensionType.DATABASE in ext_types:
            # Database extensions may need connection strings, migration settings
            provider_prefix = cls.name.upper()
            cls._env[f"{provider_prefix}_DB_CONNECTION"] = ""
            cls._env[f"{provider_prefix}_MIGRATION_ENABLED"] = "true"
        # Internal extensions typically don't need special env vars

        # Register the accumulated environment variables
        if cls._env:
            try:
                from lib.Environment import register_extension_env_vars

                register_extension_env_vars(cls._env)
                logger.debug(
                    f"Registered environment variables for {cls.name} (types: {ext_types})"
                )
            except ImportError as e:
                logger.warning(
                    f"Could not register environment variables for {cls.name}: {e}"
                )


class AbstractProviderInstance(ABC):

    def __init__(self, model: Optional[ProviderInstanceModel] = None):
        self.model = model

    model: Optional[ProviderInstanceModel]


class AbstractProviderInstance_SDK(AbstractProviderInstance):
    def __init__(self, sdk, model: Optional[ProviderInstanceModel] = None):
        super().__init__(model=model)
        if sdk is None:
            raise Exception("An SDK is required for this provider.")
        self._sdk = sdk

    @property
    def sdk(self):
        return self._sdk


class AbstractStaticProvider(AbstractStaticExtensionSystemComponent):
    """
    Base class for all service providers.
    All providers should be static/abstract - no instantiation required.

    This class should be inherited by extension-specific abstract providers
    (e.g., EXT_EMail.AbstractEmailProvider) which then define abstract abilities.
    """

    @classmethod
    @abstractmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance with the service SDK."""
        return AbstractProviderInstance(instance)

    @classproperty
    @abstractmethod
    def root(cls) -> AbstractProviderInstance:
        pass

    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Get provider abilities for rotation system."""
        return cls._abilities.copy()


class AbstractStaticExtensionMeta(ABCMeta):
    """Meta class for AbstractStaticExtension."""

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)

        # Register all @static_routes upon class definition
        from extensions.AbstractExtensionProvider import ExtensionRegistry

        if name == "EXT_Auth_MFA":
            logger.debug("hi")

        for attr_name in dir(cls):
            attr = getattr(cls, attr_name)
            if hasattr(attr, "_static_route_config"):
                paths = namespace["__module__"].split(".")
                if paths[0] != "extensions":
                    raise ValueError(
                        "'__module__' not from 'extensions'. It looks like this class is not an extension | __module__ = "
                        + namespace["__module__"]
                    )
                extension_name = paths[1]
                ExtensionRegistry.register_route(extension_name, attr)
        return cls


class AbstractStaticExtension(
    AbstractStaticExtensionSystemComponent, metaclass=AbstractStaticExtensionMeta
):
    """
    Abstract base class for all AGInfrastructure extensions.

    All extensions should be static/abstract - no instantiation required.
    Discovery and functionality should be accessible through class methods.

    Example usage for system tasks:
        # Send MFA email using the email extension's root rotation
        EXT_Email.root.rotate(EXT_Email.send_email)

        # The send_email method would be implemented as:
        @staticmethod
        def send_email(provider_instance):
            # provider_instance is a ProviderInstanceModel with api_key, model_name, etc.
            # Use provider_instance.api_key, provider_instance.model_name for configuration
            # Access settings via provider_instance.setting manager if needed
            pass
    """

    # Extension metadata (class attributes)
    version: str = "0.1.0"

    @classproperty
    def extension_type(cls) -> str:
        """Get the primary detected type of this extension for backward compatibility."""
        ext_types = cls.types
        if ExtensionType.EXTERNAL in ext_types:
            return "external"
        elif ExtensionType.DATABASE in ext_types:
            return "database"
        elif ExtensionType.ENDPOINTS in ext_types:
            return "endpoints"
        else:
            return "unknown"

    # Extension type checking properties removed - use the .types property instead

    AbstractProvider: Type[AbstractStaticProvider]

    def __init_subclass__(cls, **kwargs):
        """Automatically register abilities and hooks when extension class is defined."""
        super().__init_subclass__(**kwargs)

        # Skip registration for the abstract base class itself
        if cls.__name__ == "AbstractStaticExtension":
            return

        # Initialize class-specific attributes if not already set
        if not hasattr(cls, "_hooks"):
            cls._hooks = {}

        # Inherit abilities from parent classes
        cls._inherit_parent_abilities()

        # Inherit hooks from parent classes
        cls._inherit_parent_hooks()

        # Discover and register hooks from this class
        cls._discover_static_hooks()

    _providers: List[Type] = []

    @classproperty
    def providers(cls) -> List[Type]:
        """
        Auto-discover all providers in this extension's folder.
        Cached after first access.
        """
        if not cls._providers:
            import glob
            import importlib
            import inspect
            import os

            providers = []

            # Get extension directory
            src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            extension_dir = os.path.join(src_dir, "extensions", cls.name)
            extension_scope = f"extensions.{cls.name}"

            # Find all PRV files
            prv_files = glob.glob(os.path.join(extension_dir, "PRV_*.py"))

            for prv_file in prv_files:
                if prv_file.endswith("_test.py"):
                    continue
                module_name = os.path.basename(prv_file)[:-3]  # Remove .py
                try:
                    module = importlib.import_module(f"{extension_scope}.{module_name}")

                    # Find provider classes in the module
                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        # Check if it's a provider class (inherits from AbstractStaticProvider)
                        if obj.__module__ == module.__name__:
                            try:
                                from extensions.AbstractExtensionProvider import (
                                    AbstractStaticProvider,
                                )

                                if (
                                    issubclass(obj, AbstractStaticProvider)
                                    and obj != AbstractStaticProvider
                                ):
                                    providers.append(obj)
                            except:
                                # Also check for _is_provider attribute as fallback
                                if hasattr(obj, "_is_provider") and obj._is_provider:
                                    providers.append(obj)
                except Exception as e:
                    logger.error(f"Failed to import provider module {module_name}: {e}")

            cls._providers = providers

        return cls._providers

    _root_rotation_cache: Optional[RotationManager] = None

    @classproperty
    def root(cls) -> Optional[RotationManager]:
        """
        Get the Root RotationManager for this extension.
        Each extension gets its own root rotation based on its name.
        Uses proper caching with _root_rotation_cache attribute.
        """
        try:
            # Check cache first
            if cls._root_rotation_cache is not None:
                return cls._root_rotation_cache

            from database.DatabaseManager import DatabaseManager
            from logic.BLL_Providers import RotationManager

            # Try to get database manager from environment
            db_manager = None
            try:
                # First try to get from app state if available
                import sys

                app_module = sys.modules.get("app")
                if (
                    app_module
                    and hasattr(app_module, "app")
                    and hasattr(app_module.app, "state")
                ):
                    if hasattr(app_module.app.state, "DB"):
                        db_manager = (
                            app_module.app.state.model_registry.database_manager
                        )
            except:
                pass

            # If not available from app state, try singleton pattern (backward compatibility)
            if not db_manager and hasattr(DatabaseManager, "get_instance"):
                db_manager = DatabaseManager.get_instance()

            if not db_manager:
                logger.debug(
                    f"No database manager available for root rotation of extension {cls.name}"
                )
                return None

            # Check if database is properly initialized
            if (
                not hasattr(db_manager, "engine_config")
                or db_manager.engine_config is None
            ):
                logger.debug(
                    f"Database engine not initialized, cannot access root rotation for extension {cls.name}"
                )
                return None

            # Create rotation manager with database manager
            rotation_manager = RotationManager(
                requester_id=env("ROOT_ID"), db_manager=db_manager
            )

            # Get or create the root rotation for this extension
            root_rotation = rotation_manager.get(
                name=f"Root_{cls.name}", created_by_user_id=env("ROOT_ID")
            )

            if root_rotation:
                rotation_manager.target_id = root_rotation.id
                cls._root_rotation_cache = rotation_manager
                return rotation_manager
            else:
                logger.warning(f"Could not find root rotation for extension {cls.name}")
                return None

        except Exception as e:
            logger.debug(
                f"Cannot retrieve root rotation for extension {cls.name} (likely during discovery): {e}"
            )
            return None

    @classmethod
    def _discover_static_hooks(cls) -> None:
        """Discover and register static hook methods in the extension class."""
        for name, method in getmembers(cls, predicate=isfunction):
            if hasattr(method, "_hook_info"):
                for hook_path in method._hook_info:
                    if hook_path not in cls._hooks:
                        cls._hooks[hook_path] = []
                    cls._hooks[hook_path].append(method)
                    logger.debug(
                        f"Registered static hook {hook_path} -> {method.__name__}"
                    )

    @classmethod
    def _inherit_parent_abilities(cls) -> None:
        """Inherit abilities from parent classes."""
        for base in cls.__mro__[1:]:  # Skip self, start from first parent
            if hasattr(base, "_abilities") and isinstance(base._abilities, set):
                cls._abilities.update(base._abilities)
                if base._abilities:
                    logger.debug(
                        f"Inherited abilities from {base.__name__}: {base._abilities}"
                    )

    @classmethod
    def _inherit_parent_hooks(cls) -> None:
        """Inherit hooks from parent classes."""
        # Prevent infinite recursion by checking if we're already inheriting
        if hasattr(cls, "_inheriting_hooks"):
            return
        cls._inheriting_hooks = True

        try:
            for base in cls.__mro__[1:]:  # Skip self, start from first parent
                # Skip base classes that don't have hooks or are the abstract base classes
                if (
                    base.__name__
                    in [
                        "AbstractStaticExtension",
                        "AbstractStaticExtensionSystemComponent",
                        "ABC",
                    ]
                    or not hasattr(base, "_hooks")
                    or not isinstance(base._hooks, dict)
                ):
                    continue

                for hook_path, handlers in base._hooks.items():
                    if hook_path not in cls._hooks:
                        cls._hooks[hook_path] = []
                    # Only add handlers that aren't already present to avoid duplicates
                    for handler in handlers:
                        if handler not in cls._hooks[hook_path]:
                            cls._hooks[hook_path].append(handler)

                if base._hooks:
                    logger.debug(
                        f"Inherited hooks from {base.__name__}: {list(base._hooks.keys())}"
                    )
        finally:
            # Clean up the recursion guard
            if hasattr(cls, "_inheriting_hooks"):
                delattr(cls, "_inheriting_hooks")

    @staticmethod
    def hook(
        layer: str, domain: str, entity: str, function: str, time: str
    ) -> Callable:
        """Decorator to mark a static method as a hook handler."""

        def decorator(method: Callable) -> Callable:
            if not hasattr(method, "_hook_info"):
                method._hook_info = []
            hook_path = (layer, domain, entity, function, time)
            method._hook_info.append(hook_path)
            logger.debug(
                f"Decorated static method {method.__name__} as hook for {hook_path}"
            )
            return method

        return decorator

    # ability decorator has been moved to module level

    _types_cache: Optional[Set[ExtensionType]] = None

    @classproperty
    def types(cls) -> Set[ExtensionType]:
        """
        Get the types of this extension based on its components.
        Cached after first access.

        Returns:
            Set of ExtensionType enums
        """
        if cls._types_cache is not None:
            return cls._types_cache

        types = set()

        import glob
        import os

        # Get extension directory
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        extension_dir = os.path.join(src_dir, "extensions", cls.name)

        # Check for endpoints
        if glob.glob(os.path.join(extension_dir, "EP_*.py")):
            types.add(ExtensionType.ENDPOINTS)
        else:
            # Also check for RouterMixin in BLL files
            bll_files = glob.glob(os.path.join(extension_dir, "BLL_*.py"))
            for bll_file in bll_files:
                try:
                    with open(bll_file, "r") as f:
                        if "RouterMixin" in f.read():
                            types.add(ExtensionType.ENDPOINTS)
                            break
                except:
                    pass

        # Check for database models (in BLL files with DatabaseMixin)
        bll_files = glob.glob(os.path.join(extension_dir, "BLL_*.py"))
        for bll_file in bll_files:
            try:
                with open(bll_file, "r") as f:
                    content = f.read()
                    # Check for DatabaseMixin usage
                    if "DatabaseMixin" in content and (
                        "__tablename__" in content or "table_comment" in content
                    ):
                        types.add(ExtensionType.DATABASE)
                        break
            except:
                pass

        # Check for external components
        if glob.glob(os.path.join(extension_dir, "PRV_*.py")):
            types.add(ExtensionType.EXTERNAL)
        else:
            # Also check for AbstractExternalModel in BLL files
            bll_files = glob.glob(os.path.join(extension_dir, "BLL_*.py"))
            for bll_file in bll_files:
                try:
                    with open(bll_file, "r") as f:
                        content = f.read()
                        if (
                            "AbstractExternalModel" in content
                            or "AbstractExternalManager" in content
                        ):
                            types.add(ExtensionType.EXTERNAL)
                            break
                except:
                    pass

        cls._types_cache = types
        return types

    _models_cache: Optional[Set[Type]] = None

    @classproperty
    def models(cls) -> Set[Type]:
        """
        Get all core models (BLL with DatabaseMixin and external) for this extension.
        Cached after first access.

        Returns:
            Set of model classes from BLL files (with DatabaseMixin) and external models from PRV files
        """
        if cls._models_cache is not None:
            return cls._models_cache

        models = set()

        import glob
        import importlib
        import inspect
        import os

        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        extension_dir = os.path.join(src_dir, "extensions", cls.name)
        extension_scope = f"extensions.{cls.name}"

        for bll_file in glob.glob(os.path.join(extension_dir, "BLL_*.py")):
            module_name = os.path.basename(bll_file)[:-3]  # Remove .py
            try:
                module = importlib.import_module(f"{extension_scope}.{module_name}")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    # Check if it's a BLL model with DatabaseMixin (has .DB property)
                    if (
                        hasattr(obj, "DB")
                        and obj.__module__ == module.__name__
                        and hasattr(obj.DB, "__tablename__")
                    ):
                        models.add(obj)
            except Exception as e:
                logger.debug(f"Failed to import {module_name}: {e}")

        for prv_file in glob.glob(os.path.join(extension_dir, "PRV_*.py")):
            module_name = os.path.basename(prv_file)[:-3]  # Remove .py
            try:
                module = importlib.import_module(f"{extension_scope}.{module_name}")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    # Check if it's an external model
                    if hasattr(obj, "_is_extension_model") and obj._is_extension_model:
                        models.add(obj)
                    # Also check if it inherits from AbstractExternalModel
                    elif obj.__module__ == module.__name__:
                        try:
                            from lib.Pydantic import AbstractExternalModel

                            if issubclass(obj, AbstractExternalModel):
                                models.add(obj)
                        except:
                            pass
            except Exception as e:
                logger.debug(f"Failed to import {module_name}: {e}")

        cls._models_cache = models
        return models

    @classmethod
    def get_rotation_provider_instances_seed_data(cls) -> List[Dict[str, Any]]:
        """Get rotation provider instance seed data for this extension class."""
        try:
            from logic.BLL_Extensions import ExtensionModel
            from logic.BLL_Providers import (
                ProviderExtensionModel,
                ProviderInstanceModel,
                RotationModel,
            )

            # Get SQLAlchemy models
            Extension = ExtensionModel.DB
            ProviderExtension = ProviderExtensionModel.DB
            ProviderInstance = ProviderInstanceModel.DB
            Rotation = RotationModel.DB

            seed_data = []

            # Get database manager
            db_manager = None
            try:
                from database.DatabaseManager import DatabaseManager

                if hasattr(DatabaseManager, "get_instance"):
                    db_manager = DatabaseManager.get_instance()
            except:
                pass

            if not db_manager:
                logger.warning("No database manager available for seed data generation")
                return []

            session = get_session(db_manager)
            if not session:
                logger.warning("No database session available")
                return []

            try:
                # Find extension record
                stmt = select(Extension).where(Extension.name == cls.name)
                extension_record = session.execute(stmt).scalar_one_or_none()

                if not extension_record:
                    logger.warning(f"Extension record not found for {cls.name}")
                    return []

                # Find root rotation for this extension
                root_id = env("ROOT_ID")
                stmt = (
                    select(Rotation)
                    .where(
                        Rotation.extension_id == str(extension_record.id),
                        Rotation.created_by_user_id == root_id,
                    )
                    .limit(1)
                )
                root_rotation = session.execute(stmt).scalar_one_or_none()

                if not root_rotation:
                    extension_name_plural = inflection.plural(cls.name)
                    rotation_name = (
                        f"Root_{stringcase.capitalcase(extension_name_plural)}"
                    )

                    stmt = (
                        select(Rotation)
                        .where(
                            Rotation.name == rotation_name,
                            Rotation.created_by_user_id == root_id,
                        )
                        .limit(1)
                    )
                    root_rotation = session.execute(stmt).scalar_one_or_none()

                if not root_rotation:
                    logger.debug(f"No root rotation found for extension {cls.name}")
                    return []

                # Find associated providers
                stmt = select(ProviderExtension).where(
                    ProviderExtension.extension_id == extension_record.id
                )
                provider_extensions = session.execute(stmt).scalars().all()

                for provider_extension in provider_extensions:
                    stmt = select(ProviderInstance).where(
                        ProviderInstance.provider_id == provider_extension.provider_id
                    )
                    provider_instances = session.execute(stmt).scalars().all()

                    for instance in provider_instances:
                        seed_data.append(
                            {
                                "rotation_id": str(root_rotation.id),
                                "provider_instance_id": str(instance.id),
                                "parent_id": None,
                            }
                        )

            finally:
                if session:
                    session.close()

            return seed_data

        except Exception as e:
            logger.error(
                f"Error generating rotation provider instance seed data for extension {cls.name}: {e}"
            )
            return []

    @classmethod
    def register_hook(
        cls,
        layer: str,
        domain: str,
        entity: str,
        function: str,
        time: str,
        handler: Callable,
    ) -> None:
        """Register a hook handler for a specific path."""
        hook_path = (layer, domain, entity, function, time)

        if hook_path not in cls._hooks:
            cls._hooks[hook_path] = []

        cls._hooks[hook_path].append(handler)
        logger.debug(f"Registered hook {hook_path} -> {handler.__name__}")

    class AbstractProvider(AbstractStaticProvider):
        """
        Inner abstract provider class for backward compatibility.
        Extensions can define this as an inner class to maintain the old pattern.
        """

        pass


# Extension type detection functions
def detect_extension_type(extension_class: Type["AbstractStaticExtension"]) -> str:
    """
    DEPRECATED: Use extension_class.types property instead.

    Automatically detect the type of extension based on its components.

    This function is kept for backward compatibility but uses the new types property.
    New code should use `extension_class.types` which returns a Set[ExtensionType].

    Returns:
        - 'external' if extension has external models or providers
        - 'database' if extension has database models
        - 'endpoints' if extension has routers/endpoints (previously 'internal')
        - 'unknown' if type cannot be determined
    """
    import warnings

    warnings.warn(
        "detect_extension_type() is deprecated. Use extension_class.types property instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    types = extension_class.types

    # Return the first matching type for backward compatibility
    # Priority: external > database > endpoints
    if ExtensionType.EXTERNAL in types:
        return "external"
    elif ExtensionType.DATABASE in types:
        return "database"
    elif ExtensionType.ENDPOINTS in types:
        return "endpoints"
    else:
        return "unknown"


# Helper functions removed - functionality moved to AbstractStaticExtension.types property
