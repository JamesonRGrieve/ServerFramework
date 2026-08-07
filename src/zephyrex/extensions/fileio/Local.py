import asyncio
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import psutil

from zephyrex.extensions.fileio.PRV_FileIO import AbstractFileIOProvider, FileIOPermission


def _safe_open(path: str, mode: str, encoding: Optional[str] | None = None, errors: Optional[str] | None = None):
    """Open ``path`` refusing to follow a symlink at the leaf component.

    Closes a TOCTOU window between ``Path.resolve()`` (which follows
    symlinks at validation time) and ``open()`` (which follows symlinks
    again). If an attacker swaps the resolved leaf for a symlink between
    those two operations, ``O_NOFOLLOW`` causes the open to fail with
    ``ELOOP`` rather than dereferencing the new target.

    Falls back to a plain ``open`` on platforms without ``O_NOFOLLOW``
    (Windows). The fileio provider's primary deployment is Linux/macOS
    where O_NOFOLLOW is supported, and Windows lacks the symlink-by-
    default semantics that motivate the guard.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0 or os.name == "nt":
        return open(path, mode, encoding=encoding, errors=errors)
    flag_map = {
        "r": os.O_RDONLY,
        "rb": os.O_RDONLY,
        "w": os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        "wb": os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        "a": os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        "ab": os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        "r+": os.O_RDWR,
        "rb+": os.O_RDWR,
    }
    base_mode = mode.replace("t", "")
    flags = flag_map.get(base_mode)
    if flags is None:
        return open(path, mode, encoding=encoding, errors=errors)
    fd = os.open(path, flags | nofollow, 0o600)
    return os.fdopen(fd, mode, encoding=encoding, errors=errors)


class LocalFileSystem(AbstractFileIOProvider):
    """
    LocalFileSystem provider for interacting with the local file system.
    Supports Windows, Linux, and macOS.
    """

    def __init__(
        self,
        base_directory: str,
        allowed_permissions: Set[FileIOPermission] | None = None,
        allowlist_patterns: List[str] | None = None,
        blocklist_patterns: List[str] | None = None,
        extension_id: Optional[str] | None = None,
        **kwargs,
    ):
        """
        Initialize the LocalFileSystem provider.

        Args:
            base_directory: The root directory this provider has access to
            allowed_permissions: Set of permissions granted to this provider
            allowlist_patterns: List of glob patterns for files/directories that are allowed
            blocklist_patterns: List of glob patterns for files/directories that are blocked
            extension_id: Optional extension identifier
            **kwargs: Additional keyword arguments
        """
        # Detect OS
        self.os_type = self._detect_os()
        logging.info(f"Detected operating system: {self.os_type}")

        # Convert base_directory to absolute path if it's relative
        base_dir = Path(base_directory)
        if not base_dir.is_absolute():
            base_dir = base_dir.resolve()

        # Check if base directory exists, create it if not
        if not base_dir.exists():
            try:
                base_dir.mkdir(parents=True, exist_ok=True)
                logging.info(f"Created base directory: {base_dir}")
            except Exception as e:
                logging.error(f"Failed to create base directory {base_dir}: {str(e)}")

        super().__init__(
            base_directory=str(base_dir),
            allowed_permissions=allowed_permissions,
            allowlist_patterns=allowlist_patterns,
            blocklist_patterns=blocklist_patterns,
            extension_id=extension_id,
            **kwargs,
        )

    def _detect_os(self) -> str:
        """Detect the operating system."""
        if sys.platform.startswith("win"):
            return "Windows"
        elif sys.platform.startswith("darwin"):
            return "macOS"
        elif sys.platform.startswith("linux"):
            return "Linux"
        else:
            return "Unknown"

    async def get_filesystem_info(self) -> Dict[str, Any]:
        """
        Get information about the filesystem.

        Returns:
            Dictionary with filesystem information
        """
        try:
            # Get disk usage for base directory
            disk_usage = shutil.disk_usage(self.base_directory)

            # Get mounted volumes/drives
            mounted_volumes = self._get_mounted_volumes()

            # Get network drives
            network_drives = self._get_network_drives()

            # Get removable storage devices
            removable_storage = self._get_removable_storage()

            # Basic filesystem info
            info = {
                "base_directory": str(self.base_directory),
                "os_type": self.os_type,
                "disk_usage": {
                    "total": disk_usage.total,
                    "used": disk_usage.used,
                    "free": disk_usage.free,
                    "percent_used": (disk_usage.used / disk_usage.total) * 100,
                },
                "mounted_volumes": mounted_volumes,
                "network_drives": network_drives,
                "removable_storage": removable_storage,
                "permissions": [p.name for p in self.allowed_permissions],
            }

            return info
        except Exception as e:
            logging.error(f"Error getting filesystem info: {str(e)}")
            return {
                "error": f"Failed to get filesystem info: {str(e)}",
                "base_directory": str(self.base_directory),
                "os_type": self.os_type,
            }

    def _get_mounted_volumes(self) -> List[Dict[str, Any]]:
        """Get list of mounted volumes/drives."""
        mounted_volumes = []

        try:
            # Use psutil to get partitions in a cross-platform way
            partitions = psutil.disk_partitions(all=True)

            for partition in partitions:
                # Skip special filesystems on Unix-like systems
                if self.os_type in ["Linux", "macOS"] and partition.fstype in [
                    "tmpfs",
                    "devtmpfs",
                    "devfs",
                    "proc",
                    "sysfs",
                ]:
                    continue

                volume_info = {
                    "device": partition.device,
                    "mountpoint": partition.mountpoint,
                    "fstype": partition.fstype,
                }

                # Try to get usage info, may fail for some drives
                try:
                    usage = shutil.disk_usage(partition.mountpoint)
                    volume_info.update(
                        {"total": usage.total, "used": usage.used, "free": usage.free}
                    )
                except (PermissionError, FileNotFoundError):
                    pass

                mounted_volumes.append(volume_info)

        except Exception as e:
            logging.error(f"Error getting mounted volumes: {str(e)}")

        return mounted_volumes

    def _get_network_drives(self) -> List[Dict[str, Any]]:
        """Get list of network drives."""
        network_drives = []

        try:
            if self.os_type == "Windows":
                # On Windows, network drives usually have a UNC path starting with \\
                partitions = psutil.disk_partitions(all=True)
                for partition in partitions:
                    if partition.device.startswith("\\\\"):
                        network_drives.append(
                            {
                                "mountpoint": partition.mountpoint,
                                "remote_path": partition.device,
                                "fstype": partition.fstype,
                            }
                        )

            elif self.os_type == "Linux":
                # On Linux, network filesystems include nfs, cifs, smb
                partitions = psutil.disk_partitions(all=True)
                for partition in partitions:
                    if partition.fstype in ["nfs", "cifs", "smb"]:
                        network_drives.append(
                            {
                                "mountpoint": partition.mountpoint,
                                "device": partition.device,
                                "fstype": partition.fstype,
                            }
                        )

            elif self.os_type == "macOS":
                # On macOS, network volumes are usually mounted in /Volumes
                # Try to detect network volumes
                partitions = psutil.disk_partitions(all=True)
                for partition in partitions:
                    if partition.mountpoint.startswith(
                        "/Volumes/"
                    ) and partition.fstype in ["afpfs", "smbfs", "nfs"]:
                        network_drives.append(
                            {
                                "mountpoint": partition.mountpoint,
                                "device": partition.device,
                                "fstype": partition.fstype,
                            }
                        )

        except Exception as e:
            logging.error(f"Error getting network drives: {str(e)}")

        return network_drives

    def _get_removable_storage(self) -> List[Dict[str, Any]]:
        """Get list of removable storage devices."""
        removable_storage = []

        try:
            if self.os_type == "Windows":
                # On Windows, use Windows Management Instrumentation (WMI) command
                try:
                    cmd = [
                        "wmic",
                        "logicaldisk",
                        "where",
                        "drivetype=2",
                        "get",
                        "deviceid,volumename,description,size,freespace",
                    ]
                    output = subprocess.check_output(cmd, text=True)

                    lines = output.strip().split("\n")[1:]  # Skip header
                    for line in lines:
                        if line.strip():
                            parts = line.strip().split()
                            if parts:
                                device_id = parts[0]
                                # Rest might contain volume name with spaces
                                removable_storage.append(
                                    {
                                        "device": device_id,
                                        "mountpoint": device_id + "\\",
                                        "type": "removable",
                                    }
                                )
                except Exception as e:
                    logging.error(f"Error getting Windows removable storage: {str(e)}")

            elif self.os_type == "Linux":
                # On Linux, check /sys/block for removable devices
                try:
                    for device in os.listdir("/sys/block"):
                        removable_path = Path(f"/sys/block/{device}/removable")
                        if removable_path.exists():
                            with open(removable_path, "r") as f:
                                if f.read().strip() == "1":
                                    # Check if it's mounted
                                    mounted_at = None
                                    for partition in psutil.disk_partitions(all=True):
                                        if partition.device.startswith(
                                            f"/dev/{device}"
                                        ):
                                            mounted_at = partition.mountpoint
                                            break

                                    removable_storage.append(
                                        {
                                            "device": f"/dev/{device}",
                                            "mountpoint": mounted_at,  # type: ignore[dict-item]
                                            "type": "removable",
                                        }
                                    )
                except Exception as e:
                    logging.error(f"Error getting Linux removable storage: {str(e)}")

            elif self.os_type == "macOS":
                # On macOS, external volumes are usually mounted in /Volumes
                # This is a simplification; a more robust solution would use diskutil
                try:
                    for partition in psutil.disk_partitions(all=True):
                        if partition.mountpoint.startswith("/Volumes/"):
                            # Try to determine if it's removable (simplified check)
                            if (
                                "disk" in partition.device
                                and not partition.device.endswith("s1")
                            ):
                                removable_storage.append(
                                    {
                                        "device": partition.device,
                                        "mountpoint": partition.mountpoint,
                                        "type": "removable",
                                    }
                                )
                except Exception as e:
                    logging.error(f"Error getting macOS removable storage: {str(e)}")

        except Exception as e:
            logging.error(f"Error getting removable storage: {str(e)}")

        return removable_storage

    async def list_directory(self, directory: str = ".") -> List[Dict[str, Any]]:
        """
        List contents of a directory.

        Args:
            directory: Path to the directory to list

        Returns:
            List of dictionaries with file information
        """
        if not self._check_permission(FileIOPermission.LIST):
            return [{"error": "Permission denied. LIST permission required."}]

        try:
            # Check path permissions
            resolved_path, allowed = self._check_path_permissions(directory)
            if not allowed:
                return [{"error": f"Access denied to {directory}"}]

            if not resolved_path.is_dir():
                return [{"error": f"Not a directory: {directory}"}]

            # List directory contents
            result = []
            for item in resolved_path.iterdir():
                try:
                    # Get basic file info
                    item_stat = item.stat()
                    is_dir = item.is_dir()

                    # Check if item is in allowed paths
                    _, item_allowed = self._check_path_permissions(
                        item, check_exists=False
                    )
                    if not item_allowed:
                        continue

                    # Format item details
                    item_info = {
                        "name": item.name,
                        "path": (
                            str(item.relative_to(self.base_directory))
                            if self.base_directory in item.parents
                            or self.base_directory == item
                            else str(item)
                        ),
                        "type": "directory" if is_dir else "file",
                        "size": item_stat.st_size if not is_dir else None,
                        "created": datetime.fromtimestamp(
                            item_stat.st_ctime
                        ).isoformat(),
                        "modified": datetime.fromtimestamp(
                            item_stat.st_mtime
                        ).isoformat(),
                        "readable": os.access(item, os.R_OK),
                        "writable": os.access(item, os.W_OK),
                        "executable": os.access(item, os.X_OK),
                    }

                    result.append(item_info)
                except (PermissionError, FileNotFoundError):
                    # Skip files we can't access
                    continue

            return result
        except Exception as e:
            logging.error(f"Error listing directory: {str(e)}")
            return [{"error": f"Failed to list directory: {str(e)}"}]

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
        if not self._check_permission(FileIOPermission.READ):
            return "Permission denied. READ permission required."

        try:
            # Check path permissions
            resolved_path, allowed = self._check_path_permissions(file_path)
            if not allowed:
                return f"Access denied to {file_path}"

            if not resolved_path.is_file():
                return f"Not a file: {file_path}"

            # Read file content
            with _safe_open(str(resolved_path), "r", encoding="utf-8", errors="replace") as f:
                if offset > 0:
                    f.seek(offset)

                if length < 0:
                    content = f.read()
                else:
                    content = f.read(length)

            return content  # type: ignore[no-any-return]
        except UnicodeDecodeError:
            return f"File {file_path} contains binary data and cannot be read as text."
        except Exception as e:
            logging.error(f"Error reading file: {str(e)}")
            return f"Failed to read file: {str(e)}"

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
        if not self._check_permission(FileIOPermission.WRITE):
            return "Permission denied. WRITE permission required."

        try:
            # Check path permissions
            resolved_path, allowed = self._check_path_permissions(
                file_path, check_exists=False
            )
            if not allowed:
                return f"Access denied to {file_path}"

            # Check if file exists and overwrite flag
            if resolved_path.exists() and not overwrite:
                return f"File {file_path} already exists and overwrite is disabled."

            # Make sure parent directory exists
            parent_dir = resolved_path.parent
            if not parent_dir.exists():
                if self._check_permission(FileIOPermission.CREATE):
                    parent_dir.mkdir(parents=True, exist_ok=True)
                else:
                    return f"Parent directory for {file_path} does not exist and CREATE permission is required."

            # Write content to file
            with _safe_open(str(resolved_path), "w", encoding="utf-8") as f:
                f.write(content)

            return f"Successfully wrote {len(content)} characters to {file_path}"
        except Exception as e:
            logging.error(f"Error writing file: {str(e)}")
            return f"Failed to write file: {str(e)}"

    async def append_to_file(self, file_path: str, content: str) -> str:
        """
        Append content to a file.

        Args:
            file_path: Path to the file to append to
            content: Content to append to the file

        Returns:
            Success message
        """
        if not self._check_permission(FileIOPermission.WRITE):
            return "Permission denied. WRITE permission required."

        try:
            # Check path permissions
            resolved_path, allowed = self._check_path_permissions(
                file_path, check_exists=False
            )
            if not allowed:
                return f"Access denied to {file_path}"

            # Check if file exists, create if needed and allowed
            if not resolved_path.exists():
                if not self._check_permission(FileIOPermission.CREATE):
                    return f"File {file_path} does not exist and CREATE permission is required."

                # Make sure parent directory exists
                parent_dir = resolved_path.parent
                if not parent_dir.exists():
                    parent_dir.mkdir(parents=True, exist_ok=True)

            # Append content to file
            with _safe_open(str(resolved_path), "a", encoding="utf-8") as f:
                f.write(content)

            return f"Successfully appended {len(content)} characters to {file_path}"
        except Exception as e:
            logging.error(f"Error appending to file: {str(e)}")
            return f"Failed to append to file: {str(e)}"

    async def delete_file(self, file_path: str) -> str:
        """
        Delete a file.

        Args:
            file_path: Path to the file to delete

        Returns:
            Success message
        """
        if not self._check_permission(FileIOPermission.DELETE):
            return "Permission denied. DELETE permission required."

        try:
            # Check path permissions
            resolved_path, allowed = self._check_path_permissions(file_path)
            if not allowed:
                return f"Access denied to {file_path}"

            if not resolved_path.is_file():
                return f"Not a file: {file_path}"

            # Delete the file
            resolved_path.unlink()

            return f"Successfully deleted file {file_path}"
        except Exception as e:
            logging.error(f"Error deleting file: {str(e)}")
            return f"Failed to delete file: {str(e)}"

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
        if not self._check_permission(FileIOPermission.READ):
            return "Permission denied. READ permission required for source file."

        if not self._check_permission(FileIOPermission.WRITE):
            return "Permission denied. WRITE permission required for destination file."

        try:
            # Check source path permissions
            source_resolved, source_allowed = self._check_path_permissions(source_path)
            if not source_allowed:
                return f"Access denied to source file {source_path}"

            if not source_resolved.is_file():
                return f"Source is not a file: {source_path}"

            # Check destination path permissions
            dest_resolved, dest_allowed = self._check_path_permissions(
                destination_path, check_exists=False
            )
            if not dest_allowed:
                return f"Access denied to destination file {destination_path}"

            # Check if destination exists
            if dest_resolved.exists() and not overwrite:
                return f"Destination file {destination_path} already exists and overwrite is disabled."

            # Make sure parent directory exists
            parent_dir = dest_resolved.parent
            if not parent_dir.exists():
                if self._check_permission(FileIOPermission.CREATE):
                    parent_dir.mkdir(parents=True, exist_ok=True)
                else:
                    return f"Parent directory for {destination_path} does not exist and CREATE permission is required."

            # Copy the file. ``follow_symlinks=False`` means a symlink
            # planted at either resolved path between check and copy is
            # treated as a symlink (not dereferenced); the resolved path
            # was containment-checked, so we know it points inside the
            # base directory.
            shutil.copy2(source_resolved, dest_resolved, follow_symlinks=False)

            return f"Successfully copied {source_path} to {destination_path}"
        except Exception as e:
            logging.error(f"Error copying file: {str(e)}")
            return f"Failed to copy file: {str(e)}"

    async def create_directory(self, directory_path: str, exist_ok: bool = True) -> str:
        """
        Create a directory.

        Args:
            directory_path: Path to the directory to create
            exist_ok: Whether it's okay if the directory already exists

        Returns:
            Success message
        """
        if not self._check_permission(FileIOPermission.CREATE):
            return "Permission denied. CREATE permission required."

        try:
            # Check path permissions
            resolved_path, allowed = self._check_path_permissions(
                directory_path, check_exists=False
            )
            if not allowed:
                return f"Access denied to {directory_path}"

            # Check if directory exists
            if resolved_path.exists():
                if not exist_ok:
                    return f"Directory {directory_path} already exists."
                if not resolved_path.is_dir():
                    return f"Path exists but is not a directory: {directory_path}"
                return f"Directory {directory_path} already exists."

            # Create directory
            resolved_path.mkdir(parents=True, exist_ok=exist_ok)

            return f"Successfully created directory {directory_path}"
        except Exception as e:
            logging.error(f"Error creating directory: {str(e)}")
            return f"Failed to create directory: {str(e)}"

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
        if not self._check_permission(FileIOPermission.DELETE):
            return "Permission denied. DELETE permission required."

        try:
            # Check path permissions
            resolved_path, allowed = self._check_path_permissions(directory_path)
            if not allowed:
                return f"Access denied to {directory_path}"

            if not resolved_path.is_dir():
                return f"Not a directory: {directory_path}"

            # Check if directory is empty
            if not recursive and any(resolved_path.iterdir()):
                return f"Directory {directory_path} is not empty. Use recursive=True to delete non-empty directories."

            # Delete directory
            if recursive:
                shutil.rmtree(resolved_path)
            else:
                resolved_path.rmdir()

            return f"Successfully deleted directory {directory_path}"
        except Exception as e:
            logging.error(f"Error deleting directory: {str(e)}")
            return f"Failed to delete directory: {str(e)}"

    async def get_file_info(self, file_path: str) -> Dict[str, Any]:
        """
        Get information about a file.

        Args:
            file_path: Path to the file

        Returns:
            Dictionary with file information
        """
        if not self._check_permission(FileIOPermission.READ):
            return {"error": "Permission denied. READ permission required."}

        try:
            # Check path permissions
            resolved_path, allowed = self._check_path_permissions(file_path)
            if not allowed:
                return {"error": f"Access denied to {file_path}"}

            if not resolved_path.exists():
                return {"error": f"Path does not exist: {file_path}"}

            # Get file stats
            file_stat = resolved_path.stat()
            is_dir = resolved_path.is_dir()

            # Build info dictionary
            info = {
                "name": resolved_path.name,
                "path": (
                    str(resolved_path.relative_to(self.base_directory))
                    if self.base_directory in resolved_path.parents
                    or self.base_directory == resolved_path
                    else str(resolved_path)
                ),
                "type": "directory" if is_dir else "file",
                "size": file_stat.st_size if not is_dir else None,
                "created": datetime.fromtimestamp(file_stat.st_ctime).isoformat(),
                "modified": datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
                "accessed": datetime.fromtimestamp(file_stat.st_atime).isoformat(),
                "readable": os.access(resolved_path, os.R_OK),
                "writable": os.access(resolved_path, os.W_OK),
                "executable": os.access(resolved_path, os.X_OK),
            }

            # Add file-specific information
            if not is_dir:
                info["extension"] = (
                    resolved_path.suffix.lstrip(".") if resolved_path.suffix else ""
                )

                # Try to determine mime type
                import mimetypes

                mime_type, _ = mimetypes.guess_type(str(resolved_path))
                info["mime_type"] = mime_type or "application/octet-stream"

                # Check if it's a text file (simple check)
                try:
                    with open(
                        resolved_path, "r", encoding="utf-8", errors="replace"
                    ) as f:
                        f.read(1024)  # Read a bit of the file to see if it's text
                    info["text_file"] = True
                except:
                    info["text_file"] = False

            return info
        except Exception as e:
            logging.error(f"Error getting file info: {str(e)}")
            return {"error": f"Failed to get file info: {str(e)}"}

    async def execute_file(self, file_path: str, arguments: List[str] | None = None) -> str:
        """
        Execute a file with given arguments.

        Args:
            file_path: Path to the file to execute
            arguments: List of arguments to pass to the executable

        Returns:
            Command output
        """
        if not self._check_permission(FileIOPermission.EXECUTE):
            return "Permission denied. EXECUTE permission required."

        try:
            # Check path permissions
            resolved_path, allowed = self._check_path_permissions(file_path)
            if not allowed:
                return f"Access denied to {file_path}"

            if not resolved_path.exists():
                return f"File does not exist: {file_path}"

            if not os.access(resolved_path, os.X_OK):
                return f"File is not executable: {file_path}"

            # Build command
            cmd = [str(resolved_path)]
            if arguments:
                cmd.extend(arguments)

            # Execute command
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            # Prepare result
            result = f"Return code: {process.returncode}\n\n"

            if stdout:
                result += (
                    f"Standard output:\n{stdout.decode('utf-8', errors='replace')}\n\n"
                )

            if stderr:
                result += f"Standard error:\n{stderr.decode('utf-8', errors='replace')}"

            return result.strip()
        except Exception as e:
            logging.error(f"Error executing file: {str(e)}")
            return f"Failed to execute file: {str(e)}"

    async def is_path_accessible(self, path: str) -> bool:
        """
        Check if a path is accessible based on permissions.

        Args:
            path: Path to check

        Returns:
            True if path is accessible, False otherwise
        """
        try:
            _, allowed = self._check_path_permissions(path, check_exists=False)
            return allowed  # type: ignore[no-any-return]
        except Exception:
            return False
