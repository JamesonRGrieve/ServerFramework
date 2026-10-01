"""Blender-backed CAD provider.

Blender's Python API (``bpy``) only exists inside Blender's own embedded
interpreter — it cannot be pip-installed standalone. This provider never
imports ``bpy`` in-process; instead it writes short Python scripts that
reference ``bpy`` as *text* and hands them to the external ``blender``
binary via ``subprocess`` (``blender --background --python <script>``),
which executes them inside Blender's own interpreter. No import guard is
required here because this module never executes ``import bpy`` itself.
"""

import logging
import os
import subprocess
import tempfile
from datetime import datetime
from typing import Any, Dict, Optional

from zephyrex.extensions.cad.PRV_CAD import AbstractCADProvider

logger = logging.getLogger(__name__)

BLENDER_SUBPROCESS_TIMEOUT = 180  # seconds
DEFAULT_CUBE_TEMPLATE = "import bpy\nbpy.ops.mesh.primitive_cube_add(size={size})\n"


class BlenderProvider(AbstractCADProvider):
    """
    CAD provider backed by Blender's headless command-line interface.

    A lightweight, directly-instantiated client: it holds the working
    directory, output URL, and blender executable path, and shells out
    synchronously to ``blender --background --python <script>`` to
    validate, preview, and export models. Created models are tracked
    in-memory (model id -> ``.blend`` file path) so a later
    ``export_model``/``render_model`` call can find the source file.
    """

    def __init__(
        self,
        working_directory: str = "",
        output_url: str = "",
        blender_executable: str = "blender",
        **kwargs: Any,
    ) -> None:
        self._models: Dict[str, str] = {}
        self.blender_executable = blender_executable
        super().__init__(
            working_directory=working_directory, output_url=output_url, **kwargs
        )
        self.WORKING_DIRECTORY = self.working_directory or os.path.join(
            os.getcwd(), "WORKSPACE"
        )
        os.makedirs(self.WORKING_DIRECTORY, exist_ok=True)

    def get_modeling_tool(self) -> str:
        return "Blender"

    def _validate_model_code(self, code: str) -> bool:
        try:
            compile(code, "<string>", "exec")
            return True
        except SyntaxError as e:
            logger.warning("Blender Python code syntax error: %s", str(e))
            return False

    def _run_blender_script(
        self, script: str, blend_file: Optional[str] = None
    ) -> None:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        try:
            tmp.write(script)
            tmp.close()

            args = [self.blender_executable]
            if blend_file:
                args.append(blend_file)
            args += ["--background", "--python", tmp.name]

            subprocess.run(
                args,
                check=True,
                capture_output=True,
                timeout=BLENDER_SUBPROCESS_TIMEOUT,
            )
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def _extract_code(self, code: str) -> str:
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0]
        elif "```" in code:
            code = code.split("```")[1].split("```")[0]
        return code.strip()

    def _save_model(self, code: str) -> str:
        code = self._extract_code(code)

        model_id = f"model_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        blend_path = os.path.join(self.WORKING_DIRECTORY, f"{model_id}.blend")
        save_script = f"{code}\n\nimport bpy\nbpy.ops.wm.save_as_mainfile(filepath={blend_path!r})\n"

        try:
            self._run_blender_script(save_script)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            logger.error("Error generating Blender file: %s", stderr)
            raise RuntimeError(f"Error generating Blender file: {stderr}") from e

        if not (os.path.exists(blend_path) and os.path.getsize(blend_path) > 0):
            raise RuntimeError("Blender file was not generated or is empty")

        self._models[model_id] = blend_path
        return model_id

    def _generate_code(self, description: str) -> str:
        if not self.ApiClient:
            logger.warning(
                "No AI client configured; falling back to a placeholder Blender model."
            )
            return (
                f"# Placeholder model for: {description}\n"
                "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2)\n"
            )

        return self.ApiClient.prompt_agent(
            agent_name=self.agent_name,
            prompt_name="Think About It",
            prompt_args={
                "user_input": (
                    f"{description}\n\nThe assistant is an expert Blender Python "
                    "programmer. Produce complete, executable Blender Python code "
                    "that creates the described model, applies materials, and sets "
                    "up a render-ready camera and lighting. Respond with only the "
                    "code, inside a ```python code block."
                ),
                "conversation_name": self.conversation_name,
            },
        )

    def create_model(self, description: str) -> str:
        code = self._generate_code(description)
        if not self._validate_model_code(code):
            logger.info("Generated Blender code failed validation for: %s", description)
            code = f"# WARNING: generated code failed syntax validation\n{code}"
        return self._save_model(code)

    def generate_parametric_model(
        self, parameters: Dict[str, Any], template: str = ""
    ) -> str:
        code = template or DEFAULT_CUBE_TEMPLATE
        try:
            code = code.format(**parameters)
        except (KeyError, IndexError) as e:
            logger.warning("Could not apply parameters to Blender template: %s", str(e))
        return self._save_model(code)

    def export_model(
        self,
        model_id: str,
        format: str = "obj",
        options: Optional[Dict[str, Any]] = None,
    ) -> str:
        blend_file = self._models.get(model_id)
        if not blend_file:
            raise ValueError(f"Unknown Blender model id: {model_id}")
        if format != "obj":
            raise NotImplementedError(f"Blender export format not supported: {format}")

        output_path = os.path.join(self.WORKING_DIRECTORY, f"{model_id}.obj")
        export_script = f"import bpy\nbpy.ops.wm.obj_export(filepath={output_path!r})\n"

        try:
            self._run_blender_script(export_script, blend_file=blend_file)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            logger.error("Error exporting Blender model: %s", stderr)
            raise RuntimeError(f"Error exporting model: {stderr}") from e

        if not (os.path.exists(output_path) and os.path.getsize(output_path) > 0):
            raise RuntimeError("OBJ file was not generated or is empty")

        return self._to_url(output_path)

    def render_model(
        self, model_id: str, render_options: Optional[Dict[str, Any]] = None
    ) -> str:
        blend_file = self._models.get(model_id)
        if not blend_file:
            raise ValueError(f"Unknown Blender model id: {model_id}")

        options = render_options or {}
        output_path = os.path.join(self.WORKING_DIRECTORY, f"{model_id}.png")
        render_script = f"""
import bpy

bpy.context.scene.render.filepath = {output_path!r}
bpy.context.scene.render.resolution_x = {int(options.get("width", 1920))}
bpy.context.scene.render.resolution_y = {int(options.get("height", 1080))}
bpy.context.scene.render.image_settings.file_format = 'PNG'

if not any(obj.type == 'CAMERA' for obj in bpy.context.scene.objects):
    bpy.ops.object.camera_add(location=(0, -10, 0), rotation=(1.5708, 0, 0))
    bpy.context.scene.camera = bpy.context.object

if not any(obj.type == 'LIGHT' for obj in bpy.context.scene.objects):
    bpy.ops.object.light_add(type='SUN', location=(5, 5, 10))

bpy.ops.render.render(write_still=True)
"""

        try:
            self._run_blender_script(render_script, blend_file=blend_file)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            logger.error("Error rendering Blender preview: %s", stderr)
            raise RuntimeError(f"Error rendering model: {stderr}") from e

        if not (os.path.exists(output_path) and os.path.getsize(output_path) > 0):
            raise RuntimeError("Preview file was not generated or is empty")

        return self._to_url(output_path)

    def _to_url(self, path: str) -> str:
        return (
            f"{self.output_url}/{os.path.basename(path)}" if self.output_url else path
        )
