import uuid
from typing import Any, Dict, Optional
from unittest import mock

import pytest
from faker import Faker

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity, SkipThisTest
from zephyrex.endpoints.AbstractEPTest import AbstractEPTest
from zephyrex.endpoints.EP_Auth_test import TestTeamEndpoints as CoreTeamEndpointTests
from zephyrex.endpoints.EP_Auth_test import (
    TestUserAndSessionEndpoints as CoreUserAndSessionEndpointTests,
)
from zephyrex.endpoints.EP_Providers_test import (
    TestProviderEndpoints as ProviderEndpointTests,
)
from zephyrex.endpoints.EP_Providers_test import (
    TestProviderInstanceEndpoints as ProviderInstanceEndpointTests,
)
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    ActivityModel,
    AgentContextPromptModel,
    AgentModel,
    InvocationInstanceModel,
    InvocationTriggerModel,
    ProjectContextPromptModel,
    ProjectContextProviderModel,
    ProjectModel,
    ProviderInstanceAgentAbilityModel,
    ProviderInstanceAgentModel,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents
from zephyrex.extensions.ai_prompts.EP_AI_Prompts_test import (
    TestPromptEndpoints as EXT_PromptEndpointTests,
)
from zephyrex.extensions.conversations.EP_Conversations_test import (
    TestMessageEndpoints as EXT_MessageEndpointTests,
)

# Initialize faker
faker = Faker()


@pytest.mark.ep
@pytest.mark.agents
class TestAgentEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Agent Management endpoints."""

    extension_class = EXT_AI_Agents

    base_endpoint = "agent"
    entity_name = "agent"
    # Model under test -- lets the shared `pytest_generate_tests` hook resolve
    # the field/search parametrization (the extension's model isn't under
    # zephyrex.logic.BLL_*, so entity_name inference alone can't find it).
    class_under_test = AgentModel
    required_fields = ["id", "name", "created_at"]
    string_field_to_update = "name"
    supports_search = True
    searchable_fields = ["name", "favourite"]

    # No parent entities for agents
    parent_entities = []

    create_fields = {
        "name": lambda: f"Test Agent {faker.word()}",
        "favourite": lambda: faker.boolean(),
    }
    update_fields = {
        "name": "Updated Agent",
        "favourite": True,
    }
    unique_fields = ["name"]

    def nest_payload_in_entity(self, entity: dict) -> dict:
        """Wrap the payload in an entity wrapper using self.entity_name."""
        return {self.entity_name: entity}

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        """Create a payload for agent creation."""
        if not name:
            name = faker.name()

        if invalid_data:
            # Create invalid data for testing validation
            payload = {
                "name": 12345,  # Invalid: number instead of string
                "favourite": "not_a_boolean",  # Invalid: string instead of boolean
                "rotation_id": 789,  # Invalid: number instead of UUID string
                "image_url": 456,  # Invalid: number instead of URL string
            }
        elif minimal:
            # Only include required fields
            payload = {"name": name, "favourite": False}
        else:
            # Include all fields
            payload = {
                "name": name,
                "favourite": False,
                "rotation_id": str(uuid.uuid4()),
                "image_url": f"https://example.com/images/{name.lower().replace(' ', '_')}.png",
            }

        # Add team_id if provided
        if team_id:
            payload["team_id"] = team_id

        # Add parent IDs if provided
        if parent_ids:
            payload.update(parent_ids)

        return payload

    @pytest.mark.xfail
    @mock.patch("extensions.ai_agents.BLL_AI_Agents.AgentManager.prompt")
    def test_POST_200_prompt(self, mock_prompt, server, admin_a, team_a):
        """Test sending a prompt to an agent."""
        # Mock the prompt response
        mock_prompt.return_value = "I'm an AI assistant, how can I help you today?"

        # First create an agent
        agent = self.test_POST_201(server, admin_a.id, team_a)

        # Create a prompt payload
        prompt_payload = {
            "messages": [{"role": "user", "content": "Hello, how are you?"}],
            "model": "gpt-4",
            "max_tokens": 100,
        }

        # Send the prompt
        response = server.post(
            f"/v1/{self.base_endpoint}/{agent['id']}/prompt",
            json=prompt_payload,
            headers=self._auth_header(admin_a.jwt),
        )

        self._assert_response_status(
            response,
            200,
            "POST prompt",
            f"/v1/{self.base_endpoint}/{agent['id']}/prompt",
            prompt_payload,
        )

        json_response = response.json()
        assert "response" in json_response, (
            f"[{self.entity_name}] Prompt response missing 'response' field\n"
            f"Response: {json_response}"
        )

        # Verify the mocked method was called with expected parameters
        mock_prompt.assert_called_once()
        mock_prompt.assert_called_with(id=agent["id"], prompt_content=prompt_payload)

        return json_response

    # @mock.patch("extensions.ai_agents.BLL_AI_Agents.AgentManager.transcribe")
    # def test_POST_200_transcribe(self, mock_transcribe, server, admin_a, team_a):
    #     """Test transcribing audio using an agent."""
    #     # Mock the transcription response
    #     mock_transcribe.return_value = "This is a test transcription of audio content."

    #     # First create an agent
    #     agent = self.test_POST_201(server, admin_a.id, team_a)

    #     # Transcription request payload
    #     transcribe_payload = {"audio_path": "/path/to/test_audio.wav"}  # Simulated path

    #     # Send the transcribe request
    #     response = server.post(
    #         f"/v1/{self.base_endpoint}/{agent['id']}/transcribe",
    #         json=transcribe_payload,
    #         headers=self._auth_header(admin_a.jwt),
    #     )

    #     self._assert_response_status(
    #         response,
    #         200,
    #         "POST transcribe",
    #         f"/v1/{self.base_endpoint}/{agent['id']}/transcribe",
    #         transcribe_payload,
    #     )

    #     json_response = response.json()
    #     assert "transcription" in json_response, (
    #         f"[{self.entity_name}] Transcribe response missing 'transcription' field\n"
    #         f"Response: {json_response}"
    #     )

    #     # Verify the mocked method was called with expected parameters
    #     mock_transcribe.assert_called_once()
    #     mock_transcribe.assert_called_with(
    #         id=agent["id"], audio_path=transcribe_payload["audio_path"]
    #     )

    #     return json_response

    @pytest.mark.xfail
    @mock.patch("extensions.ai_agents.BLL_AI_Agents.AgentManager.list_abilities")
    def test_GET_200_abilities(self, mock_list_abilities, server, admin_a, team_a):
        """Test listing enabled abilities for an agent."""
        # Mock the abilities list
        mock_abilities = [
            {"id": str(uuid.uuid4()), "name": "Chat", "enabled": True},
            {"id": str(uuid.uuid4()), "name": "Transcription", "enabled": True},
            {"id": str(uuid.uuid4()), "name": "Image Generation", "enabled": False},
        ]
        mock_list_abilities.return_value = mock_abilities

        # First create an agent
        agent = self.test_POST_201(server, admin_a.id, team_a)

        # Get abilities
        response = server.get(
            f"/v1/{self.base_endpoint}/{agent['id']}/ability",
            headers=self._auth_header(admin_a.jwt),
        )

        self._assert_response_status(
            response,
            200,
            "GET abilities",
            f"/v1/{self.base_endpoint}/{agent['id']}/ability",
        )

        json_response = response.json()
        assert "abilities" in json_response, (
            f"[{self.entity_name}] Abilities response missing 'abilities' field\n"
            f"Response: {json_response}"
        )

        abilities = json_response["abilities"]
        assert isinstance(abilities, list), (
            f"[{self.entity_name}] Abilities should be a list\n"
            f"Abilities: {abilities}"
        )

        # Verify the mocked method was called with expected parameters
        mock_list_abilities.assert_called_once()
        mock_list_abilities.assert_called_with(id=agent["id"])

        return json_response


@pytest.mark.ep
@pytest.mark.provider_instance_agent
class TestProviderInstanceAgentEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Provider Instance Agent endpoints."""

    extension_class = EXT_AI_Agents

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    # ProviderInstanceAgentManager declares no prefix -> flat default route
    # /v1/provider_instance_agent. Real fields: provider_instance_id, agent_id
    # (NOT name/description/enabled/state).
    base_endpoint = "provider_instance_agent"
    entity_name = "provider_instance_agent"
    class_under_test = ProviderInstanceAgentModel
    required_fields = ["id", "provider_instance_id", "agent_id"]
    string_field_to_update = None
    supports_search = True
    searchable_fields = ["provider_instance_id", "agent_id"]

    create_fields = {
        "provider_instance_id": None,  # from the provider_instance parent
        "agent_id": None,  # from the agent parent
    }
    update_fields = {}
    unique_fields = []

    # Parent NAMES match include relationships (agent, provider_instance);
    # ProviderInstanceEndpointTests creates its own provider dependency.
    parent_entities = [
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=ProviderInstanceEndpointTests,
        ),
        ParentEntity(
            name="agent",
            foreign_key="agent_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=TestAgentEndpoints,
        ),
    ]

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        """ProviderInstanceAgent payload (raw dict; framework wraps it)."""
        parent_ids = parent_ids or {}
        payload = {}
        for key in ("provider_instance_id", "agent_id"):
            if key in parent_ids:
                payload[key] = parent_ids[key]
        if invalid_data:
            payload["provider_instance_id"] = 12345  # wrong type
        return payload


@pytest.mark.ep
@pytest.mark.provider_instance_agent_ability
class TestProviderInstanceAgentAbilityEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Provider Instance Agent Ability endpoints."""

    extension_class = EXT_AI_Agents

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    # ProviderInstanceAgentAbilityManager declares no prefix -> flat default
    # route /v1/provider_instance_agent_ability. Real model fields are
    # provider_instance_id, agent_id, state (NOT name/description/enabled).
    base_endpoint = "provider_instance_agent_ability"
    entity_name = "provider_instance_agent_ability"
    required_fields = ["id", "provider_instance_id", "agent_id"]
    string_field_to_update = None
    supports_search = True
    searchable_fields = ["provider_instance_id", "agent_id", "state"]
    class_under_test = ProviderInstanceAgentAbilityModel

    create_fields = {
        "state": lambda: False,
        "provider_instance_id": None,  # from the provider-instance parent
        "agent_id": None,  # from the agent parent
    }
    update_fields = {"state": True}
    unique_fields = []

    # Parent NAMES must match the model's include relationships
    # (valid_includes: agent, provider_instance) -- ProviderInstanceEndpointTests
    # creates its own provider dependency.
    parent_entities = [
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=ProviderInstanceEndpointTests,
        ),
        ParentEntity(
            name="agent",
            foreign_key="agent_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=TestAgentEndpoints,
        ),
    ]

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        """ProviderInstanceAgentAbility payload (raw dict; framework wraps it)."""
        parent_ids = parent_ids or {}
        payload = {"state": False}
        for key in ("provider_instance_id", "agent_id"):
            if key in parent_ids:
                payload[key] = parent_ids[key]
        if invalid_data:
            payload["state"] = "not_a_boolean"  # wrong type
        return payload


@pytest.mark.ep
@pytest.mark.projects
class TestProjectEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Project Management endpoints."""

    extension_class = EXT_AI_Agents

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    base_endpoint = "project"
    entity_name = "project"
    string_field_to_update = "name"
    required_fields = ["id", "name", "created_at"]
    supports_search = True
    searchable_fields = ["name", "description"]
    # Model under test -- lets the shared `pytest_generate_tests` hook resolve
    # the field/search parametrization (the extension's model isn't under
    # zephyrex.logic.BLL_*, so entity_name inference alone can't find it).
    class_under_test = ProjectModel

    # Parent entities for projects. `user_id` is genuinely required on
    # ProjectModel (UserModel.Reference, backed by a NOT NULL DB column and
    # enforced by ProjectManager.create_validation) -- every project has an
    # owner. `team_id` is genuinely optional (TeamModel.Reference.Optional,
    # a nullable DB column) -- a project may or may not belong to a team,
    # mirroring how ConversationModel treats team_id. "team" is handled
    # specially by AbstractEPTest._create_parent_entities (auto-detects a
    # `team_a`/`team_b` fixture from the calling test, or creates a fresh
    # team via the conftest helper), matching the framework's built-in
    # convention.
    parent_entities = [
        ParentEntity(
            name="user",
            foreign_key="user_id",
            nullable=False,
            system=False,
            path_level=None,  # Not in path
            test_class=CoreUserAndSessionEndpointTests,
        ),
        ParentEntity(
            name="team",
            foreign_key="team_id",
            nullable=True,
            system=False,
            path_level=None,  # Not in path -- /v1/project is a flat prefix
            test_class=CoreTeamEndpointTests,
        ),
    ]

    # Test data generation
    create_fields = {
        "name": lambda: f"test_project_{faker.uuid4()}",
        "description": lambda: f"Test project description: {faker.sentence()}",
        "user_id": None,  # Will be populated from parent entities
    }
    update_fields = {
        "name": lambda: f"updated_project_{faker.uuid4()}",
        "description": "Updated project description",
    }
    unique_fields = ["name"]

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for project creation.

        Args:
            name: Optional name for the project
            parent_ids: Optional parent IDs
            team_id: Optional team ID
            minimal: Whether to create a minimal payload
            invalid_data: Whether to create invalid data

        Returns:
            Dict containing project creation payload
        """
        name = name or faker.company()

        if invalid_data:
            # Create invalid data for testing validation
            payload = {
                "name": 12345,  # Invalid: number instead of string
                "description": None,  # Invalid: null for optional field
            }
        elif minimal:
            # Only include required fields
            payload = {
                "name": name,
                "description": "Minimal test project",
            }
        else:
            # Include all fields
            payload = {
                "name": name,
                "description": f"Test project description for {name}",
            }

        if team_id:
            payload["team_id"] = team_id

        # Add parent IDs if provided -- applied after the plain `team_id`
        # kwarg so that an explicit `None` from test_POST_422_null_parents
        # (which nulls out every non-nullable parent's foreign key) actually
        # overrides the default, rather than being clobbered back to a real
        # team_id.
        if parent_ids:
            if "parent_id" in parent_ids:
                payload["parent_id"] = parent_ids["parent_id"]
            if "user_id" in parent_ids:
                payload["user_id"] = parent_ids["user_id"]
            if "team_id" in parent_ids:
                payload["team_id"] = parent_ids["team_id"]

        return payload


@pytest.mark.ep
@pytest.mark.project_context_provider
class TestProjectContextProviderEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Project Context Provider endpoints."""

    extension_class = EXT_AI_Agents

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    # ProjectContextProviderManager declares no prefix, so its route is the
    # framework default: /v1/{snakecase(ManagerName - "Manager")}. It is a
    # flat prefix (no path nesting) -- provider_id/project_id are body FKs.
    base_endpoint = "project_context_provider"
    entity_name = "project_context_provider"
    string_field_to_update = "context_resource"
    required_fields = ["id", "project_id", "provider_id"]
    supports_search = True
    searchable_fields = ["context_resource", "project_id", "provider_id"]
    class_under_test = ProjectContextProviderModel

    parent_entities = [
        ParentEntity(
            name="project",
            foreign_key="project_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=TestProjectEndpoints,
        ),
        ParentEntity(
            name="provider",
            foreign_key="provider_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=ProviderEndpointTests,
        ),
    ]

    create_fields = {
        "context_resource": lambda: f"document_store/test_{faker.uuid4()}",
        "project_id": None,  # populated from the project parent
        "provider_id": None,  # populated from the provider parent
    }
    update_fields = {
        "context_resource": lambda: f"updated_store/test_{faker.uuid4()}",
    }
    unique_fields = []

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        """Create a payload for project context provider creation.

        Returns the RAW entity dict; the framework wraps it under
        ``entity_name`` itself (mirrors TestProjectEndpoints).
        """
        parent_ids = parent_ids or {}
        if invalid_data:
            payload = {"context_resource": 12345}  # wrong type
        else:
            payload = {
                "context_resource": name or f"document_store/test_{uuid.uuid4()}",
            }
        for key in ("project_id", "provider_id"):
            if key in parent_ids:
                payload[key] = parent_ids[key]
        # ProjectContextProviderModel has no team_id field -- do not send one.
        return payload


@pytest.mark.ep
@pytest.mark.agent_context_prompt
class TestAgentContextPromptEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Agent Context Prompt endpoints (/v1/agent/{agent_id}/prompt)."""

    extension_class = EXT_AI_Agents

    # AgentContextPromptManager declares no prefix -> flat default route
    # /v1/agent_context_prompt. agent_id/prompt_id are body FKs.
    base_endpoint = "agent_context_prompt"
    entity_name = "agent_context_prompt"
    required_fields = ["id", "agent_id", "prompt_id"]
    supports_search = True
    searchable_fields = ["agent_id", "prompt_id"]
    string_field_to_update = None  # Context links are usually not updatable
    class_under_test = AgentContextPromptModel

    create_fields = {
        "agent_id": None,  # populated from the agent parent
        "prompt_id": None,  # populated from the prompt parent
    }
    update_fields = {}
    unique_fields = []

    parent_entities = [
        ParentEntity(
            name="agent",
            foreign_key="agent_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=TestAgentEndpoints,
        ),
        ParentEntity(
            name="prompt",
            foreign_key="prompt_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=EXT_PromptEndpointTests,
        ),
    ]

    skip_tests = [
        SkipThisTest(name="test_PUT_200", details="Context prompts cannot be updated"),
        SkipThisTest(name="test_PUT_200_batch", details="Batch updates not supported"),
    ]

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        """Agent-Prompt association payload (raw dict; framework wraps it)."""
        parent_ids = parent_ids or {}
        payload = {}
        for key in ("agent_id", "prompt_id"):
            if key in parent_ids:
                payload[key] = parent_ids[key]
        if invalid_data:
            payload["prompt_id"] = 12345  # wrong type
        return payload


@pytest.mark.ep
@pytest.mark.project_context_prompt
class TestProjectContextPromptEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Project Context Prompt endpoints (/v1/project/{project_id}/prompt)."""

    extension_class = EXT_AI_Agents

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    # ProjectContextPromptManager declares no prefix -> flat default route
    # /v1/project_context_prompt. project_id/prompt_id are body FKs.
    base_endpoint = "project_context_prompt"
    entity_name = "project_context_prompt"
    required_fields = ["id", "project_id", "prompt_id"]
    supports_search = True
    searchable_fields = ["project_id", "prompt_id"]
    string_field_to_update = None  # Context links are usually not updatable
    class_under_test = ProjectContextPromptModel

    create_fields = {
        "project_id": None,  # populated from the project parent
        "prompt_id": None,  # populated from the prompt parent
    }
    update_fields = {}
    unique_fields = []

    parent_entities = [
        ParentEntity(
            name="project",
            foreign_key="project_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=TestProjectEndpoints,
        ),
        ParentEntity(
            name="prompt",
            foreign_key="prompt_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=EXT_PromptEndpointTests,
        ),
    ]

    skip_tests = [
        SkipThisTest(name="test_PUT_200", details="Context prompts cannot be updated"),
        SkipThisTest(name="test_PUT_200_batch", details="Batch updates not supported"),
    ]

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        """Project-Prompt association payload (raw dict; framework wraps it)."""
        parent_ids = parent_ids or {}
        payload = {}
        for key in ("project_id", "prompt_id"):
            if key in parent_ids:
                payload[key] = parent_ids[key]
        if invalid_data:
            payload["prompt_id"] = 12345  # wrong type
        return payload


@pytest.mark.ep
@pytest.mark.ep
@pytest.mark.invocation_triggers
class TestInvocationTriggerEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Invocation Trigger (standing listener) endpoints."""

    extension_class = EXT_AI_Agents

    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    base_endpoint = "invocation-trigger"
    entity_name = "invocation_trigger"
    class_under_test = InvocationTriggerModel
    required_fields = ["id", "invocation_type", "created_at"]
    string_field_to_update = None
    supports_search = True
    searchable_fields = ["invocation_type", "enabled", "event_source"]

    create_fields = {
        "invocation_type": lambda: "timer",
        "interval_seconds": lambda: 300,
        "agent_id": None,  # from the agent parent
    }
    update_fields = {"enabled": False, "invocation_payload": "updated"}
    unique_fields = []

    parent_entities = [
        ParentEntity(
            name="agent",
            foreign_key="agent_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=TestAgentEndpoints,
        ),
    ]

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        """Invocation-trigger payload (raw dict; framework wraps it)."""
        parent_ids = parent_ids or {}
        payload: Dict[str, Any] = {
            "invocation_type": "timer",
            "interval_seconds": 300,
        }
        if "agent_id" in parent_ids:
            payload["agent_id"] = parent_ids["agent_id"]
        if invalid_data:
            payload["invocation_type"] = 12345  # wrong type
        elif minimal:
            payload.pop("interval_seconds", None)
        return payload


@pytest.mark.ep
@pytest.mark.invocation_instances
class TestInvocationInstanceEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Invocation Instance (single firing / turn) endpoints."""

    extension_class = EXT_AI_Agents

    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    base_endpoint = "invocation-instance"
    entity_name = "invocation_instance"
    class_under_test = InvocationInstanceModel
    required_fields = ["id", "agent_id", "status", "created_at"]
    string_field_to_update = None
    supports_search = True
    searchable_fields = ["status", "trigger_message_id", "agent_id"]

    create_fields = {
        "status": lambda: "pending",
        "agent_id": None,  # from the agent parent
    }
    update_fields = {"status": "running"}
    unique_fields = []

    parent_entities = [
        ParentEntity(
            name="agent",
            foreign_key="agent_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=TestAgentEndpoints,
        ),
    ]

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        """Invocation-instance payload (raw dict; framework wraps it)."""
        parent_ids = parent_ids or {}
        payload: Dict[str, Any] = {"status": "pending"}
        if "agent_id" in parent_ids:
            payload["agent_id"] = parent_ids["agent_id"]
        if invalid_data:
            payload["agent_id"] = 12345  # wrong type
        return payload


@pytest.mark.activities
class TestActivityEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Activity Management endpoints (now in ai_agents)."""

    extension_class = EXT_AI_Agents

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    base_endpoint = "activity"
    entity_name = "activity"
    class_under_test = ActivityModel
    string_field_to_update = "title"
    required_fields = [
        "id",
        "title",
        "body",
        "invocation_instance_id",
        "ability_id",
        "created_at",
    ]
    supports_search = True
    searchable_fields = [
        "title",
        "body",
        "invocation_instance_id",
        "parent_id",
        "state",
    ]

    # Test data generation
    create_fields = {
        "title": lambda: f"test_activity_{faker.sentence(nb_words=3)}",
        "body": lambda: f"test_activity_body_{faker.text()}",
        "parent_id": None,
        "artifact_id": None,
        "provider_id": None,
        "ability_id": None,  # Will be populated from parent entities
        "invocation_instance_id": None,  # Will be populated from parent entities
        "state": None,  # Initial state is null
    }
    update_fields = {
        "title": lambda: f"updated_activity_{faker.sentence(nb_words=3)}",
        "body": lambda: f"updated_activity_body_{faker.text()}",
        "state": 1,  # ActivityState.WARNING (IntEnum: 0/1/2)
    }
    unique_fields = []

    from zephyrex.endpoints.EP_Extensions_test import TestAbilityEndpoints

    parent_entities = [
        ParentEntity(
            name="invocation_instance",
            foreign_key="invocation_instance_id",
            nullable=False,
            system=False,
            path_level=None,
            test_class=TestInvocationInstanceEndpoints,
        ),
        ParentEntity(
            name="ability",
            foreign_key="ability_id",
            nullable=False,
            system=True,
            is_path=False,
            test_class=TestAbilityEndpoints,
        ),
    ]

    NESTING_CONFIG_OVERRIDES = {
        "LIST": 0,  # Standalone: /v1/activity
        "CREATE": 0,  # Standalone: /v1/activity
        "DETAIL": 0,  # Standalone: /v1/activity/{id}
        "SEARCH": 0,  # Standalone: /v1/activity/search
    }

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for activity creation.

        Args:
            name: Optional title for the activity
            parent_ids: Optional parent IDs (invocation_instance_id and
                ability_id required)
            team_id: Optional team ID
            minimal: Whether to create a minimal payload
            invalid_data: Whether to create invalid data

        Returns:
            Dict containing activity creation payload
        """
        parent_ids = parent_ids or {}
        title = name or f"Test Activity {faker.sentence(nb_words=3)}"

        if invalid_data:
            # Create invalid data for testing validation
            payload = {
                "title": 12345,  # Invalid: number instead of string
                "body": None,  # Invalid: null for required field
                "state": "invalid_state",  # Invalid: invalid enum value
                "ability_id": 123,  # Invalid: number instead of UUID string
                "invocation_instance_id": 456,  # Invalid: number instead of UUID string
            }
        elif minimal:
            # Only include required fields
            payload = {
                "title": title,
                "body": f"Minimal activity body for {title}",
                "ability_id": parent_ids.get("ability_id", str(uuid.uuid4())),
                "invocation_instance_id": parent_ids.get("invocation_instance_id"),
            }
        else:
            # Include all fields
            payload = {
                "title": title,
                "body": f"Test activity body for {title}",
                "parent_id": None,
                "artifact_id": None,
                "provider_id": None,
                "ability_id": parent_ids.get("ability_id", str(uuid.uuid4())),
                "invocation_instance_id": parent_ids.get("invocation_instance_id"),
                "state": None,  # Initial state is null
            }

        return payload

    def test_POST_201_child(self, server, admin_a, team_a, parent=None, api_key=None):
        """Test creating a child activity (nested under parent activity)."""
        if not parent:
            parent_activity = self._create(server, admin_a.jwt, admin_a.id, team_a.id)
        else:
            parent_activity = parent

        child_title = f"Test Child Activity {uuid.uuid4()}"
        payload = {
            "title": child_title,
            "body": f"Test child body {uuid.uuid4()}",
            "parent_id": parent_activity["id"],
            "invocation_instance_id": parent_activity["invocation_instance_id"],
            "ability_id": parent_activity["ability_id"],
        }

        headers = self._get_appropriate_headers(admin_a.jwt, api_key)
        create_url = self.get_create_endpoint({})

        response = server.post(
            create_url, json={self.entity_name: payload}, headers=headers
        )
        self._assert_response_status(
            response, 201, "POST child activity", create_url, payload
        )
        child_activity = self._assert_entity_in_response(response, "title", child_title)

        assert child_activity["parent_id"] == parent_activity["id"]
        assert (
            child_activity["invocation_instance_id"]
            == parent_activity["invocation_instance_id"]
        )
        return child_activity

    def test_GET_200_hierarchy(self, server, admin_a, team_a, api_key=None):
        """Test getting the activity hierarchy for a turn (invocation instance)."""
        parent_activity = self._create(server, admin_a.jwt, admin_a.id, team_a.id)
        child_activity = self.test_POST_201_child(
            server, admin_a, team_a, parent=parent_activity, api_key=api_key
        )

        instance_id = parent_activity["invocation_instance_id"]
        endpoint = f"/v1/{self.base_endpoint}/hierarchy/{instance_id}"
        response = server.get(
            endpoint, headers=self._get_appropriate_headers(admin_a.jwt)
        )
        self._assert_response_status(response, 200, "GET activity hierarchy", endpoint)

        json_response = response.json()
        assert "activities" in json_response
        activities = json_response["activities"]
        assert isinstance(activities, dict)
        assert parent_activity["id"] in activities

        parent_data = activities[parent_activity["id"]]
        assert (
            "activity" in parent_data
            and parent_data["activity"]["id"] == parent_activity["id"]
        )
        assert "children" in parent_data
        assert child_activity["id"] in parent_data["children"]
        return activities
