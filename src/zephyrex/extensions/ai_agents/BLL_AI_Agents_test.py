import os
import uuid

import pytest
from fastapi import HTTPException
from faker import Faker

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    ActivityManager,
    AgentContextPromptManager,
    AgentManager,
    ConversationAgentManager,
    InvocationInstanceManager,
    InvocationTriggerManager,
    ProjectContextPromptManager,
    ProjectContextProviderManager,
    ProjectManager,
    ProviderInstanceAgentAbilityManager,
    ProviderInstanceAgentManager,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents
from zephyrex.extensions.ai_prompts.BLL_AI_Prompts_test import (
    TestPromptManager as ExtPromptManagerTests,
)
from zephyrex.extensions.conversations.BLL_Conversations_test import (
    TestConversationManager as ExtConversationManagerTests,
)
from zephyrex.extensions.conversations.BLL_Conversations_test import (
    TestMessageManager as ExtMessageManagerTests,
)
from zephyrex.logic.AbstractBLLTest import AbstractBLLTest
from zephyrex.logic.BLL_Auth_test import TestTeamManager as CoreTeamManagerTests
from zephyrex.logic.BLL_Auth_test import TestUserManager as CoreUserManagerTests
from zephyrex.logic.BLL_Extensions_test import (
    TestAbilityManager as CoreAbilityManagerTests,
)
from zephyrex.logic.BLL_Providers_test import (
    TestProviderManager as CoreProviderManagerTests,
)
from zephyrex.logic.BLL_Providers_test import (
    TestRotationManager as CoreRotationManagerTests,
)

# Set default test configuration for all test classes
AbstractBLLTest.test_config = ClassOfTestsConfig(
    categories=[CategoryOfTest.LOGIC, CategoryOfTest.EXTENSION]
)

# Initialize faker for generating test data
faker = Faker()


def _extension_server_fixture(*extra_extensions: str):
    """Build a module-scoped ``server`` fixture that also loads ``extra_extensions``.

    ``ExtensionServerMixin.server`` (the default) only binds ``extension_class``
    plus the framework's core-companion extensions in the isolated model
    registry -- ``create_registry_with_db_manager`` discovers models solely
    from the requested CSV, it does not walk the ``EXT_Dependency`` graph
    that ``ExtensionRegistry.register_extension`` resolves for extension
    *loading*. Test classes that exercise a real dependency extension's
    manager (``conversations``, ``ai_prompts``) must request it explicitly so
    its models are bound -- mirrors the pattern in
    ``extensions/ai/BLL_AI_test.py::TestAiRequestManager.server``.
    """

    @pytest.fixture(scope="module")
    def server(self):
        if not self.extension_class:
            pytest.skip("extension_class not defined, test cannot run")

        from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

        prepare_test_registry()

        from fastapi.testclient import TestClient

        extension_name = self.extension_class.name.lower()
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
        test_db_prefix = (
            f"test.{extension_name}.{worker_id}"
            if worker_id
            else f"test.{extension_name}"
        )
        from conftest import CORE_COMPANION_EXTENSIONS

        wanted = (extension_name, *extra_extensions)
        names = list(wanted) + [c for c in CORE_COMPANION_EXTENSIONS if c not in wanted]
        extension_list = ",".join(names)
        try:
            from zephyrex.app import instance

            app = instance(db_prefix=test_db_prefix, extensions=extension_list)
            client = TestClient(app)
            yield client
        except ImportError as e:
            pytest.skip(f"FastAPI dependencies not available: {e}")
        except Exception as e:
            pytest.skip(f"Server setup failed: {e}")

    return server


def _get_or_create_root_ai_rotation(rotation_manager):
    """Fetch the "Root_Ai" rotation, creating it if this isolated test
    registry hasn't seeded it yet.

    ``"Root_Ai"`` is the framework's root-rotation naming convention for the
    ``ai`` extension (``RotationModel.seed_data`` PascalCases each
    underscore-delimited part of the extension name). Seeding only emits
    that row once provider discovery has found at least one provider for
    the extension, so a test registry that never loads the ``ai`` extension
    (this file only requests ``ai_agents`` + ``conversations``) legitimately
    has no such row. Mirrors the lazy-create idiom already used in
    ``extensions/ai/BLL_AI.py::transcribe_audio_to_text``.
    """
    try:
        return rotation_manager.get(name="Root_Ai")
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return rotation_manager.create(
            name="Root_Ai", description="Root rotation for the ai extension."
        )


class TestAgentManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = AgentManager
    extension_class = EXT_AI_Agents

    create_fields = {
        "name": lambda: f"Test Agent {faker.word()}",
        "favourite": lambda: faker.boolean(),
    }
    update_fields = {
        "name": "Updated Agent",
        "favourite": True,
    }
    unique_fields = ["name"]

    parent_entities = [
        ParentEntity(
            name="rotation",
            foreign_key="rotation_id",
            test_class=CoreRotationManagerTests,
        ),
        ParentEntity(
            name="user", foreign_key="user_id", test_class=CoreUserManagerTests
        ),
        ParentEntity(
            name="team", foreign_key="team_id", test_class=CoreTeamManagerTests
        ),
    ]

    def create_parent_entities(self, manager_class, field_name):
        """Override to handle optional parent dependencies."""
        if field_name in ["rotation_id", "user_id", "team_id"]:
            return None  # These are optional
        return super().create_parent_entities(manager_class, field_name)

    def test_prompt_method_with_rotation(self, admin_a, team_a, model_registry):
        """Test the prompt method of AgentManager with a rotation."""
        # Create a rotation first
        from zephyrex.logic.BLL_Providers import RotationManager

        with RotationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as rotation_manager:
            rotation = rotation_manager.create(name=f"Test Rotation {uuid.uuid4()}")

        # Create an agent with the rotation
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            agent = manager.create(
                name=f"Test Agent {uuid.uuid4()}", rotation_id=rotation.id
            )

            # Verify agent creation
            assert agent is not None
            assert agent.name.startswith("Test Agent")
            assert agent.rotation_id == rotation.id

        # Clean up
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as cleanup_manager:
            cleanup_manager.delete(id=agent.id)

        with RotationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as rotation_cleanup:
            rotation_cleanup.delete(id=rotation.id)

    def test_agent_without_rotation(self, admin_a, team_a, model_registry):
        """Test creating an agent without a rotation."""
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            agent = manager.create(
                name=f"Test Agent No Rotation {uuid.uuid4()}", favourite=True
            )

            assert agent is not None
            assert agent.rotation_id is None
            assert agent.favourite is True

            # Clean up
            manager.delete(id=agent.id)


class TestConversationAgentManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = ConversationAgentManager
    extension_class = EXT_AI_Agents
    server = _extension_server_fixture("conversations")

    create_fields = {
        "active": lambda: True,
        "auto_respond": lambda: faker.boolean(),
    }
    update_fields = {
        "active": False,
        "auto_respond": True,
    }
    unique_fields = []

    parent_entities = [
        ParentEntity(name="agent", foreign_key="agent_id", test_class=TestAgentManager),
        ParentEntity(
            name="conversation",
            foreign_key="conversation_id",
            test_class=ExtMessageManagerTests,  # Uses ConversationManager through MessageManager
        ),
    ]

    def test_auto_response_hook_flow(self, admin_a, team_a, model_registry):
        """Test the auto-response hook when a user sends a message."""
        # Import required dependencies
        from zephyrex.extensions.conversations.BLL_Conversations import (
            ConversationManager,
            MessageManager,
        )
        from zephyrex.logic.BLL_Providers import (  # ProviderInstanceManager,; ProviderManager,; RotationProviderInstanceManager,
            RotationManager,
        )

        with RotationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as rotation_manager:
            rotation = _get_or_create_root_ai_rotation(rotation_manager)

        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agent_manager:
            # Create an agent with the rotation
            agent = agent_manager.create(
                name=f"Test Agent {faker.word()}",
                rotation_id=rotation.id,
                team_id=team_a.id,
            )

        # 1. Create a conversation
        with ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as conv_manager:
            conversation = conv_manager.create(name=f"Test Conversation {faker.word()}")

        # # 2. Create a rotation with a provider instance
        # with ProviderManager(
        #     requester_id=admin_a.id, model_registry=model_registry
        # ) as provider_manager:
        #     provider = provider_manager.create(
        #         name=f"test_provider_{faker.word()}", friendly_name="Test Provider"
        #     )

        # with ProviderInstanceManager(
        #     requester_id=admin_a.id, model_registry=model_registry
        # ) as pi_manager:
        #     provider_instance = pi_manager.create(
        #         name=f"Test Provider Instance {faker.word()}",
        #         provider_id=provider.id,
        #         api_key=env("AGINYOURPC_API_KEY"),
        #         model_name="aginyourpc",
        #     )

        # with RotationManager(
        #     requester_id=admin_a.id, model_registry=model_registry
        # ) as rotation_manager:
        #     rotation = rotation_manager.create(name=f"Test Rotation {faker.word()}")

        # with RotationProviderInstanceManager(
        #     requester_id=admin_a.id, model_registry=model_registry
        # ) as rotation_pi_manager:
        #     rotation_pi_manager.create(
        #         rotation_id=rotation.id,
        #         provider_instance_id=provider_instance.id,
        #     )

        # # 3. Create an agent with the rotation
        # with AgentManager(
        #     requester_id=admin_a.id, model_registry=model_registry
        # ) as agent_manager:
        #     agent = agent_manager.create(
        #         name=f"Test Agent {faker.word()}", rotation_id=rotation.id
        #     )

        # # 4. Add the agent to the conversation with auto_respond enabled
        # with self.class_under_test(
        #     requester_id=admin_a.id, model_registry=model_registry
        # ) as conv_agent_manager:
        #     conversation_agent = conv_agent_manager.create(
        #         conversation_id=conversation.id,
        #         agent_id=agent.id,
        #         active=True,
        #         auto_respond=True,
        #     )

        # 5. Create a user message (this should trigger the hook)
        with MessageManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as msg_manager:
            user_message = msg_manager.create(
                conversation_id=conversation.id,
                content="Hello, how are you?",
                user_id=admin_a.id,
            )

            # 6. Check if agent response was created (the hook should have triggered)
            # Note: In a real test, we'd need to mock the AI provider or have a test provider
            # For now, we'll just verify the basic structure works

            all_messages = msg_manager.list(conversation_id=conversation.id)

            # The user message should exist
            assert len(all_messages) >= 1
            assert user_message.content == "Hello, how are you?"
            assert user_message.user_id == admin_a.id

            # Verify the conversation agent setup
            # assert conversation_agent.conversation_id == conversation.id
            # assert conversation_agent.agent_id == agent.id
            # assert conversation_agent.auto_respond is True
        # TODO: Verify the agent response message

        # Clean up
        # with self.class_under_test(
        #     requester_id=admin_a.id, model_registry=model_registry
        # ) as cleanup_conv_agent:
        #     cleanup_conv_agent.delete(id=conversation_agent.id)

        with MessageManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as msg_cleanup:
            msg_cleanup.delete(id=user_message.id)

        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agent_cleanup:
            agent_cleanup.delete(id=agent.id)

        # with RotationManager(
        #     requester_id=admin_a.id, model_registry=model_registry
        # ) as rotation_cleanup:
        #     rotation_cleanup.delete(id=rotation.id)

        # with ProviderInstanceManager(
        #     requester_id=admin_a.id, model_registry=model_registry
        # ) as pi_cleanup:
        #     pi_cleanup.delete(id=provider_instance.id)

        # with ProviderManager(
        #     requester_id=admin_a.id, model_registry=model_registry
        # ) as provider_cleanup:
        #     provider_cleanup.delete(id=provider.id)

        with ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as conv_cleanup:
            conv_cleanup.delete(id=conversation.id)


class TestProviderInstanceAgentManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = ProviderInstanceAgentManager
    extension_class = EXT_AI_Agents

    create_fields = {}  # No specific fields apart from relationships
    update_fields = {}
    unique_fields = []

    from zephyrex.logic.BLL_Providers_test import (
        TestProviderInstanceManager as CoreProviderInstanceManagerTests,
    )

    parent_entities = [
        ParentEntity(name="agent", foreign_key="agent_id", test_class=TestAgentManager),
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            test_class=CoreProviderInstanceManagerTests,
        ),
    ]


class TestProviderInstanceAgentAbilityManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = ProviderInstanceAgentAbilityManager
    extension_class = EXT_AI_Agents

    create_fields = {"state": lambda: False}
    update_fields = {"state": True}
    unique_fields = []

    from zephyrex.logic.BLL_Providers_test import (
        TestProviderInstanceManager as CoreProviderInstanceManagerTests,
    )

    parent_entities = [
        ParentEntity(name="agent", foreign_key="agent_id", test_class=TestAgentManager),
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            test_class=CoreProviderInstanceManagerTests,
        ),
    ]

    def test_create_with_valid_provider_instance(self, admin_a, team_a, model_registry):
        """Test creating a ProviderInstanceAgentAbility with valid provider instance."""
        # Create agent first
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agent_manager:
            agent = agent_manager.create(name=f"Test Agent {faker.word()}")

        # Create provider instance
        from zephyrex.logic.BLL_Providers import (
            ProviderInstanceManager,
            ProviderManager,
        )

        with ProviderManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as provider_manager:
            provider = provider_manager.create(
                name=f"Test Provider {faker.word()}", friendly_name="Test Provider"
            )

        with ProviderInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as pi_manager:
            provider_instance = pi_manager.create(
                name=f"Test Provider Instance {faker.word()}",
                provider_id=provider.id,
                api_key="test-key",
                model_name="test-model",
            )

        # Create ability association
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            ability = manager.create(
                agent_id=agent.id, provider_instance_id=provider_instance.id, state=True
            )

            assert ability is not None
            assert ability.agent_id == agent.id
            assert ability.provider_instance_id == provider_instance.id
            assert ability.state is True

            # Clean up
            manager.delete(id=ability.id)

        # Clean up related entities
        with ProviderInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as pi_cleanup:
            pi_cleanup.delete(id=provider_instance.id)

        with ProviderManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as provider_cleanup:
            provider_cleanup.delete(id=provider.id)

        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agent_cleanup:
            agent_cleanup.delete(id=agent.id)


class TestInvocationTriggerManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = InvocationTriggerManager
    extension_class = EXT_AI_Agents

    create_fields = {
        "invocation_type": lambda: "timer",
        "interval_seconds": lambda: 300,
    }
    update_fields = {"invocation_payload": "updated payload", "enabled": False}
    unique_fields = []

    parent_entities = [
        ParentEntity(name="agent", foreign_key="agent_id", test_class=TestAgentManager),
    ]

    def test_timer_trigger_fields(self, admin_a, team_a, model_registry):
        """A timer trigger persists its cadence and bookkeeping defaults."""
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            agent = agents.create(name=f"Agent {uuid.uuid4()}")
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as triggers:
            trigger = triggers.create(
                agent_id=agent.id, invocation_type="timer", interval_seconds=300
            )
            assert trigger.invocation_type == "timer"
            assert trigger.interval_seconds == 300
            assert trigger.enabled is True
            assert trigger.fire_count == 0
            triggers.delete(id=trigger.id)


class TestInvocationInstanceManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = InvocationInstanceManager
    extension_class = EXT_AI_Agents

    create_fields = {"status": lambda: "pending"}
    update_fields = {"status": "running"}
    unique_fields = []

    parent_entities = [
        ParentEntity(name="agent", foreign_key="agent_id", test_class=TestAgentManager),
    ]

    def test_instance_links_to_trigger(self, admin_a, team_a, model_registry):
        """An instance can record the trigger that fired it (or none)."""
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            agent = agents.create(name=f"Agent {uuid.uuid4()}")
        with InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as triggers:
            trigger = triggers.create(
                agent_id=agent.id, invocation_type="timer", interval_seconds=300
            )
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as instances:
            # A firing bound to its trigger.
            instance = instances.create(
                agent_id=agent.id, invocation_trigger_id=trigger.id
            )
            assert instance.invocation_trigger_id == trigger.id
            assert instance.status == "pending"
            # An ad-hoc firing with no standing trigger is also valid.
            ad_hoc = instances.create(agent_id=agent.id)
            assert ad_hoc.invocation_trigger_id is None
            instances.delete(id=instance.id)
            instances.delete(id=ad_hoc.id)

    def test_unknown_trigger_rejected(self, admin_a, team_a, model_registry):
        from fastapi import HTTPException

        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            agent = agents.create(name=f"Agent {uuid.uuid4()}")
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as instances:
            with pytest.raises(HTTPException) as exc:
                instances.create(
                    agent_id=agent.id, invocation_trigger_id="does-not-exist"
                )
            assert exc.value.status_code == 404


class TestProjectManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = ProjectManager
    extension_class = EXT_AI_Agents

    create_fields = {
        "name": lambda: f"Test Project {faker.word()}",
        "description": lambda: faker.text(max_nb_chars=200),
        "user_id": lambda: None,
    }
    update_fields = {
        "name": "Updated Project",
        "description": "Updated project description",
    }
    unique_fields = ["name"]

    parent_entities = [
        ParentEntity(
            name="team", foreign_key="team_id", test_class=CoreTeamManagerTests
        ),
    ]

    def create_parent_entities(self, manager_class, field_name):
        """Override to handle optional parent dependencies."""
        if field_name == "team_id":
            return None  # This is optional
        return super().create_parent_entities(manager_class, field_name)

    def test_project_with_team(self, admin_a, team_a, model_registry):
        """Test creating a project with a team association."""
        with self.class_under_test(
            requester_id=admin_a.id, target_id=admin_a.id, model_registry=model_registry
        ) as manager:
            project = manager.create(
                name=f"Team Project {faker.word()}",
                description="Project for team A",
                team_id=team_a.id,
            )

            assert project is not None
            assert project.team_id == team_a.id
            assert project.user_id == admin_a.id
            assert "Team Project" in project.name

            # Clean up
            manager.delete(id=project.id)


class TestProjectContextProviderManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = ProjectContextProviderManager
    extension_class = EXT_AI_Agents

    create_fields = {"context_resource": lambda: f"resource_{faker.word()}"}
    update_fields = {"context_resource": "updated_resource"}
    unique_fields = []

    parent_entities = [
        ParentEntity(
            name="provider",
            foreign_key="provider_id",
            test_class=CoreProviderManagerTests,
        ),
        ParentEntity(
            name="project", foreign_key="project_id", test_class=TestProjectManager
        ),
    ]


# Test class with optional ai_prompts dependency
try:

    class TestProjectContextPromptManager(AbstractBLLTest, ExtensionServerMixin):
        class_under_test = ProjectContextPromptManager
        extension_class = EXT_AI_Agents
        server = _extension_server_fixture("ai_prompts")

        create_fields = {}
        update_fields = {}
        unique_fields = []

        parent_entities = [
            ParentEntity(
                name="project", foreign_key="project_id", test_class=TestProjectManager
            ),
            ParentEntity(
                name="prompt", foreign_key="prompt_id", test_class=ExtPromptManagerTests
            ),
        ]

except ImportError:
    # Skip this test if ai_prompts extension is not available
    TestProjectContextPromptManager = None


# Test class with optional ai_prompts dependency
try:

    class TestAgentContextPromptManager(AbstractBLLTest, ExtensionServerMixin):
        class_under_test = AgentContextPromptManager
        extension_class = EXT_AI_Agents
        server = _extension_server_fixture("ai_prompts")

        create_fields = {}
        update_fields = {}
        unique_fields = []

        parent_entities = [
            ParentEntity(
                name="agent", foreign_key="agent_id", test_class=TestAgentManager
            ),
            ParentEntity(
                name="prompt", foreign_key="prompt_id", test_class=ExtPromptManagerTests
            ),
        ]

        def test_create_agent_context_prompt(self, admin_a, team_a, model_registry):
            """Test creating an agent context prompt association."""
            # Create agent
            with AgentManager(
                requester_id=admin_a.id, model_registry=model_registry
            ) as agent_manager:
                agent = agent_manager.create(name=f"Context Agent {faker.word()}")

            # Create prompt
            from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptManager

            with PromptManager(
                requester_id=admin_a.id, model_registry=model_registry
            ) as prompt_manager:
                prompt = prompt_manager.create(
                    name=f"Test Prompt {faker.word()}",
                    content="Test prompt content",
                    description="Test prompt description",
                )

            # Create association
            with self.class_under_test(
                requester_id=admin_a.id, model_registry=model_registry
            ) as manager:
                context_prompt = manager.create(agent_id=agent.id, prompt_id=prompt.id)

                assert context_prompt is not None
                assert context_prompt.agent_id == agent.id
                assert context_prompt.prompt_id == prompt.id

                # Clean up
                manager.delete(id=context_prompt.id)

            # Clean up related entities
            with PromptManager(
                requester_id=admin_a.id, model_registry=model_registry
            ) as prompt_cleanup:
                prompt_cleanup.delete(id=prompt.id)

            with AgentManager(
                requester_id=admin_a.id, model_registry=model_registry
            ) as agent_cleanup:
                agent_cleanup.delete(id=agent.id)

except ImportError:
    # Skip this test if ai_prompts extension is not available
    TestAgentContextPromptManager = None


# Test class with optional conversations dependency
try:

    class TestActivityManager(AbstractBLLTest, ExtensionServerMixin):
        class_under_test = ActivityManager
        extension_class = EXT_AI_Agents
        server = _extension_server_fixture("conversations")

        create_fields = {
            "title": lambda: f"Test Activity {faker.word()}",
            "body": lambda: faker.text(max_nb_chars=500),
        }
        update_fields = {
            "title": "Updated Activity",
            "body": "Updated activity body",
        }
        unique_fields = []

        parent_entities = [
            ParentEntity(
                name="invocation_instance",
                foreign_key="invocation_instance_id",
                test_class=TestInvocationInstanceManager,
            ),
            ParentEntity(
                name="ability",
                foreign_key="ability_id",
                test_class=CoreAbilityManagerTests,
            ),
        ]

        def test_activity_creation_with_instance(self, admin_a, team_a, model_registry):
            """Test creating an activity anchored to an invocation instance (turn)."""
            # Create the agent + the turn (invocation instance) the activity
            # belongs to.
            with AgentManager(
                requester_id=admin_a.id, model_registry=model_registry
            ) as agent_manager:
                agent = agent_manager.create(name=f"Agent {faker.word()}")

            with InvocationInstanceManager(
                requester_id=admin_a.id, model_registry=model_registry
            ) as instance_manager:
                instance = instance_manager.create(agent_id=agent.id)

            # Create ability
            from zephyrex.logic.BLL_Extensions import AbilityManager, ExtensionManager

            # First create an extension
            with ExtensionManager(
                requester_id=admin_a.id, model_registry=model_registry
            ) as extension_manager:
                extension = extension_manager.create(
                    name=f"test_extension_{faker.word()}",
                    friendly_name="Test Extension",
                )

            with AbilityManager(
                requester_id=admin_a.id, model_registry=model_registry
            ) as ability_manager:
                ability = ability_manager.create(
                    name=f"test_ability_{faker.word()}",
                    friendly_name="Test Ability",
                    extension_id=extension.id,
                )

            # Create activity
            with self.class_under_test(
                requester_id=admin_a.id, model_registry=model_registry
            ) as manager:
                activity = manager.create(
                    invocation_instance_id=instance.id,
                    ability_id=ability.id,
                    title="Test Activity",
                    body="Activity performed during the turn",
                )

                assert activity is not None
                assert activity.invocation_instance_id == instance.id
                assert activity.ability_id == ability.id
                assert activity.title == "Test Activity"

                # Update activity
                updated = manager.update(id=activity.id, title="Updated Activity Title")
                assert updated.title == "Updated Activity Title"

                # Clean up
                manager.delete(id=activity.id)

            # Clean up related entities
            with AbilityManager(
                requester_id=admin_a.id, model_registry=model_registry
            ) as ability_cleanup:
                ability_cleanup.delete(id=ability.id)

            with InvocationInstanceManager(
                requester_id=admin_a.id, model_registry=model_registry
            ) as instance_cleanup:
                instance_cleanup.delete(id=instance.id)

            with AgentManager(
                requester_id=admin_a.id, model_registry=model_registry
            ) as agent_cleanup:
                agent_cleanup.delete(id=agent.id)

except ImportError:
    # Skip this test if conversations extension is not available
    TestActivityManager = None


class TestTeamManagerWithAgentHook(CoreTeamManagerTests, ExtensionServerMixin):
    """Test TeamManager with AI agent creation hook."""

    extension_class = EXT_AI_Agents

    @classmethod
    def setUpClass(cls):
        """Ensure the AI agents extension is loaded before running tests."""
        super().setUpClass()
        # Force import of BLL_AI_Agents to register hooks
        import zephyrex.extensions.ai_agents.BLL_AI_Agents

    def test_team_creation_creates_agent(self, admin_a, model_registry):
        """Test that creating a team automatically creates an associated agent."""
        # Force import the BLL_AI_Agents module to ensure hooks are registered
        import zephyrex.extensions.ai_agents.BLL_AI_Agents
        from zephyrex.logic.BLL_Auth import TeamManager

        # Debug: Check if the hook is registered
        print(
            f"TeamManager has _hook_registry: {hasattr(TeamManager, '_hook_registry')}"
        )
        print(f"TeamManager MRO: {[c.__name__ for c in TeamManager.__mro__]}")
        if hasattr(TeamManager, "_hook_registry"):
            print(f"Hook registry hooks: {TeamManager._hook_registry.hooks}")
            create_hooks = TeamManager._hook_registry.get_hooks("create")
            print(f"Create method hooks: {create_hooks}")

        # Create a team
        team_name = f"Test Team {faker.word()}"
        with TeamManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as team_manager:
            team = team_manager.create(
                name=team_name, description="Test team for agent hook verification"
            )

        # Verify the team was created
        assert team is not None
        assert team.name == team_name

        # Check if an agent was created for this team
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agent_manager:
            # First list all agents to debug
            all_agents = agent_manager.list()
            print(f"Total agents in system: {len(all_agents)}")

            # Search for agents belonging to this team
            agents = agent_manager.list(team_id=team.id)
            print(f"Agents for team {team.id}: {len(agents)}")

            # Should have exactly one agent
            assert len(agents) == 1
            agent = agents[0]

            # Verify agent properties
            assert agent.team_id == team.id
            assert agent.name == f"{team_name} Agent"

            # Clean up - delete the agent
            agent_manager.delete(id=agent.id)

        # Clean up - delete the team
        with TeamManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as team_cleanup:
            team_cleanup.delete(id=team.id)


class TestConversationManagerWithAgentHook(
    ExtConversationManagerTests, ExtensionServerMixin
):
    """Test ConversationManager with AI agent creation hook."""

    extension_class = EXT_AI_Agents
    server = _extension_server_fixture("conversations")

    @classmethod
    def setUpClass(cls):
        """Ensure the AI agents extension is loaded before running tests."""
        super().setUpClass()
        # Force import of BLL_AI_Agents to register hooks
        import zephyrex.extensions.ai_agents.BLL_AI_Agents

    def test_conversation_creation_creates_agent(self, admin_a, team_a, model_registry):
        """Test that creating a conversation automatically creates an associated agent."""
        # Force import the BLL_AI_Agents module to ensure hooks are registered
        import zephyrex.extensions.ai_agents.BLL_AI_Agents
        from zephyrex.extensions.conversations.BLL_Conversations import (
            ConversationManager,
        )

        # Debug: Check if the hook is registered
        print(
            f"ConversationManager has _hook_registry: {hasattr(ConversationManager, '_hook_registry')}"
        )

        # Get the RootAi rotation
        from zephyrex.logic.BLL_Providers import RotationManager

        with RotationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as rotation_manager:
            rotation = _get_or_create_root_ai_rotation(rotation_manager)

        # create an agent with rotation for the team
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agent_manager:
            # create an agent for the team
            agent = agent_manager.create(
                name=f"Test Agent",
                team_id=team_a.id,
                rotation_id=rotation.id,
            )

        # Create a conversation
        conversation_name = f"Test Conversation {faker.word()}"
        with ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as conv_manager:
            conversation = conv_manager.create(name=conversation_name)

        # Verify the conversation was created
        assert conversation is not None
        assert conversation.name == conversation_name

        # check if agent is associated with the conversation
        with ConversationAgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as conv_agent_manager:
            agents = conv_agent_manager.list(conversation_id=conversation.id)

            # Should have exactly one agent
            assert len(agents) == 1
            conversation_agent = agents[0]

            # Verify agent properties
            assert conversation_agent.conversation_id == conversation.id
            assert conversation_agent.agent_id is not None
            assert conversation_agent.agent_id == agent.id
            assert conversation_agent.active is True
            assert conversation_agent.auto_respond is True

            # Clean up - delete the conversation agent
            conv_agent_manager.delete(id=conversation_agent.id)

        # Clean up - delete the conversation
        with ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as conv_cleanup:
            conv_cleanup.delete(id=conversation.id)
        # Clean up - delete the agent
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agent_cleanup:
            agent_cleanup.delete(id=agent.id)
