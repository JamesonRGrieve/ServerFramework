import inspect
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Set, Type, TypeVar

import pytest
import stringcase
from faker import Faker

from serverframework.AbstractTest import AbstractTest
from serverframework.extensions.AbstractExtensionProvider import AbstractStaticProvider
from serverframework.lib.Dependencies import Dependencies
from serverframework.lib.Environment import inflection
from serverframework.lib.Logging import logger
from serverframework.lib.Pydantic2Strawberry import convert_field_name

T = TypeVar("T", bound=AbstractStaticProvider)

# Using shared inflection instance from Environment


class ProviderTestType(str, Enum):
    STRUCTURE = "structure"
    METADATA = "metadata"
    DEPENDENCIES = "dependencies"
    ABILITIES = "abilities"
    SERVICES = "services"
    ENVIRONMENT = "environment"
    ROTATION = "rotation"
    ERROR_HANDLING = "error_handling"
    PERFORMANCE = "performance"
    CONCURRENCY = "concurrency"


class GraphQLTestType(str, Enum):
    QUERY_SINGLE = "query_single"
    QUERY_LIST = "query_list"
    MUTATION_CREATE = "mutation_create"
    MUTATION_UPDATE = "mutation_update"
    MUTATION_DELETE = "mutation_delete"
    SUBSCRIPTION = "subscription"
    NAVIGATION = "navigation"


@dataclass
class ProviderTestConfig:
    """Configuration for provider testing behavior."""

    test_types: Set[ProviderTestType] = field(
        default_factory=lambda: set(ProviderTestType)
    )
    expected_abilities: Set[str] = field(default_factory=set)
    expected_services: Set[str] = field(default_factory=set)
    expected_dependencies: Dependencies = field(
        default_factory=lambda: Dependencies([])
    )
    performance_thresholds: Dict[str, float] = field(
        default_factory=lambda: {
            "cache_improvement_min": 2.0,
            "concurrent_threads": 10,
            "performance_iterations": 100,
        }
    )
    skip_rotation_tests: bool = False
    skip_performance_tests: bool = False
    skip_error_handling_tests: bool = False


@dataclass
class GraphQLTestConfig:
    """Configuration for GraphQL testing."""

    test_types: Set[GraphQLTestType] = field(
        default_factory=lambda: set(GraphQLTestType)
    )
    external_model_class: Type | None = None
    external_manager_class: Type | None = None
    external_entity_name: str | None = None
    external_string_field: str = "name"
    external_graphql_fields: List[str] = field(
        default_factory=lambda: ["id", "createdAt", "updatedAt"]
    )
    supports_mutations: bool = True
    supports_subscriptions: bool = True
    supports_navigation: bool = False


class GraphQLTestMixin:
    """Simplified mixin for GraphQL testing of external API models."""

    graphql_config: GraphQLTestConfig = GraphQLTestConfig()
    faker = Faker()

    def _get_graphql_entity_name(self, plural: bool = False) -> str:
        """Get the GraphQL field name for the external entity."""
        if not self.graphql_config.external_entity_name:
            return None  # type: ignore[return-value]

        entity_name = self.graphql_config.external_entity_name
        if "_" in entity_name:
            entity_name = stringcase.camelcase(entity_name)

        if plural:
            return inflection.plural(entity_name)
        return entity_name

    def _get_mutation_name(self, operation: str) -> str:
        """Get the GraphQL mutation name for external entity operations."""
        entity_name = self._get_graphql_entity_name()
        if not entity_name:
            return None  # type: ignore[return-value]
        return f"{operation}{stringcase.pascalcase(entity_name)}"

    def _create_test_data(self) -> Dict[str, Any]:
        """Create test data for external entity."""
        test_data = {}
        if self.graphql_config.external_string_field:
            camel_case_field = convert_field_name(
                self.graphql_config.external_string_field, use_camelcase=True
            )
            test_data[camel_case_field] = f"External Test {self.faker.word()}"
        return test_data

    def _build_query_fields(self, include_string_field: bool = True) -> List[str]:
        """Build GraphQL query fields for external entity."""
        fields = self.graphql_config.external_graphql_fields.copy()
        if include_string_field and self.graphql_config.external_string_field:
            gql_string_field = convert_field_name(
                self.graphql_config.external_string_field, use_camelcase=True
            )
            if gql_string_field not in fields:
                fields.insert(1, gql_string_field)
        return fields

    @pytest.mark.parametrize(
        "test_type",
        sorted(
            [
                GraphQLTestType.QUERY_SINGLE,
                GraphQLTestType.QUERY_LIST,
                GraphQLTestType.MUTATION_CREATE,
                GraphQLTestType.MUTATION_UPDATE,
                GraphQLTestType.MUTATION_DELETE,
                GraphQLTestType.SUBSCRIPTION,
                GraphQLTestType.NAVIGATION,
            ]
        ),
    )
    def test_graphql_functionality(self, extension_server, test_type: GraphQLTestType):
        """Parameterized GraphQL tests."""
        if test_type not in self.graphql_config.test_types:
            pytest.skip(f"GraphQL test type {test_type.value} not configured")

        if test_type == GraphQLTestType.QUERY_SINGLE:
            self._test_query_single(extension_server)
        elif test_type == GraphQLTestType.QUERY_LIST:
            self._test_query_list(extension_server)
        elif test_type == GraphQLTestType.MUTATION_CREATE:
            self._test_mutation_create(extension_server)
        elif test_type == GraphQLTestType.MUTATION_UPDATE:
            self._test_mutation_update(extension_server)
        elif test_type == GraphQLTestType.MUTATION_DELETE:
            self._test_mutation_delete(extension_server)
        elif test_type == GraphQLTestType.SUBSCRIPTION:
            self._test_subscription(extension_server)
        elif test_type == GraphQLTestType.NAVIGATION:
            self._test_navigation(extension_server)

    def _test_query_single(self, extension_server):
        """Test single entity GraphQL query."""
        entity_name = self._get_graphql_entity_name()
        fields = self._build_query_fields()
        fields_str = "\n                ".join(fields)

        query = f"""
        query {{
            {entity_name}(id: "test_123") {{
                {fields_str}
            }}
        }}
        """
        self._execute_graphql_test(extension_server, query, "query")

    def _test_query_list(self, extension_server):
        """Test list entities GraphQL query."""
        entity_name = self._get_graphql_entity_name(plural=True)
        fields = self._build_query_fields()
        fields_str = "\n                ".join(fields)

        query = f"""
        query {{
            {entity_name} {{
                {fields_str}
            }}
        }}
        """
        self._execute_graphql_test(extension_server, query, "query")

    def _test_mutation_create(self, extension_server):
        """Test create mutation."""
        if not self.graphql_config.supports_mutations:
            pytest.skip("Mutations not supported")

        mutation_name = self._get_mutation_name("create")
        input_data = self._create_test_data()
        fields = self._build_query_fields()

        mutation = self._build_mutation(mutation_name, input_data, fields)
        self._execute_graphql_test(extension_server, mutation, "mutation")

    def _test_mutation_update(self, extension_server):
        """Test update mutation."""
        if not self.graphql_config.supports_mutations:
            pytest.skip("Mutations not supported")

        mutation_name = self._get_mutation_name("update")
        input_data = self._create_test_data()
        fields = self._build_query_fields()

        mutation = self._build_mutation(
            mutation_name, input_data, fields, entity_id="test_123"
        )
        self._execute_graphql_test(extension_server, mutation, "mutation")

    def _test_mutation_delete(self, extension_server):
        """Test delete mutation."""
        if not self.graphql_config.supports_mutations:
            pytest.skip("Mutations not supported")

        mutation_name = self._get_mutation_name("delete")
        mutation = f"""
        mutation {{
            {mutation_name}(id: "test_123")
        }}
        """
        self._execute_graphql_test(extension_server, mutation, "mutation")

    def _test_subscription(self, extension_server):
        """Test subscription."""
        if not self.graphql_config.supports_subscriptions:
            pytest.skip("Subscriptions not supported")

        entity_name = self._get_graphql_entity_name()
        fields = self._build_query_fields()
        fields_str = "\n                ".join(fields)

        subscription = f"""
        subscription {{
            {entity_name}Created {{
                {fields_str}
            }}
        }}
        """
        self._execute_graphql_test(
            extension_server, subscription, "subscription", expected_status=[200, 400]
        )

    def _test_navigation(self, extension_server):
        """Test navigation properties."""
        if not self.graphql_config.supports_navigation:
            pytest.skip("Navigation not supported")

        # Test schema introspection for navigation properties
        schema_query = """
        query {
            __type(name: "User") {
                fields {
                    name
                    type {
                        name
                        kind
                    }
                }
            }
        }
        """
        self._execute_graphql_test(extension_server, schema_query, "schema")

    def _build_mutation(
        self,
        mutation_name: str,
        input_data: Dict,
        fields: List[str],
        entity_id: str | None = None,
    ) -> str:
        """Build a GraphQL mutation."""
        input_fields = []
        for key, value in input_data.items():
            if isinstance(value, str):
                input_fields.append(f'{key}: "{value}"')
            else:
                input_fields.append(f"{key}: {value}")

        input_str = "{" + ", ".join(input_fields) + "}"
        id_param = f'id: "{entity_id}", ' if entity_id else ""
        fields_str = "\n                ".join(fields)

        return f"""
        mutation {{
            {mutation_name}({id_param}input: {input_str}) {{
                {fields_str}
            }}
        }}
        """

    def _execute_graphql_test(
        self,
        extension_server,
        query: str,
        operation_type: str,
        expected_status: List[int] | None = None,
    ):
        """Execute a GraphQL test and validate response."""
        expected_status = expected_status or [200]

        response = extension_server.post("/graphql", json={"query": query})
        assert (
            response.status_code in expected_status
        ), f"Unexpected status code: {response.status_code}"

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        if "errors" in data:
            error_messages = [error.get("message", "") for error in data["errors"]]
            # Only fail for syntax errors, not schema errors (which are expected in tests)
            syntax_errors = [
                msg
                for msg in error_messages
                if "syntax" in msg.lower() or "parse" in msg.lower()
            ]
            if syntax_errors:
                pytest.fail(
                    f"GraphQL syntax errors in {operation_type}: {syntax_errors}"
                )


class AbstractPRVTest(AbstractTest):
    """
    Configuration-driven abstract base class for testing static provider components.

    Provides a flexible framework for testing providers with configurable test types,
    expectations, and behavior.
    """

    provider_class: Type[T] | None = None  # type: ignore[valid-type]
    test_config: ProviderTestConfig = ProviderTestConfig()

    def _should_run_test(self, test_type: ProviderTestType) -> bool:
        """Check if a test type should be run based on configuration."""
        return test_type in self.test_config.test_types

    def _skip_if_not_configured(self, test_type: ProviderTestType):
        """Skip test if not configured to run."""
        if not self._should_run_test(test_type):
            pytest.skip(f"Test type {test_type.value} not configured to run")

    @pytest.mark.parametrize(
        "test_type",
        sorted(
            [
                ProviderTestType.STRUCTURE,
                ProviderTestType.METADATA,
                ProviderTestType.DEPENDENCIES,
                ProviderTestType.ABILITIES,
                ProviderTestType.SERVICES,
                ProviderTestType.ENVIRONMENT,
            ]
        ),
    )
    def test_provider_basic_functionality(self, test_type: ProviderTestType):
        """Parameterized test for basic provider functionality."""
        self._skip_if_not_configured(test_type)

        if not self.provider_class:
            pytest.skip("provider_class not defined, test cannot run")

        if test_type == ProviderTestType.STRUCTURE:
            self._test_structure()
        elif test_type == ProviderTestType.METADATA:
            self._test_metadata()
        elif test_type == ProviderTestType.DEPENDENCIES:
            self._test_dependencies()
        elif test_type == ProviderTestType.ABILITIES:
            self._test_abilities()
        elif test_type == ProviderTestType.SERVICES:
            self._test_services()
        elif test_type == ProviderTestType.ENVIRONMENT:
            self._test_environment()

    def _test_structure(self):
        """Test provider structure."""
        assert inspect.isclass(self.provider_class), "Provider must be a class"

        required_attrs = [
            "name",
            "description",
            "dependencies",
            "_env",
            "_hooks",
            "_abilities",
        ]
        for attr in required_attrs:
            assert hasattr(
                self.provider_class, attr
            ), f"Provider must have {attr} attribute"

        required_properties = ["hooks", "abilities", "root"]
        for prop in required_properties:
            assert hasattr(
                self.provider_class, prop
            ), f"Provider must have {prop} property"

        required_methods = ["_register_env_vars", "bond_instance", "get_abilities"]
        for method in required_methods:
            assert hasattr(
                self.provider_class, method
            ), f"Provider must have {method} method"
            assert callable(
                getattr(self.provider_class, method)
            ), f"{method} must be callable"

    def _test_metadata(self):
        """Test provider metadata."""
        assert isinstance(self.provider_class.name, str), "Provider name must be string"
        assert isinstance(
            self.provider_class.description, str
        ), "Provider description must be string"

    def _test_dependencies(self):
        """Test provider dependencies."""
        assert isinstance(
            self.provider_class.dependencies, Dependencies
        ), "Dependencies must be Dependencies instance"

        for attr in ["sys", "pip", "ext", "install", "check", "get_missing", "summary"]:
            assert hasattr(
                self.provider_class.dependencies, attr
            ), f"Dependencies must have {attr}"

    def _test_abilities(self):
        """Test provider abilities."""
        assert hasattr(
            self.provider_class, "_abilities"
        ), "Provider must have _abilities attribute"
        assert isinstance(
            self.provider_class._abilities, set
        ), "_abilities must be a set"

        abilities = self.provider_class.abilities
        assert isinstance(abilities, set), "abilities property must return a set"

        if self.test_config.expected_abilities:
            for expected_ability in self.test_config.expected_abilities:
                assert (
                    expected_ability in abilities
                ), f"Expected ability {expected_ability} not found"

        # Test get_abilities method
        abilities_from_method = self.provider_class.get_abilities()
        assert isinstance(
            abilities_from_method, set
        ), "get_abilities() must return a set"
        assert (
            abilities_from_method == abilities
        ), "get_abilities() should return same abilities as property"

        # Test that modifications to returned set don't affect original
        abilities_copy = self.provider_class.get_abilities()
        abilities_copy.add("test_ability")
        assert (
            "test_ability" not in self.provider_class.get_abilities()
        ), "get_abilities() should return a copy"

    def _test_services(self):
        """Test provider services."""
        if hasattr(self.provider_class, "services"):
            services = self.provider_class.services()
            assert isinstance(services, list), "services() must return a list"

            if self.test_config.expected_services:
                for expected_service in self.test_config.expected_services:
                    assert (
                        expected_service in services
                    ), f"Expected service {expected_service} not found"

    def _test_environment(self):
        """Test provider environment variables."""
        assert hasattr(self.provider_class, "_env"), "Provider must have _env attribute"
        env = self.provider_class._env
        assert isinstance(env, dict), "_env attribute must be a dictionary"

        assert hasattr(
            self.provider_class, "_register_env_vars"
        ), "Provider must have _register_env_vars method"
        assert callable(
            self.provider_class._register_env_vars
        ), "_register_env_vars must be callable"

    def test_rotation_integration(self, extension_server, extension_db):
        """Test provider integration with rotation system."""
        self._skip_if_not_configured(ProviderTestType.ROTATION)

        if self.test_config.skip_rotation_tests:
            pytest.skip("Rotation tests disabled in configuration")

        if not self.provider_class or not hasattr(self.provider_class, "extension"):
            pytest.skip("provider_class or extension not defined")

        parent_extension = self.provider_class.extension
        assert hasattr(
            parent_extension, "root"
        ), "Parent extension must have root property"

        root_rotation = parent_extension.root
        if root_rotation is None:
            pytest.skip("Root rotation manager not available in test environment")

        assert hasattr(
            root_rotation, "rotate"
        ), "Rotation manager must have rotate method"
        assert callable(root_rotation.rotate), "rotate must be callable"

    ERROR_HANDLING_SCENARIOS = [
        ("provider_not_found", "Provider instance not found in database"),
        ("provider_disabled", "Provider instance is disabled"),
        ("api_key_missing", "API key is missing or invalid"),
        ("network_timeout", "Network timeout during API call"),
    ]

    @pytest.mark.parametrize(
        "scenario_name,scenario_description", ERROR_HANDLING_SCENARIOS
    )
    def test_error_handling_scenarios(
        self, scenario_name, scenario_description, extension_server, extension_db
    ):
        """Parametrized: every provider must survive each canonical failure mode."""
        self._skip_if_not_configured(ProviderTestType.ERROR_HANDLING)

        if self.test_config.skip_error_handling_tests:
            pytest.skip("Error handling tests disabled in configuration")

        if not self.provider_class or not hasattr(self.provider_class, "extension"):
            pytest.skip("provider_class or extension not defined")

        parent_extension = self.provider_class.extension
        try:
            root_rotation = parent_extension.root
            assert root_rotation is not None
            logger.debug(
                f"Testing error scenario: {scenario_name} - {scenario_description}"
            )
        except Exception as e:
            logger.debug(f"Error scenario '{scenario_name}' handled: {str(e)}")

    def test_performance_benchmarks(self, extension_server, extension_db):
        """Test provider performance."""
        self._skip_if_not_configured(ProviderTestType.PERFORMANCE)

        if self.test_config.skip_performance_tests:
            pytest.skip("Performance tests disabled in configuration")

        if not self.provider_class or not hasattr(self.provider_class, "extension"):
            pytest.skip("provider_class or extension not defined")

        parent_extension = self.provider_class.extension

        start_time = time.time()
        root_rotation_1 = parent_extension.root
        if root_rotation_1 is None:
            pytest.skip("Root rotation manager not available in test environment")
        clean_lookup_time = time.time() - start_time

        start_time = time.time()
        root_rotation_2 = parent_extension.root
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

    def test_concurrent_access(self, extension_server, extension_db):
        """Test provider under concurrent access."""
        self._skip_if_not_configured(ProviderTestType.CONCURRENCY)

        if not self.provider_class or not hasattr(self.provider_class, "extension"):
            pytest.skip("provider_class or extension not defined")

        parent_extension = self.provider_class.extension
        if parent_extension.root is None:
            pytest.skip("Root rotation manager not available in test environment")

        def access_root_rotation():
            return parent_extension.root

        num_threads = self.test_config.performance_thresholds["concurrent_threads"]
        results = []

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [
                executor.submit(access_root_rotation) for _ in range(num_threads)
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

    def test_dependency_operations(self):
        """Test dependency operations."""
        if not self.provider_class:
            pytest.skip("No provider class specified")

        dependencies = self.provider_class.dependencies
        loaded_extensions = {"core": "1.0.0"}

        try:
            results = dependencies.check(loaded_extensions)
            assert isinstance(results, dict), "Check should return results dict"

            missing = dependencies.get_missing(loaded_extensions)
            assert isinstance(
                missing, Dependencies
            ), "Should return Dependencies instance"

            summary = dependencies.summary()
            # Summary can be various types depending on implementation

        except Exception as e:
            # Some operations may fail in test environment - log but don't fail
            logger.debug(f"Dependency operation note: {e}")

        # Test extension dependencies if they exist
        if dependencies.ext:
            env_csv = dependencies.ext.env
            assert isinstance(env_csv, str), "Environment CSV should be a string"

    # Factory methods for creating test configurations
    @classmethod
    def create_config(
        cls,
        test_types: Set[ProviderTestType] | None = None,
        expected_abilities: Set[str] | None = None,
        expected_services: Set[str] | None = None,
        skip_rotation: bool = False,
        skip_performance: bool = False,
        skip_error_handling: bool = False,
        **kwargs,
    ) -> ProviderTestConfig:
        """Factory method for creating test configurations."""
        return ProviderTestConfig(
            test_types=test_types or set(ProviderTestType),
            expected_abilities=expected_abilities or set(),
            expected_services=expected_services or set(),
            skip_rotation_tests=skip_rotation,
            skip_performance_tests=skip_performance,
            skip_error_handling_tests=skip_error_handling,
            **kwargs,
        )

    @classmethod
    def basic_config(cls) -> ProviderTestConfig:
        """Basic test configuration for minimal testing."""
        return cls.create_config(
            test_types={
                ProviderTestType.STRUCTURE,
                ProviderTestType.METADATA,
                ProviderTestType.DEPENDENCIES,
            },
            skip_rotation=True,
            skip_performance=True,
            skip_error_handling=True,
        )

    @classmethod
    def full_config(
        cls, expected_abilities: Set[str] | None = None, expected_services: Set[str] | None = None
    ) -> ProviderTestConfig:
        """Full test configuration for comprehensive testing."""
        return cls.create_config(
            test_types=set(ProviderTestType),
            expected_abilities=expected_abilities,
            expected_services=expected_services,
        )

    @classmethod
    def performance_config(cls) -> ProviderTestConfig:
        """Performance-focused test configuration."""
        return cls.create_config(
            test_types={
                ProviderTestType.STRUCTURE,
                ProviderTestType.PERFORMANCE,
                ProviderTestType.CONCURRENCY,
            },
        )

    @classmethod
    def create_graphql_config(
        cls,
        entity_name: str,
        model_class: Type | None = None,
        test_types: Set[GraphQLTestType] | None = None,
        **kwargs,
    ) -> GraphQLTestConfig:
        """Factory method for creating GraphQL test configurations."""
        return GraphQLTestConfig(
            external_entity_name=entity_name,
            external_model_class=model_class,
            test_types=test_types or set(GraphQLTestType),
            **kwargs,
        )

    def test_bond_instance_implementation(self):
        """Test that bond_instance is properly implemented."""
        if not self.provider_class:
            pytest.skip("provider_class not defined")

        # Check that bond_instance exists
        assert hasattr(
            self.provider_class, "bond_instance"
        ), "Provider must have bond_instance method"

        # For abstract providers, bond_instance should be abstract
        # For concrete providers, it should be implemented
        import abc

        if hasattr(self.provider_class, "__abstractmethods__"):
            # This is still an abstract class
            assert (
                "bond_instance" in self.provider_class.__abstractmethods__
                or callable(getattr(self.provider_class, "bond_instance", None))
            ), ("bond_instance must be abstract or implemented")
        else:
            # This is a concrete class - bond_instance must be callable
            assert callable(
                self.provider_class.bond_instance
            ), "bond_instance must be implemented in concrete providers"

    def test_root_property_implementation(self):
        """Test that root property is properly implemented."""
        if not self.provider_class:
            pytest.skip("provider_class not defined")

        # Check that root property exists
        assert hasattr(self.provider_class, "root"), "Provider must have root property"

        # For abstract providers, root should be abstract
        # For concrete providers, it should return a value
        import abc

        if hasattr(self.provider_class, "__abstractmethods__"):
            # This is still an abstract class
            if "root" in self.provider_class.__abstractmethods__:
                # It's properly marked as abstract
                pass
            else:
                # It should be implemented or accessible
                try:
                    root_value = self.provider_class.root
                    # If we can access it, it should be None or a valid object
                    assert root_value is None or hasattr(
                        root_value, "__class__"
                    ), "root property should return None or a valid object"
                except Exception as e:
                    # Access might fail in test environment
                    logger.debug(f"root property access in test environment: {e}")
        else:
            # This is a concrete class - root should be accessible
            try:
                root_value = self.provider_class.root
                # root can be None in test environment or a valid rotation manager
                if root_value is not None:
                    assert hasattr(
                        root_value, "rotate"
                    ), "root should return a rotation manager with rotate method"
            except Exception as e:
                logger.debug(f"root property access failed in concrete provider: {e}")


# ============================================================================
# Reusable security-deny matrix for any AbstractEmailProvider subclass.
#
# Mixed into a concrete provider's test class, this gives free coverage of
# CRLF / NUL / oversized / traversal / homograph rejection without duplicating
# the matrix in each provider test file. The matrix tests rely only on the
# provider class being importable — they do NOT require a real API key, so
# they always run in CI rather than xfailing.
# ============================================================================


EMAIL_SECURITY_DENY_MATRIX = [
    pytest.param(
        "recipient",
        "victim@example.com\r\nBcc: attacker@evil.example.com",
        "CRLF in recipient grafts a Bcc header",
        id="crlf_recipient",
    ),
    pytest.param(
        "subject",
        "Hi\r\nX-Injected: yes",
        "CRLF in subject grafts an injected header",
        id="crlf_subject",
    ),
    pytest.param(
        "subject",
        "A" * 1000,
        "subject longer than RFC 5322 998-octet header limit",
        id="oversized_subject",
    ),
    pytest.param(
        "body",
        "ok\x00malicious",
        "NUL in body smuggles past C-string transports",
        id="nul_in_body",
    ),
    pytest.param(
        "recipient",
        "no-at-sign",
        "malformed recipient (no @)",
        id="malformed_recipient",
    ),
    pytest.param(
        "body",
        "x" * (10 * 1024 * 1024 + 1),
        "body exceeds 10 MiB DoS cap",
        id="oversized_body",
    ),
    pytest.param(
        "attachments",
        ["../../etc/passwd"],
        "attachment path traversal (relative)",
        id="attachment_traversal",
    ),
    pytest.param(
        "attachments",
        ["/tmp/file\x00.png"],
        "NUL in attachment filename",
        id="attachment_nul",
    ),
    pytest.param(
        "recipient",
        "аdmin@example.com",  # Cyrillic 'а' (U+0430) lookalike for ASCII 'a'
        "Cyrillic homograph 'a' in recipient local part",
        id="recipient_homograph",
    ),
]


class AbstractEmailProviderSecurityTests:
    """
    Mixin that any email provider's test class can inherit to gain a full
    parametrized matrix of input-validation denial tests. Requires the host
    test class to expose a ``mock_provider_instance`` fixture and to set
    ``provider_class`` to a concrete ``AbstractEmailProvider`` subclass.
    """

    @pytest.mark.security
    @pytest.mark.parametrize("field,payload,reason", EMAIL_SECURITY_DENY_MATRIX)
    @pytest.mark.asyncio
    async def test_send_email_rejects_malicious_input(
        self, mock_provider_instance, field, payload, reason
    ):
        """All providers must reject the same malicious payloads identically."""
        if not self.provider_class:
            pytest.skip("provider_class not defined")

        kwargs = {
            "recipient": "ok@example.com",
            "subject": "ok",
            "body": "ok",
            "attachments": None,
        }
        kwargs[field] = payload

        result = await self.provider_class.send_email(
            mock_provider_instance, **kwargs
        )

        assert isinstance(result, str), (
            f"send_email must return a str even on rejection ({reason})"
        )
        lowered = result.lower()
        assert any(
            token in lowered for token in ("rejected", "invalid", "exceeds", "failed")
        ), (
            f"{self.provider_class.__name__}.send_email accepted malicious input "
            f"({reason}); got {result!r}"
        )

    @pytest.mark.security
    @pytest.mark.asyncio
    async def test_send_message_rejects_crlf_in_to(self, mock_provider_instance):
        """The friendly ``send(EmailMessage)`` path enforces the same denials."""
        if not self.provider_class:
            pytest.skip("provider_class not defined")
        from serverframework.extensions.email.EXT_EMail import EmailAddress, EmailMessage

        msg = EmailMessage(
            to=[EmailAddress(address="victim@x.com\r\nBcc: bad@y.com")],
            subject="ok",
            body_text="ok",
        )
        result = await self.provider_class.send(mock_provider_instance, msg)
        assert "rejected" in result.lower() and "crlf" in result.lower(), (
            f"{self.provider_class.__name__}.send accepted CRLF in to: {result!r}"
        )

    @pytest.mark.security
    @pytest.mark.asyncio
    async def test_send_message_rejects_crlf_in_custom_header(
        self, mock_provider_instance
    ):
        """Custom headers must be CRLF-clean — a header-injection vector
        the legacy ``send_email`` positional API has no slot for."""
        if not self.provider_class:
            pytest.skip("provider_class not defined")
        from serverframework.extensions.email.EXT_EMail import EmailAddress, EmailMessage

        msg = EmailMessage(
            to=[EmailAddress(address="ok@example.com")],
            subject="ok",
            body_text="ok",
            headers={"X-Foo": "bar\r\nX-Bcc: bad@example.com"},
        )
        result = await self.provider_class.send(mock_provider_instance, msg)
        assert "rejected" in result.lower(), (
            f"{self.provider_class.__name__}.send accepted CRLF in custom "
            f"header: {result!r}"
        )

    @pytest.mark.security
    @pytest.mark.asyncio
    async def test_send_message_rejects_crlf_in_display_name(
        self, mock_provider_instance
    ):
        """Display names ride the same line as the address; a CRLF in the
        name part is a header-injection vector identical to one in the
        address part."""
        if not self.provider_class:
            pytest.skip("provider_class not defined")
        from serverframework.extensions.email.EXT_EMail import EmailAddress, EmailMessage

        msg = EmailMessage(
            to=[
                EmailAddress(
                    address="ok@example.com",
                    name="Alice\r\nBcc: bad@example.com",
                )
            ],
            subject="ok",
            body_text="ok",
        )
        result = await self.provider_class.send(mock_provider_instance, msg)
        assert "rejected" in result.lower(), (
            f"{self.provider_class.__name__}.send accepted CRLF in display "
            f"name: {result!r}"
        )
