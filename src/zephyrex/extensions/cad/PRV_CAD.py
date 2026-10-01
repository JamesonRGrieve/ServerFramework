from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AbstractCADProvider(ABC):
    """
    Abstract base class for all 3D-modelling/CAD providers used by the CAD
    extension (currently OpenSCAD and Blender, with room for additional
    modelling tools).

    Concrete providers are lightweight, directly-instantiated clients that
    hold their own config (working directory, output URL, and an optional
    AI client used for natural-language-to-code generation) and expose
    synchronous model creation/export/render operations. EXT_CAD's async
    abilities call into these methods directly (no await) so a provider's
    return values are plain strings rather than awaitables.
    """

    def __init__(
        self,
        working_directory: str = "",
        output_url: str = "",
        extension_id: Optional[str] = None,
        agent_name: str = "",
        ApiClient: Any = None,
        conversation_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.working_directory = working_directory or "."
        self.output_url = output_url
        self.extension_id = extension_id
        self.agent_name = agent_name
        self.ApiClient = ApiClient
        self.conversation_name = conversation_name
        self.settings: Dict[str, Any] = kwargs

        tool = self.get_modeling_tool()
        self.commands = {
            f"Create 3D Model with {tool}": self.create_model,
            f"Generate {tool} Parametric Model": self.generate_parametric_model,
            f"Export {tool} Model": self.export_model,
            f"Render {tool} Model": self.render_model,
        }

    @abstractmethod
    def get_modeling_tool(self) -> str:
        """Get the name of the modelling tool this provider interacts with."""

    @abstractmethod
    def create_model(self, description: str) -> str:
        """Create a 3D model from a natural-language description."""

    @abstractmethod
    def generate_parametric_model(
        self, parameters: Dict[str, Any], template: str = ""
    ) -> str:
        """Generate a parametric 3D model from a template and parameters."""

    @abstractmethod
    def export_model(
        self,
        model_id: str,
        format: str = "stl",
        options: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Export a previously created model to the requested file format."""

    @abstractmethod
    def render_model(
        self, model_id: str, render_options: Optional[Dict[str, Any]] = None
    ) -> str:
        """Render a preview image of a previously created model."""

    @staticmethod
    def services() -> List[str]:
        """Return a list of services provided by this provider."""
        return ["3d_modeling", "cad"]

    def get_extension_info(self) -> Dict[str, Any]:
        """Get information about the CAD extension."""
        return {
            "name": "CAD",
            "description": f"3D modelling extension using {self.get_modeling_tool()}",
            "type": self.get_modeling_tool(),
        }
