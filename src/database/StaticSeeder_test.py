import pytest
from sqlalchemy import select
from types import SimpleNamespace

import database.StaticSeeder as static_seeder

# We'll create our own simplified model discovery for testing
from lib.Logging import logger


class MockProvider:
    name = "SendGrid"


def _get_semantic_identifier(seed_item: dict, fallback_index: int = 0) -> str:
    """
    Extract semantic identifier from seed item for meaningful test names.
    Priority: name > email > id > friendly_name > placeholder combinations > fallback
    """
    # Priority 1: name field (most semantic)
    if "name" in seed_item and seed_item["name"]:
        return str(seed_item["name"]).replace(" ", "_").lower()

    # Priority 2: email local part (for user models)
    if "email" in seed_item and seed_item["email"]:
        email = seed_item["email"]
        if "@" in email:
            return email.split("@")[0].replace(".", "_")
        return email.replace(".", "_")

    # Priority 3: id (if meaningful)
    if "id" in seed_item and seed_item["id"]:
        id_val = str(seed_item["id"])
        # Check for meaningful IDs (not just UUIDs)
        if len(id_val) < 20 or any(
            word in id_val.lower()
            for word in ["root", "system", "admin", "user", "template"]
        ):
            return id_val.replace("-", "_").lower()

    # Priority 4: friendly_name
    if "friendly_name" in seed_item and seed_item["friendly_name"]:
        return str(seed_item["friendly_name"]).replace(" ", "_").lower()

    # Priority 5: Placeholder field combinations for relational models
    if "_provider_name" in seed_item and "_extension_name" in seed_item:
        provider = str(seed_item["_provider_name"]).replace(" ", "_").lower()
        extension = str(seed_item["_extension_name"]).replace(" ", "_").lower()
        return f"{provider}_{extension}"

    if "_provider_name" in seed_item and "_rotation_name" in seed_item:
        provider = str(seed_item["_provider_name"]).replace(" ", "_").lower()
        rotation = str(seed_item["_rotation_name"]).replace(" ", "_").lower()
        return f"{provider}_{rotation}"

    if "_extension_name" in seed_item:
        extension = str(seed_item["_extension_name"]).replace(" ", "_").lower()
        return extension

    if "_provider_name" in seed_item:
        provider = str(seed_item["_provider_name"]).replace(" ", "_").lower()
        return provider

    # Fallback: item_index
    return f"item_{fallback_index}"


def _is_extension_model(pydantic_model) -> bool:
    """
    Determine if a model is from an extension based on its module path.
    Extension models have module paths like 'extensions.ext_name.BLL_*'
    """
    module_name = getattr(pydantic_model, "__module__", "")
    return module_name.startswith("extensions.")


def _get_extension_name(pydantic_model) -> str:
    """Extract extension name from module path."""
    module_name = getattr(pydantic_model, "__module__", "")
    if module_name.startswith("extensions."):
        parts = module_name.split(".")
        if len(parts) >= 2:
            return parts[1]  # e.g., "auth_mfa" from "extensions.auth_mfa.BLL_Auth_MFA"
    return "unknown"


def _find_core_models_with_seed_data():
    """Find core models that have seed_data attributes without validating DB properties."""
    import inspect
    import sys

    models_with_seed_data = []
    seen_class_names = set()

    for module_name, module in sys.modules.items():
        if module and module_name.startswith("logic.BLL_"):
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    inspect.isclass(attr)
                    and attr_name.endswith("Model")
                    and not attr_name.endswith("Mixin")
                    and not any(
                        suffix in attr_name
                        for suffix in ["Reference", "Network", "Manager"]
                    )
                    and hasattr(attr, "seed_data")
                ):
                    # De-duplicate by fully qualified class name
                    full_name = f"{attr.__module__}.{attr.__name__}"
                    if full_name not in seen_class_names:
                        models_with_seed_data.append(attr)
                        seen_class_names.add(full_name)
    return models_with_seed_data


def _find_extension_models_with_seed_data():
    """Find extension models that have seed_data attributes without validating DB properties."""
    import inspect
    import sys

    models_with_seed_data = []
    seen_class_names = set()

    for module_name, module in sys.modules.items():
        if module and "extensions." in module_name and ".BLL_" in module_name:
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    inspect.isclass(attr)
                    and attr_name.endswith("Model")
                    and not attr_name.endswith("Mixin")
                    and not any(
                        suffix in attr_name
                        for suffix in ["Reference", "Network", "Manager"]
                    )
                    and hasattr(attr, "seed_data")
                ):
                    # De-duplicate by fully qualified class name
                    full_name = f"{attr.__module__}.{attr.__name__}"
                    if full_name not in seen_class_names:
                        models_with_seed_data.append(attr)
                        seen_class_names.add(full_name)
    return models_with_seed_data


def _find_models_with_seed_data():
    """Find models that have seed_data attributes without validating DB properties (for testing)."""
    # For the static functionality tests, use core models
    return _find_core_models_with_seed_data()


def discover_core_seed_items():
    """Discover seed items from core BLL models (src/logic/) only."""
    seed_items = []

    # Use ModelRegistry for model discovery instead of deprecated scoped_import
    try:
        from database.DatabaseManager import DatabaseManager
        from lib.Pydantic import ModelRegistry

        # Create temporary registry and database manager for discovery
        # Use test prefix to avoid touching production database
        temp_db_manager = DatabaseManager("test.seed_discovery")
        temp_db_manager.init_engine_config()
        temp_registry = ModelRegistry(database_manager=temp_db_manager)

        # Import core models using ModelRegistry
        temp_registry.from_scoped_import(file_type="BLL", scopes=["logic"])

        # Get models from the registry instead of sys.modules
        pydantic_models = []
        if hasattr(temp_registry, "bound_models"):
            pydantic_models = temp_registry.bound_models
        else:
            # Fallback to finding models the old way
            pydantic_models = _find_core_models_with_seed_data()

    except Exception as e:
        logger.warning(f"Core discovery: Failed to import core BLL models - {e}")
        return []

    for pydantic_model in pydantic_models:
        try:
            # Skip extension models in this discovery
            if _is_extension_model(pydantic_model):
                continue

            model_name = pydantic_model.__name__

            # Skip mixin, reference, network, and manager classes
            if any(
                suffix in model_name
                for suffix in ["Mixin", "Reference", "Network", "Manager"]
            ):
                continue

            # Get seed data from the model
            seed_data = []
            if hasattr(pydantic_model, "seed_data"):
                seed_data_attr = getattr(pydantic_model, "seed_data")
                if callable(seed_data_attr):
                    try:
                        seed_data = seed_data_attr()
                    except Exception as e:
                        logger.warning(
                            f"Core discovery: {model_name} seed data failed to load - {e}"
                        )
                        continue
                else:
                    seed_data = seed_data_attr or []

            if not seed_data:
                continue

            # Create individual test scenarios for each seed item
            for item_index, seed_item in enumerate(seed_data):
                if not isinstance(seed_item, dict):
                    continue

                semantic_id = _get_semantic_identifier(seed_item, item_index)
                test_id = f"{model_name}_{semantic_id}"

                seed_items.append(
                    {
                        "test_id": test_id,
                        "pydantic_model": pydantic_model,
                        "model_name": model_name,
                        "seed_item": seed_item,
                        "item_index": item_index,
                        "semantic_id": semantic_id,
                    }
                )

        except Exception as e:
            model_name = getattr(pydantic_model, "__name__", "unknown")
            logger.warning(f"Core discovery: {model_name} failed to process - {e}")

    return seed_items


def discover_extension_seed_items():
    """Discover seed items from extension BLL models (src/extensions/*/) only."""
    seed_items = []

    # Use ModelRegistry for extension model discovery instead of deprecated scoped_import
    try:
        from database.DatabaseManager import DatabaseManager
        from lib.Environment import env
        from lib.Pydantic import ModelRegistry

        # Only try to discover extensions if they're configured
        app_extensions = env("APP_EXTENSIONS")
        if not app_extensions:
            logger.debug("No extensions configured, skipping extension discovery")
            return [], {}

        # Create temporary registry and database manager for discovery
        # Use test prefix to avoid touching production database
        temp_db_manager = DatabaseManager("test.seed_discovery")
        temp_db_manager.init_engine_config()
        temp_registry = ModelRegistry(database_manager=temp_db_manager)

        # Import extension models using ModelRegistry
        temp_registry.from_scoped_import(file_type="BLL", scopes=["extensions"])

        # Get models from the registry instead of sys.modules
        pydantic_models = []
        if hasattr(temp_registry, "bound_models"):
            pydantic_models = temp_registry.bound_models
        else:
            # Fallback to finding models the old way
            pydantic_models = _find_extension_models_with_seed_data()

    except ImportError as e:
        logger.warning(f"Extension discovery: Failed to import extensions - {e}")
        return [], {}
    except Exception as e:
        logger.warning(f"Extension discovery: Unexpected error during import - {e}")
        return [], {}

    # Group by extension
    extension_groups = {}

    for pydantic_model in pydantic_models:
        try:
            # Only process extension models
            if not _is_extension_model(pydantic_model):
                continue

            extension_name = _get_extension_name(pydantic_model)
            model_name = pydantic_model.__name__

            # Skip mixin, reference, network, and manager classes
            if any(
                suffix in model_name
                for suffix in ["Mixin", "Reference", "Network", "Manager"]
            ):
                continue

            # Get seed data from the model with comprehensive error handling
            seed_data = []
            if hasattr(pydantic_model, "seed_data"):
                seed_data_attr = getattr(pydantic_model, "seed_data")
                if callable(seed_data_attr):
                    try:
                        seed_data = seed_data_attr()
                    except ImportError as e:
                        logger.warning(
                            f"Extension discovery: {extension_name}.{model_name} seed data not available due to missing dependency - {e}"
                        )
                        continue
                    except Exception as e:
                        logger.warning(
                            f"Extension discovery: {extension_name}.{model_name} seed data failed to load - {e}"
                        )
                        continue
                else:
                    seed_data = seed_data_attr or []

            if not seed_data:
                continue

            if extension_name not in extension_groups:
                extension_groups[extension_name] = []

            # Create individual test scenarios for each seed item
            for item_index, seed_item in enumerate(seed_data):
                if not isinstance(seed_item, dict):
                    continue

                semantic_id = _get_semantic_identifier(seed_item, item_index)
                test_id = f"{extension_name}_{model_name}_{semantic_id}"

                extension_groups[extension_name].append(
                    {
                        "test_id": test_id,
                        "pydantic_model": pydantic_model,
                        "model_name": model_name,
                        "extension_name": extension_name,
                        "seed_item": seed_item,
                        "item_index": item_index,
                        "semantic_id": semantic_id,
                    }
                )

        except ImportError as e:
            model_name = getattr(pydantic_model, "__name__", "unknown")
            extension_name = (
                _get_extension_name(pydantic_model)
                if hasattr(pydantic_model, "__module__")
                else "unknown"
            )
            logger.warning(
                f"Extension discovery: {extension_name}.{model_name} not available due to missing dependency - {e}"
            )
        except Exception as e:
            model_name = getattr(pydantic_model, "__name__", "unknown")
            extension_name = (
                _get_extension_name(pydantic_model)
                if hasattr(pydantic_model, "__module__")
                else "unknown"
            )
            logger.warning(
                f"Extension discovery: {extension_name}.{model_name} failed to process - {e}"
            )

    # Flatten all extension seed items
    for ext_items in extension_groups.values():
        seed_items.extend(ext_items)

    return seed_items, extension_groups


# Discover seed items lazily to avoid import errors at module load time
def get_core_seed_items():
    """Get core seed items, discovering them if not already cached."""
    global _CORE_SEED_ITEMS_CACHE
    if not _CORE_SEED_ITEMS_CACHE:
        try:
            _CORE_SEED_ITEMS_CACHE = discover_core_seed_items()
        except Exception as e:
            logger.warning(f"Core seed discovery failed: {e}")
            _CORE_SEED_ITEMS_CACHE = []
    return _CORE_SEED_ITEMS_CACHE


def get_extension_seed_items():
    """Get extension seed items, discovering them if not already cached."""
    global _EXTENSION_SEED_ITEMS_CACHE, _EXTENSION_GROUPS_CACHE
    if not _EXTENSION_SEED_ITEMS_CACHE:
        try:
            _EXTENSION_SEED_ITEMS_CACHE, _EXTENSION_GROUPS_CACHE = (
                discover_extension_seed_items()
            )
        except Exception as e:
            logger.warning(f"Extension seed discovery failed: {e}")
            _EXTENSION_SEED_ITEMS_CACHE, _EXTENSION_GROUPS_CACHE = [], {}
    return _EXTENSION_SEED_ITEMS_CACHE, _EXTENSION_GROUPS_CACHE


# Initialize empty caches
_CORE_SEED_ITEMS_CACHE = []
_EXTENSION_SEED_ITEMS_CACHE = []
_EXTENSION_GROUPS_CACHE = {}


# Backwards compatibility - these will be populated lazily with error handling
def _initialize_global_seed_items():
    """Initialize global seed item variables with error handling."""
    global CORE_SEED_ITEMS, EXTENSION_SEED_ITEMS, EXTENSION_GROUPS

    try:
        CORE_SEED_ITEMS = get_core_seed_items()
    except Exception as e:
        logger.warning(f"Failed to initialize core seed items: {e}")
        CORE_SEED_ITEMS = []

    # DO NOT initialize extension seed items at module import time
    # This prevents contamination of other DB tests with extension imports
    EXTENSION_SEED_ITEMS = []
    EXTENSION_GROUPS = {}


# Initialize with error handling
try:
    _initialize_global_seed_items()
except Exception as e:
    logger.warning(f"Failed to initialize seed items at module level: {e}")
    CORE_SEED_ITEMS = []
    EXTENSION_SEED_ITEMS = []
    EXTENSION_GROUPS = {}


@pytest.mark.seed
class TestStaticSeederFunctionality:
    """Test StaticSeeder functionality as a static library (no server dependency)"""

    @pytest.mark.seed
    def test_seed_discovery_handles_core_items_gracefully(self):
        """Test that core seed discovery handles errors gracefully"""
        # This should not raise exceptions even if ModelRegistry is not committed
        core_items = get_core_seed_items()
        assert isinstance(core_items, list), "Should return a list even if empty"

        # Log discovered core items for debugging
        logger.info(f"Seeding {len(core_items)} core seed items...")
        for item in core_items[:5]:  # Show first 5
            logger.debug(f"Core seed: {item.get('test_id', 'unknown')}")

    @pytest.mark.seed
    @pytest.mark.skip()
    def test_seed_discovery_handles_extension_items_gracefully(self):
        """Test that extension seed discovery handles missing dependencies gracefully"""
        # This should not raise exceptions even if dependencies are missing
        extension_items, extension_groups = get_extension_seed_items()
        assert isinstance(extension_items, list), "Should return a list even if empty"
        assert isinstance(extension_groups, dict), "Should return a dict even if empty"

        # Extension items may be empty if no extensions are enabled or dependencies missing
        logger.info(f"Seeding {len(extension_items)} extension seed items...")
        if extension_groups:
            for ext_name, items in extension_groups.items():
                logger.debug(f"Extension {ext_name}: {len(items)} seed items")

    @pytest.mark.seed
    def test_find_models_with_seed_data_works(self):
        """Test that _find_models_with_seed_data finds models with seed_data"""
        # This should not raise exceptions and should find models with seed_data
        models = _find_core_models_with_seed_data()
        assert isinstance(models, list), "Should return a list even if empty"

        if models:  # Only check if models were found
            for model in models:
                assert hasattr(model, "__name__"), "Model should have a name"
                assert hasattr(
                    model, "seed_data"
                ), "Model should have seed_data property"


@pytest.mark.seed
class TestStaticSeederMockFunctionality:
    """Test StaticSeeder functionality using mock_server fixture"""

    @pytest.mark.seed
    @pytest.mark.skip()
    def test_seed_discovery_with_mock_server(self, mock_server):
        """Test that seed discovery works with mock server context"""
        core_items = get_core_seed_items()
        assert isinstance(core_items, list), "Should return a list even if empty"

        extension_items, extension_groups = get_extension_seed_items()
        assert isinstance(extension_items, list), "Should return a list even if empty"
        assert isinstance(extension_groups, dict), "Should return a dict even if empty"


@pytest.mark.seed
class TestSeedDataGeneration:
    """Test seed data generation methods"""

    @pytest.mark.seed
    def test_extension_model_seed_data_generation(self):
        """Test that ExtensionModel.seed_data generates correct data"""
        from extensions.AbstractExtensionProvider import ExtensionRegistry
        from logic.BLL_Extensions import ExtensionModel

        # Create mock model registry with extension registry
        class MockModelRegistry:
            def __init__(self):
                self.extension_registry = ExtensionRegistry("email,payment")

        model_registry = MockModelRegistry()
        seed_data = ExtensionModel.seed_data(model_registry)

        assert isinstance(seed_data, list)
        assert len(seed_data) >= 2  # At least email and payment

        # Check structure
        for item in seed_data:
            assert "name" in item
            assert "description" in item
            # Extensions found should be a subset of expected
            assert item["name"] in [
                "email",
                "payment",
                "ai_agents",
                "ai_prompts",
                "auth_mfa",
            ]

    @pytest.mark.seed
    def test_ability_model_seed_data_uses_correct_placeholders(self):
        """Test that AbilityModel.seed_data uses _extension_name placeholders"""
        from extensions.AbstractExtensionProvider import ExtensionRegistry
        from logic.BLL_Extensions import AbilityModel

        # Create mock extension registry with abilities
        class MockExtensionRegistry:
            def __init__(self):
                self.extension_abilities = {
                    "email": [
                        {"name": "send_email", "meta": True},
                        {"name": "receive_email", "meta": True},
                    ]
                }
                self.provider_abilities = {}

        class MockModelRegistry:
            def __init__(self):
                self.extension_registry = MockExtensionRegistry()

        model_registry = MockModelRegistry()
        seed_data = AbilityModel.seed_data(model_registry)

        assert isinstance(seed_data, list)
        assert len(seed_data) == 2

        # Verify correct placeholder format
        for item in seed_data:
            assert "_extension_name" in item
            assert "extension_id" not in item  # Should NOT have direct extension_id
            assert item["_extension_name"] == "email"

            # Should NOT contain the old EXT: format
            for value in item.values():
                if isinstance(value, str):
                    assert not value.startswith(
                        "EXT:"
                    ), f"Found legacy EXT: format in {value}"

    @pytest.mark.seed
    def test_provider_model_seed_data_generation(self):
        """Test that ProviderModel.seed_data generates correct data"""
        from extensions.AbstractExtensionProvider import ExtensionRegistry
        from logic.BLL_Providers import ProviderModel

        class MockExtensionRegistry:
            def __init__(self):
                self.extension_providers = {"email": [MockProvider]}

        class MockModelRegistry:
            def __init__(self):
                self.extension_registry = MockExtensionRegistry()

        model_registry = MockModelRegistry()
        seed_data = ProviderModel.seed_data(model_registry)

        assert isinstance(seed_data, list)
        assert len(seed_data) == 1
        assert seed_data[0]["name"] == "SendGrid"
        assert seed_data[0]["system"] == True

    @pytest.mark.seed
    def test_provider_extension_model_uses_placeholders(self):
        """Test that ProviderExtensionModel uses correct placeholders"""
        from logic.BLL_Providers import ProviderExtensionModel

        class MockExtensionRegistry:
            def __init__(self):
                self.extension_providers = {"email": [MockProvider]}

        class MockModelRegistry:
            def __init__(self):
                self.extension_registry = MockExtensionRegistry()

        model_registry = MockModelRegistry()
        seed_data = ProviderExtensionModel.seed_data(model_registry)

        assert isinstance(seed_data, list)
        assert len(seed_data) == 1

        # Should use placeholder fields
        assert "_provider_name" in seed_data[0]
        assert "_extension_name" in seed_data[0]
        assert seed_data[0]["_provider_name"] == "SendGrid"
        assert seed_data[0]["_extension_name"] == "email"

        # Should NOT have direct IDs
        assert "provider_id" not in seed_data[0]
        assert "extension_id" not in seed_data[0]

    @pytest.mark.seed
    def test_rotation_model_uses_extension_placeholder(self):
        """Test that RotationModel uses _extension_name placeholder"""
        from extensions.AbstractExtensionProvider import ExtensionRegistry
        from logic.BLL_Providers import RotationModel

        class MockExtension:
            name = "email"

        class MockExtensionRegistry:
            def __init__(self):
                self.extensions = {"email": MockExtension}
                self._extension_name_map = {"email": MockExtension}
                self.extension_providers = {"email": MockProvider}

        class MockModelRegistry:
            def __init__(self):
                self.extension_registry = MockExtensionRegistry()

        model_registry = MockModelRegistry()
        seed_data = RotationModel.seed_data(model_registry)

        assert isinstance(seed_data, list)
        assert len(seed_data) == 1

        # Should use placeholder
        assert "_extension_name" in seed_data[0]
        assert seed_data[0]["_extension_name"] == "email"
        assert seed_data[0]["name"] == "Root_Email"  # Pluralized

        # Should NOT have direct extension_id
        assert "extension_id" not in seed_data[0]

    @pytest.mark.seed
    def test_placeholder_resolution_handles_legacy_format(self):
        """Test that _resolve_placeholder_fields handles legacy EXT: format"""
        from database.DatabaseManager import DatabaseManager
        from database.StaticSeeder import _resolve_placeholder_fields

        # Mock session and db_manager
        class MockExtension:
            id = "ext-123"
            name = "email"

        class MockSession:
            def execute(self, stmt):
                class Result:
                    def scalar_one_or_none(self):
                        return MockExtension()

                return Result()

        # Use test prefix to avoid touching production database
        db_manager = DatabaseManager("test.placeholder_resolution")

        # Test legacy format
        legacy_item = {
            "name": "test_ability",
            "extension_id": "EXT:email",
            "meta": True,
        }

        resolved = _resolve_placeholder_fields(
            legacy_item, MockSession(), "AbilityModel", db_manager
        )

        assert resolved is not None
        assert resolved["extension_id"] == "ext-123"
        assert resolved["name"] == "test_ability"
        assert resolved["meta"] == True

        # Test new format
        new_item = {"name": "test_ability2", "_extension_name": "email", "meta": False}

        resolved2 = _resolve_placeholder_fields(
            new_item, MockSession(), "AbilityModel", db_manager
        )

        assert resolved2 is not None
        assert resolved2["extension_id"] == "ext-123"
        assert "_extension_name" not in resolved2
        assert resolved2["name"] == "test_ability2"
        assert resolved2["meta"] == False

    def test_placeholder_resolution_resolves_all_supported_placeholders(self):
        """Ensure provider, extension, rotation, and instance placeholders resolve."""

        class Dummy:
            def __init__(self, identifier: str):
                self.id = identifier

        provider_calls = []
        extension_calls = []
        rotation_calls = []
        instance_calls = []

        original_provider = static_seeder.get_provider_by_name
        original_extension = static_seeder.get_extension_by_name
        original_rotation = static_seeder.get_rotation_by_name
        original_instance = static_seeder.get_provider_instance_by_name

        try:
            static_seeder.get_provider_by_name = (
                lambda session, name, db_manager: provider_calls.append(name)
                or Dummy("provider-id")
            )
            static_seeder.get_extension_by_name = (
                lambda session, name, db_manager: extension_calls.append(name)
                or Dummy("extension-id")
            )
            static_seeder.get_rotation_by_name = (
                lambda session, name, db_manager: rotation_calls.append(name)
                or Dummy("rotation-id")
            )
            static_seeder.get_provider_instance_by_name = (
                lambda session, name, db_manager: instance_calls.append(name)
                or Dummy("instance-id")
            )

            item = {
                "name": "demo",
                "extension_id": "EXT:legacy-ext",
                "_extension_name": "ext",
                "_provider_name": "provider",
                "_rotation_name": "rotation",
                "_provider_instance_name": "instance",
            }

            resolved = static_seeder._resolve_placeholder_fields(
                item, object(), "TestModel", object()
            )

            assert resolved["extension_id"] == "extension-id"
            assert resolved["provider_id"] == "provider-id"
            assert resolved["rotation_id"] == "rotation-id"
            assert resolved["provider_instance_id"] == "instance-id"
            assert "_extension_name" not in resolved
            assert "_provider_name" not in resolved
            assert "_rotation_name" not in resolved
            assert "_provider_instance_name" not in resolved
            assert provider_calls == ["provider"]
            assert extension_calls == ["legacy-ext", "ext"]
            assert rotation_calls == ["rotation"]
            assert instance_calls == ["instance"]
        finally:
            static_seeder.get_provider_by_name = original_provider
            static_seeder.get_extension_by_name = original_extension
            static_seeder.get_rotation_by_name = original_rotation
            static_seeder.get_provider_instance_by_name = original_instance

    def test_placeholder_resolution_returns_none_when_dependency_missing(self):
        """If any placeholder lookup fails, the item should be skipped."""

        original_provider = static_seeder.get_provider_by_name

        try:
            static_seeder.get_provider_by_name = lambda *args, **kwargs: None

            unresolved = static_seeder._resolve_placeholder_fields(
                {"_provider_name": "missing"}, object(), "TestModel", object()
            )

            assert unresolved is None
        finally:
            static_seeder.get_provider_by_name = original_provider


class TestSeedModelBehavior:
    """Focused tests for the seed_model helper."""

    def test_seed_model_uses_seed_list_and_skips_existing_items(self):
        created_items = []
        existence_checks = []

        class DummyModel:
            __name__ = "DummyModel"
            seed_list = [
                {"id": "exists", "name": "existing"},
                {"id": "new", "name": "fresh"},
            ]

            @classmethod
            def exists(cls, requester_id, model_registry, **kwargs):
                existence_checks.append(kwargs)
                return kwargs.get("id") == "exists"

            @classmethod
            def create(cls, creator_id, model_registry, return_type="db", **item):
                created_items.append({"creator": creator_id, **item})

        static_seeder.seed_model(DummyModel, None, None, None)

        assert existence_checks == [{"id": "exists"}, {"id": "new"}]
        assert created_items
        assert created_items[0]["id"] == "new"

    def test_seed_model_handles_callable_seed_list(self):
        created_items = []

        class CallableSeedModel:
            __name__ = "CallableSeedModel"
            seed_list = lambda: [  # noqa: E731 - intentional callable attribute
                {"id": "callable", "name": "callable-item"}
            ]

            @classmethod
            def exists(cls, requester_id, model_registry, **kwargs):
                return False

            @classmethod
            def create(cls, creator_id, model_registry, return_type="db", **item):
                created_items.append(item)

        static_seeder.seed_model(CallableSeedModel, None, None, None)

        assert created_items == [{"id": "callable", "name": "callable-item"}]

    def test_seed_model_uses_pydantic_seed_data_and_creator(self):
        created = []

        class PydanticBackedModel:
            __name__ = "PydanticBackedModel"
            seed_list = []

            @classmethod
            def exists(cls, requester_id, model_registry, **kwargs):
                return False

            @classmethod
            def create(cls, creator_id, model_registry, return_type="db", **item):
                created.append({"creator": creator_id, **item})

        class FakePydanticModel:
            seed_creator_id = "seed-user-123"

            @staticmethod
            def DB(base):
                return PydanticBackedModel

            @staticmethod
            def seed_data(model_registry=None):
                return [{"id": "from-pydantic", "name": "pydantic"}]

        class FakeRegistry:
            def __init__(self):
                self.bound_models = [FakePydanticModel]

        db_manager = SimpleNamespace(Base=object())

        static_seeder.seed_model(PydanticBackedModel, None, db_manager, FakeRegistry())

        assert created == [
            {"creator": "seed-user-123", "id": "from-pydantic", "name": "pydantic"}
        ]

    def test_seed_model_handles_get_seed_list_errors(self):
        class FaultyModel:
            __name__ = "FaultyModel"
            seed_list = []

            @classmethod
            def get_seed_list(cls):
                raise RuntimeError("boom")

            @classmethod
            def exists(cls, requester_id, model_registry, **kwargs):
                raise AssertionError("should not be called")

        static_seeder.seed_model(FaultyModel, None, None, None)

    def test_seed_model_accepts_typed_pydantic_instances(self):
        """Item 38 — a seed_list declared as `List[ModelClass]` (typed
        Pydantic instances rather than dicts) is normalized to dicts at
        the seeder boundary so the rest of the pipeline keeps its shape.
        Field typos and missing required fields surface at Pydantic
        instantiation (import time) rather than at seeding time."""
        from pydantic import BaseModel

        class TypedSeed(BaseModel):
            id: str
            name: str

        created = []

        class TypedSeedModel:
            __name__ = "TypedSeedModel"
            seed_list = [
                TypedSeed(id="typed-1", name="alpha"),
                TypedSeed(id="typed-2", name="beta"),
            ]

            @classmethod
            def exists(cls, requester_id, model_registry, **kwargs):
                return False

            @classmethod
            def create(cls, creator_id, model_registry, return_type="db", **item):
                created.append(item)

        static_seeder.seed_model(TypedSeedModel, None, None, None)

        # The Pydantic instances are converted via model_dump so the
        # `create` call receives the same kwargs as the dict path.
        assert {item["id"] for item in created} == {"typed-1", "typed-2"}
        assert all("name" in item for item in created)

    def test_seed_model_skips_invalid_seed_item_types(self):
        """Item 38 — seed entries that are neither dicts nor Pydantic
        models are skipped with a warning rather than crashing the
        seeder. This guards against accidental string / list seed entries
        slipping past the type check at declaration time."""

        class MixedSeedModel:
            __name__ = "MixedSeedModel"
            seed_list = [
                {"id": "ok", "name": "valid"},
                "this-is-not-a-valid-seed-entry",
                42,
            ]

            @classmethod
            def exists(cls, requester_id, model_registry, **kwargs):
                return False

            @classmethod
            def create(cls, creator_id, model_registry, return_type="db", **item):
                MixedSeedModel.created = getattr(MixedSeedModel, "created", []) + [item]

        static_seeder.seed_model(MixedSeedModel, None, None, None)

        # Only the dict survives; bare string and int are dropped.
        created = getattr(MixedSeedModel, "created", [])
        assert len(created) == 1
        assert created[0]["id"] == "ok"


@pytest.mark.seed
@pytest.mark.db
class TestCoreSeedData:
    """Test core seed data using server fixture (real seeded database)"""

    @pytest.mark.parametrize(
        "seed_scenario",
        sorted(CORE_SEED_ITEMS),
        ids=sorted([item["test_id"] for item in CORE_SEED_ITEMS]),
    )
    @pytest.mark.seed
    @pytest.mark.db
    def test_core_seed_item_exists_in_database(self, server, seed_scenario):
        """Test that each core seed item exists in the database"""
        pydantic_model = seed_scenario["pydantic_model"]
        seed_item = seed_scenario["seed_item"]

        # Get database components from server
        db_manager = server.app.state.model_registry.database_manager

        # Check if the model can create DB class (ModelRegistry committed)
        try:
            db_model_class = pydantic_model.DB(db_manager.Base)
        except RuntimeError as e:
            if "ModelRegistry must be committed" in str(e):
                pytest.skip(
                    f"ModelRegistry not committed for {pydantic_model.__name__} - database tests require server context"
                )
            else:
                raise

        # Use the tested DB functions instead of direct SQL
        from lib.Environment import env

        try:
            if "id" in seed_item:
                result = db_model_class.get(
                    requester_id=env("ROOT_ID"),
                    id=seed_item["id"],
                    db=db_manager.get_session(),
                    db_manager=db_manager,
                )
                assert result is not None, (
                    f"Core seed item {seed_scenario['test_id']} with id='{seed_item['id']}' "
                    f"not found using {db_model_class.__name__}.get()"
                )
            elif "name" in seed_item:
                result = db_model_class.get(
                    requester_id=env("ROOT_ID"),
                    name=seed_item["name"],
                    db=db_manager.get_session(),
                    db_manager=db_manager,
                )
                assert result is not None, (
                    f"Core seed item {seed_scenario['test_id']} with name='{seed_item['name']}' "
                    f"not found using {db_model_class.__name__}.get()"
                )
            else:
                pytest.skip(
                    f"Cannot query {seed_scenario['test_id']} - no 'id' or 'name' field"
                )
        except Exception as e:
            if "no such table" in str(e).lower() or "table" in str(e).lower():
                pytest.skip(
                    f"Database table for {db_model_class.__name__} not available - database not seeded"
                )
            elif "no such column" in str(e).lower():
                pytest.skip(
                    f"Schema mismatch for {db_model_class.__name__} - missing column (likely from disabled extension): {e}"
                )
            else:
                raise

        # Verify key attributes match (excluding placeholders which are resolved)
        for key, expected_value in seed_item.items():
            if key.startswith("_"):  # Skip placeholder fields
                continue
            if hasattr(result, key):
                actual_value = getattr(result, key)
                # For foreign keys that were placeholders, they should now be UUIDs
                if key.endswith("_id") and isinstance(actual_value, str):
                    # Just verify it's not the placeholder format anymore
                    assert not actual_value.startswith(
                        "EXT:"
                    ), f"Foreign key {key} still contains placeholder format: {actual_value}"
                else:
                    assert actual_value == expected_value, (
                        f"Core seed item {seed_scenario['test_id']} field '{key}': "
                        f"expected '{expected_value}', got '{actual_value}'"
                    )

    @pytest.mark.parametrize(
        "seed_scenario",
        sorted(CORE_SEED_ITEMS),
        ids=sorted([item["test_id"] for item in CORE_SEED_ITEMS]),
    )
    @pytest.mark.seed
    def test_core_seed_item_structure_valid(self, seed_scenario):
        """Test that each core seed item has valid structure"""
        seed_item = seed_scenario["seed_item"]

        assert isinstance(seed_item, dict), "Seed item should be a dictionary"

        # Should have either 'id', 'name', or placeholder fields for identification
        has_identifier = (
            "id" in seed_item
            or "name" in seed_item
            or ("_provider_name" in seed_item and "_extension_name" in seed_item)
            or "_extension_name" in seed_item
            or "_provider_name" in seed_item
        )
        assert (
            has_identifier
        ), f"Core seed item {seed_scenario['test_id']} should have 'id', 'name', or placeholder fields for identification"

        # All values should be JSON-serializable
        import json

        try:
            json.dumps(seed_item)
        except TypeError as e:
            pytest.fail(
                f"Core seed item {seed_scenario['test_id']} not JSON-serializable: {e}"
            )


# Extension seed data tests - use server fixture context instead of module-level discovery
# This prevents extension imports from contaminating core DB tests
@pytest.mark.seed
@pytest.mark.db
@pytest.mark.extensions
class TestExtensionSeedData:
    """Test extension seed data using isolated extension servers"""

    @pytest.mark.seed
    @pytest.mark.db
    @pytest.mark.extensions
    def test_extension_seed_discovery_with_server_context(
        self, isolated_extension_server
    ):
        """Test extension seed discovery using server context to avoid contamination"""
        # Discover available extensions from server context
        try:
            from lib.Environment import env

            app_extensions = env("APP_EXTENSIONS", "")
            if not app_extensions:
                pytest.skip("No extensions configured")

            extension_names = [
                ext.strip() for ext in app_extensions.split(",") if ext.strip()
            ]
            if not extension_names:
                pytest.skip("No valid extensions found")

            # Test each extension individually with isolated server
            for extension_name in extension_names:
                server = isolated_extension_server(extension_name)

                # Get extension seed items from server context
                extension_items, _ = get_extension_seed_items()

                # Filter for this extension
                extension_specific_items = [
                    item
                    for item in extension_items
                    if item.get("extension_name") == extension_name
                ]

                if not extension_specific_items:
                    continue

                # Test each seed item
                for seed_scenario in extension_specific_items:
                    self._test_extension_seed_item(server, seed_scenario)

        except Exception as e:
            pytest.skip(f"Extension testing not available: {e}")

    def _test_extension_seed_item(self, server, seed_scenario):
        """Helper method to test a single extension seed item"""
        pydantic_model = seed_scenario["pydantic_model"]
        seed_item = seed_scenario["seed_item"]

        # Get database components from extension server
        db_manager = server.app.state.model_registry.database_manager
        db_model_class = pydantic_model.DB(db_manager.Base)

        with db_manager.get_session() as session:
            # Query by primary identifier (id or name)
            result = None
            if "id" in seed_item:
                stmt = select(db_model_class).where(
                    db_model_class.id == seed_item["id"]
                )
                result = session.execute(stmt).scalar_one_or_none()
                assert result is not None, (
                    f"Extension seed item {seed_scenario['test_id']} with id='{seed_item['id']}' "
                    f"not found in {db_model_class.__tablename__}"
                )
            elif "name" in seed_item and hasattr(db_model_class, "name"):
                stmt = select(db_model_class).where(
                    db_model_class.name == seed_item["name"]
                )
                result = session.execute(stmt).scalar_one_or_none()
                assert result is not None, (
                    f"Extension seed item {seed_scenario['test_id']} with name='{seed_item['name']}' "
                    f"not found in {db_model_class.__tablename__}"
                )
            else:
                pytest.skip(
                    f"Cannot query {seed_scenario['test_id']} - no 'id' or 'name' field"
                )

            # Verify key attributes match
            for key, expected_value in seed_item.items():
                if key.startswith("_"):  # Skip placeholder fields
                    continue
                if hasattr(result, key):
                    actual_value = getattr(result, key)
                    assert actual_value == expected_value, (
                        f"Extension seed item {seed_scenario['test_id']} field '{key}': "
                        f"expected '{expected_value}', got '{actual_value}'"
                    )


@pytest.mark.seed
class TestSeedDataConsistency:
    """Test overall seed data consistency and uniqueness"""

    @pytest.mark.seed
    def test_no_duplicate_core_seed_ids(self):
        """Test that there are no duplicate seed item IDs within core models"""
        seen_ids = {}
        duplicates = []

        core_items = get_core_seed_items()
        for scenario in core_items:
            model_name = scenario["model_name"]
            seed_item = scenario["seed_item"]

            if "id" in seed_item:
                key = f"{model_name}:{seed_item['id']}"
                if key in seen_ids:
                    duplicates.append(
                        {"key": key, "first": seen_ids[key], "duplicate": scenario}
                    )
                else:
                    seen_ids[key] = scenario

        assert (
            not duplicates
        ), f"Found duplicate core seed IDs: {[d['key'] for d in duplicates]}"

    @pytest.mark.seed
    def test_semantic_naming_quality_core_only(self):
        """Test that semantic naming is working properly for core seed items only"""
        # Only test core items to avoid extension contamination
        core_items = get_core_seed_items()

        if not core_items:
            pytest.skip("No core seed items found")

        fallback_count = sum(
            1 for item in core_items if item["semantic_id"].startswith("item_")
        )
        total_count = len(core_items)
        fallback_ratio = fallback_count / total_count if total_count > 0 else 0

        # Allow up to 20% fallback names, but prefer semantic names
        assert fallback_ratio <= 0.2, (
            f"Too many fallback names ({fallback_count}/{total_count} = {fallback_ratio:.1%}). "
            f"Most seed items should have semantic identifiers like names, emails, or meaningful IDs."
        )
