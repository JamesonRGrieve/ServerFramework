import logging
from typing import Any, Dict, Optional, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.extensions.fileio.PRV_FileIO import FileIOPermission


class EXT_FileIO(AbstractStaticExtension):
    """
    FileIO extension for AGInfrastructure.

    Provides functionality for interacting with the local file system on Windows, Linux, and macOS.
    This extension focuses on secure file operations with permission-based access control.
    """

    # Extension metadata
    name = "fileio"
    version = "1.0.0"
    description = "File I/O extension for interacting with local files and directories"

    # Define dependencies
    ext_dependencies = []
    pip_dependencies = []
    sys_dependencies = []

    # Define what capabilities this extension provides
    capabilities = [
        "file_read",
        "file_write",
        "directory_list",
        "file_system_info",
        "file_operations",
    ]

    # Define database tables
    db_tables = []

    def __init__(
        self,
        base_directory: str = ".",
        allowed_permissions: Optional[Set[str]] = None,
        allowlist_patterns: Optional[Dict[str, Any]] = None,
        blocklist_patterns: Optional[Dict[str, Any]] = None,
        provider_type: str = "local",
        **kwargs,
    ):
        """
        Initialize the FileIO extension.

        Args:
            base_directory: The root directory this provider has access to
            allowed_permissions: Set of permission names granted to this provider
            allowlist_patterns: Dict of patterns for files/directories that are allowed
            blocklist_patterns: Dict of patterns for files/directories that are blocked
            provider_type: Type of FileIO provider to use ('local' is currently supported)
            **kwargs: Additional configuration options
        """
        super().__init__(**kwargs)

        self.base_directory = base_directory
        self.provider_type = provider_type.lower()

        # Convert string permissions to enum values
        self.permissions = set()
        if allowed_permissions:
            for perm in allowed_permissions:
                try:
                    self.permissions.add(FileIOPermission[perm.upper()])
                except KeyError:
                    logging.warning(f"Unknown permission: {perm}, ignoring")

        # Extract patterns
        self.allowlist = (
            allowlist_patterns.get("patterns", ["*"]) if allowlist_patterns else ["*"]
        )
        self.blocklist = (
            blocklist_patterns.get("patterns", []) if blocklist_patterns else []
        )

        # Provider instance reference
        self.provider = None

    def on_initialize(self) -> bool:
        """
        Initialize the FileIO extension.
        """
        logging.debug("Initializing FileIO Extension...")

        try:
            # Create provider instance
            self._create_provider()

            # Register capabilities
            self.register_capability("file_read")
            self.register_capability("file_write")
            self.register_capability("directory_list")
            self.register_capability("file_system_info")
            self.register_capability("file_operations")

            logging.debug("FileIO extension initialized successfully")
            return True

        except Exception as e:
            logging.error(f"Failed to initialize FileIO extension: {str(e)}")
            return False

    def _create_provider(self):
        """
        Create the appropriate FileIO provider instance based on provider_type.
        """
        try:
            if self.provider_type == "local":
                from extensions.fileio.LocalFileSystem import LocalFileSystem

                self.provider = LocalFileSystem(
                    base_directory=self.base_directory,
                    allowed_permissions=self.permissions,
                    allowlist_patterns=self.allowlist,
                    blocklist_patterns=self.blocklist,
                    extension_id=self.name,
                    **self.settings,
                )
            else:
                logging.error(f"Unsupported FileIO provider type: {self.provider_type}")
                self.provider = None

            if self.provider:
                logging.debug(
                    f"FileIO provider for {self.provider_type} created successfully"
                )
            else:
                logging.warning(
                    f"No FileIO provider available for {self.provider_type}"
                )

        except Exception as e:
            logging.error(f"Error creating FileIO provider: {str(e)}")
            self.provider = None

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    def register_capability(self, capability: str):
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    @AbstractExtension.ability("get_file_system_info")
    async def get_file_system_info(self) -> str:
        """
        Get file system information.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return await self.provider.get_file_system_info()
        except Exception as e:
            return f"Failed to get file system info: {str(e)}"

    @AbstractExtension.ability("list_directory")
    async def list_directory(self, directory_path: str = ".") -> str:
        """
        List contents of a directory.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return await self.provider.list_directory(directory_path)
        except Exception as e:
            return f"Failed to list directory: {str(e)}"

    @AbstractExtension.ability("read_file")
    async def read_file(self, file_path: str) -> str:
        """
        Read contents of a file.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return await self.provider.read_file(file_path)
        except Exception as e:
            return f"Failed to read file: {str(e)}"

    @AbstractExtension.ability("write_file")
    async def write_file(self, file_path: str, content: str) -> str:
        """
        Write content to a file.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return await self.provider.write_file(file_path, content)
        except Exception as e:
            return f"Failed to write file: {str(e)}"

    async def _no_provider_warning(self, *args, **kwargs) -> str:
        """Return a warning message when no provider is configured."""
        return f"FileIO provider not configured for {self.provider_type}. Please check your configuration."

    def get_required_permissions(self) -> list[str]:
        """Return the list of permissions required by this extension."""
        return [
            "fileio:read",
            "fileio:write",
            "fileio:list",
            "fileio:info",
        ]

    def on_start(self) -> bool:
        """
        Start the FileIO extension.
        """
        try:
            logging.debug("FileIO extension started successfully")
            return True
        except Exception as e:
            logging.error(f"Failed to start FileIO extension: {e}")
            return False

    def on_stop(self) -> bool:
        """
        Stop the FileIO extension.
        """
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logging.debug("FileIO extension stopped successfully")
            return True
        except Exception as e:
            logging.error(f"Error stopping FileIO extension: {e}")
            return False

    def validate_config(self) -> list[str]:
        """
        Validate the extension configuration.
        """
        issues = []

        if not self.base_directory:
            issues.append("Base directory not specified")

        if not self.provider_type:
            issues.append("Provider type not specified")

        return issues

    def on_startup(self):
        """
        Called during application startup.
        """
        logging.debug("FileIO extension startup hook called")

    def on_shutdown(self):
        """
        Called during application shutdown.
        """
        logging.debug("FileIO extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
