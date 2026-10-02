from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import monotonic
from types import ModuleType
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple, Type

from fastapi import HTTPException, status
from ordered_set import OrderedSet
from pydantic import BaseModel

try:
    import pytest
except ImportError:
    pytest = None  # type: ignore[assignment]

from zephyrex.lib.ClassMembers import decorated_functions
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Environment import AbstractRegistry, env
from zephyrex.lib.Logging import logger
from zephyrex.lib.Paths import (
    extensions_dir as _resolve_extensions_dir,
    src_dir as _resolve_src_dir,
)
from zephyrex.pydantic2.registry import classproperty
from zephyrex.logic.BLL_Providers import ProviderInstanceModel, RotationManager

# Imports needed for patching in tests and used in methods
try:
    from sqlalchemy import select

    from zephyrex.database.DatabaseManager import DatabaseManager
except ImportError:
    # Handle case where these modules might not be available during testing
    # Removed inflect_engine - using shared inflection from Environment
    select = None  # type: ignore[assignment]


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
            from zephyrex.database.DatabaseManager import DatabaseManager as DBM

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
        method._ability_info = {  # type: ignore[attr-defined]
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

    def __init__(
        self,
        extensions_csv: str,
        extensions_path: Optional[str] = None,
    ):
        import glob
        import inspect
        import os

        from zephyrex.lib.Logging import logger

        self.extensions: OrderedSet[Type[AbstractStaticExtension]] = (
            OrderedSet()
        )  # Extension classes in dependency order
        self._extension_name_map = (  # type: ignore[var-annotated]
            {}
        )  # Maps extension name to extension class for quick lookup
        self.loaded_extensions: dict[str, str] = {}
        self.extension_models: dict[str, list[Any]] = {}
        self.extension_abilities: dict[str, list[Any]] = {}
        self.extension_providers: dict[str, list[Any]] = {}
        self.provider_abilities: dict[Any, list[Any]] = {}

        # Per-instance override for the extensions root. ``None`` means "use
        # the bundled <src_dir>/extensions". Resolved through Paths so a
        # caller-supplied path (e.g. ``zephyrex.run(extensions_path=…)``)
        # is honored without disturbing existing call sites.
        self.extensions_path: Optional[str] = (
            _resolve_extensions_dir(extensions_path)
            if extensions_path is not None
            else None
        )

        # Load extensions from CSV
        if not extensions_csv:
            logger.debug("No extensions configured for registry")
            return

        from zephyrex.app import parse_extension_csv

        extension_names = parse_extension_csv(extensions_csv)

        if not extension_names:
            logger.debug("No valid extension names found")
            return

        # Register each requested extension - dependencies will be handled automatically
        for extension_name in extension_names:
            try:
                # Find the extension module — checks consumer path first,
                # falls back to bundled extensions.
                scope_dir = self._extension_dir(extension_name)
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
                    module_name = f"zephyrex.extensions.{extension_name}.{os.path.basename(file_path)[:-3]}"

                    try:
                        # Item 61: dual-name registration via the
                        # canonical loader. This ensures intra-extension
                        # imports keep resolving when the extensions tree
                        # lives at a non-default path.
                        from zephyrex.extensions.ExtensionLoader import (
                            load_extension_module,
                        )

                        file_stem = os.path.basename(file_path)[:-3]
                        module = load_extension_module(
                            self._extensions_root(), extension_name, file_stem
                        )

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

        loaded_names = {ext.name for ext in self.extensions if hasattr(ext, "name")}
        requested_set = set(extension_names)
        missing = requested_set - loaded_names
        if missing:
            logger.error(
                "EXTENSION LOAD FAILURE: requested extensions not loaded: %s. "
                "Check spelling in APP_EXTENSIONS and ensure each extension "
                "directory exists with an EXT_*.py file containing a class "
                "with a matching 'name' attribute.",
                ", ".join(sorted(missing)),
            )

    def _extensions_root(self) -> str:
        """Resolve the extensions root for this registry instance.

        Honors a per-instance ``extensions_path`` override; otherwise falls
        back to the framework default (``<src_dir>/extensions`` or whatever
        ``Paths.set_extensions_root`` has configured globally).
        """
        return _resolve_extensions_dir(self.extensions_path)

    def _extension_dir(self, extension_name: str) -> str:
        """Path to a specific extension's directory.

        Checks the consumer's extensions_path first; falls back to the
        bundled ``<src_dir>/extensions`` if the extension isn't found there.
        This lets consumers mix their own extensions with framework-bundled ones.
        """
        import os

        primary = os.path.join(self._extensions_root(), extension_name)
        if os.path.isdir(primary):
            return primary
        bundled = os.path.join(_resolve_src_dir(), "extensions", extension_name)
        if os.path.isdir(bundled):
            return bundled
        return primary

    @property
    def csv(self) -> str:
        """Get CSV string of extension names in dependency order."""
        return ",".join(ext_class.name for ext_class in self.extensions)

    @property
    def extension_names(self) -> frozenset[str]:
        """Names of every extension loaded into this registry."""
        return frozenset(ext_class.name for ext_class in self.extensions)

    def initialize_extensions(self) -> None:
        """Call ``on_initialize`` on every loaded extension, in dependency
        order. An extension that returns False cannot run, so the build
        stops with an error naming it."""
        from zephyrex import ExtensionLoadError

        for extension_class in self.extensions:
            if not extension_class.on_initialize():
                raise ExtensionLoadError(
                    f"Extension '{extension_class.name}' failed to initialize "
                    f"(on_initialize returned False); see the log for its reason"
                )

    def start_extensions(self) -> None:
        """Call ``on_start`` on every loaded extension, in dependency order."""
        for extension_class in self.extensions:
            extension_class.on_start()

    def stop_extensions(self) -> None:
        """Call ``on_stop`` on every loaded extension, in reverse dependency
        order. Every extension gets its turn even when an earlier one raises;
        each failure is logged."""
        for extension_class in reversed(list(self.extensions)):
            try:
                extension_class.on_stop()
            except Exception:
                logger.exception(
                    "Extension '%s' raised in on_stop", extension_class.name
                )

    # Extension names must be safe Python module identifiers — letters,
    # digits, underscores. Anything else (path separators, dots, shell
    # metacharacters, control characters) is rejected outright before the
    # name reaches importlib or the filesystem.
    _SAFE_EXTENSION_NAME = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def register_extension(self, extension_class: Type["AbstractStaticExtension"]):
        """Register a static extension class and automatically handle recursive dependencies."""

        from zephyrex.lib.Logging import logger

        extension_name = extension_class.name

        # Reject names that could resolve outside the extensions/ tree or
        # otherwise break the loader. Path-traversal markers, shell
        # metacharacters, and null bytes are not legal Python identifiers
        # and have no business being an extension name.
        if not isinstance(extension_name, str) or not self._SAFE_EXTENSION_NAME.match(
            extension_name
        ):
            raise ValueError(
                f"Refusing to register extension with unsafe name "
                f"{extension_name!r}: must match [A-Za-z_][A-Za-z0-9_]*"
            )

        # An extension already registered under this name must NOT be
        # silently replaced by a second class — that is a privilege-
        # confusion vector. The caller has to remove the existing entry
        # explicitly first.
        existing = self._extension_name_map.get(extension_name)
        if existing is not None and existing is not extension_class:
            raise ValueError(
                f"Extension name {extension_name!r} is already registered "
                f"by {existing!r}; refusing to override with "
                f"{extension_class!r}"
            )
        if existing is extension_class:
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
        import inspect
        import os

        from zephyrex.lib.Dependencies import EXT_Dependency
        from zephyrex.lib.Logging import logger

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
                dep_module_pattern = os.path.join(
                    self._extension_dir(dep_name), "EXT_*.py"
                )
                dep_files = glob.glob(dep_module_pattern)

                dep_class = None
                for dep_file in dep_files:
                    if dep_file.endswith("_test.py"):
                        continue

                    file_stem = os.path.basename(dep_file)[:-3]

                    # Item 61: dual-name registration so intra-extension
                    # imports keep working when the extensions tree lives
                    # outside the package.
                    from zephyrex.extensions.ExtensionLoader import (
                        load_extension_module,
                    )

                    module = load_extension_module(
                        self._extensions_root(), dep_name, file_stem
                    )

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
        import os

        from zephyrex.extensions.ExtensionLoader import load_extension_module
        from zephyrex.lib.Logging import logger

        for extension_name in extension_names:
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

                # Find both BLL and PRV files in the extension directory.
                # Use ``_extension_dir`` so an external extensions root
                # (set via ``extensions_path``) is honored.
                ext_dir = self._extension_dir(extension_name)
                file_patterns = [
                    ("BLL", os.path.join(ext_dir, "BLL_*.py")),
                    ("PRV", os.path.join(ext_dir, "PRV_*.py")),
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

                        logger.debug(f"Processing {file_type} file: {file_path}")

                        try:
                            # Item 61: Use load_extension_module so out-of-tree
                            # extensions (extensions_path != bundled) load
                            # correctly via spec_from_file_location.
                            file_stem = os.path.basename(file_path)[:-3]
                            module = load_extension_module(
                                self._extensions_root(), extension_name, file_stem
                            )
                            logger.debug(
                                f"Successfully imported module: {module.__name__}"
                            )

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
                            logger.debug(f"Could not import {file_path}: {import_err}")
                        except Exception as module_err:
                            logger.debug(
                                f"Error processing module {file_path}: {module_err}"
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
        from zephyrex.lib.Dependencies import Dependencies

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
        from zephyrex.lib.Dependencies import Dependencies

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
        from zephyrex.lib.Dependencies import EXT_Dependency

        # Build dependency graph
        # Only required extension dependencies constrain the order.
        dependency_graph: Dict[str, List[str]] = {
            ext_name: [
                declared.name
                for declared in ext_class.dependencies
                if isinstance(declared, EXT_Dependency) and not declared.optional
            ]
            for ext_name, ext_class in available_extensions.items()
        }

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

        from zephyrex.lib.Dependencies import Dependencies
        from zephyrex.lib.Logging import logger

        results = {}

        for extension_name in extension_names:
            try:
                # Try to import the extension module to get its dependencies
                extension_module_name = (
                    f"zephyrex.extensions.{extension_name}.EXT_{extension_name}"
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

        from zephyrex.lib.Logging import logger

        extension_name = extension_class.name
        abilities = []

        # Meta abilities: methods decorated with @ability on the extension class
        for _, _, ability_info in decorated_functions(extension_class, "_ability_info"):
            abilities.append(
                {
                    "name": ability_info["name"],
                    "meta": True,
                    "extension_name": extension_name,
                    "type": "meta",
                }
            )
            logger.debug(
                f"Found meta ability {ability_info['name']} for extension {extension_name}"
            )

        # Abstract abilities: inner Abstract*Provider classes
        for attr_name, attr_value in extension_class.__dict__.items():
            if (
                inspect.isclass(attr_value)
                and "Abstract" in attr_name
                and "Provider" in attr_name
            ):
                for _, _, ability_info in decorated_functions(
                    attr_value, "_ability_info"
                ):
                    abilities.append(
                        {
                            "name": ability_info["name"],
                            "meta": False,
                            "extension_name": extension_name,
                            "type": "abstract",
                            "provider_class": attr_name,
                        }
                    )
                    logger.debug(
                        f"Found abstract ability {ability_info['name']} in "
                        f"{attr_name} for extension {extension_name}"
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
        import inspect
        import os

        from zephyrex.extensions.ExtensionLoader import load_extension_module
        from zephyrex.lib.Logging import logger

        extension_name = extension_class.name
        providers = []

        extension_dir = self._extension_dir(extension_name)

        # Find PRV_*.py files
        pattern = os.path.join(extension_dir, "PRV_*.py")
        prv_files = glob.glob(pattern)

        for prv_file in prv_files:
            if prv_file.endswith("_test.py"):
                continue

            try:
                # Item 61: load via spec_from_file_location helper so
                # out-of-tree extensions resolve correctly.
                file_stem = os.path.basename(prv_file)[:-3]
                module = load_extension_module(
                    self._extensions_root(), extension_name, file_stem
                )

                # Providers defined in this module (not ones it imports). The
                # comparison must use the loaded module's own name: the
                # package lives under ``zephyrex.*``, so a name derived from a
                # path relative to the source dir never matches.
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        inspect.isclass(attr)
                        and attr.__module__ == module.__name__
                        and issubclass(attr, AbstractStaticProvider)
                        and attr is not AbstractStaticProvider
                    ):
                        providers.append(attr)

                        # Track provider abilities
                        if hasattr(attr, "_abilities"):
                            provider_abilities = []

                            # Check decorated methods
                            for _, _, ability_info in decorated_functions(
                                attr, "_ability_info"
                            ):
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
                logger.error(f"Error importing provider module {prv_file}: {e}")

        self.extension_providers[extension_name] = providers
        logger.debug(f"Total providers for {extension_name}: {len(providers)}")


class AbstractStaticExtensionSystemComponent(ABC):
    name: ClassVar[str] = "abstract"
    description: ClassVar[str] = "Abstract extension base class"
    # Environment variables that this extension needs
    _env: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def get_env_value(cls, key: str, default: Any = None) -> Any:
        """Look up an environment value (with fallback) for this component.

        Consolidated onto the shared base so both extensions and providers
        inherit it (several providers previously carried their own copy).
        """
        from zephyrex.lib.Environment import env

        return env(key, default)

    # Item 37 — typed settings and env schema. Optional; when present the
    # framework prefers `Settings`/`EnvSchema` over the legacy `_env` dict.
    Settings: ClassVar[Optional[Type[BaseModel]]] = None
    EnvSchema: ClassVar[Optional[Type[BaseModel]]] = None

    # Unified dependencies of this extension using the Dependencies class
    dependencies: ClassVar[Dependencies] = Dependencies([])

    # Hooks registered by this extension
    _hooks: ClassVar[Dict[HookPath, List[Callable]]] = {}

    @classproperty
    def root(cls) -> Any:
        return None

    @classproperty
    def hooks(cls) -> Set[str]:
        return cls._hooks.copy()  # type: ignore[return-value]

    # Abilities of this extension
    _abilities: ClassVar[Set[str]] = set()

    @classproperty
    def abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Return the abilities this extension/provider offers."""
        return cls._abilities.copy()

    @classmethod
    def has_ability(cls, ability: str) -> bool:
        """Check whether this extension/provider has a specific ability."""
        return ability in cls.get_abilities()

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

        # Give every class its own abilities set. ``_abilities`` is always
        # inherited, so without this a subclass that declares none shares its
        # ancestor's set and ability registration below writes into it,
        # leaking one extension's abilities into every sibling. Copying keeps
        # the inherited abilities visible.
        if "_abilities" not in cls.__dict__:
            cls._abilities = set(cls._abilities)

        # Item 37: validate that Settings/EnvSchema, when declared, are BaseModel.
        for attr_name in ("Settings", "EnvSchema"):
            schema = cls.__dict__.get(attr_name)
            if schema is not None and not (
                isinstance(schema, type) and issubclass(schema, BaseModel)
            ):
                raise TypeError(
                    f"{cls.__name__}.{attr_name} must inherit from pydantic.BaseModel"
                )

        # Discover and register abilities from this class with validation
        cls._discover_static_abilities_with_validation()

        # Register environment variables
        cls._register_env_vars()

    @classmethod
    def get_setting(cls, name: str, default: Any = None) -> Any:
        """Item 37 — typed setting accessor.

        Consults `Settings` (Pydantic) when available so callers get
        typed values; falls back to the legacy `_env` dict otherwise.
        """
        if cls.Settings is not None:
            try:
                instance = cls.Settings()
                if hasattr(instance, name):
                    return getattr(instance, name)
            except Exception as exc:
                logger.debug(
                    "Settings construction failed for %s: %s", cls.__name__, exc
                )
        return cls._env.get(name, default) if isinstance(cls._env, dict) else default

    @classmethod
    def get_env_schema(cls) -> Optional[Type[BaseModel]]:
        """Return the declared `EnvSchema` Pydantic model, or None."""
        return cls.EnvSchema

    @classmethod
    def _discover_static_abilities_with_validation(cls) -> None:
        """Discover and register static ability methods."""
        # Meta abilities are only on extensions, not providers.
        is_extension = any(
            base.__name__ == "AbstractStaticExtension" for base in cls.__mro__
        )
        is_provider = any(
            "Provider" in base.__name__ and base.__name__ != "AbstractStaticExtension"
            for base in cls.__mro__
        )
        is_meta = is_extension and not is_provider
        is_abstract = "Abstract" in cls.__name__

        for _, method, ability_info in decorated_functions(cls, "_ability_info"):
            ability_info["meta"] = is_meta
            ability_info["abstract"] = is_abstract
            cls._abilities.add(ability_info["name"])
            logger.debug(
                f"Registered static ability {ability_info['name']} -> "
                f"{method.__name__} (meta={is_meta}, abstract={is_abstract})"
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
                from zephyrex.lib.Environment import register_extension_env_vars

                register_extension_env_vars(cls._env)
                logger.debug(
                    f"Registered environment variables for {cls.name} (types: {ext_types})"
                )
            except ImportError as e:
                logger.warning(
                    f"Could not register environment variables for {cls.name}: {e}"
                )


@dataclass(frozen=True)
class InstanceSetting:
    """A setting a provider reads from each of its instances: a
    ``ProviderInstanceSetting`` row named ``key`` (or the instance's own
    ``field`` column, such as ``api_key``), else the ``env`` variable, else
    ``default``. A ``secret`` one is stored encrypted and never returned
    once written. A ``multiline`` value spans lines (a PEM key, a JSON
    credentials file), so a form offers a text area for it."""

    key: str
    description: str
    env: Optional[str] = None
    default: Optional[str] = None
    secret: bool = False
    field: Optional[str] = None
    multiline: bool = False


class AbstractProviderInstance(ABC):
    """Item 26 — typed contract for bonded provider instances.

    Concrete subclasses MUST accept a `ProviderInstanceModel` in their
    constructor. `validate_credentials` and `close` carry default impls
    so existing subclasses keep working; override them when the provider
    holds resources to release or wants a self-test before first use.
    """

    model: Optional[ProviderInstanceModel]

    def __init__(self, instance: Optional[ProviderInstanceModel] = None) -> None:
        self.model = instance

    def validate_credentials(self) -> bool:
        """Self-test the bonded credentials. Default: trust them.

        Override to issue a no-op upstream call (e.g., GET /v1/me) and
        return False on auth-failure responses. Should NOT raise.
        """
        return True

    def close(self) -> None:
        """Release any held resources (open SDK clients, sockets).

        Default no-op. Subclasses with persistent connections (gRPC
        channels, long-lived HTTP/2 clients) override this.
        """
        return None


class AbstractProviderInstance_SDK(AbstractProviderInstance):
    def __init__(
        self, sdk: Any, instance: Optional[ProviderInstanceModel] = None
    ) -> None:
        super().__init__(instance=instance)
        if sdk is None:
            raise Exception("An SDK is required for this provider.")
        self._sdk = sdk

    @property
    def sdk(self):
        return self._sdk


# ----- Item 27: liveness reporting ------------------------------------------


class HealthStatus(Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class HealthReport:
    """Per-instance liveness report.

    Item 27: distinct from `is_configured`. `is_configured` reports
    "all required env vars present"; `health_check` reports a real
    upstream-validated liveness check. A provider with stale credentials
    is `is_configured == True` and `health_check == DOWN`.
    """

    __slots__ = ("status", "timestamp", "detail")

    def __init__(
        self,
        status: HealthStatus,
        timestamp: Optional[datetime] = None,
        detail: str = "",
    ) -> None:
        self.status = status
        self.timestamp = timestamp or datetime.now(timezone.utc)
        self.detail = detail

    def __repr__(self) -> str:
        return (
            f"HealthReport(status={self.status.value}, "
            f"timestamp={self.timestamp.isoformat()}, detail={self.detail!r})"
        )


class AbstractStaticProvider(AbstractStaticExtensionSystemComponent):
    """
    Base class for all service providers.
    All providers should be static/abstract - no instantiation required.

    This class should be inherited by extension-specific abstract providers
    (e.g., EXT_EMail.AbstractEmailProvider) which then define abstract abilities.
    """

    # Item 26 — typed bonded-instance attribute.
    _instance: ClassVar[Optional[AbstractProviderInstance]] = None

    # Item 50 — paired-name discriminator source. Providers may override
    # (e.g., `STRIPE_ENV`) when they need per-provider environment selection.
    environment_source: ClassVar[str] = "APP_ENV"

    # Item 10 — default auth strategy name. Providers may override
    # (e.g. "oauth2", "jwt_bearer", "aws_sigv4"). Per-provider-instance
    # overrides are read from `ProviderInstanceModel.auth_strategy_name`.
    auth_strategy_name: ClassVar[str] = "api_key"

    # Item 17 — optional per-provider rate-limit / concurrency caps.
    # Concrete providers set these as needed. Imported lazily to avoid a
    # hard module-cycle when this file loads before extensions.RateLimit.
    rate_limit: ClassVar[Optional[Any]] = None
    concurrency_limit: ClassVar[Optional[Any]] = None

    # Item 2 — per-provider rotation policy. Concrete providers set this
    # to a `RotationPolicy` (from `extensions.ExternalErrors`); the
    # rotation system reads it via `RotationManager._resolve_rotation_policy`.
    rotation_policy: ClassVar[Optional[Any]] = None

    # Item 48 — per-ability graceful-degradation policy. Concrete providers
    # may attach a `DegradationPolicy` to an ability via the ability
    # decorator, or fall back to this provider-class-level default. The
    # rotation system consults the policy when its chain is exhausted:
    # `FAIL_FAST` (default) raises HTTP 500; `QUEUE_AND_RETRY` enrolls
    # the operation in the outbox (Item 35) and returns 202; `SILENT_DROP`
    # logs and returns success while emitting `provider_silent_drop_total`
    # metric. None means "inherit the framework default" which is
    # `FAIL_FAST`. Switching modes is a breaking change to the API
    # contract — version per Item 39.
    degradation_policy: ClassVar[Optional[Any]] = None

    # Item 84 — per-provider cost-observability model. A `CostModel`
    # callable (`(request, response) -> Decimal`) returns the cost of a
    # single upstream call in the deployment's base currency. The
    # framework reads this on the outbound-call path and emits the
    # `provider_cost_usd_total{tenant, provider, ability}` counter +
    # writes per-request cost into the audit log (Item 56 retention).
    # `None` means the provider does not contribute to cost metrics
    # (rather than emitting zero-cost noise — providers that are
    # genuinely free declare `FreeCostModel()` explicitly).
    cost_model: ClassVar[Optional[Any]] = None

    # Item 33 — upstream API version pinning. Concrete providers may pin
    # the upstream wire version (e.g. Stripe "2024-06-20"). When
    # `external_api_version_header` is also set, `ProviderHTTPClient`
    # injects the version as that header on every outbound call. When
    # only `external_api_version` is set the value is informational
    # (typically consumed by the upstream SDK rather than as a wire
    # header) and the framework warns once at startup.
    external_api_version: ClassVar[Optional[str]] = None
    external_api_version_header: ClassVar[Optional[str]] = None

    # Item 27 — cached health report per provider class instance.
    _cached_health: ClassVar[Optional[HealthReport]] = None
    _cached_health_at: ClassVar[float] = 0.0

    @classmethod
    @abstractmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance]:
        """Bond a provider instance with the service SDK; ``None`` when it
        cannot be bonded (SDK not installed, credentials missing)."""

    @classproperty
    @abstractmethod
    def root(cls) -> AbstractProviderInstance:
        pass

    # Seconds before an outbound HTTP call through ``http()`` gives up.
    http_timeout_seconds: ClassVar[float] = 30.0

    # The settings this provider reads from its instances: the catalogue a
    # client renders, and the only keys ``setting()`` will read.
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = ()

    @classmethod
    def instance_setting(cls, key: str) -> InstanceSetting:
        for declared in cls.instance_settings:
            if declared.key == key:
                return declared
        raise KeyError(f"{cls.name} declares no instance setting {key!r}")

    @classmethod
    def setting(
        cls, instance: Optional[ProviderInstanceModel], key: str
    ) -> Optional[str]:
        """The value of the declared setting ``key`` for ``instance``: its
        column or setting row, else its environment variable, else its
        default. ``instance`` is None for an environment-only lookup."""
        declared = cls.instance_setting(key)
        return cls.resolve_setting(
            instance,
            declared.key,
            declared.env,
            field=declared.field,
            default=declared.default,
        )

    @classmethod
    def http(cls) -> Any:
        """This provider's ``ProviderHTTPClient``: SSRF-guarded, typed
        errors on non-2xx (which drive the rotation), and a User-Agent
        naming this software."""
        from zephyrex.lib.ProviderHTTPClient import ClientPolicy, ProviderHTTPClient

        return ProviderHTTPClient(
            policy=ClientPolicy(timeout=cls.http_timeout_seconds),
            provider_name=cls.name,
            provider=cls,
        )

    @classmethod
    async def get_json(
        cls,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """GET ``url`` through ``http()``: the decoded JSON answer."""
        return await cls.http().get(url, params=params, headers=headers)

    @classmethod
    def resolve_setting(
        cls,
        instance: Optional[ProviderInstanceModel],
        key: str,
        env_var: Optional[str] = None,
        *,
        field: Optional[str] = None,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """The first non-empty of: the instance's ``field`` column, the
        instance's ``key`` setting, the ``env_var`` environment value, and
        ``default``. ``instance`` is None for an environment-only lookup
        (configuration checks have no instance)."""
        if instance is not None:
            if field is not None:
                value = getattr(instance, field)
                if value:
                    return str(value)
            setting = instance.get_setting(key)
            if setting:
                return setting
        if env_var is not None:
            env_value = cls.get_env_value(env_var)
            if env_value:
                return str(env_value)
        return default

    @classmethod
    def is_configured(cls) -> bool:
        """All required environment variables present and non-empty.

        Used for startup / admin readiness reporting. NOT a liveness check —
        see `health_check` for upstream-validated liveness.
        """
        env_dict = cls._env if isinstance(cls._env, dict) else {}
        for var_name in env_dict.keys():
            value = env(var_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                return False
        return True

    @classmethod
    def health_check(cls) -> HealthReport:
        """Live liveness check. Default: report OK based on `is_configured`.

        Per Item 27 the cache is per-provider-instance, not per-class —
        but the framework's existing structure carries provider state
        on the class. Override at the concrete-provider level to issue
        a real upstream call and downgrade to DEGRADED / DOWN.
        """
        if cls.is_configured():
            return HealthReport(HealthStatus.OK, detail="defaults: configured")
        return HealthReport(HealthStatus.DOWN, detail="missing required env vars")

    @classmethod
    def build_auth_strategy(
        cls,
        instance: ProviderInstanceModel,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Item 10 — build the auth strategy for a bonded instance.

        Looks up the strategy name from the per-instance override (if
        present on `ProviderInstanceModel.auth_strategy_name`) falling
        back to the class-level default `cls.auth_strategy_name`. The
        registry resolves the factory; payload is the credential blob
        the strategy interprets (typically containing `api_key`,
        `access_token`, `username`/`password`, etc.).
        """
        from zephyrex.extensions.AuthStrategy import AuthStrategyRegistry

        name = getattr(instance, "auth_strategy_name", None) or cls.auth_strategy_name
        return AuthStrategyRegistry.get(name, payload or {})

    @classmethod
    def cached_health_check(cls, ttl_seconds: int = 60) -> HealthReport:
        """TTL-cached `health_check`. Per Item 27 the cache lives per
        provider-instance — for now, per-class given the framework's
        static-provider model. Reset by calling `cls._cached_health = None`."""
        now = monotonic()
        if (
            cls._cached_health is not None
            and (now - cls._cached_health_at) < ttl_seconds
        ):
            return cls._cached_health
        report = cls.health_check()
        cls._cached_health = report
        cls._cached_health_at = now
        return report


class AbstractStaticExtensionMeta(ABCMeta):
    """Meta class for AbstractStaticExtension."""

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)

        # Extension classes serve no routes: a route lives on a RouterMixin
        # manager. A @static_route here used to be collected and never
        # mounted (#241), so it is refused at definition instead. Found
        # without evaluating class attributes: a class property such as
        # ``root`` queries the database, which defining a class must never do.
        from zephyrex.lib.ClassMembers import decorated_functions

        declared = [
            method_name
            for method_name, _, _ in decorated_functions(cls, "_static_route_config")
        ]
        if declared:
            raise TypeError(
                f"{name} declares @static_route on {declared}, but extension "
                "classes serve no routes: put them on a RouterMixin manager "
                "as @custom_route"
            )
        return cls


class AbstractStaticExtension(
    AbstractStaticExtensionSystemComponent, metaclass=AbstractStaticExtensionMeta
):
    """
    Abstract base class for all AGInfrastructure extensions.

    All extensions should be static/abstract - no instantiation required.
    Discovery and functionality should be accessible through class methods.

    Example usage for system tasks:
        # Send an email through the email extension's root rotation: each
        # attempt calls send_email on the concrete provider serving that
        # provider instance, failing over to the next on error.
        await EXT_EMail.root.arotate(
            EXT_EMail.provider_call("send_email"),
            recipient="user@example.com", subject="Hi", body="...",
        )

        # Providers implement it taking the rotated ProviderInstanceModel:
        @classmethod
        async def send_email(cls, provider_instance, recipient, subject, body):
            ...

    Use ``root.rotate`` only for synchronous callables; an async callable
    under ``rotate`` would bypass failover.
    """

    # Extension metadata (class attributes)
    version: ClassVar[str] = "0.1.0"

    # -- Lifecycle ------------------------------------------------------------
    # The framework drives these through ``ExtensionRegistry``; subclasses
    # override only when they have real work to do. Every app build calls them
    # again, so an override must be safe to repeat.

    @classmethod
    def on_initialize(cls) -> bool:
        """Called once per app build, after the extension's BLL/PRV modules
        and models are imported and before the database is migrated.

        Register hooks and participation here. Return False only when the
        extension cannot function at all; the app build then fails with an
        error naming the extension."""
        return True

    @classmethod
    def on_start(cls) -> None:
        """Called at app startup (FastAPI lifespan), after the worker's
        database engine is ready and before background services start."""

    @classmethod
    def on_stop(cls) -> None:
        """Called at app shutdown (FastAPI lifespan), in reverse load order,
        before the worker's database engine is closed."""

    @classmethod
    def validate_config(cls) -> List[str]:
        """Return a list of configuration issues. Empty means valid."""
        return []

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

    _providers: ClassVar[List[Type[AbstractStaticProvider]]] = []

    @classmethod
    def _extensions_root(cls) -> str:
        """The directory holding this extension's folder: where its class is
        defined, when that is a folder named for it, so a bundled extension
        and a consumer's own both find their modules whichever root is
        active; otherwise the active root (``set_extensions_root``)."""
        import inspect

        defined_in = Path(inspect.getfile(cls)).resolve().parent
        if defined_in.name == cls.name:
            return str(defined_in.parent)
        return _resolve_extensions_dir()

    @classmethod
    def _load_component_modules(cls, prefix: str) -> Tuple[List[ModuleType], bool]:
        """Load this extension's ``{prefix}*.py`` modules (tests excluded).

        Returns the modules and whether the scan is partial: a module still
        mid-import (discovery triggered re-entrantly by a module-level import)
        exposes only the classes defined above its current import point, so it
        is skipped and the caller must not cache the result.
        """
        import glob
        import os

        from zephyrex.extensions.ExtensionLoader import load_extension_module

        extensions_root = cls._extensions_root()
        pattern = os.path.join(extensions_root, cls.name, f"{prefix}*.py")
        modules: List[ModuleType] = []
        partial = False
        for path in sorted(glob.glob(pattern)):
            if path.endswith("_test.py"):
                continue
            module_name = os.path.basename(path)[: -len(".py")]
            try:
                module = load_extension_module(extensions_root, cls.name, module_name)
            except Exception as e:
                logger.error(f"Failed to import extension module {module_name}: {e}")
                continue
            spec = getattr(module, "__spec__", None)
            if spec is not None and getattr(spec, "_initializing", False):
                partial = True
                continue
            modules.append(module)
        return modules, partial

    @classproperty
    @classmethod
    def providers(cls) -> List[Type[AbstractStaticProvider]]:
        """The provider classes defined in this extension's PRV_ modules.
        Cached after the first complete scan."""
        # This class's own cache only; an inherited one lists a base class's
        # providers.
        cached: List[Type[AbstractStaticProvider]] = cls.__dict__.get("_providers", [])
        if cached:
            return cached

        import inspect

        modules, partial = cls._load_component_modules("PRV_")
        providers: List[Type[AbstractStaticProvider]] = [
            obj
            for module in modules
            for _, obj in inspect.getmembers(module, inspect.isclass)
            if obj.__module__ == module.__name__
            and issubclass(obj, AbstractStaticProvider)
            and obj is not AbstractStaticProvider
        ]
        if not partial:
            cls._providers = providers
        return providers

    @classmethod
    def pip_requirements(cls) -> List[str]:
        """Every pip requirement this extension and its providers declare,
        one per package, as sorted PEP 508 strings: what the extension's
        ``zephyrex[<name>]`` extra installs and its manifest lists. Two
        declarations of one package must agree."""
        found: Dict[str, str] = {}
        for owner in (cls, *cls.providers):
            for dependency in owner.dependencies.pip:
                requirement = f"{dependency.name}{dependency.semver or ''}"
                earlier = found.setdefault(dependency.name.lower(), requirement)
                if earlier != requirement:
                    raise ValueError(
                        f"{cls.name}: {dependency.name} is declared as both "
                        f"{earlier!r} and {requirement!r}"
                    )
        return sorted(found.values())

    _root_rotation_cache: ClassVar[Optional[RotationManager]] = None

    @classproperty
    @classmethod
    def root(cls) -> Optional[RotationManager]:
        """The RotationManager targeting this extension's root rotation.

        Built against the attached app's model registry (extensions are static,
        so there is no request to take one from) and cached per registry.
        ``None`` before any app is built, or when the extension has no root
        rotation because it has no providers.
        """
        from zephyrex.logic.BLL_Providers import root_rotation_name
        from zephyrex.pydantic2.registry import ModelRegistry

        registry = ModelRegistry.attached()
        if registry is None:
            return None
        cached = cls._root_rotation_cache
        if cached is not None and cached.model_registry is registry:
            return cached

        manager = RotationManager(model_registry=registry, requester_id=env("ROOT_ID"))
        try:
            rotation = manager.get(name=root_rotation_name(cls.name))
        except HTTPException as e:
            if e.status_code != status.HTTP_404_NOT_FOUND:
                raise
            logger.debug("Extension %s has no root rotation", cls.name)
            return None
        manager.target_id = rotation.id
        cls._root_rotation_cache = manager
        return manager

    @classmethod
    def _discover_static_hooks(cls) -> None:
        """Discover and register static hook methods in the extension class."""
        for _, method, hook_paths in decorated_functions(cls, "_hook_info"):
            for hook_path in hook_paths:
                cls._hooks.setdefault(hook_path, []).append(method)
                logger.debug(f"Registered static hook {hook_path} -> {method.__name__}")

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
        cls._inheriting_hooks = True  # type: ignore[attr-defined]

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
                method._hook_info = []  # type: ignore[attr-defined]
            hook_path = (layer, domain, entity, function, time)
            method._hook_info.append(hook_path)  # type: ignore[attr-defined]
            logger.debug(
                f"Decorated static method {method.__name__} as hook for {hook_path}"
            )
            return method

        return decorator

    # ability decorator has been moved to module level

    _types_cache: ClassVar[Optional[Set[ExtensionType]]] = None

    @classproperty
    @classmethod
    def types(cls) -> Set[ExtensionType]:
        """
        Get the types of this extension based on its components.
        Cached after first access.

        Returns:
            Set of ExtensionType enums
        """
        # This class's own cache only; see ``models``.
        cached: Optional[Set[ExtensionType]] = cls.__dict__.get("_types_cache")
        if cached is not None:
            return cached

        types: Set[ExtensionType] = set()

        import glob
        import os

        # Get extension directory through Paths so a global
        # ``set_extensions_root`` override is honored.
        extension_dir = os.path.join(_resolve_extensions_dir(), cls.name)

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
                except OSError as e:
                    logger.debug(
                        "extension type detection: cannot read %s: %s",
                        bll_file,
                        e,
                    )

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
            except OSError as e:
                logger.debug(
                    "extension type detection: cannot read %s: %s",
                    bll_file,
                    e,
                )

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
                except OSError as e:
                    logger.debug(
                        "extension type detection: cannot read %s: %s",
                        bll_file,
                        e,
                    )

        cls._types_cache = types
        return types

    _models_cache: ClassVar[Optional[Set[Type]]] = None

    @classproperty
    @classmethod
    def models(cls) -> Set[Type]:
        """The models this extension contributes: the tables its BLL_ modules
        define and the external models its PRV_ modules define. Cached after
        the first complete scan."""
        # This class's own cache only; an inherited one lists a base class's
        # models.
        cached: Optional[Set[Type]] = cls.__dict__.get("_models_cache")
        if cached is not None:
            return cached

        import inspect

        from zephyrex.extensions.AbstractExternalModel import AbstractExternalModel
        from zephyrex.pydantic2.sqlalchemy.mixins import DatabaseMixin

        models: Set[Type] = set()
        bll_modules, bll_partial = cls._load_component_modules("BLL_")
        for module in bll_modules:
            for _, obj in inspect.getmembers(module, inspect.isclass):
                # @extension_model classes add fields to another extension's
                # table rather than defining one.
                if (
                    obj.__module__ == module.__name__
                    and issubclass(obj, DatabaseMixin)
                    and not getattr(obj, "_is_extension_model", False)
                ):
                    models.add(obj)

        prv_modules, prv_partial = cls._load_component_modules("PRV_")
        for module in prv_modules:
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if getattr(obj, "_is_extension_model", False) or (
                    obj.__module__ == module.__name__
                    and issubclass(obj, AbstractExternalModel)
                    and not inspect.isabstract(obj)
                ):
                    models.add(obj)

        if not (bll_partial or prv_partial):
            cls._models_cache = models
        return models

    @classmethod
    def provider_class_for(
        cls, provider_instance: ProviderInstanceModel
    ) -> Type["AbstractStaticProvider"]:
        """The provider class (among this extension's providers) that serves
        ``provider_instance``, matched through its Provider row's name.

        Raises LookupError when no provider of this extension serves it; in a
        rotation that advances to the next provider instance.
        """
        from zephyrex.logic.BLL_Providers import ProviderModel, _resolve_provider_name
        from zephyrex.pydantic2.registry import ModelRegistry

        registry = ModelRegistry.attached()
        if registry is None:
            raise LookupError("No app registry attached to resolve providers")
        provider = ProviderModel.DB(registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            return_type="dto",
            override_dto=ProviderModel,
            id=provider_instance.provider_id,
        )
        if provider is not None:
            for candidate in cls.providers:
                if _resolve_provider_name(candidate) == provider.name:
                    return candidate
        raise LookupError(
            f"{cls.__name__} has no provider serving instance {provider_instance.id}"
        )

    @classmethod
    def provider_call(
        cls, method_name: str, *, pass_instance: bool = True
    ) -> Callable[..., Any]:
        """A rotation callable that invokes ``method_name`` on the concrete
        provider class serving each rotated instance.

        Passing an abstract base method (``AbstractEmailProvider.send_email``)
        to a rotation would run the abstract stub, not the provider's
        implementation. ``pass_instance=False`` is for providers whose methods
        are configured from the environment and take no instance.
        """

        def call(
            provider_instance: ProviderInstanceModel, *args: Any, **kwargs: Any
        ) -> Any:
            method = getattr(cls.provider_class_for(provider_instance), method_name)
            if pass_instance:
                return method(provider_instance, *args, **kwargs)
            return method(*args, **kwargs)

        call.__name__ = method_name
        return call

    @classmethod
    def as_requester(cls, manager_class: Type[Any], requester_id: str) -> Any:
        """``manager_class`` acting as ``requester_id`` on the running app's
        registry: what an ability that reads or writes a user's records
        works through, under that user's permissions. 400 without a
        requester; 503 before an app is running."""
        from zephyrex.pydantic2.registry import ModelRegistry

        if not requester_id or not str(requester_id).strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{cls.name}: requester_id names the user acted for",
            )
        registry = ModelRegistry.attached()
        if registry is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"{cls.name}: no running app to act in",
            )
        return manager_class(model_registry=registry, requester_id=requester_id)

    @classmethod
    async def rotate_provider(cls, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Run ``method_name`` on the provider serving each instance of this
        extension's root rotation, with failover: the provider method gets the
        rotated ``ProviderInstanceModel`` first, and a
        ``TransientExternalError`` moves on to the next instance. 503 when no
        provider is configured."""
        root = cls.root
        if root is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"No {cls.name} provider is configured",
            )
        return await root.arotate(cls.provider_call(method_name), *args, **kwargs)

    @classmethod
    async def rotate_provider_for(
        cls, provider_name: str, method_name: str, *args: Any, **kwargs: Any
    ) -> Any:
        """:meth:`rotate_provider` over only the instances of the provider
        named ``provider_name``: for an operation on something that provider
        owns (its message id, its media id). 503 when the rotation holds no
        instance of it; 400 when this extension has no such provider."""
        if not any(p.name == provider_name for p in cls.providers):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{cls.name} has no provider {provider_name!r}",
            )
        root = cls.root
        if root is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"No {cls.name} provider is configured",
            )
        from zephyrex.logic.BLL_Providers import (
            ProviderInstanceManager,
            ProviderManager,
        )

        registry, requester_id = root.model_registry, env("ROOT_ID")
        provider = ProviderManager(
            model_registry=registry, requester_id=requester_id
        ).get(name=provider_name)
        instances = ProviderInstanceManager(
            model_registry=registry, requester_id=requester_id
        ).list(provider_id=provider.id)
        return await root.arotate(
            cls.provider_call(method_name),
            *args,
            provider_instance_ids=[instance.id for instance in instances],
            **kwargs,
        )

    @classmethod
    async def rotate_on_instance(
        cls, instance_ref: str, method_name: str, *args: Any, **kwargs: Any
    ) -> Any:
        """:meth:`rotate_provider` on one provider instance, named by its id
        or its name: for an operation on a device that instance is (a
        printer, a camera), which no other instance can stand in for. It is
        still retried as the rotation retries. 404 when no instance of this
        extension's providers in the rotation goes by ``instance_ref``."""
        root = cls.root
        if root is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"No {cls.name} provider is configured",
            )
        from zephyrex.logic.BLL_Providers import (
            ProviderInstanceManager,
            ProviderManager,
        )

        registry, requester_id = root.model_registry, env("ROOT_ID")
        providers = ProviderManager(model_registry=registry, requester_id=requester_id)
        own = {providers.get(name=provider.name).id for provider in cls.providers}
        instances = ProviderInstanceManager(
            model_registry=registry, requester_id=requester_id
        )
        matches = [
            instance
            for instance in (
                instances.list(id=instance_ref) or instances.list(name=instance_ref)
            )
            if instance.provider_id in own
        ]
        if not matches:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{cls.name} has no instance {instance_ref!r}",
            )
        return await root.arotate(
            cls.provider_call(method_name),
            *args,
            provider_instance_ids=[matches[0].id],
            **kwargs,
        )

    @classmethod
    def instances_of(cls) -> List[Dict[str, Any]]:
        """This extension's provider instances: id, name, provider."""
        root = cls.root
        if root is None:
            return []
        from zephyrex.logic.BLL_Providers import (
            ProviderInstanceManager,
            ProviderManager,
        )

        registry, requester_id = root.model_registry, env("ROOT_ID")
        providers = ProviderManager(model_registry=registry, requester_id=requester_id)
        instances = ProviderInstanceManager(
            model_registry=registry, requester_id=requester_id
        )
        found = []
        for provider in cls.providers:
            record = providers.get(name=provider.name)
            for instance in instances.list(provider_id=record.id) or []:
                found.append(
                    {
                        "id": str(instance.id),
                        "name": instance.name,
                        "provider": provider.name,
                    }
                )
        return found

    @classmethod
    def get_rotation_provider_instances_seed_data(cls) -> List[Dict[str, Any]]:
        """Seed rows linking this extension's root rotation to every instance
        of its providers.

        Empty before an app is built (no attached registry) or while the
        extension record or its root rotation does not exist yet.
        """
        from zephyrex.logic.BLL_Extensions import ExtensionModel
        from zephyrex.logic.BLL_Providers import (
            ProviderExtensionModel,
            ProviderInstanceModel,
            RotationModel,
            root_rotation_name,
        )
        from zephyrex.pydantic2.registry import ModelRegistry

        registry = ModelRegistry.attached()
        if registry is None:
            return []
        base = registry.DB.manager.Base
        Extension = ExtensionModel.DB(base)
        ProviderExtension = ProviderExtensionModel.DB(base)
        ProviderInstance = ProviderInstanceModel.DB(base)
        Rotation = RotationModel.DB(base)

        session = registry.DB.session()
        try:
            extension_record = session.execute(
                select(Extension).where(Extension.name == cls.name)
            ).scalar_one_or_none()
            if extension_record is None:
                return []

            root_rotation = session.execute(
                select(Rotation).where(
                    Rotation.extension_id == extension_record.id,
                    Rotation.name == root_rotation_name(cls.name),
                )
            ).scalar_one_or_none()
            if root_rotation is None:
                return []

            provider_ids = select(ProviderExtension.provider_id).where(
                ProviderExtension.extension_id == extension_record.id
            )
            instances = (
                session.execute(
                    select(ProviderInstance).where(
                        ProviderInstance.provider_id.in_(provider_ids)
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "rotation_id": str(root_rotation.id),
                    "provider_instance_id": str(instance.id),
                    "parent_id": None,
                }
                for instance in instances
            ]
        finally:
            session.close()

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

    class AbstractProvider(AbstractStaticProvider):  # type: ignore[no-redef]
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
