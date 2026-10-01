from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.cad.EXT_CAD import EXT_CAD


class TestCADExtension:
    """
    Test suite for the CAD extension.

    Tests extension metadata/configuration, OpenSCAD/Blender provider
    creation and integration, 3D modelling abilities (create, parametric
    generation, export, render), capability management, and extension
    lifecycle/config validation.
    """

    @pytest.fixture
    def extension(self):
        """Create an EXT_CAD instance for testing."""
        return EXT_CAD()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "3d_modelling"
        assert extension.version == "1.0.0"
        assert "3d model" in extension.description.lower()
        assert "cad" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps
        assert "commands" in ext_deps
        assert "labels" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "pyvirtualdisplay" in pip_deps
        assert "requests" in pip_deps

        # Check sys dependencies
        sys_deps = {dep.name for dep in extension.sys_dependencies}
        assert "openscad" in sys_deps
        assert "blender" in sys_deps

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "3d_model_generation",
            "cad_design",
            "parametric_modeling",
            "mesh_generation",
            "model_export",
            "model_rendering",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "modeling_tool")
        assert hasattr(extension, "working_directory")
        assert hasattr(extension, "output_url")
        assert hasattr(extension, "blender_executable")
        assert hasattr(extension, "provider")
        assert hasattr(extension, "commands")
        assert extension.provider is None

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.modeling_tool == "openscad"
        assert extension.working_directory == ""
        assert extension.output_url == ""
        assert extension.blender_executable == "blender"

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0

    @patch("zephyrex.extensions.cad.EXT_CAD.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.cad.EXT_CAD.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_openscad(self, extension):
        """Test OpenSCAD provider creation."""
        extension.modeling_tool = "openscad"

        with patch(
            "zephyrex.extensions.cad.PRV_OpenSCAD.OpenSCADProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_blender(self, extension):
        """Test Blender provider creation."""
        extension.modeling_tool = "blender"

        with patch(
            "zephyrex.extensions.cad.PRV_Blender.BlenderProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_unsupported_tool(self, extension):
        """Test provider creation with unsupported tool."""
        extension.modeling_tool = "unsupported_tool"

        with patch("zephyrex.extensions.cad.EXT_CAD.logger") as mock_logger:
            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called_with(
                "Unsupported 3D modeling tool: unsupported_tool"
            )

    def test_create_provider_import_error(self, extension):
        """Test provider creation with import error."""
        extension.modeling_tool = "openscad"

        with patch(
            "zephyrex.extensions.cad.PRV_OpenSCAD.OpenSCADProvider",
            side_effect=ImportError("Module not found"),
        ), patch("zephyrex.extensions.cad.EXT_CAD.logger") as mock_logger:

            extension._create_provider()

            assert extension.provider is None
            mock_logger.warning.assert_called()

    def test_register_commands_with_provider(self, extension):
        """Test command registration when provider is available."""
        mock_provider = MagicMock()
        mock_provider.commands = {"test_command": MagicMock()}
        extension.provider = mock_provider

        extension._register_commands()

        assert extension.commands == mock_provider.commands

    def test_register_commands_without_provider(self, extension):
        """Test command registration when provider is not available."""
        extension.provider = None
        extension.modeling_tool = "openscad"

        extension._register_commands()

        assert len(extension.commands) == 3
        assert "Create 3D Model with OPENSCAD" in extension.commands
        assert "Generate OPENSCAD Parametric Model" in extension.commands
        assert "Export OPENSCAD Model" in extension.commands

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test warning message when no provider is available."""
        extension.modeling_tool = "openscad"

        result = await extension._no_provider_warning()

        assert "No 3D modelling provider available for openscad" in result

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

    def test_capability_registration_is_instance_scoped(self):
        """Registering a capability on one instance must not leak to another."""
        first = EXT_CAD()
        second = EXT_CAD()

        first.register_capability("instance_only_capability")

        assert "instance_only_capability" in first.capabilities
        assert "instance_only_capability" not in second.capabilities

    @pytest.mark.asyncio
    async def test_create_model_success(self, extension):
        """Test successful model creation."""
        mock_provider = MagicMock()
        mock_provider.create_model = MagicMock(
            return_value="model_created_successfully"
        )
        extension.provider = mock_provider

        result = await extension.create_model("Create a cube")

        assert result["success"] is True
        assert result["result"] == "model_created_successfully"
        assert result["description"] == "Create a cube"
        mock_provider.create_model.assert_called_once_with("Create a cube")

    @pytest.mark.asyncio
    async def test_create_model_no_provider(self, extension):
        """Test model creation without provider."""
        extension.provider = None

        result = await extension.create_model("Create a cube")

        assert result["success"] is False
        assert "No 3D modelling provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_generate_parametric_model_success(self, extension):
        """Test successful parametric model generation."""
        mock_provider = MagicMock()
        mock_provider.generate_parametric_model = MagicMock(
            return_value="parametric_model_created"
        )
        extension.provider = mock_provider

        parameters = {"width": 10, "height": 5, "depth": 3}
        template = "cube_template"

        result = await extension.generate_parametric_model(parameters, template)

        assert result["success"] is True
        assert result["result"] == "parametric_model_created"
        assert result["parameters"] == parameters
        assert result["template"] == template
        mock_provider.generate_parametric_model.assert_called_once_with(
            parameters, template
        )

    @pytest.mark.asyncio
    async def test_generate_parametric_model_no_provider(self, extension):
        """Test parametric model generation without provider."""
        extension.provider = None

        result = await extension.generate_parametric_model({"width": 10})

        assert result["success"] is False
        assert "No 3D modelling provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_export_model_success(self, extension):
        """Test successful model export."""
        mock_provider = MagicMock()
        mock_provider.export_model = MagicMock(return_value="model_exported")
        extension.provider = mock_provider

        result = await extension.export_model("model_123", "stl", {"quality": "high"})

        assert result["success"] is True
        assert result["result"] == "model_exported"
        assert result["model_id"] == "model_123"
        assert result["format"] == "stl"
        mock_provider.export_model.assert_called_once_with(
            "model_123", "stl", {"quality": "high"}
        )

    @pytest.mark.asyncio
    async def test_export_model_no_provider(self, extension):
        """Test model export without provider."""
        extension.provider = None

        result = await extension.export_model("model_123", "stl")

        assert result["success"] is False
        assert "No 3D modelling provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_render_model_success(self, extension):
        """Test successful model rendering."""
        mock_provider = MagicMock()
        mock_provider.render_model = MagicMock(return_value="model_rendered")
        extension.provider = mock_provider

        render_options = {"camera_angle": 45, "lighting": "bright"}

        result = await extension.render_model("model_123", render_options)

        assert result["success"] is True
        assert result["result"] == "model_rendered"
        assert result["model_id"] == "model_123"
        assert result["render_options"] == render_options
        mock_provider.render_model.assert_called_once_with("model_123", render_options)

    @pytest.mark.asyncio
    async def test_render_model_no_provider(self, extension):
        """Test model rendering without provider."""
        extension.provider = None

        result = await extension.render_model("model_123")

        assert result["success"] is False
        assert "No 3D modelling provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_ability_error_handling(self, extension):
        """Test error handling in abilities."""
        mock_provider = MagicMock()
        mock_provider.create_model = MagicMock(side_effect=Exception("Provider error"))
        extension.provider = mock_provider

        result = await extension.create_model("Create a cube")

        assert result["success"] is False
        assert "Error creating 3D model" in result["message"]

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        # Test on_start
        assert extension.on_start() is True

        # Test on_stop
        extension.provider = MagicMock()
        assert extension.on_stop() is True
        assert extension.provider is None

        # Test on_startup and on_shutdown
        extension.on_startup()  # Should not raise exception
        extension.on_shutdown()  # Should not raise exception

    def test_validate_config_success(self, extension):
        """Test successful configuration validation."""
        with patch("builtins.__import__"):
            issues = extension.validate_config()
            assert isinstance(issues, list)

    def test_validate_config_missing_requests(self, extension):
        """Test configuration validation with missing requests library."""

        def mock_import(name, *args, **kwargs):
            if name == "requests":
                raise ImportError("No module named 'requests'")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            issues = extension.validate_config()

            assert len(issues) > 0
            assert any("Requests library not installed" in issue for issue in issues)

    def test_validate_config_no_modeling_tool(self, extension):
        """Test configuration validation with no modeling tool."""
        extension.modeling_tool = ""

        issues = extension.validate_config()

        assert any("3D modeling tool not specified" in issue for issue in issues)

    def test_validate_config_unsupported_tool(self, extension):
        """Test configuration validation with unsupported tool."""
        extension.modeling_tool = "unsupported"

        issues = extension.validate_config()

        assert any(
            "Unsupported 3D modeling tool: unsupported" in issue for issue in issues
        )

    @patch("subprocess.run")
    def test_validate_config_openscad_missing(self, mock_run, extension):
        """Test configuration validation when OpenSCAD is missing."""
        extension.modeling_tool = "openscad"
        mock_run.side_effect = FileNotFoundError("Command not found")

        issues = extension.validate_config()

        assert any("OpenSCAD not found" in issue for issue in issues)

    @patch("subprocess.run")
    def test_validate_config_blender_missing(self, mock_run, extension):
        """Test configuration validation when Blender is missing."""
        extension.modeling_tool = "blender"
        mock_run.side_effect = FileNotFoundError("Command not found")

        issues = extension.validate_config()

        assert any("Blender not found" in issue for issue in issues)

    @patch("subprocess.run")
    def test_validate_config_tool_available(self, mock_run, extension):
        """Test configuration validation when tool is available."""
        extension.modeling_tool = "openscad"
        mock_run.return_value = MagicMock(returncode=0)

        issues = extension.validate_config()

        # Should not have tool-related issues
        assert not any("OpenSCAD not found" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "3dmodeling:create",
            "3dmodeling:export",
            "3dmodeling:render",
            "files:read",
            "files:write",
        ]
        assert set(permissions) == set(expected_permissions)

    def test_custom_configuration(self):
        """Test extension with custom configuration."""
        extension = EXT_CAD(
            modeling_tool="blender",
            working_directory="/custom/path",
            output_url="http://example.com",
            blender_executable="/usr/bin/blender",
        )

        assert extension.modeling_tool == "blender"
        assert extension.working_directory == "/custom/path"
        assert extension.output_url == "http://example.com"
        assert extension.blender_executable == "/usr/bin/blender"

    def test_provider_with_commands(self, extension):
        """Test provider that has commands attribute."""
        mock_provider = MagicMock()
        mock_provider.commands = {
            "create_model": MagicMock(),
            "export_model": MagicMock(),
        }
        extension.provider = mock_provider

        extension._register_commands()

        assert extension.commands == mock_provider.commands

    def test_provider_without_commands(self, extension):
        """Test provider that doesn't have commands attribute."""
        mock_provider = MagicMock()
        del mock_provider.commands  # Remove commands attribute
        extension.provider = mock_provider
        extension.modeling_tool = "blender"

        extension._register_commands()

        # Should fall back to placeholder commands
        assert "Create 3D Model with BLENDER" in extension.commands

    @pytest.mark.asyncio
    async def test_all_abilities_with_different_tools(self, extension):
        """Test all abilities work with different modeling tools."""
        for tool in ["openscad", "blender"]:
            extension.modeling_tool = tool
            extension.provider = None

            # All abilities should return provider not available error
            result = await extension.create_model("test")
            assert result["success"] is False
            assert f"No 3D modelling provider available for {tool}" in result["message"]

            result = await extension.generate_parametric_model({})
            assert result["success"] is False

            result = await extension.export_model("test", "stl")
            assert result["success"] is False

            result = await extension.render_model("test")
            assert result["success"] is False

    def test_provider_settings_passed(self, extension):
        """Test that settings are passed to provider."""
        extension.settings = {"custom_setting": "value"}
        extension.modeling_tool = "openscad"

        with patch(
            "zephyrex.extensions.cad.PRV_OpenSCAD.OpenSCADProvider"
        ) as mock_provider:
            extension._create_provider()

            # Check that settings were passed to provider
            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert "custom_setting" in call_kwargs
            assert call_kwargs["custom_setting"] == "value"

    def test_provider_with_extension_id(self, extension):
        """Test that extension ID is passed to provider."""
        extension.modeling_tool = "openscad"

        with patch(
            "zephyrex.extensions.cad.PRV_OpenSCAD.OpenSCADProvider"
        ) as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["extension_id"] == "3d_modelling"


if __name__ == "__main__":
    pytest.main([__file__])
