"""Alembic environment configuration for core and extension migrations."""

from logging.config import fileConfig
from pathlib import Path

import stringcase
from alembic import context
from sqlalchemy import MetaData, engine_from_config, pool

# Get database configuration
from database.migrations.Migration import MigrationManager
from lib.Environment import env

# Imports for migration environment
from lib.Logging import logger

# Database configuration will be read from Alembic config later

# Determine if this is running in core mode or extension mode
current_file = Path(__file__).resolve()
is_extension_mode = (
    "extensions" in str(current_file).lower()
    and "migrations" in str(current_file).lower()
)
logger.debug(f"Running env.py in {'EXTENSION' if is_extension_mode else 'CORE'} mode")

# Setup paths and import Base
paths = MigrationManager.env_setup_python_path(current_file)

# Lazy initialization of DatabaseManager and Base to avoid creating
# database files at import time (which would create production database during tests)
_db_mgr = None
_Base = None


def _get_db_manager():
    """Get or create the DatabaseManager instance lazily."""
    global _db_mgr
    if _db_mgr is None:
        db_manager_module = MigrationManager.env_import_module_safely(
            "database.DatabaseManager", "Failed to import DatabaseManager"
        )
        if db_manager_module:
            DatabaseManager = getattr(db_manager_module, "DatabaseManager", None)
            if DatabaseManager:
                # DatabaseManager.__init__ already calls init_engine_config()
                # and will read DATABASE_NAME from environment (which is set by
                # MigrationManager.get_common_env_vars for test databases)
                _db_mgr = DatabaseManager()
            else:
                raise ImportError("Could not find DatabaseManager class in module")
        else:
            raise ImportError("Could not import DatabaseManager module")
    return _db_mgr


def _get_base():
    """Get the SQLAlchemy Base lazily."""
    global _Base
    if _Base is None:
        db_mgr = _get_db_manager()
        _Base = db_mgr.Base
        if not _Base:
            raise ImportError("Could not get Base from DatabaseManager")
        logger.debug("Base imported successfully from DatabaseManager")
    return _Base


# For backwards compatibility, expose Base as a property that triggers lazy loading
class _LazyBase:
    """Lazy loader for Base to avoid import-time database creation."""

    def __getattr__(self, name):
        return getattr(_get_base(), name)

    @property
    def metadata(self):
        return _get_base().metadata


Base = _LazyBase()


def import_all_models():
    """Import all database models to populate metadata for migrations."""
    logger.debug("=== STARTING import_all_models() ===")

    # Prefer the in-memory cfg attribute (set by MigrationManager since Phase 4);
    # fall back to ALEMBIC_EXTENSION env var for legacy callers.
    extension_name = (
        context.config.attributes.get("extension") if context.config else None
    ) or env("ALEMBIC_EXTENSION")
    logger.debug(f"Extension target for env.py: {extension_name!r}")

    # Check if we have a ModelRegistry attached to the Base
    model_registry = getattr(Base, "_model_registry", None)

    if (
        model_registry
        and hasattr(model_registry, "is_committed")
        and model_registry.is_committed()
    ):
        logger.debug("Found committed ModelRegistry on Base - using it for models")
        # The ModelRegistry has already processed all models and applied extensions
        # All SQLAlchemy models should already be created and available
        logger.debug(
            f"ModelRegistry has {len(model_registry.db_models)} SQLAlchemy models"
        )

        # Set metadata on tables for extension tracking if needed
        if extension_name:
            logger.debug(f"Marking tables for extension: {extension_name}")
            for pydantic_model, sa_model in model_registry.db_models.items():
                if hasattr(sa_model, "__table__") and hasattr(sa_model, "__module__"):
                    module_name = sa_model.__module__
                    if f"extensions.{extension_name}" in module_name:
                        sa_model.__table__.info["extension"] = extension_name
                        logger.debug(
                            f"Marked table {sa_model.__tablename__} as belonging to extension {extension_name}"
                        )
    else:
        logger.warning(
            "No ModelRegistry found on Base - falling back to legacy imports"
        )
        # Legacy fallback - use ModelRegistry.from_scoped_import
        from lib.Pydantic import ModelRegistry

        if extension_name:
            logger.debug(f"This is an EXTENSION migration for: {extension_name}")
            # For extension migrations, import both core and extension BLL models
            # This ensures foreign key relationships to core tables can be resolved
            ModelRegistry.from_scoped_import(
                file_type="BLL", scopes=["logic", f"extensions.{extension_name}"]
            )
            logger.debug(
                f"Imported BLL models from: logic, extensions.{extension_name}"
            )
        else:
            logger.debug("This is a CORE migration (no ALEMBIC_EXTENSION set)")
            ModelRegistry.from_scoped_import(file_type="BLL", scopes=["logic"])
            logger.debug(f"Imported BLL models from: logic")

    # Force generation of SQLAlchemy models by accessing .DB property of known BLL models
    logger.debug("=== Forcing BLL model registration ===")

    # If we have a ModelRegistry, we don't need to force model creation
    if model_registry and model_registry.is_committed():
        logger.debug(
            "Skipping manual model discovery - ModelRegistry already has all models"
        )
        return

    try:
        # Dynamically discover all models with DatabaseMixin from imported modules
        import inspect
        import sys

        from lib.Pydantic2SQLAlchemy import DatabaseMixin

        discovered_models = []

        # Iterate through all imported modules to find BLL models
        # Create a copy of the items to avoid "dictionary changed size during iteration" error
        for module_name, module in list(sys.modules.items()):
            # Only check BLL modules (logic.BLL_* or extensions.*.BLL_*)
            if not (
                module_name.startswith("logic.BLL_")
                or (module_name.startswith("extensions.") and "BLL_" in module_name)
            ):
                continue

            logger.debug(f"Scanning module {module_name} for DatabaseMixin models")

            # Find all classes in the module that have DatabaseMixin
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # Skip if not a proper class or not defined in this module
                if not hasattr(obj, "__module__") or obj.__module__ != module_name:
                    continue

                # Check if the class has DatabaseMixin as a base class
                if (
                    hasattr(obj, "__bases__")
                    and any("DatabaseMixin" in str(base) for base in obj.__mro__)
                    and hasattr(obj, "DB")
                ):

                    try:
                        # Try to access the .DB property to verify it's a proper model
                        # First check if it's a proper Pydantic model
                        if (
                            not hasattr(obj, "__annotations__")
                            or not obj.__annotations__
                        ):
                            logger.debug(f"Skipping {name} - no annotations")
                            continue

                        # Check if it's a base model or mixin class (these should be skipped)
                        if name.endswith("Model") and (
                            "Mixin" in name
                            or "Base" in name
                            or name
                            in ["DatabaseMixin", "ApplicationModel", "UpdateMixinModel"]
                        ):
                            logger.debug(
                                f"Skipping {name} - appears to be a mixin or base class"
                            )
                            continue

                        # Access the .DB property to force SQLAlchemy model creation
                        db_model = obj.DB
                        discovered_models.append((name, obj, db_model))
                        logger.debug(
                            f"Found and registered model {name} from {module_name} -> table: {getattr(db_model, '__tablename__', 'unknown')}"
                        )

                        # Set module information on the SQLAlchemy model for ownership tracking
                        if hasattr(db_model, "__table__"):
                            # Store the original module name for extension tracking
                            db_model.__table__.info["source_module"] = module_name
                            # Store the extension name if this is an extension model
                            if module_name.startswith("extensions."):
                                parts = module_name.split(".")
                                if len(parts) >= 2:
                                    ext_name = parts[1]
                                    db_model.__table__.info["extension"] = ext_name
                                    logger.debug(
                                        f"Marked table {getattr(db_model, '__tablename__', 'unknown')} as belonging to extension {ext_name}"
                                    )

                    except Exception as e:
                        logger.warning(
                            f"Error accessing .DB property of {name} in {module_name}: {e}"
                        )

        # Log all discovered models
        logger.debug(
            f"Discovered {len(discovered_models)} BLL models with DatabaseMixin:"
        )
        for name, pydantic_model, db_model in discovered_models:
            table_name = getattr(db_model, "__tablename__", "unknown")
            module_name = pydantic_model.__module__
            logger.debug(f"  {name} -> {table_name} (from {module_name})")

    except Exception as e:
        logger.error(f"Error during dynamic BLL model discovery: {e}")
        import traceback

        traceback.print_exc()

        # Fallback to manual imports if dynamic discovery fails
        logger.debug("Falling back to manual model imports...")
        try:
            # Import and access .DB property for core auth models
            from logic.BLL_Auth import (
                FailedLoginAttemptModel,
                InvitationModel,
                InviteeModel,
                PermissionModel,
                RateLimitPolicyModel,
                RoleModel,
                SessionModel,
                TeamMetadataModel,
                TeamModel,
                UserCredentialModel,
                UserMetadataModel,
                UserModel,
                UserRecoveryQuestionModel,
                UserTeamModel,
            )

            # Force SQLAlchemy model generation by accessing .DB property
            auth_models = [
                UserModel,
                UserCredentialModel,
                UserRecoveryQuestionModel,
                FailedLoginAttemptModel,
                TeamModel,
                TeamMetadataModel,
                RoleModel,
                UserTeamModel,
                UserMetadataModel,
                PermissionModel,
                InvitationModel,
                InviteeModel,
                RateLimitPolicyModel,
                SessionModel,
            ]

            # Access .DB property to force SQLAlchemy model creation
            db_models = [model.DB for model in auth_models]
            logger.debug(f"Generated {len(db_models)} SQLAlchemy models from BLL_Auth")

            # Also import BLL models from Extensions and Providers
            try:
                from logic.BLL_Extensions import AbilityModel, ExtensionModel
                from logic.BLL_Providers import (
                    ProviderExtensionAbilityModel,
                    ProviderExtensionModel,
                    ProviderInstanceExtensionAbilityModel,
                    ProviderInstanceModel,
                    ProviderInstanceSettingModel,
                    ProviderInstanceUsageModel,
                    ProviderModel,
                    RotationModel,
                    RotationProviderInstanceModel,
                )

                extension_models = [
                    ExtensionModel,
                    AbilityModel,
                    ProviderModel,
                    ProviderExtensionModel,
                    ProviderExtensionAbilityModel,
                    ProviderInstanceModel,
                    ProviderInstanceUsageModel,
                    ProviderInstanceSettingModel,
                    ProviderInstanceExtensionAbilityModel,
                    RotationModel,
                    RotationProviderInstanceModel,
                ]
                extension_db_models = [model.DB for model in extension_models]
                logger.debug(
                    f"Generated {len(extension_db_models)} extension/provider SQLAlchemy models"
                )

            except ImportError as e:
                logger.warning(f"Could not import extension/provider models: {e}")

            # For extension migrations, also try to import the specific extension models
            if extension_name:
                try:
                    # Import all BLL models from the specific extension
                    import importlib

                    # Try different naming patterns for the BLL module
                    possible_module_names = [
                        f"extensions.{extension_name}.BLL_Auth_MFA",  # Specific case for auth_mfa
                        f"extensions.{extension_name}.BLL_{stringcase.pascalcase(extension_name)}",  # auth_mfa -> BLL_AuthMfa
                        f"extensions.{extension_name}.BLL_{stringcase.pascalcase(extension_name)}",  # auth_mfa -> BLL_AuthMfa (replacing capitalize)
                        f"extensions.{extension_name}.BLL_{extension_name}",  # auth_mfa -> BLL_auth_mfa
                    ]

                    ext_module = None
                    ext_module_name = None

                    for module_name in possible_module_names:
                        try:
                            ext_module = importlib.import_module(module_name)
                            ext_module_name = module_name
                            logger.debug(f"Successfully imported {module_name}")
                            break
                        except ImportError:
                            logger.debug(f"Could not import {module_name}")
                            continue

                    if ext_module:
                        # Find all model classes in the extension module
                        import inspect

                        for name, obj in inspect.getmembers(
                            ext_module, inspect.isclass
                        ):
                            if (
                                hasattr(obj, "__mro__")
                                and any(
                                    "DatabaseMixin" in str(base) for base in obj.__mro__
                                )
                                and hasattr(obj, "DB")
                            ):
                                try:
                                    db_model = obj.DB
                                    logger.debug(
                                        f"Manually registered extension model {name} -> {getattr(db_model, '__tablename__', 'unknown')}"
                                    )
                                    # Mark the table as belonging to this extension
                                    if hasattr(db_model, "__table__"):
                                        db_model.__table__.info["extension"] = (
                                            extension_name
                                        )
                                        db_model.__table__.info["source_module"] = (
                                            ext_module_name
                                        )
                                except Exception as e:
                                    logger.warning(
                                        f"Error accessing .DB property of {name}: {e}"
                                    )
                    else:
                        logger.warning(
                            f"Could not import any BLL module for extension {extension_name}"
                        )

                except Exception as e:
                    logger.warning(
                        f"Error importing extension {extension_name} models: {e}"
                    )

        except Exception as e:
            logger.error(f"Fallback manual imports also failed: {e}")
            import traceback

            traceback.print_exc()

    # Check what's now in Base metadata
    logger.debug(f"Tables now in Base metadata: {list(Base.metadata.tables.keys())}")
    logger.debug("=== BLL model registration complete ===")

    logger.debug(
        f"Base metadata before extension filtering: {len(Base.metadata.tables)} tables"
    )

    if extension_name:
        logger.debug(f"Creating filtered metadata for extension: {extension_name}")
        logger.debug(
            f"Base metadata has {len(Base.metadata.tables)} tables: {list(Base.metadata.tables.keys())}"
        )

        # Create filtered metadata for extension
        filtered_metadata = MetaData()
        included_tables = []
        referenced_tables = set()

        # First pass: identify extension tables and their foreign key references.
        # A table belongs to this extension's autogenerate run if it is either
        # owned by the extension OR extended by it via @extension_model
        # (per IMPROVEMENTS_ORDERED.md Item 24).
        for table_name, table in Base.metadata.tables.items():
            owner = MigrationManager.env_is_table_owned_by_extension(table)
            extenders = MigrationManager.env_table_extenders(table)
            is_owned = owner == extension_name or extension_name in extenders

            logger.debug(
                f"Checking table {table_name}: owner={owner!r}, extenders={extenders} "
                f"-> is_owned_by_{extension_name} = {is_owned}"
            )

            if is_owned:
                logger.debug(
                    f"Including table {table_name} for extension {extension_name}"
                )
                included_tables.append(table_name)

                # Check for foreign key references to other tables
                for fk in table.foreign_keys:
                    referenced_table_name = fk.column.table.name
                    if (
                        referenced_table_name != table_name
                    ):  # Don't include self-references
                        referenced_tables.add(referenced_table_name)
                        logger.debug(
                            f"Extension table {table_name} references table {referenced_table_name}"
                        )

        # Second pass: include all extension tables and their referenced tables
        tables_to_include = set(included_tables) | referenced_tables

        for table_name in tables_to_include:
            if table_name in Base.metadata.tables:
                table = Base.metadata.tables[table_name]
                # Create a copy of the table for the filtered metadata
                table_copy = table.tometadata(filtered_metadata)
                # Preserve the class_ attribute which gets lost during tometadata()
                if hasattr(table, "class_"):
                    table_copy.class_ = table.class_
                logger.debug(f"Added table {table_name} to filtered metadata")

        # Replace the target_metadata with our filtered version
        target_metadata = filtered_metadata
        logger.debug(
            f"Filtered metadata has {len(target_metadata.tables)} tables: {list(target_metadata.tables.keys())}"
        )
        logger.debug("=== FINISHED import_all_models() (extension mode) ===")
    else:
        # For core migrations, use the full metadata
        target_metadata = Base.metadata
        logger.debug(f"Tables: {list(Base.metadata.tables.keys())}")
        logger.debug("=== FINISHED import_all_models() (core mode) ===")

    return target_metadata


# Import models and configure Alembic
target_metadata = import_all_models()
config = context.config
version_table = MigrationManager.env_setup_alembic_config(config)

# Log database configuration from Alembic config (set by MigrationManager)
db_url = config.get_main_option("sqlalchemy.url")
logger.debug(f"Using database URL from Alembic config: {db_url}")

if config.config_file_name:
    try:
        fileConfig(config.config_file_name)
    except (KeyError, AttributeError) as e:
        logger.warning(
            f"Could not configure logging from {config.config_file_name}: {e}"
        )
        logger.debug("Using existing logging configuration")


def include_object(object, name, type_, reflected, compare_to):
    """Filter tables based on migration context."""
    return MigrationManager.env_include_object(
        object, name, type_, reflected, compare_to, Base
    )


def get_alembic_context_config(connection=None, url=None):
    """Configure context for online/offline mode."""
    config_args = {
        "target_metadata": target_metadata,
        "include_object": include_object,
        "version_table": version_table,
        "render_as_batch": True,
    }

    if connection:
        config_args["connection"] = connection
    else:
        config_args.update(
            {"url": url, "literal_binds": True, "dialect_opts": {"paramstyle": "named"}}
        )

    return config_args


def run_migrations():
    """Run migrations in online mode."""
    config_section = config.get_section(config.config_ini_section)
    if not config_section.get("script_location"):
        config_section["script_location"] = str(current_file.parent)

    connectable = engine_from_config(
        config_section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )

    with connectable.connect() as connection:
        context.configure(**get_alembic_context_config(connection=connection))
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_offline():
    """Run migrations in offline mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(**get_alembic_context_config(url=url))
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations()
