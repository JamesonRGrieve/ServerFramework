from typing import Any, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_Cloud(AbstractStaticExtension):
    """
    Cloud storage extension for AGInfrastructure.
    Provides file management (upload/download/delete/list/sync) across
    multiple cloud storage providers: AWS S3, Azure Blob Storage,
    Google Cloud Storage, Dropbox, and Nextcloud.

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "cloud"
    version = "1.0.0"
    description = (
        "Cloud storage extension providing file management across multiple "
        "cloud storage providers (AWS S3, Azure Blob Storage, Google Cloud "
        "Storage, Dropbox, Nextcloud)"
    )

    # Supported provider types.
    PROVIDER_TYPES = ["aws_s3", "azure_blob", "google_cloud", "dropbox", "nextcloud"]

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base cloud storage functionality",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="boto3",
            friendly_name="AWS SDK",
            optional=True,
            reason="Required for AWS S3 integration",
            semver=">=1.26.0",
        ),
        PIP_Dependency(
            name="azure-storage-blob",
            friendly_name="Azure Storage Blob SDK",
            optional=True,
            reason="Required for Azure Blob Storage integration",
            semver=">=12.0.0",
        ),
        PIP_Dependency(
            name="google-cloud-storage",
            friendly_name="Google Cloud Storage SDK",
            optional=True,
            reason="Required for Google Cloud Storage integration",
            semver=">=2.0.0",
        ),
        PIP_Dependency(
            name="dropbox",
            friendly_name="Dropbox SDK",
            optional=True,
            reason="Required for Dropbox integration",
            semver=">=11.0.0",
        ),
        PIP_Dependency(
            name="requests",
            friendly_name="Requests HTTP library",
            optional=True,
            reason="Required for Nextcloud WebDAV/OCS integration",
            semver=">=2.28.0",
        ),
    ]

    sys_dependencies: List[str] = []

    # Define database tables (none for this extension)
    db_tables: List[str] = []

    # Define what capabilities this extension provides
    capabilities = [
        "file_storage",
        "file_retrieval",
        "cloud_sync",
        "multi_provider",
        "backup_management",
        "access_control",
    ]

    def __init__(
        self,
        provider_type: str = "aws_s3",
        access_key: str = "",
        secret_key: str = "",
        bucket_name: str = "",
        region: str = "us-east-1",
        **kwargs: Any,
    ):
        """
        Initialize the cloud storage extension.
        """
        super().__init__(**kwargs)

        self.provider_type = provider_type.lower()
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket_name = bucket_name
        self.region = region
        self.settings: Dict[str, Any] = {}
        self.provider = None

    def on_initialize(self) -> bool:
        """Initialize the cloud storage extension with the appropriate provider."""
        logger.debug("Initializing Cloud Storage Extension...")

        try:
            self._create_provider()

            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("Cloud storage extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Cloud storage extension: {str(e)}")
            return False

    def _create_provider(self) -> None:
        """Create the appropriate cloud storage provider based on provider_type."""
        provider_kwargs = {
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "bucket_name": self.bucket_name,
            "region": self.region,
            **getattr(self, "settings", {}),
        }

        try:
            if self.provider_type == "aws_s3":
                from zephyrex.extensions.cloud.aws_s3 import AWSS3Provider

                self.provider = AWSS3Provider(**provider_kwargs)

            elif self.provider_type == "azure_blob":
                from zephyrex.extensions.cloud.azure_blob import AzureBlobProvider

                self.provider = AzureBlobProvider(**provider_kwargs)

            elif self.provider_type == "google_cloud":
                from zephyrex.extensions.cloud.google_cloud import GoogleCloudProvider

                self.provider = GoogleCloudProvider(**provider_kwargs)

            elif self.provider_type == "dropbox":
                from zephyrex.extensions.cloud.dropbox_provider import DropboxProvider

                self.provider = DropboxProvider(**provider_kwargs)

            elif self.provider_type == "nextcloud":
                from zephyrex.extensions.cloud.nextcloud import NextcloudProvider

                self.provider = NextcloudProvider(**provider_kwargs)

            else:
                logger.error(f"Unsupported cloud provider type: {self.provider_type}")
                self.provider = None

        except Exception as e:
            logger.error(f"Error creating cloud storage provider: {str(e)}")
            self.provider = None

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

    async def _no_provider_warning(self) -> Dict[str, Any]:
        """Warning result when a provider is not available."""
        return {
            "success": False,
            "message": f"Cloud provider not configured for {self.provider_type}",
        }

    @ability("upload_file")
    async def upload_file(
        self, file_path: str, content: bytes, folder_path: str = ""
    ) -> Dict[str, Any]:
        """Upload file content to the specified folder."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.upload_file(file_path, content, folder_path)
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            return {"success": False, "message": f"Failed to upload file: {str(e)}"}

    @ability("download_file")
    async def download_file(
        self, file_id: str, destination_path: str = ""
    ) -> Dict[str, Any]:
        """Download a file's content."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            content = self.provider.download_file(file_id, destination_path)
            return {"success": True, "content": content}
        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            return {"success": False, "message": f"Failed to download file: {str(e)}"}

    @ability("delete_file")
    async def delete_file(self, file_id: str, folder_path: str = "") -> Dict[str, Any]:
        """Delete a file."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            self.provider.delete_file(file_id, folder_path)
            return {"success": True, "message": "File deleted successfully"}
        except Exception as e:
            logger.error(f"Error deleting file: {e}")
            return {"success": False, "message": f"Failed to delete file: {str(e)}"}

    @ability("list_files")
    async def list_files(self, folder_path: str = "") -> Dict[str, Any]:
        """List files in a specified folder."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            files = self.provider.list_files(folder_path)
            return {"success": True, "files": files}
        except Exception as e:
            logger.error(f"Error listing files: {e}")
            return {"success": False, "message": f"Failed to list files: {str(e)}"}

    @ability("sync_files")
    async def sync_files(
        self, local_path: str = "", remote_path: str = ""
    ) -> Dict[str, Any]:
        """Synchronize files between a local path and a remote path."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            result = self.provider.sync_files(local_path, remote_path)
            return {"success": True, **result}
        except Exception as e:
            logger.error(f"Error syncing files: {e}")
            return {"success": False, "message": f"Failed to sync files: {str(e)}"}

    def on_start(self) -> bool:
        """Start the Cloud Storage extension."""
        try:
            logger.debug("Cloud Storage extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Cloud Storage extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Cloud Storage extension."""
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Cloud Storage extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Cloud Storage extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues: List[str] = []

        if not self.provider_type:
            issues.append("Cloud provider type not specified")
        elif self.provider_type not in self.PROVIDER_TYPES:
            issues.append(f"Unsupported cloud provider type: {self.provider_type}")

        if self.provider_type == "aws_s3":
            if not self.access_key:
                issues.append("AWS access key not provided")
            if not self.secret_key:
                issues.append("AWS secret key not provided")
            if not self.bucket_name:
                issues.append("Bucket name not provided")

        elif self.provider_type == "azure_blob":
            if not self.access_key:
                issues.append("Azure connection string not provided")

        elif self.provider_type == "google_cloud":
            if not self.access_key:
                issues.append("Google Cloud service account key not provided")

        elif self.provider_type == "dropbox":
            if not self.access_key:
                issues.append("Dropbox access token not provided")

        elif self.provider_type == "nextcloud":
            if not self.access_key:
                issues.append("Nextcloud username not provided")
            if not self.secret_key:
                issues.append("Nextcloud app password not provided")
            if not self.settings.get("base_url"):
                issues.append("Nextcloud base URL not provided")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "cloud:upload",
            "cloud:download",
            "cloud:delete",
            "cloud:list",
            "cloud:sync",
            "network:connect",
        ]

    def on_startup(self) -> None:
        """Called during application startup."""
        logger.debug("Cloud Storage extension startup hook called")

    def on_shutdown(self) -> None:
        """Called during application shutdown."""
        logger.debug("Cloud Storage extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
