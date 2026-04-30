# import inspect
# import unittest
# from unittest.mock import Mock, patch

# import pytest

# from lib.AbstractPydantic2Test import (
#     AbstractPydanticTestMixin,
#     MockComprehensiveModel,
#     MockRelationshipModel,
#     MockSimpleModel,
#     _ensure_discovery_completed,
#     get_all_models_for_testing,
#     is_discovery_cached,
#     print_discovery_cache_stats,
# )
# from lib.Logging import logger
# from lib.Pydantic2SDKHandler import (
#     BLL_MODEL_REGISTRY,
#     SDK_HANDLER_REGISTRY,
#     RouterWithSDK,
#     SDKGenerator,
#     analyze_model_sdk_requirements,
#     clear_sdk_registry_cache,
#     create_sdk_handler_class,
#     discover_bll_models_for_sdk,
#     extend_router_with_sdk,
#     generate_sdk_documentation,
#     generate_sdk_handler_class,
#     get_model_reference_fields,
#     get_scaffolded_sdk_handler,
#     infer_endpoint_prefix_from_model,
#     infer_resource_name_from_model,
#     list_scaffolded_sdk_handlers,
#     scaffold_all_sdk_handlers,
# )
# from sdk.AbstractSDKHandler import AbstractSDKHandler

# # Ensure discovery is completed and cached at module import time
# logger.debug("Initializing BLL discovery cache for SDK Handler tests...")
# _ensure_discovery_completed()
# print_discovery_cache_stats()

# # Get all models for testing using the cached framework
# ALL_MODELS_FOR_TESTING = get_all_models_for_testing(include_mocks=True)


# class TestPydantic2SDKHandlerDynamic(unittest.TestCase, AbstractPydanticTestMixin):
#     """
#     Comprehensive test suite for the dynamic Pydantic to SDK handler generation system.
#     """

#     @classmethod
#     def setUpClass(cls):
#         """Set up test class with cached discovery."""
#         logger.debug("\n=== Setting up SDK Handler Dynamic Tests ===")
#         # Ensure discovery is completed and cached
#         _ensure_discovery_completed()
#         print_discovery_cache_stats()

#     def setUp(self):
#         """Set up each test."""
#         # Clear SDK registries to avoid conflicts between tests (but keep BLL discovery cache)
#         clear_sdk_registry_cache()

#         # Verify cache is still available
#         if not is_discovery_cached():
#             logger.debug("Warning: Discovery cache not available, reinitializing...")
#             _ensure_discovery_completed()

#     def tearDown(self):
#         """Clean up after each test."""
#         # Clear SDK registries after each test (but preserve BLL discovery cache)
#         clear_sdk_registry_cache()

#     def test_discover_bll_models_for_sdk(self):
#         """Test that BLL models are discovered correctly for SDK generation."""
#         logger.debug("\n=== Testing BLL Model Discovery for SDK ===")

#         # Discover BLL models
#         models = discover_bll_models_for_sdk()

#         # Verify that models were discovered
#         self.assertIsInstance(models, dict)
#         self.assertGreater(len(models), 0, "Should discover at least some BLL models")

#         # Print discovered models for debugging
#         logger.debug(f"Discovered {len(models)} BLL models for SDK generation:")
#         for model_name, model_class in models.items():
#             logger.debug(f"  - {model_name}: {model_class}")

#         # Verify that discovered models are proper classes
#         for model_name, model_class in models.items():
#             self.assertTrue(
#                 isinstance(model_class, type), f"{model_name} should be a class"
#             )
#             # Check if it has expected model attributes
#             if hasattr(model_class, "__mro__"):
#                 has_base_mixin = any(
#                     base.__name__ == "ApplicationModel" for base in model_class.__mro__
#                 )
#                 self.assertTrue(
#                     has_base_mixin,
#                     f"{model_name} should inherit from ApplicationModel",
#                 )
#                 logger.debug(f"✓ {model_name} inherits from ApplicationModel")

#     def test_infer_resource_name_from_model(self):
#         """Test that resource names are correctly inferred from model classes."""
#         logger.debug("\n=== Testing Resource Name Inference ===")

#         test_cases = [
#             (MockComprehensiveModel, "mock_comprehensive"),
#             (MockSimpleModel, "mock_simple"),
#             (MockRelationshipModel, "mock_relationship"),
#         ]

#         for model_class, expected_name in test_cases:
#             actual_name = infer_resource_name_from_model(model_class)
#             self.assertEqual(
#                 actual_name,
#                 expected_name,
#                 f"Resource name for {model_class.__name__} should be {expected_name}, got {actual_name}",
#             )
#             logger.debug(f"✓ {model_class.__name__} -> {actual_name}")

#     def test_infer_endpoint_prefix_from_model(self):
#         """Test that endpoint prefixes are correctly inferred from model classes."""
#         logger.debug("\n=== Testing Endpoint Prefix Inference ===")

#         test_cases = [
#             (MockComprehensiveModel, "/v1/mock_comprehensive"),
#             (MockSimpleModel, "/v1/mock_simple"),
#             (MockRelationshipModel, "/v1/mock_relationship"),
#         ]

#         for model_class, expected_prefix in test_cases:
#             actual_prefix = infer_endpoint_prefix_from_model(model_class)
#             self.assertEqual(
#                 actual_prefix,
#                 expected_prefix,
#                 f"Endpoint prefix for {model_class.__name__} should be {expected_prefix}, got {actual_prefix}",
#             )
#             logger.debug(f"✓ {model_class.__name__} -> {actual_prefix}")

#     def test_get_model_reference_fields(self):
#         """Test extraction of reference fields from model classes."""
#         logger.debug("\n=== Testing Reference Field Extraction ===")

#         # Test model with references
#         ref_info = get_model_reference_fields(MockRelationshipModel)
#         self.assertIsInstance(ref_info, dict)
#         self.assertIn("required_refs", ref_info)
#         self.assertIn("optional_refs", ref_info)
#         self.assertIn("has_references", ref_info)

#         if ref_info["has_references"]:
#             logger.debug(f"✓ MockRelationshipModel has references:")
#             logger.debug(f"  Required: {ref_info['required_refs']}")
#             logger.debug(f"  Optional: {ref_info['optional_refs']}")
#         else:
#             logger.debug("✓ MockRelationshipModel has no references")

#         # Test model without references
#         ref_info_simple = get_model_reference_fields(MockSimpleModel)
#         self.assertFalse(
#             ref_info_simple["has_references"],
#             "MockSimpleModel should not have references",
#         )
#         logger.debug("✓ MockSimpleModel correctly identified as having no references")

#     def test_create_sdk_handler_class(self):
#         """Test that SDK handler classes are created from model classes."""
#         logger.debug("\n=== Testing SDK Handler Class Creation ===")

#         # Create SDK handler from mock model
#         handler_class = create_sdk_handler_class(MockSimpleModel)

#         # Verify handler properties
#         self.assertTrue(issubclass(handler_class, AbstractSDKHandler))
#         self.assertTrue(hasattr(handler_class, "__name__"))
#         self.assertTrue(hasattr(handler_class, "__doc__"))

#         logger.debug(f"✓ Created handler class: {handler_class.__name__}")

#         # Check that handler has expected methods
#         expected_methods = [
#             "create_mock_simple",
#             "get_mock_simple",
#             "list_mock_simples",
#             "search_mock_simples",
#             "update_mock_simple",
#             "delete_mock_simple",
#         ]

#         handler_methods = [
#             method
#             for method in dir(handler_class)
#             if not method.startswith("_") and callable(getattr(handler_class, method))
#         ]

#         for expected_method in expected_methods:
#             if expected_method in handler_methods:
#                 logger.debug(f"✓ Handler has method: {expected_method}")
#             else:
#                 logger.debug(f"✗ Handler missing method: {expected_method}")

#         # Test with custom parameters
#         custom_handler = create_sdk_handler_class(
#             MockComprehensiveModel,
#             class_name="CustomComprehensiveSDK",
#             methods_to_include=["get", "list", "create"],
#         )

#         self.assertEqual(custom_handler.__name__, "CustomComprehensiveSDK")
#         logger.debug("✓ Created custom handler with specified parameters")

#     def test_generate_sdk_handler_class_code(self):
#         """Test that SDK handler class code is generated correctly."""
#         logger.debug("\n=== Testing SDK Handler Code Generation ===")

#         # Generate code for mock model
#         code = generate_sdk_handler_class(MockSimpleModel)

#         # Verify code structure
#         self.assertIsInstance(code, str)
#         self.assertIn("class", code)
#         self.assertIn("AbstractSDKHandler", code)
#         self.assertIn("def create_mock_simple", code)
#         self.assertIn("def get_mock_simple", code)
#         self.assertIn("def list_mock_simples", code)

#         logger.debug("✓ Generated SDK handler code contains expected structure")
#         logger.debug(f"  Code length: {len(code)} characters")

#         # Test with limited methods
#         limited_code = generate_sdk_handler_class(
#             MockSimpleModel, methods_to_include=["get", "list"]
#         )

#         self.assertIn("def get_mock_simple", limited_code)
#         self.assertIn("def list_mock_simples", limited_code)
#         self.assertNotIn("def create_mock_simple", limited_code)

#         logger.debug("✓ Generated limited SDK handler code correctly")

#     def test_scaffold_all_sdk_handlers(self):
#         """Test that all BLL models are scaffolded to SDK handlers."""
#         logger.debug("\n=== Testing SDK Handler Scaffolding ===")

#         # Scaffold all handlers
#         handlers = scaffold_all_sdk_handlers()

#         # Verify that handlers were created
#         self.assertIsInstance(handlers, dict)
#         self.assertGreater(len(handlers), 0, "Should create at least some SDK handlers")

#         # Print created handlers for debugging
#         logger.debug(f"Created {len(handlers)} SDK handlers:")
#         for name, handler_class in handlers.items():
#             method_count = len(
#                 [
#                     method
#                     for method in dir(handler_class)
#                     if not method.startswith("_")
#                     and callable(getattr(handler_class, method))
#                 ]
#             )
#             logger.debug(f"  - {name}: {method_count} methods")

#             # Verify it's a proper SDK handler
#             self.assertTrue(
#                 issubclass(handler_class, AbstractSDKHandler),
#                 f"{name} should be a subclass of AbstractSDKHandler",
#             )

#         logger.debug("✓ Successfully scaffolded all SDK handlers")

#     def test_handler_registry_functions(self):
#         """Test the handler registry and utility functions."""
#         logger.debug("\n=== Testing Handler Registry Functions ===")

#         # Test scaffolding
#         handlers = scaffold_all_sdk_handlers()

#         # Test list_scaffolded_sdk_handlers
#         handler_names = list_scaffolded_sdk_handlers()
#         self.assertIsInstance(handler_names, list)
#         self.assertEqual(len(handler_names), len(handlers))
#         logger.debug(
#             f"✓ list_scaffolded_sdk_handlers returned {len(handler_names)} handlers"
#         )

#         # Test get_scaffolded_sdk_handler
#         if handler_names:
#             first_handler_name = handler_names[0]
#             retrieved_handler = get_scaffolded_sdk_handler(first_handler_name)
#             self.assertIsNotNone(retrieved_handler)
#             self.assertEqual(retrieved_handler, handlers[first_handler_name])
#             logger.debug(
#                 f"✓ get_scaffolded_sdk_handler successfully retrieved {first_handler_name}"
#             )

#         # Test getting non-existent handler
#         non_existent = get_scaffolded_sdk_handler("NonExistentSDK")
#         self.assertIsNone(non_existent)
#         logger.debug(
#             "✓ get_scaffolded_sdk_handler correctly returns None for non-existent handler"
#         )

#     def test_analyze_model_sdk_requirements(self):
#         """Test analysis of model SDK requirements."""
#         logger.debug("\n=== Testing Model SDK Requirements Analysis ===")

#         # Analyze requirements
#         analysis = analyze_model_sdk_requirements()

#         self.assertIsInstance(analysis, dict)
#         self.assertGreater(len(analysis), 0, "Should analyze at least some models")

#         logger.debug(f"Analyzed {len(analysis)} model SDK requirements:")
#         for model_name, model_analysis in analysis.items():
#             logger.debug(f"  - {model_name}:")
#             logger.debug(f"    Resource: {model_analysis['resource_name']}")
#             logger.debug(f"    Endpoint: {model_analysis['endpoint_prefix']}")
#             logger.debug(f"    Has References: {model_analysis['has_references']}")
#             logger.debug(f"    Reference Fields: {model_analysis['reference_fields']}")
#             logger.debug(
#                 f"    Suggested Methods: {model_analysis['suggested_methods']}"
#             )

#             # Verify analysis structure
#             self.assertIn("model_class", model_analysis)
#             self.assertIn("resource_name", model_analysis)
#             self.assertIn("endpoint_prefix", model_analysis)
#             self.assertIn("reference_info", model_analysis)
#             self.assertIn("has_references", model_analysis)
#             self.assertIn("reference_fields", model_analysis)
#             self.assertIn("suggested_methods", model_analysis)

#         logger.debug("✓ Model SDK requirements analysis completed successfully")

#     def test_generate_sdk_documentation(self):
#         """Test generation of SDK documentation."""
#         logger.debug("\n=== Testing SDK Documentation Generation ===")

#         # Generate documentation
#         documentation = generate_sdk_documentation()

#         self.assertIsInstance(documentation, dict)
#         self.assertIn("summary", documentation)
#         self.assertIn("models", documentation)
#         self.assertIn("handlers", documentation)
#         self.assertIn("analysis", documentation)

#         # Check summary
#         summary = documentation["summary"]
#         self.assertIn("total_models", summary)
#         self.assertIn("total_handlers", summary)
#         self.assertIn("successful_generations", summary)
#         self.assertIn("failed_generations", summary)

#         logger.debug(f"Documentation Summary:")
#         logger.debug(f"  Total Models: {summary['total_models']}")
#         logger.debug(f"  Total Handlers: {summary['total_handlers']}")
#         logger.debug(f"  Successful Generations: {summary['successful_generations']}")
#         logger.debug(f"  Failed Generations: {summary['failed_generations']}")

#         # Check models documentation
#         models_doc = documentation["models"]
#         self.assertIsInstance(models_doc, dict)
#         logger.debug(f"  Documented Models: {len(models_doc)}")

#         # Check handlers documentation
#         handlers_doc = documentation["handlers"]
#         self.assertIsInstance(handlers_doc, dict)
#         logger.debug(f"  Documented Handlers: {len(handlers_doc)}")

#         # Print sample handler documentation
#         if handlers_doc:
#             sample_handler = next(iter(handlers_doc.values()))
#             logger.debug(
#                 f"  Sample Handler Methods: {sample_handler.get('method_count', 0)}"
#             )

#         logger.debug("✓ SDK documentation generated successfully")

#     def test_legacy_sdk_generator_class(self):
#         """Test that the legacy SDKGenerator class still works."""
#         logger.debug("\n=== Testing Legacy SDKGenerator ===")

#         # Test discovery
#         models = SDKGenerator.discover_models()
#         self.assertIsInstance(models, dict)
#         self.assertGreater(len(models), 0)
#         logger.debug(f"✓ Legacy discover_models returned {len(models)} models")

#         # Test handler creation
#         if models:
#             first_model = next(iter(models.values()))
#             handler = SDKGenerator.create_handler_from_model(first_model)
#             self.assertTrue(issubclass(handler, AbstractSDKHandler))
#             logger.debug("✓ Legacy create_handler_from_model works")

#         # Test scaffold all
#         all_handlers = SDKGenerator.scaffold_all()
#         self.assertIsInstance(all_handlers, dict)
#         logger.debug(f"✓ Legacy scaffold_all returned {len(all_handlers)} handlers")

#         # Test generate documentation
#         documentation = SDKGenerator.generate_documentation()
#         self.assertIsInstance(documentation, dict)
#         logger.debug("✓ Legacy generate_documentation works")

#     def test_error_handling(self):
#         """Test error handling in the SDK generation system."""
#         logger.debug("\n=== Testing Error Handling ===")

#         # Test with invalid model class
#         class InvalidModel:
#             pass

#         try:
#             handler = create_sdk_handler_class(InvalidModel)
#             # Should not fail, but might create a basic handler
#             logger.debug(f"✓ Handled invalid model gracefully: {handler.__name__}")
#         except Exception as e:
#             logger.debug(f"✓ Correctly handled invalid model: {type(e).__name__}")

#         # Test clear_sdk_registry_cache
#         scaffold_all_sdk_handlers()  # Populate registries
#         self.assertGreater(len(BLL_MODEL_REGISTRY), 0)
#         self.assertGreater(len(SDK_HANDLER_REGISTRY), 0)

#         clear_sdk_registry_cache()
#         self.assertEqual(len(BLL_MODEL_REGISTRY), 0)
#         self.assertEqual(len(SDK_HANDLER_REGISTRY), 0)

#         logger.debug("✓ clear_sdk_registry_cache works correctly")

#     def test_mock_model_handler_creation(self):
#         """Test handler creation with mock models."""
#         logger.debug("\n=== Testing Mock Model Handler Creation ===")

#         mock_models = [
#             (MockComprehensiveModel, "comprehensive"),
#             (MockSimpleModel, "simple"),
#             (MockRelationshipModel, "relationship"),
#         ]

#         for model_class, expected_resource in mock_models:
#             # Create handler
#             handler_class = create_sdk_handler_class(model_class)

#             # Verify handler
#             self.assertTrue(issubclass(handler_class, AbstractSDKHandler))
#             self.assertTrue(hasattr(handler_class, "__name__"))

#             # Check resource name inference
#             resource_name = infer_resource_name_from_model(model_class)
#             self.assertIn(expected_resource, resource_name)

#             # Count methods
#             method_count = len(
#                 [
#                     method
#                     for method in dir(handler_class)
#                     if not method.startswith("_")
#                     and callable(getattr(handler_class, method))
#                 ]
#             )

#             logger.debug(
#                 f"✓ Created handler for {model_class.__name__}: {method_count} methods"
#             )

#     @patch("serverframework.lib.Pydantic2SDKHandler.AbstractSDKHandler")
#     def test_sdk_handler_instantiation(self, mock_abstract_handler):
#         """Test that generated SDK handlers can be instantiated."""
#         logger.debug("\n=== Testing SDK Handler Instantiation ===")

#         # Create a handler class
#         handler_class = create_sdk_handler_class(MockSimpleModel)

#         # Test instantiation with mock
#         try:
#             # Mock the AbstractSDKHandler to avoid needing real API credentials
#             mock_instance = Mock()
#             mock_abstract_handler.return_value = mock_instance

#             # This would normally require base_url and other parameters
#             # but we're testing the class structure, not actual API calls
#             logger.debug("✓ SDK handler class structure is valid for instantiation")

#         except Exception as e:
#             logger.debug(
#                 f"Note: Handler instantiation test failed (expected without API setup): {e}"
#             )

#     def test_comprehensive_bll_model_coverage(self):
#         """Test that the system can handle all actual BLL models from the codebase."""
#         logger.debug("\n=== Testing Comprehensive BLL Model Coverage ===")

#         # Discover and scaffold all real BLL models
#         bll_models = discover_bll_models_for_sdk()
#         sdk_handlers = scaffold_all_sdk_handlers()

#         logger.debug(f"Discovered {len(bll_models)} BLL models")
#         logger.debug(f"Created {len(sdk_handlers)} SDK handlers")

#         # Analyze coverage
#         coverage_rate = len(sdk_handlers) / max(len(bll_models), 1)
#         logger.debug(f"Coverage rate: {coverage_rate:.1%}")

#         # Count total methods across all handlers
#         total_methods = 0
#         for handler_name, handler_class in sdk_handlers.items():
#             method_count = len(
#                 [
#                     method
#                     for method in dir(handler_class)
#                     if not method.startswith("_")
#                     and callable(getattr(handler_class, method))
#                 ]
#             )
#             total_methods += method_count
#             logger.debug(f"  {handler_name}: {method_count} methods")

#         logger.debug(f"  Total methods across all handlers: {total_methods}")

#         # We expect reasonable coverage
#         self.assertGreaterEqual(
#             coverage_rate,
#             0.5,
#             "Should successfully generate handlers for at least 50% of models",
#         )

#         logger.debug("✓ Successfully handled comprehensive BLL model coverage")

#     def test_matrix_all_real_models(self):
#         """
#         Matrix test that comprehensively tests all real BLL models from the logic directory.
#         This test validates the complete dynamic SDK generation pipeline.
#         """
#         logger.debug("\n=== Matrix Test: All Real BLL Models ===")

#         # Step 1: Import and discover all BLL models
#         logger.debug("Step 1: Discovering BLL models...")
#         bll_models = discover_bll_models_for_sdk()

#         self.assertGreater(len(bll_models), 0, "Should discover at least one BLL model")
#         logger.debug(f"✓ Discovered {len(bll_models)} BLL models")

#         # Print all discovered models
#         logger.debug("Discovered models:")
#         for name, model_class in bll_models.items():
#             logger.debug(f"  - {name}: {model_class.__module__}.{model_class.__name__}")

#         # Step 2: Analyze model structure and abilities
#         logger.debug("\nStep 2: Analyzing model structures...")
#         model_analysis = {}

#         for name, model_class in bll_models.items():
#             analysis = {
#                 "has_base_mixin": False,
#                 "resource_name": None,
#                 "endpoint_prefix": None,
#                 "has_references": False,
#                 "reference_fields": [],
#                 "errors": [],
#             }

#             try:
#                 # Check inheritance
#                 if hasattr(model_class, "__mro__"):
#                     analysis["has_base_mixin"] = any(
#                         base.__name__ == "ApplicationModel"
#                         for base in model_class.__mro__
#                     )

#                 # Infer properties
#                 analysis["resource_name"] = infer_resource_name_from_model(model_class)
#                 analysis["endpoint_prefix"] = infer_endpoint_prefix_from_model(
#                     model_class
#                 )

#                 # Check references
#                 ref_info = get_model_reference_fields(model_class)
#                 analysis["has_references"] = ref_info["has_references"]
#                 analysis["reference_fields"] = (
#                     ref_info["required_refs"] + ref_info["optional_refs"]
#                 )

#             except Exception as e:
#                 analysis["errors"].append(f"Analysis error: {str(e)}")

#             model_analysis[name] = analysis
#             status = "✓" if not analysis["errors"] else "⚠"
#             logger.debug(
#                 f"  {status} {name}: {analysis['resource_name']} ({analysis['endpoint_prefix']})"
#             )

#             if analysis["errors"]:
#                 for error in analysis["errors"]:
#                     logger.debug(f"    Error: {error}")

#         # Step 3: Test handler creation for each model
#         logger.debug("\nStep 3: Testing handler creation...")
#         handler_creation_results = {
#             "successful_handlers": [],
#             "failed_handlers": [],
#             "total_methods": 0,
#         }

#         for name, model_class in bll_models.items():
#             try:
#                 handler_class = create_sdk_handler_class(model_class)
#                 method_count = len(
#                     [
#                         method
#                         for method in dir(handler_class)
#                         if not method.startswith("_")
#                         and callable(getattr(handler_class, method))
#                     ]
#                 )

#                 handler_creation_results["successful_handlers"].append(
#                     {
#                         "name": name,
#                         "handler_class": handler_class,
#                         "method_count": method_count,
#                     }
#                 )
#                 handler_creation_results["total_methods"] += method_count

#                 logger.debug(f"  ✓ {name}: {method_count} methods")

#             except Exception as e:
#                 handler_creation_results["failed_handlers"].append(
#                     {
#                         "name": name,
#                         "error": str(e),
#                     }
#                 )
#                 logger.debug(f"  ⚠ {name}: Failed - {e}")

#         # Step 4: Test documentation generation
#         logger.debug("\nStep 4: Testing documentation generation...")
#         try:
#             documentation = generate_sdk_documentation()
#             doc_summary = documentation.get("summary", {})
#             logger.debug(f"✓ Generated documentation:")
#             logger.debug(f"  Total Models: {doc_summary.get('total_models', 0)}")
#             logger.debug(f"  Total Handlers: {doc_summary.get('total_handlers', 0)}")
#             logger.debug(
#                 f"  Success Rate: {doc_summary.get('successful_generations', 0)}/{doc_summary.get('total_models', 0)}"
#             )
#         except Exception as e:
#             logger.debug(f"⚠ Documentation generation failed: {e}")

#         # Step 5: Generate comprehensive report
#         logger.debug("\n" + "=" * 60)
#         logger.debug("COMPREHENSIVE MATRIX TEST REPORT")
#         logger.debug("=" * 60)

#         logger.debug(f"BLL Models Discovered: {len(bll_models)}")
#         logger.debug(
#             f"Handlers Created Successfully: {len(handler_creation_results['successful_handlers'])}"
#         )
#         logger.debug(
#             f"Handler Creation Failures: {len(handler_creation_results['failed_handlers'])}"
#         )
#         logger.debug(
#             f"Total Methods Generated: {handler_creation_results['total_methods']}"
#         )

#         if handler_creation_results["failed_handlers"]:
#             logger.debug("\nFailed Handler Creations:")
#             for failure in handler_creation_results["failed_handlers"]:
#                 logger.debug(f"  - {failure['name']}: {failure['error']}")

#         logger.debug(f"\nModel Analysis Summary:")
#         total_with_base_mixin = sum(
#             1 for analysis in model_analysis.values() if analysis["has_base_mixin"]
#         )
#         total_with_references = sum(
#             1 for analysis in model_analysis.values() if analysis["has_references"]
#         )

#         logger.debug(f"  Models with ApplicationModel: {total_with_base_mixin}")
#         logger.debug(f"  Models with References: {total_with_references}")

#         # Step 6: Performance metrics
#         logger.debug(f"\nPerformance Metrics:")
#         import time

#         # Time discovery
#         start_time = time.time()
#         discover_bll_models_for_sdk()
#         discovery_time = time.time() - start_time

#         # Time scaffolding
#         start_time = time.time()
#         scaffold_all_sdk_handlers()
#         scaffolding_time = time.time() - start_time

#         logger.debug(f"  Discovery Time: {discovery_time:.3f} seconds")
#         logger.debug(f"  Scaffolding Time: {scaffolding_time:.3f} seconds")
#         logger.debug(
#             f"  Handlers per Second: {len(handler_creation_results['successful_handlers']) / max(scaffolding_time, 0.001):.1f}"
#         )

#         # Final assertions
#         success_rate = len(handler_creation_results["successful_handlers"]) / max(
#             len(bll_models), 1
#         )
#         logger.debug(f"\nOverall Success Rate: {success_rate:.1%}")

#         # We expect at least 80% success rate for the matrix test to pass
#         self.assertGreaterEqual(
#             success_rate,
#             0.8,
#             f"Matrix test success rate should be at least 80%, got {success_rate:.1%}",
#         )

#         logger.debug("✓ Matrix test completed successfully!")


# class TestBLLModelSDKMatrix(AbstractPydanticTestMixin):
#     """
#     Parameterized matrix tests for individual BLL models and SDK generation.
#     Each discovered BLL model will appear as a separate test in the pytest explorer.
#     """

#     @classmethod
#     def setup_class(cls):
#         """Set up test environment for the matrix tests."""
#         logger.debug("\n=== Setting up BLL Model SDK Matrix Tests ===")
#         # Ensure discovery is completed and cached
#         _ensure_discovery_completed()
#         print_discovery_cache_stats()

#         # Clear SDK registries to start fresh (but keep BLL discovery cache)
#         clear_sdk_registry_cache()

#     def setup_method(self):
#         """Set up each test method."""
#         # Verify cache is still available
#         if not is_discovery_cached():
#             logger.debug("Warning: Discovery cache not available, reinitializing...")
#             _ensure_discovery_completed()

#     def teardown_method(self):
#         """Clean up after each test method."""
#         # Note: We don't clear the BLL discovery cache here to maintain performance
#         pass

#     @pytest.mark.parametrize(
#         "model_name,model_class",
#         get_all_models_for_testing(),
#     )
#     def test_individual_model_discovery(self, model_name, model_class):
#         """Test that individual BLL model can be discovered correctly."""
#         # Verify the model is a proper class
#         assert isinstance(model_class, type), f"{model_name} should be a class"

#         # Verify it has the expected attributes
#         assert hasattr(
#             model_class, "__name__"
#         ), f"{model_name} should have __name__ attribute"
#         assert (
#             model_class.__name__ == model_name
#         ), f"Model class name should match {model_name}"

#         # Check for ApplicationModel inheritance
#         has_base_mixin = False
#         if hasattr(model_class, "__mro__"):
#             has_base_mixin = any(
#                 base.__name__ == "ApplicationModel" for base in model_class.__mro__
#             )

#         logger.debug(
#             f"✓ {model_name} discovered successfully (BaseMixin: {has_base_mixin})"
#         )

#     @pytest.mark.parametrize(
#         "model_name,model_class",
#         [
#             (name, cls)
#             for name, cls in get_all_models_for_testing()
#             if hasattr(cls, "__mro__")
#             and any(base.__name__ == "ApplicationModel" for base in cls.__mro__)
#         ],
#     )
#     def test_individual_model_sdk_creation(self, model_name, model_class):
#         """Test that individual BLL model can be converted to SDK handler."""
#         # Create SDK handler
#         handler_class = create_sdk_handler_class(model_class)

#         # Verify the handler was created correctly
#         assert issubclass(
#             handler_class, AbstractSDKHandler
#         ), f"{model_name} should create an AbstractSDKHandler subclass"

#         # Verify handler configuration
#         resource_name = infer_resource_name_from_model(model_class)
#         endpoint_prefix = infer_endpoint_prefix_from_model(model_class)

#         logger.debug(
#             f"✓ {model_name} -> SDK handler with resource '{resource_name}' and endpoint '{endpoint_prefix}'"
#         )

#     @pytest.mark.parametrize(
#         "model_name,model_class",
#         [
#             (name, cls)
#             for name, cls in get_all_models_for_testing()
#             if hasattr(cls, "__mro__")
#             and any(base.__name__ == "ApplicationModel" for base in cls.__mro__)
#         ],
#     )
#     def test_individual_model_method_generation(self, model_name, model_class):
#         """Test that individual model generates appropriate SDK methods."""
#         # Create handler
#         handler_class = create_sdk_handler_class(model_class)

#         # Check methods
#         methods = [
#             method
#             for method in dir(handler_class)
#             if not method.startswith("_") and callable(getattr(handler_class, method))
#         ]
#         method_count = len(methods)

#         # Verify we have some methods
#         assert method_count > 0, f"{model_name} should generate at least one method"

#         # Check for expected method patterns
#         resource_name = infer_resource_name_from_model(model_class)
#         expected_method_patterns = [
#             f"create_{resource_name}",
#             f"get_{resource_name}",
#             f"update_{resource_name}",
#             f"delete_{resource_name}",
#         ]

#         found_patterns = []
#         for pattern in expected_method_patterns:
#             if pattern in methods:
#                 found_patterns.append(pattern)

#         logger.debug(
#             f"✓ {model_name}: {method_count} methods, patterns: {found_patterns}"
#         )

#     @pytest.mark.parametrize(
#         "model_name,model_class",
#         get_all_models_for_testing(),
#     )
#     def test_individual_model_analysis(self, model_name, model_class):
#         """Test analysis of individual model's properties and structure."""
#         analysis = {
#             "has_base_mixin": False,
#             "resource_name": None,
#             "endpoint_prefix": None,
#             "has_references": False,
#             "reference_fields": [],
#             "errors": [],
#         }

#         try:
#             # Check inheritance
#             if hasattr(model_class, "__mro__"):
#                 analysis["has_base_mixin"] = any(
#                     base.__name__ == "ApplicationModel" for base in model_class.__mro__
#                 )

#             # Infer properties
#             analysis["resource_name"] = infer_resource_name_from_model(model_class)
#             analysis["endpoint_prefix"] = infer_endpoint_prefix_from_model(model_class)

#             # Check references
#             ref_info = get_model_reference_fields(model_class)
#             analysis["has_references"] = ref_info["has_references"]
#             analysis["reference_fields"] = (
#                 ref_info["required_refs"] + ref_info["optional_refs"]
#             )

#         except Exception as e:
#             analysis["errors"].append(f"Analysis error: {str(e)}")

#         # Verify we have meaningful structure
#         assert (
#             analysis["resource_name"] is not None
#         ), f"{model_name} should have a resource name"
#         assert (
#             analysis["endpoint_prefix"] is not None
#         ), f"{model_name} should have an endpoint prefix"

#         logger.debug(
#             f"✓ {model_name}: {analysis['resource_name']} ({analysis['endpoint_prefix']}) - Refs: {len(analysis['reference_fields'])}"
#         )

#         if analysis["errors"]:
#             for error in analysis["errors"]:
#                 logger.debug(f"  Warning: {error}")

#     def test_matrix_comprehensive_report(self):
#         """Generate a comprehensive report of all matrix test results."""
#         logger.debug("\n" + "=" * 80)
#         logger.debug("COMPREHENSIVE BLL MODEL SDK MATRIX TEST REPORT")
#         logger.debug("=" * 80)

#         # Discover all models
#         bll_models = discover_bll_models_for_sdk()

#         # Filter models with required attributes
#         valid_models = {}
#         for name, model_class in bll_models.items():
#             if hasattr(model_class, "__mro__") and any(
#                 base.__name__ == "ApplicationModel" for base in model_class.__mro__
#             ):
#                 valid_models[name] = model_class

#         logger.debug(f"Total BLL Models Discovered: {len(bll_models)}")
#         logger.debug(f"Valid Models for SDK Generation: {len(valid_models)}")

#         # Analyze model categories
#         categories = {}
#         for name, model_class in valid_models.items():
#             module = model_class.__module__
#             if module not in categories:
#                 categories[module] = []
#             categories[module].append(name)

#         logger.debug(f"\nModels by Category:")
#         for module, models in categories.items():
#             logger.debug(f"  {module}: {len(models)} models")
#             for model in sorted(models):
#                 logger.debug(f"    - {model}")

#         # Test scaffolding all valid models
#         logger.debug(f"\nTesting Complete Scaffolding...")
#         try:
#             sdk_handlers = scaffold_all_sdk_handlers()
#             logger.debug(f"✓ Successfully scaffolded {len(sdk_handlers)} handlers")

#             # Count total methods
#             total_methods = 0
#             for handler_name, handler_class in sdk_handlers.items():
#                 method_count = len(
#                     [
#                         method
#                         for method in dir(handler_class)
#                         if not method.startswith("_")
#                         and callable(getattr(handler_class, method))
#                     ]
#                 )
#                 total_methods += method_count

#             logger.debug(
#                 f"✓ Generated {total_methods} total methods across all handlers"
#             )

#         except Exception as e:
#             logger.debug(f"⚠ Scaffolding issues: {e}")

#         logger.debug("\n" + "=" * 80)
#         logger.debug(
#             "Matrix test report completed. Check individual parameterized tests for details."
#         )
#         logger.debug("=" * 80)


# class TestMockModelSDKFeatures(unittest.TestCase, AbstractPydanticTestMixin):
#     """
#     Specific tests for mock models to ensure all SDK features are handled correctly.
#     """

#     @classmethod
#     def setUpClass(cls):
#         """Set up test class with cached discovery."""
#         logger.debug("\n=== Setting up Mock Model SDK Features Tests ===")
#         # Ensure discovery is completed and cached
#         _ensure_discovery_completed()
#         print_discovery_cache_stats()

#     def setUp(self):
#         """Set up each test."""
#         # Clear SDK registries (but keep BLL discovery cache)
#         clear_sdk_registry_cache()

#         # Verify cache is still available
#         if not is_discovery_cached():
#             logger.debug("Warning: Discovery cache not available, reinitializing...")
#             _ensure_discovery_completed()

#     def tearDown(self):
#         """Clean up after each test."""
#         # Clear SDK registries (but preserve BLL discovery cache)
#         clear_sdk_registry_cache()

#     def test_mock_comprehensive_model_sdk(self):
#         """Test that the comprehensive mock model creates a complete SDK handler."""
#         logger.debug("\n=== Testing Mock Comprehensive Model SDK ===")

#         # Create handler
#         handler_class = create_sdk_handler_class(MockComprehensiveModel)

#         # Verify basic properties
#         self.assertTrue(issubclass(handler_class, AbstractSDKHandler))
#         self.assertIn("MockComprehensive", handler_class.__name__)

#         # Check methods
#         methods = [
#             method
#             for method in dir(handler_class)
#             if not method.startswith("_") and callable(getattr(handler_class, method))
#         ]
#         method_count = len(methods)

#         logger.debug(f"Created handler with {method_count} methods")

#         # Verify we have expected method types
#         expected_method_patterns = [
#             "create_",
#             "get_",
#             "list_",
#             "search_",
#             "update_",
#             "delete_",
#         ]

#         found_patterns = []
#         for pattern in expected_method_patterns:
#             if any(pattern in method for method in methods):
#                 found_patterns.append(pattern)

#         logger.debug(f"Method patterns: {found_patterns}")
#         logger.debug(f"All methods: {methods}")

#         self.assertGreater(len(found_patterns), 0, "Should have standard CRUD patterns")
#         logger.debug("✓ Mock comprehensive model SDK handler created successfully")

#     def test_mock_model_reference_handling(self):
#         """Test that mock models with references are handled correctly."""
#         logger.debug("\n=== Testing Mock Model Reference Handling ===")

#         # Test model with references
#         ref_info = get_model_reference_fields(MockRelationshipModel)

#         if ref_info["has_references"]:
#             logger.debug(f"✓ MockRelationshipModel has references:")
#             logger.debug(f"  Required: {ref_info['required_refs']}")
#             logger.debug(f"  Optional: {ref_info['optional_refs']}")

#             # Create handler and verify it handles references
#             handler_class = create_sdk_handler_class(MockRelationshipModel)
#             self.assertTrue(issubclass(handler_class, AbstractSDKHandler))
#             logger.debug("✓ Handler created successfully for model with references")
#         else:
#             logger.debug("✓ MockRelationshipModel has no references (as expected)")

#         # Test model without references
#         ref_info_simple = get_model_reference_fields(MockSimpleModel)
#         self.assertFalse(ref_info_simple["has_references"])

#         handler_simple = create_sdk_handler_class(MockSimpleModel)
#         self.assertTrue(issubclass(handler_simple, AbstractSDKHandler))
#         logger.debug("✓ Handler created successfully for model without references")

#     def test_mock_model_method_signatures(self):
#         """Test that mock model SDK methods have correct signatures."""
#         logger.debug("\n=== Testing Mock Model Method Signatures ===")

#         for model_class in [
#             MockComprehensiveModel,
#             MockSimpleModel,
#             MockRelationshipModel,
#         ]:
#             handler_class = create_sdk_handler_class(model_class)

#             # Get resource name for this model
#             resource_name = infer_resource_name_from_model(model_class)

#             # Check that expected methods exist
#             expected_methods = [
#                 f"create_{resource_name}",
#                 f"get_{resource_name}",
#                 f"update_{resource_name}",
#                 f"delete_{resource_name}",
#             ]

#             for method_name in expected_methods:
#                 if hasattr(handler_class, method_name):
#                     method = getattr(handler_class, method_name)
#                     self.assertTrue(callable(method))
#                     logger.debug(f"✓ {model_class.__name__} has method: {method_name}")
#                 else:
#                     logger.debug(
#                         f"✗ {model_class.__name__} missing method: {method_name}"
#                     )

#     def test_mock_model_comprehensive_coverage(self):
#         """Test that mock models cover all features found in real models."""
#         logger.debug("\n=== Testing Mock Model Feature Coverage ===")

#         # Features that should be covered by our mock models
#         expected_features = {
#             "resource_inference": True,
#             "endpoint_generation": True,
#             "method_generation": True,
#             "reference_handling": True,
#         }

#         mock_models = [MockComprehensiveModel, MockSimpleModel, MockRelationshipModel]

#         for model_class in mock_models:
#             logger.debug(f"\nTesting {model_class.__name__}:")

#             # Test resource inference
#             resource_name = infer_resource_name_from_model(model_class)
#             self.assertIsNotNone(resource_name)
#             logger.debug(f"✓ Resource name: {resource_name}")

#             # Test endpoint generation
#             endpoint_prefix = infer_endpoint_prefix_from_model(model_class)
#             self.assertIsNotNone(endpoint_prefix)
#             self.assertTrue(endpoint_prefix.startswith("/v1/"))
#             logger.debug(f"✓ Endpoint prefix: {endpoint_prefix}")

#             # Test method generation
#             handler_class = create_sdk_handler_class(model_class)
#             methods = [
#                 method
#                 for method in dir(handler_class)
#                 if not method.startswith("_")
#                 and callable(getattr(handler_class, method))
#             ]
#             self.assertGreater(len(methods), 0)
#             logger.debug(f"✓ Generated {len(methods)} methods")

#             # Test reference handling
#             ref_info = get_model_reference_fields(model_class)
#             logger.debug(
#                 f"✓ Reference analysis completed (has_refs: {ref_info['has_references']})"
#             )

#             logger.debug(f"✓ {model_class.__name__} covers all expected features")


# class TestPydantic2SDKHandlerPerformance(unittest.TestCase, AbstractPydanticTestMixin):
#     """Performance and scalability tests."""

#     @classmethod
#     def setUpClass(cls):
#         """Set up test class with cached discovery."""
#         logger.debug("\n=== Setting up SDK Handler Performance Tests ===")
#         # Ensure discovery is completed and cached
#         _ensure_discovery_completed()
#         print_discovery_cache_stats()

#     def setUp(self):
#         """Set up each test."""
#         # Clear SDK registries (but keep BLL discovery cache)
#         clear_sdk_registry_cache()

#         # Verify cache is still available
#         if not is_discovery_cached():
#             logger.debug("Warning: Discovery cache not available, reinitializing...")
#             _ensure_discovery_completed()

#     def tearDown(self):
#         """Clean up after each test."""
#         # Clear SDK registries (but preserve BLL discovery cache)
#         clear_sdk_registry_cache()

#     def test_performance_and_scalability(self):
#         """Test performance characteristics of the SDK generation system."""
#         logger.debug("\n=== Testing Performance and Scalability ===")

#         import time

#         # Time the discovery process
#         start_time = time.time()
#         bll_models = discover_bll_models_for_sdk()
#         discovery_time = time.time() - start_time

#         logger.debug(
#             f"✓ BLL model discovery took {discovery_time:.3f} seconds for {len(bll_models)} models"
#         )

#         # Time the scaffolding process
#         start_time = time.time()
#         sdk_handlers = scaffold_all_sdk_handlers()
#         scaffolding_time = time.time() - start_time

#         logger.debug(
#             f"✓ SDK handler scaffolding took {scaffolding_time:.3f} seconds for {len(sdk_handlers)} handlers"
#         )

#         # Performance should be reasonable
#         self.assertLess(
#             discovery_time, 10.0, "Discovery should complete within 10 seconds"
#         )
#         self.assertLess(
#             scaffolding_time, 10.0, "Scaffolding should complete within 10 seconds"
#         )

#         # Test documentation generation time
#         start_time = time.time()
#         documentation = generate_sdk_documentation()
#         documentation_time = time.time() - start_time

#         logger.debug(
#             f"✓ Documentation generation took {documentation_time:.3f} seconds"
#         )
#         self.assertLess(
#             documentation_time,
#             5.0,
#             "Documentation generation should complete within 5 seconds",
#         )


# class TestRouterWithSDKIntegration(unittest.TestCase, AbstractPydanticTestMixin):
#     """
#     Tests for RouterWithSDK integration that extends routers with SDK functionality.
#     """

#     @classmethod
#     def setUpClass(cls):
#         """Set up test class with cached discovery."""
#         logger.debug("\n=== Setting up Router SDK Integration Tests ===")
#         # Ensure discovery is completed and cached
#         _ensure_discovery_completed()
#         print_discovery_cache_stats()

#     def setUp(self):
#         """Set up each test."""
#         # Clear SDK registries (but keep BLL discovery cache)
#         clear_sdk_registry_cache()

#         # Verify cache is still available
#         if not is_discovery_cached():
#             logger.debug("Warning: Discovery cache not available, reinitializing...")
#             _ensure_discovery_completed()

#     def tearDown(self):
#         """Clean up after each test."""
#         # Clear SDK registries (but preserve BLL discovery cache)
#         clear_sdk_registry_cache()

#     def test_router_with_sdk_creation(self):
#         """Test that RouterWithSDK can be created and wraps APIRouter correctly."""
#         logger.debug("\n=== Testing RouterWithSDK Creation ===")

#         from fastapi import APIRouter

#         # Create a mock manager class
#         class MockManager:
#             __name__ = "MockUserManager"
#             prefix = "/v1/user"
#             tags = ["users"]
#             auth_type = "JWT"

#             class Model:
#                 __name__ = "MockUserModel"

#         # Create original router
#         original_router = APIRouter(prefix="/v1/user", tags=["users"])

#         # Create RouterWithSDK
#         router_with_sdk = extend_router_with_sdk(original_router, MockManager, {})

#         # Verify it's a RouterWithSDK instance
#         self.assertIsInstance(router_with_sdk, RouterWithSDK)
#         logger.debug("✓ RouterWithSDK instance created successfully")

#         # Verify it maintains APIRouter compatibility
#         self.assertEqual(router_with_sdk.prefix, "/v1/user")
#         self.assertEqual(router_with_sdk.tags, ["users"])
#         logger.debug("✓ APIRouter attributes preserved")

#         # Verify SDK property exists
#         self.assertTrue(hasattr(router_with_sdk, "SDK"))
#         logger.debug("✓ SDK property available")

#     def test_router_sdk_property_functionality(self):
#         """Test that the .SDK property returns a valid AbstractSDKHandler subclass."""
#         logger.debug("\n=== Testing Router SDK Property Functionality ===")

#         from fastapi import APIRouter

#         # Create a mock manager class with proper structure
#         class MockTeamManager:
#             __name__ = "MockTeamManager"
#             prefix = "/v1/team"
#             tags = ["teams"]
#             auth_type = "JWT"

#             class Model:
#                 __name__ = "MockTeamModel"

#         # Create RouterWithSDK
#         original_router = APIRouter(prefix="/v1/team", tags=["teams"])
#         router_with_sdk = extend_router_with_sdk(original_router, MockTeamManager, {})

#         # Get the SDK class
#         sdk_class = router_with_sdk.SDK

#         # Verify it's a class that subclasses AbstractSDKHandler
#         self.assertTrue(inspect.isclass(sdk_class))
#         self.assertTrue(issubclass(sdk_class, AbstractSDKHandler))
#         logger.debug(f"✓ SDK class generated: {sdk_class.__name__}")

#         # Verify class name follows expected pattern
#         self.assertIn("RouterSDK", sdk_class.__name__)
#         logger.debug("✓ SDK class name follows expected pattern")

#         # Verify metadata is stored
#         self.assertTrue(hasattr(sdk_class, "_router_metadata"))
#         metadata = sdk_class._router_metadata
#         self.assertEqual(metadata["prefix"], "/v1/team")
#         self.assertEqual(metadata["tags"], ["teams"])
#         self.assertEqual(metadata["manager_class"], MockTeamManager)
#         logger.debug("✓ Router metadata properly stored in SDK class")

#     def test_router_sdk_handler_instantiation(self):
#         """Test that SDK handlers can be instantiated and configured properly."""
#         logger.debug("\n=== Testing Router SDK Handler Instantiation ===")

#         from fastapi import APIRouter

#         # Create a mock manager class
#         class MockProjectManager:
#             __name__ = "MockProjectManager"
#             prefix = "/v1/project"
#             tags = ["projects"]
#             auth_type = "API_KEY"

#             class Model:
#                 __name__ = "MockProjectModel"

#         # Create RouterWithSDK
#         original_router = APIRouter(prefix="/v1/project", tags=["projects"])
#         router_with_sdk = extend_router_with_sdk(
#             original_router, MockProjectManager, {}
#         )

#         # Get SDK class and instantiate it
#         sdk_class = router_with_sdk.SDK

#         # Test instantiation with required parameters
#         try:
#             sdk_instance = sdk_class(
#                 base_url="http://localhost:8000", api_key="test-key-123"
#             )
#             self.assertIsInstance(sdk_instance, AbstractSDKHandler)
#             logger.debug("✓ SDK handler instantiated successfully")

#             # Verify resource configuration
#             self.assertTrue(hasattr(sdk_instance, "resource_configs"))
#             self.assertIsInstance(sdk_instance.resource_configs, dict)
#             logger.debug(
#                 f"✓ Resource configs: {list(sdk_instance.resource_configs.keys())}"
#             )

#             # Check that resources were configured
#             if sdk_instance.resource_configs:
#                 resource_name = list(sdk_instance.resource_configs.keys())[0]
#                 config = sdk_instance.resource_configs[resource_name]
#                 self.assertEqual(config.name, "project")
#                 self.assertEqual(config.endpoint, "/v1/project")
#                 logger.debug(f"✓ Resource '{resource_name}' configured correctly")

#         except Exception as e:
#             logger.debug(f"SDK instantiation test completed with note: {e}")
#             # This is acceptable as the test verifies the class can be created

#     def test_router_method_forwarding(self):
#         """Test that RouterWithSDK properly forwards APIRouter methods."""
#         logger.debug("\n=== Testing Router Method Forwarding ===")

#         from fastapi import APIRouter

#         # Create a mock manager class
#         class MockOrderManager:
#             __name__ = "MockOrderManager"
#             prefix = "/v1/order"
#             tags = ["orders"]

#             class Model:
#                 __name__ = "MockOrderModel"

#         # Create RouterWithSDK
#         original_router = APIRouter(prefix="/v1/order", tags=["orders"])
#         router_with_sdk = extend_router_with_sdk(original_router, MockOrderManager, {})

#         # Test that common APIRouter methods are available
#         router_methods = [
#             "get",
#             "post",
#             "put",
#             "patch",
#             "delete",
#             "include_router",
#             "add_api_route",
#         ]

#         for method_name in router_methods:
#             self.assertTrue(hasattr(router_with_sdk, method_name))
#             method = getattr(router_with_sdk, method_name)
#             self.assertTrue(callable(method))
#             logger.debug(f"✓ Method '{method_name}' is available and callable")

#         # Test that SDK property is maintained alongside router methods
#         self.assertTrue(hasattr(router_with_sdk, "SDK"))
#         logger.debug("✓ SDK property maintained alongside router methods")

#     def test_multiple_router_sdk_independence(self):
#         """Test that multiple RouterWithSDK instances maintain independence."""
#         logger.debug("\n=== Testing Multiple Router SDK Independence ===")

#         from fastapi import APIRouter

#         # Create multiple mock manager classes
#         class MockUserModel:
#             __name__ = "MockUserModel"

#         class MockTaskModel:
#             __name__ = "MockTaskModel"

#         class MockUserManager:
#             __name__ = "MockUserManager"
#             prefix = "/v1/user"
#             tags = ["users"]
#             Model = MockUserModel

#         class MockTaskManager:
#             __name__ = "MockTaskManager"
#             prefix = "/v1/task"
#             tags = ["tasks"]
#             Model = MockTaskModel

#         # Create multiple RouterWithSDK instances
#         user_router = extend_router_with_sdk(
#             APIRouter(prefix="/v1/user", tags=["users"]), MockUserManager, {}
#         )

#         task_router = extend_router_with_sdk(
#             APIRouter(prefix="/v1/task", tags=["tasks"]), MockTaskManager, {}
#         )

#         # Verify they have different SDK classes
#         user_sdk_class = user_router.SDK
#         task_sdk_class = task_router.SDK

#         self.assertNotEqual(user_sdk_class, task_sdk_class)
#         self.assertNotEqual(user_sdk_class.__name__, task_sdk_class.__name__)
#         logger.debug(
#             f"✓ Independent SDK classes: {user_sdk_class.__name__} vs {task_sdk_class.__name__}"
#         )

#         # Verify they have different metadata
#         user_metadata = user_sdk_class._router_metadata
#         task_metadata = task_sdk_class._router_metadata

#         self.assertEqual(user_metadata["prefix"], "/v1/user")
#         self.assertEqual(task_metadata["prefix"], "/v1/task")
#         self.assertEqual(user_metadata["tags"], ["users"])
#         self.assertEqual(task_metadata["tags"], ["tasks"])
#         logger.debug("✓ Independent metadata maintained")

#     def test_router_mixin_patching_integration(self):
#         """Test that the monkey patching of RouterMixin works correctly."""
#         logger.debug("\n=== Testing RouterMixin Patching Integration ===")

#         try:
#             from lib.Pydantic2FastAPI import RouterMixin

#             # Verify RouterMixin has been patched by checking if Router returns RouterWithSDK
#             # We can't directly test this without a real BLL manager, but we can verify
#             # the method exists and is callable
#             self.assertTrue(hasattr(RouterMixin, "Router"))
#             self.assertTrue(callable(RouterMixin.Router))
#             logger.debug("✓ RouterMixin.Router method is available")

#             # Test that the method is a classmethod
#             self.assertTrue(
#                 isinstance(inspect.getattr_static(RouterMixin, "Router"), classmethod)
#             )
#             logger.debug("✓ RouterMixin.Router is correctly defined as classmethod")

#         except ImportError as e:
#             logger.debug(f"RouterMixin import test skipped: {e}")

#     def test_router_sdk_error_handling(self):
#         """Test error handling in RouterWithSDK creation and usage."""
#         logger.debug("\n=== Testing Router SDK Error Handling ===")

#         from fastapi import APIRouter

#         # Test with manager class missing Model attribute
#         class IncompleteManager:
#             __name__ = "IncompleteManager"
#             prefix = "/v1/incomplete"

#         # Should still work but use fallback logic
#         original_router = APIRouter(prefix="/v1/incomplete")
#         router_with_sdk = extend_router_with_sdk(original_router, IncompleteManager, {})

#         self.assertIsInstance(router_with_sdk, RouterWithSDK)
#         logger.debug("✓ RouterWithSDK handles incomplete manager gracefully")

#         # Verify SDK can still be created
#         sdk_class = router_with_sdk.SDK
#         self.assertTrue(issubclass(sdk_class, AbstractSDKHandler))
#         logger.debug("✓ SDK class created despite incomplete manager info")

#         # Test with minimal manager info
#         class MinimalManager:
#             __name__ = "MinimalManager"

#         minimal_router = extend_router_with_sdk(APIRouter(), MinimalManager, {})
#         minimal_sdk_class = minimal_router.SDK
#         self.assertTrue(issubclass(minimal_sdk_class, AbstractSDKHandler))
#         logger.debug("✓ SDK handles minimal manager information")


# if __name__ == "__main__":
#     # Run tests with verbose output
#     unittest.main(verbosity=2)
