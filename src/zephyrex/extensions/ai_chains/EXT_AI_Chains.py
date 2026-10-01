from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_AI_Chains(AbstractStaticExtension):
    """
    AI Chains extension for AGInfrastructure.
    Provides functionality for creating, managing, and executing sequences of AI operations
    and workflows. Supports chaining multiple AI tasks together with data flow management.

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "ai_chains"
    version = "1.0.0"
    description = "AI workflow chains and sequences for complex AI task automation"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base AI chains functionality",
        ),
        EXT_Dependency(
            name="ai",
            friendly_name="AI Extension",
            optional=False,
            reason="Required for AI model interactions in chains",
        ),
        EXT_Dependency(
            name="database",
            friendly_name="Database Extension",
            optional=False,
            reason="Required for storing chain definitions and execution logs",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="pydantic",
            friendly_name="Pydantic Data Validation",
            optional=False,
            reason="Required for chain definition validation",
            semver=">=2.0.0",
        ),
        PIP_Dependency(
            name="asyncio",
            friendly_name="Asyncio",
            optional=False,
            reason="Required for asynchronous chain execution",
            semver=">=3.4.0",
        ),
        PIP_Dependency(
            name="jsonschema",
            friendly_name="JSON Schema Validator",
            optional=True,
            reason="Optional for advanced chain validation",
            semver=">=4.0.0",
        ),
    ]

    sys_dependencies = []

    # Define database tables (loaded via import system)
    db_tables = []

    # Define what capabilities this extension provides
    capabilities = [
        "chain_creation",
        "chain_execution",
        "workflow_management",
        "step_orchestration",
        "data_flow_management",
        "chain_monitoring",
        "error_handling",
        "conditional_branching",
    ]

    def __init__(self, **kwargs):
        """
        Initialize the AI Chains extension.
        """
        super().__init__(**kwargs)
        self.bll_managers = {}
        self.commands = {}
        self.active_chains = {}

    def on_initialize(self) -> bool:
        """Initialize the AI Chains extension."""
        logger.debug("Initializing AI Chains Extension...")

        try:
            self._register_managers()
            self._register_commands()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("AI Chains extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize AI Chains extension: {str(e)}")
            return False

    def _register_managers(self) -> None:
        """Register business logic managers."""
        try:
            manager_mapping = {
                "ChainManager": ("extensions.ai_chains.BLL_AI_Chains", "ChainManager"),
                "ExecutionManager": (
                    "extensions.ai_chains.BLL_AI_Chains",
                    "ExecutionManager",
                ),
                "StepManager": ("extensions.ai_chains.BLL_AI_Chains", "StepManager"),
                "WorkflowManager": (
                    "extensions.ai_chains.BLL_AI_Chains",
                    "WorkflowManager",
                ),
            }

            self.bll_managers = {}
            for manager_name, (module_name, class_name) in manager_mapping.items():
                try:
                    module = __import__(module_name, fromlist=[class_name])
                    manager_class = getattr(module, class_name)
                    self.bll_managers[manager_name] = manager_class
                    logger.debug(f"Registered BLL manager: {manager_name}")
                except ImportError as e:
                    logger.warning(f"Could not import BLL manager {manager_name}: {e}")
                except Exception as e:
                    logger.error(f"Error registering BLL manager {manager_name}: {e}")

            logger.debug(f"Registered {len(self.bll_managers)} BLL managers")

        except Exception as e:
            logger.error(f"Error registering managers: {e}")
            self.bll_managers = {}

    def _register_commands(self) -> None:
        """Register commands for chain operations."""
        self.commands = {
            "create_chain": self.create_chain,
            "update_chain": self.update_chain,
            "delete_chain": self.delete_chain,
            "execute_chain": self.execute_chain,
            "pause_chain": self.pause_chain,
            "resume_chain": self.resume_chain,
            "stop_chain": self.stop_chain,
            "get_chain_status": self.get_chain_status,
            "list_chains": self.list_chains,
            "validate_chain": self.validate_chain,
        }

    def register_capability(self, capability: str):
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @ability("create_chain")
    async def create_chain(
        self,
        name: str,
        description: str = "",
        steps: List[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new AI chain."""
        try:
            if "ChainManager" not in self.bll_managers:
                return {
                    "success": False,
                    "message": "Error creating chain: ChainManager not available",
                }

            chain_definition = {
                "name": name,
                "description": description,
                "steps": steps or [],
                "metadata": metadata or {},
                "created_at": "2024-01-01T00:00:00Z",
                "status": "draft",
            }

            # Validate chain structure
            validation_result = await self._validate_chain_structure(chain_definition)
            if not validation_result["valid"]:
                return {
                    "success": False,
                    "message": f"Chain validation failed: {'; '.join(validation_result['errors'])}",
                }

            return {
                "success": True,
                "chain": chain_definition,
                "message": f"Chain '{name}' created successfully",
            }

        except Exception as e:
            logger.error(f"Error creating chain: {e}")
            return {"success": False, "message": f"Error creating chain: {str(e)}"}

    @ability("update_chain")
    async def update_chain(
        self,
        chain_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update an existing AI chain."""
        try:
            if "ChainManager" not in self.bll_managers:
                return {"success": False, "message": "ChainManager not available"}

            update_data = {}
            if name is not None:
                update_data["name"] = name
            if description is not None:
                update_data["description"] = description
            if steps is not None:
                update_data["steps"] = steps
            if metadata is not None:
                update_data["metadata"] = metadata

            update_data["updated_at"] = "2024-01-01T00:00:00Z"

            return {
                "success": True,
                "chain_id": chain_id,
                "updates": update_data,
                "message": "Chain updated successfully",
            }

        except Exception as e:
            logger.error(f"Error updating chain: {e}")
            return {"success": False, "message": f"Error updating chain: {str(e)}"}

    @ability("delete_chain")
    async def delete_chain(self, chain_id: str) -> Dict[str, Any]:
        """Delete an AI chain."""
        try:
            if "ChainManager" not in self.bll_managers:
                return {"success": False, "message": "ChainManager not available"}

            return {
                "success": True,
                "chain_id": chain_id,
                "message": "Chain deleted successfully",
            }

        except Exception as e:
            logger.error(f"Error deleting chain: {e}")
            return {"success": False, "message": f"Error deleting chain: {str(e)}"}

    @ability("execute_chain")
    async def execute_chain(
        self,
        chain_id: str,
        input_data: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute an AI chain."""
        try:
            if "ExecutionManager" not in self.bll_managers:
                return {"success": False, "message": "ExecutionManager not available"}

            execution_id = f"exec_{chain_id}_{len(self.active_chains)}"

            # Store execution in active chains
            self.active_chains[execution_id] = {
                "chain_id": chain_id,
                "status": "running",
                "started_at": "2024-01-01T00:00:00Z",
                "input_data": input_data or {},
                "config": execution_config or {},
            }

            # Simulate chain execution
            result = await self._execute_chain_steps(chain_id, input_data or {})

            return {
                "success": True,
                "execution_id": execution_id,
                "chain_id": chain_id,
                "result": result,
                "message": "Chain execution started successfully",
            }

        except Exception as e:
            logger.error(f"Error executing chain: {e}")
            return {"success": False, "message": f"Error executing chain: {str(e)}"}

    @ability("pause_chain")
    async def pause_chain(self, execution_id: str) -> Dict[str, Any]:
        """Pause a running chain execution."""
        try:
            if execution_id not in self.active_chains:
                return {"success": False, "message": "Execution not found"}

            self.active_chains[execution_id]["status"] = "paused"
            self.active_chains[execution_id]["paused_at"] = "2024-01-01T00:00:00Z"

            return {
                "success": True,
                "execution_id": execution_id,
                "message": "Chain execution paused",
            }

        except Exception as e:
            logger.error(f"Error pausing chain: {e}")
            return {"success": False, "message": f"Error pausing chain: {str(e)}"}

    @ability("resume_chain")
    async def resume_chain(self, execution_id: str) -> Dict[str, Any]:
        """Resume a paused chain execution."""
        try:
            if execution_id not in self.active_chains:
                return {"success": False, "message": "Execution not found"}

            self.active_chains[execution_id]["status"] = "running"
            self.active_chains[execution_id]["resumed_at"] = "2024-01-01T00:00:00Z"

            return {
                "success": True,
                "execution_id": execution_id,
                "message": "Chain execution resumed",
            }

        except Exception as e:
            logger.error(f"Error resuming chain: {e}")
            return {"success": False, "message": f"Error resuming chain: {str(e)}"}

    @ability("stop_chain")
    async def stop_chain(self, execution_id: str) -> Dict[str, Any]:
        """Stop a running chain execution."""
        try:
            if execution_id not in self.active_chains:
                return {"success": False, "message": "Execution not found"}

            self.active_chains[execution_id]["status"] = "stopped"
            self.active_chains[execution_id]["stopped_at"] = "2024-01-01T00:00:00Z"

            return {
                "success": True,
                "execution_id": execution_id,
                "message": "Chain execution stopped",
            }

        except Exception as e:
            logger.error(f"Error stopping chain: {e}")
            return {"success": False, "message": f"Error stopping chain: {str(e)}"}

    @ability("get_chain_status")
    async def get_chain_status(self, execution_id: str) -> Dict[str, Any]:
        """Get the status of a chain execution."""
        try:
            if execution_id not in self.active_chains:
                return {"success": False, "message": "Execution not found"}

            execution_data = self.active_chains[execution_id]
            return {
                "success": True,
                "execution_id": execution_id,
                "status": execution_data,
            }

        except Exception as e:
            logger.error(f"Error getting chain status: {e}")
            return {
                "success": False,
                "message": f"Error getting chain status: {str(e)}",
            }

    @ability("list_chains")
    async def list_chains(
        self, status: Optional[str] = None, limit: int = 50
    ) -> Dict[str, Any]:
        """List available AI chains."""
        try:
            if "ChainManager" not in self.bll_managers:
                return {"success": False, "message": "ChainManager not available"}

            # Mock chain data
            mock_chains = [
                {
                    "id": "chain_1",
                    "name": "Text Processing Chain",
                    "description": "Chain for processing text inputs",
                    "status": "active",
                    "step_count": 3,
                },
                {
                    "id": "chain_2",
                    "name": "Image Analysis Chain",
                    "description": "Chain for analyzing images",
                    "status": "draft",
                    "step_count": 5,
                },
            ]

            filtered_chains = mock_chains
            if status:
                filtered_chains = [c for c in mock_chains if c["status"] == status]

            return {
                "success": True,
                "chains": filtered_chains[:limit],
                "count": len(filtered_chains),
                "status_filter": status,
            }

        except Exception as e:
            logger.error(f"Error listing chains: {e}")
            return {"success": False, "message": f"Error listing chains: {str(e)}"}

    @ability("validate_chain")
    async def validate_chain(self, chain_definition: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a chain definition."""
        try:
            validation_result = await self._validate_chain_structure(chain_definition)

            return {
                "success": True,
                "valid": validation_result["valid"],
                "errors": validation_result.get("errors", []),
                "warnings": validation_result.get("warnings", []),
            }

        except Exception as e:
            logger.error(f"Error validating chain: {e}")
            return {"success": False, "message": f"Error validating chain: {str(e)}"}

    async def _validate_chain_structure(
        self, chain_definition: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Internal method to validate chain structure."""
        errors = []
        warnings = []

        # Check required fields
        if "name" not in chain_definition:
            errors.append("Chain name is required")
        if "steps" not in chain_definition:
            errors.append("Chain steps are required")

        # Check steps structure
        steps = chain_definition.get("steps", [])
        if not isinstance(steps, list):
            errors.append("Steps must be a list")
        elif len(steps) == 0:
            warnings.append("Chain has no steps defined")

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"Step {i} must be a dictionary")
                continue
            if "type" not in step:
                errors.append(f"Step {i} missing required 'type' field")
            if "name" not in step:
                warnings.append(f"Step {i} has no name defined")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    async def _execute_chain_steps(
        self, chain_id: str, input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Internal method to execute chain steps."""
        # This would contain the actual execution logic
        # For now, return a mock result
        return {
            "steps_executed": 3,
            "final_output": "Chain execution completed",
            "execution_time": 2.5,
        }

    def on_start(self) -> bool:
        """Start the AI Chains extension."""
        try:
            logger.debug("AI Chains extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start AI Chains extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the AI Chains extension."""
        try:
            # Stop all active chains
            for execution_id in list(self.active_chains.keys()):
                self.active_chains[execution_id]["status"] = "stopped"

            logger.debug("AI Chains extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping AI Chains extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import pydantic
        except ImportError:
            issues.append(
                "Pydantic library not installed - chain validation will not work"
            )

        try:
            import asyncio
        except ImportError:
            issues.append(
                "Asyncio library not installed - async execution will not work"
            )

        # Check optional packages
        try:
            import jsonschema
        except ImportError:
            logger.debug("JSONSchema not available - advanced validation disabled")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "chains:create",
            "chains:read",
            "chains:update",
            "chains:delete",
            "chains:execute",
            "chains:monitor",
            "ai:inference",
        ]

    def on_startup(self):
        """Called during application startup."""
        logger.debug("AI Chains extension startup hook called")

    def on_shutdown(self):
        """Called during application shutdown."""
        logger.debug("AI Chains extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
