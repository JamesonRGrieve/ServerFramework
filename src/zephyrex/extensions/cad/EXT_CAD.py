import subprocess
from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency, SYS_Dependency
from zephyrex.lib.Logging import logger


class EXT_CAD(AbstractStaticExtension):
    """
    CAD / 3D modelling extension for AGInfrastructure.

    Provides 3D model generation using various CAD tools including OpenSCAD
    and Blender, currently supporting parametric model creation, export,
    and rendering, with room for additional modelling tools.

    Component loading (DB, BLL, EP) is handled automatically by the import
    system based on file naming conventions.
    """

    # Extension metadata
    name = "3d_modelling"
    version = "1.0.0"
    description = "3D model generation using various CAD tools"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base 3D modelling functionality",
        ),
        EXT_Dependency(
            name="commands",
            friendly_name="Commands Extension",
            optional=False,
            reason="Required for command execution functionality",
        ),
        EXT_Dependency(
            name="labels",
            friendly_name="Labels Extension",
            optional=True,
            reason="Optional for labeling and organizing 3D models",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="pyvirtualdisplay",
            friendly_name="PyVirtualDisplay",
            optional=True,
            reason="Required for headless rendering in virtual environments",
            semver=">=3.0",
        ),
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            reason="Required for API communications",
            semver=">=2.28.0",
        ),
    ]

    sys_dependencies = [
        SYS_Dependency(
            name="openscad",
            friendly_name="OpenSCAD",
            optional=True,
            reason="Required for OpenSCAD 3D modelling functionality",
        ),
        SYS_Dependency(
            name="blender",
            friendly_name="Blender",
            optional=True,
            reason="Required for Blender 3D modelling functionality",
        ),
    ]

    # Define database tables (none for this extension)
    db_tables: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "3d_model_generation",
        "cad_design",
        "parametric_modeling",
        "mesh_generation",
        "model_export",
        "model_rendering",
    ]

    def __init__(
        self,
        modeling_tool: str = "openscad",
        working_directory: str = "",
        output_url: str = "",
        blender_executable: str = "blender",
        **kwargs: Any,
    ):
        """
        Initialize the CAD extension.
        """
        super().__init__(**kwargs)

        self.modeling_tool = modeling_tool.lower()
        self.working_directory = working_directory
        self.output_url = output_url
        self.blender_executable = blender_executable
        self.provider = None
        self.commands: Dict[str, Any] = {}

        # Give each instance its own copy of the mutable class-level
        # capability list so register_capability() on one instance can never
        # leak into the class default (and therefore into sibling instances).
        self.capabilities = list(type(self).capabilities)

    def on_initialize(self) -> bool:
        """Initialize the CAD extension with the appropriate provider."""
        logger.debug("Initializing CAD Extension...")

        try:
            self._create_provider()
            self._register_commands()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("CAD extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize CAD extension: {str(e)}")
            return False

    def _create_provider(self) -> None:
        """Create the appropriate 3D modelling provider based on modeling_tool."""
        try:
            if self.modeling_tool == "openscad":
                from zephyrex.extensions.cad.PRV_OpenSCAD import OpenSCADProvider

                self.provider = OpenSCADProvider(
                    working_directory=self.working_directory,
                    output_url=self.output_url,
                    extension_id=self.name,
                    **getattr(self, "settings", {}),
                )
                logger.debug("OpenSCAD provider created successfully")

            elif self.modeling_tool == "blender":
                from zephyrex.extensions.cad.PRV_Blender import BlenderProvider

                self.provider = BlenderProvider(
                    working_directory=self.working_directory,
                    output_url=self.output_url,
                    blender_executable=self.blender_executable,
                    extension_id=self.name,
                    **getattr(self, "settings", {}),
                )
                logger.debug("Blender provider created successfully")

            else:
                logger.error(f"Unsupported 3D modeling tool: {self.modeling_tool}")
                self.provider = None

        except ImportError as e:
            logger.warning(
                f"Could not import 3D modelling provider for {self.modeling_tool}: {e}"
            )
            self.provider = None
        except Exception as e:
            logger.error(f"Error creating 3D modelling provider: {str(e)}")
            self.provider = None

    def _register_commands(self) -> None:
        """Register commands based on available provider."""
        if self.provider and hasattr(self.provider, "commands"):
            self.commands = self.provider.commands
        else:
            # Provide placeholder commands that warn about missing provider
            tool_name = self.modeling_tool.upper()
            self.commands = {
                f"Create 3D Model with {tool_name}": self._no_provider_warning,
                f"Generate {tool_name} Parametric Model": self._no_provider_warning,
                f"Export {tool_name} Model": self._no_provider_warning,
            }

    async def _no_provider_warning(self, *args: Any, **kwargs: Any) -> str:
        """Warning message when a provider is not available."""
        return f"No 3D modelling provider available for {self.modeling_tool}. Please check your configuration."

    def register_capability(self, capability: str) -> None:
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @ability("create_model")
    async def create_model(self, description: str) -> Dict[str, Any]:
        """Create a 3D model based on a description."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.create_model(description)
            return {"success": True, "result": result, "description": description}

        except Exception as e:
            logger.error(f"Error creating 3D model: {e}")
            return {"success": False, "message": f"Error creating 3D model: {str(e)}"}

    @ability("generate_parametric_model")
    async def generate_parametric_model(
        self, parameters: Dict[str, Any], template: str = ""
    ) -> Dict[str, Any]:
        """Generate a parametric 3D model with specified parameters."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.generate_parametric_model(parameters, template)
            return {
                "success": True,
                "result": result,
                "parameters": parameters,
                "template": template,
            }

        except Exception as e:
            logger.error(f"Error generating parametric model: {e}")
            return {
                "success": False,
                "message": f"Error generating parametric model: {str(e)}",
            }

    @ability("export_model")
    async def export_model(
        self,
        model_id: str,
        format: str = "stl",
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Export a 3D model to specified format."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.export_model(model_id, format, options or {})
            return {
                "success": True,
                "result": result,
                "model_id": model_id,
                "format": format,
            }

        except Exception as e:
            logger.error(f"Error exporting model: {e}")
            return {"success": False, "message": f"Error exporting model: {str(e)}"}

    @ability("render_model")
    async def render_model(
        self, model_id: str, render_options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Render a 3D model with specified options."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.render_model(model_id, render_options or {})
            return {
                "success": True,
                "result": result,
                "model_id": model_id,
                "render_options": render_options,
            }

        except Exception as e:
            logger.error(f"Error rendering model: {e}")
            return {"success": False, "message": f"Error rendering model: {str(e)}"}

    def on_start(self) -> bool:
        """Start the CAD extension."""
        try:
            logger.debug("CAD extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start CAD extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the CAD extension."""
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("CAD extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping CAD extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import requests  # noqa: F401
        except ImportError:
            issues.append(
                "Requests library not installed - API communications will not work"
            )

        # Tool-specific validation
        if not self.modeling_tool:
            issues.append("3D modeling tool not specified")
        elif self.modeling_tool not in ["openscad", "blender"]:
            issues.append(f"Unsupported 3D modeling tool: {self.modeling_tool}")

        # Check tool-specific requirements
        if self.modeling_tool == "openscad":
            try:
                subprocess.run(
                    ["openscad", "--version"], capture_output=True, timeout=10
                )
            except (
                subprocess.CalledProcessError,
                FileNotFoundError,
                subprocess.TimeoutExpired,
            ):
                issues.append("OpenSCAD not found - OpenSCAD modeling will not work")

        elif self.modeling_tool == "blender":
            try:
                subprocess.run(
                    [self.blender_executable, "--version"],
                    capture_output=True,
                    timeout=10,
                )
            except (
                subprocess.CalledProcessError,
                FileNotFoundError,
                subprocess.TimeoutExpired,
            ):
                issues.append("Blender not found - Blender modeling will not work")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "3dmodeling:create",
            "3dmodeling:export",
            "3dmodeling:render",
            "files:read",
            "files:write",
        ]

    def on_startup(self) -> None:
        """Called during application startup."""
        logger.debug("3D Modelling extension startup hook called")

    def on_shutdown(self) -> None:
        """Called during application shutdown."""
        logger.debug("3D Modelling extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if the extension has a specific capability."""
        return capability in self.capabilities
