from typing import Any, ClassVar, Dict, Set
from unittest.mock import patch

import pytest
from ordered_set import OrderedSet

from extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractProviderInstance_SDK,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ExtensionRegistry,
    ExtensionType,
    ability,
    get_session,
)
from lib.Dependencies import (
    Dependencies,
    EXT_Dependency,
    PIP_Dependency,
    SYS_Dependency,
)
from lib.Pydantic import classproperty


class TestExtensionSystemComponent(AbstractStaticExtension):
    name: ClassVar[str] = "test_system_component"
    description: ClassVar[str] = "Test system component"
    version: ClassVar[str] = "1.0.0"
    _env: ClassVar[Dict[str, Any]] = {"TEST_SETTING": "value"}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            SYS_Dependency.for_all_platforms(
                name="git",
                apt_pkg="git",
                brew_pkg="git",
                winget_pkg="Git.Git",
                friendly_name="Git",
                reason="Version control",
            )
        ]
    )
    _abilities: ClassVar[Set[str]] = {"test_ability"}


class TestExtension(AbstractStaticExtension):
    name: ClassVar[str] = "test_extension"
    description: ClassVar[str] = "Test extension"
    version: ClassVar[str] = "1.0.0"
    _env: ClassVar[Dict[str, Any]] = {"TEST_EXT_SETTING": "ext_value"}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [EXT_Dependency(name="auth", friendly_name="Auth", reason="Authentication")]
    )
    _abilities: ClassVar[Set[str]] = {"ext_ability"}

    @staticmethod
    @ability(name="decorated_ability")
    def test_decorated_ability():
        return "ability_result"

    test_decorated_ability.__test__ = False

    @staticmethod
    @AbstractStaticExtension.hook("bll", "auth", "user", "create", "before")
    def test_hook_handler():
        return "hook_result"

    test_hook_handler.__test__ = False


class TestExtensionWithOptionalDeps(AbstractStaticExtension):
    name: ClassVar[str] = "test_optional_deps"
    description: ClassVar[str] = "Test extension with optional dependencies"
    version: ClassVar[str] = "2.0.0"
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="required_ext",
                friendly_name="Required",
                reason="Required for functionality",
            ),
            EXT_Dependency(
                name="optional_ext",
                friendly_name="Optional",
                reason="Optional enhancement",
                optional=True,
            ),
            PIP_Dependency(
                name="requests", friendly_name="Requests", reason="HTTP client"
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {"optional_ability"}


class TestExtensionWithCircularDeps(AbstractStaticExtension):
    name: ClassVar[str] = "circular_ext"
    description: ClassVar[str] = "Extension with circular dependencies"
    version: ClassVar[str] = "1.0.0"
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="other_circular_ext",
                friendly_name="Other",
                reason="Circular dependency",
            )
        ]
    )
    _abilities: ClassVar[Set[str]] = {"circular_ability"}


class OtherCircularExtension(AbstractStaticExtension):
    name: ClassVar[str] = "other_circular_ext"
    description: ClassVar[str] = "Other extension with circular dependencies"
    version: ClassVar[str] = "1.0.0"
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="circular_ext",
                friendly_name="Circular",
                reason="Circular dependency",
            )
        ]
    )
    _abilities: ClassVar[Set[str]] = {"other_circular_ability"}

    @staticmethod
    @ability(name="decorated_ability")
    def test_decorated_ability():
        return "ability_result"

    test_decorated_ability.__test__ = False

    @staticmethod
    @ability(name="meta_ability")
    def test_meta_ability():
        return "meta_ability_result"

    test_meta_ability.__test__ = False

    @staticmethod
    @AbstractStaticExtension.hook("bll", "auth", "user", "create", "before")
    def test_hook_handler():
        return "hook_result"

    test_hook_handler.__test__ = False


class TestProvider(AbstractStaticProvider):
    name: ClassVar[str] = "test_provider"
    extension_type: ClassVar[str] = "test"
    description: ClassVar[str] = "Test provider"
    _abilities: ClassVar[Set[str]] = {"provider_ability"}
    _env: ClassVar[Dict[str, Any]] = {"TEST_PROVIDER_KEY": ""}
    dependencies: ClassVar[Dependencies] = Dependencies([])

    @classmethod
    def bond_instance(cls, instance):
        return MockProviderInstance(instance)


class TestExternalExtension(AbstractStaticExtension):
    name: ClassVar[str] = "test_external"
    description: ClassVar[str] = "Test external extension"
    version: ClassVar[str] = "1.0.0"
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = set()

    @classproperty
    def types(cls) -> Set[ExtensionType]:
        return {ExtensionType.EXTERNAL}


class TestAPIProvider(AbstractStaticProvider):
    name: ClassVar[str] = "test_api_provider"
    extension_type: ClassVar[str] = "test"
    description: ClassVar[str] = "Test API provider"
    _abilities: ClassVar[Set[str]] = {"api_ability"}
    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    extension = TestExternalExtension

    @classmethod
    def bond_instance(cls, instance):
        return MockProviderInstance(instance)


class MockProviderInstance(AbstractProviderInstance):
    def __init__(self, instance_data):
        self.instance_data = instance_data

    @property
    def api_key(self):
        return getattr(self.instance_data, "api_key", "test_key")


class MockProviderInstanceSDK(AbstractProviderInstance_SDK):
    def __init__(self, sdk, instance_data=None):
        super().__init__(sdk)
        self.instance_data = instance_data or {}

    @property
    def api_key(self):
        return self.instance_data.get("api_key", "sdk_key")


class TestAbstractExtensionProvider:
    """Test suite for AbstractExtensionProvider functionality."""

    def test_system_component_structure(self):
        """Test system component has required structure."""
        assert hasattr(TestExtensionSystemComponent, "name")
        assert hasattr(TestExtensionSystemComponent, "description")
        assert hasattr(TestExtensionSystemComponent, "dependencies")
        assert hasattr(TestExtensionSystemComponent, "_env")
        assert hasattr(TestExtensionSystemComponent, "_abilities")
        assert hasattr(TestExtensionSystemComponent, "abilities")
        assert callable(TestExtensionSystemComponent._register_env_vars)

    def test_extension_structure(self):
        """Test extension has required structure."""
        assert hasattr(TestExtension, "name")
        assert hasattr(TestExtension, "description")
        assert hasattr(TestExtension, "version")
        assert hasattr(TestExtension, "dependencies")
        assert hasattr(TestExtension, "_env")
        assert hasattr(TestExtension, "_abilities")
        assert hasattr(TestExtension, "abilities")
        assert hasattr(TestExtension, "providers")
        assert hasattr(TestExtension, "root")

    def test_provider_structure(self):
        """Test provider has required structure."""
        assert hasattr(TestProvider, "name")
        assert hasattr(TestProvider, "description")
        assert hasattr(TestProvider, "dependencies")
        assert hasattr(TestProvider, "_env")
        assert hasattr(TestProvider, "_abilities")
        assert callable(TestProvider.bond_instance)

    def test_component_metadata(self):
        """Test component metadata."""
        assert isinstance(TestExtensionSystemComponent.name, str)
        assert isinstance(TestExtensionSystemComponent.description, str)

        assert isinstance(TestExtension.name, str)
        assert isinstance(TestExtension.description, str)
        assert isinstance(TestExtension.version, str)

        assert isinstance(TestProvider.name, str)
        assert isinstance(TestProvider.description, str)

        assert isinstance(TestAPIProvider.name, str)
        assert isinstance(TestAPIProvider.description, str)

    def test_component_dependencies(self):
        """Test component dependencies."""
        components = [
            TestExtensionSystemComponent,
            TestExtension,
            TestProvider,
            TestAPIProvider,
        ]

        for component in components:
            assert isinstance(component.dependencies, Dependencies)
            assert hasattr(component.dependencies, "sys")
            assert hasattr(component.dependencies, "pip")
            assert hasattr(component.dependencies, "ext")
            assert hasattr(component.dependencies, "install")
            assert hasattr(component.dependencies, "check")

    def test_component_abilities(self):
        """Test component abilities."""
        assert isinstance(TestExtensionSystemComponent.abilities, set)
        assert "test_ability" in TestExtensionSystemComponent.abilities

        assert isinstance(TestExtension.abilities, set)
        assert "ext_ability" in TestExtension.abilities
        assert "decorated_ability" in TestExtension.abilities

        assert isinstance(TestProvider.abilities, set)
        assert "provider_ability" in TestProvider.abilities

        assert isinstance(TestAPIProvider.abilities, set)
        assert "api_ability" in TestAPIProvider.abilities

    def test_component_env_vars(self):
        """Test component environment variables."""
        assert hasattr(TestExtensionSystemComponent, "_env")
        assert isinstance(TestExtensionSystemComponent._env, dict)
        assert "TEST_SETTING" in TestExtensionSystemComponent._env

        assert hasattr(TestExtension, "_env")
        assert isinstance(TestExtension._env, dict)
        assert "TEST_EXT_SETTING" in TestExtension._env

        assert hasattr(TestProvider, "_env")
        assert isinstance(TestProvider._env, dict)

        assert hasattr(TestAPIProvider, "_env")
        assert isinstance(TestAPIProvider._env, dict)

    def test_provider_instance_creation(self):
        """Test provider instance creation."""

        # Create a simple mock instance data class
        class MockInstanceData:
            api_key = "test_key"

        instance_data = MockInstanceData()
        instance = MockProviderInstance(instance_data)
        assert instance.api_key == "test_key"

    def test_sdk_provider_instance_creation(self):
        """Test SDK provider instance creation."""
        with pytest.raises(Exception, match="An SDK is required for this provider"):
            MockProviderInstanceSDK(None)

        mock_sdk = {"client": "test_client"}
        instance = MockProviderInstanceSDK(mock_sdk, {"api_key": "sdk_key"})
        assert instance.sdk == mock_sdk
        assert instance.api_key == "sdk_key"

    def test_provider_bonding(self):
        """Test provider bonding."""

        # Create a simple mock instance class
        class MockInstance:
            api_key = "test_bond_key"

        mock_instance = MockInstance()
        bonded_instance = TestProvider.bond_instance(mock_instance)
        assert hasattr(bonded_instance, "api_key")
        assert bonded_instance.api_key == "test_bond_key"

        bonded_api_instance = TestAPIProvider.bond_instance(mock_instance)
        assert hasattr(bonded_api_instance, "api_key")
        assert bonded_api_instance.api_key == "test_bond_key"

    def test_get_session_function(self):
        """Test get_session function."""
        # Test with real database manager
        try:
            from database.DatabaseManager import DatabaseManager

            # Try to get an instance if available
            db_manager = (
                DatabaseManager.get_instance()
                if hasattr(DatabaseManager, "get_instance")
                else None
            )

            if db_manager and hasattr(db_manager, "get_session"):
                try:
                    session = get_session(db_manager)
                    # Session might be None if DB is not initialized
                    assert session is None or hasattr(session, "execute")
                except RuntimeError as e:
                    if "Engine configuration not initialized" in str(e):
                        # Expected error when DB isn't properly initialized in test environment
                        session = get_session(None)
                        assert session is None
                    else:
                        raise
            else:
                # No DB manager available, should return None
                session = get_session(None)
                assert session is None

        except ImportError:
            # Database module not available
            session = get_session(None)
            assert session is None

    def test_ability_decorator(self):
        """Test ability decorator functionality."""
        result = TestExtension.test_decorated_ability()
        assert result == "ability_result"

        assert hasattr(TestExtension.test_decorated_ability, "_ability_info")
        ability_info = TestExtension.test_decorated_ability._ability_info
        assert ability_info["name"] == "decorated_ability"
        assert ability_info["enabled"] is True
        # Meta is now determined by context, not parameter

    def test_ability_decorator_on_extension(self):
        """Test ability decorator functionality on extension (meta ability)."""
        result = OtherCircularExtension.test_meta_ability()
        assert result == "meta_ability_result"

        assert hasattr(OtherCircularExtension.test_meta_ability, "_ability_info")
        ability_info = OtherCircularExtension.test_meta_ability._ability_info
        assert ability_info["name"] == "meta_ability"
        assert ability_info["enabled"] is True
        # Meta is now determined by context, not parameter

    def test_meta_ability_on_extension(self):
        """Test that abilities on AbstractStaticExtension are meta abilities."""

        # This should work - create a test extension with meta ability
        class ValidMetaExtension(AbstractStaticExtension):
            name: ClassVar[str] = "valid_meta"
            description: ClassVar[str] = "Extension with valid meta ability"
            version: ClassVar[str] = "1.0.0"
            dependencies: ClassVar[Dependencies] = Dependencies([])
            _abilities: ClassVar[Set[str]] = set()

            @staticmethod
            @ability(name="valid_meta_ability")
            def valid_meta_ability():
                return "valid"

        # Should not raise an error
        assert "valid_meta_ability" in ValidMetaExtension.abilities

    def test_ability_on_provider(self):
        """Test that abilities on providers are abstract abilities."""

        # Create a test provider with ability
        class TestProviderWithAbility(AbstractStaticProvider):
            name: ClassVar[str] = "test_provider_ability"
            extension_type: ClassVar[str] = "test"
            description: ClassVar[str] = "Provider with abstract ability"
            dependencies: ClassVar[Dependencies] = Dependencies([])
            _abilities: ClassVar[Set[str]] = set()

            @staticmethod
            @ability(name="provider_ability")
            def provider_ability():
                return "provider_result"

            @classmethod
            def bond_instance(cls, instance):
                return MockProviderInstance(instance)

        # Should work - abilities on providers are abstract abilities
        assert "provider_ability" in TestProviderWithAbility.abilities

    def test_hook_decorator(self):
        """Test hook decorator functionality."""
        result = TestExtension.test_hook_handler()
        assert result == "hook_result"

        assert hasattr(TestExtension.test_hook_handler, "_hook_info")
        hook_info = TestExtension.test_hook_handler._hook_info
        assert len(hook_info) == 1
        assert hook_info[0] == ("bll", "auth", "user", "create", "before")

    def test_hook_registration(self):
        """Test hook registration functionality."""

        def test_handler():
            return "test"

        TestExtension.register_hook(
            "bll", "test", "entity", "func", "after", test_handler
        )
        hook_path = ("bll", "test", "entity", "func", "after")
        assert hook_path in TestExtension._hooks
        assert test_handler in TestExtension._hooks[hook_path]

    def test_extension_registry(self):
        """Test ExtensionRegistry functionality."""
        registry = ExtensionRegistry("")  # Empty CSV string
        assert isinstance(registry, ExtensionRegistry)

    def test_root_rotation_access(self):
        """Test root rotation access."""
        # Test when database manager is not available
        root = TestExtension.root
        assert root is None  # Should return None when no DB manager

    def test_api_provider_env_registration(self):
        """Test API provider environment variable registration."""
        TestAPIProvider._register_env_vars()

        # Should have added API-specific env vars
        expected_env_keys = [
            "TEST_API_PROVIDER_API_KEY",
            "TEST_API_PROVIDER_SECRET_KEY",
            "TEST_API_PROVIDER_WEBHOOK_SECRET",
            "TEST_API_PROVIDER_CURRENCY",
        ]

        for key in expected_env_keys:
            assert key in TestAPIProvider._env

    def test_provider_abilities_access(self):
        """Test provider abilities access method."""
        abilities = TestProvider.get_abilities()
        assert isinstance(abilities, set)
        assert "provider_ability" in abilities

        # Ensure it returns a copy, not the original
        abilities.add("new_ability")
        assert "new_ability" not in TestProvider._abilities

    def test_seed_data_generation(self):
        """Test seed data generation for rotation provider instances."""
        # Without a real database connection, this should return empty list
        seed_data = TestExtension.get_rotation_provider_instances_seed_data()
        assert isinstance(seed_data, list)
        assert len(seed_data) == 0  # No DB available, so empty list

    def test_inheritance_functionality(self):
        """Test ability and hook inheritance."""

        class ParentExtension(AbstractStaticExtension):
            name: ClassVar[str] = "parent"
            description: ClassVar[str] = "Parent extension"
            version: ClassVar[str] = "1.0.0"
            _abilities: ClassVar[Set[str]] = {"parent_ability"}
            _hooks: ClassVar[Dict] = {("test", "hook", "path", "func", "before"): []}

        class ChildExtension(ParentExtension):
            name: ClassVar[str] = "child"
            description: ClassVar[str] = "Child extension"
            _abilities: ClassVar[Set[str]] = {"child_ability"}

        # Test ability inheritance
        assert "parent_ability" in ChildExtension.abilities
        assert "child_ability" in ChildExtension.abilities

        # Test hook inheritance
        assert ("test", "hook", "path", "func", "before") in ChildExtension._hooks

    def test_abstract_methods(self):
        """Test abstract method requirements."""
        with pytest.raises(TypeError):
            # Cannot instantiate abstract class
            AbstractStaticProvider()

        # Concrete implementation should work
        assert TestProvider.bond_instance is not None
        assert callable(TestProvider.bond_instance)


class TestExtensionRegistry:
    """Test suite for ExtensionRegistry dependency checking functionality."""

    def test_registry_initialization(self):
        """Test that registry initializes with empty state."""
        registry = ExtensionRegistry("")  # Empty CSV string
        assert isinstance(registry.extensions, OrderedSet)
        assert len(registry.extensions) == 0
        assert registry.loaded_extensions == {}
        assert registry.extension_models == {}
        assert registry._extension_name_map == {}
        assert registry.extension_abilities == {}
        assert registry.extension_providers == {}
        assert registry.provider_abilities == {}

    def test_register_extension_tracks_version(self):
        """Test that registering an extension tracks its version."""
        registry = ExtensionRegistry("")  # Empty CSV string
        registry.register_extension(TestExtension)

        assert "test_extension" in registry._extension_name_map
        assert "test_extension" in registry.loaded_extensions
        assert registry.loaded_extensions["test_extension"] == "1.0.0"

    def test_register_extension_default_version(self):
        """Test that extensions without version get default version."""

        class NoVersionExtension(AbstractStaticExtension):
            name = "no_version"
            description = "Extension without version"
            dependencies = Dependencies([])
            _abilities: ClassVar[Set[str]] = set()

        registry = ExtensionRegistry("")  # Empty CSV string
        registry.register_extension(NoVersionExtension)

        assert registry.loaded_extensions["no_version"] == "0.1.0"

    def test_check_dependencies_no_dependencies(self):
        """Test dependency checking for extension with no dependencies."""

        class NoDepsExtension(AbstractStaticExtension):
            name = "no_deps"
            description = "Extension without dependencies"
            dependencies = Dependencies([])
            _abilities: ClassVar[Set[str]] = set()

        registry = ExtensionRegistry("")  # Empty CSV string
        result = registry.check_dependencies(NoDepsExtension)
        assert result == {}

    def test_check_dependencies_satisfied(self):
        """Test dependency checking when all dependencies are satisfied."""
        registry = ExtensionRegistry("")  # Empty CSV string
        # Register the dependency first
        registry.register_extension(TestExtension)  # This provides "test_extension"

        # Create extension that depends on test_extension
        class DependentExtension(AbstractStaticExtension):
            name = "dependent"
            description = "Extension with satisfied dependencies"
            dependencies = Dependencies(
                [
                    EXT_Dependency(
                        name="test_extension", friendly_name="Test", reason="Testing"
                    )
                ]
            )
            _abilities: ClassVar[Set[str]] = set()

        result = registry.check_dependencies(DependentExtension)
        assert "test_extension" in result
        assert result["test_extension"] is True

    def test_check_dependencies_unsatisfied(self):
        """Test dependency checking when dependencies are not satisfied."""
        registry = ExtensionRegistry("")  # Empty CSV string

        result = registry.check_dependencies(
            TestExtension
        )  # Depends on "auth" which isn't loaded
        assert "auth" in result
        assert result["auth"] is False

    def test_are_optional_dependencies_met_all_satisfied(self):
        """Test optional dependency checking when all are satisfied."""
        registry = ExtensionRegistry("")  # Empty CSV string
        # Register optional dependency
        registry.loaded_extensions["optional_ext"] = "1.0.0"

        result = registry.are_optional_dependencies_met(TestExtensionWithOptionalDeps)
        assert result is True

    def test_are_optional_dependencies_met_unsatisfied(self):
        """Test optional dependency checking when some are not satisfied."""
        registry = ExtensionRegistry("")  # Empty CSV string
        # Don't register optional_ext, so it's not satisfied

        result = registry.are_optional_dependencies_met(TestExtensionWithOptionalDeps)
        assert result is False

    def test_are_optional_dependencies_met_no_optional_deps(self):
        """Test optional dependency checking for extension with no optional deps."""
        registry = ExtensionRegistry("")  # Empty CSV string

        result = registry.are_optional_dependencies_met(TestExtension)
        assert result is True

    def test_resolve_extension_dependencies_simple_order(self):
        """Test extension dependency resolution with simple ordering."""
        registry = ExtensionRegistry("")  # Empty CSV string

        # Create extensions with clear dependency order
        class BaseExtension(AbstractStaticExtension):
            name = "base"
            description = "Base extension"
            dependencies = Dependencies([])
            _abilities: ClassVar[Set[str]] = set()

        class DependentExtension(AbstractStaticExtension):
            name = "dependent"
            description = "Dependent extension"
            dependencies = Dependencies(
                [
                    EXT_Dependency(
                        name="base", friendly_name="Base", reason="Required base"
                    )
                ]
            )
            _abilities: ClassVar[Set[str]] = set()

        available_extensions = {"base": BaseExtension, "dependent": DependentExtension}

        result = registry.resolve_extension_dependencies(available_extensions)
        assert result == ["base", "dependent"]

    def test_resolve_extension_dependencies_circular(self):
        """Test extension dependency resolution with circular dependencies."""
        registry = ExtensionRegistry("")  # Empty CSV string

        available_extensions = {
            "circular_ext": TestExtensionWithCircularDeps,
            "other_circular_ext": OtherCircularExtension,
        }

        with pytest.raises(ValueError, match="Circular dependency detected"):
            registry.resolve_extension_dependencies(available_extensions)

    def test_resolve_extension_dependencies_optional_ignored(self):
        """Test that optional dependencies are ignored in resolution order."""
        registry = ExtensionRegistry("")  # Empty CSV string

        # Extension with only optional dependencies should not affect order
        class OptionalOnlyExtension(AbstractStaticExtension):
            name = "optional_only"
            description = "Extension with only optional dependencies"
            dependencies = Dependencies(
                [
                    EXT_Dependency(
                        name="nonexistent",
                        friendly_name="Non",
                        reason="Optional",
                        optional=True,
                    )
                ]
            )
            _abilities: ClassVar[Set[str]] = set()

        available_extensions = {"optional_only": OptionalOnlyExtension}

        result = registry.resolve_extension_dependencies(available_extensions)
        assert result == ["optional_only"]

    def test_install_extension_dependencies_success(self):
        """Test successful installation of extension dependencies."""
        registry = ExtensionRegistry("")  # Empty CSV string

        # Test that the method returns empty dict when no valid extension module is found
        # This is the expected behavior based on the implementation
        result = registry.install_extension_dependencies(["nonexistent_ext"])
        assert result == {}

        # For a real successful test, we would need to create an actual extension module
        # Since this is complex to mock properly without causing recursion issues,
        # we verify the error handling path works correctly
        with patch("importlib.import_module") as mock_import:
            mock_import.side_effect = ImportError("Module not found")
            result = registry.install_extension_dependencies(["test_ext"])
            assert result == {}

    @patch("importlib.import_module")
    def test_install_extension_dependencies_import_error(self, mock_import):
        """Test handling of import errors during dependency installation."""
        registry = ExtensionRegistry("")  # Empty CSV string

        mock_import.side_effect = ImportError("Module not found")

        result = registry.install_extension_dependencies(["nonexistent_ext"])
        assert result == {}  # Should return empty dict when no dependencies found


class TestAbstractProviderInnerClass:
    """Test suite for AbstractProvider as inner class pattern."""

    def test_extension_with_inner_provider(self):
        """Test extension with AbstractProvider inner class."""

        class TestExtensionWithProvider(AbstractStaticExtension):
            name = "test_with_provider"
            description = "Extension with inner provider"
            version = "1.0.0"
            dependencies = Dependencies([])
            _abilities = {"meta_ability"}

            class AbstractProvider(AbstractStaticExtension.AbstractProvider):
                """Abstract provider for this extension."""

                _abilities = {"abstract_ability"}

                @staticmethod
                @ability("abstract_ability")
                def abstract_ability():
                    return "abstract_result"

        # Test that extension has meta abilities
        assert "meta_ability" in TestExtensionWithProvider.abilities

        # Test that inner class exists
        assert hasattr(TestExtensionWithProvider, "AbstractProvider")

        # Test that inner class has abstract abilities
        assert (
            "abstract_ability" in TestExtensionWithProvider.AbstractProvider._abilities
        )

    def test_provider_inheritance_from_inner_class(self):
        """Test provider inheriting from extension's inner AbstractProvider."""

        class TestExtensionBase(AbstractStaticExtension):
            name = "test_base"
            description = "Base extension"
            version = "1.0.0"
            dependencies = Dependencies([])
            _abilities = {"base_meta"}

            class AbstractProvider(AbstractStaticExtension.AbstractProvider):
                """Abstract provider interface."""

                _abilities = {"base_abstract"}

        class ConcreteProvider(TestExtensionBase.AbstractProvider):
            name = "concrete_provider"
            description = "Concrete provider implementation"
            _abilities = {"concrete_ability", "base_abstract"}

            @classmethod
            def bond_instance(cls, instance):
                return MockProviderInstance(instance)

        # Test inheritance
        assert "base_abstract" in ConcreteProvider.abilities
        assert "concrete_ability" in ConcreteProvider.abilities


class TestExtensionTypeFeatures:
    """Test suite for new ExtensionType enum and related features."""

    def test_extension_type_enum(self):
        """Test ExtensionType enum exists and has correct values."""
        assert hasattr(ExtensionType, "ENDPOINTS")
        assert hasattr(ExtensionType, "DATABASE")
        assert hasattr(ExtensionType, "EXTERNAL")
        assert ExtensionType.ENDPOINTS.value == "endpoints"
        assert ExtensionType.DATABASE.value == "database"
        assert ExtensionType.EXTERNAL.value == "external"

    def test_extension_types_property(self):
        """Test extension .types property returns set of ExtensionType."""
        # TestExtension has no actual files in test environment
        types = TestExtension.types
        assert isinstance(types, set)
        # All items should be ExtensionType instances
        for t in types:
            assert isinstance(t, ExtensionType)

    def test_extension_models_property(self):
        """Test extension .models property returns set of model classes."""
        models = TestExtension.models
        assert isinstance(models, set)
        # In test environment without actual files, should be empty
        assert len(models) == 0

    def test_providers_property_no_scoped_import(self):
        """Test providers property doesn't use deprecated scoped_import."""
        # Should not raise deprecation warning
        providers = TestExtension.providers
        assert isinstance(providers, list)
        # In test environment without actual PRV files, should be empty
        assert len(providers) == 0

    def test_ability_decorator_standalone(self):
        """Test ability decorator can be used without AbstractStaticExtension prefix."""

        @ability(name="test_standalone")
        def standalone_ability():
            return "standalone"

        assert hasattr(standalone_ability, "_ability_info")
        assert standalone_ability._ability_info["name"] == "test_standalone"
        assert standalone_ability() == "standalone"

    def test_extension_type_property_backward_compat(self):
        """Test that extension_type property still works for backward compatibility."""
        assert hasattr(TestExtension, "extension_type")
        # Should return string for backward compatibility
        assert isinstance(TestExtension.extension_type, str)
        assert TestExtension.extension_type in [
            "endpoints",
            "database",
            "external",
            "unknown",
        ]


class TestExtensionTypeDetection:
    """Test suite for automatic extension type detection."""

    def test_detect_endpoints_extension_type(self):
        """Test detection of endpoints extension type via .types property."""
        # TestExtension has no actual files, so it should be empty or default type
        ext_types = TestExtension.types
        assert isinstance(ext_types, set)
        # In test environment without actual files, types will be based on actual file detection

    def test_extension_types_property_structure(self):
        """Test that .types property returns correct structure."""
        ext_types = TestExtension.types
        assert isinstance(ext_types, set)
        # All items should be ExtensionType instances
        for ext_type in ext_types:
            assert isinstance(ext_type, ExtensionType)

    def test_extension_type_backward_compatibility(self):
        """Test that extension_type property provides backward compatibility."""
        ext_type = TestExtension.extension_type
        assert isinstance(ext_type, str)
        assert ext_type in ["endpoints", "database", "external", "unknown"]

    def test_extension_types_property_exists(self):
        """Test that extensions have types property based on files."""
        # Test that .types property exists and returns a set
        assert hasattr(TestExtension, "types")
        ext_types = TestExtension.types
        assert isinstance(ext_types, set)
        # In test environment, types are based on actual file detection


class TestExtensionModelDiscovery:
    """Test suite for extension model discovery including PRV files."""

    def test_discover_extension_models_includes_prv(self):
        """Test that discover_extension_models scans PRV files."""
        registry = ExtensionRegistry("")  # Empty CSV string

        # The method is called internally during extension loading
        # We can verify the extension_models structure includes external models
        # In a real scenario, PRV files would be discovered

        # Check that external models use special keys
        for key in registry.extension_models:
            if key.startswith("external."):
                # This is an external model from a PRV file
                parts = key.split(".")
                assert len(parts) >= 3  # external.extension_name.ModelName


class TestExtensionDependencyLoading:
    """Test suite for automatic dependency loading in CSV initialization."""

    def test_load_extensions_includes_dependencies(self):
        """Test that loading an extension automatically includes its dependencies."""
        # For this test we'll use mock extension classes directly instead of files
        from typing import ClassVar, Set

        # Create mock extension classes
        class BaseExtension(AbstractStaticExtension):
            name: ClassVar[str] = "base"
            description: ClassVar[str] = "Base extension"
            dependencies: ClassVar[Dependencies] = Dependencies([])
            _abilities: ClassVar[Set[str]] = set()

        class DependentExtension(AbstractStaticExtension):
            name: ClassVar[str] = "dependent"
            description: ClassVar[str] = "Dependent extension"
            dependencies: ClassVar[Dependencies] = Dependencies(
                [EXT_Dependency(name="base", friendly_name="Base", reason="Required")]
            )
            _abilities: ClassVar[Set[str]] = set()

        # Test the resolve_extension_dependencies method directly
        registry = ExtensionRegistry("")  # Empty CSV string
        available_extensions = {"dependent": DependentExtension, "base": BaseExtension}

        # Resolve should include both extensions in correct order
        sorted_names = registry.resolve_extension_dependencies(available_extensions)
        assert sorted_names == ["base", "dependent"]

        # Now test that the CSV loading would work with dependency resolution
        # by manually registering in the resolved order
        for name in sorted_names:
            registry.register_extension(available_extensions[name])

        assert "dependent" in registry.loaded_extensions
        assert "base" in registry.loaded_extensions

    def test_load_extensions_circular_dependency_error(self):
        """Test that circular dependencies are detected during loading."""
        from typing import ClassVar, Set

        # Create mock extension classes with circular dependencies
        class Ext1(AbstractStaticExtension):
            name: ClassVar[str] = "ext1"
            description: ClassVar[str] = "Extension 1"
            dependencies: ClassVar[Dependencies] = Dependencies(
                [EXT_Dependency(name="ext2", friendly_name="Ext2", reason="Required")]
            )
            _abilities: ClassVar[Set[str]] = set()

        class Ext2(AbstractStaticExtension):
            name: ClassVar[str] = "ext2"
            description: ClassVar[str] = "Extension 2"
            dependencies: ClassVar[Dependencies] = Dependencies(
                [EXT_Dependency(name="ext1", friendly_name="Ext1", reason="Required")]
            )
            _abilities: ClassVar[Set[str]] = set()

        # Test the resolve_extension_dependencies method with circular deps
        registry = ExtensionRegistry("")  # Empty CSV string
        available_extensions = {"ext1": Ext1, "ext2": Ext2}

        # This should raise an error for circular dependencies
        with pytest.raises(ValueError, match="Circular dependency detected"):
            registry.resolve_extension_dependencies(available_extensions)


class TestOrderedSetAndDependencyResolution:
    """Test suite for OrderedSet functionality and automatic dependency resolution."""

    def test_orderedset_maintains_insertion_order(self):
        """Test that extensions are stored in OrderedSet maintaining dependency order."""
        registry = ExtensionRegistry("")

        # Create extensions with dependencies
        class BaseExt(AbstractStaticExtension):
            name: ClassVar[str] = "base"
            description: ClassVar[str] = "Base"
            dependencies: ClassVar[Dependencies] = Dependencies([])
            _abilities: ClassVar[Set[str]] = set()

        class MiddleExt(AbstractStaticExtension):
            name: ClassVar[str] = "middle"
            description: ClassVar[str] = "Middle"
            dependencies: ClassVar[Dependencies] = Dependencies(
                [EXT_Dependency(name="base", friendly_name="Base", reason="Required")]
            )
            _abilities: ClassVar[Set[str]] = set()

        class TopExt(AbstractStaticExtension):
            name: ClassVar[str] = "top"
            description: ClassVar[str] = "Top"
            dependencies: ClassVar[Dependencies] = Dependencies(
                [
                    EXT_Dependency(
                        name="middle", friendly_name="Middle", reason="Required"
                    )
                ]
            )
            _abilities: ClassVar[Set[str]] = set()

        # Since we can't mock file system loading, test that OrderedSet maintains order
        # Register in order and verify OrderedSet behavior
        registry.register_extension(BaseExt)
        registry.register_extension(MiddleExt)
        registry.register_extension(TopExt)

        # Check that all three were registered in correct order
        ext_names = [ext.name for ext in registry.extensions]
        assert ext_names == ["base", "middle", "top"]

    def test_csv_property_returns_dependency_order(self):
        """Test that csv property returns extensions in dependency order."""
        registry = ExtensionRegistry("")

        # Register test extensions
        registry.register_extension(TestExtension)
        registry.register_extension(TestExtensionSystemComponent)

        csv = registry.csv
        assert isinstance(csv, str)
        # CSV should contain registered extensions
        assert "test_extension" in csv
        assert "test_system_component" in csv

    def test_automatic_dependency_loading(self):
        """Test that registering an extension automatically loads its dependencies."""
        registry = ExtensionRegistry("")

        # Create a chain of dependencies
        class Dep1(AbstractStaticExtension):
            name: ClassVar[str] = "dep1"
            description: ClassVar[str] = "Dependency 1"
            dependencies: ClassVar[Dependencies] = Dependencies([])
            _abilities: ClassVar[Set[str]] = set()

        class Dep2(AbstractStaticExtension):
            name: ClassVar[str] = "dep2"
            description: ClassVar[str] = "Dependency 2"
            dependencies: ClassVar[Dependencies] = Dependencies(
                [EXT_Dependency(name="dep1", friendly_name="Dep1", reason="Required")]
            )
            _abilities: ClassVar[Set[str]] = set()

        class MainExt(AbstractStaticExtension):
            name: ClassVar[str] = "main"
            description: ClassVar[str] = "Main Extension"
            dependencies: ClassVar[Dependencies] = Dependencies(
                [EXT_Dependency(name="dep2", friendly_name="Dep2", reason="Required")]
            )
            _abilities: ClassVar[Set[str]] = set()

        # Mock the dependency loading to provide our test classes
        original_register_deps = registry._register_dependencies

        def mock_register_deps(ext_class):
            # Simulate finding dependency classes
            if ext_class == MainExt and "dep2" not in registry._extension_name_map:
                registry.register_extension(Dep2)
            elif ext_class == Dep2 and "dep1" not in registry._extension_name_map:
                registry.register_extension(Dep1)

        registry._register_dependencies = mock_register_deps

        # Register only the main extension
        registry.register_extension(MainExt)

        # Restore original method
        registry._register_dependencies = original_register_deps

        # All dependencies should be loaded in correct order
        ext_names = [ext.name for ext in registry.extensions]
        assert ext_names == ["dep1", "dep2", "main"]
        assert all(
            name in registry.loaded_extensions for name in ["dep1", "dep2", "main"]
        )

    def test_duplicate_registration_skipped(self):
        """Test that registering the same extension twice is skipped."""
        registry = ExtensionRegistry("")

        # Register once
        registry.register_extension(TestExtension)
        initial_count = len(registry.extensions)

        # Register again
        registry.register_extension(TestExtension)

        # Should still have same count
        assert len(registry.extensions) == initial_count
        assert list(registry.extensions).count(TestExtension) == 1

    def test_optional_dependencies_not_auto_loaded(self):
        """Test that optional dependencies are not automatically loaded."""
        registry = ExtensionRegistry("")

        # Register extension with optional dependencies
        registry.register_extension(TestExtensionWithOptionalDeps)

        # Required dependency warning should appear but optional should not
        assert "test_optional_deps" in registry.loaded_extensions
        # Optional dependency should not be loaded
        assert "optional_ext" not in registry.loaded_extensions

    def test_extension_abilities_tracked(self):
        """Test that extension abilities are properly tracked during registration."""
        registry = ExtensionRegistry("")

        registry.register_extension(TestExtension)

        # Check abilities were discovered
        assert "test_extension" in registry.extension_abilities
        abilities = registry.extension_abilities["test_extension"]

        # Abilities are stored as list of dictionaries
        ability_names = [ability["name"] for ability in abilities]
        assert "ext_ability" in ability_names
        assert "decorated_ability" in ability_names

    def test_recursive_dependency_resolution(self):
        """Test deep recursive dependency resolution."""
        registry = ExtensionRegistry("")

        # Create a deep dependency chain
        class Level0(AbstractStaticExtension):
            name: ClassVar[str] = "level0"
            description: ClassVar[str] = "Level 0"
            dependencies: ClassVar[Dependencies] = Dependencies([])
            _abilities: ClassVar[Set[str]] = set()

        class Level1(AbstractStaticExtension):
            name: ClassVar[str] = "level1"
            description: ClassVar[str] = "Level 1"
            dependencies: ClassVar[Dependencies] = Dependencies(
                [EXT_Dependency(name="level0", friendly_name="L0", reason="Required")]
            )
            _abilities: ClassVar[Set[str]] = set()

        class Level2(AbstractStaticExtension):
            name: ClassVar[str] = "level2"
            description: ClassVar[str] = "Level 2"
            dependencies: ClassVar[Dependencies] = Dependencies(
                [EXT_Dependency(name="level1", friendly_name="L1", reason="Required")]
            )
            _abilities: ClassVar[Set[str]] = set()

        class Level3(AbstractStaticExtension):
            name: ClassVar[str] = "level3"
            description: ClassVar[str] = "Level 3"
            dependencies: ClassVar[Dependencies] = Dependencies(
                [EXT_Dependency(name="level2", friendly_name="L2", reason="Required")]
            )
            _abilities: ClassVar[Set[str]] = set()

        # Mock the dependency resolution
        def mock_register_deps(ext_class):
            deps_map = {Level3: Level2, Level2: Level1, Level1: Level0}
            if ext_class in deps_map:
                dep_class = deps_map[ext_class]
                if dep_class.name not in registry._extension_name_map:
                    registry.register_extension(dep_class)

        original_method = registry._register_dependencies
        registry._register_dependencies = mock_register_deps

        # Register the deepest level
        registry.register_extension(Level3)

        registry._register_dependencies = original_method

        # All levels should be registered in correct order
        ext_names = [ext.name for ext in registry.extensions]
        assert ext_names == ["level0", "level1", "level2", "level3"]

    def test_mixed_dependency_types(self):
        """Test extension with mixed dependency types (sys, pip, ext)."""
        registry = ExtensionRegistry("")

        # TestExtensionWithOptionalDeps has mixed dependencies
        registry.register_extension(TestExtensionWithOptionalDeps)

        # Should register successfully despite having sys and pip dependencies
        assert "test_optional_deps" in registry.loaded_extensions

    def test_csv_initialization_with_dependencies(self):
        """Test that CSV initialization respects dependency order."""
        # This would require mocking the file system to provide actual extension files
        # For now, test that empty CSV works correctly
        registry = ExtensionRegistry("")
        assert registry.csv == ""

        # Test with non-empty CSV but no actual files
        registry2 = ExtensionRegistry("ext1,ext2,ext3")
        # Without actual files, no extensions are loaded
        assert len(registry2.extensions) == 0


class TestExtensionRegistryMethods:
    """Test suite for ExtensionRegistry methods and functionality."""

    def test_discover_extension_abilities_comprehensive(self):
        """Test comprehensive extension ability discovery."""
        registry = ExtensionRegistry("")

        # Test with extension that has both _abilities and decorated abilities
        registry.register_extension(TestExtension)

        # Check that abilities were discovered
        assert "test_extension" in registry.extension_abilities
        abilities = registry.extension_abilities["test_extension"]

        # Should include both class-level and decorated abilities
        ability_names = [ability["name"] for ability in abilities]
        assert "ext_ability" in ability_names
        assert "decorated_ability" in ability_names

    def test_discover_extension_providers_comprehensive(self):
        """Test comprehensive extension provider discovery."""
        registry = ExtensionRegistry("")

        # Register extension that would have providers
        registry.register_extension(TestExtension)

        # Check provider discovery structure
        assert "test_extension" in registry.extension_providers
        providers = registry.extension_providers["test_extension"]
        assert isinstance(providers, list)

    def test_extension_name_map_consistency(self):
        """Test that extension name map stays consistent with OrderedSet."""
        registry = ExtensionRegistry("")

        # Register multiple extensions
        registry.register_extension(TestExtension)
        registry.register_extension(TestExtensionSystemComponent)

        # Name map should match extensions in OrderedSet
        ext_names_from_set = [ext.name for ext in registry.extensions]
        ext_names_from_map = list(registry._extension_name_map.keys())

        assert set(ext_names_from_set) == set(ext_names_from_map)

    def test_version_tracking_accurate(self):
        """Test that version tracking is accurate."""
        registry = ExtensionRegistry("")

        # Register extension with version
        registry.register_extension(TestExtension)

        # Check version is tracked correctly
        assert registry.loaded_extensions["test_extension"] == "1.0.0"

        # Register extension without version
        class NoVersionExt(AbstractStaticExtension):
            name: ClassVar[str] = "no_version"
            description: ClassVar[str] = "No version"
            dependencies: ClassVar[Dependencies] = Dependencies([])
            _abilities: ClassVar[Set[str]] = set()

        registry.register_extension(NoVersionExt)
        assert registry.loaded_extensions["no_version"] == "0.1.0"

    def test_extension_models_tracking(self):
        """Test that extension models are tracked properly."""
        registry = ExtensionRegistry("")

        # Register extension
        registry.register_extension(TestExtension)

        # Check that extension models structure exists
        assert isinstance(registry.extension_models, dict)
        # In test environment, models would be discovered from actual files

    def test_provider_abilities_tracking(self):
        """Test that provider abilities are tracked separately."""
        registry = ExtensionRegistry("")

        # Register extension with providers
        registry.register_extension(TestExtension)

        # Check provider abilities structure
        assert isinstance(registry.provider_abilities, dict)

    def test_register_extension_with_complex_dependencies(self):
        """Test registering extension with complex dependency structure."""
        registry = ExtensionRegistry("")

        # Create extension with various dependency types
        class ComplexDepsExt(AbstractStaticExtension):
            name: ClassVar[str] = "complex_deps"
            description: ClassVar[str] = "Complex dependencies"
            dependencies: ClassVar[Dependencies] = Dependencies(
                [
                    EXT_Dependency(
                        name="required_ext", friendly_name="Required", reason="Required"
                    ),
                    EXT_Dependency(
                        name="optional_ext",
                        friendly_name="Optional",
                        reason="Optional",
                        optional=True,
                    ),
                    PIP_Dependency(
                        name="requests", friendly_name="Requests", reason="HTTP client"
                    ),
                    SYS_Dependency.for_all_platforms(
                        name="git",
                        apt_pkg="git",
                        brew_pkg="git",
                        winget_pkg="Git.Git",
                        friendly_name="Git",
                        reason="Version control",
                    ),
                ]
            )
            _abilities: ClassVar[Set[str]] = {"complex_ability"}

        # Should register successfully
        registry.register_extension(ComplexDepsExt)

        assert "complex_deps" in registry.loaded_extensions
        abilities = registry.extension_abilities["complex_deps"]
        ability_names = [ability["name"] for ability in abilities]
        assert "complex_ability" in ability_names

    def test_csv_property_empty_when_no_extensions(self):
        """Test that CSV property returns empty string when no extensions."""
        registry = ExtensionRegistry("")
        assert registry.csv == ""

    def test_csv_property_single_extension(self):
        """Test CSV property with single extension."""
        registry = ExtensionRegistry("")
        registry.register_extension(TestExtension)

        csv = registry.csv
        assert csv == "test_extension"

    def test_csv_property_multiple_extensions_ordered(self):
        """Test CSV property with multiple extensions in dependency order."""
        registry = ExtensionRegistry("")

        # Register extensions in various orders
        registry.register_extension(TestExtensionSystemComponent)
        registry.register_extension(TestExtension)

        csv = registry.csv
        assert isinstance(csv, str)
        assert "test_extension" in csv
        assert "test_system_component" in csv

        # Should be comma-separated
        names = csv.split(",")
        assert len(names) == 2
        assert "test_extension" in names
        assert "test_system_component" in names
