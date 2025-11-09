#!/usr/bin/env python
"""
Unified database migration management tool for core and extension migrations.
Handles initialization, creation, applying, and management of migrations.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import time

import stringcase

# Get the current file's directory
current_file_dir = Path(__file__).resolve().parent
template_dir = current_file_dir / "template"
src_path = Path(__file__).resolve().parent.parent.parent

# Add project root and src directories to path
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from lib.Logging import logger


# Define default templates as dictionaries
def get_script_py_mako_template():
    """Get the script.py.mako template from file or fallback to default"""
    try:
        script_py_path = template_dir / "script.py.mako"
        if script_py_path.exists():
            with open(script_py_path, "r") as f:
                return f.read()
    except Exception as e:
        logger.warning(f"Error loading script.py.mako template: {e}")

    # Default template
    return '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema."""
    ${downgrades if downgrades else "pass"}
'''


# Standard INI file sections and options for alembic.ini
def get_default_alembic_ini_dict(test_mode=False, database_dir="database"):
    """Get default alembic.ini configuration as a dictionary"""
    versions_dir_name = "test_versions" if test_mode else "versions"

    return {
        "alembic": {
            "script_location": "database/migrations",  # Always hardcoded for infrastructure
            "file_template": "%%(rev)s_%%(slug)s",
            "prepend_sys_path": ".",
            "version_locations": f"%(here)s/{database_dir}/migrations/{versions_dir_name}",  # Use parameterized database_dir
            "version_path_separator": "os",
            "sqlalchemy.url": "sqlite:///database.db",
            "version_table": "alembic_version",
        },
        "loggers": {"keys": "root,sqlalchemy,alembic"},
        "handlers": {"keys": "console"},
        "formatters": {"keys": "generic"},
        "logger_root": {"level": "INFO", "handlers": "console", "qualname": ""},
        "logger_sqlalchemy": {
            "level": "INFO",
            "handlers": "console",
            "qualname": "sqlalchemy.engine",
        },
        "logger_alembic": {
            "level": "INFO",
            "handlers": "console",
            "qualname": "alembic",
        },
        "handler_console": {
            "class": "StreamHandler",
            "args": "(sys.stderr,)",
            "level": "NOTSET",
            "formatter": "generic",
        },
        "formatter_generic": {
            "format": "%(levelname)-5.5s [%(name)s] %(message)s",
            "datefmt": "%H:%M:%S",
        },
    }


def get_extension_alembic_ini_dict(
    extension_name, ext_migrations_dir, db_url, test_mode=False, database_dir="database"
):
    """Get extension-specific alembic.ini configuration as a dictionary"""
    config = get_default_alembic_ini_dict(test_mode, database_dir)

    # Determine the correct versions directory name
    versions_dir_name = "test_versions" if test_mode else "versions"

    # Override with extension-specific settings
    config["alembic"].update(
        {
            "script_location": str(ext_migrations_dir),
            "sqlalchemy.url": db_url,
            "version_table": f"alembic_version_{extension_name}",
            "branch_label": f"ext_{extension_name}",
            "version_locations": f"{ext_migrations_dir}/{versions_dir_name}",  # Extension-specific path
        }
    )

    return config


def dict_to_ini(config_dict):
    """Convert a nested dictionary to INI file format"""
    lines = []

    for section, options in config_dict.items():
        lines.append(f"[{section}]")
        for key, value in options.items():
            lines.append(f"{key} = {value}")
        lines.append("")  # Add an empty line between sections

    return "\n".join(lines)


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
            full_module_name = f"database.{module_name}"
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
    def env_is_table_owned_by_extension(
        table, extension_name, extensions_dir="extensions"
    ):
        """
        Check if a table belongs to an extension based on the location of its BLL_*.py file.
        Returns True if the table's class is defined in a file located in the extension's directory.

        Enhanced to handle model extensions where extensions can modify core tables.
        """
        # Get the table class
        table_class = getattr(table, "class_", None)
        if not table_class:
            logger.debug(f"Table {table} has no class_ attribute")

            # Check if table has extension metadata set by env.py
            if hasattr(table, "info") and "extension" in table.info:
                table_extension = table.info["extension"]
                is_owned = table_extension == extension_name
                logger.debug(
                    f"Table {table} has extension metadata: {table_extension}, owned by {extension_name}? {is_owned}"
                )
                return is_owned
            return False

        # Check module name first - this handles BLL models
        module_name = table_class.__module__
        if module_name:
            # Check if module name matches extension pattern for BLL files
            ext_module_pattern = f"{extensions_dir}.{extension_name}."
            if module_name.startswith(ext_module_pattern):
                logger.debug(
                    f"Table {table} belongs to extension {extension_name} by module name: {module_name}"
                )
                return True

        # NEW: Check for model extensions - core tables that have been extended by this extension
        if MigrationManager._is_core_table_extended_by_extension(table, extension_name):
            logger.debug(
                f"Table {table} is a core table extended by extension {extension_name}"
            )
            return True

        # Check if table has extension metadata set by env.py during model registration
        if hasattr(table, "info") and "extension" in table.info:
            table_extension = table.info["extension"]
            is_owned = table_extension == extension_name
            logger.debug(
                f"Table {table} has extension metadata: {table_extension}, owned by {extension_name}? {is_owned}"
            )
            return is_owned

        # Check if file is in the extension's directory (fallback for non-patched modules)
        is_in_extension_dir = False
        try:
            module = sys.modules.get(module_name)
            if not module or not hasattr(module, "__file__"):
                logger.debug(f"Module for table {table} has no __file__ attribute")
                return False

            file_path = module.__file__
            if not file_path:
                logger.debug(f"File path for table {table} is empty")
                return False

            # Check if file path contains 'extensions/<extension_name>' using configurable directory name
            # For BLL files, we look for BLL_*.py files in extension directories
            ext_path = f"{extensions_dir}{os.path.sep}{extension_name}"
            is_in_extension_dir = ext_path in file_path.replace("/", os.path.sep)

            # Also check specifically for BLL files
            if is_in_extension_dir and "BLL_" in file_path:
                logger.debug(
                    f"Table {table} file path: {file_path}, found BLL file in extension {extension_name}"
                )
                return True

            logger.debug(
                f"Table {table} file path: {file_path}, looking for: {ext_path}, found: {is_in_extension_dir}"
            )
        except Exception as e:
            logger.debug(f"Error checking file path: {e}")
            return False

        # If the table is not in the extension directory, it doesn't belong to this extension
        if not is_in_extension_dir:
            logger.debug(
                f"Table {table} is not in extension directory {extension_name}"
            )
            return False

        # Table is in extension directory - include it regardless of extend_existing
        # The extend_existing flag is for SQLAlchemy's table creation behavior,
        # not for determining migration ownership
        logger.debug(f"Table {table} belongs to extension {extension_name}")
        return True

    @staticmethod
    def _is_core_table_extended_by_extension(table, extension_name):
        """
        Check if a core table has been extended by the specified extension.

        This checks the model extension registry to see if any extensions from
        the specified extension have been applied to the model that corresponds
        to this table.

        Args:
            table: SQLAlchemy table object
            extension_name: Name of the extension to check

        Returns:
            bool: True if the table's model has been extended by this extension
        """
        try:
            from lib.Pydantic2SQLAlchemy import get_applied_extensions

            # Get the table class
            table_class = getattr(table, "class_", None)
            if not table_class:
                return False

            # Get applied extensions registry
            applied_extensions = get_applied_extensions()

            # Look for the target model in the applied extensions
            # The table class might be the SQLAlchemy model, we need to find the corresponding Pydantic model
            target_model_key = None

            # Try to find the Pydantic model that corresponds to this SQLAlchemy table
            # This is a bit tricky because we need to reverse-lookup from SQLAlchemy to Pydantic

            # Strategy 1: Check if the table class has a reference to the Pydantic model
            if hasattr(table_class, "_pydantic_model"):
                pydantic_model = table_class._pydantic_model
                target_model_key = (
                    f"{pydantic_model.__module__}.{pydantic_model.__name__}"
                )

            # Strategy 2: Look for a model with similar naming in the applied extensions
            if not target_model_key:
                table_name = getattr(table, "name", "")
                class_name = getattr(table_class, "__name__", "")

                # Try to find a matching model in applied extensions
                for target_key in applied_extensions.keys():
                    if (
                        class_name in target_key
                        or stringcase.alphanumcase(table_name).lower()
                        in target_key.lower()
                    ):
                        target_model_key = target_key
                        break

            # Strategy 3: Check by module name patterns
            if not target_model_key and table_class:
                module_name = table_class.__module__
                class_name = table_class.__name__

                # For core models, try common patterns
                if module_name and (
                    "logic.BLL_" in module_name or "database.DB_" in module_name
                ):
                    # Try to construct the likely Pydantic model key
                    if "logic.BLL_" in module_name:
                        # BLL model - the table class might be named differently
                        # Look for models that end with "Model"
                        for target_key in applied_extensions.keys():
                            if (
                                target_key.startswith("logic.BLL_")
                                and "Model" in target_key
                            ):
                                # Check if this could be related to our table
                                model_part = target_key.split(".")[-1]  # Get class name
                                if (
                                    model_part.replace("Model", "").lower()
                                    in class_name.lower()
                                    or class_name.replace("DB", "").lower()
                                    in model_part.lower()
                                ):
                                    target_model_key = target_key
                                    break

            if not target_model_key:
                logger.debug(f"Could not find Pydantic model for table {table.name}")
                return False

            # Check if this target model has extensions from our extension
            if target_model_key in applied_extensions:
                extension_list = applied_extensions[target_model_key]

                # Check if any of the applied extensions are from our extension
                for extension_key in extension_list:
                    if f"extensions.{extension_name}." in extension_key:
                        logger.debug(
                            f"Found model extension {extension_key} for table {table.name} "
                            f"from extension {extension_name}"
                        )
                        return True

            return False

        except Exception as e:
            logger.debug(f"Error checking model extensions for table {table.name}: {e}")
            return False

    @staticmethod
    def env_include_object(object, name, type_, reflected, compare_to, base=None):
        """Filters objects for inclusion in migrations."""
        # Only apply filtering to tables
        if type_ != "table":
            return True

        # Get the current extension context
        from lib.Environment import env

        extension_name = env("ALEMBIC_EXTENSION")
        extensions_dir = MigrationManager._extensions_dir_name

        # Log which objects we're processing with standard log level
        logger.debug(
            f"Processing object: {name}, type: {type_}, extension context: {extension_name}"
        )

        # For extension migrations, we need enhanced detection
        if extension_name:
            # For extension migrations only include tables from this extension
            is_owned = MigrationManager.env_is_table_owned_by_extension(
                object, extension_name, extensions_dir
            )
            should_include = is_owned
            logger.debug(
                f"Table {name} owned by {extension_name}? {is_owned} => include: {should_include}"
            )
            return should_include
        else:
            # For core migrations, include all non-extension tables
            for app_ext in env("APP_EXTENSIONS").split(","):
                if app_ext.strip():
                    # If owned by any extension, exclude from core migrations
                    if MigrationManager.env_is_table_owned_by_extension(
                        object, app_ext.strip(), extensions_dir
                    ):
                        logger.debug(
                            f"Table {name} owned by extension {app_ext} => excluding from core"
                        )
                        return False

            # Include in core if not claimed by any extension
            logger.debug(
                f"Table {name} not owned by any extension => including in core"
            )
            return True

    @staticmethod
    def env_setup_alembic_config(config):
        """Configures Alembic settings consistently."""
        # Set the version table name (can be customized for extensions)
        from lib.Environment import env

        extension_name = env("ALEMBIC_EXTENSION")

        version_table = "alembic_version"
        if extension_name:
            version_table = f"alembic_version_{extension_name}"

        return version_table

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

    @staticmethod
    def env_parse_csv_env_var(env_var_name, default=None):
        """Parse a comma-separated environment variable into a list of strings."""
        from lib.Environment import env

        value = env(env_var_name)
        if not value or value.strip() == "":
            return default or []
        return [item.strip() for item in value.split(",") if item.strip()]

    def __init__(
        self,
        test_mode=False,
        custom_db_info=None,
        extensions_dir="extensions",
        database_dir="database",
    ):
        """Initialize the migration manager.

        Args:
            test_mode: Whether to run in test mode
            custom_db_info: Custom database configuration
            extensions_dir: Directory name for extensions (relative to src_dir), defaults to "extensions"
            database_dir: Directory name for database (relative to src_dir), defaults to "database"
        """
        # Store directory configuration
        self.extensions_dir_name = extensions_dir
        self.database_dir_name = database_dir

        # Set class variable for static methods to access
        MigrationManager._extensions_dir_name = extensions_dir

        self.paths = self._setup_python_path()
        self.current_extension = None
        self.configured_extensions = self._get_configured_extensions()

        # Set test mode from parameter (no auto-detection)
        self.test_mode = test_mode

        # Initialize database manager
        self._db_manager = None

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

        self.alembic_ini_path = self._find_alembic_ini()
        self.template_dir = current_file_dir / "template"

        if self.test_mode:
            logger.debug("Migration manager initialized in TEST MODE")
        else:
            logger.debug("Migration manager initialized in PRODUCTION MODE")

        logger.debug(f"Using extensions directory: {extensions_dir}")
        logger.debug(f"Using database directory: {database_dir}")

        # Track temporary files created for extensions so they can be cleaned up reliably
        self._extension_temp_inis = {}

    @property
    def db_manager(self):
        """Get or create the database manager instance."""
        if self._db_manager is None:
            from database.DatabaseManager import DatabaseManager

            self._db_manager = DatabaseManager()
            self._db_manager.init_engine_config()
        return self._db_manager

    def _get_versions_directory_name(self):
        """Get the appropriate versions directory name based on test mode."""
        return "test_versions" if self.test_mode else "versions"

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
        """Set up Python path for imports using configurable directory names."""
        # Get the current migrations directory (where this file is located)
        migrations_dir = current_file_dir

        # Always use standard database/migrations structure for migration infrastructure
        database_dir = migrations_dir.parent  # migrations -> database
        src_dir = database_dir.parent  # database -> src
        root_dir = src_dir.parent  # src -> root

        # Extensions directory uses configurable name for DB model discovery
        if hasattr(self, "extensions_dir_name"):
            extensions_dir = src_dir / self.extensions_dir_name
        else:
            extensions_dir = src_dir / "extensions"

        for path in [str(root_dir), str(src_dir)]:
            if path not in sys.path:
                sys.path.insert(0, path)

        return {
            "migrations_dir": migrations_dir,
            "database_dir": database_dir,
            "src_dir": src_dir,
            "root_dir": root_dir,
            "extensions_dir": extensions_dir,  # Uses configurable directory name
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

    def _find_alembic_ini(self):
        """Find the alembic.ini file in various possible locations or return a suitable path for creation"""
        from lib.Environment import env

        # Look for alembic.ini in src directory and other locations
        potential_paths = [
            self.paths["src_dir"] / "alembic.ini",  # /src/alembic.ini
            Path("alembic.ini"),  # Current directory
            self.paths["root_dir"] / "alembic.ini",  # Project root
            Path(f"/{env('APP_NAME').lower()}/src/alembic.ini"),  # Container path
            Path("../alembic.ini"),  # One level up
        ]

        for path in potential_paths:
            if path.exists():
                logger.debug(f"Found alembic.ini at {path}")
                return path

        # If no existing file found, return the standard location in src directory
        logger.debug(
            "Could not find alembic.ini in any standard location, will create at src/alembic.ini"
        )
        return self.paths["src_dir"] / "alembic.ini"

    def ensure_alembic_ini_exists(self):
        """Make sure alembic.ini exists, creating it if necessary"""
        # First check if the file exists at the expected location
        if self.alembic_ini_path.exists():
            logger.debug(f"Using existing alembic.ini at {self.alembic_ini_path}")

            # If we're in test mode or using custom DB info, we need to update the database URL
            if self.test_mode or hasattr(self, "db_info"):
                try:
                    # Read the existing file
                    with open(self.alembic_ini_path, "r") as f:
                        content = f.read()

                    # Update the database URL in the content
                    lines = content.split("\n")
                    updated_lines = []
                    for line in lines:
                        if line.strip().startswith("sqlalchemy.url"):
                            updated_lines.append(
                                f"sqlalchemy.url = {self.db_info['url']}"
                            )
                            logger.debug(
                                f"Updated sqlalchemy.url to: {self.db_info['url']}"
                            )
                        elif (
                            line.strip().startswith("version_locations")
                            and self.test_mode
                        ):
                            # Update version_locations for test mode - use parameterized database directory
                            updated_lines.append(
                                f"version_locations = %(here)s/{self.database_dir_name}/migrations/test_versions"
                            )
                            logger.debug("Updated version_locations for test mode")
                        else:
                            updated_lines.append(line)

                    # Write the updated content back
                    updated_content = "\n".join(updated_lines)
                    with open(self.alembic_ini_path, "w") as f:
                        f.write(updated_content)

                    logger.debug("Updated existing alembic.ini with test configuration")
                except Exception as e:
                    logger.warning(
                        f"Could not update existing alembic.ini: {e}, creating temporary file"
                    )
                    # If we can't update the existing file, create a temporary one
                    config_dict = get_default_alembic_ini_dict(
                        self.test_mode, self.database_dir_name
                    )
                    config_dict["alembic"]["sqlalchemy.url"] = self.db_info["url"]
                    config_content = dict_to_ini(config_dict)
                    temp_path = self.create_temp_file(
                        config_content,
                        suffix=".ini",
                        directory=self.paths["src_dir"],
                    )
                    logger.debug(f"Created temporary alembic.ini at {temp_path}")
                    return temp_path

            return self.alembic_ini_path

        # Create a new alembic.ini file if it doesn't exist
        logger.debug(f"Creating alembic.ini at {self.alembic_ini_path}")

        # Get default config and update with database URL
        config_dict = get_default_alembic_ini_dict(
            self.test_mode, self.database_dir_name
        )
        config_dict["alembic"]["sqlalchemy.url"] = self.db_info["url"]

        # Convert to INI content
        config_content = dict_to_ini(config_dict)

        # Write to the file location
        if self.write_file(self.alembic_ini_path, config_content):
            logger.debug(f"Successfully created alembic.ini at {self.alembic_ini_path}")
            return self.alembic_ini_path
        else:
            # If we can't write to the specified location, create a temporary file
            logger.warning(
                f"Could not write to {self.alembic_ini_path}, creating temporary file"
            )
            temp_path = self.create_temp_file(
                config_content,
                suffix=".ini",
                directory=self.paths["src_dir"],
            )
            logger.debug(f"Created temporary alembic.ini at {temp_path}")
            return temp_path

    def debug_file_info(self, file_path):
        """Debug a file by showing its permissions, size, and other metadata."""
        try:
            path = Path(file_path)
            if not path.exists():
                logger.debug(f"File {path} does not exist")
                return

            # Get file stats
            import datetime
            import stat

            stats = path.stat()

            # Convert mode to human readable
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

            # Format dates
            mtime = datetime.datetime.fromtimestamp(stats.st_mtime)
            atime = datetime.datetime.fromtimestamp(stats.st_atime)

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

    def get_common_env_vars(self, extension_name=None):
        """Get common environment variables for subprocess execution."""
        env = {
            "PYTHONPATH": f"{self.paths['src_dir']}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        }
        if extension_name:
            env["ALEMBIC_EXTENSION"] = extension_name
        return env

    def run_subprocess(self, cmd, env=None, capture_output=True):
        """Run a subprocess command with environment variables and proper output capture."""
        try:
            combined_env = dict(os.environ)
            if env:
                combined_env.update(env)

            logger.debug(f"Running command: {' '.join(cmd)}")

            # Use subprocess.run for better output capture
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # Keep stderr separate to avoid mixing
                text=True,
                env=combined_env,
                check=False,  # Don't raise exception on non-zero exit
            )

            # Log stderr separately if it exists
            if result.stderr:
                for line in result.stderr.strip().split("\n"):
                    if line.strip():
                        logger.debug(f"[SUBPROCESS_STDERR] {line}")

            # Log stdout separately if it exists
            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        logger.debug(f"[SUBPROCESS_STDOUT] {line}")

            success = result.returncode == 0
            if success:
                logger.debug(
                    f"Command completed successfully with return code {result.returncode}"
                )
            else:
                logger.error(f"Command failed with return code {result.returncode}")
                if result.stderr:
                    logger.error(f"Stderr: {result.stderr}")

            return result, success

        except Exception as e:
            # If the error is FileNotFoundError (e.g., 'alembic' not on PATH),
            # retry by invoking the module via the current Python interpreter: `python -m alembic ...`.
            logger.debug(f"Initial subprocess attempt failed: {e}")
            try:
                if isinstance(e, FileNotFoundError) and cmd and isinstance(cmd, (list, tuple)):
                    # Build a fallback command that uses the current Python executable to run alembic as a module
                    fallback_cmd = [sys.executable, "-m", cmd[0]] + list(cmd[1:])
                    logger.debug(f"Retrying with fallback command: {' '.join(fallback_cmd)}")
                    result = subprocess.run(
                        fallback_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=combined_env,
                        check=False,
                    )

                    # Log outputs
                    if result.stderr:
                        for line in result.stderr.strip().split("\n"):
                            if line.strip():
                                logger.debug(f"[FALLBACK_SUBPROCESS_STDERR] {line}")
                    if result.stdout:
                        for line in result.stdout.strip().split("\n"):
                            if line.strip():
                                logger.debug(f"[FALLBACK_SUBPROCESS_STDOUT] {line}")

                    success = result.returncode == 0
                    if success:
                        logger.debug(
                            f"Fallback command completed successfully with return code {result.returncode}"
                        )
                        return result, True
                    else:
                        logger.error(f"Fallback command failed with return code {result.returncode}")
                        if result.stderr:
                            logger.error(f"Fallback stderr: {result.stderr}")
                        return result, False
            except Exception as e2:
                logger.error(f"Fallback subprocess attempt also failed: {e2}")

            logger.error(f"Error running command {' '.join(cmd)}: {e}")
            return None, False

    def cleanup_temporary_files(self, extension_name=None):
        """Clean up any temporary files that might remain from previous operations"""
        logger.debug("Cleaning up temporary files before operation")

        # Clean up in core migrations directory
        core_temp_files = [
            self.paths["migrations_dir"] / "script.py.mako",
            self.paths["migrations_dir"] / "temp_alembic.ini",
        ]

        for file_path in core_temp_files:
            if file_path.exists():
                self.cleanup_file(
                    file_path, f"Cleaned up leftover temporary file: {file_path}"
                )

        # If an extension is specified, clean up its temporary files too
        if extension_name:
            ext_dir = self.paths["extensions_dir"] / extension_name
            if ext_dir.exists():
                migrations_dir = ext_dir / "migrations"
                if migrations_dir.exists():
                    ext_temp_files = [
                        migrations_dir / "temp_alembic.ini",
                        migrations_dir / "alembic.ini",
                    ]

                    for file_path in ext_temp_files:
                        if file_path.exists():
                            self.cleanup_file(
                                file_path,
                                f"Cleaned up leftover extension temporary file: {file_path}",
                            )

        # Also find any temporary alembic ini files in the current directory
        temp_files = Path(".").glob("tmp*.ini")
        for temp_file in temp_files:
            if "alembic" in temp_file.name.lower():
                self.cleanup_file(
                    temp_file,
                    f"Cleaned up leftover temporary alembic ini file: {temp_file}",
                )

        # Clean up the main alembic.ini if it exists and we're not in an active operation
        if not hasattr(self, "_operation_in_progress"):
            alembic_ini = self.paths["src_dir"] / "alembic.ini"
            if alembic_ini.exists():
                self.cleanup_file(alembic_ini, "Cleaned up main alembic.ini")

        return True

    def run_alembic_command(self, command, *args, extra_env=None, extension=None):
        """Run an alembic command."""
        try:
            self._operation_in_progress = True  # Mark that we're in an operation
            self.cleanup_temporary_files(extension)
            alembic_ini_path = self.ensure_alembic_ini_exists()

            original_dir = os.getcwd()
            os.chdir(str(self.paths["src_dir"]))

            rel_path = os.path.relpath(str(alembic_ini_path), self.paths["src_dir"])
            alembic_cmd = ["alembic", "-c", rel_path, command]
            if args:
                alembic_cmd.extend(args)

            script_template_dst = None
            try:
                env = self.get_common_env_vars(extension)
                if extra_env:
                    env.update(extra_env)

                if command == "revision" and not extension:
                    script_template_dst = (
                        self.paths["migrations_dir"] / "script.py.mako"
                    )
                    if not self.write_file(
                        script_template_dst, get_script_py_mako_template()
                    ):
                        return False
                    logger.debug(
                        f"Created temporary script.py.mako at {script_template_dst}"
                    )

                _, success = self.run_subprocess(alembic_cmd, env)
                return success
            except Exception as e:
                logger.error(f"Error running Alembic command: {e}")
                return False
            finally:
                if script_template_dst and script_template_dst.exists():
                    self.cleanup_file(script_template_dst)
                # Always clean up alembic.ini after the command completes
                if self.alembic_ini_path.exists():
                    self.alembic_ini_path.unlink()
                    logger.debug(f"Deleted alembic.ini at {self.alembic_ini_path}")
                os.chdir(original_dir)
                delattr(self, "_operation_in_progress")  # Remove the operation flag
        except Exception as e:
            logger.error(f"Error in run_alembic_command: {e}")
            # Clean up alembic.ini on error
            if self.alembic_ini_path and self.alembic_ini_path.exists():
                try:
                    self.alembic_ini_path.unlink()
                    logger.debug(
                        f"Deleted alembic.ini at {self.alembic_ini_path} after error"
                    )
                except Exception as cleanup_error:
                    logger.warning(f"Failed to clean up alembic.ini: {cleanup_error}")
            if hasattr(self, "_operation_in_progress"):
                delattr(self, "_operation_in_progress")
            return False

    def run_extension_migration(
        self, extension_name, command, target="head", auto=True
    ):
        """Run a migration command for a specific extension."""
        # Clean up any existing files for this specific extension first
        self._cleanup_specific_extension_files(extension_name)

        success, versions_dir = self.ensure_extension_versions_directory(extension_name)
        if not success:
            return False

        original_dir = os.getcwd()
        os.chdir(str(self.paths["src_dir"]))

        temp_ini = None
        env_py_path = None
        try:
            temp_ini = self.create_extension_alembic_ini(extension_name, versions_dir)

            # Create env.py specifically for this extension
            env_py_path = versions_dir.parent / "env.py"
            core_env_py = self.paths["migrations_dir"] / "env.py"
            if core_env_py.exists():
                # Delete existing env.py if it exists
                if env_py_path.exists():
                    env_py_path.unlink()
                    logger.debug(f"Deleted existing env.py for {extension_name}")

                # Copy core env.py to extension
                shutil.copy2(core_env_py, env_py_path)
                logger.debug(f"Created env.py for extension {extension_name}")

            from lib.Pydantic import ModelRegistry

            # Load extension BLL models
            ext_path = self.paths["extensions_dir"] / extension_name
            bll_model_files = list(ext_path.glob("BLL_*.py"))
            if not bll_model_files:
                logger.warning(
                    f"No BLL_*.py files found for extension {extension_name}"
                )
                return False

            # Import BLL files from the extension
            ModelRegistry.from_scoped_import(
                file_type="BLL",
                scopes=[f"{self.extensions_dir_name}.{extension_name}"],
            )

            rel_temp_ini = os.path.relpath(str(temp_ini), self.paths["src_dir"])

            # Handle different commands
            if command == "revision":
                # Check if this is the first migration for revision command
                alembic_history_cmd = ["alembic", "-c", rel_temp_ini, "history"]
                result_history, _ = self.run_subprocess(
                    alembic_history_cmd,
                    self.get_common_env_vars(extension_name),
                    capture_output=True,
                )

                is_first_migration = (
                    not result_history
                    or not result_history.stdout
                    or "->" not in result_history.stdout
                )
                branch_label = f"ext_{extension_name}"

                alembic_cmd = ["alembic", "-c", rel_temp_ini, "revision"]
                if auto:
                    alembic_cmd.append("--autogenerate")

                if is_first_migration:
                    alembic_cmd.extend(
                        ["--head", "base", "--branch-label", branch_label]
                    )

                result, success = self.run_subprocess(
                    alembic_cmd, self.get_common_env_vars(extension_name)
                )
                return success

            else:
                # Handle other commands like upgrade, downgrade, current, history
                alembic_cmd = ["alembic", "-c", rel_temp_ini, command]

                # Add target argument for upgrade/downgrade commands
                if command in ["upgrade", "downgrade"] and target:
                    alembic_cmd.append(target)
                    logger.debug(
                        f"Extension {extension_name}: Running {command} to target '{target}'"
                    )

                logger.debug(
                    f"Extension {extension_name}: Command = {' '.join(alembic_cmd)}"
                )
                result, success = self.run_subprocess(
                    alembic_cmd, self.get_common_env_vars(extension_name)
                )

                # For commands that return output (like current, history), return the result
                if command in ["current", "history"] and result:
                    return result

                return success

        except Exception as e:
            logger.error(f"Error running extension migration: {e}")
            return False
        finally:
            # Clean up files for this specific extension at the end
            self._cleanup_specific_extension_files(extension_name)
            os.chdir(original_dir)

    def create_extension_migration(self, extension_name, message, auto=True):
        """Create a migration for a specific extension."""
        # Clean up any existing files for this specific extension first
        self._cleanup_specific_extension_files(extension_name)

        success, versions_dir = self.ensure_extension_versions_directory(extension_name)
        if not success:
            return False

        original_dir = os.getcwd()
        os.chdir(str(self.paths["src_dir"]))

        temp_ini = None
        env_py_path = None
        script_template_path = None

        try:
            temp_ini = self.create_extension_alembic_ini(extension_name, versions_dir)

            # Create env.py specifically for this extension
            env_py_path = versions_dir.parent / "env.py"
            core_env_py = self.paths["migrations_dir"] / "env.py"
            if core_env_py.exists():
                # Delete existing env.py if it exists
                if env_py_path.exists():
                    env_py_path.unlink()
                    logger.debug(f"Deleted existing env.py for {extension_name}")

                # Copy core env.py to extension
                shutil.copy2(core_env_py, env_py_path)
                logger.debug(f"Created env.py for extension {extension_name}")

            # Create script.py.mako template for extension
            script_template_path = versions_dir.parent / "script.py.mako"
            if script_template_path.exists():
                script_template_path.unlink()
                logger.debug(f"Deleted existing script.py.mako for {extension_name}")

            if not self.write_file(script_template_path, get_script_py_mako_template()):
                return False
            logger.debug(f"Created script.py.mako for extension {extension_name}")

            from lib.Pydantic import ModelRegistry

            # Load extension BLL models
            ext_path = self.paths["extensions_dir"] / extension_name
            bll_model_files = list(ext_path.glob("BLL_*.py"))
            if not bll_model_files:
                logger.warning(
                    f"No BLL_*.py files found for extension {extension_name}"
                )
                return False

            # Import BLL files from the extension
            ModelRegistry.from_scoped_import(
                file_type="BLL",
                scopes=[f"{self.extensions_dir_name}.{extension_name}"],
            )

            rel_temp_ini = os.path.relpath(str(temp_ini), self.paths["src_dir"])
            alembic_history_cmd = ["alembic", "-c", rel_temp_ini, "history"]
            result_history, _ = self.run_subprocess(
                alembic_history_cmd,
                self.get_common_env_vars(extension_name),
                capture_output=True,
            )

            is_first_migration = (
                not result_history
                or not result_history.stdout
                or "->" not in result_history.stdout
            )
            branch_label = f"ext_{extension_name}"

            alembic_cmd = ["alembic", "-c", rel_temp_ini, "revision", "-m", message]
            if auto:
                alembic_cmd.append("--autogenerate")

            if is_first_migration:
                alembic_cmd.extend(["--head", "base", "--branch-label", branch_label])

            # Run the alembic command
            logger.debug(f"Running alembic command: {' '.join(alembic_cmd)}")
            result, success = self.run_subprocess(
                alembic_cmd, self.get_common_env_vars(extension_name)
            )

            return success
        except Exception as e:
            logger.error(f"Error creating extension migration: {e}")
            return False
        finally:
            # Clean up files for this specific extension at the end
            self._cleanup_specific_extension_files(extension_name)
            os.chdir(original_dir)

    def create_extension_alembic_ini(self, extension_name, extension_versions_dir):
        """Create a completely customized alembic.ini for an extension"""
        ext_migrations_dir = extension_versions_dir.parent

        # Prepare configuration dictionary
        config_dict = get_extension_alembic_ini_dict(
            extension_name,
            ext_migrations_dir,
            self.db_info["url"],
            self.test_mode,
            self.database_dir_name,
        )

        # Convert to INI format
        config_content = dict_to_ini(config_dict)

        # Write to a temporary file located alongside the extension migrations
        temp_file_path = self.create_temp_file(
            config_content,
            suffix=".ini",
            directory=ext_migrations_dir,
        )

        self._extension_temp_inis.setdefault(extension_name, set()).add(temp_file_path)
        logger.debug(
            f"Created dedicated extension alembic.ini at {temp_file_path} for {extension_name}"
        )

        # For debugging, log the configuration
        logger.debug(f"Extension alembic.ini configuration:\n{config_content}")

        return temp_file_path

    def ensure_extension_versions_directory(self, extension_name):
        """Ensure extension has a migrations/versions directory."""
        ext_dir = self.paths["extensions_dir"] / extension_name
        if not ext_dir.exists():
            return False, None

        # Create root extension __init__.py if it doesn't exist (needed for imports)
        ext_init_file = ext_dir / "__init__.py"
        if not ext_init_file.exists():
            ext_init_file.touch()
            logger.debug(f"Created root __init__.py for extension {extension_name}")

        # Create migrations directory
        migrations_dir = ext_dir / "migrations"
        migrations_dir.mkdir(exist_ok=True)

        # Create versions directory inside migrations
        if self.test_mode:
            versions_dir = migrations_dir / "test_versions"
        else:
            versions_dir = migrations_dir / "versions"

        versions_dir.mkdir(exist_ok=True)

        # Create __init__.py in both migrations and versions directories
        (migrations_dir / "__init__.py").touch()
        (versions_dir / "__init__.py").touch()

        logger.debug(f"Ensured extension versions directory at {versions_dir}")
        return True, versions_dir

    def _cleanup_specific_extension_files(self, extension_name):
        """Clean up temporary files for ONE specific extension only."""
        logger.debug(f"Cleaning up files for extension: {extension_name}")

        ext_dir = self.paths["extensions_dir"] / extension_name
        if not ext_dir.exists():
            return

        migrations_dir = ext_dir / "migrations"
        if not migrations_dir.exists():
            return

        # Remove any tracked temporary alembic.ini files created for this extension
        temp_ini_paths = getattr(self, "_extension_temp_inis", {}).pop(
            extension_name, set()
        )
        for temp_ini in temp_ini_paths:
            self.cleanup_file(
                temp_ini,
                f"Removed temporary alembic.ini for extension {extension_name}: {temp_ini}",
            )

        # Files to clean up
        cleanup_files = [
            migrations_dir / "alembic.ini",
            migrations_dir / "env.py",
            migrations_dir / "script.py.mako",
            migrations_dir / "__init__.py",
        ]

        # Clean up files in migrations directory
        for file_path in cleanup_files:
            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.debug(
                        f"Removed {file_path.name} for extension {extension_name}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to remove {file_path}: {e}")

        # Clean up __init__.py in versions directories
        for versions_subdir in ["versions", "test_versions"]:
            versions_dir = migrations_dir / versions_subdir
            if versions_dir.exists():
                versions_init = versions_dir / "__init__.py"
                if versions_init.exists():
                    try:
                        versions_init.unlink()
                        logger.debug(
                            f"Removed __init__.py from {versions_subdir} for extension {extension_name}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to remove {versions_init}: {e}")

        # Clean up root extension __init__.py if it was created during migration operations
        root_init = ext_dir / "__init__.py"
        if root_init.exists():
            try:
                content = root_init.read_text().strip()
                # Only remove if it looks like it was created by migration system
                if not content or content.startswith("# Extension:"):
                    root_init.unlink()
                    logger.debug(
                        f"Removed root __init__.py for extension {extension_name}"
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to remove root __init__.py for {extension_name}: {e}"
                )

    def cleanup_extension_files(self):
        """Clean up temporary files and resources for an extension"""
        logger.debug("Cleaning up temporary extension files")

        # Files to clean up for extensions - these are temporary files created during migration operations
        cleanup_patterns = ["alembic.ini", "env.py", "script.py.mako"]

        # Clean up in all extension directories
        for ext_name in self.configured_extensions:
            # Get extension directory using configurable path
            possible_ext_dirs = [
                self.paths["extensions_dir"] / ext_name,
                Path("extensions") / ext_name,  # Fallback for compatibility
                self.paths["root_dir"] / self.extensions_dir_name / ext_name,
                Path(f"src/{self.extensions_dir_name}") / ext_name,
            ]

            for ext_dir in possible_ext_dirs:
                if not ext_dir.exists():
                    continue

                ext_migrations_dir = ext_dir / "migrations"
                if not ext_migrations_dir.exists():
                    continue

                logger.debug(f"Cleaning up files in {ext_migrations_dir}")
                for pattern in cleanup_patterns:
                    file_path = ext_migrations_dir / pattern
                    if file_path.exists():
                        try:
                            file_path.unlink()
                            logger.debug(f"Removed {pattern} at {file_path}")
                        except Exception as e:
                            logger.warning(f"Failed to clean up {file_path}: {e}")

                # AGGRESSIVE CLEANUP: Remove ALL __init__.py files created during migration operations
                # Remove __init__.py from migrations directory
                migrations_init = ext_migrations_dir / "__init__.py"
                if migrations_init.exists():
                    try:
                        migrations_init.unlink()
                        logger.debug(
                            f"Removed migrations __init__.py at {migrations_init}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to clean up {migrations_init}: {e}")

                # Remove __init__.py from versions directories (both regular and test)
                for versions_subdir in ["versions", "test_versions"]:
                    versions_dir = ext_migrations_dir / versions_subdir
                    if versions_dir.exists():
                        versions_init = versions_dir / "__init__.py"
                        if versions_init.exists():
                            try:
                                versions_init.unlink()
                                logger.debug(
                                    f"Removed versions __init__.py at {versions_init}"
                                )
                            except Exception as e:
                                logger.warning(
                                    f"Failed to clean up {versions_init}: {e}"
                                )

                # AGGRESSIVE CLEANUP: Remove root extension __init__.py if it was created during migration operations
                # Remove it regardless of content since migration operations shouldn't create permanent __init__.py files
                root_init = ext_dir / "__init__.py"
                if root_init.exists():
                    try:
                        # Read content to log what we're removing
                        content = root_init.read_text().strip()
                        root_init.unlink()
                        logger.debug(
                            f"Removed root __init__.py at {root_init} (content: {content[:50]}...)"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to clean up root __init__.py {root_init}: {e}"
                        )

        # Also clean up any temporary INI files in the current directory
        temp_files = Path(".").glob("tmp*.ini")
        for temp_file in temp_files:
            if "alembic" in temp_file.name.lower():
                try:
                    temp_file.unlink()
                    logger.debug(f"Removed temporary file: {temp_file}")
                except Exception as e:
                    logger.warning(f"Failed to clean up {temp_file}: {e}")

        # Clean up the main alembic.ini if it exists and we're not in an active operation
        if not hasattr(self, "_operation_in_progress"):
            alembic_ini = self.paths["src_dir"] / "alembic.ini"
            if alembic_ini.exists():
                self.cleanup_file(alembic_ini, "Cleaned up main alembic.ini")

        return True

    def cleanup_all_extension_files(self, extension_list=None):
        """Clean up all files for specific extensions, including root __init__.py files.
        This is more aggressive than cleanup_extension_files and should be used for test cleanup.
        """
        extension_list = extension_list or []

        logger.debug(f"Performing complete cleanup for extensions: {extension_list}")

        for ext_name in extension_list:
            possible_ext_dirs = [
                self.paths["extensions_dir"] / ext_name,
                Path("extensions") / ext_name,  # Fallback for compatibility
                self.paths["root_dir"] / self.extensions_dir_name / ext_name,
                Path(f"src/{self.extensions_dir_name}") / ext_name,
            ]

            for ext_dir in possible_ext_dirs:
                if ext_dir.exists():
                    try:
                        import shutil

                        shutil.rmtree(ext_dir)
                        logger.debug(
                            f"Completely removed extension directory: {ext_dir}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to remove extension directory {ext_dir}: {e}"
                        )

                        # Try to remove individual files if rmtree fails
                        try:
                            for file_path in ext_dir.rglob("*"):
                                if file_path.is_file():
                                    file_path.unlink()

                            # Remove empty directories
                            for dir_path in sorted(
                                ext_dir.rglob("*"), key=lambda x: str(x), reverse=True
                            ):
                                if dir_path.is_dir() and not any(dir_path.iterdir()):
                                    dir_path.rmdir()

                            # Finally remove the root extension directory if empty
                            if ext_dir.exists() and not any(ext_dir.iterdir()):
                                ext_dir.rmdir()
                                logger.debug(
                                    f"Removed extension directory after manual cleanup: {ext_dir}"
                                )
                        except Exception as e2:
                            logger.error(
                                f"Manual cleanup also failed for {ext_name}: {e2}"
                            )

        return True

    def regenerate_migrations(
        self, extension_name=None, all_extensions=False, message=None
    ):
        """Delete existing migrations and regenerate from scratch"""
        logger.debug(f"Starting regeneration of migrations")

        if message is None:
            message = "initial schema"

        logger.debug(f"Using message: {message}")

        # NUKE THE DATABASE - We're starting from scratch
        logger.debug("Deleting existing database to start fresh")
        db_path = self.paths["database_dir"] / "database.db"
        if db_path.exists():
            try:
                db_path.unlink()
                logger.debug(f"Successfully deleted database: {db_path}")
            except Exception as e:
                logger.warning(f"Failed to delete database {db_path}: {e}")
        else:
            logger.debug(f"Database file {db_path} does not exist, skipping deletion")

        # Handle core migrations first
        if not extension_name or all_extensions:
            logger.debug("Regenerating core migrations")

            # 1. Clear core migrations directory
            versions_dir = (
                self.paths["migrations_dir"] / self._get_versions_directory_name()
            )
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

            # Clean up extension temporary files after all extensions are processed
            if extensions_to_process:
                self.cleanup_extension_files()
                logger.debug("Cleaned up extension temporary files after regeneration")

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
        """Create a sample DB_*.py file with table definitions"""
        logger.debug(f"Creating sample DB model file for extension {extension_name}")

        # Format extension name for class name (capitalize first letter)
        class_name = stringcase.pascalcase(extension_name)

        # Default table name based on extension name
        table_name = f"{extension_name}_items"

        # Create the model file
        model_file = extension_dir / f"DB_{class_name}.py"

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
        finally:
            # Clean up temporary files manually
            try:
                extensions_dir = self.paths["extensions_dir"]
                ext_dir = extensions_dir / extension_name
                migrations_dir = ext_dir / "migrations"

                if migrations_dir.exists():
                    temp_files = [
                        migrations_dir / "alembic.ini",
                        migrations_dir / "env.py",
                        migrations_dir / "script.py.mako",
                    ]

                    for file_path in temp_files:
                        self.cleanup_file(
                            file_path, f"Cleaning up temporary file {file_path}"
                        )
            except Exception as e:
                logger.error(f"Error during final cleanup: {e}", exc_info=True)

            # Also run the standard cleanup
            self.cleanup_extension_files()

    def _ensure_extension_in_env_var(self, extension_name):
        """Ensure an extension is in the APP_EXTENSIONS environment variable"""
        extensions = self._parse_csv_env_var("APP_EXTENSIONS", [])
        if extension_name not in extensions:
            extensions.append(extension_name)

        return ",".join(extensions)

    def update_extension_config(self, extension_name):
        """Update the extension configuration - DEPRECATED

        This function is kept for backward compatibility but doesn't do anything.
        Extensions are now configured via the APP_EXTENSIONS environment variable.
        """
        # Function is kept for backward compatibility
        logger.debug(
            f"NOTICE: Extension '{extension_name}' needs to be added to APP_EXTENSIONS environment variable."
        )
        logger.debug(
            "Please update your environment variables to include this extension for migrations to work correctly."
        )
        return True

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
            from lib.Environment import env

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

        # Alembic configuration
        logger.debug("--- ALEMBIC CONFIG ---")
        logger.debug(f"Alembic ini path: {self.alembic_ini_path}")
        if self.alembic_ini_path.exists():
            logger.debug("Alembic ini exists: Yes")
        else:
            logger.debug("Alembic ini exists: No")

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
                # Check for DB models
                db_files = list(ext_dir.glob("DB_*.py"))
                if db_files:
                    logger.debug(
                        f"Extension {ext_name} DB models: {[f.name for f in db_files]}"
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

        # Wait for alembic.ini to not exist before proceeding
        # This ensures only one thread runs migrations at a time
        alembic_ini_path = self.paths["src_dir"] / "alembic.ini"
        wait_count = 0
        max_wait_count = 600  # 60 seconds timeout

        while alembic_ini_path.exists() and wait_count < max_wait_count:
            if wait_count == 0:
                logger.debug(
                    f"Waiting for alembic.ini to be released by another thread..."
                )
            wait_count += 1

            time.sleep(0.1)  # Sleep for 100ms

            # Log progress every 5 seconds
            if wait_count % 50 == 0:
                logger.debug(
                    f"Still waiting for alembic.ini (waited {wait_count * 0.1:.1f}s)..."
                )

        if wait_count >= max_wait_count:
            logger.error(
                f"Timeout waiting for alembic.ini after {wait_count * 0.1:.1f}s - file may be stuck"
            )
            # Try to clean up the stuck file
            try:
                alembic_ini_path.unlink()
                logger.warning(f"Forcefully removed stuck alembic.ini file")
            except Exception as e:
                logger.error(f"Failed to remove stuck alembic.ini: {e}")
                return False
        elif wait_count > 0:
            logger.debug(
                f"alembic.ini is now available after {wait_count * 0.1:.1f}s wait"
            )

        # Refresh configured extensions to pick up any environment changes
        self.configured_extensions = extensions or self._get_configured_extensions()
        logger.debug(f"Running migrations for extensions: {self.configured_extensions}")
        logger.debug(f"Running {command} for core migrations")
        core_result = self.run_alembic_command(command, target)

        if not core_result:
            logger.error(f"Core migrations {command} failed")
            return False

        extension_migrations = []

        # First, check which extensions actually have DB models
        for ext_name in self.configured_extensions:
            extension_dir = self.paths["extensions_dir"] / ext_name
            db_model_files = list(extension_dir.glob("DB_*.py"))

            if not db_model_files:
                logger.debug(
                    f"Skipping extension '{ext_name}' - no DB_*.py files found"
                )
                continue

            logger.debug(
                f"Found DB models for extension '{ext_name}': {[f.name for f in db_model_files]}"
            )

            # Check for migrations directory
            migrations_dir = extension_dir / "migrations"
            versions_dir = migrations_dir / self._get_versions_directory_name()

            if versions_dir.exists():
                extension_migrations.append((ext_name, versions_dir))
            else:
                # Directory structure needs to be created
                success, dir_path = self.ensure_extension_versions_directory(ext_name)
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
                    db_model_files = list(extension_dir.glob("DB_*.py"))

                    if db_model_files:
                        logger.debug(
                            f"Creating initial migration for extension {extension_name}"
                        )
                        created = self.create_extension_migration(
                            extension_name, "Initial migration", auto=True
                        )
                        if not created:
                            logger.warning(
                                f"Could not automatically create initial migration for {extension_name}. Skipping upgrade."
                            )
                            failed_extensions.append(extension_name)
                            continue
                    else:
                        logger.debug(
                            f"Skipping migration for extension {extension_name} as no DB_*.py files found."
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
            migration_files = [f for f in migration_files if f.name != "__init__.py"]

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
                        logger.warning(
                            f"Could not create initial migration for {extension_name}. Skipping upgrade."
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
        from lib.Environment import env

        value = env(env_var_name)
        if not value or value.strip() == "":
            return default or []
        return [item.strip() for item in value.split(",") if item.strip()]

    def cleanup_test_environment(self):
        """Clean up test-specific files and directories when in test mode"""
        if not self.test_mode:
            return True

        logger.debug("Cleaning up test environment")

        try:
            # Clean up test_versions directory in core migrations
            test_versions_dir = self.paths["migrations_dir"] / "test_versions"
            if test_versions_dir.exists():
                import shutil

                shutil.rmtree(test_versions_dir)
                logger.debug(f"Removed test_versions directory: {test_versions_dir}")

            # Clean up test_versions directories in all extensions
            for ext_name in self.configured_extensions:
                ext_dir = self.paths["extensions_dir"] / ext_name
                if ext_dir.exists():
                    ext_test_versions = ext_dir / "migrations" / "test_versions"
                    if ext_test_versions.exists():
                        import shutil

                        shutil.rmtree(ext_test_versions)
                        logger.debug(
                            f"Removed extension test_versions directory: {ext_test_versions}"
                        )

            # Clean up test database file if it exists and is a test database
            if (
                self.db_info.get("file_path")
                and "test" in self.db_info.get("name", "").lower()
            ):
                test_db_file = Path(self.db_info["file_path"])
                if test_db_file.exists():
                    test_db_file.unlink()
                    logger.debug(f"Removed test database file: {test_db_file}")

        except Exception as e:
            logger.error(f"Error during test environment cleanup: {e}")
            return False

        return True


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
    revision_parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Delete all existing migrations and regenerate",
    )
    revision_parser.add_argument(
        "--all",
        action="store_true",
        help="With --regenerate: regenerate all extensions after core",
    )

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

    init_parser = subparsers.add_parser(
        "init", help="Initialize migration structure for an extension"
    )
    init_parser.add_argument("extension", help="Extension to initialize")
    init_parser.add_argument(
        "--skip-model", action="store_true", help="Skip creating sample model"
    )
    init_parser.add_argument(
        "--skip-migrate", action="store_true", help="Skip migration creation"
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

    # Clean up any leftover temporary files at the start
    extension_name = getattr(args, "extension", None)
    manager.cleanup_temporary_files(extension_name)

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
            if args.regenerate:
                success = manager.regenerate_migrations(
                    extension_name=args.extension,
                    all_extensions=args.all,
                    message=args.message,
                )
            elif args.extension:
                if not args.message:
                    if args.regenerate:
                        args.message = "initial schema"
                    else:
                        logger.debug(
                            "Error: --message is required for new non-regenerated revisions"
                        )
                        sys.exit(1)
                success = manager.create_extension_migration(
                    args.extension, args.message, args.autogenerate
                )
            else:
                if not args.message:
                    if args.regenerate:
                        args.message = "initial schema"
                    else:
                        logger.debug(
                            "Error: --message is required for new non-regenerated revisions"
                        )
                        sys.exit(1)
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

        elif args.command == "init":
            success = manager.create_extension(
                args.extension,
                skip_model=args.skip_model,
                skip_migrate=args.skip_migrate,
            )

        elif args.command == "create":
            success = manager.create_extension(
                args.extension,
                skip_model=args.skip_model,
                skip_migrate=args.skip_migrate,
            )

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
            sys.exit(1)

        sys.exit(0 if success else 1)

    finally:  # FIXME: This code is unreachable
        # Ensure cleanup runs regardless of success/failure
        try:
            # Clean up only the specific extension being worked on
            extension_name = getattr(args, "extension", None)
            if extension_name:
                manager._cleanup_specific_extension_files(extension_name)
                logger.debug(f"Final cleanup for extension {extension_name} completed")
            else:
                # For core operations, just clean up general temporary files
                manager.cleanup_temporary_files()
                logger.debug("Final cleanup of temporary files completed")
        except Exception as e:
            logger.warning(f"Error during final cleanup: {e}", exc_info=True)

        # Clean up test environment
        manager.cleanup_test_environment()


if __name__ == "__main__":
    main()
