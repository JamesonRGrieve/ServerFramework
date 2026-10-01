"""OpenSCAD-backed CAD provider.

Generates, exports, and renders parametric 3D models by shelling out to the
``openscad`` command-line tool. ``pyvirtualdisplay`` is an optional
dependency used only for headless preview rendering; its absence degrades
``render_model`` gracefully instead of failing at import time.
"""

import logging
import os
import subprocess
import tempfile
from datetime import datetime
from typing import Any, Dict, Optional

from zephyrex.extensions.cad.PRV_CAD import AbstractCADProvider

try:
    from pyvirtualdisplay import Display
except ImportError:  # pragma: no cover - optional headless-rendering dependency
    Display = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

OPENSCAD_SUBPROCESS_TIMEOUT = 120  # seconds
DEFAULT_CUBE_TEMPLATE = "cube([{width}, {depth}, {height}]);"


class OpenSCADProvider(AbstractCADProvider):
    """
    CAD provider backed by the OpenSCAD command-line tool.

    A lightweight, directly-instantiated client: it holds the working
    directory and output URL and shells out synchronously to the
    ``openscad`` binary to validate, preview, and export models. Created
    models are tracked in-memory (model id -> ``.scad`` file path) so a
    later ``export_model``/``render_model`` call can find the source file.
    """

    def __init__(
        self,
        working_directory: str = "",
        output_url: str = "",
        **kwargs: Any,
    ) -> None:
        self._models: Dict[str, str] = {}
        super().__init__(
            working_directory=working_directory, output_url=output_url, **kwargs
        )
        self.WORKING_DIRECTORY = self.working_directory or os.path.join(
            os.getcwd(), "WORKSPACE"
        )
        os.makedirs(self.WORKING_DIRECTORY, exist_ok=True)

    def get_modeling_tool(self) -> str:
        return "OpenSCAD"

    def _validate_model_code(self, code: str) -> bool:
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".scad") as tmp_file:
                tmp_file.write(code)
                tmp_file.flush()

                result = subprocess.run(
                    [
                        "openscad",
                        "--export-format=stl",
                        "-o",
                        "/dev/null",
                        tmp_file.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=OPENSCAD_SUBPROCESS_TIMEOUT,
                )
                if result.returncode != 0:
                    logger.warning("OpenSCAD validation failed: %s", result.stderr)
                return result.returncode == 0
        except Exception as e:
            logger.error("Unexpected error validating OpenSCAD code: %s", str(e))
            return False

    def _extract_code(self, code: str) -> str:
        if "```openscad" in code:
            code = code.split("```openscad")[1].split("```")[0]
        elif "```" in code:
            code = code.split("```")[1].split("```")[0]
        return code.strip()

    def _save_model(self, code: str) -> str:
        code = self._extract_code(code)

        model_id = f"model_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        filepath = os.path.join(self.WORKING_DIRECTORY, f"{model_id}.scad")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)

        self._models[model_id] = filepath
        return model_id

    def _generate_code(self, description: str) -> str:
        if not self.ApiClient:
            logger.warning(
                "No AI client configured; falling back to a placeholder OpenSCAD model."
            )
            return f"// Placeholder model for: {description}\ncube([10, 10, 10]);"

        return self.ApiClient.prompt_agent(
            agent_name=self.agent_name,
            prompt_name="Think About It",
            prompt_args={
                "user_input": (
                    f"{description}\n\nThe assistant is an expert OpenSCAD programmer. "
                    "Produce complete, printable, well-commented OpenSCAD code "
                    "using millimeters, with a $fn resolution suitable for the "
                    "described shape. Respond with only the OpenSCAD code, "
                    "inside a ```openscad code block."
                ),
                "conversation_name": self.conversation_name,
            },
        )

    def create_model(self, description: str) -> str:
        code = self._generate_code(description)
        if not self._validate_model_code(code):
            logger.info(
                "Generated OpenSCAD code failed validation for: %s", description
            )
            code = f"// WARNING: generated code failed OpenSCAD validation\n{code}"
        return self._save_model(code)

    def generate_parametric_model(
        self, parameters: Dict[str, Any], template: str = ""
    ) -> str:
        code = template or DEFAULT_CUBE_TEMPLATE
        try:
            code = code.format(**parameters)
        except (KeyError, IndexError) as e:
            logger.warning(
                "Could not apply parameters to OpenSCAD template: %s", str(e)
            )
        return self._save_model(code)

    def export_model(
        self,
        model_id: str,
        format: str = "stl",
        options: Optional[Dict[str, Any]] = None,
    ) -> str:
        scad_file = self._models.get(model_id)
        if not scad_file:
            raise ValueError(f"Unknown OpenSCAD model id: {model_id}")

        export_format = "binstl" if format == "stl" else format
        output_path = os.path.join(self.WORKING_DIRECTORY, f"{model_id}.{format}")

        try:
            subprocess.run(
                [
                    "openscad",
                    f"--export-format={export_format}",
                    "-o",
                    output_path,
                    scad_file,
                ],
                check=True,
                capture_output=True,
                timeout=OPENSCAD_SUBPROCESS_TIMEOUT,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            logger.error("Error exporting OpenSCAD model: %s", stderr)
            raise RuntimeError(f"Error exporting model: {stderr}") from e

        if not (os.path.exists(output_path) and os.path.getsize(output_path) > 0):
            raise RuntimeError(f"{format.upper()} file was not generated or is empty")

        return self._to_url(output_path)

    def render_model(
        self, model_id: str, render_options: Optional[Dict[str, Any]] = None
    ) -> str:
        scad_file = self._models.get(model_id)
        if not scad_file:
            raise ValueError(f"Unknown OpenSCAD model id: {model_id}")

        if Display is None:
            raise RuntimeError(
                "pyvirtualdisplay is not installed; OpenSCAD preview rendering is unavailable."
            )

        options = render_options or {}
        output_path = os.path.join(self.WORKING_DIRECTORY, f"{model_id}.png")

        try:
            with Display(visible=0, size=(800, 600)) as display:
                subprocess.run(
                    [
                        "openscad",
                        "--export-format=png",
                        "--viewall",
                        "--autocenter",
                        f"--colorscheme={options.get('colorscheme', 'Sunset')}",
                        "-o",
                        output_path,
                        scad_file,
                    ],
                    check=True,
                    capture_output=True,
                    env={"DISPLAY": display.new_display_var},
                    timeout=OPENSCAD_SUBPROCESS_TIMEOUT,
                )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            logger.error("Error rendering OpenSCAD preview: %s", stderr)
            raise RuntimeError(f"Error rendering model: {stderr}") from e

        if not (os.path.exists(output_path) and os.path.getsize(output_path) > 0):
            raise RuntimeError("Preview file was not generated or is empty")

        return self._to_url(output_path)

    def _to_url(self, path: str) -> str:
        return (
            f"{self.output_url}/{os.path.basename(path)}" if self.output_url else path
        )
