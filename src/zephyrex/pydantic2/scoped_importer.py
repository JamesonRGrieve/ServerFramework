# SPDX-License-Identifier: AGPL-3.0-or-later
"""Filesystem model-discovery + dependency-ordered import for ModelRegistry.

Extracted from ModelRegistry (#226): this collaborator owns the "discover &
import modules" concern -- scanning scopes for ``<file_type>_*`` modules, parsing
their imports into a load-time dependency DAG, and importing them in topological
order -- separate from the registry's "register & bind models" concern. The
registry keeps thin delegators so its public/test surface is unchanged.
"""

from __future__ import annotations

from typing import Any

from zephyrex.lib.Logging import logger
from zephyrex.lib.Paths import (
    extensions_dir as _resolve_extensions_dir,
    src_dir as _resolve_src_dir,
)


class ScopedModuleImporter:
    """Discovers and imports ``<file_type>_*`` modules in dependency order.

    Holds the per-registry ``imports`` cache so repeated scoped imports with the
    same (file_type, scopes) are served from cache, exactly as before.
    """

    def __init__(self, cache: Any) -> None:
        self._cache = cache

    def _scoped_import(
        self, file_type="DB", scopes=["database", "extensions"], clean=False
    ):
        """
        Private method to safely import models with automatic dependency resolution.
        This functionality was moved from lib.Import to be contained within ModelRegistry.

        Args:
            file_type: Prefix of the file name (e.g., "DB" for files starting with "DB_")
            scopes: List of relative module paths to search for files
            clean: If True, bypass cache and force fresh import

        Returns:
            tuple: (imported_modules, import_errors)
        """
        import glob
        import importlib
        import os
        import sys

        from sqlalchemy.orm import configure_mappers

        from zephyrex.lib.Environment import env

        # Create cache key based on file_type and scopes
        scoped_import_cache_key = f"{file_type}_{'+'.join(sorted(scopes))}"

        # Return cached result if available and not forcing clean
        if (
            not clean
            and hasattr(self, "_import_cache")
            and scoped_import_cache_key in self._cache
        ):
            logger.debug(
                f"Using cached scoped_import result for {scoped_import_cache_key}"
            )
            return self._cache[scoped_import_cache_key]

        # Cache is already initialized in __init__ via CacheManager

        # Get the source directory
        src_dir = _resolve_src_dir()

        # Dictionary to store files by scope
        files_by_scope = {}

        # Track already imported modules to prevent duplicates
        already_imported_modules = set()
        for module_name in sys.modules:
            if (
                module_name.startswith("zephyrex.database.DB_")
                or module_name.startswith("zephyrex.extensions.")
                or module_name.startswith("extensions.")
            ):
                already_imported_modules.add(module_name)
                logger.debug(f"Module already imported: {module_name}")

        # Expand "extensions" scope to include only enabled extension subdirectories
        expanded_scopes = []
        for scope in scopes:
            if scope == "extensions":
                # Get enabled extensions from APP_EXTENSIONS environment variable
                app_extensions = env("APP_EXTENSIONS")
                if app_extensions:
                    enabled_extensions = [
                        ext.strip() for ext in app_extensions.split(",") if ext.strip()
                    ]
                    logger.debug(
                        f"Only importing from enabled extensions: {enabled_extensions}"
                    )

                    # Include enabled extension directories. Check the
                    # consumer's extensions_path first, fall back to the
                    # bundled extensions inside the framework package.
                    extensions_dir = _resolve_extensions_dir()
                    bundled_dir = os.path.join(_resolve_src_dir(), "extensions")
                    for ext_name in enabled_extensions:
                        ext_path = os.path.join(extensions_dir, ext_name)
                        if os.path.isdir(ext_path):
                            expanded_scopes.append(f"extensions.{ext_name}")
                        elif extensions_dir != bundled_dir and os.path.isdir(
                            os.path.join(bundled_dir, ext_name)
                        ):
                            expanded_scopes.append(f"extensions.{ext_name}")
                        else:
                            logger.warning(
                                f"Enabled extension directory not found: {ext_path}"
                            )
                else:
                    logger.debug(
                        "No APP_EXTENSIONS configured, skipping extension imports"
                    )
            else:
                expanded_scopes.append(scope)

        # Always ensure core database models are imported first when dealing with extensions
        contains_extensions = any(
            scope.startswith("extensions") for scope in expanded_scopes
        )
        if contains_extensions and "database" not in expanded_scopes:
            logger.debug(
                "Adding database scope to ensure core models are imported for extensions"
            )
            expanded_scopes.insert(0, "database")

        # Find all Python files with the specified prefix in all scopes
        for scope in expanded_scopes:
            # Convert module path to directory path. For extension scopes,
            # check the consumer's extensions_path first, then fall back
            # to the bundled extensions inside the framework package.
            scope_parts = scope.split(".")
            scope_dir = os.path.join(src_dir, *scope_parts)
            if scope.startswith("extensions.") and not os.path.isdir(scope_dir):
                ext_name = scope_parts[1] if len(scope_parts) > 1 else ""
                consumer_dir = os.path.join(_resolve_extensions_dir(), ext_name)
                if os.path.isdir(consumer_dir):
                    scope_dir = consumer_dir

            # Create the pattern for files with the specified prefix
            files_pattern = os.path.join(scope_dir, f"{file_type}_*.py")

            # Get all matching files
            all_files = glob.glob(files_pattern)

            # Also discover package-form modules: a ``{file_type}_*`` directory
            # that is a Python package (has an ``__init__.py``). A decomposed
            # module (e.g. ``logic/BLL_Auth/``) is a directory, so the flat file
            # glob above no longer sees it; importing its ``__init__`` — which
            # re-exports the package's models/managers — makes discovery bind
            # them exactly as it did for the single-file form.
            all_files += [
                os.path.join(entry, "__init__.py")
                for entry in glob.glob(os.path.join(scope_dir, f"{file_type}_*"))
                if os.path.isdir(entry)
                and os.path.isfile(os.path.join(entry, "__init__.py"))
            ]

            # Filter out test files
            matching_files = [
                f for f in all_files if not os.path.basename(f).endswith("_test.py")
            ]

            if matching_files:
                files_by_scope[scope] = matching_files
                logger.debug(
                    f"Found {len(matching_files)} {file_type} files in {scope}"
                )

        if not files_by_scope:
            logger.debug(
                f"No {file_type} files found in any of the specified scopes: {scopes}"
            )
            return [], []

        # Build dependency graph and get ordered file list
        ordered_files, dependency_graph, module_to_file = self._build_dependency_graph(
            files_by_scope
        )

        # Track imported modules and errors
        imported_modules = []
        import_errors = []
        imported_file_paths = set()

        # Import modules in the determined order
        for file_path in ordered_files:
            # Skip if already imported
            if file_path in imported_file_paths:
                continue

            # Determine the module name and scope based on the file path.
            # Module names are made canonical (rooted at ``zephyrex.``)
            # so they collide-merge with Python's normal package machinery
            # rather than registering a parallel duplicate that re-defines
            # SQLAlchemy tables on the shared ``Base.metadata``.
            module_name = None
            for scope, files in files_by_scope.items():
                if file_path in files:
                    # A package-form module is matched via its ``__init__.py``;
                    # its module name is the package directory, not "__init__".
                    if os.path.basename(file_path) == "__init__.py":
                        stem = os.path.basename(os.path.dirname(file_path))
                    else:
                        stem = os.path.basename(file_path)[:-3]
                    canonical = (
                        f"zephyrex.{scope}.{stem}"
                        if not scope.startswith("zephyrex.")
                        else f"{scope}.{stem}"
                    )
                    module_name = canonical
                    break

            if module_name is None:
                module_name = f"unknown.{os.path.basename(file_path)[:-3]}"
                logger.warning(f"Could not determine module name for {file_path}")
                continue

            # Skip this module if it's already imported
            if module_name in already_imported_modules:
                logger.debug(f"Skipping already imported module: {module_name}")
                imported_modules.append(module_name)
                imported_file_paths.add(file_path)
                continue

            try:
                logger.debug(f"Importing {module_name}")

                # Check if core module is attempting to be re-imported
                if (
                    "zephyrex.database.DB_" in module_name
                    and module_name in sys.modules
                ):
                    logger.debug(f"Reusing already imported core module: {module_name}")
                    module = sys.modules[module_name]
                elif module_name.startswith(
                    "zephyrex.extensions."
                ) or module_name.startswith("extensions."):
                    # Item 61: route extension imports through the
                    # canonical loader so they work for out-of-tree
                    # extension trees and register under both legacy
                    # and synthesized names.
                    from zephyrex.extensions.ExtensionLoader import (
                        load_extension_module,
                    )

                    parts = module_name.split(".")
                    # Canonical form is ``zephyrex.extensions.<ext_name>.<file_stem>``;
                    # the legacy alias is ``extensions.<ext_name>.<file_stem>``.
                    # Pick the index of "extensions" so the same code path
                    # handles either prefix.
                    if "extensions" in parts:
                        ext_idx = parts.index("extensions")
                    else:
                        ext_idx = 0
                    if len(parts) >= ext_idx + 3:
                        ext_name = parts[ext_idx + 1]
                        file_stem = parts[-1]
                        extensions_root = _resolve_extensions_dir()
                        module = load_extension_module(
                            extensions_root, ext_name, file_stem
                        )
                    else:
                        spec = importlib.util.spec_from_file_location(
                            module_name, file_path
                        )
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)
                else:
                    # If Python's normal package machinery already loaded
                    # this module (e.g. an earlier module body did
                    # ``from zephyrex.logic.BLL_X import Y``),
                    # reuse that instance rather than re-loading via
                    # spec_from_file_location — re-loading would re-execute
                    # the module body and double-register SQLAlchemy
                    # tables on the shared ``Base.metadata``.
                    if module_name in sys.modules:
                        logger.debug(f"Reusing already imported module: {module_name}")
                        module = sys.modules[module_name]
                    elif os.path.basename(file_path) == "__init__.py":
                        # Package-form BLL module (e.g. ``logic/BLL_Auth/``):
                        # import through normal package machinery so the
                        # package ``__path__`` and its submodule imports resolve
                        # — ``spec_from_file_location`` on an ``__init__`` would
                        # not set up the package search path.
                        module = importlib.import_module(module_name)
                    else:
                        spec = importlib.util.spec_from_file_location(
                            module_name, file_path
                        )
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)

                # Register this module as imported
                already_imported_modules.add(module_name)

                # Tag tables with their module path for filtering in migrations
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    # Look for SQLAlchemy Table or declarative model classes
                    if hasattr(attr, "__tablename__") and hasattr(attr, "__table__"):
                        # Add module_path to table.info
                        table = getattr(attr, "__table__")
                        if "info" not in table.__dict__:
                            table.info = {}
                        table.info["module_path"] = module_name

                        # Ensure bidirectional relationship between table and class
                        if not hasattr(table, "class_") or table.class_ is None:
                            table.class_ = attr
                            logger.debug(
                                f"Set class_ attribute for table {attr.__tablename__} to {attr}"
                            )

                imported_modules.append(module_name)
                imported_file_paths.add(file_path)
                logger.debug(f"Successfully imported {module_name}")

            except Exception as e:
                import_errors.append((file_path, str(e)))
                logger.error(f"Error importing {file_path}: {e}")

        # Log summary
        if import_errors:
            error_details = []
            for file_path, error in import_errors:
                error_details.append(f"  {file_path}: {error}")

            error_message = (
                f"Failed to import {len(import_errors)} model files:\n"
                + "\n".join(error_details)
            )
            logger.error(error_message)
            raise ImportError(error_message)

        # Configure mappers to resolve any remaining issues
        try:
            configure_mappers()
        except Exception as e:
            logger.error(f"Error configuring mappers: {e}")

        # Cache the result for future use
        result = (imported_modules, import_errors)
        self._cache[scoped_import_cache_key] = result
        logger.debug(f"Cached scoped_import result for {scoped_import_cache_key}")

        return result

    def _build_dependency_graph(self, files_by_scope):
        """
        Private method to build a dependency graph of modules and determine import order.

        Args:
            files_by_scope: Dict mapping scope names to lists of file paths to analyze

        Returns:
            tuple: (ordered_files, module_graph, module_to_file)
        """

        import networkx as nx

        # Create mapping from module name to file path
        module_to_file = {}
        module_classes = {}
        module_imports = {}
        dependencies = {}
        all_defined_classes = set()
        parsing_errors = []

        # First pass: collect all modules and their defined classes from all scopes
        for scope, file_paths in files_by_scope.items():
            for file_path in file_paths:
                try:
                    module_name, deps, classes, imports = (
                        self._parse_imports_and_dependencies(file_path, scope)
                    )

                    module_to_file[module_name] = file_path
                    module_classes[module_name] = classes
                    module_imports[module_name] = imports
                    dependencies[module_name] = deps
                    all_defined_classes.update(classes)
                except Exception as e:
                    parsing_errors.append((file_path, str(e)))
                    logger.error(f"Skipping {file_path} due to parsing error: {e}")
                    continue

        if parsing_errors:
            logger.warning(f"Encountered {len(parsing_errors)} parsing errors")
            for file_path, error in parsing_errors:
                logger.warning(f"  {file_path}: {error}")

        # Build a directed graph for module dependencies
        G = nx.DiGraph()

        # Add all modules as nodes
        for module_name in module_to_file.keys():
            G.add_node(module_name)

        # Build an inverted index (class_fqn -> defining module) ONCE so each
        # dependency resolves in O(1). The prior code re-scanned every module
        # for every dep of every module, i.e. O(M^2 * D). Fully-qualified class
        # names are unique per module (each is prefixed by its own module name),
        # so there is exactly one owner per name; ``setdefault`` keeps the first
        # module that claims a name, replicating the original "break on first
        # match" semantics even in the theoretical duplicate case.
        class_to_module: Dict[str, str] = {}
        for other_module, classes in module_classes.items():
            for class_fqn in classes:
                class_to_module.setdefault(class_fqn, other_module)

        # Add edges for dependencies
        for module_name, deps in dependencies.items():
            for dep in deps:
                # Find which module defines this dependency
                other_module = class_to_module.get(dep)
                if (
                    other_module is not None
                    and other_module != module_name  # Avoid self-dependencies
                ):
                    G.add_edge(module_name, other_module)

        # Check for cycles and resolve them
        try:
            cycles = list(nx.simple_cycles(G))
            if cycles:
                logger.warning(f"Detected {len(cycles)} circular dependencies")
                for cycle in cycles:
                    logger.warning(f"Circular dependency: {' -> '.join(cycle)}")

                # Break cycles by removing edges
                while cycles:
                    edge_cycle_count = {}
                    for cycle in cycles:
                        for i in range(len(cycle)):
                            edge = (cycle[i], cycle[(i + 1) % len(cycle)])
                            edge_cycle_count[edge] = edge_cycle_count.get(edge, 0) + 1

                    if not edge_cycle_count:
                        break

                    max_edge = max(edge_cycle_count.items(), key=lambda x: x[1])[0]
                    G.remove_edge(*max_edge)
                    logger.warning(
                        f"Breaking cycle by removing dependency: {max_edge[0]} -> {max_edge[1]}"
                    )
                    cycles = list(nx.simple_cycles(G))
        except nx.NetworkXNoCycle:
            pass

        # Get topological sort order
        try:
            ordered_modules = list(nx.topological_sort(G))
        except nx.NetworkXUnfeasible:
            logger.warning("Graph still contains cycles, using fallback ordering")
            ordered_modules = list(module_to_file.keys())

        # Convert ordered modules to file paths
        ordered_files = [
            module_to_file[m] for m in ordered_modules if m in module_to_file
        ]

        # Create module graph dict for backward compatibility
        module_graph = {node: set(G.successors(node)) for node in G.nodes()}

        return ordered_files, module_graph, module_to_file

    def _parse_imports_and_dependencies(self, file_path, scope="database"):
        """
        Private method to parse a Python file to identify its imports and class dependencies.

        Args:
            file_path: Path to the Python file
            scope: The module scope (e.g., "database" or "zephyrex.extensions.prompts")

        Returns:
            tuple: (module_name, dependencies, defined_classes, imports)
        """
        import os

        if os.path.basename(file_path) == "__init__.py":
            # Package-form module: name it after the package dir, not "__init__"
            # (otherwise every ``{TYPE}_*/__init__.py`` collides on one key).
            current_module = f"{scope}.{os.path.basename(os.path.dirname(file_path))}"
        else:
            current_module = f"{scope}.{os.path.basename(file_path)[:-3]}"

        try:
            # Use AST to parse the file
            imports, import_froms, classes = self._parse_module_ast(file_path)

            # Process the results
            all_imports = set(imports)
            dependencies = set(import_froms)
            defined_classes = {f"{current_module}.{cls}" for cls in classes}

            return current_module, dependencies, defined_classes, all_imports
        except Exception as e:
            logger.error(f"Error parsing {file_path}: {e}")
            return current_module, set(), set(), set()

    def _parse_module_ast(self, file_path):
        """
        Private method to parse a Python file using the AST module to extract imports and classes.

        Args:
            file_path: Path to the Python file

        Returns:
            Tuple of (imports, import_froms, classes)
        """
        import ast

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            tree = ast.parse(content)
            imports = [
                name.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for name in node.names
            ]
            import_froms = [
                f"{node.module}.{name.name}"
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
                for name in node.names
            ]
            classes = [
                node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
            ]

            return imports, import_froms, classes
        except UnicodeDecodeError:
            # Try with latin-1 encoding if UTF fails
            try:
                with open(file_path, "r", encoding="latin-1") as f:
                    content = f.read()
                logger.warning(f"File {file_path} required latin-1 encoding")
                tree = ast.parse(content)

                imports = []
                import_froms = []
                classes = []

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for name in node.names:
                            imports.append(name.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            module_name = node.module
                            for name in node.names:
                                import_froms.append(f"{module_name}.{name.name}")
                    elif isinstance(node, ast.ClassDef):
                        classes.append(node.name)

                return imports, import_froms, classes
            except Exception as e:
                logger.error(f"Error parsing {file_path} with alternate encoding: {e}")
                raise ValueError(
                    f"Failed to parse dependencies in {file_path}: {str(e)}"
                )
        except Exception as e:
            logger.error(f"Error parsing {file_path}: {e}")
            raise ValueError(f"Failed to parse dependencies in {file_path}: {str(e)}")
