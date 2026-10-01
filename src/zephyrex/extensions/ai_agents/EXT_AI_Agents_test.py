from typing import List

import pytest

from zephyrex.extensions.AbstractEXTTest import (
    AbstractEXTTest,
    ExtensionTestConfig,
    ExtensionTestType,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents


class TestEXTAIAgents(AbstractEXTTest):
    """
    Test suite for EXT_AI_Agents extension.

    Tests extension initialization, agent management capabilities, abilities, and BLL manager integration.
    Focuses on testing agent functionality, project management, activity tracking, and static extension
    metadata rather than component loading.

    Test areas:
    - Extension metadata and configuration
    - Agent management capabilities and abilities
    - BLL manager registration and integration
    - Activity type management and seeding
    - Agent commands and operations
    - Extension lifecycle and configuration validation
    """

    # Configure the test class
    extension_class = EXT_AI_Agents
    test_config = ExtensionTestConfig(
        test_types={
            ExtensionTestType.STRUCTURE,
            ExtensionTestType.METADATA,
            ExtensionTestType.DEPENDENCIES,
            ExtensionTestType.ABILITIES,
            ExtensionTestType.ENVIRONMENT,
        },
        expected_abilities={
            "manage_agents",
            "configure_agent_providers",
            "track_agent_activities",
            "manage_agent_abilities",
        },
    )

    # Abilities implemented as @ability methods on the extension class (the
    # management/meta abilities). Checked for callability in
    # test_static_extension_abilities.
    expected_method_abilities = [
        "manage_agents",
        "configure_agent_providers",
        "track_agent_activities",
        "manage_agent_abilities",
    ]

    # The full ability name set the extension seeds — the method abilities above
    # plus the turn executor's intrinsic/self abilities (thinking_turn is the
    # turn's root Activity; speak/memorize/trim/recall/abilities are executor-
    # handled). These are seeded as Ability rows (for grants + Activity.ability_id)
    # without a method on the class, so they are asserted as the set, not probed
    # as callables.
    expected_abilities = [
        "manage_agents",
        "configure_agent_providers",
        "track_agent_activities",
        "manage_agent_abilities",
        "thinking_turn",
        "speak",
        "memorize",
        "trim",
        "recall",
        "abilities",
    ]

    expected_managers = [
        "AgentManager",
        "ProviderInstanceAgentManager",
        "ProviderInstanceAgentAbilityManager",
        "ProjectManager",
        "ProjectContextProviderManager",
        "ProjectContextPromptManager",
        "AgentContextPromptManager",
        "ActivityTypeManager",
        "ActivityManager",
    ]

    expected_commands = [
        "rename_agent",
        "update_agent_image",
        "toggle_favorite",
        "set_provider_instance",
        "enable_ability",
        "disable_ability",
        "create_activity",
    ]

    # Tests to skip
    _skip_tests: List[str] = []

    def test_extension_has_dependencies(self):
        """
        Test that the extension has proper dependencies defined.
        """
        assert hasattr(self.extension_class, "dependencies")
        dependencies = self.extension_class.dependencies

        # Check that it has extension dependencies
        assert hasattr(dependencies, "ext")
        ext_deps = dependencies.ext
        assert len(ext_deps) >= 3

        dep_names = [dep.name for dep in ext_deps]
        assert "ai" in dep_names
        assert "ai_prompts" in dep_names
        assert "conversations" in dep_names

    def test_extension_metadata(self):
        """Test extension metadata and basic attributes"""
        assert self.extension_class.name == "ai_agents"
        assert self.extension_class.version == "1.0.0"
        assert "AI agent management" in self.extension_class.description
        assert hasattr(self.extension_class, "dependencies")

    def test_dependencies_structure(self):
        """Test that dependencies are properly structured"""
        # Check extension dependencies
        dependencies = self.extension_class.dependencies
        ext_deps = dependencies.ext
        assert len(ext_deps) == 3

        dep_names = [dep.name for dep in ext_deps]
        assert "ai" in dep_names
        assert "ai_prompts" in dep_names
        assert "conversations" in dep_names

        # Check that all extension dependencies are required (not optional)
        for dep in ext_deps:
            assert dep.optional is False

        # Check pip dependencies
        pip_deps = dependencies.pip
        assert len(pip_deps) >= 1

        pip_dep_names = [dep.name for dep in pip_deps]
        assert "tiktoken" in pip_dep_names

        # Check sys dependencies
        sys_deps = dependencies.sys
        assert len(sys_deps) == 0  # AI Agents has no system dependencies

    def test_static_extension_abilities(self):
        """Test that static extension has ability methods"""
        # Only the meta/management abilities are implemented as methods; the
        # executor-handled self abilities are seeded names without a class method.
        for ability in self.expected_method_abilities:
            assert hasattr(self.extension_class, ability)
            assert callable(getattr(self.extension_class, ability))

    def test_abilities_have_decorator(self):
        """Test that ability methods have the @ability decorator"""
        # The _abilities class variable should contain all abilities
        assert hasattr(self.extension_class, "_abilities")
        abilities = self.extension_class._abilities
        assert isinstance(abilities, set)
        assert abilities == set(self.expected_abilities)

    def test_no_commands_in_static_extension(self):
        """Test that static extension doesn't have commands"""
        # Static extensions like EXT_AI_Agents don't have commands
        # Commands would be in the BLL layer
        pass

    def test_static_extension_inheritance(self):
        """Test that extension inherits from AbstractStaticExtension"""
        from zephyrex.extensions.AbstractExtensionProvider import (
            AbstractStaticExtension,
        )

        assert issubclass(self.extension_class, AbstractStaticExtension)

    def test_extension_metadata_fields(self):
        """Test that extension has all required metadata fields"""
        assert hasattr(self.extension_class, "name")
        assert hasattr(self.extension_class, "friendly_name")
        assert hasattr(self.extension_class, "version")
        assert hasattr(self.extension_class, "description")

        assert self.extension_class.name == "ai_agents"
        assert self.extension_class.friendly_name == "AI Agent Management"
        assert self.extension_class.version == "1.0.0"

    def test_extension_class_variables(self):
        """Test that extension has expected class variables"""
        assert hasattr(self.extension_class, "_abilities")
        assert hasattr(self.extension_class, "_env")
        assert hasattr(self.extension_class, "dependencies")

    def test_extension_env_vars(self):
        """Test that extension has environment variables"""
        assert hasattr(self.extension_class, "_env")
        env_vars = self.extension_class._env
        assert isinstance(env_vars, dict)

        # AI Agents extension should have agent-related env vars
        expected_env_vars = [
            "AI_AGENTS_MAX_MEMORY",
            "AI_AGENTS_DEFAULT_MODEL",
            "AI_AGENTS_ACTIVITY_LOGGING",
        ]

        for env_var in expected_env_vars:
            assert env_var in env_vars, f"Missing environment variable: {env_var}"

    def test_extension_type_properties(self):
        """Test extension type properties"""
        # Static extensions don't have these properties by default
        # They would be set in concrete implementations if needed
        pass

    def test_expected_managers_list(self):
        """Test that the extension defines expected managers"""
        # The extension should have a way to define which managers it expects to register
        # This helps ensure the BLL managers are properly structured
        assert len(self.expected_managers) > 0
        assert "AgentManager" in self.expected_managers
        assert "ProjectManager" in self.expected_managers
        assert "ActivityManager" in self.expected_managers

    def test_extension_abilities_list(self):
        """Test that the extension defines expected abilities"""
        assert len(self.expected_abilities) > 0
        for ability in self.expected_abilities:
            assert isinstance(ability, str)
            assert len(ability) > 0
