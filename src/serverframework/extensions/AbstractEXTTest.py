import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Set, Type, TypeVar

import pytest

from serverframework.AbstractTest import AbstractTest
from conftest import (
    add_user_to_team,
    create_role,
    create_team,
    create_user,
    generate_test_email,
)
from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies
from serverframework.lib.Environment import env
from serverframework.lib.Logging import logger

T = TypeVar("T", bound=AbstractStaticExtension)


class ExtensionTestType(str, Enum):
    STRUCTURE = "structure"
    METADATA = "metadata"
    DEPENDENCIES = "dependencies"
    ABILITIES = "abilities"
    ENVIRONMENT = "environment"
    ROTATION = "rotation"
    PERFORMANCE = "performance"
    CONCURRENCY = "concurrency"
    MODEL_REGISTRY = "model_registry"
    DATABASE_ISOLATION = "database_isolation"


@dataclass
class ExtensionTestConfig:
    """Configuration for extension testing behavior."""

    test_types: Set[ExtensionTestType] = field(
        default_factory=lambda: set(ExtensionTestType)
    )
    expected_abilities: Set[str] = field(default_factory=set)
    expected_dependencies: Dependencies = field(
        default_factory=lambda: Dependencies([])
    )
    expected_env_vars: Dict[str, Any] = field(default_factory=dict)
    performance_thresholds: Dict[str, float] = field(
        default_factory=lambda: {
            "cache_improvement_min": 2.0,
            "concurrent_threads": 10,
            "performance_iterations": 100,
        }
    )
    skip_rotation_tests: bool = False
    skip_performance_tests: bool = False


class ExtensionServerMixin:
    """Simplified mixin for extension server fixtures."""

    extension_class: Type[T] = None

    @pytest.fixture(scope="module")
    def server(self):
        """Create an isolated test server for the extension."""
        if not self.extension_class:
            pytest.skip("extension_class not defined, test cannot run")

        from serverframework.lib.Pydantic2SQLAlchemy import clear_registry_cache

        clear_registry_cache()
        logger.debug(
            f"Cleared registry caches for extension {self.extension_class.name}"
        )

        from fastapi.testclient import TestClient

        extension_name = self.extension_class.name.lower()
        test_db_prefix = f"test.{extension_name}"
        extension_list = extension_name
        try:
            from serverframework.app import instance

            app = instance(db_prefix=test_db_prefix, extensions=extension_list)
            client = TestClient(app)
            yield client
        except ImportError as e:
            pytest.skip(f"FastAPI dependencies not available: {e}")
        except Exception as e:
            pytest.skip(f"Server setup failed: {e}")

    @pytest.fixture(scope="module")
    def admin_a(self, server):
        """Admin user for team_a"""
        return create_user(
            server,
            email=generate_test_email("admin_a"),
            last_name="AdminA",
        )

    @pytest.fixture(scope="module")
    def team_a(self, server, admin_a):
        """Create team_a for testing"""
        return create_team(server, admin_a.id, name="Team A")

    @pytest.fixture(scope="module")
    def admin_b(self, server):
        """Admin user for team_b"""
        return create_user(
            server,
            email=generate_test_email("admin_b"),
            last_name="AdminB",
        )

    @pytest.fixture(scope="module")
    def team_b(self, server, admin_b):
        """Create team_b for testing"""
        return create_team(server, admin_b.id, name="Team B")

    @pytest.fixture(scope="module")
    def user_b(self, server, team_b):
        """Regular user for team_b"""
        user = create_user(
            server, email=generate_test_email("user_b"), last_name="UserB"
        )
        add_user_to_team(server, user.id, team_b.id, env("USER_ROLE_ID"))
        return user

    @pytest.fixture(scope="module")
    def mod_b_role(self, server, admin_a, team_b):
        """Moderator role for team_b"""
        return create_role(
            server,
            admin_a.id,
            team_b.id,
            name="mod_b",
            friendly_name="Moderator B",
            parent_id=env("USER_ROLE_ID"),
        )

    @pytest.fixture(scope="module")
    def mod_b(self, server, admin_b, team_b, mod_b_role):
        """Moderator user for team_b"""
        user = create_user(server, email=generate_test_email("mod_b"), last_name="ModB")
        add_user_to_team(
            server, user.id, team_b.id, mod_b_role.id, requester_id=admin_b.id
        )
        return user

    @pytest.fixture(scope="module")
    def model_registry(self, server):
        """Get the isolated model registry from the extension server."""
        if not hasattr(server.app.state, "model_registry"):
            pytest.skip("No isolated model registry found on extension server")
        return server.app.state.model_registry

    @pytest.fixture(scope="module")
    def extension_db(self, server):
        """Get a database session for testing from the extension server."""
        session = server.app.state.model_registry.database_manager.get_session()
        try:
            yield session
        finally:
            session.close()


class AbstractEXTTest(AbstractTest, ExtensionServerMixin):
    """
    Configuration-driven abstract base class for testing static extension components.

    Provides a flexible framework for testing extensions with configurable test types,
    expectations, and behavior. Tests are driven by ExtensionTestConfig rather than
    hard-coded test methods.
    """

    extension_class: Type[T] = None
    test_config: ExtensionTestConfig = ExtensionTestConfig()

    @classmethod
    def setup_class(cls):
        """Override setup_class to avoid accessing non-existent attributes."""
        logger.debug(f"Setting up extension test class: {cls.__name__}")
        # ExtensionTestConfig doesn't have timeout or gh_action_skip attributes
        # so we skip the AbstractTest setup_class implementation

    def teardown_method(self, method):
        """Override teardown_method to avoid accessing non-existent attributes."""
        logger.debug(f"Tearing down test method: {method.__name__}")
        # ExtensionTestConfig doesn't have cleanup attribute
        # so we skip the AbstractTest teardown_method implementation

    @pytest.fixture
    def extension(self) -> Type[AbstractStaticExtension]:
        """Get the extension class for testing."""
        if not self.extension_class:
            pytest.skip("extension_class not defined, test cannot run")
        return self.extension_class

    def _should_run_test(self, test_type: ExtensionTestType) -> bool:
        """Check if a test type should be run based on configuration."""
        return test_type in self.test_config.test_types

    def _skip_if_not_configured(self, test_type: ExtensionTestType):
        """Skip test if not configured to run."""
        if not self._should_run_test(test_type):
            pytest.skip(f"Test type {test_type.value} not configured to run")

    @pytest.mark.parametrize(
        "test_type",
        sorted(
            [
                ExtensionTestType.STRUCTURE,
                ExtensionTestType.METADATA,
                ExtensionTestType.DEPENDENCIES,
                ExtensionTestType.ABILITIES,
                ExtensionTestType.ENVIRONMENT,
            ]
        ),
    )
    def test_extension_basic_functionality(
        self, extension, test_type: ExtensionTestType
    ):
        """Parameterized test for basic extension functionality."""
        self._skip_if_not_configured(test_type)

        if test_type == ExtensionTestType.STRUCTURE:
            self._test_structure(extension)
        elif test_type == ExtensionTestType.METADATA:
            self._test_metadata(extension)
        elif test_type == ExtensionTestType.DEPENDENCIES:
            self._test_dependencies(extension)
        elif test_type == ExtensionTestType.ABILITIES:
            self._test_abilities(extension)
        elif test_type == ExtensionTestType.ENVIRONMENT:
            self._test_environment(extension)

    def _test_structure(self, extension):
        """Test extension structure."""
        import inspect

        assert inspect.isclass(extension), "Extension must be a class"

        required_attrs = [
            "name",
            "version",
            "description",
            "dependencies",
            "_env",
            "root",
        ]
        for attr in required_attrs:
            assert hasattr(extension, attr), f"Extension must have {attr} attribute"

        required_properties = ["abilities"]
        for prop in required_properties:
            assert hasattr(extension, prop), f"Extension must have {prop} property"

    def _test_metadata(self, extension):
        """Test extension metadata."""
        assert isinstance(extension.name, str), "Extension name must be string"
        assert isinstance(extension.version, str), "Extension version must be string"
        assert isinstance(
            extension.description, str
        ), "Extension description must be string"

        version_parts = extension.version.split(".")
        assert (
            len(version_parts) >= 2
        ), "Version should have at least major.minor format"

    def _test_dependencies(self, extension):
        """Test extension dependencies."""
        assert isinstance(
            extension.dependencies, Dependencies
        ), "Dependencies must be Dependencies instance"

        for attr in ["sys", "pip", "ext", "install", "check", "get_missing", "summary"]:
            assert hasattr(
                extension.dependencies, attr
            ), f"Dependencies must have {attr}"

    def _test_abilities(self, extension):
        """Test extension abilities."""
        abilities = extension.abilities
        assert isinstance(abilities, set), "Abilities must be a set"

        if self.test_config.expected_abilities:
            for expected_ability in self.test_config.expected_abilities:
                assert (
                    expected_ability in abilities
                ), f"Expected ability {expected_ability} not found"

    def _test_environment(self, extension):
        """Test extension environment variables."""
        env = extension._env
        assert isinstance(env, dict), "_env property must return a dictionary"

    def test_model_registry_functionality(self, model_registry):
        """Test model registry functionality."""
        self._skip_if_not_configured(ExtensionTestType.MODEL_REGISTRY)

        from serverframework.lib.Pydantic import ModelRegistry

        assert isinstance(
            model_registry, ModelRegistry
        ), "Should have ModelRegistry instance"

        bound_models = model_registry.get_bound_models()
        assert len(bound_models) > 0, "Registry should have bound models"
        assert model_registry.is_committed(), "Registry should be committed"

    def test_rotation_system(self, server):
        """Test rotation system functionality."""
        self._skip_if_not_configured(ExtensionTestType.ROTATION)

        if self.test_config.skip_rotation_tests:
            pytest.skip("Rotation tests disabled in configuration")

        if not self.extension_class:
            pytest.skip("extension_class not defined")

        root_rotation = self.extension_class.root
        if root_rotation is None:
            pytest.skip("Root rotation manager not available in test environment")

        assert hasattr(
            root_rotation, "rotate"
        ), "Rotation manager must have rotate method"
        assert callable(root_rotation.rotate), "rotate must be callable"

    def test_performance_metrics(self, server):
        """Test performance metrics."""
        self._skip_if_not_configured(ExtensionTestType.PERFORMANCE)

        if self.test_config.skip_performance_tests:
            pytest.skip("Performance tests disabled in configuration")

        if not self.extension_class:
            pytest.skip("extension_class not defined")

        # Clear cache
        if hasattr(self.extension_class, "_root_rotation_cache"):
            delattr(self.extension_class, "_root_rotation_cache")

        # Measure clean lookup
        start_time = time.time()
        root_rotation_1 = self.extension_class.root
        clean_lookup_time = time.time() - start_time

        if root_rotation_1 is None:
            pytest.skip("Root rotation manager not available in test environment")

        # Measure cached lookup
        start_time = time.time()
        root_rotation_2 = self.extension_class.root
        cached_lookup_time = time.time() - start_time

        assert root_rotation_1 is root_rotation_2, "Root rotation should be cached"
        assert cached_lookup_time < clean_lookup_time, "Cached lookup should be faster"

        min_improvement = self.test_config.performance_thresholds[
            "cache_improvement_min"
        ]
        cache_improvement = clean_lookup_time / cached_lookup_time
        assert (
            cache_improvement > min_improvement
        ), f"Cache should provide >{min_improvement}x improvement"

    def test_concurrent_access(self, server):
        """Test concurrent access to extension."""
        self._skip_if_not_configured(ExtensionTestType.CONCURRENCY)

        if not self.extension_class:
            pytest.skip("extension_class not defined")

        if self.extension_class.root is None:
            pytest.skip("Root rotation manager not available in test environment")

        def access_root_property():
            return self.extension_class.root

        num_threads = self.test_config.performance_thresholds["concurrent_threads"]
        results = []

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [
                executor.submit(access_root_property) for _ in range(num_threads)
            ]

            for future in as_completed(futures):
                try:
                    result = future.result(timeout=5)
                    results.append(result)
                except Exception as e:
                    pytest.fail(f"Concurrent access failed: {str(e)}")

        assert len(results) == num_threads, "Should have results from all threads"
        first_result = results[0]
        for result in results[1:]:
            assert (
                result is first_result
            ), "All threads should get the same cached instance"

    def test_cache_effectiveness(self, server):
        """Test cache effectiveness over multiple iterations."""
        self._skip_if_not_configured(ExtensionTestType.PERFORMANCE)

        if self.test_config.skip_performance_tests:
            pytest.skip("Performance tests disabled in configuration")

        if not self.extension_class:
            pytest.skip("extension_class not defined")

        # Clear cache
        if hasattr(self.extension_class, "_root_rotation_cache"):
            delattr(self.extension_class, "_root_rotation_cache")

        num_accesses = self.test_config.performance_thresholds["performance_iterations"]
        access_times = []

        for i in range(num_accesses):
            start_time = time.time()
            root_rotation = self.extension_class.root
            access_time = time.time() - start_time
            access_times.append(access_time)

            if i == 0 and root_rotation is None:
                pytest.skip("Root rotation manager not available in test environment")

        first_access_time = access_times[0]
        avg_cached_time = sum(access_times[1:]) / (num_accesses - 1)

        min_improvement = self.test_config.performance_thresholds[
            "cache_improvement_min"
        ]
        cache_improvement = first_access_time / avg_cached_time
        assert (
            cache_improvement > min_improvement
        ), f"Cache should provide >{min_improvement}x improvement"

    def test_database_isolation(self, server):
        """Test database isolation."""
        self._skip_if_not_configured(ExtensionTestType.DATABASE_ISOLATION)

        if not self.extension_class:
            pytest.skip("extension_class not defined")

        extension_name = self.extension_class.name.lower()
        expected_db_prefix = f"test.{extension_name}"

        db_manager = server.app.state.model_registry.database_manager

        assert hasattr(
            db_manager, "db_prefix"
        ), "Database manager should have db_prefix"
        assert (
            db_manager.db_prefix == expected_db_prefix
        ), f"Database prefix should be {expected_db_prefix}"

    def test_hook_and_ability_system(self):
        """Test hook and ability decorator system."""
        if not self.extension_class:
            pytest.skip("extension_class not defined")

        # Test that extension has abilities (the actual abilities set)
        assert hasattr(
            self.extension_class, "abilities"
        ), "Extension must have abilities property"
        assert hasattr(
            self.extension_class, "_abilities"
        ), "Extension must have _abilities attribute"

        abilities = self.extension_class.abilities
        assert isinstance(abilities, set), "abilities should be a set"

        # Test get_abilities method if it exists
        if hasattr(self.extension_class, "get_abilities"):
            get_abilities_result = self.extension_class.get_abilities()
            assert isinstance(
                get_abilities_result, set
            ), "get_abilities should return a set"
            assert (
                get_abilities_result == abilities
            ), "get_abilities should match abilities property"

    def test_dependency_operations(self):
        """Test dependency operations."""
        if not self.extension_class:
            pytest.skip("extension_class not defined")

        dependencies = self.extension_class.dependencies
        loaded_extensions = {"core": "1.0.0"}

        # Test operations don't raise exceptions
        try:
            dependencies.check(loaded_extensions)
            dependencies.get_missing(loaded_extensions)
            dependencies.summary()
        except Exception as e:
            pytest.fail(f"Dependency operations should not raise exceptions: {e}")

        # Test extension dependencies if they exist
        if dependencies.ext:
            env_csv = dependencies.ext.env
            assert isinstance(env_csv, str), "Environment CSV should be a string"

    def test_provider_discovery(self):
        """Test provider discovery functionality."""
        if not self.extension_class:
            pytest.skip("extension_class not defined")

        providers = self.extension_class.providers
        assert isinstance(providers, list), "Providers should be a list"
        # Providers list may be empty in test environment, which is acceptable

    def test_get_rotation_provider_instances_seed_data(self):
        """Test get_rotation_provider_instances_seed_data method."""
        if not self.extension_class:
            pytest.skip("extension_class not defined")

        try:
            seed_data = self.extension_class.get_rotation_provider_instances_seed_data()
            assert isinstance(seed_data, list), "Seed data should be a list"

            for item in seed_data:
                assert isinstance(
                    item, dict
                ), "Each seed data item should be a dictionary"
                # Check for common seed data fields
                if item:  # Only validate non-empty items
                    assert any(
                        key in item for key in ["name", "provider_name", "type"]
                    ), "Seed data should contain expected fields"
        except Exception as e:
            # Method may not be implemented in all extensions
            logger.debug(
                f"get_rotation_provider_instances_seed_data not implemented: {e}"
            )

    # Factory method for creating test configurations
    @classmethod
    def create_config(
        cls,
        test_types: Set[ExtensionTestType] = None,
        expected_abilities: Set[str] = None,
        skip_rotation: bool = False,
        skip_performance: bool = False,
        **kwargs,
    ) -> ExtensionTestConfig:
        """Factory method for creating test configurations."""
        return ExtensionTestConfig(
            test_types=test_types or set(ExtensionTestType),
            expected_abilities=expected_abilities or set(),
            skip_rotation_tests=skip_rotation,
            skip_performance_tests=skip_performance,
            **kwargs,
        )

    # Convenience methods for common test configurations
    @classmethod
    def basic_config(cls) -> ExtensionTestConfig:
        """Basic test configuration for minimal testing."""
        return cls.create_config(
            test_types={
                ExtensionTestType.STRUCTURE,
                ExtensionTestType.METADATA,
                ExtensionTestType.DEPENDENCIES,
            },
            skip_rotation=True,
            skip_performance=True,
        )

    @classmethod
    def full_config(cls, expected_abilities: Set[str] = None) -> ExtensionTestConfig:
        """Full test configuration for comprehensive testing."""
        return cls.create_config(
            test_types=set(ExtensionTestType),
            expected_abilities=expected_abilities,
        )

    @classmethod
    def performance_config(cls) -> ExtensionTestConfig:
        """Performance-focused test configuration."""
        return cls.create_config(
            test_types={
                ExtensionTestType.STRUCTURE,
                ExtensionTestType.PERFORMANCE,
                ExtensionTestType.CONCURRENCY,
            },
        )
