# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid

from faker import Faker

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity
from zephyrex.database.AbstractDBTest import AbstractDBTest
from zephyrex.database.DB_Auth_test import TestTeam as CoreTeamTests
from zephyrex.database.DB_Auth_test import TestUser as CoreUserTests
from zephyrex.database.DB_Extensions_test import TestAbility as CoreAbilityTests
from zephyrex.database.DB_Providers_test import TestProvider as CoreProviderTests
from zephyrex.database.DB_Providers_test import (
    TestProviderInstance as CoreProviderInstanceTests,
)
from zephyrex.database.DB_Providers_test import TestRotation as CoreRotationTests
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    ActivityModel,
    ActivityState,
    AgentAbilityModel,
    AgentContextPromptModel,
    AgentMemoryModel,
    AgentModel,
    InvocationInstanceModel,
    InvocationTriggerModel,
    ProjectContextPromptModel,
    ProjectContextProviderModel,
    ProjectConversationModel,
    ProjectModel,
    ProviderInstanceAgentAbilityModel,
    ProviderInstanceAgentModel,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents
from zephyrex.extensions.ai_prompts.DB_AI_Prompts_test import (
    TestPrompt as ExtPromptTests,
)
from zephyrex.extensions.conversations.DB_Conversations_test import (
    TestArtifact as ExtArtifactTests,
)
from zephyrex.extensions.conversations.DB_Conversations_test import (
    TestConversation as ExtConversationTests,
)
from zephyrex.extensions.conversations.DB_Conversations_test import (
    TestMessage as ExtMessageTests,
)

# Set default test configuration for all test classes
AbstractDBTest.test_config = ClassOfTestsConfig(
    categories=[CategoryOfTest.DATABASE, CategoryOfTest.EXTENSION]
)

faker = Faker()


class TestAgent(AbstractDBTest, ExtensionServerMixin):
    """Test AgentModel database operations."""

    class_under_test = AgentModel
    extension_class = EXT_AI_Agents

    create_fields = {
        "name": lambda: f"Test Agent {faker.unique.uuid4()}",
        "favourite": False,
    }
    update_fields = {
        "name": "Updated Agent",
        "favourite": True,
    }
    parent_entities = [
        ParentEntity(
            name="rotation",
            foreign_key="rotation_id",
            test_class=CoreRotationTests,
        ),
        ParentEntity(
            name="team",
            foreign_key="team_id",
            test_class=CoreTeamTests,
            optional=True,
        ),
        ParentEntity(
            name="user",
            foreign_key="user_id",
            test_class=CoreUserTests,
            optional=True,
        ),
    ]


class TestInvocationTrigger(AbstractDBTest, ExtensionServerMixin):
    """Test InvocationTriggerModel database operations."""

    class_under_test = InvocationTriggerModel
    extension_class = EXT_AI_Agents

    create_fields = {
        "invocation_type": lambda: "timer",
        "interval_seconds": lambda: 300,
        "agent_id": lambda: str(uuid.uuid4()),
    }
    update_fields = {"enabled": False, "invocation_payload": "updated"}
    parent_entities = [
        ParentEntity(name="agent", foreign_key="agent_id", test_class=TestAgent),
    ]
    unique_fields = []


class TestInvocationInstance(AbstractDBTest, ExtensionServerMixin):
    """Test InvocationInstanceModel database operations."""

    class_under_test = InvocationInstanceModel
    extension_class = EXT_AI_Agents

    create_fields = {
        "status": lambda: "pending",
        "agent_id": lambda: str(uuid.uuid4()),
    }
    update_fields = {"status": "running"}
    parent_entities = [
        ParentEntity(name="agent", foreign_key="agent_id", test_class=TestAgent),
        ParentEntity(
            name="invocation_trigger",
            foreign_key="invocation_trigger_id",
            test_class=TestInvocationTrigger,
            optional=True,
        ),
    ]
    unique_fields = []


class TestAgentAbility(AbstractDBTest, ExtensionServerMixin):
    """Test AgentAbilityModel database operations (the tool allowlist)."""

    class_under_test = AgentAbilityModel
    extension_class = EXT_AI_Agents

    create_fields = {
        "agent_id": lambda: str(uuid.uuid4()),
        "ability_id": lambda: str(uuid.uuid4()),
        "enabled": True,
    }
    update_fields = {"enabled": False}
    parent_entities = [
        ParentEntity(name="agent", foreign_key="agent_id", test_class=TestAgent),
        ParentEntity(
            name="ability", foreign_key="ability_id", test_class=CoreAbilityTests
        ),
    ]
    unique_fields = []
    is_system_entity = False


class TestAgentMemory(AbstractDBTest, ExtensionServerMixin):
    """Test AgentMemoryModel database operations (short-term memory)."""

    class_under_test = AgentMemoryModel
    extension_class = EXT_AI_Agents

    create_fields = {
        "key": lambda: f"key-{faker.unique.uuid4()}",
        "content": lambda: faker.text(max_nb_chars=120),
    }
    update_fields = {"content": "updated content"}
    parent_entities = [
        ParentEntity(name="agent", foreign_key="agent_id", test_class=TestAgent),
    ]
    unique_fields = []
    is_system_entity = False


class TestProviderInstanceAgent(AbstractDBTest, ExtensionServerMixin):
    """Test ProviderInstanceAgentModel database operations."""

    class_under_test = ProviderInstanceAgentModel
    extension_class = EXT_AI_Agents

    # Use the Pydantic field names as they appear in the BLL model
    create_fields = {
        "provider_instance_id": lambda: str(uuid.uuid4()),
        "agent_id": lambda: str(uuid.uuid4()),
    }

    update_fields = {}

    parent_entities = [
        ParentEntity(
            name="agent",
            foreign_key="agent_id",
            test_class=TestAgent,
        ),
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",  # Use the Pydantic field name
            test_class=CoreProviderInstanceTests,
        ),
    ]

    # Add any additional configuration if needed
    unique_fields = []  # This model doesn't appear to have unique constraints
    is_system_entity = False


class TestProviderInstanceAgentAbility(AbstractDBTest, ExtensionServerMixin):
    """Test ProviderInstanceAgentAbilityModel database operations."""

    class_under_test = ProviderInstanceAgentAbilityModel
    extension_class = EXT_AI_Agents

    create_fields = {
        "provider_instance_id": lambda: str(uuid.uuid4()),
        "agent_id": lambda: str(uuid.uuid4()),
        "ability_id": lambda: str(uuid.uuid4()),
        "state": False,
    }
    update_fields = {
        "state": True,
    }
    parent_entities = [
        ParentEntity(
            name="agent",
            foreign_key="agent_id",
            test_class=TestAgent,
        ),
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            test_class=CoreProviderInstanceTests,
        ),
        ParentEntity(
            name="ability", foreign_key="ability_id", test_class=CoreAbilityTests
        ),
    ]


class TestProject(AbstractDBTest, ExtensionServerMixin):
    """Test ProjectModel database operations."""

    class_under_test = ProjectModel
    extension_class = EXT_AI_Agents

    create_fields = {
        "name": lambda: f"Test Project {faker.unique.uuid4()}",
        "description": faker.text,
    }
    update_fields = {
        "name": "Updated Project",
        "description": "Updated project description",
    }
    parent_entities = [
        ParentEntity(
            name="team",
            foreign_key="team_id",
            test_class=CoreTeamTests,
            optional=True,
        ),
        ParentEntity(
            name="user",
            foreign_key="user_id",
            test_class=CoreUserTests,
        ),
    ]


class TestProjectContextPrompt(AbstractDBTest, ExtensionServerMixin):
    """Test ProjectContextPromptModel database operations."""

    class_under_test = ProjectContextPromptModel
    extension_class = EXT_AI_Agents

    create_fields = {}
    update_fields = {}
    parent_entities = [
        ParentEntity(
            name="project",
            foreign_key="project_id",
            test_class=TestProject,
        ),
        ParentEntity(
            name="prompt",
            foreign_key="prompt_id",
            test_class=ExtPromptTests,
        ),
    ]


class TestProjectConversation(AbstractDBTest, ExtensionServerMixin):
    """Test ProjectConversationModel database operations."""

    class_under_test = ProjectConversationModel
    extension_class = EXT_AI_Agents

    create_fields = {}
    update_fields = {}
    parent_entities = [
        ParentEntity(
            name="project",
            foreign_key="project_id",
            test_class=TestProject,
        ),
        ParentEntity(
            name="conversation",
            foreign_key="conversation_id",
            test_class=ExtConversationTests,
        ),
    ]


class TestProjectContextProvider(AbstractDBTest, ExtensionServerMixin):
    """Test ProjectContextProviderModel database operations."""

    class_under_test = ProjectContextProviderModel
    extension_class = EXT_AI_Agents

    create_fields = {
        "context_resource": lambda: f"test_resource_{faker.unique.uuid4()}"
    }
    update_fields = {"context_resource": "updated_resource"}
    parent_entities = [
        ParentEntity(
            name="project",
            foreign_key="project_id",
            test_class=TestProject,
        ),
        ParentEntity(
            name="provider",
            foreign_key="provider_id",
            test_class=CoreProviderTests,
        ),
    ]


class TestAgentContextPrompt(AbstractDBTest, ExtensionServerMixin):
    """Test AgentContextPromptModel database operations."""

    class_under_test = AgentContextPromptModel
    extension_class = EXT_AI_Agents

    create_fields = {
        "agent_id": lambda: str(uuid.uuid4()),
        "prompt_id": lambda: str(uuid.uuid4()),
    }
    update_fields = {}
    parent_entities = [
        ParentEntity(
            name="agent",
            foreign_key="agent_id",
            test_class=TestAgent,
        ),
        ParentEntity(
            name="prompt",
            foreign_key="prompt_id",
            test_class=ExtPromptTests,
        ),
    ]


class TestActivity(AbstractDBTest, ExtensionServerMixin):
    """Test ActivityModel database operations."""

    class_under_test = ActivityModel
    extension_class = EXT_AI_Agents

    create_fields = {
        "title": lambda: f"Test Activity {faker.unique.uuid4()}",
        "body": faker.text,
        "state": None,
    }
    update_fields = {
        "title": "Updated Activity",
        "body": "Updated activity body",
        "state": ActivityState.SUCCESS,
    }
    parent_entities = [
        ParentEntity(
            name="invocation_instance",
            foreign_key="invocation_instance_id",
            test_class=TestInvocationInstance,
        ),
        ParentEntity(
            name="ability",
            foreign_key="ability_id",
            test_class=CoreAbilityTests,
        ),
        ParentEntity(
            name="provider",
            foreign_key="provider_id",
            test_class=CoreProviderTests,
            optional=True,
        ),
        ParentEntity(
            name="artifact",
            foreign_key="artifact_id",
            test_class=ExtArtifactTests,
            optional=True,
        ),
    ]
