import uuid
from typing import Any, Dict, List, Optional

import pytest
from faker import Faker

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, SkipThisTest
from zephyrex.endpoints.AbstractEPTest import AbstractEPTest
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptModel
from zephyrex.extensions.ai_prompts.EXT_AI_Prompts import EXT_AI_Prompts

# Initialize faker
faker = Faker()


@pytest.mark.ep
@pytest.mark.prompts
class TestPromptEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Prompt Management endpoints."""

    # Extension configuration
    extension_class = EXT_AI_Prompts

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.ENDPOINT, CategoryOfTest.REST],
        timeout=60,
        cleanup=True,
    )

    # Base endpoint configuration
    base_endpoint = "prompt"
    entity_name = "prompt"
    # Model under test — lets the shared `pytest_generate_tests` hook resolve
    # the field/search parametrization (the extension's model isn't under
    # zephyrex.logic.BLL_*, so entity_name inference alone can't find it).
    class_under_test = PromptModel
    required_fields = ["id", "name", "content", "created_at"]
    string_field_to_update = "name"

    # Search configuration
    supports_search = True
    searchable_fields = ["name", "content", "team_id"]
    search_example_value = "Test Prompt"

    # No parent entities for prompts
    parent_entities = []

    # Not a system entity
    system_entity = False

    # Test data generation
    create_fields = {
        "name": lambda: f"test_prompt_{faker.uuid4()}",
        "content": lambda: f"This is a test prompt with {{VARIABLE}} placeholder.",
        "description": lambda: faker.sentence(),
    }
    update_fields = {
        "name": lambda: f"updated_prompt_{faker.uuid4()}",
        "content": "Updated prompt content with {{NEW_VARIABLE}}.",
        "description": "Updated prompt description.",
    }
    unique_fields = []  # Prompts don't have unique constraints

    # Tests to skip (if any)
    _skip_tests = [
        SkipThisTest(
            name="test_GQL_mutation_delete",
            details="GraphQL delete not implemented yet",
        ),
        SkipThisTest(
            name="test_GET_200_arguments",
            details="Prompt arguments endpoint not implemented yet",
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
        SkipThisTest(
            name="test_GQL_mutation_create",
            details="GraphQL mutation needs all required fields in test",
        ),
        SkipThisTest(
            name="test_GQL_mutation_update",
            details="GraphQL mutation needs all required fields in test",
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
        """Create a payload for prompt creation.

        Args:
            name: Optional name for the prompt
            parent_ids: Optional parent IDs (not used for prompts)
            team_id: Optional team ID
            minimal: Whether to create a minimal payload
            invalid_data: Whether to create invalid data

        Returns:
            Dict containing prompt creation payload
        """
        name = name or f"Test Prompt {faker.catch_phrase()}"

        payload: Dict[str, Any]
        if invalid_data:
            # Create invalid data for testing validation
            payload = {
                "name": 12345,  # Invalid: number instead of string
                "content": None,  # Invalid: null for required field
            }
        elif minimal:
            # Only include required fields
            payload = {
                "name": name,
                "content": f"This is a test prompt with {{VARIABLE}} placeholder.",
                "description": "Minimal test prompt",
            }
        else:
            # Include all fields
            payload = {
                "name": name,
                "content": f"This is a test prompt with {{VARIABLE}} placeholder for {name}.",
                "description": f"A test prompt description for {name}.",
            }

        return payload

    def test_GET_200_arguments(
        self, server: Any, admin_a: Any, team_a: Dict[str, Any]
    ) -> List[str]:
        """Test retrieving prompt arguments.

        Args:
            server: Test client instance
            admin_a: Admin user object with jwt property
            team_a: Team context

        Returns:
            List of prompt arguments
        """
        if self.reason_to_skip("test_GET_200_arguments"):
            pytest.skip("Skipping test_GET_200_arguments")

        # First create a prompt
        created_prompt = self.test_POST_201(server, admin_a, team_a)

        # Retrieve arguments
        endpoint = f"/v1/prompt/{created_prompt['id']}/arguments"
        response = server.get(
            endpoint,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        self._assert_response_status(response, 200, "GET arguments", endpoint)
        json_response = response.json()

        if "prompt_arguments" not in json_response:
            raise AssertionError(
                f"[{self.entity_name}] Arguments not found in response\n"
                f"Response: {json_response}"
            )

        if not isinstance(json_response["prompt_arguments"], list):
            raise AssertionError(
                f"[{self.entity_name}] Arguments should be a list\n"
                f"Arguments: {json_response['prompt_arguments']}"
            )

        # Verify VARIABLE argument was detected from our prompt content
        if "VARIABLE" not in json_response["prompt_arguments"]:
            raise AssertionError(
                f"[{self.entity_name}] Expected argument 'VARIABLE' not found\n"
                f"Arguments: {json_response['prompt_arguments']}"
            )

        return json_response["prompt_arguments"]

    def test_POST_201_batch(
        self, server: Any, admin_a: Any, team_a: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Test batch creation of prompts.

        Args:
            server: Test client instance
            admin_a: Admin user object with jwt property
            team_a: Team context

        Returns:
            List of created prompts
        """
        if self.reason_to_skip("test_POST_201_batch"):
            pytest.skip("Skipping test_POST_201_batch")

        # Create multiple prompts with batch API
        batch_size = 3
        batch = []

        for i in range(batch_size):
            name = f"Batch Prompt {i+1} {uuid.uuid4()}"
            batch.append(
                {
                    "name": name,
                    "content": f"Batch content for {{VARIABLE}} in prompt {i+1}",
                    "description": f"Description for batch prompt {i+1}",
                }
            )

        payload = {"prompts": batch}

        response = server.post(
            "/v1/prompt",
            json=payload,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        self._assert_response_status(
            response, 201, "POST batch create", "/v1/prompt", payload
        )
        json_response = response.json()

        if "prompts" not in json_response:
            raise AssertionError(
                f"[{self.entity_name}] Prompts not found in batch creation response\n"
                f"Response: {json_response}"
            )

        prompts = json_response["prompts"]
        if not isinstance(prompts, list):
            raise AssertionError(
                f"[{self.entity_name}] Batch response should be a list\n"
                f"Response: {prompts}"
            )

        if len(prompts) != batch_size:
            raise AssertionError(
                f"[{self.entity_name}] Expected {batch_size} prompts, got {len(prompts)}\n"
                f"Prompts: {prompts}"
            )

        # Verify each batch item was created properly
        for i, prompt in enumerate(prompts):
            if prompt["name"] != batch[i]["name"]:
                raise AssertionError(
                    f"[{self.entity_name}] Batch item name mismatch\n"
                    f"Expected: {batch[i]['name']}\n"
                    f"Got: {prompt['name']}"
                )
            if prompt["content"] != batch[i]["content"]:
                raise AssertionError(
                    f"[{self.entity_name}] Batch item content mismatch\n"
                    f"Expected: {batch[i]['content']}\n"
                    f"Got: {prompt['content']}"
                )

        return prompts
