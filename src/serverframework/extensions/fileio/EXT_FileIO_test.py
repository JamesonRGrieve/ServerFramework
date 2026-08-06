from unittest.mock import MagicMock, patch

import pytest

from serverframework.extensions.fileio.EXT_FileIO import EXT_FileIO


class TestFileIOExtension:
    """Test cases for FileIO Extension."""

    @pytest.fixture
    def extension(self):
        """Create an EXT_FileIO instance for testing."""
        return EXT_FileIO()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "fileio"
        assert extension.version == "1.0.0"
        assert "file i/o" in extension.description.lower()
        assert "local files" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert len(ext_deps) == 0  # FileIO has no extension dependencies

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert len(pip_deps) == 0  # FileIO has no pip dependencies

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)
        assert len(extension.sys_dependencies) == 0

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "file_read",
            "file_write",
            "directory_list",
            "file_system_info",
            "file_operations",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "base_directory")
        assert hasattr(extension, "provider_type")
        assert hasattr(extension, "permissions")
        assert hasattr(extension, "allowlist")
        assert hasattr(extension, "blocklist")
        assert hasattr(extension, "provider")

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.base_directory == "."
        assert extension.provider_type == "local"
        assert extension.allowlist == ["*"]
        assert extension.blocklist == []

    def test_custom_configuration(self):
        """Test extension with custom configuration."""
        extension = EXT_FileIO(
            base_directory="/custom/path",
            allowed_permissions={"READ", "WRITE"},
            allowlist_patterns={"patterns": ["*.txt", "*.py"]},
            blocklist_patterns={"patterns": ["*.exe", "*.bat"]},
            provider_type="local",
        )

        assert extension.base_directory == "/custom/path"
        assert extension.allowlist == ["*.txt", "*.py"]
        assert extension.blocklist == ["*.exe", "*.bat"]

    @patch("extensions.fileio.EXT_FileIO.logging")
    def test_on_initialize_success(self, mock_logging, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "register_capability"
        ):

            result = extension.on_initialize()
            assert result is True
            mock_logging.debug.assert_called()

    @patch("extensions.fileio.EXT_FileIO.logging")
    def test_on_initialize_failure(self, mock_logging, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logging.error.assert_called()

    def test_create_provider_local(self, extension):
        """Test Local provider creation."""
        extension.provider_type = "local"

        with patch(
            "extensions.fileio.LocalFileSystem.LocalFileSystem"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_unsupported_type(self, extension):
        """Test provider creation with unsupported type."""
        extension.provider_type = "unsupported_type"

        with patch("extensions.fileio.EXT_FileIO.logging") as mock_logging:
            extension._create_provider()

            assert extension.provider is None
            mock_logging.error.assert_called_with(
                "Unsupported FileIO provider type: unsupported_type"
            )

    def test_create_provider_import_error(self, extension):
        """Test provider creation with import error."""
        extension.provider_type = "local"

        with patch(
            "extensions.fileio.LocalFileSystem.LocalFileSystem",
            side_effect=ImportError("Module not found"),
        ), patch("extensions.fileio.EXT_FileIO.logging") as mock_logging:

            extension._create_provider()

            assert extension.provider is None
            mock_logging.error.assert_called()

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

    @pytest.mark.asyncio
    async def test_get_file_system_info_success(self, extension):
        """Test successful file system info retrieval."""
        mock_provider = MagicMock()
        mock_provider.get_file_system_info = MagicMock(return_value="file_system_info")
        extension.provider = mock_provider

        result = await extension.get_file_system_info()

        assert result == "file_system_info"
        mock_provider.get_file_system_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_file_system_info_no_provider(self, extension):
        """Test file system info retrieval without provider."""
        extension.provider = None
        extension.provider_type = "local"

        result = await extension.get_file_system_info()

        assert "FileIO provider not configured for local" in result

    @pytest.mark.asyncio
    async def test_get_file_system_info_error(self, extension):
        """Test file system info retrieval with error."""
        mock_provider = MagicMock()
        mock_provider.get_file_system_info = MagicMock(
            side_effect=Exception("Provider error")
        )
        extension.provider = mock_provider

        result = await extension.get_file_system_info()

        assert "Failed to get file system info" in result

    @pytest.mark.asyncio
    async def test_list_directory_success(self, extension):
        """Test successful directory listing."""
        mock_provider = MagicMock()
        mock_provider.list_directory = MagicMock(return_value="directory_contents")
        extension.provider = mock_provider

        result = await extension.list_directory("/test/path")

        assert result == "directory_contents"
        mock_provider.list_directory.assert_called_once_with("/test/path")

    @pytest.mark.asyncio
    async def test_list_directory_no_provider(self, extension):
        """Test directory listing without provider."""
        extension.provider = None
        extension.provider_type = "local"

        result = await extension.list_directory(".")

        assert "FileIO provider not configured for local" in result

    @pytest.mark.asyncio
    async def test_list_directory_error(self, extension):
        """Test directory listing with error."""
        mock_provider = MagicMock()
        mock_provider.list_directory = MagicMock(
            side_effect=Exception("Provider error")
        )
        extension.provider = mock_provider

        result = await extension.list_directory(".")

        assert "Failed to list directory" in result

    @pytest.mark.asyncio
    async def test_read_file_success(self, extension):
        """Test successful file reading."""
        mock_provider = MagicMock()
        mock_provider.read_file = MagicMock(return_value="file_contents")
        extension.provider = mock_provider

        result = await extension.read_file("test.txt")

        assert result == "file_contents"
        mock_provider.read_file.assert_called_once_with("test.txt")

    @pytest.mark.asyncio
    async def test_read_file_no_provider(self, extension):
        """Test file reading without provider."""
        extension.provider = None
        extension.provider_type = "local"

        result = await extension.read_file("test.txt")

        assert "FileIO provider not configured for local" in result

    @pytest.mark.asyncio
    async def test_read_file_error(self, extension):
        """Test file reading with error."""
        mock_provider = MagicMock()
        mock_provider.read_file = MagicMock(side_effect=Exception("Provider error"))
        extension.provider = mock_provider

        result = await extension.read_file("test.txt")

        assert "Failed to read file" in result

    @pytest.mark.asyncio
    async def test_write_file_success(self, extension):
        """Test successful file writing."""
        mock_provider = MagicMock()
        mock_provider.write_file = MagicMock(return_value="file_written")
        extension.provider = mock_provider

        result = await extension.write_file("test.txt", "test content")

        assert result == "file_written"
        mock_provider.write_file.assert_called_once_with("test.txt", "test content")

    @pytest.mark.asyncio
    async def test_write_file_no_provider(self, extension):
        """Test file writing without provider."""
        extension.provider = None
        extension.provider_type = "local"

        result = await extension.write_file("test.txt", "content")

        assert "FileIO provider not configured for local" in result

    @pytest.mark.asyncio
    async def test_write_file_error(self, extension):
        """Test file writing with error."""
        mock_provider = MagicMock()
        mock_provider.write_file = MagicMock(side_effect=Exception("Provider error"))
        extension.provider = mock_provider

        result = await extension.write_file("test.txt", "content")

        assert "Failed to write file" in result

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test warning message when no provider is available."""
        extension.provider_type = "local"

        result = await extension._no_provider_warning()

        assert "FileIO provider not configured for local" in result

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
        issues = extension.validate_config()
        assert isinstance(issues, list)

    def test_validate_config_no_base_directory(self, extension):
        """Test configuration validation with no base directory."""
        extension.base_directory = ""

        issues = extension.validate_config()

        assert any("Base directory not specified" in issue for issue in issues)

    def test_validate_config_no_provider_type(self, extension):
        """Test configuration validation with no provider type."""
        extension.provider_type = ""

        issues = extension.validate_config()

        assert any("Provider type not specified" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "fileio:read",
            "fileio:write",
            "fileio:list",
            "fileio:info",
        ]
        assert set(permissions) == set(expected_permissions)

    def test_provider_settings_passed(self, extension):
        """Test that settings are passed to provider."""
        extension.settings = {"custom_setting": "value"}
        extension.provider_type = "local"

        with patch(
            "extensions.fileio.LocalFileSystem.LocalFileSystem"
        ) as mock_provider:
            extension._create_provider()

            # Check that settings were passed to provider
            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert "custom_setting" in call_kwargs
            assert call_kwargs["custom_setting"] == "value"

    def test_provider_with_extension_id(self, extension):
        """Test that extension ID is passed to provider."""
        extension.provider_type = "local"

        with patch(
            "extensions.fileio.LocalFileSystem.LocalFileSystem"
        ) as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["extension_id"] == "fileio"

    def test_provider_with_configuration(self, extension):
        """Test that configuration is passed to provider."""
        extension.provider_type = "local"
        extension.base_directory = "/test/path"

        with patch(
            "extensions.fileio.LocalFileSystem.LocalFileSystem"
        ) as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["base_directory"] == "/test/path"
            assert (
                call_kwargs["allowed_permissions"] == set()
            )  # No permissions set by default
            assert call_kwargs["allowlist_patterns"] == ["*"]
            assert call_kwargs["blocklist_patterns"] == []

    def test_permissions_conversion(self):
        """Test that string permissions are converted to enum values."""
        from extensions.fileio.PRV_FileIO import FileIOPermission

        extension = EXT_FileIO(allowed_permissions={"READ", "WRITE"})

        # Should have converted string permissions to enums
        assert FileIOPermission.READ in extension.permissions
        assert FileIOPermission.WRITE in extension.permissions

    def test_permissions_conversion_with_unknown_permission(self):
        """Test that unknown permissions are ignored with warning."""
        with patch("extensions.fileio.EXT_FileIO.logging") as mock_logging:
            extension = EXT_FileIO(allowed_permissions={"READ", "UNKNOWN_PERMISSION"})

            # Should have converted READ but ignored UNKNOWN_PERMISSION
            from extensions.fileio.PRV_FileIO import FileIOPermission

            assert FileIOPermission.READ in extension.permissions
            # UNKNOWN_PERMISSION should be ignored (no exception thrown)

    def test_allowlist_patterns_extraction(self):
        """Test allowlist patterns extraction from dict."""
        allowlist_patterns = {"patterns": ["*.py", "*.txt"]}
        extension = EXT_FileIO(allowlist_patterns=allowlist_patterns)

        assert extension.allowlist == ["*.py", "*.txt"]

    def test_blocklist_patterns_extraction(self):
        """Test blocklist patterns extraction from dict."""
        blocklist_patterns = {"patterns": ["*.exe", "*.bat"]}
        extension = EXT_FileIO(blocklist_patterns=blocklist_patterns)

        assert extension.blocklist == ["*.exe", "*.bat"]

    def test_patterns_default_values(self):
        """Test default values for patterns when None provided."""
        extension = EXT_FileIO(
            allowlist_patterns=None,
            blocklist_patterns=None,
        )

        assert extension.allowlist == ["*"]
        assert extension.blocklist == []

    def test_has_capability(self, extension):
        """Test has_capability method."""
        assert extension.has_capability("file_read") is True
        assert extension.has_capability("file_write") is True
        assert extension.has_capability("directory_list") is True
        assert extension.has_capability("file_system_info") is True
        assert extension.has_capability("file_operations") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_all_abilities_with_default_parameters(self, extension):
        """Test all abilities work with default parameters."""
        extension.provider = None
        extension.provider_type = "local"

        # All abilities should return provider not available error
        result = await extension.get_file_system_info()
        assert "FileIO provider not configured for local" in result

        result = await extension.list_directory()
        assert "FileIO provider not configured for local" in result

        result = await extension.read_file("test.txt")
        assert "FileIO provider not configured for local" in result

        result = await extension.write_file("test.txt", "content")
        assert "FileIO provider not configured for local" in result

    @pytest.mark.asyncio
    async def test_provider_method_calls_with_parameters(self, extension):
        """Test that provider methods are called with correct parameters."""
        mock_provider = MagicMock()
        extension.provider = mock_provider

        # Test list_directory with default parameter
        await extension.list_directory()
        mock_provider.list_directory.assert_called_with(".")

        # Test list_directory with custom parameter
        await extension.list_directory("/custom/path")
        mock_provider.list_directory.assert_called_with("/custom/path")

        # Test write_file with parameters
        await extension.write_file("test.txt", "test content")
        mock_provider.write_file.assert_called_with("test.txt", "test content")

        # Test read_file with parameter
        await extension.read_file("test.txt")
        mock_provider.read_file.assert_called_with("test.txt")

    def test_provider_error_handling_during_creation(self, extension):
        """Test provider error handling during creation."""
        extension.provider_type = "local"

        with patch(
            "extensions.fileio.LocalFileSystem.LocalFileSystem",
            side_effect=Exception("Creation error"),
        ), patch("extensions.fileio.EXT_FileIO.logging") as mock_logging:

            extension._create_provider()

            assert extension.provider is None
            mock_logging.error.assert_called()

    def test_empty_permissions_set(self):
        """Test extension with empty permissions set."""
        extension = EXT_FileIO(allowed_permissions=set())

        assert len(extension.permissions) == 0

    def test_none_permissions_set(self):
        """Test extension with None permissions set."""
        extension = EXT_FileIO(allowed_permissions=None)

        assert len(extension.permissions) == 0


if __name__ == "__main__":
    pytest.main([__file__])
