from abc import ABC, abstractmethod
from typing import Any, Dict, List


class AbstractCloudProvider(ABC):
    """
    Abstract base class for all cloud storage providers used by the Cloud
    extension (AWS S3, Azure Blob Storage, Google Cloud Storage, Dropbox).

    Concrete providers are lightweight, directly-instantiated clients that
    hold their own credentials/config (access key, secret key, bucket name,
    region) and expose synchronous storage operations. EXT_Cloud's async
    abilities call into these methods directly (no await) so a provider's
    return values can be plain values rather than awaitables.
    """

    def __init__(
        self,
        access_key: str = "",
        secret_key: str = "",
        bucket_name: str = "",
        region: str = "us-east-1",
        **kwargs: Any,
    ) -> None:
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket_name = bucket_name
        self.region = region
        self.settings: Dict[str, Any] = kwargs

    @abstractmethod
    def upload_file(
        self, file_path: str, content: bytes, folder_path: str = ""
    ) -> Dict[str, Any]:
        """Upload file content to the given folder/prefix.

        Returns a result dict containing at least ``{"success": bool}``.
        """

    @abstractmethod
    def download_file(self, file_id: str, destination_path: str = "") -> bytes:
        """Download and return the raw bytes of a file."""

    @abstractmethod
    def delete_file(self, file_id: str, folder_path: str = "") -> bool:
        """Delete a file. Returns True on success."""

    @abstractmethod
    def list_files(self, folder_path: str = "") -> List[Dict[str, Any]]:
        """List files under a folder/prefix."""

    @abstractmethod
    def sync_files(self, local_path: str = "", remote_path: str = "") -> Dict[str, Any]:
        """Sync files between a local path and a remote path.

        Returns ``{"synced": [...], "failed": [...]}``.
        """

    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the name of the cloud platform this provider interacts with."""

    def get_extension_info(self) -> Dict[str, Any]:
        """Get information about the cloud extension."""
        return {
            "name": "Cloud",
            "description": f"Cloud storage extension for {self.get_platform_name()}",
            "platform": self.get_platform_name(),
            "bucket_name": self.bucket_name,
            "region": self.region,
        }
