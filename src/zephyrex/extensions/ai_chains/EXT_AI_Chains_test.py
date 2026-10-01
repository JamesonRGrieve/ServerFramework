from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.ai_chains.EXT_AI_Chains import EXT_AI_Chains


class TestAIChainsExtension:
    """Test cases for AI Chains Extension."""

    @pytest.fixture
    def extension(self):
        """Create an AIChainsExtension instance for testing."""
        return EXT_AI_Chains()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "ai_chains"
        assert extension.version == "1.0.0"
        assert "ai workflow" in extension.description.lower()
        assert "chains" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps
        assert "ai" in ext_deps
        assert "database" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "pydantic" in pip_deps
        assert "asyncio" in pip_deps
        assert "jsonschema" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "chain_creation",
            "chain_execution",
            "workflow_management",
            "step_orchestration",
            "data_flow_management",
            "chain_monitoring",
            "error_handling",
            "conditional_branching",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "bll_managers")
        assert hasattr(extension, "commands")
        assert hasattr(extension, "active_chains")
        assert isinstance(extension.bll_managers, dict)
        assert isinstance(extension.commands, dict)
        assert isinstance(extension.active_chains, dict)

    @patch("zephyrex.extensions.ai_chains.EXT_AI_Chains.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_register_managers"), patch.object(
            extension, "_register_commands"
        ), patch.object(extension, "register_capability"):
            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.ai_chains.EXT_AI_Chains.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_register_managers", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_register_managers(self, extension):
        """Test manager registration."""
        with patch("builtins.__import__") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module

            extension._register_managers()

            assert mock_import.called

    def test_register_commands(self, extension):
        """Test command registration."""
        extension._register_commands()

        expected_commands = {
            "create_chain",
            "update_chain",
            "delete_chain",
            "execute_chain",
            "pause_chain",
            "resume_chain",
            "stop_chain",
            "get_chain_status",
            "list_chains",
            "validate_chain",
        }
        assert set(extension.commands.keys()) == expected_commands

    def test_capability_management(self, extension):
        """Test capability management methods."""
        # Test register_capability
        extension.register_capability("test_capability")
        assert "test_capability" in extension.capabilities

        # Test get_registered_capabilities
        capabilities = extension.get_registered_capabilities()
        assert isinstance(capabilities, set)
        assert "test_capability" in capabilities

        # Test get_capabilities
        capabilities = extension.get_capabilities()
        assert isinstance(capabilities, set)

        # Test has_capability
        assert extension.has_capability("test_capability") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_create_chain_success(self, extension):
        """Test successful chain creation."""
        extension.bll_managers["ChainManager"] = MagicMock()

        steps = [
            {"type": "ai_prompt", "name": "Step 1", "config": {"prompt": "Hello"}},
            {"type": "ai_prompt", "name": "Step 2", "config": {"prompt": "World"}},
        ]

        result = await extension.create_chain(
            name="Test Chain",
            description="Test Description",
            steps=steps,
            metadata={"version": "1.0"},
        )

        assert result["success"] is True
        assert result["chain"]["name"] == "Test Chain"
        assert result["chain"]["steps"] == steps
        assert "Test Chain" in result["message"]

    @pytest.mark.asyncio
    async def test_create_chain_no_manager(self, extension):
        """Test chain creation without manager."""
        result = await extension.create_chain(name="Test Chain")

        assert result["success"] is False
        assert "ChainManager not available" in result["message"]

    @pytest.mark.asyncio
    async def test_create_chain_validation_failure(self, extension):
        """Test chain creation with validation failure."""
        extension.bll_managers["ChainManager"] = MagicMock()

        # Create invalid chain (missing required fields in steps)
        steps = [{"invalid": "step"}]

        result = await extension.create_chain(
            name="Invalid Chain", description="Test", steps=steps
        )

        assert result["success"] is False
        assert "validation failed" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_update_chain_success(self, extension):
        """Test successful chain update."""
        extension.bll_managers["ChainManager"] = MagicMock()

        result = await extension.update_chain(
            chain_id="test_id",
            name="Updated Chain",
            description="Updated Description",
        )

        assert result["success"] is True
        assert result["chain_id"] == "test_id"
        assert result["updates"]["name"] == "Updated Chain"
        assert result["updates"]["description"] == "Updated Description"

    @pytest.mark.asyncio
    async def test_delete_chain_success(self, extension):
        """Test successful chain deletion."""
        extension.bll_managers["ChainManager"] = MagicMock()

        result = await extension.delete_chain(chain_id="test_id")

        assert result["success"] is True
        assert result["chain_id"] == "test_id"
        assert "deleted successfully" in result["message"]

    @pytest.mark.asyncio
    async def test_execute_chain_success(self, extension):
        """Test successful chain execution."""
        extension.bll_managers["ExecutionManager"] = MagicMock()

        input_data = {"input": "test data"}
        execution_config = {"timeout": 30}

        result = await extension.execute_chain(
            chain_id="test_chain",
            input_data=input_data,
            execution_config=execution_config,
        )

        assert result["success"] is True
        assert "execution_id" in result
        assert result["chain_id"] == "test_chain"
        assert "result" in result
        assert "execution started successfully" in result["message"]

        # Check that execution was stored in active_chains
        execution_id = result["execution_id"]
        assert execution_id in extension.active_chains
        assert extension.active_chains[execution_id]["status"] == "running"

    @pytest.mark.asyncio
    async def test_pause_chain_success(self, extension):
        """Test successful chain pause."""
        # Add an active chain execution
        execution_id = "test_exec_1"
        extension.active_chains[execution_id] = {
            "chain_id": "test_chain",
            "status": "running",
        }

        result = await extension.pause_chain(execution_id)

        assert result["success"] is True
        assert result["execution_id"] == execution_id
        assert "paused" in result["message"]
        assert extension.active_chains[execution_id]["status"] == "paused"

    @pytest.mark.asyncio
    async def test_pause_chain_not_found(self, extension):
        """Test pause chain with non-existent execution."""
        result = await extension.pause_chain("nonexistent_id")

        assert result["success"] is False
        assert "Execution not found" in result["message"]

    @pytest.mark.asyncio
    async def test_resume_chain_success(self, extension):
        """Test successful chain resume."""
        # Add a paused chain execution
        execution_id = "test_exec_1"
        extension.active_chains[execution_id] = {
            "chain_id": "test_chain",
            "status": "paused",
        }

        result = await extension.resume_chain(execution_id)

        assert result["success"] is True
        assert result["execution_id"] == execution_id
        assert "resumed" in result["message"]
        assert extension.active_chains[execution_id]["status"] == "running"

    @pytest.mark.asyncio
    async def test_stop_chain_success(self, extension):
        """Test successful chain stop."""
        # Add a running chain execution
        execution_id = "test_exec_1"
        extension.active_chains[execution_id] = {
            "chain_id": "test_chain",
            "status": "running",
        }

        result = await extension.stop_chain(execution_id)

        assert result["success"] is True
        assert result["execution_id"] == execution_id
        assert "stopped" in result["message"]
        assert extension.active_chains[execution_id]["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_get_chain_status_success(self, extension):
        """Test successful chain status retrieval."""
        # Add an active chain execution
        execution_id = "test_exec_1"
        execution_data = {
            "chain_id": "test_chain",
            "status": "running",
            "started_at": "2024-01-01T00:00:00Z",
        }
        extension.active_chains[execution_id] = execution_data

        result = await extension.get_chain_status(execution_id)

        assert result["success"] is True
        assert result["execution_id"] == execution_id
        assert result["status"] == execution_data

    @pytest.mark.asyncio
    async def test_list_chains_success(self, extension):
        """Test successful chain listing."""
        extension.bll_managers["ChainManager"] = MagicMock()

        result = await extension.list_chains(limit=10)

        assert result["success"] is True
        assert "chains" in result
        assert "count" in result
        assert isinstance(result["chains"], list)

    @pytest.mark.asyncio
    async def test_list_chains_with_status_filter(self, extension):
        """Test chain listing with status filter."""
        extension.bll_managers["ChainManager"] = MagicMock()

        result = await extension.list_chains(status="active", limit=10)

        assert result["success"] is True
        assert result["status_filter"] == "active"

    @pytest.mark.asyncio
    async def test_validate_chain_success(self, extension):
        """Test successful chain validation."""
        chain_definition = {
            "name": "Test Chain",
            "steps": [
                {"type": "ai_prompt", "name": "Step 1"},
                {"type": "ai_prompt", "name": "Step 2"},
            ],
        }

        result = await extension.validate_chain(chain_definition)

        assert result["success"] is True
        assert result["valid"] is True
        assert isinstance(result["errors"], list)
        assert isinstance(result["warnings"], list)

    @pytest.mark.asyncio
    async def test_validate_chain_with_errors(self, extension):
        """Test chain validation with errors."""
        chain_definition = {
            "description": "Missing name",
            "steps": [{"invalid": "step"}],  # Missing required fields
        }

        result = await extension.validate_chain(chain_definition)

        assert result["success"] is True
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    @pytest.mark.asyncio
    async def test_validate_chain_structure(self, extension):
        """Test internal chain structure validation."""
        # Test valid chain
        valid_chain = {
            "name": "Valid Chain",
            "steps": [{"type": "ai_prompt", "name": "Step 1"}],
        }
        result = await extension._validate_chain_structure(valid_chain)
        assert result["valid"] is True
        assert len(result["errors"]) == 0

        # Test invalid chain - missing name
        invalid_chain = {"steps": []}
        result = await extension._validate_chain_structure(invalid_chain)
        assert result["valid"] is False
        assert any("name is required" in error for error in result["errors"])

        # Test invalid chain - missing steps
        invalid_chain = {"name": "Test"}
        result = await extension._validate_chain_structure(invalid_chain)
        assert result["valid"] is False
        assert any("steps are required" in error for error in result["errors"])

        # Test invalid chain - invalid step structure
        invalid_chain = {
            "name": "Test",
            "steps": [{"invalid": "step"}],  # Missing 'type' field
        }
        result = await extension._validate_chain_structure(invalid_chain)
        assert result["valid"] is False
        assert any(
            "missing required 'type' field" in error for error in result["errors"]
        )

    @pytest.mark.asyncio
    async def test_execute_chain_steps(self, extension):
        """Test internal chain step execution."""
        result = await extension._execute_chain_steps("test_chain", {"input": "data"})

        assert isinstance(result, dict)
        assert "steps_executed" in result
        assert "final_output" in result
        assert "execution_time" in result

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        # Test on_start
        assert extension.on_start() is True

        # Test on_stop
        extension.active_chains["test_exec"] = {"status": "running"}
        assert extension.on_stop() is True
        assert extension.active_chains["test_exec"]["status"] == "stopped"

        # Test on_startup
        extension.on_startup()  # Should not raise exception

        # Test on_shutdown
        extension.on_shutdown()  # Should not raise exception

    def test_validate_config(self, extension):
        """Test configuration validation."""
        with patch("builtins.__import__") as mock_import:
            # Test successful validation
            issues = extension.validate_config()
            assert isinstance(issues, list)

            # Test missing dependencies
            mock_import.side_effect = ImportError("Module not found")
            issues = extension.validate_config()
            assert len(issues) > 0
            assert any("Pydantic" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "chains:create",
            "chains:read",
            "chains:update",
            "chains:delete",
            "chains:execute",
            "chains:monitor",
            "ai:inference",
        ]
        assert set(permissions) == set(expected_permissions)

    @pytest.mark.asyncio
    async def test_chain_operations_no_manager(self, extension):
        """Test chain operations without managers."""
        # Test create_chain
        result = await extension.create_chain(name="Test")
        assert result["success"] is False
        assert "ChainManager not available" in result["message"]

        # Test update_chain
        result = await extension.update_chain(chain_id="test", name="Updated")
        assert result["success"] is False

        # Test delete_chain
        result = await extension.delete_chain(chain_id="test")
        assert result["success"] is False

        # Test execute_chain
        result = await extension.execute_chain(chain_id="test")
        assert result["success"] is False
        assert "ExecutionManager not available" in result["message"]

        # Test list_chains
        result = await extension.list_chains()
        assert result["success"] is False
        assert "ChainManager not available" in result["message"]

    @pytest.mark.asyncio
    async def test_error_handling(self, extension):
        """Test error handling in abilities."""
        extension.bll_managers["ChainManager"] = MagicMock()

        with patch.object(
            extension, "bll_managers", side_effect=Exception("Test error")
        ):
            result = await extension.create_chain(name="Test")
            assert result["success"] is False
            assert "Error creating chain" in result["message"]


if __name__ == "__main__":
    pytest.main([__file__])
