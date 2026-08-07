import unittest
from typing import get_type_hints

from pydantic import BaseModel

from zephyrex.lib.AbstractPydantic2Test import (
    AbstractPydanticTestMixin,
    _ensure_discovery_completed,
    clear_discovery_cache,
    discover_bll_models_for_testing,
    get_all_models_for_testing,
    get_discovery_cache_stats,
    is_discovery_cached,
    print_discovery_cache_stats,
)
from zephyrex.lib.Logging import logger

# Ensure discovery is completed and cached at module import time
logger.debug("Initializing BLL discovery cache for AbstractPydanticTest tests...")
_ensure_discovery_completed()
print_discovery_cache_stats()


class TestAbstractPydanticTestFramework(unittest.TestCase):
    """
    Test suite for the AbstractPydanticTest framework functionality.
    Tests the discovery, caching, and abstraction layer.
    """

    @classmethod
    def setUpClass(cls):
        """Set up the test class with cached discovery."""
        logger.debug("\n=== Setting up AbstractPydanticTest Framework Tests ===")

        # Ensure discovery is completed and cached
        _ensure_discovery_completed()
        print_discovery_cache_stats()

    def setUp(self):
        """Set up each test."""
        # Verify cache is available
        if not is_discovery_cached():
            logger.debug("Warning: Discovery cache not available, reinitializing...")
            _ensure_discovery_completed()

    def test_discovery_cache_functionality(self):
        """Test that the discovery cache works correctly."""
        logger.debug("\n=== Testing Discovery Cache Functionality ===")

        # Verify cache is initialized
        self.assertTrue(is_discovery_cached(), "Discovery cache should be initialized")

        # Get cache stats
        stats = get_discovery_cache_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("models_cached", stats)
        self.assertIn("managers_cached", stats)
        self.assertIn("discovery_completed", stats)

        logger.debug(f"Cache stats: {stats}")

        # Verify we have cached models
        self.assertGreater(stats["models_cached"], 0, "Should have cached models")
        self.assertTrue(stats["discovery_completed"], "Discovery should be completed")

    def test_discover_bll_models_for_testing(self):
        """Test the main discovery function that returns models by domain."""
        logger.debug("\n=== Testing BLL Model Discovery by Domain ===")

        # Discover models by domain
        models_by_domain = discover_bll_models_for_testing()

        # Verify structure
        self.assertIsInstance(models_by_domain, dict)
        self.assertGreater(
            len(models_by_domain), 0, "Should discover at least one domain"
        )

        logger.debug(f"Discovered {len(models_by_domain)} domains:")

        total_models = 0
        for domain, models in models_by_domain.items():
            domain_name = f'"{domain}"' if domain == "" else domain
            logger.debug(f"  Domain {domain_name}: {len(models)} models")

            # Verify each domain has a list of tuples
            self.assertIsInstance(models, list)

            for model_name, model_class in models:
                # Verify tuple structure
                self.assertIsInstance(model_name, str)
                self.assertTrue(hasattr(model_class, "__name__"))
                self.assertTrue(issubclass(model_class, BaseModel))
                total_models += 1

                logger.debug(
                    f"    - {model_name}: {model_class.__module__}.{model_class.__name__}"
                )

        logger.debug(f"Total models discovered: {total_models}")
        self.assertGreater(total_models, 0, "Should discover at least some models")

    def test_get_all_models_for_testing(self):
        """Test the flattened model discovery function."""
        logger.debug("\n=== Testing Flattened Model Discovery ===")

        # Test without mocks
        models_no_mocks = get_all_models_for_testing(include_mocks=False)
        self.assertIsInstance(models_no_mocks, list)

        # Test with mocks
        models_with_mocks = get_all_models_for_testing(include_mocks=True)
        self.assertIsInstance(models_with_mocks, list)

        # Should have more models when including mocks
        self.assertGreaterEqual(len(models_with_mocks), len(models_no_mocks))

        logger.debug(f"Models without mocks: {len(models_no_mocks)}")
        logger.debug(f"Models with mocks: {len(models_with_mocks)}")

        # Verify structure of returned tuples
        for model_name, model_class in models_with_mocks[:5]:  # Check first 5
            self.assertIsInstance(model_name, str)
            self.assertTrue(hasattr(model_class, "__name__"))
            self.assertTrue(issubclass(model_class, BaseModel))
            logger.debug(f"  - {model_name}: {model_class.__name__}")

    def test_mock_model_discovery(self):
        """Test that mock models are discovered correctly."""
        logger.debug("\n=== Testing Mock Model Discovery ===")

        models_with_mocks = get_all_models_for_testing(include_mocks=True)
        models_without_mocks = get_all_models_for_testing(include_mocks=False)

        # Find mock models
        mock_models = []
        all_model_names = {name for name, _ in models_with_mocks}
        no_mock_names = {name for name, _ in models_without_mocks}

        for model_name, model_class in models_with_mocks:
            if model_name.startswith("Mock") or "mock" in model_name.lower():
                mock_models.append((model_name, model_class))

        logger.debug(f"Found {len(mock_models)} mock models:")
        for name, cls in mock_models:
            logger.debug(f"  - {name}: {cls.__name__}")

        # Verify mock models are only included when requested
        mock_only_names = all_model_names - no_mock_names
        logger.debug(f"Models only in 'with mocks': {mock_only_names}")

        # Should have some mock models when including them
        self.assertGreater(len(mock_models), 0, "Should discover mock models")

    def test_model_structure_validation(self):
        """Test that discovered models have the expected structure."""
        logger.debug("\n=== Testing Model Structure Validation ===")

        models = get_all_models_for_testing(include_mocks=True)

        structure_analysis = {
            "has_fields": 0,
            "has_reference_id": 0,
            "has_optional_refs": 0,
            "inherits_from_base_mixin": 0,
            "has_descriptions": 0,
        }

        for model_name, model_class in models:
            # Skip reference and network models for this analysis
            if model_name.endswith("ReferenceModel") or model_name.endswith(
                "NetworkModel"
            ):
                continue

            # Check if model has fields
            try:
                type_hints = get_type_hints(model_class)
                if len(type_hints) > 0:
                    structure_analysis["has_fields"] += 1
            except Exception:
                pass

            # Check for ReferenceID structure
            if hasattr(model_class, "Reference"):
                structure_analysis["has_reference_id"] += 1

                # Check for optional references
                if hasattr(model_class.Reference.ID, "Optional"):
                    structure_analysis["has_optional_refs"] += 1

            # Check inheritance from ApplicationModel
            if hasattr(model_class, "__mro__"):
                mro_names = [base.__name__ for base in model_class.__mro__]
                if "ApplicationModel" in mro_names:
                    structure_analysis["inherits_from_base_mixin"] += 1

            # Check for field descriptions
            has_descriptions = False
            if hasattr(model_class, "model_fields"):
                # Pydantic v2
                for field_info in model_class.model_fields.values():
                    if hasattr(field_info, "description") and field_info.description:
                        has_descriptions = True
                        break

            if has_descriptions:
                structure_analysis["has_descriptions"] += 1

        logger.debug("Model structure analysis:")
        for feature, count in structure_analysis.items():
            logger.debug(f"  {feature}: {count} models")

        # Verify we have models with expected features
        self.assertGreater(
            structure_analysis["has_fields"], 0, "Should have models with fields"
        )
        self.assertGreater(
            structure_analysis["has_reference_id"],
            0,
            "Should have models with Reference.ID",
        )
        self.assertGreater(
            structure_analysis["inherits_from_base_mixin"],
            0,
            "Should have models inheriting from ApplicationModel",
        )

    def test_cache_performance(self):
        """Test that caching provides performance benefits."""
        logger.debug("\n=== Testing Cache Performance ===")

        import time

        # Clear cache and time discovery
        clear_discovery_cache()
        self.assertFalse(is_discovery_cached(), "Cache should be cleared")

        # Time first discovery (cold)
        start_time = time.time()
        models_cold = get_all_models_for_testing(include_mocks=True)
        cold_time = time.time() - start_time

        # Verify cache is now populated
        self.assertTrue(
            is_discovery_cached(), "Cache should be populated after discovery"
        )

        # Time second discovery (warm)
        start_time = time.time()
        models_warm = get_all_models_for_testing(include_mocks=True)
        warm_time = time.time() - start_time

        logger.debug(f"Cold discovery time: {cold_time:.3f} seconds")
        logger.debug(f"Warm discovery time: {warm_time:.3f} seconds")

        # Verify results are the same
        self.assertEqual(
            len(models_cold), len(models_warm), "Should get same results from cache"
        )

        # Warm should be significantly faster (at least 2x)
        if cold_time > 0.1:  # Only test if cold time is meaningful
            speedup = cold_time / warm_time if warm_time > 0 else float("inf")
            logger.debug(f"Cache speedup: {speedup:.1f}x")
            self.assertGreater(speedup, 2.0, "Cache should provide significant speedup")

        # Restore cache for other tests
        _ensure_discovery_completed()

    def test_cache_consistency(self):
        """Test that cache provides consistent results across multiple calls."""
        logger.debug("\n=== Testing Cache Consistency ===")

        # Get models multiple times
        results = []
        for i in range(3):
            models = get_all_models_for_testing(include_mocks=True)
            results.append(set((name, cls.__name__) for name, cls in models))

        # All results should be identical
        first_result = results[0]
        for i, result in enumerate(results[1:], 1):
            self.assertEqual(
                first_result, result, f"Result {i+1} should match first result"
            )

        logger.debug(f"✓ Consistent results across {len(results)} calls")
        logger.debug(f"  Models discovered: {len(first_result)}")

    def test_domain_organization(self):
        """Test that models are properly organized by domain."""
        logger.debug("\n=== Testing Domain Organization ===")

        models_by_domain = discover_bll_models_for_testing()

        # Analyze domain organization
        domain_analysis = {}
        for domain, models in models_by_domain.items():
            domain_name = domain if domain else "root"
            domain_analysis[domain_name] = {
                "count": len(models),
                "model_names": [name for name, _ in models],
                "modules": set(cls.__module__ for _, cls in models),
            }

        logger.debug("Domain organization:")
        for domain, info in domain_analysis.items():
            logger.debug(
                f"  {domain}: {info['count']} models from {len(info['modules'])} modules"
            )

            # Show a few example models
            examples = info["model_names"][:3]
            if len(info["model_names"]) > 3:
                examples.append(f"... and {len(info['model_names']) - 3} more")
            logger.debug(f"    Examples: {', '.join(examples)}")

        # Verify we have reasonable domain organization
        self.assertGreater(len(domain_analysis), 0, "Should have at least one domain")

        # Check that models are in appropriate domains
        for domain, info in domain_analysis.items():
            self.assertGreater(info["count"], 0, f"Domain {domain} should have models")


class TestBLLModelMatrix(AbstractPydanticTestMixin, unittest.TestCase):
    """
    Parameterized matrix tests for individual BLL models.
    Each discovered BLL model will appear as a separate test in the pytest explorer.
    """

    @classmethod
    def setUpClass(cls):
        """Set up the matrix tests with cached discovery."""
        logger.debug("\n=== Setting up BLL Model Matrix Tests ===")

        # Ensure discovery is completed and cached
        _ensure_discovery_completed()
        print_discovery_cache_stats()

    def setUp(self):
        """Set up each test method."""
        # Verify cache is still available
        if not is_discovery_cached():
            logger.debug("Warning: Discovery cache not available, reinitializing...")
            _ensure_discovery_completed()

    def test_individual_model_discovery(self):
        """Test that individual BLL models can be discovered correctly."""
        models = get_all_models_for_testing(include_mocks=True)

        for model_name, model_class in models:
            with self.subTest(model=model_name):
                # Verify the model is a proper Pydantic BaseModel
                self.assertTrue(
                    issubclass(model_class, BaseModel),
                    f"{model_name} should be a Pydantic BaseModel subclass",
                )

                # Verify it has the expected structure
                self.assertTrue(
                    hasattr(model_class, "__name__"),
                    f"{model_name} should have __name__ attribute",
                )
                self.assertEqual(
                    model_class.__name__,
                    model_name,
                    f"Model class name should match {model_name}",
                )

                logger.debug(f"✓ {model_name} discovered successfully")

    def test_individual_model_field_analysis(self):
        """Test analysis of individual model's fields and structure."""
        models = get_all_models_for_testing(include_mocks=True)

        for model_name, model_class in models:
            with self.subTest(model=model_name):
                analysis = {
                    "fields": [],
                    "reference_fields": [],
                    "mixins": [],
                    "has_reference_id": False,
                    "has_optional_refs": False,
                }

                # Analyze fields
                try:
                    type_hints = get_type_hints(model_class)
                    analysis["fields"] = list(type_hints.keys())
                except Exception as e:
                    self.fail(f"Could not get type hints for {model_name}: {e}")

                # Analyze mixins
                if hasattr(model_class, "__bases__"):
                    for base in model_class.__bases__:
                        base_name = base.__name__
                        if "Mixin" in base_name:
                            analysis["mixins"].append(base_name)

                # Analyze Reference.ID structure
                if hasattr(model_class, "Reference") and hasattr(
                    model_class.Reference, "ID"
                ):
                    analysis["has_reference_id"] = True
                    ref_class = model_class.Reference.ID
                    try:
                        ref_fields = get_type_hints(ref_class)
                        analysis["reference_fields"] = list(ref_fields.keys())
                    except Exception as e:
                        self.fail(
                            f"Could not analyze Reference.ID for {model_name}: {e}"
                        )

                    # Check for optional references
                    if hasattr(ref_class, "Optional"):
                        analysis["has_optional_refs"] = True

                # Verify we have some meaningful structure
                self.assertGreater(
                    len(analysis["fields"]),
                    0,
                    f"{model_name} should have at least one field",
                )

                logger.debug(
                    f"✓ {model_name}: {len(analysis['fields'])} fields, {len(analysis['reference_fields'])} refs, {len(analysis['mixins'])} mixins"
                )

    def test_individual_model_inheritance(self):
        """Test that individual models have proper inheritance structure."""
        models = get_all_models_for_testing(include_mocks=True)

        for model_name, model_class in models:
            with self.subTest(model=model_name):
                # Get the method resolution order
                mro = model_class.__mro__
                mro_names = [cls.__name__ for cls in mro]

                # Should inherit from BaseModel
                self.assertIn(
                    "BaseModel",
                    mro_names,
                    f"{model_name} should inherit from BaseModel",
                )

                # Check for common mixin patterns
                mixin_count = sum(1 for name in mro_names if "Mixin" in name)

                logger.debug(
                    f"✓ {model_name}: MRO = {' -> '.join(mro_names[:4])}{'...' if len(mro_names) > 4 else ''}"
                )
                logger.debug(f"  Mixins: {mixin_count}")

    def test_matrix_comprehensive_report(self):
        """Generate a comprehensive report of all matrix test results."""
        logger.debug("\n" + "=" * 80)
        logger.debug("COMPREHENSIVE BLL MODEL DISCOVERY MATRIX TEST REPORT")
        logger.debug("=" * 80)

        # Discover all models
        bll_models_by_domain = discover_bll_models_for_testing()

        # Flatten for analysis
        bll_models = {}
        for domain, models in bll_models_by_domain.items():
            for model_name, model_class in models:
                bll_models[model_name] = model_class

        self.assertGreater(len(bll_models), 0, "Should discover at least one BLL model")
        logger.debug(
            f"✓ Discovered {len(bll_models)} BLL models across {len(bll_models_by_domain)} domains"
        )

        # Print all discovered models by domain
        logger.debug("Discovered models by domain:")
        for domain, models in bll_models_by_domain.items():
            domain_name = f'"{domain}"' if domain == "" else domain
            logger.debug(f"  Domain {domain_name}: {len(models)} models")
            for model_name, model_class in models:
                logger.debug(
                    f"    - {model_name}: {model_class.__module__}.{model_class.__name__}"
                )

        # Analyze model structure and dependencies
        logger.debug("\nModel structure analysis:")
        model_analysis = {}

        for name, model_class in bll_models.items():
            if (
                name.endswith("ReferenceModel")
                or name.endswith("NetworkModel")
                or "." in name
            ):
                continue

            # Skip mixin models themselves
            if (
                name.endswith("MixinModel")
                or name == "ApplicationModel"
                or name == "UpdateMixinModel"
                or name == "ImageMixinModel"
                or name == "ParentMixinModel"
            ):
                continue

            # Skip utility models
            if name.endswith("SearchModel") or name == "StringSearchModel":
                continue

            # Check if the model derives from ApplicationModel
            try:
                if hasattr(model_class, "__bases__"):
                    # Check if ApplicationModel is in the inheritance chain
                    mro = model_class.__mro__
                    has_base_mixin = any(
                        base.__name__ == "ApplicationModel" for base in mro
                    )

                    if not has_base_mixin:
                        continue

            except Exception as e:
                logger.debug(f"Error checking inheritance for {name}: {e}")
                continue

            analysis = {
                "fields": [],
                "reference_fields": [],
                "mixins": [],
                "has_reference_id": False,
                "has_optional_refs": False,
            }

            # Analyze fields
            try:
                type_hints = get_type_hints(model_class)
                analysis["fields"] = list(type_hints.keys())
            except Exception as e:
                logger.debug(f"  Warning: Could not get type hints for {name}: {e}")

            # Analyze mixins
            if hasattr(model_class, "__bases__"):
                for base in model_class.__bases__:
                    base_name = base.__name__
                    if "Mixin" in base_name:
                        analysis["mixins"].append(base_name)

            # Analyze Reference.ID structure
            if hasattr(model_class, "Reference") and hasattr(
                model_class.Reference, "ID"
            ):
                analysis["has_reference_id"] = True
                ref_class = model_class.Reference.ID
                try:
                    ref_fields = get_type_hints(ref_class)
                    analysis["reference_fields"] = list(ref_fields.keys())
                except Exception as e:
                    logger.debug(
                        f"  Warning: Could not analyze Reference.ID for {name}: {e}"
                    )

                # Check for optional references
                if hasattr(ref_class, "Optional"):
                    analysis["has_optional_refs"] = True

            model_analysis[name] = analysis
            logger.debug(
                f"  {name}: {len(analysis['fields'])} fields, {len(analysis['reference_fields'])} refs, {len(analysis['mixins'])} mixins"
            )

        # Generate summary statistics
        logger.debug(f"\nModel Analysis Summary:")
        mixin_usage = {}
        total_fields = 0
        total_refs = 0

        for name, analysis in model_analysis.items():
            total_fields += len(analysis["fields"])
            total_refs += len(analysis["reference_fields"])

            for mixin in analysis["mixins"]:
                mixin_usage[mixin] = mixin_usage.get(mixin, 0) + 1

        logger.debug(f"  Total Fields Processed: {total_fields}")
        logger.debug(f"  Total Reference Fields: {total_refs}")
        logger.debug(f"  Mixin Usage:")
        for mixin, count in mixin_usage.items():
            logger.debug(f"    {mixin}: {count} models")

        # Performance metrics
        logger.debug(f"\nPerformance Metrics:")
        import time

        # Time discovery
        start_time = time.time()
        discover_bll_models_for_testing()
        discovery_time = time.time() - start_time

        logger.debug(
            f"✓ Model discovery took {discovery_time:.3f} seconds for {len(bll_models)} models"
        )

        # Performance should be reasonable with caching
        self.assertLess(
            discovery_time,
            2.0,
            "Discovery should complete within 2 seconds with caching",
        )

        logger.debug(f"✓ Total discovery time: {discovery_time:.3f} seconds")


class TestAbstractPydanticTestMixin(unittest.TestCase):
    """Test the AbstractPydanticTestMixin functionality."""

    def test_mixin_inheritance(self):
        """Test that the mixin can be properly inherited."""

        class TestClass(AbstractPydanticTestMixin):
            pass

        test_instance = TestClass()

        # Verify mixin methods are available
        self.assertTrue(hasattr(test_instance, "setup_test_database"))
        self.assertTrue(hasattr(test_instance, "setup_test_session"))
        self.assertTrue(hasattr(test_instance, "teardown_test_session"))

        logger.debug("✓ AbstractPydanticTestMixin methods available")

    def test_mixin_with_unittest(self):
        """Test that the mixin works with unittest.TestCase."""

        class TestClassWithUnittest(AbstractPydanticTestMixin, unittest.TestCase):
            def test_example(self):
                self.assertTrue(True)

        # Verify both mixin and unittest methods are available
        test_instance = TestClassWithUnittest()
        self.assertTrue(hasattr(test_instance, "setup_test_database"))  # From mixin
        self.assertTrue(hasattr(test_instance, "assertTrue"))  # From unittest

        logger.debug("✓ AbstractPydanticTestMixin works with unittest.TestCase")


if __name__ == "__main__":
    # Run tests with verbose output
    unittest.main(verbosity=2)
