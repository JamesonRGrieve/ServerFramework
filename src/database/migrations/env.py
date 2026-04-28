"""Alembic environment configuration.

Single shared env.py module — referenced by every Alembic invocation
(core and per-extension) via the script_location of the in-memory Config
that MigrationManager._make_alembic_config builds. There is no
per-extension env.py copy; the extension target is propagated through
context.config.attributes["extension"] (set by _make_alembic_config) with
ALEMBIC_EXTENSION as a back-compat fallback for direct alembic CLI use.
"""

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import MetaData, engine_from_config, pool

from database.migrations.Migration import MigrationManager
from lib.Environment import env
from lib.Logging import logger

current_file = Path(__file__).resolve()
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

    # Pull the ModelRegistry from cfg.attributes (set by
    # MigrationManager._make_alembic_config when commit() drives migrations)
    # or from Base._model_registry (set when migrations run on a registry
    # whose database_manager.Base happens to coincide with env.py's lazy
    # Base — primarily the direct alembic CLI path). The cfg path is
    # authoritative because env.py's lazy-loaded Base is a separate
    # SQLAlchemy declarative instance from the registry-driven Base, so the
    # attribute-lookup path misses during commit().
    cfg_registry = (
        context.config.attributes.get("model_registry") if context.config else None
    )
    base_registry = getattr(Base, "_model_registry", None)
    model_registry = cfg_registry or base_registry

    if model_registry is None:
        raise RuntimeError(
            "env.py was invoked without a ModelRegistry. Run migrations "
            "through MigrationManager (or app.instance) so the registry is "
            "passed via cfg.attributes['model_registry']; the ~280-line "
            "legacy import-discovery fallback was removed in the Phase 3 "
            "cleanup. See src/database/migrations/DB.Migrations.md for "
            "details."
        )

    # Make the registry visible to downstream consumers (env_include_object
    # and the filtered-metadata loop) by attaching it to env.py's Base when
    # it came from cfg. This keeps the existing call sites that read
    # Base.metadata / Base._model_registry working without thread-through.
    if cfg_registry is not None:
        # The cfg-supplied registry's database_manager.Base may have all the
        # SA tables defined; reuse its metadata directly.
        registry_base = getattr(
            getattr(model_registry, "database_manager", None), "Base", None
        )
        if registry_base is not None:
            global _Base
            _Base = registry_base
            try:
                setattr(_Base, "_model_registry", model_registry)
            except Exception:
                pass

    # During ModelRegistry.commit(), env.py is invoked from inside the
    # `upgrade head` step *before* SA models are created and before _locked
    # flips. That is intentional: `upgrade` does not need target_metadata —
    # it just replays revision files. Autogenerate paths arrange to call
    # _create_sqlalchemy_models before invoking the revision command, so
    # Base.metadata is populated by then.
    logger.debug(
        f"ModelRegistry resolved: "
        f"{len(getattr(model_registry, 'db_models', {}))} SA models registered, "
        f"committed={getattr(model_registry, '_locked', False)}, "
        f"source={'cfg' if cfg_registry else 'Base'}"
    )
    # Ownership stamps on table.info["extension"] / ["extensions"] are
    # populated by ModelRegistry._stamp_extension_table_ownership at commit
    # time — env.py does NOT need to set them again per migration. The
    # filtering loop below reads those stamps via
    # MigrationManager.env_is_table_owned_by_extension.

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
