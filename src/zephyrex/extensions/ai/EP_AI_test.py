"""Endpoint tests for the AI extension's AIRequest resource."""

from typing import Any, Dict, List, Optional

from faker import Faker

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, SkipThisTest
from zephyrex.endpoints.AbstractEPTest import AbstractEPTest
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai.BLL_AI import AiRequestModel
from zephyrex.extensions.ai.EXT_AI import EXT_AI

faker = Faker()


class TestAIRequestEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the AI request management endpoints."""

    # Extension configuration
    extension_class = EXT_AI

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.ENDPOINT, CategoryOfTest.REST],
        timeout=60,
        cleanup=True,
    )

    # Base endpoint configuration
    base_endpoint = "ai"
    entity_name = "ai_request"
    class_under_test = AiRequestModel
    required_fields = [
        "id",
        "name",
        "prompt",
        "provider_instance_id",
        "request_type",
        "status",
        "created_at",
    ]
    string_field_to_update = "name"

    # Search configuration
    supports_search = True
    searchable_fields = ["name", "prompt", "status", "request_type"]
    search_example_value = "text_generation"

    # No parent entities for AI requests
    parent_entities = []

    # Not a system entity
    system_entity = False

    # Test data generation
    create_fields = {
        "name": lambda: f"ai_request_{faker.uuid4()}",
        "prompt": faker.text,
        "provider_instance_id": lambda: faker.uuid4(),
        "request_type": lambda: faker.random_element(
            ["text_generation", "embedding", "image_generation"]
        ),
        "model_name": lambda: faker.random_element(
            ["gpt-4", "gpt-3.5-turbo", "dall-e-3"]
        ),
    }
    update_fields = {
        "name": lambda: f"updated_ai_request_{faker.uuid4()}",
        "response": "Updated AI response",
        "status": "completed",
    }
    unique_fields: List[str] = []

    # Tests to skip (framework-level gaps unrelated to AI request behavior,
    # mirroring the sibling ai_prompts conversion).
    _skip_tests = [
        SkipThisTest(
            name="test_GQL_mutation_delete",
            details="GraphQL delete not implemented yet",
        ),
        SkipThisTest(
            name="test_GQL_mutation_create",
            details="GraphQL mutation needs all required fields in test",
        ),
        SkipThisTest(
            name="test_GQL_mutation_update",
            details="GraphQL mutation needs all required fields in test",
        ),
        SkipThisTest(
            name="test_PUT_200",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_404_nonexistent",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_400",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_422",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
    ]

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for AI request creation.

        Args:
            name: Optional name for the AI request
            parent_ids: Optional parent IDs (not used for AI requests)
            team_id: Optional team ID
            minimal: Whether to create a minimal payload
            invalid_data: Whether to create invalid data

        Returns:
            Dict containing AI request creation payload
        """
        name = name or f"Test AI Request {faker.catch_phrase()}"

        if invalid_data:
            # Create invalid data for testing validation
            payload = {
                "name": 12345,  # Invalid: number instead of string
                "prompt": None,  # Invalid: null for required field
            }
        elif minimal:
            # Only include required fields
            payload = {
                "name": name,
                "prompt": "What is artificial intelligence?",
                "provider_instance_id": faker.uuid4(),
                "request_type": "text_generation",
            }
        else:
            # Include all fields
            payload = {
                "name": name,
                "prompt": f"Test prompt for {name}",
                "provider_instance_id": faker.uuid4(),
                "request_type": "text_generation",
                "model_name": "gpt-4",
            }

        return payload
