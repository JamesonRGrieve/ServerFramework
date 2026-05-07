from abc import abstractmethod
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticProvider as AbstractProvider,
)


class FileIOPermission(Enum):
    """Enumeration of file I/O permissions."""

    READ = auto()  # Permission to read files
    WRITE = auto()  # Permission to write/modify files
    DELETE = auto()  # Permission to delete files
    CREATE = auto()  # Permission to create new files
    LIST = auto()  # Permission to list directory contents
    EXECUTE = auto()  # Permission to execute files


class AbstractFileIOProvider(AbstractProvider):
    """
    Abstract base class for file I/O service extensions.
    This class defines the common interface and functionality that all file I/O providers should implement.
    """

    def __init__(
        self,
        base_directory: str,
        allowed_permissions: Set[FileIOPermission] = None,
        allowlist_patterns: List[str] = None,
        blocklist_patterns: List[str] = None,
        extension_id: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize the file I/O provider with configuration parameters.

        Args:
            base_directory: The root directory this provider has access to
            allowed_permissions: Set of permissions granted to this provider
            allowlist_patterns: List of glob patterns for files/directories that are allowed
            blocklist_patterns: List of glob patterns for files/directories that are blocked
            extension_id: Optional extension identifier
            **kwargs: Additional keyword arguments
        """
        super().__init__(
            api_key="",  # File I/O doesn't use API keys
            api_uri="",  # File I/O doesn't use API URIs
            extension_id=extension_id,
            **kwargs,
        )

        self.base_directory = Path(base_directory).resolve()

        # Set default permissions if not provided
        if allowed_permissions is None:
            self.allowed_permissions = {FileIOPermission.READ, FileIOPermission.LIST}
        else:
            self.allowed_permissions = allowed_permissions

        # Initialize allowlist and blocklist
        self.allowlist_patterns = allowlist_patterns or ["*"]  # Default to allow all
        self.blocklist_patterns = blocklist_patterns or []  # Default to block none

        # Setup commands based on permissions
        self._configure_commands()

    def _configure_commands(self) -> None:
        """Configure available commands based on permissions."""
        self.commands = {}

        # Always available
        self.commands["Get File System Info"] = self.get_filesystem_info

        # Add commands based on permissions
        if FileIOPermission.LIST in self.allowed_permissions:
            self.commands["List Directory"] = self.list_directory

        if FileIOPermission.READ in self.allowed_permissions:
            self.commands["Read File"] = self.read_file
            self.commands["Get File Info"] = self.get_file_info

        if FileIOPermission.WRITE in self.allowed_permissions:
            self.commands["Write File"] = self.write_file
            self.commands["Append To File"] = self.append_to_file
            self.commands["Copy File"] = self.copy_file

        if FileIOPermission.CREATE in self.allowed_permissions:
            self.commands["Create Directory"] = self.create_directory

        if FileIOPermission.DELETE in self.allowed_permissions:
            self.commands["Delete File"] = self.delete_file
            self.commands["Delete Directory"] = self.delete_directory

        if FileIOPermission.EXECUTE in self.allowed_permissions:
            self.commands["Execute File"] = self.execute_file

    def _check_path_permissions(
        self, path: Union[str, Path], check_exists: bool = True
    ) -> Tuple[Path, bool]:
        """
        Check if a path is allowed based on permissions, allowlist, and blocklist.

        Args:
            path: The path to check
            check_exists: Whether to check if the path exists

        Returns:
            Tuple of (resolved_path, is_allowed)
        """
        # Convert to Path object and resolve
        path_obj = Path(path)

        # If path is absolute, check if it's within base directory
        if path_obj.is_absolute():
            resolved_path = path_obj.resolve()
            if not str(resolved_path).startswith(str(self.base_directory)):
                return resolved_path, False
        else:
            # If path is relative, resolve it relative to base directory
            resolved_path = (self.base_directory / path_obj).resolve()

        # Check if path exists if required
        if check_exists and not resolved_path.exists():
            return resolved_path, False

        # Check against allowlist and blocklist
        path_str = str(resolved_path)

        # Check if path matches any blocklist pattern
        import fnmatch

        for pattern in self.blocklist_patterns:
            if fnmatch.fnmatch(path_str, pattern):
                return resolved_path, False

        # Check if path matches any allowlist pattern
        allowed = False
        for pattern in self.allowlist_patterns:
            if fnmatch.fnmatch(path_str, pattern):
                allowed = True
                break

        return resolved_path, allowed

    def _check_permission(self, permission: FileIOPermission) -> bool:
        """Check if a specific permission is granted."""
        return permission in self.allowed_permissions

    @staticmethod
    def services() -> List[str]:
        """Return a list of services provided by this provider."""
        return ["fileio", "filesystem"]

    @abstractmethod
    async def get_filesystem_info(self) -> Dict[str, Any]:
        """
        Get information about the filesystem.

        Returns:
            Dictionary with filesystem information like available space, type, etc.
        """
        pass

    @abstractmethod
    async def list_directory(self, directory: str = ".") -> List[Dict[str, Any]]:
        """
        List contents of a directory.

        Args:
            directory: Path to the directory to list (relative to base_directory or absolute)

        Returns:
            List of dictionaries with file information
        """
        pass

    @abstractmethod
    async def read_file(self, file_path: str, offset: int = 0, length: int = -1) -> str:
        """
        Read contents of a file.

        Args:
            file_path: Path to the file to read
            offset: Byte offset to start reading from
            length: Number of bytes to read, -1 for all

        Returns:
            File contents as a string
        """
        pass

    @abstractmethod
    async def write_file(
        self, file_path: str, content: str, overwrite: bool = True
    ) -> str:
        """
        Write content to a file.

        Args:
            file_path: Path to the file to write
            content: Content to write to the file
            overwrite: Whether to overwrite if file exists

        Returns:
            Success message
        """
        pass

    @abstractmethod
    async def append_to_file(self, file_path: str, content: str) -> str:
        """
        Append content to a file.

        Args:
            file_path: Path to the file to append to
            content: Content to append to the file

        Returns:
            Success message
        """
        pass

    @abstractmethod
    async def delete_file(self, file_path: str) -> str:
        """
        Delete a file.

        Args:
            file_path: Path to the file to delete

        Returns:
            Success message
        """
        pass

    @abstractmethod
    async def copy_file(
        self, source_path: str, destination_path: str, overwrite: bool = False
    ) -> str:
        """
        Copy a file from source to destination.

        Args:
            source_path: Path to the source file
            destination_path: Path to the destination file
            overwrite: Whether to overwrite if destination exists

        Returns:
            Success message
        """
        pass

    @abstractmethod
    async def create_directory(self, directory_path: str, exist_ok: bool = True) -> str:
        """
        Create a directory.

        Args:
            directory_path: Path to the directory to create
            exist_ok: Whether it's okay if the directory already exists

        Returns:
            Success message
        """
        pass

    @abstractmethod
    async def delete_directory(
        self, directory_path: str, recursive: bool = False
    ) -> str:
        """
        Delete a directory.

        Args:
            directory_path: Path to the directory to delete
            recursive: Whether to recursively delete contents

        Returns:
            Success message
        """
        pass

    @abstractmethod
    async def get_file_info(self, file_path: str) -> Dict[str, Any]:
        """
        Get information about a file.

        Args:
            file_path: Path to the file

        Returns:
            Dictionary with file information
        """
        pass

    @abstractmethod
    async def execute_file(self, file_path: str, arguments: List[str] = None) -> str:
        """
        Execute a file with given arguments.

        Args:
            file_path: Path to the file to execute
            arguments: List of arguments to pass to the executable

        Returns:
            Command output
        """
        pass

    @abstractmethod
    async def is_path_accessible(self, path: str) -> bool:
        """
        Check if a path is accessible based on permissions.

        Args:
            path: Path to check

        Returns:
            True if path is accessible, False otherwise
        """
        pass
