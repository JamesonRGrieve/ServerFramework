import os
from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.cloud.EXT_Cloud import EXT_Cloud
from zephyrex.extensions.cloud.nextcloud import NextcloudProvider


class TestCloudExtension:
    """Test cases for Cloud Extension."""

    @pytest.fixture
    def extension(self):
        """Create an EXT_Cloud instance for testing."""
        return EXT_Cloud()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "cloud"
        assert extension.version == "1.0.0"
        assert "cloud storage" in extension.description.lower()
        assert "file management" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "boto3" in pip_deps
        assert "azure-storage-blob" in pip_deps
        assert "google-cloud-storage" in pip_deps
        assert "dropbox" in pip_deps
        assert "requests" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "file_storage",
            "file_retrieval",
            "cloud_sync",
            "multi_provider",
            "backup_management",
            "access_control",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "provider")
        assert hasattr(extension, "provider_type")
        assert hasattr(extension, "access_key")
        assert hasattr(extension, "secret_key")
        assert hasattr(extension, "bucket_name")
        assert hasattr(extension, "region")
        assert extension.provider is None

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.provider_type == "aws_s3"
        assert extension.access_key == ""
        assert extension.secret_key == ""
        assert extension.bucket_name == ""
        assert extension.region == "us-east-1"

    def test_provider_types_constant(self, extension):
        """Test PROVIDER_TYPES constant is properly defined."""
        assert "aws_s3" in extension.PROVIDER_TYPES
        assert "azure_blob" in extension.PROVIDER_TYPES
        assert "google_cloud" in extension.PROVIDER_TYPES
        assert "dropbox" in extension.PROVIDER_TYPES
        assert "nextcloud" in extension.PROVIDER_TYPES

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0  # Empty by default

    @patch("zephyrex.extensions.cloud.EXT_Cloud.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "register_capability"
        ):

            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.cloud.EXT_Cloud.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_aws_s3(self, extension):
        """Test AWS S3 provider creation."""
        extension.provider_type = "aws_s3"

        with patch("zephyrex.extensions.cloud.aws_s3.AWSS3Provider") as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_azure_blob(self, extension):
        """Test Azure Blob provider creation."""
        extension.provider_type = "azure_blob"

        with patch(
            "zephyrex.extensions.cloud.azure_blob.AzureBlobProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_google_cloud(self, extension):
        """Test Google Cloud provider creation."""
        extension.provider_type = "google_cloud"

        with patch(
            "zephyrex.extensions.cloud.google_cloud.GoogleCloudProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_dropbox(self, extension):
        """Test Dropbox provider creation."""
        extension.provider_type = "dropbox"

        with patch(
            "zephyrex.extensions.cloud.dropbox_provider.DropboxProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_nextcloud(self, extension):
        """Test Nextcloud provider creation."""
        extension.provider_type = "nextcloud"

        with patch(
            "zephyrex.extensions.cloud.nextcloud.NextcloudProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_unsupported_type(self, extension):
        """Test provider creation with unsupported provider type."""
        extension.provider_type = "unsupported_type"

        with patch("zephyrex.extensions.cloud.EXT_Cloud.logger") as mock_logger:
            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called_with(
                "Unsupported cloud provider type: unsupported_type"
            )

    def test_create_provider_import_error(self, extension):
        """Test provider creation with import error."""
        extension.provider_type = "aws_s3"

        with patch(
            "zephyrex.extensions.cloud.aws_s3.AWSS3Provider",
            side_effect=ImportError("Module not found"),
        ), patch("zephyrex.extensions.cloud.EXT_Cloud.logger") as mock_logger:

            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called()

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
    async def test_upload_file_success(self, extension):
        """Test successful file upload."""
        mock_provider = MagicMock()
        mock_provider.upload_file = MagicMock(
            return_value={"success": True, "url": "https://bucket/file.txt"}
        )
        extension.provider = mock_provider

        result = await extension.upload_file("test.txt", b"file content", "folder/")

        assert result["success"] is True
        assert "url" in result
        mock_provider.upload_file.assert_called_once_with(
            "test.txt", b"file content", "folder/"
        )

    @pytest.mark.asyncio
    async def test_upload_file_no_provider(self, extension):
        """Test file upload without provider."""
        extension.provider = None
        extension.provider_type = "aws_s3"

        result = await extension.upload_file("test.txt", b"content")

        assert result["success"] is False
        assert "Cloud provider not configured for aws_s3" in result["message"]

    @pytest.mark.asyncio
    async def test_upload_file_error(self, extension):
        """Test file upload with error."""
        mock_provider = MagicMock()
        mock_provider.upload_file = MagicMock(side_effect=Exception("Upload error"))
        extension.provider = mock_provider

        result = await extension.upload_file("test.txt", b"content")

        assert result["success"] is False
        assert "Failed to upload file" in result["message"]

    @pytest.mark.asyncio
    async def test_upload_file_default_path(self, extension):
        """Test file upload with default path."""
        mock_provider = MagicMock()
        mock_provider.upload_file = MagicMock(return_value={"success": True})
        extension.provider = mock_provider

        result = await extension.upload_file("test.txt", b"content")

        mock_provider.upload_file.assert_called_once_with("test.txt", b"content", "")

    @pytest.mark.asyncio
    async def test_download_file_success(self, extension):
        """Test successful file download."""
        mock_provider = MagicMock()
        mock_provider.download_file = MagicMock(return_value=b"file content")
        extension.provider = mock_provider

        result = await extension.download_file("test.txt", "folder/")

        assert result["success"] is True
        assert result["content"] == b"file content"
        mock_provider.download_file.assert_called_once_with("test.txt", "folder/")

    @pytest.mark.asyncio
    async def test_download_file_no_provider(self, extension):
        """Test file download without provider."""
        extension.provider = None
        extension.provider_type = "azure_blob"

        result = await extension.download_file("test.txt")

        assert result["success"] is False
        assert "Cloud provider not configured for azure_blob" in result["message"]

    @pytest.mark.asyncio
    async def test_download_file_error(self, extension):
        """Test file download with error."""
        mock_provider = MagicMock()
        mock_provider.download_file = MagicMock(side_effect=Exception("Download error"))
        extension.provider = mock_provider

        result = await extension.download_file("test.txt")

        assert result["success"] is False
        assert "Failed to download file" in result["message"]

    @pytest.mark.asyncio
    async def test_download_file_default_path(self, extension):
        """Test file download with default path."""
        mock_provider = MagicMock()
        mock_provider.download_file = MagicMock(return_value=b"content")
        extension.provider = mock_provider

        result = await extension.download_file("test.txt")

        mock_provider.download_file.assert_called_once_with("test.txt", "")

    @pytest.mark.asyncio
    async def test_delete_file_success(self, extension):
        """Test successful file deletion."""
        mock_provider = MagicMock()
        mock_provider.delete_file = MagicMock(return_value=True)
        extension.provider = mock_provider

        result = await extension.delete_file("test.txt", "folder/")

        assert result["success"] is True
        assert "File deleted successfully" in result["message"]
        mock_provider.delete_file.assert_called_once_with("test.txt", "folder/")

    @pytest.mark.asyncio
    async def test_delete_file_no_provider(self, extension):
        """Test file deletion without provider."""
        extension.provider = None
        extension.provider_type = "google_cloud"

        result = await extension.delete_file("test.txt")

        assert result["success"] is False
        assert "Cloud provider not configured for google_cloud" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_file_error(self, extension):
        """Test file deletion with error."""
        mock_provider = MagicMock()
        mock_provider.delete_file = MagicMock(side_effect=Exception("Delete error"))
        extension.provider = mock_provider

        result = await extension.delete_file("test.txt")

        assert result["success"] is False
        assert "Failed to delete file" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_file_default_path(self, extension):
        """Test file deletion with default path."""
        mock_provider = MagicMock()
        mock_provider.delete_file = MagicMock(return_value=True)
        extension.provider = mock_provider

        result = await extension.delete_file("test.txt")

        mock_provider.delete_file.assert_called_once_with("test.txt", "")

    @pytest.mark.asyncio
    async def test_list_files_success(self, extension):
        """Test successful file listing."""
        mock_provider = MagicMock()
        mock_provider.list_files = MagicMock(return_value=["file1.txt", "file2.txt"])
        extension.provider = mock_provider

        result = await extension.list_files("folder/")

        assert result["success"] is True
        assert result["files"] == ["file1.txt", "file2.txt"]
        mock_provider.list_files.assert_called_once_with("folder/")

    @pytest.mark.asyncio
    async def test_list_files_no_provider(self, extension):
        """Test file listing without provider."""
        extension.provider = None
        extension.provider_type = "dropbox"

        result = await extension.list_files()

        assert result["success"] is False
        assert "Cloud provider not configured for dropbox" in result["message"]

    @pytest.mark.asyncio
    async def test_list_files_error(self, extension):
        """Test file listing with error."""
        mock_provider = MagicMock()
        mock_provider.list_files = MagicMock(side_effect=Exception("List error"))
        extension.provider = mock_provider

        result = await extension.list_files()

        assert result["success"] is False
        assert "Failed to list files" in result["message"]

    @pytest.mark.asyncio
    async def test_list_files_default_path(self, extension):
        """Test file listing with default path."""
        mock_provider = MagicMock()
        mock_provider.list_files = MagicMock(return_value=[])
        extension.provider = mock_provider

        result = await extension.list_files()

        mock_provider.list_files.assert_called_once_with("")

    @pytest.mark.asyncio
    async def test_sync_files_success(self, extension):
        """Test successful file synchronization."""
        mock_provider = MagicMock()
        mock_provider.sync_files = MagicMock(
            return_value={"synced": ["file1.txt"], "failed": []}
        )
        extension.provider = mock_provider

        result = await extension.sync_files("local/", "remote/")

        assert result["success"] is True
        assert "synced" in result
        assert "failed" in result
        mock_provider.sync_files.assert_called_once_with("local/", "remote/")

    @pytest.mark.asyncio
    async def test_sync_files_no_provider(self, extension):
        """Test file synchronization without provider."""
        extension.provider = None
        extension.provider_type = "aws_s3"

        result = await extension.sync_files("local/", "remote/")

        assert result["success"] is False
        assert "Cloud provider not configured for aws_s3" in result["message"]

    @pytest.mark.asyncio
    async def test_sync_files_error(self, extension):
        """Test file synchronization with error."""
        mock_provider = MagicMock()
        mock_provider.sync_files = MagicMock(side_effect=Exception("Sync error"))
        extension.provider = mock_provider

        result = await extension.sync_files("local/", "remote/")

        assert result["success"] is False
        assert "Failed to sync files" in result["message"]

    @pytest.mark.asyncio
    async def test_sync_files_default_paths(self, extension):
        """Test file synchronization with default paths."""
        mock_provider = MagicMock()
        mock_provider.sync_files = MagicMock(return_value={"synced": [], "failed": []})
        extension.provider = mock_provider

        result = await extension.sync_files()

        mock_provider.sync_files.assert_called_once_with("", "")

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test warning message when no provider is available."""
        extension.provider_type = "azure_blob"

        result = await extension._no_provider_warning()

        assert result["success"] is False
        assert "Cloud provider not configured for azure_blob" in result["message"]

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
        extension.provider_type = "aws_s3"
        extension.access_key = "test_key"
        extension.secret_key = "test_secret"
        extension.bucket_name = "test_bucket"

        issues = extension.validate_config()
        assert isinstance(issues, list)

    def test_validate_config_no_provider_type(self, extension):
        """Test configuration validation with no provider type."""
        extension.provider_type = ""

        issues = extension.validate_config()

        assert any("Cloud provider type not specified" in issue for issue in issues)

    def test_validate_config_aws_no_credentials(self, extension):
        """Test configuration validation for AWS without credentials."""
        extension.provider_type = "aws_s3"
        extension.access_key = ""
        extension.secret_key = ""

        issues = extension.validate_config()

        assert any("AWS access key not provided" in issue for issue in issues)
        assert any("AWS secret key not provided" in issue for issue in issues)

    def test_validate_config_aws_no_bucket(self, extension):
        """Test configuration validation for AWS without bucket name."""
        extension.provider_type = "aws_s3"
        extension.access_key = "test_key"
        extension.secret_key = "test_secret"
        extension.bucket_name = ""

        issues = extension.validate_config()

        assert any("Bucket name not provided" in issue for issue in issues)

    def test_validate_config_azure_no_credentials(self, extension):
        """Test configuration validation for Azure without credentials."""
        extension.provider_type = "azure_blob"
        extension.access_key = ""  # Connection string for Azure

        issues = extension.validate_config()

        assert any("Azure connection string not provided" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "cloud:upload",
            "cloud:download",
            "cloud:delete",
            "cloud:list",
            "cloud:sync",
            "network:connect",
        ]
        assert set(permissions) == set(expected_permissions)

    def test_custom_configuration(self):
        """Test extension with custom configuration."""
        extension = EXT_Cloud(
            provider_type="google_cloud",
            access_key="custom_key",
            secret_key="custom_secret",
            bucket_name="custom_bucket",
            region="us-west-1",
        )

        assert extension.provider_type == "google_cloud"
        assert extension.access_key == "custom_key"
        assert extension.secret_key == "custom_secret"
        assert extension.bucket_name == "custom_bucket"
        assert extension.region == "us-west-1"

    def test_provider_with_configuration_parameters(self, extension):
        """Test provider creation with configuration parameters."""
        extension.provider_type = "aws_s3"
        extension.access_key = "test_access_key"
        extension.secret_key = "test_secret_key"
        extension.bucket_name = "test_bucket"
        extension.region = "us-west-2"
        extension.settings = {"custom_setting": "value"}

        with patch("zephyrex.extensions.cloud.aws_s3.AWSS3Provider") as mock_provider:
            extension._create_provider()

            # Check that provider was called with correct parameters
            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["access_key"] == "test_access_key"
            assert call_kwargs["secret_key"] == "test_secret_key"
            assert call_kwargs["bucket_name"] == "test_bucket"
            assert call_kwargs["region"] == "us-west-2"
            assert call_kwargs["custom_setting"] == "value"

    def test_provider_with_default_parameters(self, extension):
        """Test provider creation with default parameters."""
        extension.provider_type = "aws_s3"
        extension.settings = {"test": "value"}

        with patch("zephyrex.extensions.cloud.aws_s3.AWSS3Provider") as mock_provider:
            extension._create_provider()

            # Check that provider was called with default parameters
            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["access_key"] == ""
            assert call_kwargs["secret_key"] == ""
            assert call_kwargs["bucket_name"] == ""
            assert call_kwargs["region"] == "us-east-1"
            assert call_kwargs["test"] == "value"

    def test_has_capability(self, extension):
        """Test has_capability method."""
        assert extension.has_capability("file_storage") is True
        assert extension.has_capability("file_retrieval") is True
        assert extension.has_capability("cloud_sync") is True
        assert extension.has_capability("multi_provider") is True
        assert extension.has_capability("backup_management") is True
        assert extension.has_capability("access_control") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_all_abilities_with_different_provider_types(self, extension):
        """Test all abilities work with different provider types."""
        for provider_type in [
            "aws_s3",
            "azure_blob",
            "google_cloud",
            "dropbox",
            "nextcloud",
        ]:
            extension.provider_type = provider_type
            extension.provider = None

            # All abilities should return provider not available error
            result = await extension.upload_file("test.txt", b"content")
            assert result["success"] is False

            result = await extension.download_file("test.txt")
            assert result["success"] is False

            result = await extension.delete_file("test.txt")
            assert result["success"] is False

            result = await extension.list_files()
            assert result["success"] is False

            result = await extension.sync_files()
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_provider_method_calls_with_parameters(self, extension):
        """Test that provider methods are called with correct parameters."""
        mock_provider = MagicMock()
        extension.provider = mock_provider

        # Test upload_file
        await extension.upload_file("document.pdf", b"pdf content", "documents/")
        mock_provider.upload_file.assert_called_with(
            "document.pdf", b"pdf content", "documents/"
        )

        # Test download_file
        await extension.download_file("image.jpg", "images/")
        mock_provider.download_file.assert_called_with("image.jpg", "images/")

        # Test delete_file
        await extension.delete_file("old_file.txt", "archive/")
        mock_provider.delete_file.assert_called_with("old_file.txt", "archive/")

        # Test list_files
        await extension.list_files("projects/")
        mock_provider.list_files.assert_called_with("projects/")

        # Test sync_files
        await extension.sync_files("local_folder/", "remote_folder/")
        mock_provider.sync_files.assert_called_with("local_folder/", "remote_folder/")

    def test_provider_error_handling_during_creation(self, extension):
        """Test provider error handling during creation."""
        extension.provider_type = "aws_s3"

        with patch(
            "zephyrex.extensions.cloud.aws_s3.AWSS3Provider",
            side_effect=Exception("Creation error"),
        ), patch("zephyrex.extensions.cloud.EXT_Cloud.logger") as mock_logger:

            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called()

    def test_provider_type_case_insensitive(self):
        """Test that provider type is handled case-insensitively."""
        extension = EXT_Cloud(provider_type="AWS_S3")
        assert extension.provider_type == "aws_s3"

        extension = EXT_Cloud(provider_type="AZURE_BLOB")
        assert extension.provider_type == "azure_blob"

    @pytest.mark.asyncio
    async def test_abilities_with_empty_parameters(self, extension):
        """Test abilities with empty string parameters."""
        mock_provider = MagicMock()
        mock_provider.upload_file = MagicMock(return_value={"success": True})
        mock_provider.download_file = MagicMock(return_value=b"")
        mock_provider.delete_file = MagicMock(return_value=True)
        mock_provider.list_files = MagicMock(return_value=[])
        mock_provider.sync_files = MagicMock(return_value={"synced": [], "failed": []})
        extension.provider = mock_provider

        # Test with empty strings and empty content
        await extension.upload_file("", b"", "")
        mock_provider.upload_file.assert_called_with("", b"", "")

        await extension.download_file("", "")
        mock_provider.download_file.assert_called_with("", "")

        await extension.delete_file("", "")
        mock_provider.delete_file.assert_called_with("", "")

        await extension.list_files("")
        mock_provider.list_files.assert_called_with("")

        await extension.sync_files("", "")
        mock_provider.sync_files.assert_called_with("", "")

    def test_settings_passed_to_provider(self, extension):
        """Test that settings are properly passed to provider."""
        extension.provider_type = "aws_s3"
        extension.settings = {"timeout": 60, "retries": 5, "encryption": True}

        with patch("zephyrex.extensions.cloud.aws_s3.AWSS3Provider") as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["timeout"] == 60
            assert call_kwargs["retries"] == 5
            assert call_kwargs["encryption"] is True

    def test_validate_config_google_cloud_credentials(self, extension):
        """Test configuration validation for Google Cloud credentials."""
        extension.provider_type = "google_cloud"
        extension.access_key = ""  # Service account key for Google Cloud

        issues = extension.validate_config()

        assert any(
            "Google Cloud service account key not provided" in issue for issue in issues
        )

    def test_validate_config_dropbox_credentials(self, extension):
        """Test configuration validation for Dropbox credentials."""
        extension.provider_type = "dropbox"
        extension.access_key = ""  # Access token for Dropbox

        issues = extension.validate_config()

        assert any("Dropbox access token not provided" in issue for issue in issues)

    def test_validate_config_nextcloud_credentials(self, extension):
        """Test configuration validation for Nextcloud credentials."""
        extension.provider_type = "nextcloud"
        extension.access_key = ""  # Username for Nextcloud
        extension.secret_key = ""  # App password for Nextcloud
        extension.settings = {}

        issues = extension.validate_config()

        assert any("Nextcloud username not provided" in issue for issue in issues)
        assert any("Nextcloud app password not provided" in issue for issue in issues)
        assert any("Nextcloud base URL not provided" in issue for issue in issues)

    def test_validate_config_nextcloud_success(self, extension):
        """Test successful Nextcloud configuration validation."""
        extension.provider_type = "nextcloud"
        extension.access_key = "alice"
        extension.secret_key = "app-password"
        extension.settings = {"base_url": "https://cloud.example.com"}

        issues = extension.validate_config()

        assert not any("Nextcloud" in issue for issue in issues)

    def test_create_provider_nextcloud_with_settings(self, extension):
        """Test Nextcloud provider creation passes base_url through settings."""
        extension.provider_type = "nextcloud"
        extension.access_key = "alice"
        extension.secret_key = "app-password"
        extension.settings = {"base_url": "https://cloud.example.com"}

        with patch(
            "zephyrex.extensions.cloud.nextcloud.NextcloudProvider"
        ) as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["access_key"] == "alice"
            assert call_kwargs["secret_key"] == "app-password"
            assert call_kwargs["base_url"] == "https://cloud.example.com"

    @pytest.mark.asyncio
    async def test_file_operations_with_binary_content(self, extension):
        """Test file operations with various binary content types."""
        mock_provider = MagicMock()
        mock_provider.upload_file = MagicMock(return_value={"success": True})
        mock_provider.download_file = MagicMock(
            return_value=b"\x89PNG\r\n\x1a\n"
        )  # PNG header
        extension.provider = mock_provider

        # Test upload with binary content
        binary_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        result = await extension.upload_file("image.png", binary_content)
        assert result["success"] is True

        # Test download returning binary content
        result = await extension.download_file("image.png")
        assert result["success"] is True
        assert isinstance(result["content"], bytes)

    def test_bucket_name_validation_for_different_providers(self, extension):
        """Test bucket name validation for different providers."""
        # AWS S3 bucket validation
        extension.provider_type = "aws_s3"
        extension.bucket_name = "valid-bucket-name"
        issues = extension.validate_config()
        bucket_issues = [issue for issue in issues if "bucket" in issue.lower()]
        # Should not have bucket format issues for valid name

        # Azure container validation
        extension.provider_type = "azure_blob"
        extension.bucket_name = "valid-container-name"
        issues = extension.validate_config()
        # Azure uses the same bucket_name field for container name

    def test_region_validation_for_aws(self, extension):
        """Test region validation for AWS provider."""
        extension.provider_type = "aws_s3"
        extension.region = "invalid-region"
        # Region validation would depend on the provider implementation
        # but the extension should accept any string

        valid_regions = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]
        for region in valid_regions:
            extension.region = region
            issues = extension.validate_config()
            # Should not have region-specific issues for standard regions


class TestNextcloudProvider:
    """Direct unit tests for NextcloudProvider (WebDAV + OCS share API over requests)."""

    @pytest.fixture
    def provider(self):
        """Create a NextcloudProvider instance for testing."""
        return NextcloudProvider(
            access_key="alice",
            secret_key="app-password",
            base_url="https://cloud.example.com",
        )

    def test_get_platform_name(self, provider):
        """Test platform name reporting."""
        assert provider.get_platform_name() == "Nextcloud"

    def test_base_url_strips_trailing_slash(self):
        """Test base_url setting normalizes a trailing slash."""
        provider = NextcloudProvider(
            access_key="alice",
            secret_key="app-password",
            base_url="https://cloud.example.com/",
        )
        assert provider.base_url == "https://cloud.example.com"

    def test_upload_file_success(self, provider):
        """Test successful upload issues a WebDAV PUT and returns the path."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.put.return_value = mock_response

        with patch.object(provider, "_session", return_value=mock_session):
            result = provider.upload_file("report.txt", b"hello", "docs")

        assert result == {"success": True, "path": "docs/report.txt"}
        mock_session.put.assert_called_once()
        called_url = mock_session.put.call_args[0][0]
        assert called_url == (
            "https://cloud.example.com/remote.php/dav/files/alice/docs/report.txt"
        )
        assert mock_session.put.call_args[1]["data"] == b"hello"
        mock_response.raise_for_status.assert_called_once()

    def test_upload_file_http_error_propagates(self, provider):
        """Test an HTTP error from WebDAV propagates to the caller."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception(
            "507 Insufficient Storage"
        )
        mock_session = MagicMock()
        mock_session.put.return_value = mock_response

        with patch.object(provider, "_session", return_value=mock_session):
            with pytest.raises(Exception, match="507 Insufficient Storage"):
                provider.upload_file("report.txt", b"hello")

    def test_download_file_success(self, provider):
        """Test successful download issues a WebDAV GET and returns bytes."""
        mock_response = MagicMock(content=b"file bytes")
        mock_response.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response

        with patch.object(provider, "_session", return_value=mock_session):
            content = provider.download_file("report.txt", "docs")

        assert content == b"file bytes"
        called_url = mock_session.get.call_args[0][0]
        assert called_url == (
            "https://cloud.example.com/remote.php/dav/files/alice/docs/report.txt"
        )

    def test_download_file_http_error_propagates(self, provider):
        """Test an HTTP error on download propagates to the caller."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response

        with patch.object(provider, "_session", return_value=mock_session):
            with pytest.raises(Exception, match="404 Not Found"):
                provider.download_file("missing.txt")

    def test_delete_file_success(self, provider):
        """Test successful delete issues a WebDAV DELETE and returns True."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_response

        with patch.object(provider, "_session", return_value=mock_session):
            result = provider.delete_file("report.txt", "docs")

        assert result is True
        mock_session.delete.assert_called_once()

    def test_delete_file_http_error_propagates(self, provider):
        """Test an HTTP error on delete propagates to the caller."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_response

        with patch.object(provider, "_session", return_value=mock_session):
            with pytest.raises(Exception, match="404 Not Found"):
                provider.delete_file("missing.txt")

    def test_list_files_success(self, provider):
        """Test listing parses a WebDAV PROPFIND multistatus response."""
        propfind_xml = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice/docs/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
      </d:prop>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/docs/report.txt</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>report.txt</d:displayname>
        <d:getcontentlength>42</d:getcontentlength>
        <d:resourcetype/>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""
        mock_response = MagicMock(content=propfind_xml)
        mock_response.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        with patch.object(provider, "_session", return_value=mock_session):
            files = provider.list_files("docs")

        assert files == [
            {
                "name": "report.txt",
                "path": "/remote.php/dav/files/alice/docs/report.txt",
                "is_directory": False,
                "size": 42,
            }
        ]
        method, url = mock_session.request.call_args[0]
        assert method == "PROPFIND"
        assert url == "https://cloud.example.com/remote.php/dav/files/alice/docs/"
        assert mock_session.request.call_args[1]["headers"]["Depth"] == "1"

    def test_list_files_error(self, provider):
        """Test an HTTP error on listing propagates to the caller."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("401 Unauthorized")
        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        with patch.object(provider, "_session", return_value=mock_session):
            with pytest.raises(Exception, match="401 Unauthorized"):
                provider.list_files()

    def test_sync_files_uploads_local_tree(self, provider, tmp_path):
        """Test sync walks a local directory and uploads every file."""
        (tmp_path / "a.txt").write_bytes(b"a")
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "b.txt").write_bytes(b"b")

        with patch.object(
            provider, "upload_file", return_value={"success": True}
        ) as mock_upload:
            result = provider.sync_files(str(tmp_path), "backup")

        assert set(result["synced"]) == {"a.txt", os.path.join("nested", "b.txt")}
        assert result["failed"] == []
        assert mock_upload.call_count == 2

    def test_sync_files_records_failures(self, provider, tmp_path):
        """Test sync records per-file failures without aborting the batch."""
        (tmp_path / "a.txt").write_bytes(b"a")

        with patch.object(provider, "upload_file", side_effect=Exception("boom")):
            result = provider.sync_files(str(tmp_path), "backup")

        assert result["synced"] == []
        assert result["failed"] == ["a.txt"]

    def test_sync_files_missing_local_path(self, provider):
        """Test sync short-circuits when the local path doesn't exist."""
        result = provider.sync_files("", "backup")
        assert result == {"synced": [], "failed": []}

    def test_create_share_link_success(self, provider):
        """Test creating a public share link via the OCS Share API."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "ocs": {
                "data": {
                    "url": "https://cloud.example.com/s/abc123",
                    "token": "abc123",
                }
            }
        }
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response

        with patch.object(provider, "_session", return_value=mock_session):
            result = provider.create_share_link("report.txt", "docs")

        assert result == {
            "success": True,
            "url": "https://cloud.example.com/s/abc123",
            "token": "abc123",
        }
        called_url = mock_session.post.call_args[0][0]
        assert called_url == (
            "https://cloud.example.com/ocs/v2.php/apps/files_sharing/api/v1/shares"
        )
        called_kwargs = mock_session.post.call_args[1]
        assert called_kwargs["data"]["path"] == "/docs/report.txt"
        assert called_kwargs["data"]["shareType"] == 3

    def test_create_share_link_error_propagates(self, provider):
        """Test an HTTP error from the OCS Share API propagates to the caller."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("403 Forbidden")
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response

        with patch.object(provider, "_session", return_value=mock_session):
            with pytest.raises(Exception, match="403 Forbidden"):
                provider.create_share_link("report.txt")


if __name__ == "__main__":
    pytest.main([__file__])
