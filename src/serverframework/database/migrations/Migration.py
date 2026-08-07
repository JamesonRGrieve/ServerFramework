#!/usr/bin/env python
"""
Unified database migration management tool for core and extension migrations.
Handles initialization, creation, applying, and management of migrations.
"""

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

import stringcase
from filelock import FileLock, Timeout

# Get the current file's directory
current_file_dir = Path(__file__).resolve().parent
template_dir = current_file_dir / "template"
src_path = Path(__file__).resolve().parent.parent.parent

# Add project root and src directories to path
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from serverframework.lib.Logging import logger


class MigrationManager:
    """
    Centralized manager for database migrations.
    Handles both core system and extension-specific migrations through Alembic.
    """

    # Class variable to store extensions directory name for static methods
    _extensions_dir_name = "extensions"

    @staticmethod
    def env_setup_python_path(file_path):
        """Sets up the Python path for imports."""
        current_file_path = file_path
        migrations_dir = current_file_path.parent
        database_dir = migrations_dir.parent
        src_dir = database_dir.parent
        root_dir = src_dir.parent

        # Normalize paths to avoid duplicated segments
        src_dir_norm = str(src_dir)
        root_dir_norm = str(root_dir)

        # Add to Python path if not already present
        if root_dir_norm not in sys.path:
            sys.path.insert(0, root_dir_norm)
        if src_dir_norm not in sys.path:
            sys.path.insert(0, src_dir_norm)

        return {
            "migrations_dir": migrations_dir,
            "database_dir": database_dir,
            "src_dir": src_dir,
            "root_dir": root_dir,
        }

    @staticmethod
    def env_import_module_safely(module_path, error_msg=None):
        """Handles module imports with multiple strategies."""
        try:
            import importlib
            import sys

            # Check if the module is already imported
            if module_path in sys.modules:
                return sys.modules[module_path]

            return importlib.import_module(module_path)
        except ImportError as e:
            if error_msg:
                logger.debug(f"{error_msg}: {e}")
            return None

    @staticmethod
    def env_import_module_from_file(file_path, module_name):
        """Import a module directly from a file path."""
        try:
            import importlib.util
            import sys

            # Check if the module is already imported
            if module_name in sys.modules:
                return sys.modules[module_name]

            # Also check with database. prefix for core modules
            full_module_name = f"serverframework.database.{module_name}"
            if full_module_name in sys.modules:
                return sys.modules[full_module_name]

            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if not spec:
                logger.debug(
                    f"Could not create spec for {module_name} from {file_path}"
                )
                return None

            module = importlib.util.module_from_spec(spec)

            # Store the file path in the module for table ownership tracking
            module.__file_path__ = str(file_path)

            # Check if this is an extension module by examining the path
            file_path_str = str(file_path).replace("\\", "/")
            if "/extensions/" in file_path_str:
                # Extract extension name from path
                parts = file_path_str.split("/extensions/")
                if len(parts) > 1:
                    ext_parts = parts[1].split("/")
                    if len(ext_parts) > 0:
                        module.__extension__ = ext_parts[0]

            spec.loader.exec_module(module)
            return module
        except Exception as e:
            logger.debug(f"Error importing {module_name} from {file_path}: {e}")
            return None

    @staticmethod
    def env_is_table_owned_by_extension(table) -> Optional[str]:
        """Return the owning extension name, or None for core tables.

        Resolution rule (per IMPROVEMENTS_ORDERED.md Item 24):
        1. table.info["extension"] takes precedence — set deterministically by
           ModelRegistry._stamp_extension_table_ownership for any table whose
           Pydantic source module lives under extensions.<name>.*
        2. Fall back to file-path inspection of the SA model's source module
           for tables created outside the registry path (e.g. raw alembic CLI
           usage with no committed registry).

        Note: core tables that have been *extended* by an @extension_model
        decorator are still core-owned and return None. The extender names
        are tracked separately in table.info["extensions"] (a set/list of
        names); enumerate via that key, not this function.
        """
        owner = (table.info or {}).get("extension")
        if owner:
            return owner

        # File-path fallback — covers tables built without ModelRegistry.
        table_class = getattr(table, "class_", None)
        if table_class is None:
            return None
        module_name = getattr(table_class, "__module__", "") or ""
        parts = module_name.split(".")
        if len(parts) >= 2 and parts[0] == MigrationManager._extensions_dir_name:
            return parts[1]
        return None

    @staticmethod
    def env_table_extenders(table) -> List[str]:
        """Return the list of extension names that inject fields into this
        (core) table via @extension_model. Empty for tables with no
        injections. Sorted for deterministic autogenerate output."""
        raw = (table.info or {}).get("extensions") or ()
        return sorted(raw)

    def _compute_migration_order(self, extensions: List[str]) -> List[str]:
        """Topologically sort extensions for FK-aware migration ordering.

        Thin adapter around :func:`database.MigrationOrdering.compute_migration_order`
        (Item 49). Gathers the inputs the algorithm needs from this manager's
        attached registry:

         (a) Declared ``EXT_Dependency`` relationships, looked up via the
             ExtensionRegistry attached to ``self._model_registry``. Optional
             dependencies are filtered out (only required deps contribute edges).
         (b) The live ``Base.metadata`` so FK references can be inspected.

        Returns a list of extension names — restricted to the input set —
        in a valid migration order. On a cycle, ``MigrationOrderingError``
        is re-raised as ``RuntimeError`` so existing callers (which catch
        ``RuntimeError``) keep working; the original message naming the
        offending extensions, FK columns, and join-table workaround is
        preserved verbatim.
        """
        if not extensions:
            return []

        from serverframework.database.MigrationOrdering import (
            MigrationOrderingError,
            compute_migration_order as _compute,
        )

        # Build the declared-deps mapping. Only consider extensions that
        # appear in the input (or are referenced as dependencies of one).
        ext_dependencies: Dict[str, List[str]] = {n: [] for n in extensions}
        registry = self._model_registry
        ext_registry = getattr(registry, "extension_registry", None) if registry else None
        if ext_registry is not None:
            from serverframework.lib.Dependencies import EXT_Dependency

            for ext_class in getattr(ext_registry, "extensions", []):
                ext_name = getattr(ext_class, "name", None)
                if ext_name not in ext_dependencies:
                    continue
                deps = getattr(ext_class, "dependencies", None)
                if deps is None:
                    continue
                ext_deps = []
                if hasattr(deps, "ext"):
                    ext_deps = list(deps.ext)
                elif hasattr(deps, "__iter__"):
                    ext_deps = list(deps)
                for dep in ext_deps:
                    if not isinstance(dep, EXT_Dependency) or dep.optional:
                        continue
                    if dep.name and dep.name != ext_name:
                        ext_dependencies[ext_name].append(dep.name)

        metadata = None
        if registry is not None:
            base = getattr(getattr(registry, "database_manager", None), "Base", None)
            metadata = getattr(base, "metadata", None) if base else None

        try:
            full_order = _compute(metadata, ext_dependencies)
        except MigrationOrderingError as e:
            # Preserve the historic RuntimeError contract for callers that
            # catch RuntimeError; the message body comes verbatim from
            # MigrationOrderingError.__str__.
            raise RuntimeError(str(e)) from e

        # Restrict to the requested extension set, preserving order.
        wanted = set(extensions)
        return [n for n in full_order if n in wanted]

    def audit_table_ownership(self) -> bool:
        """Print a tab-separated audit of table → owner → extenders.

        Boots the model registry (so info dict is populated) and walks
        Base.metadata.tables. One line per table:
            <table_name>\\t<owner_or_core>\\t<extenders_csv_or_->

        Returns True on success.
        """
        try:
            from serverframework.logic import BLL_Auth, BLL_Extensions, BLL_Providers  # noqa: F401
            from serverframework.lib.Pydantic import ModelRegistry, Base

            ModelRegistry().commit(extensions=os.environ.get("APP_EXTENSIONS", ""))

            logger.info("table\towner\textenders")
            for table_name in sorted(Base.metadata.tables.keys()):
                table = Base.metadata.tables[table_name]
                owner = self.env_is_table_owned_by_extension(table) or "core"
                extenders = self.env_table_extenders(table)
                ext_col = ",".join(extenders) if extenders else "-"
                logger.info(f"{table_name}\t{owner}\t{ext_col}")
            return True
        except Exception as e:
            logger.error(f"audit-ownership failed: {e}", exc_info=True)
            return False

    @staticmethod
    def _is_core_table_extended_by_extension(table, extension_name):
        """Whether `extension_name` extends `table` via @extension_model.

        Reads the deterministic stamp written by
        ModelRegistry._stamp_extension_table_ownership() at commit time.
        """
        return extension_name in (table.info or {}).get("extensions", set())

    @staticmethod
    def env_include_object(object, name, type_, reflected, compare_to, base=None):
        """Filter objects for inclusion in an autogenerate run.

        Resolution rule (Item 24):
        - Extension migration: include if the table is owned by this extension
          OR if this extension extends the (core-owned) table via @extension_model.
        - Core migration: include if the table is not owned by any extension.
          Extender-only annotations don't change ownership — a core table
          extended by an extension still belongs to core's autogenerate.
        """
        if type_ != "table":
            return True

        from serverframework.lib.Environment import env

        extension_name = (
            base.config.attributes.get("extension") if base and base.config else None
        ) or env("ALEMBIC_EXTENSION")

        owner = MigrationManager.env_is_table_owned_by_extension(object)
        extenders = MigrationManager.env_table_extenders(object)

        if extension_name:
            include = owner == extension_name or extension_name in extenders
            logger.debug(
                f"Table {name}: owner={owner!r}, extenders={extenders} -> "
                f"include for ext {extension_name!r}? {include}"
            )
            return include

        include = owner is None
        logger.debug(
            f"Table {name}: owner={owner!r} -> include for core? {include}"
        )
        return include

    @staticmethod
    def env_setup_alembic_config(config):
        """Resolve the version_table name for this Alembic invocation.

        Preference order:
        1. `version_table` main option set on the in-memory Config by
           MigrationManager._make_alembic_config.
        2. The extension name from `config.attributes["extension"]`.
        3. The legacy ALEMBIC_EXTENSION env var (back-compat for any direct
           `alembic` CLI invocations that bypass MigrationManager).
        4. "alembic_version" (core).
        """
        from serverframework.lib.Environment import env

        explicit = config.get_main_option("version_table")
        if explicit:
            return explicit

        extension_name = config.attributes.get("extension") or env("ALEMBIC_EXTENSION")
        return (
            f"alembic_version_{extension_name}" if extension_name else "alembic_version"
        )

    @staticmethod
    def env_get_alembic_context_config(
        connection=None, url=None, target_metadata=None, version_table="alembic_version"
    ):
        """Configure context for either online or offline mode."""
        config_args = {
            "target_metadata": target_metadata,
            "include_object": MigrationManager.env_include_object,
            "version_table": version_table,
            "render_as_batch": True,  # Helps with SQLite "table already exists" errors
        }

        if connection:
            # Online mode with connection
            config_args["connection"] = connection
        else:
            # Offline mode with URL
            config_args.update(
                {
                    "url": url,
                    "literal_binds": True,
                    "dialect_opts": {"paramstyle": "named"},
                }
            )

        return config_args

    def __init__(
        self,
        test_mode=False,
        custom_db_info=None,
        extensions_dir="extensions",
        database_dir="database",
        model_registry=None,
        test_versions_root=None,
        extensions_path=None,
    ):
        """Initialize the migration manager.

        Args:
            test_mode: Whether to run in test mode
            custom_db_info: Custom database configuration
            extensions_dir: Directory name for extensions (relative to src_dir), defaults to "extensions"
            database_dir: Directory name for database (relative to src_dir), defaults to "database"
            model_registry: ModelRegistry instance to thread through to env.py
                via cfg.attributes. Required for migrations that run during
                ModelRegistry.commit() — env.py looks up Base._model_registry
                but the DatabaseManager attached to env.py's Base is a
                different instance from the one driving commit(), so the
                attribute lookup misses. Pass the live registry here so env.py
                can resolve it via context.config.attributes["model_registry"].
            test_versions_root: Optional Path. When set (and test_mode=True),
                test-mode revision files for both core and extensions land
                under this root rather than in src/{database,extensions/<ext>}/
                migrations/test_versions/. This keeps src/ pristine across
                test runs — pytest can pass tmp_path here to scope test
                artifacts to the test's lifecycle. When None, test_versions
                directories under the source tree are used (legacy behavior,
                relied on by direct CLI test invocations).
            extensions_path: Optional path to an out-of-tree extensions
                root (Item 62). When provided, migration discovery walks
                this path rather than the bundled ``<src>/extensions``.
                Equivalent to calling ``lib.Paths.set_extensions_root``
                with the same value but scoped to this MigrationManager
                instance. When None, falls back to
                ``lib.Paths.extensions_dir()`` which already honors any
                global override (set by ExtensionRegistry).
        """
        # Store directory configuration
        self.extensions_dir_name = extensions_dir
        self.database_dir_name = database_dir
        self._extensions_path_override = extensions_path

        # Set class variable for static methods to access
        MigrationManager._extensions_dir_name = extensions_dir

        self.paths = self._setup_python_path()
        self.current_extension = None
        self.configured_extensions = self._get_configured_extensions()

        # Set test mode from parameter (no auto-detection)
        self.test_mode = test_mode

        # Initialize database manager
        self._db_manager = None
        self._model_registry = model_registry
        self._test_versions_root = (
            Path(test_versions_root) if test_versions_root else None
        )

        # Use custom database info if provided, otherwise get from Base
        if custom_db_info:
            self.db_info = custom_db_info
            logger.debug(f"Using custom database configuration: {custom_db_info}")
        else:
            db_manager = self.db_manager
            self.db_info = {
                "type": db_manager.DATABASE_TYPE,
                "name": db_manager.DATABASE_NAME,
                "url": db_manager.DATABASE_URI,
            }

        self.template_dir = current_file_dir / "template"

        if self.test_mode:
            logger.debug("Migration manager initialized in TEST MODE")
        else:
            logger.debug("Migration manager initialized in PRODUCTION MODE")

        logger.debug(f"Using extensions directory: {extensions_dir}")
        logger.debug(f"Using database directory: {database_dir}")

    @property
    def db_manager(self):
        """Get or create the database manager instance."""
        if self._db_manager is None:
            from serverframework.database.DatabaseManager import DatabaseManager

            # Use test prefix in test mode to avoid touching production database
            # DatabaseManager.__init__ already calls init_engine_config(db_prefix)
            db_prefix = "test.migration" if self.test_mode else ""
            self._db_manager = DatabaseManager(db_prefix)
        return self._db_manager

    def _get_versions_directory_name(self):
        """Get the appropriate versions directory name based on test mode."""
        return "test_versions" if self.test_mode else "versions"

    def _resolve_core_versions_dir(self):
        """Resolve the core (non-extension) version_locations directory.

        In production this is src/database/migrations/versions/. In test mode
        with a test_versions_root override, it's <root>/_core/versions/, so
        the source tree stays pristine across runs. Otherwise it's the
        in-tree test_versions/ directory (legacy CLI back-compat)."""
        if self.test_mode and self._test_versions_root is not None:
            return self._test_versions_root / "_core" / "versions"
        return self.paths["migrations_dir"] / self._get_versions_directory_name()

    # def _setup_logging(self):
    #     """Set up logging with file and console handlers."""
    #     logger = logger.getLogger("migration_manager")
    #     logger.setLevel(logger.debug)

    #     formatter = logger.Formatter(
    #         "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    #     )

    #     # Console handler
    #     console_handler = logger.StreamHandler()
    #     console_handler.setFormatter(formatter)
    #     logger.addHandler(console_handler)

    #     # File handler
    #     try:
    #         log_file = current_file_dir / "migration.log"
    #         file_handler = logger.FileHandler(str(log_file))
    #         file_handler.setFormatter(formatter)
    #         logger.addHandler(file_handler)
    #         logger.debug(f"File logging initialized to {log_file}")
    #     except Exception as e:
    #         logger.warning(f"Failed to initialize file logging: {e}")

    #     return logger

    def _setup_python_path(self):
        """Set up Python path for imports.

        Item 62: Consults ``lib.Paths.extensions_dir()`` for the extensions
        root rather than computing it inline as ``<src>/<extensions_dir_name>``.
        This honors:
          1. ``extensions_path`` passed to ``MigrationManager(...)`` (highest
             priority — scopes the override to this manager instance).
          2. ``lib.Paths.set_extensions_root(...)`` global override (set by
             ``ExtensionRegistry(extensions_path=...)`` or by an explicit
             setup call).
          3. The bundled ``<src>/extensions`` location when neither override
             is set.
        """
        from serverframework.lib.Paths import extensions_path as resolve_extensions_path

        # Get the current migrations directory (where this file is located)
        migrations_dir = current_file_dir

        # Always use standard database/migrations structure for migration infrastructure
        database_dir = migrations_dir.parent  # migrations -> database
        src_dir = database_dir.parent  # database -> src
        root_dir = src_dir.parent  # src -> root

        # Extensions directory: route through lib.Paths so the same source
        # of truth governs code loading and migration discovery.
        instance_override = getattr(self, "_extensions_path_override", None)
        extensions_dir = resolve_extensions_path(instance_override)

        # Back-compat: if a non-default extensions_dir_name was passed and
        # no override is active, fall back to the legacy in-src-dir form.
        if instance_override is None and getattr(
            self, "extensions_dir_name", "extensions"
        ) != "extensions":
            extensions_dir = src_dir / self.extensions_dir_name

        for path in [str(root_dir), str(src_dir)]:
            if path not in sys.path:
                sys.path.insert(0, path)

        return {
            "migrations_dir": migrations_dir,
            "database_dir": database_dir,
            "src_dir": src_dir,
            "root_dir": root_dir,
            "extensions_dir": extensions_dir,
        }

    def _get_configured_extensions(self):
        """Get the list of configured extensions from the environment variable."""

        extension_list = self._parse_csv_env_var("APP_EXTENSIONS")
        if not extension_list:
            logger.debug(
                "No APP_EXTENSIONS environment variable set. No extensions will be processed."
            )
            return []
        else:
            logger.debug(f"Using extensions from APP_EXTENSIONS: {extension_list}")
        return extension_list

    def debug_file_info(self, file_path):
        """Debug a file by showing its permissions, size, and other metadata."""
        try:
            path = Path(file_path)
            if not path.exists():
                logger.debug(f"File {path} does not exist")
                return

            # Get file stats
            import stat
            from datetime import datetime as _datetime

            stats = path.stat()

            mode = stats.st_mode
            perms = ""
            perms += "r" if mode & stat.S_IRUSR else "-"
            perms += "w" if mode & stat.S_IWUSR else "-"
            perms += "x" if mode & stat.S_IXUSR else "-"
            perms += "r" if mode & stat.S_IRGRP else "-"
            perms += "w" if mode & stat.S_IWGRP else "-"
            perms += "x" if mode & stat.S_IXGRP else "-"
            perms += "r" if mode & stat.S_IROTH else "-"
            perms += "w" if mode & stat.S_IWOTH else "-"
            perms += "x" if mode & stat.S_IXOTH else "-"

            mtime = _datetime.fromtimestamp(stats.st_mtime)
            atime = _datetime.fromtimestamp(stats.st_atime)

            logger.debug(f"File: {path}")
            logger.debug(f"Size: {stats.st_size} bytes")
            logger.debug(f"Permissions: {perms} ({oct(stats.st_mode)})")
            logger.debug(f"Last modified: {mtime}")
            logger.debug(f"Last accessed: {atime}")
            logger.debug(f"Parent dir exists: {path.parent.exists()}")
            logger.debug(f"Parent dir writable: {os.access(path.parent, os.W_OK)}")

            # Try to read the first few bytes
            try:
                with open(path, "rb") as f:
                    first_bytes = f.read(20)
                logger.debug(f"First bytes: {first_bytes}")
            except Exception as e:
                logger.warning(f"Cannot read file: {e}")

        except Exception as e:
            logger.error(f"Error getting file info for {file_path}: {e}")

    def cleanup_file(self, file_path, message=None):
        """Safely remove a file if it exists."""
        if file_path and Path(file_path).exists():
            try:
                Path(file_path).unlink()
                if message:
                    logger.debug(message)
                return True
            except Exception as e:
                logger.warning(f"Could not clean up {file_path}: {e}")
        return False

    def create_temp_file(self, content, suffix=None, directory=None):
        """Create a temporary file with given content.

        Args:
            content: Text to write into the file.
            suffix: Optional file suffix.
            directory: Optional directory where the file should be created.
        """

        temp_file_kwargs = {"suffix": suffix, "delete": False}

        if directory is not None:
            directory_path = Path(directory)
            directory_path.mkdir(parents=True, exist_ok=True)
            temp_file_kwargs["dir"] = str(directory_path)

        temp_file = tempfile.NamedTemporaryFile(**temp_file_kwargs)
        temp_file.write(content.strip().encode("utf-8"))
        temp_file.close()
        return Path(temp_file.name)

    def write_file(self, file_path, content):
        """Write content to a file, creating parent directories if needed."""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception as e:
            logger.error(f"Error writing to file {file_path}: {e}")
            return False

    def cleanup_test_artifacts(self):
        """Remove core-side test artifacts left behind by older runs.

        Post-Phase-4 there is nothing in extension folders to clean (the
        in-memory Alembic config path writes nothing there). The only
        ephemera left are:
         - in-tree test_versions/ directories under src/database/migrations
           and src/extensions/<name>/migrations (only present for legacy
           CLI test runs that didn't pass test_versions_root)
         - the test SQLite file at db_info["file_path"] when test_mode and
           the db name has "test" in it
         - any tmp*.ini stragglers from pre-Phase-4 versions

        Returns True regardless of whether anything was actually deleted —
        callers use this as a best-effort sweep at end-of-run.
        """
        if not self.test_mode:
            return True

        try:
            core_test = self.paths["migrations_dir"] / "test_versions"
            if core_test.exists():
                shutil.rmtree(core_test, ignore_errors=True)
                logger.debug(f"Removed core test_versions: {core_test}")

            for ext_name in self.configured_extensions:
                ext_test = (
                    self.paths["extensions_dir"]
                    / ext_name
                    / "migrations"
                    / "test_versions"
                )
                if ext_test.exists():
                    shutil.rmtree(ext_test, ignore_errors=True)
                    logger.debug(f"Removed extension test_versions: {ext_test}")

            file_path = self.db_info.get("file_path") if self.db_info else None
            db_name = self.db_info.get("name", "") if self.db_info else ""
            if file_path and "test" in db_name.lower():
                test_db = Path(file_path)
                if test_db.exists():
                    test_db.unlink()
                    logger.debug(f"Removed test database file: {test_db}")

            for stale in self.paths["src_dir"].glob("tmp*alembic*.ini"):
                self.cleanup_file(stale, f"Removed stale tmp ini: {stale}")
        except Exception as e:
            logger.warning(f"cleanup_test_artifacts: {e}", exc_info=True)
            return False
        return True


    def _make_alembic_config(self, extension_name, versions_dir):
        """Build an Alembic Config in memory.

        `script_location` is always the shared core dir (src/database/migrations/)
        — env.py and script.py.mako are never copied into per-extension folders.
        Only `version_locations` and `version_table` vary by extension.

        The extension name is propagated via `cfg.attributes["extension"]`,
        which env.py reads via `context.config.attributes`.
        """
        from alembic.config import Config

        cfg = Config()  # No path argument => never writes a config file.
        cfg.set_main_option("script_location", str(self.paths["migrations_dir"]))
        cfg.set_main_option("sqlalchemy.url", self.db_info["url"])
        cfg.set_main_option("version_locations", str(versions_dir))
        cfg.set_main_option(
            "version_table",
            "alembic_version" if not extension_name else f"alembic_version_{extension_name}",
        )
        # %% escapes the % so ConfigParser doesn't try to interpolate %(rev)s
        # at read time — Alembic substitutes at template-render time instead.
        cfg.set_main_option("file_template", "%%(rev)s_%%(slug)s")
        cfg.attributes["extension"] = extension_name
        if self._model_registry is not None:
            # See __init__ docstring: env.py's lazy-loaded Base is a separate
            # SQLAlchemy declarative instance from the one driving commit(),
            # so we hand off the live registry here for env.py to reuse.
            cfg.attributes["model_registry"] = self._model_registry
        return cfg

    def _dispatch_alembic(self, cfg, command, *args, **kwargs):
        """Run an Alembic command via the Python API; translate raises to bool.

        Returns True on success, False on failure (logged). For commands that
        produce structured output (history, current) we still return True/False
        — callers that need the output can inspect logs or query
        ScriptDirectory directly.
        """
        from alembic import command as alembic_command

        fn = getattr(alembic_command, command, None)
        if fn is None:
            logger.error(f"Unknown alembic command: {command}")
            return False
        try:
            fn(cfg, *args, **kwargs)
            return True
        except Exception as e:
            logger.error(f"Alembic command '{command}' failed: {e}", exc_info=True)
            return False

    def run_alembic_command(self, command, *args, extra_env=None, extension=None):
        """Run a core (non-extension) alembic command via the Python API.

        Replaces the prior subprocess-based flow: no os.chdir, no temp
        alembic.ini, no script.py.mako write. The shared script_location at
        src/database/migrations/ holds the single env.py and the materialized
        script.py.mako template.
        """
        if extension:
            return self.run_extension_migration(extension, command, *args)

        versions_dir = self._resolve_core_versions_dir()
        versions_dir.mkdir(parents=True, exist_ok=True)
        cfg = self._make_alembic_config(None, versions_dir)

        if command == "upgrade":
            target = args[0] if args else "head"
            return self._dispatch_alembic(cfg, "upgrade", target)
        if command == "downgrade":
            target = args[0] if args else "-1"
            return self._dispatch_alembic(cfg, "downgrade", target)
        if command == "history":
            return self._dispatch_alembic(cfg, "history")
        if command == "current":
            return self._dispatch_alembic(cfg, "current")
        if command == "revision":
            return self._dispatch_revision(cfg, args, autogenerate=False)
        if command == "--autogenerate" or "--autogenerate" in args:
            return self._dispatch_revision(cfg, args, autogenerate=True)

        logger.error(f"Unsupported alembic command: {command} {args}")
        return False

    def _dispatch_revision(self, cfg, args, autogenerate):
        """Parse the legacy `revision -m <msg> [--autogenerate]` arg shape and
        dispatch to alembic.command.revision."""
        message = None
        auto = autogenerate
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-m", "--message"):
                if i + 1 < len(args):
                    message = args[i + 1]
                    i += 2
                    continue
            if a == "--autogenerate":
                auto = True
            i += 1
        if message is None:
            logger.error("revision command requires --message/-m")
            return False
        from alembic import command as alembic_command

        try:
            alembic_command.revision(cfg, message=message, autogenerate=auto)
            return True
        except Exception as e:
            logger.error(f"Alembic revision failed: {e}", exc_info=True)
            return False

    def _is_first_migration(self, cfg):
        """Whether the version_locations directory has zero base revisions."""
        from alembic.script import ScriptDirectory

        try:
            return len(list(ScriptDirectory.from_config(cfg).get_revisions("base"))) == 0
        except Exception as e:
            logger.debug(f"_is_first_migration: defaulting to True due to {e}")
            return True

    def _ensure_extension_models_loaded(self, extension_name):
        """Make sure the extension's BLL_*.py modules are imported and bound
        to a committed ModelRegistry, so env.py can resolve target_metadata
        for autogenerate runs.

        When commit() drives the migration, self._model_registry is already
        set and we just trigger BLL imports for the extension. When a caller
        invokes us directly (CLI / per-test fixtures), we bootstrap a fresh
        registry: import core BLL files (so FK targets resolve), import the
        extension's BLL files, and commit the registry against a Base bound
        to db_info["url"]. The committed registry is then threaded through
        cfg.attributes by _make_alembic_config.

        Returns True if at least one BLL file exists for the extension.
        """
        from serverframework.lib.Pydantic import ModelRegistry

        ext_path = self.paths["extensions_dir"] / extension_name
        if not ext_path.exists() or not list(ext_path.glob("BLL_*.py")):
            logger.warning(f"No BLL_*.py files found for extension {extension_name}")
            return False

        if self._model_registry is not None:
            ModelRegistry.from_scoped_import(
                file_type="BLL",
                scopes=[f"{self.extensions_dir_name}.{extension_name}"],
            )
            return True

        # Standalone path: bootstrap a registry against an isolated Base.
        from serverframework.database.DatabaseManager import DatabaseManager

        ModelRegistry.from_scoped_import(
            file_type="BLL",
            scopes=[
                "logic",
                f"{self.extensions_dir_name}.{extension_name}",
            ],
        )
        registry = ModelRegistry()
        try:
            db_prefix = self.db_info.get("name", "") if self.db_info else ""
            db_mgr = DatabaseManager(db_prefix=db_prefix)
            registry.database_manager = db_mgr
            db_mgr.Base._model_registry = registry
            registry.commit()
        except Exception as e:
            logger.warning(
                f"Could not commit a standalone registry for {extension_name}: {e}",
                exc_info=True,
            )
            return False
        self._model_registry = registry
        return True

    def run_extension_migration(
        self, extension_name, command, target="head", auto=True
    ):
        """Run an alembic command for a specific extension via the Python API.

        Writes nothing to the extension folder — the only filesystem touch is
        creating <extension>/migrations/versions/ if missing. env.py and
        script.py.mako are taken from the shared script_location.
        """
        ok, versions_dir = self.ensure_extension_versions_directory(extension_name)
        if not ok:
            return False

        if not self._ensure_extension_models_loaded(extension_name):
            return False

        cfg = self._make_alembic_config(extension_name, versions_dir)

        if command == "upgrade":
            return self._dispatch_alembic(cfg, "upgrade", target or "head")
        if command == "downgrade":
            return self._dispatch_alembic(cfg, "downgrade", target or "-1")
        if command == "history":
            return self._dispatch_alembic(cfg, "history")
        if command == "current":
            return self._dispatch_alembic(cfg, "current")
        if command == "revision":
            return self._extension_revision(cfg, extension_name, message=None, auto=auto)

        logger.error(f"Unsupported extension alembic command: {command}")
        return False

    def _extension_revision(self, cfg, extension_name, message, auto):
        """Create a new revision file for an extension, branch-labeling it on
        the first revision so it forms its own line of history independent
        from core."""
        from alembic import command as alembic_command

        kwargs = {"message": message, "autogenerate": auto}
        if self._is_first_migration(cfg):
            kwargs["head"] = "base"
            kwargs["branch_label"] = f"ext_{extension_name}"
        try:
            alembic_command.revision(cfg, **kwargs)
            return True
        except Exception as e:
            logger.error(
                f"Alembic revision for extension {extension_name} failed: {e}",
                exc_info=True,
            )
            return False

    def create_extension_migration(self, extension_name, message, auto=True):
        """Create a new revision for an extension. Same in-process Python API
        path as run_extension_migration's revision branch."""
        ok, versions_dir = self.ensure_extension_versions_directory(extension_name)
        if not ok:
            return False
        if not self._ensure_extension_models_loaded(extension_name):
            return False
        cfg = self._make_alembic_config(extension_name, versions_dir)
        return self._extension_revision(cfg, extension_name, message=message, auto=auto)

    def discover_extension_migration_dirs(self):
        """Walk ``lib.Paths.extensions_dir()`` and list per-extension migration roots.

        Item 62 — when the framework is configured with an out-of-tree
        extensions root (via ``ExtensionRegistry(extensions_path=...)``
        or ``MigrationManager(extensions_path=...)``), this returns the
        ``migrations/`` directories under that root for every extension
        that has BLL files. The framework's core migration root is
        excluded — Alembic's ``script_location`` already points at
        ``database/migrations``.

        Returns a list of ``(extension_name, migrations_dir_path)``
        tuples in deterministic (sorted) order.
        """
        ext_root = Path(self.paths["extensions_dir"])
        out = []
        if not ext_root.exists():
            return out
        for ext_dir in sorted(ext_root.iterdir()):
            if not ext_dir.is_dir() or ext_dir.name.startswith("__"):
                continue
            # Only consider extensions that ship BLL files — those are the
            # ones with database state. Extensions with no BLL_*.py have
            # nothing to migrate.
            if not list(ext_dir.glob("BLL_*.py")):
                continue
            migrations_dir = ext_dir / "migrations"
            if migrations_dir.exists():
                out.append((ext_dir.name, migrations_dir))
        return out

    def _resolve_extension_versions_dir(self, extension_name):
        """Resolve the per-extension version_locations directory.

        In production / non-test paths, this is
            src/extensions/<name>/migrations/versions/
        In test mode with a test_versions_root override, this is
            <test_versions_root>/<name>/versions/
        which keeps the source tree pristine across test runs.

        Falls back to the legacy in-tree test_versions/ when no override is
        set, preserving back-compat with direct CLI invocations of the
        test-mode flow.
        """
        if self.test_mode and self._test_versions_root is not None:
            return self._test_versions_root / extension_name / "versions"
        ext_migrations = self.paths["extensions_dir"] / extension_name / "migrations"
        return ext_migrations / ("test_versions" if self.test_mode else "versions")

    def ensure_extension_versions_directory(self, extension_name):
        """Ensure the per-extension version_locations directory exists.

        Creates only the directories — no __init__.py files anywhere. Alembic
        loads revision files by filesystem path, not via Python import, so
        these directories are not Python packages.
        """
        ext_dir = self.paths["extensions_dir"] / extension_name
        if not ext_dir.exists():
            return False, None

        versions_dir = self._resolve_extension_versions_dir(extension_name)
        versions_dir.mkdir(parents=True, exist_ok=True)
        return True, versions_dir

    def regenerate_migrations(
        self, extension_name=None, all_extensions=False, message=None
    ):
        """Delete existing migrations and regenerate from scratch"""
        logger.debug(f"Starting regeneration of migrations")

        if message is None:
            message = "initial schema"

        logger.debug(f"Using message: {message}")

        # NUKE THE DATABASE - We're starting from scratch.
        # Use db_info["file_path"] so test/parametrized DBs delete the right file.
        # Postgres has no file_path; skip the delete in that case (tables are
        # dropped by the migration, not the file).
        file_path = self.db_info.get("file_path") if self.db_info else None
        if file_path:
            db_path = Path(file_path)
            logger.debug(f"Deleting existing database to start fresh: {db_path}")
            if db_path.exists():
                try:
                    db_path.unlink()
                    logger.debug(f"Successfully deleted database: {db_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete database {db_path}: {e}")
            else:
                logger.debug(f"Database file {db_path} does not exist, skipping deletion")
        else:
            logger.debug("No db_info file_path set; skipping pre-regenerate DB delete")

        # Handle core migrations first
        if not extension_name or all_extensions:
            logger.debug("Regenerating core migrations")

            # 1. Clear core migrations directory
            versions_dir = self._resolve_core_versions_dir()
            versions_dir.mkdir(parents=True, exist_ok=True)

            # Count and delete migration files
            migration_files = list(versions_dir.glob("*.py"))
            deleted_count = 0
            for f in migration_files:
                if f.name == "__init__.py":
                    continue
                try:
                    f.unlink()
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete {f}: {e}")

            logger.debug(f"Deleted {deleted_count} core migration files")

            # 2. Create a new revision
            success = self.run_alembic_command(
                "revision", "--autogenerate", "-m", message
            )
            if not success:
                logger.error("Failed to regenerate core migrations")
                return False

            logger.debug("Successfully regenerated core migrations")

        # Handle extension migrations if needed
        if extension_name or all_extensions:
            extensions_to_process = []

            if extension_name:
                extensions_to_process.append(extension_name)
            elif all_extensions:
                # Refresh configured extensions to pick up any newly added to APP_EXTENSIONS
                self.configured_extensions = self._get_configured_extensions()
                extensions_to_process = self.configured_extensions

            for ext_name in extensions_to_process:
                logger.debug(f"Regenerating migrations for extension: {ext_name}")

                # 1. Ensure extension directory exists
                success, versions_dir = self.ensure_extension_versions_directory(
                    ext_name
                )
                if not success:
                    logger.error(f"Failed to ensure versions directory for {ext_name}")
                    continue

                # 2. Clear existing migration files
                migration_files = list(versions_dir.glob("*.py"))
                deleted_count = 0
                for f in migration_files:
                    if f.name == "__init__.py":
                        continue
                    try:
                        f.unlink()
                        deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete {f}: {e}")

                logger.debug(f"Deleted {deleted_count} migration files for {ext_name}")

                # 3. Create a new revision for the extension
                success = self.create_extension_migration(ext_name, message, auto=True)
                if not success:
                    logger.error(f"Failed to regenerate migrations for {ext_name}")
                    return False

                logger.debug(f"Successfully regenerated migrations for {ext_name}")

            # Phase 4 eliminated per-extension temporary files; nothing to clean.

        return True

    def create_extension_directory(self, extension_name):
        """Create the extension directory structure"""
        logger.debug(f"Creating extension directory structure for {extension_name}")

        # Define the directory structure using configurable path
        ext_dir = self.paths["extensions_dir"] / extension_name

        # Create main extension directory
        ext_dir.mkdir(parents=True, exist_ok=True)

        # Create __init__.py for all modes
        init_file = ext_dir / "__init__.py"
        if not init_file.exists():
            self.write_file(init_file, f"# Extension: {extension_name}\n")

        # Create migrations directory
        migrations_dir = ext_dir / "migrations"
        migrations_dir.mkdir(exist_ok=True)

        # Create versions directory (use appropriate name based on test mode)
        versions_dir_name = self._get_versions_directory_name()
        versions_dir = migrations_dir / versions_dir_name
        versions_dir.mkdir(exist_ok=True)

        # Create __init__.py in versions directory only (not migrations directory in test mode)
        if not self.test_mode:
            self.write_file(migrations_dir / "__init__.py", "")
        self.write_file(versions_dir / "__init__.py", "")

        return ext_dir

    def create_db_file(self, extension_dir, extension_name):
        """Create a sample BLL_*.py file with table definitions"""
        logger.debug(f"Creating sample BLL model file for extension {extension_name}")

        # Format extension name for class name (capitalize first letter)
        class_name = stringcase.pascalcase(extension_name)

        # Default table name based on extension name
        table_name = f"{extension_name}_items"

        # Create the model file
        model_file = extension_dir / f"BLL_{class_name}.py"

        # Get the template file
        template_file = self.template_dir / "db_model.py.mako"

        # Check if template exists, fall back to a string template if not
        if template_file.exists():
            with open(template_file, "r") as f:
                template_content = f.read()

            # Replace template variables
            from string import Template

            template = Template(template_content)
            model_content = template.substitute(
                class_name=class_name,
                table_name=table_name,
                extension_name=extension_name,
            )
            logger.debug(f"Using template from {template_file} to create model")
        else:
            # Fallback to string template if the file doesn't exist yet
            logger.warning(
                f"Template file {template_file} not found, using fallback template"
            )
            model_content = f"""from database.DatabaseManager import DatabaseManager
from sqlalchemy import Column, Integer, String

# Get Base from database manager
db_manager = self.db_manager
Base = db_manager.Base

class {class_name}(Base):
    __tablename__ = "{table_name}"
    
    # Mark as extending existing table if needed
    __table_args__ = {{"extend_existing": True, "info": {{"extension": "{extension_name}"}}}}

    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    description = Column(String(200))
"""

        # Write the model file
        self.write_file(model_file, model_content)

        return model_file

    def create_extension(self, extension_name, skip_model=False, skip_migrate=False):
        """Create a new extension with migrations"""
        logger.debug(f"Creating new extension: {extension_name}")

        try:
            # 1. Create extension directory structure
            ext_dir = self.create_extension_directory(extension_name)

            # 2. Create DB model file if not skipped
            if not skip_model:
                model_file = self.create_db_file(ext_dir, extension_name)
                logger.debug(f"Created model file: {model_file}")

            # 3. Update the APP_EXTENSIONS environment variable if needed
            os.environ["APP_EXTENSIONS"] = self._ensure_extension_in_env_var(
                extension_name
            )
            # Reload configured extensions
            self.configured_extensions = self._get_configured_extensions()

            # 4. Create and apply migration if not skipped
            if (
                not skip_migrate and not skip_model
            ):  # Only create migration if model was created
                logger.debug(
                    f"Creating initial migration for extension {extension_name}"
                )

                # First ensure directories exist
                success, versions_dir = self.ensure_extension_versions_directory(
                    extension_name
                )
                if not success:
                    logger.error(
                        f"Failed to ensure versions directory for {extension_name}"
                    )
                    return False

                # Create migration
                migration_success = self.create_extension_migration(
                    extension_name, "Initial migration", auto=True
                )
                if not migration_success:
                    logger.warning(
                        f"Failed to create initial migration for {extension_name}"
                    )

                # Apply migration
                logger.debug(f"Applying migration for extension {extension_name}")
                upgrade_success = self.run_extension_migration(
                    extension_name, "upgrade", "head"
                )
                if not upgrade_success:
                    logger.warning(f"Failed to apply migration for {extension_name}")

            logger.debug(f"Successfully created extension: {extension_name}")
            return True

        except Exception as e:
            logger.error(
                f"Error creating extension {extension_name}: {e}", exc_info=True
            )
            return False
        # Phase 4 eliminated per-extension temporary files; nothing to clean
        # in a finally block.

    def _ensure_extension_in_env_var(self, extension_name):
        """Ensure an extension is in the APP_EXTENSIONS environment variable"""
        extensions = self._parse_csv_env_var("APP_EXTENSIONS", [])
        if extension_name not in extensions:
            extensions.append(extension_name)

        return ",".join(extensions)

    def debug_environment(self):
        """Show debug information about the environment"""
        logger.debug("=== ENVIRONMENT DEBUG INFORMATION ===")

        # Environment variables
        logger.debug("--- ENVIRONMENT VARIABLES ---")
        env_vars_to_show = [
            "APP_NAME",
            "DATABASE_TYPE",
            "DATABASE_NAME",
            "DATABASE_HOST",
            "DATABASE_PORT",
            "APP_EXTENSIONS",
            "PYTHONPATH",
        ]
        for var in env_vars_to_show:
            from serverframework.lib.Environment import env

            logger.debug(f"{var}: {env(var)}")

        # Database configuration
        logger.info(f"Database Type: {self.db_info['type']}")
        logger.info(f"Database Name: {self.db_info['name']}")
        # Mask sensitive parts of the URL
        url = self.db_info["url"]
        if "://" in url and "@" in url:
            parts = url.split("@")
            auth_part = parts[0].split("://")[1]
            if ":" in auth_part:
                masked_url = url.replace(auth_part, auth_part.split(":")[0] + ":****")
                logger.debug(f"Database URL: {masked_url}")
            else:
                logger.debug(f"Database URL: {url}")
        else:
            logger.debug(f"Database URL: {url}")

        # Paths
        logger.debug("--- PATHS ---")
        for name, path in self.paths.items():
            logger.debug(f"{name}: {path}")

        # Alembic configuration is built in-memory via _make_alembic_config —
        # there is no on-disk alembic.ini to inspect.
        logger.debug("--- ALEMBIC CONFIG ---")
        logger.debug("Alembic config: in-memory (no on-disk alembic.ini)")

        # Extensions
        logger.debug("--- EXTENSIONS ---")
        logger.debug(
            f"Configured extensions (from APP_EXTENSIONS): {self.configured_extensions}"
        )

        # Check for extension directories and models
        for ext_name in self.configured_extensions:
            ext_dir = self.paths["extensions_dir"] / ext_name
            if ext_dir.exists():
                logger.debug(f"Extension directory {ext_name}: Exists")
                # Check for BLL models
                db_files = list(ext_dir.glob("BLL_*.py"))
                if db_files:
                    logger.debug(
                        f"Extension {ext_name} BLL models: {[f.name for f in db_files]}"
                    )
                else:
                    logger.debug(f"Extension {ext_name} DB models: None found")

                # Check for migrations
                migrations_dir = ext_dir / "migrations"
                if migrations_dir.exists():
                    migration_files = list(migrations_dir.glob("*.py"))
                    migration_count = len(
                        [f for f in migration_files if f.name != "__init__.py"]
                    )
                    logger.debug(
                        f"Extension {ext_name} migrations: {migration_count} found"
                    )
                else:
                    logger.debug(
                        f"Extension {ext_name} migrations: No migrations directory"
                    )
            else:
                logger.debug(f"Extension directory {ext_name}: Not found")

        return True

    def run_all_migrations(self, command, target="head", extensions=None):
        """Run migrations for core and all extensions"""
        logger.debug(
            f"Database environment: TYPE={self.db_info['type']}, NAME={self.db_info['name']}"
        )

        # Use a proper file lock to ensure only one process runs migrations at a time
        # This prevents race conditions when multiple pytest workers try to run migrations simultaneously
        # Make the lock file database-specific so different workers with different databases don't block each other
        db_name_safe = (
            self.db_info["name"].replace("/", "_").replace("\\", "_").replace(":", "_")
        )
        lock_file_path = self.paths["src_dir"] / f".migration.{db_name_safe}.lock"
        lock = FileLock(lock_file_path, timeout=120)  # 120 seconds timeout

        try:
            worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
            logger.debug(
                f"[Worker {worker_id}] Attempting to acquire migration lock at {lock_file_path}"
            )
            lock.acquire()
            logger.debug(f"[Worker {worker_id}] Migration lock acquired successfully")
        except Timeout:
            worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
            logger.error(
                f"[Worker {worker_id}] Timeout waiting for migration lock at {lock_file_path} after 120s - "
                f"db={self.db_info['name']}"
            )
            return False

        try:
            # Refresh configured extensions to pick up any environment changes
            self.configured_extensions = extensions or self._get_configured_extensions()
            try:
                self.configured_extensions = self._compute_migration_order(
                    list(self.configured_extensions)
                )
            except RuntimeError:
                # Cycle errors must surface to the operator — re-raise.
                raise
            logger.debug(
                f"Running migrations for extensions (toposorted): "
                f"{self.configured_extensions}"
            )
            logger.debug(
                f"[Worker {worker_id}] Running {command} for core migrations on db={self.db_info['name']}"
            )
            core_result = self.run_alembic_command(command, target)

            if not core_result:
                logger.error(
                    f"[Worker {worker_id}] Core migrations {command} failed for db={self.db_info['name']}"
                )
                return False

            extension_migrations = []

            # First, check which extensions actually have BLL models
            for ext_name in self.configured_extensions:
                extension_dir = self.paths["extensions_dir"] / ext_name
                db_model_files = list(extension_dir.glob("BLL_*.py"))

                if not db_model_files:
                    logger.debug(
                        f"Skipping extension '{ext_name}' - no BLL_*.py files found"
                    )
                    continue

                logger.debug(
                    f"Found BLL models for extension '{ext_name}': {[f.name for f in db_model_files]}"
                )

                # Resolve where this extension's revision files live (honors
                # test_versions_root override).
                versions_dir = self._resolve_extension_versions_dir(ext_name)

                if versions_dir.exists():
                    extension_migrations.append((ext_name, versions_dir))
                else:
                    # Directory structure needs to be created
                    success, dir_path = self.ensure_extension_versions_directory(
                        ext_name
                    )
                    if success:
                        extension_migrations.append((ext_name, dir_path))

            failed_extensions = []
            for extension_name, versions_dir in extension_migrations:
                logger.debug(f"Running migrations for extension: {extension_name}")

                # Check if the versions directory actually exists and has migration files
                if not versions_dir.exists():
                    # No versions directory exists - need to create initial migration for upgrade
                    if command == "upgrade":
                        extension_dir = self.paths["extensions_dir"] / extension_name
                        db_model_files = list(extension_dir.glob("BLL_*.py"))

                        if db_model_files:
                            logger.debug(
                                f"Creating initial migration for extension {extension_name}"
                            )
                            created = self.create_extension_migration(
                                extension_name, "Initial migration", auto=True
                            )
                            if not created:
                                logger.info(
                                    f"Auto-migration unavailable for {extension_name}; "
                                    f"falling back to create_all for initial tables."
                                )
                                try:
                                    if self._model_registry and hasattr(
                                        self._model_registry, "DB"
                                    ):
                                        engine = self._model_registry.DB.manager._setup_engine
                                        if engine is not None:
                                            self._model_registry.DB.manager.Base.metadata.create_all(
                                                bind=engine
                                            )
                                            logger.info(
                                                f"Created tables for {extension_name} via create_all"
                                            )
                                            continue
                                except Exception as fallback_err:
                                    logger.warning(
                                        f"create_all fallback also failed for {extension_name}: {fallback_err}"
                                    )
                                failed_extensions.append(extension_name)
                                continue
                        else:
                            logger.debug(
                                f"Skipping migration for extension {extension_name} as no BLL_*.py files found."
                            )
                            continue
                    else:
                        # For other commands like downgrade/history, skip if no versions exist
                        logger.debug(
                            f"Skipping '{command}' for extension {extension_name} as no migrations exist."
                        )
                        continue

                # Re-check if versions dir exists after potential creation attempt
                if not versions_dir.exists():
                    logger.warning(
                        f"Versions directory {versions_dir} still not found for extension {extension_name}, skipping."
                    )
                    failed_extensions.append(extension_name)
                    continue

                # Check if there are any migration files in the versions directory
                migration_files = list(versions_dir.glob("*.py"))
                migration_files = [
                    f for f in migration_files if f.name != "__init__.py"
                ]

                if not migration_files:
                    # No migration files found - create one if it's an upgrade command
                    if command == "upgrade":
                        logger.debug(
                            f"No migration files found for extension {extension_name}, creating initial migration"
                        )
                        created = self.create_extension_migration(
                            extension_name, "Initial migration", auto=True
                        )
                        if not created:
                            logger.info(
                                f"Auto-migration unavailable for {extension_name}; "
                                f"falling back to create_all."
                            )
                            try:
                                if self._model_registry and hasattr(
                                    self._model_registry, "DB"
                                ):
                                    engine = self._model_registry.DB.manager._setup_engine
                                    if engine is not None:
                                        self._model_registry.DB.manager.Base.metadata.create_all(
                                            bind=engine
                                        )
                                        logger.info(
                                            f"Created tables for {extension_name} via create_all"
                                        )
                                        continue
                            except Exception as fallback_err:
                                logger.warning(
                                    f"create_all fallback also failed for {extension_name}: {fallback_err}"
                                )
                            failed_extensions.append(extension_name)
                            continue
                    else:
                        logger.debug(
                            f"No migration files found for extension {extension_name}, skipping {command}."
                        )
                        continue

                # Now run the migration command for this extension
                # For upgrade --all, we want to ensure all pending migrations are applied
                logger.debug(
                    f"About to run extension migration: extension={extension_name}, command={command}, target={target}"
                )
                success = self.run_extension_migration(extension_name, command, target)
                if not success:
                    logger.error(
                        f"Migration {command} failed for extension {extension_name}"
                    )
                    failed_extensions.append(extension_name)
                else:
                    logger.debug(
                        f"Migration {command} completed successfully for extension {extension_name}"
                    )

            if failed_extensions:
                logger.error(
                    f"Migration failed for extensions: {', '.join(failed_extensions)}"
                )
                return False

            return True
        finally:
            # Always release the lock, even if an exception occurred
            lock.release()
            logger.debug("Migration lock released")

    def _normalize_path(self, path):
        """Normalize a path to avoid duplicated segments like /path/src/src/..."""
        path_str = str(path)
        components = path_str.split(os.sep)

        result = []
        for i, comp in enumerate(components):
            if not comp:
                continue
            if i > 0 and comp == components[i - 1]:
                continue
            result.append(comp)

        normalized = os.sep.join(result)
        if path_str.startswith(os.sep) and not normalized.startswith(os.sep):
            normalized = os.sep + normalized

        return normalized

    def _parse_csv_env_var(self, env_var_name, default=None):
        """Parse a comma-separated environment variable into a list of strings."""
        from serverframework.lib.Environment import env

        value = env(env_var_name)
        if not value or value.strip() == "":
            return default or []
        return [item.strip() for item in value.split(",") if item.strip()]

def main():
    parser = argparse.ArgumentParser(description="Unified database migration tool")
    subparsers = parser.add_subparsers(dest="command", help="Migration command")

    upgrade_parser = subparsers.add_parser("upgrade", help="Upgrade the database")
    upgrade_parser.add_argument(
        "--all",
        action="store_true",
        help="Run migrations for core and all extensions",
    )
    upgrade_parser.add_argument(
        "--extension", help="Run migrations for a specific extension"
    )
    upgrade_parser.add_argument(
        "--target", default="head", help="Migration target (default: head)"
    )

    downgrade_parser = subparsers.add_parser("downgrade", help="Downgrade the database")
    downgrade_parser.add_argument(
        "--all", action="store_true", help="Run migrations for core and all extensions"
    )
    downgrade_parser.add_argument(
        "--extension", help="Run migrations for a specific extension"
    )
    downgrade_parser.add_argument(
        "--target", default="-1", help="Migration target (default: -1)"
    )

    revision_parser = subparsers.add_parser("revision", help="Create a new revision")
    revision_parser.add_argument(
        "--extension", help="Create a revision for a specific extension"
    )
    revision_parser.add_argument("--message", "-m", help="Revision message")
    revision_parser.add_argument(
        "--no-autogenerate",
        action="store_false",
        dest="autogenerate",
        help="Create an empty migration file without auto-generating content.",
    )
    revision_parser.set_defaults(autogenerate=True)

    history_parser = subparsers.add_parser(
        "history", help="Show migration version history"
    )
    history_parser.add_argument(
        "--extension", help="Show history for a specific extension"
    )

    current_parser = subparsers.add_parser(
        "current", help="Show current migration version"
    )
    current_parser.add_argument(
        "--extension", help="Show current version for a specific extension"
    )

    create_parser = subparsers.add_parser(
        "create", help="Create a new extension with migrations"
    )
    create_parser.add_argument("extension", help="Name of the extension to create")
    create_parser.add_argument(
        "--skip-model", action="store_true", help="Skip creating sample model"
    )
    create_parser.add_argument(
        "--skip-migrate",
        action="store_true",
        help="Skip migration creation and application",
    )

    debug_parser = subparsers.add_parser(
        "debug", help="Show detailed debug information"
    )

    # Audit table ownership (Item 24)
    subparsers.add_parser(
        "audit-ownership",
        help=(
            "List every table and its owning extension (or 'core'). "
            "Field-injection extenders are listed in the third column."
        ),
    )

    # Add separate regenerate command
    regenerate_parser = subparsers.add_parser(
        "regenerate", help="Delete all existing migrations and regenerate"
    )
    regenerate_parser.add_argument(
        "--extension", help="Regenerate migrations for a specific extension"
    )
    regenerate_parser.add_argument(
        "--all", action="store_true", help="Regenerate all extensions after core"
    )
    regenerate_parser.add_argument(
        "--message",
        "-m",
        default="initial schema",
        help="Revision message (default: 'initial schema')",
    )

    args = parser.parse_args()

    # Create our migration manager
    manager = MigrationManager()

    # Sweep stale test artifacts from prior runs.
    manager.cleanup_test_artifacts()

    success = False
    try:
        if args.command in ["upgrade", "downgrade"]:
            if args.all:
                success = manager.run_all_migrations(args.command, args.target)
            elif args.extension:
                success = manager.run_extension_migration(
                    args.extension, args.command, args.target
                )
            else:
                success = manager.run_alembic_command(args.command, args.target)

        elif args.command == "revision":
            if not args.message:
                logger.error("--message is required for new revisions")
                success = False
            elif args.extension:
                success = manager.create_extension_migration(
                    args.extension, args.message, args.autogenerate
                )
            else:
                cmd = ["revision"]
                if args.autogenerate:
                    cmd.append("--autogenerate")
                cmd.extend(["-m", args.message])
                success = manager.run_alembic_command(*cmd)

        elif args.command == "history":
            if args.extension:
                success = manager.run_extension_migration(args.extension, "history")
            else:
                success = manager.run_alembic_command("history")

        elif args.command == "current":
            if args.extension:
                success = manager.run_extension_migration(args.extension, "current")
            else:
                success = manager.run_alembic_command("current")

        elif args.command == "create":
            success = manager.create_extension(
                args.extension,
                skip_model=args.skip_model,
                skip_migrate=args.skip_migrate,
            )

        elif args.command == "audit-ownership":
            success = manager.audit_table_ownership()

        elif args.command == "debug":
            manager.debug_environment()
            success = True

        elif args.command == "regenerate":
            success = manager.regenerate_migrations(
                extension_name=args.extension,
                all_extensions=args.all,
                message=args.message,
            )

        else:
            parser.print_help()
            success = False

    finally:
        try:
            manager.cleanup_test_artifacts()
        except Exception as e:
            logger.warning(f"Error during final cleanup: {e}", exc_info=True)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
