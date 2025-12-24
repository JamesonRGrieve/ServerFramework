import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

import stringcase
from fastapi.testclient import TestClient

from lib.Logging import logger
from lib.Pydantic2SQLAlchemy import clear_registry_cache


class AbstractMigrationTest(ABC):
    """
    Abstract base class for migration tests that provides isolated test environments
    and common functionality. Child classes should override configuration properties
    and implement abstract methods to customize behavior.
    """

    # Configuration properties that child classes should override
    TEST_DB_NAME_SUFFIX: str = "abstract"
    TEST_EXTENSIONS: str = ""
    TRACK_CLEANUP: bool = True
    USE_MOCK_DIRECTORIES: bool = False
    CREATE_TEST_EXTENSIONS: List[str] = []

    @abstractmethod
    def get_test_config(self) -> Dict[str, Any]:
        """
        Return test-specific configuration. Child classes must implement this.

        Returns:
            Dict containing test configuration like:
            {
                "test_type": "meta|mock|real",
                "create_mock_extensions": bool,
                "matrix_targets": List[str],
                "test_variations": Dict[str, List],
                # other test-specific config
            }
        """
        pass

    def _initialize_test_instance(self):
        """Initialize the test instance with thread-safe isolation."""
        # Use the class-level suffix for database naming
        self.test_id = self.TEST_DB_NAME_SUFFIX
        self.test_db_name = f"test.migration.{self.TEST_DB_NAME_SUFFIX}"

        # Initialize paths
        self.src_dir = Path(__file__).resolve().parent.parent.parent
        self.database_dir = self.src_dir / "database"
        self.migrations_dir = self.database_dir / "migrations"
        self.extensions_dir = self.src_dir / "extensions"

        # Tracking lists for cleanup
        self.created_model_files = []
        self.created_migration_files = []
        self.created_migration_dirs = []
        self.created_test_extensions = []
        self.created_temp_files = []

        # Test environment state
        self.isolated_server = None
        self.migration_manager = None
        self.custom_db_info = None
        self.versions_dir = None

        # Get test configuration
        self.test_config = self.get_test_config()

    def setup_method(self, method):
        """Setup that runs before each test method."""
        self._initialize_test_instance()
        self._setup_test_environment()

    def teardown_method(self, method):
        """Cleanup that runs after each test method."""
        self._cleanup_test_environment()

    def _setup_test_environment(self):
        """Set up isolated test environment using app.instance()."""
        logger.debug(f"Setting up migration test environment: {self.test_db_name}")

        # Clear registry cache for isolation
        clear_registry_cache()

        # Create isolated server using app.instance()
        from app import instance

        self.isolated_server = TestClient(
            instance(
                db_prefix=f"migration_{self.TEST_DB_NAME_SUFFIX}",
                extensions=self.TEST_EXTENSIONS,
            )
        )

        # Set up Base for the new system
        try:
            from database.DatabaseManager import DatabaseManager

            db_manager = self.isolated_server.app.state.model_registry.database_manager
            Base = db_manager.Base
            logger.debug("Successfully obtained Base from isolated database manager")

            # Ensure the model registry is properly available and committed
            if hasattr(self.isolated_server.app.state, "model_registry"):
                model_registry = self.isolated_server.app.state.model_registry
                # Check if registry is already committed (should be from app.instance)
                if not model_registry._committed:
                    logger.debug(
                        "Registry not committed, committing with test extensions"
                    )
                    model_registry.commit(self.TEST_EXTENSIONS, db_manager)
                else:
                    logger.debug("Registry already committed from app.instance")
            else:
                logger.warning("No model registry found on isolated server")
        except Exception as e:
            logger.warning(f"Could not obtain Base from isolated manager: {e}")
            from sqlalchemy.orm import declarative_base

            Base = declarative_base()

        # Configure database info for migration manager with semantic naming
        # Use the semantic name directly for the database file
        semantic_db_filename = f"test.migration.{self.TEST_DB_NAME_SUFFIX}.db"
        self.custom_db_info = {
            "type": "sqlite",
            "name": self.test_db_name,
            "url": f"sqlite:///{self.database_dir}/{semantic_db_filename}",
            "file_path": str(self.database_dir / semantic_db_filename),
        }

        # Create migration manager with isolated database
        from database.migrations.Migration import MigrationManager

        self.migration_manager = MigrationManager(
            test_mode=True,
            custom_db_info=self.custom_db_info,
            extensions_dir=(
                "extensions" if not self.USE_MOCK_DIRECTORIES else "mock_ext"
            ),
            database_dir="database" if not self.USE_MOCK_DIRECTORIES else "mock_db",
        )

        # Set up mock directories if needed
        if self.USE_MOCK_DIRECTORIES:
            self._setup_mock_directories()

        # Create test_versions directory
        self.versions_dir = self.migrations_dir / "test_versions"
        self.versions_dir.mkdir(exist_ok=True)
        init_file = self.versions_dir / "__init__.py"
        init_file.touch()
        if self.TRACK_CLEANUP:
            self.created_migration_dirs.append(self.versions_dir)
            self.created_migration_files.append(init_file)

        # Create test extensions if configured
        for ext_name in self.CREATE_TEST_EXTENSIONS:
            self._create_test_extension(ext_name)

        logger.debug(f"Migration test environment ready: {self.test_db_name}")

    def _setup_mock_directories(self):
        """Set up mock directories for isolated testing."""
        mock_extensions_dir = self.migrations_dir / "mock_ext"
        mock_database_dir = self.migrations_dir / "mock_db"

        # Update manager paths to use mock directories
        self.migration_manager.paths["extensions_dir"] = mock_extensions_dir
        self.migration_manager.paths["database_dir"] = mock_database_dir

        # Track mock directories for cleanup
        if self.TRACK_CLEANUP:
            self.created_migration_dirs.extend([mock_extensions_dir, mock_database_dir])

        logger.debug(
            f"Set up mock directories: {mock_extensions_dir}, {mock_database_dir}"
        )

    def _create_test_extension(self, ext_name: str, create_model: bool = True):
        """Create a test extension with BLL model using new Pydantic2SQLAlchemy system."""
        if self.USE_MOCK_DIRECTORIES:
            base_dir = self.migration_manager.paths["extensions_dir"]
        else:
            base_dir = self.extensions_dir

        ext_dir = base_dir / ext_name
        ext_dir.mkdir(parents=True, exist_ok=True)

        if self.TRACK_CLEANUP:
            self.created_test_extensions.append(ext_dir)

        # Create __init__.py
        init_file = ext_dir / "__init__.py"
        init_file.touch()
        if self.TRACK_CLEANUP:
            self.created_model_files.append(init_file)

        if create_model:
            # Create BLL model using new Pydantic2SQLAlchemy system
            class_name = f"{stringcase.pascalcase(ext_name)}TestModel"
            model_content = f'''# Test BLL model for extension {ext_name}
from typing import Optional, ClassVar, List, Dict, Any
from pydantic import BaseModel, Field
from lib.Pydantic2SQLAlchemy import (
    
    ApplicationModel,
    UpdateMixinModel,
    StringSearchModel,
)

class {class_name}(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    
):
    """Test model for {ext_name} extension"""
    name: Optional[str] = Field(None, description="Test item name")
    description: Optional[str] = Field(None, description="Test item description")

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = "Test table for {ext_name} extension"

    class ReferenceID:
        {ext_name.lower()}_test_id: str = Field(..., description="The ID of the related {ext_name} test item")

        class Optional:
            {ext_name.lower()}_test_id: Optional[str] = None

        class Search:
            {ext_name.lower()}_test_id: Optional[StringSearchModel] = None

    class Create(BaseModel):
        name: str = Field(..., description="Test item name")
        description: Optional[str] = Field(None, description="Test item description")

    class Update(BaseModel):
        name: Optional[str] = Field(None, description="Test item name")
        description: Optional[str] = Field(None, description="Test item description")

    class Search(ApplicationModel.Search):
        name: Optional[StringSearchModel] = None
        description: Optional[StringSearchModel] = None

class {class_name}ReferenceModel({class_name}.Reference.ID):
    {ext_name.lower()}_test: Optional[{class_name}] = None

    class Optional({class_name}.Reference.ID.Optional):
        {ext_name.lower()}_test: Optional[{class_name}] = None

class {class_name}NetworkModel:
    class POST(BaseModel):
        {ext_name.lower()}_test: {class_name}.Create

    class PUT(BaseModel):
        {ext_name.lower()}_test: {class_name}.Update

    class SEARCH(BaseModel):
        {ext_name.lower()}_test: {class_name}.Search

    class ResponseSingle(BaseModel):
        {ext_name.lower()}_test: {class_name}

    class ResponsePlural(BaseModel):
        {ext_name.lower()}_tests: List[{class_name}]
'''

            model_file = ext_dir / f"BLL_{class_name}.py"
            self._write_file(model_file, model_content)
            if self.TRACK_CLEANUP:
                self.created_model_files.append(model_file)

        logger.debug(f"Created test extension: {ext_name}")
        return ext_dir

    def _write_file(self, file_path: Path, content: str):
        """Write content to a file, creating parent directories if needed."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

    def _cleanup_test_environment(self):
        """Clean up all created files and directories."""
        if not self.TRACK_CLEANUP:
            return

        logger.debug(f"Cleaning up migration test environment: {self.test_db_name}")

        # Clean up model files
        for file_path in self.created_model_files:
            try:
                if file_path.exists():
                    file_path.unlink()
                    logger.debug(f"Removed model file: {file_path}")
            except Exception as e:
                logger.error(f"Error cleaning up file {file_path}: {e}")

        # Clean up migration files
        for file_path in self.created_migration_files:
            try:
                if file_path.exists():
                    file_path.unlink()
                    logger.debug(f"Removed migration file: {file_path}")
            except Exception as e:
                logger.error(f"Error cleaning up migration file {file_path}: {e}")

        # Clean up temp files
        for file_path in self.created_temp_files:
            try:
                if file_path.exists():
                    file_path.unlink()
                    logger.debug(f"Removed temp file: {file_path}")
            except Exception as e:
                logger.error(f"Error cleaning up temp file {file_path}: {e}")

        # Clean up directories (in reverse order to handle nested structures)
        all_dirs = self.created_migration_dirs + self.created_test_extensions
        for dir_path in reversed(all_dirs):
            try:
                if dir_path.exists():
                    if dir_path.is_dir():
                        shutil.rmtree(dir_path)
                        logger.debug(f"Removed directory: {dir_path}")
            except Exception as e:
                logger.error(f"Error cleaning up directory {dir_path}: {e}")

        # Clean up test database file
        try:
            if self.custom_db_info and "file_path" in self.custom_db_info:
                db_file = Path(self.custom_db_info["file_path"])
                if db_file.exists():
                    db_file.unlink()
                    logger.debug(f"Removed test database: {db_file}")
        except Exception as e:
            logger.error(f"Error cleaning up database file: {e}")

        # Clear registry cache after cleanup
        clear_registry_cache()
        logger.debug(f"Migration test cleanup complete: {self.test_db_name}")

    # Helper methods for common test operations

    def run_migration_command_for_target(
        self, target: str, *args, expect_success: bool = True
    ):
        """Run migration commands for a specific target (core or extension)."""
        if not args:
            raise ValueError("No command provided")

        command = args[0]
        remaining_args = args[1:]

        logger.debug(
            f"Running migration command for {target}: {command} {' '.join(remaining_args)}"
        )

        class Result:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        try:
            if command == "revision":
                message = None
                autogenerate = True
                regenerate = False

                # Parse arguments
                i = 0
                while i < len(remaining_args):
                    arg = remaining_args[i]
                    if arg in ["-m", "--message"]:
                        if i + 1 < len(remaining_args):
                            message = remaining_args[i + 1]
                            i += 1
                    elif arg == "--regenerate":
                        regenerate = True
                    elif arg == "--no-autogenerate":
                        autogenerate = False
                    i += 1

                if not message and not regenerate:
                    return Result(returncode=1, stderr="Error: --message is required")

                if target == "core":
                    if regenerate:
                        success = self.migration_manager.regenerate_migrations(
                            extension_name=None,
                            all_extensions=False,
                            message=message or "initial schema",
                        )
                    else:
                        if autogenerate:
                            success = self.migration_manager.run_alembic_command(
                                "revision", "--autogenerate", "-m", message
                            )
                        else:
                            success = self.migration_manager.run_alembic_command(
                                "revision", "-m", message
                            )
                else:
                    # Extension
                    if regenerate:
                        success = self.migration_manager.regenerate_migrations(
                            extension_name=target,
                            all_extensions=False,
                            message=message or "initial schema",
                        )
                    else:
                        success = self.migration_manager.create_extension_migration(
                            target, message, autogenerate
                        )

                return Result(returncode=0 if success else 1)

            elif command in ["upgrade", "downgrade"]:
                migration_target = "head" if command == "upgrade" else "-1"

                # Parse target argument
                for arg in remaining_args:
                    if not arg.startswith("--"):
                        migration_target = arg
                        break

                if target == "core":
                    success = self.migration_manager.run_alembic_command(
                        command, migration_target
                    )
                else:
                    success = self.migration_manager.run_extension_migration(
                        target, command, migration_target
                    )

                return Result(returncode=0 if success else 1)

            elif command in ["current", "history"]:
                if target == "core":
                    success = self.migration_manager.run_alembic_command(command)
                    output = (
                        "o current revision"
                        if command == "current"
                        else "<base> -> <current>, initial"
                    )
                else:
                    result = self.migration_manager.run_extension_migration(
                        target, command
                    )
                    if hasattr(result, "stdout"):
                        return result
                    success = result
                    output = (
                        "o current revision"
                        if command == "current"
                        else "<base> -> <current>, initial"
                    )

                return Result(returncode=0 if success else 1, stdout=output)

            else:
                return Result(returncode=1, stderr=f"Unknown command: {command}")

        except Exception as e:
            logger.error(f"Error running migration command for {target}: {e}")
            if expect_success:
                raise
            return Result(returncode=1, stderr=str(e))

    def get_migration_files_for_target(self, target: str):
        """Get all migration files for the specified target."""
        if target == "core":
            if self.USE_MOCK_DIRECTORIES:
                versions_dir = (
                    self.migration_manager.paths["database_dir"]
                    / "migrations"
                    / "test_versions"
                )
            else:
                versions_dir = self.migrations_dir / "test_versions"
        else:
            # Extension
            if self.USE_MOCK_DIRECTORIES:
                base_dir = self.migration_manager.paths["extensions_dir"]
            else:
                base_dir = self.extensions_dir

            ext_dir = base_dir / target
            versions_dir = ext_dir / "migrations" / "test_versions"

            # Track directories for cleanup if they don't exist yet
            if not ext_dir.exists() and self.TRACK_CLEANUP:
                self.created_test_extensions.append(ext_dir)
            if not versions_dir.exists() and self.TRACK_CLEANUP:
                self.created_migration_dirs.append(versions_dir)

        versions_dir.mkdir(parents=True, exist_ok=True)
        init_file = versions_dir / "__init__.py"
        if not init_file.exists():
            init_file.touch()
            if self.TRACK_CLEANUP:
                self.created_migration_files.append(init_file)

        files = sorted(
            [f for f in versions_dir.glob("*.py") if f.name != "__init__.py"],
            key=lambda f: f.stat().st_ctime,
        )
        return files

    def clear_migrations_for_target(self, target: str):
        """Clear migration files for the specified target."""
        files = self.get_migration_files_for_target(target)
        for f in files:
            f.unlink()
        return files[0].parent if files else None

    def assert_migration_content(self, migration_file: Path, expected_content: str):
        """Assert that a migration file contains the expected content."""
        assert (
            migration_file.exists()
        ), f"Migration file {migration_file} does not exist"
        content = migration_file.read_text()
        assert (
            expected_content in content
        ), f"Expected content '{expected_content}' not found in {migration_file}"

    def get_test_targets(self):
        """Get test targets based on configuration."""
        config = self.get_test_config()
        return config.get("matrix_targets", ["core"])

    def setup_extension_for_testing(self, extension_name: str):
        """Set up an extension for testing (real or test extension)."""
        if extension_name == "core":
            return True

        # Check if this is a real extension that already exists
        real_ext_dir = self.extensions_dir / extension_name
        if real_ext_dir.exists():
            bll_files = list(real_ext_dir.glob("BLL_*.py"))
            if bll_files:
                logger.debug(
                    f"Using existing real extension with {len(bll_files)} BLL models: {extension_name}"
                )
                return True
            else:
                logger.debug(
                    f"Real extension {extension_name} exists but has no BLL models - skipping"
                )
                return False

        # Create test extension
        self._create_test_extension(extension_name)
        return True


# Pytest utilities for parameterized testing


def pytest_generate_matrix_tests(
    metafunc, targets: List[str], variations: Optional[Dict[str, List]] = None
):
    """
    Generate parameterized tests for matrix testing across targets and variations.

    Args:
        metafunc: pytest metafunc object
        targets: List of targets (e.g., ["core", "ext1", "ext2"])
        variations: Optional dict of variations for specific parameters
    """
    if "migration_target" in metafunc.fixturenames:
        # Handle nested parametrization for variations
        if variations:
            for param_name, param_values in variations.items():
                if param_name in metafunc.fixturenames:
                    metafunc.parametrize("migration_target", targets, indirect=True)
                    metafunc.parametrize(
                        param_name,
                        param_values,
                        indirect=True,
                        ids=[
                            str(v.get("type", v)) if isinstance(v, dict) else str(v)
                            for v in param_values
                        ],
                    )
                    return

        # Standard matrix parametrization
        metafunc.parametrize("migration_target", targets, indirect=True)


def create_revision_variations():
    """Create standard revision test variations."""
    return [
        {"type": "auto", "message": "auto test", "flags": []},
        {"type": "default_autogenerate", "message": "default autogen", "flags": []},
        {
            "type": "no_autogenerate",
            "message": "empty migration",
            "flags": ["--no-autogenerate"],
        },
    ]


def create_regenerate_variations():
    """Create standard regenerate test variations."""
    return [
        {"type": "with_message", "message": "regenerated message"},
        {"type": "default_message", "message": None},
    ]
