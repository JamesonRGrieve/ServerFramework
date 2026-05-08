"""
Dependencies.py - Module to analyze and resolve dependencies between Python modules
and to manage system and Python package dependencies.
"""

import os
import platform
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from enum import Enum
from http.client import HTTPException
from typing import Any, Dict, List, Optional, Tuple, Type

import httpx

import semver
import stringcase
from pydantic import BaseModel, Field

from serverframework.lib.Logging import logger

try:
    from importlib.metadata import PackageNotFoundError, version
except ImportError:
    from importlib_metadata import version, PackageNotFoundError

# Import for better OS detection
try:
    import distro

    HAS_DISTRO = True
except ImportError:
    HAS_DISTRO = False

# Import for dependency resolution
try:
    import resolvelib
    from resolvelib.providers import AbstractExtensionProvider
    from resolvelib.resolvers import RequirementInformation, Resolver

    HAS_RESOLVELIB = True
except ImportError:
    HAS_RESOLVELIB = False

    # Create stub classes to avoid errors when importing
    class AbstractExtensionProvider:
        pass

    class Resolver:
        def __init__(self, provider, reporter=None):
            pass


# Common utility function for executing shell commands
def execute_command(
    cmd: List[str], check: bool = False, capture_output: bool = True, text: bool = True
) -> Tuple[bool, str, str]:
    """
    Execute a shell command with standardized error handling.

    Args:
        cmd: Command as list of string arguments
        check: Whether to raise an exception on failure
        capture_output: Whether to capture stdout/stderr
        text: Whether to return stdout/stderr as text

    Returns:
        Tuple of (success, stdout, stderr)
    """
    try:
        result = subprocess.run(
            cmd, capture_output=capture_output, text=text, check=check
        )
        return (
            result.returncode == 0,
            result.stdout if hasattr(result, "stdout") else "",
            result.stderr if hasattr(result, "stderr") else "",
        )
    except FileNotFoundError:
        return False, "", f"Command not found: {cmd[0]}"
    except subprocess.SubprocessError as e:
        return False, "", str(e)
    except Exception as e:
        return False, "", str(e)


class Dependency(BaseModel):
    """Base class for all types of dependencies."""

    model_config = {"extra": "ignore", "arbitrary_types_allowed": True}

    name: str = Field(..., description="Name of the dependency.")
    friendly_name: str = Field(..., description="The friendly name of the dependency.")
    optional: bool = Field(False, description="Whether this dependency is optional.")
    reason: str = Field(
        "None specified.",
        description="The reason this dependency is required and what it adds if optional.",
    )
    semver: Optional[str] = Field(
        None, description="Semantic version requirement (e.g., '>=1.0.0')"
    )


class SystemPackageMapping(BaseModel):
    """Package mapping for a specific package manager."""

    model_config = {"extra": "ignore"}

    manager: str
    package_name: str


class SYS_Dependency(Dependency):
    """
    Base class for system dependencies across different package managers.
    """

    package_mappings: List[SystemPackageMapping] = Field(
        default_factory=list,
        description="Package mappings for different package managers",
    )

    def is_satisfied(self) -> bool:
        """
        Check if the dependency is satisfied on the current OS.

        Returns:
            bool: True if the dependency is installed by any available package manager
        """
        available_managers = get_available_package_managers()

        if not available_managers:
            logger.warning(
                f"No supported package managers found for dependency {self.name}"
            )
            return self.optional

        # Check if any of our package mappings match available package managers
        for mapping in self.package_mappings:
            if mapping.manager in available_managers:
                manager_cls = available_managers[mapping.manager]
                if manager_cls.check_package_installed(mapping.package_name):
                    return True

        # No matching package found or none installed
        return self.optional


class PIP_Dependency(Dependency):
    """
    Represents a dependency on a Python package.
    """

    def is_satisfied(self) -> bool:
        """
        Check if this PIP dependency is satisfied.

        Returns:
            bool: True if the dependency is installed with correct version
        """
        try:
            # Check if package is installed
            installed_version = version(self.name)

            # If semver is specified, check version
            if self.semver:
                try:

                    # Normalize version to semver format if needed
                    # Many packages use versions like "8.2" instead of "8.2.0"
                    version_parts = installed_version.split(".")
                    if len(version_parts) == 2:
                        # Convert "8.2" to "8.2.0"
                        normalized_version = f"{version_parts[0]}.{version_parts[1]}.0"
                    elif len(version_parts) == 1:
                        # Convert "8" to "8.0.0"
                        normalized_version = f"{version_parts[0]}.0.0"
                    else:
                        normalized_version = installed_version

                    # Use the newer semver API to avoid deprecation warning
                    version_obj = semver.Version.parse(normalized_version)
                    return version_obj.match(self.semver)
                except (ValueError, ImportError, AttributeError) as e:
                    logger.warning(
                        f"Cannot verify version for {self.name}: requirement '{self.semver}' - {str(e)}"
                    )
                    return True

            return True
        except PackageNotFoundError:
            return self.optional
        except Exception as e:
            logger.error(f"Error checking PIP dependency {self.name}: {str(e)}")
            return self.optional


class BREW_Dependency(SYS_Dependency):
    """
    Represents a dependency on a Homebrew package (macOS).
    For backward compatibility.
    """

    brew_package: Optional[str] = Field(None, description="Homebrew package name")

    def __init__(self, **data):
        super().__init__(**data)
        # Ensure brew_package is set from name for backward compatibility
        if self.brew_package is None:
            self.brew_package = self.name


class WINGET_Dependency(SYS_Dependency):
    """
    Represents a dependency on a WinGet package (Windows).
    For backward compatibility.
    """

    winget_package: Optional[str] = Field(None, description="WinGet package name")

    def __init__(self, **data):
        super().__init__(**data)
        # Ensure winget_package is set from name for backward compatibility
        if self.winget_package is None:
            self.winget_package = self.name


def check_system_dependencies(dependencies: List[SYS_Dependency]) -> Dict[str, bool]:
    """
    Check if system dependencies are satisfied.

    Args:
        dependencies: List of SYS_Dependency objects

    Returns:
        Dict mapping dependency names to whether they are satisfied
    """
    result = {}
    for dep in dependencies:
        # Call the method directly on the class to avoid Pydantic __getattr__ issues
        result[dep.name] = SYS_Dependency.is_satisfied(dep)
        if not result[dep.name] and not dep.optional:
            logger.warning(f"Required system dependency '{dep.name}' is not installed")

    return result


# Helper function for version checking
def check_version_compatibility(
    installed_version: str, required_version: Optional[str]
) -> bool:
    """
    Check if an installed version is compatible with a requirement.

    Args:
        installed_version: Currently installed version
        required_version: Required version constraint (e.g., ">=1.0.0")

    Returns:
        bool: True if the version is compatible, False otherwise
    """
    if not required_version:
        return True

    try:
        import semver

        # Normalize version to semver format if needed
        # Many packages use versions like "8.2" instead of "8.2.0"
        version_parts = installed_version.split(".")
        if len(version_parts) == 2:
            # Convert "8.2" to "8.2.0"
            normalized_version = f"{version_parts[0]}.{version_parts[1]}.0"
        elif len(version_parts) == 1:
            # Convert "8" to "8.0.0"
            normalized_version = f"{version_parts[0]}.0.0"
        else:
            normalized_version = installed_version

        # Use the newer semver API to avoid deprecation warning
        version_obj = semver.Version.parse(normalized_version)
        return version_obj.match(required_version)
    except (ImportError, ValueError, AttributeError):
        logger.warning(
            f"Cannot verify version: {installed_version} against requirement '{required_version}'"
        )
        return True  # Assume compatible if we can't check


class OSType(str, Enum):
    """Enumeration of supported operating systems."""

    DEBIAN = "debian"
    UBUNTU = "ubuntu"
    FEDORA = "fedora"
    REDHAT = "redhat"
    MACOS = "macos"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


def get_os_type() -> OSType:
    """
    Detect the operating system type.
    Uses the distro package if available for more robust detection.

    Returns:
        OSType: Detected operating system
    """
    system = platform.system().lower()

    if system == "linux":
        # Use distro package if available for more robust detection
        if HAS_DISTRO:
            distro_id = distro.id()
            distro_like = distro.like() or ""

            if distro_id == "ubuntu" or "ubuntu" in distro_like:
                return OSType.UBUNTU
            elif distro_id == "debian" or "debian" in distro_like:
                return OSType.DEBIAN
            elif distro_id == "fedora" or "fedora" in distro_like:
                return OSType.FEDORA
            elif distro_id == "rhel" or distro_id == "centos" or "rhel" in distro_like:
                return OSType.REDHAT
            else:
                return OSType.UNKNOWN

        # Fall back to file-based detection if distro is not available
        if os.path.exists("/etc/debian_version"):
            with open("/etc/debian_version", "r") as f:
                version = f.read().strip()
            if os.path.exists("/etc/lsb-release"):
                with open("/etc/lsb-release", "r") as f:
                    if "ubuntu" in f.read().lower():
                        return OSType.UBUNTU
            return OSType.DEBIAN
        elif os.path.exists("/etc/fedora-release"):
            return OSType.FEDORA
        elif os.path.exists("/etc/redhat-release"):
            return OSType.REDHAT
        else:
            # If no specific Linux distribution is identified, return UNKNOWN
            return OSType.UNKNOWN
    elif system == "darwin":
        return OSType.MACOS
    elif system == "windows":
        return OSType.WINDOWS
    else:
        return OSType.UNKNOWN


class PackageManager(ABC):
    """Abstract base class for package managers."""

    # Dictionary mapping commands to their respective arguments
    COMMANDS = {}

    # List of supported operating systems
    SUPPORTED_OS = []

    @classmethod
    def _build_command(cls, command_type: str, *args) -> List[str]:
        """
        Build a command using the COMMANDS template.

        Args:
            command_type: Type of command (check, install, etc.)
            *args: Arguments to include in the command

        Returns:
            List[str]: Complete command
        """
        if command_type not in cls.COMMANDS:
            raise ValueError(f"Unsupported command type: {command_type}")

        command_template = cls.COMMANDS[command_type]
        command = []

        # Process each part of the command template
        for part in command_template:
            if part == "%args%":
                # Replace with all arguments
                command.extend(args)
            elif part.startswith("%arg"):
                # Replace with a specific argument index
                try:
                    index = int(part[4:-1])  # Extract index from %argN%
                    if index < len(args):
                        command.append(args[index])
                except (ValueError, IndexError):
                    # Skip invalid argument references
                    pass
            else:
                # Add the template part as-is
                command.append(part)

        return command

    @classmethod
    def _execute(
        cls, command_type: str, *args, sudo: bool = False
    ) -> Tuple[bool, str, str]:
        """
        Build and execute a command.

        Args:
            command_type: Type of command (check, install, etc.)
            *args: Arguments to include in the command
            sudo: Whether to prepend sudo to the command

        Returns:
            Tuple[bool, str, str]: (success, stdout, stderr)
        """
        cmd = cls._build_command(command_type, *args)

        # Add sudo if requested and not already present
        if sudo and cmd and cmd[0] != "sudo":
            cmd = ["sudo"] + cmd

        return execute_command(cmd)

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Check if this package manager is available on the system."""
        pass

    @classmethod
    def check_package_installed(cls, package_name: str) -> bool:
        """
        Check if a package is installed.

        Args:
            package_name: Name of the package to check

        Returns:
            bool: True if the package is installed
        """
        success, stdout, _ = cls._execute("check", package_name)
        return success and cls._is_package_in_output(package_name, stdout)

    @classmethod
    def _is_package_in_output(cls, package_name: str, output: str) -> bool:
        """
        Check if a package is present in command output.
        Default implementation just checks if the name is in the output.
        Override for package managers with specific output formats.

        Args:
            package_name: Name of the package
            output: Command output to check

        Returns:
            bool: True if the package is found in the output
        """
        return package_name in output

    @classmethod
    def install_package(cls, package_name: str) -> bool:
        """
        Install a package.

        Args:
            package_name: Name of the package to install

        Returns:
            bool: True if installation was successful
        """
        success, _, stderr = cls._execute("install", package_name, sudo=True)
        if not success:
            logger.error(f"Failed to install {package_name}: {stderr}")
        return success

    @classmethod
    def batch_install_packages(cls, package_names: List[str]) -> Dict[str, bool]:
        """
        Install multiple packages.
        Default implementation attempts batch installation first,
        then falls back to individual installation if needed.

        Args:
            package_names: List of package names to install

        Returns:
            Dict[str, bool]: Mapping of package names to installation success
        """
        if not package_names:
            return {}

        results = {}

        # Try batch installation if supported
        if hasattr(cls, "SUPPORTS_BATCH") and cls.SUPPORTS_BATCH:
            success, _, _ = cls._execute("batch_install", *package_names, sudo=True)

            if success:
                # All packages installed successfully
                return {pkg: True for pkg in package_names}

        # Fall back to individual installation
        for pkg_name in package_names:
            results[pkg_name] = cls.install_package(pkg_name)

        return results

    @classmethod
    def supports_os(cls, os_type: OSType) -> bool:
        """
        Check if this package manager supports the given OS.

        Args:
            os_type: Operating system to check

        Returns:
            bool: True if the OS is supported
        """
        return os_type in cls.SUPPORTED_OS


class APTPackageManager(PackageManager):
    """Package manager for APT (Debian, Ubuntu)."""

    SUPPORTED_OS = [OSType.DEBIAN, OSType.UBUNTU]
    SUPPORTS_BATCH = True

    COMMANDS = {
        "version": ["apt-get", "--version"],
        "check": ["dpkg-query", "-W", "-f=${Status}", "%arg0%"],
        "install": ["apt-get", "install", "-y", "%arg0%"],
        "batch_install": ["apt-get", "install", "-y", "%args%"],
        "update": ["apt-get", "update", "-qq"],
    }

    @classmethod
    def is_available(cls) -> bool:
        success, _, _ = cls._execute("version")
        return success

    @classmethod
    def _is_package_in_output(cls, package_name: str, output: str) -> bool:
        return "install ok installed" in output

    @classmethod
    def batch_install_packages(cls, package_names: List[str]) -> Dict[str, bool]:
        if not package_names:
            return {}

        results = {}

        # Update package lists first
        cls._execute("update", sudo=True)

        # Try batch installation
        success, _, _ = cls._execute("batch_install", *package_names, sudo=True)

        if success:
            # All packages installed successfully
            return {pkg: True for pkg in package_names}
        else:
            # Fall back to individual installation
            for pkg_name in package_names:
                results[pkg_name] = cls.install_package(pkg_name)

        return results


class SnapPackageManager(PackageManager):
    """Package manager for Snap (Ubuntu, other Linux)."""

    SUPPORTED_OS = [OSType.UBUNTU, OSType.DEBIAN, OSType.FEDORA, OSType.REDHAT]

    COMMANDS = {
        "version": ["snap", "--version"],
        "check": ["snap", "list", "%arg0%"],
        "install": ["snap", "install", "%arg0%"],
    }

    @classmethod
    def is_available(cls) -> bool:
        success, _, _ = cls._execute("version")
        return success


class BrewPackageManager(PackageManager):
    """Package manager for Homebrew (macOS, Linux)."""

    SUPPORTED_OS = [OSType.MACOS, OSType.DEBIAN, OSType.UBUNTU]
    SUPPORTS_BATCH = True

    COMMANDS = {
        "version": ["brew", "--version"],
        "check": ["brew", "list", "--formula", "%arg0%"],
        "install": ["brew", "install", "%arg0%"],
        "batch_install": ["brew", "install", "%args%"],
    }

    @classmethod
    def is_available(cls) -> bool:
        success, _, _ = cls._execute("version")
        return success


class WinGetPackageManager(PackageManager):
    """Package manager for WinGet (Windows)."""

    SUPPORTED_OS = [OSType.WINDOWS]

    COMMANDS = {
        "version": ["winget", "--version"],
        "check": ["winget", "list", "--id", "%arg0%"],
        "install": [
            "winget",
            "install",
            "--id",
            "%arg0%",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ],
    }

    @classmethod
    def is_available(cls) -> bool:
        success, _, _ = cls._execute("version")
        return success


class ChocolateyPackageManager(PackageManager):
    """Package manager for Chocolatey (Windows)."""

    SUPPORTED_OS = [OSType.WINDOWS]
    SUPPORTS_BATCH = True

    COMMANDS = {
        "version": ["choco", "--version"],
        "check": ["choco", "list", "--local-only", "%arg0%"],
        "install": ["choco", "install", "%arg0%", "-y"],
        "batch_install": ["choco", "install", "%args%", "-y"],
    }

    @classmethod
    def is_available(cls) -> bool:
        success, _, _ = cls._execute("version")
        return success

    @classmethod
    def _is_package_in_output(cls, package_name: str, output: str) -> bool:
        return f"{package_name} " in output.lower()


# Registry of available package managers
PACKAGE_MANAGERS = {
    "apt": APTPackageManager,
    "snap": SnapPackageManager,
    "brew": BrewPackageManager,
    "winget": WinGetPackageManager,
    "chocolatey": ChocolateyPackageManager,
}


def get_available_package_managers() -> Dict[str, Type[PackageManager]]:
    """
    Get all available package managers for the current system.

    Returns:
        Dict mapping package manager names to their classes
    """
    os_type = get_os_type()
    available_managers = {}

    for name, manager_cls in PACKAGE_MANAGERS.items():
        if manager_cls.supports_os(os_type) and manager_cls.is_available():
            available_managers[name] = manager_cls

    return available_managers


class DependencyFactory:
    """Factory class for creating dependencies with different configurations."""

    @staticmethod
    def create_system_dependency(
        name: str,
        friendly_name: Optional[str] = None,
        optional: bool = False,
        reason: str = "None specified.",
        **package_mappings,
    ) -> SYS_Dependency:
        """
        Create a system dependency with mappings for multiple package managers.

        Args:
            name: Name of the dependency
            friendly_name: User-friendly name (defaults to name if not provided)
            optional: Whether the dependency is optional
            reason: Reason for the dependency
            **package_mappings: Package names mapped to package manager names
                (e.g., apt="package-name", brew="brew-package")

        Returns:
            SYS_Dependency: Configured system dependency
        """
        mappings = []

        for manager, pkg_name in package_mappings.items():
            if pkg_name:
                mappings.append(
                    SystemPackageMapping(manager=manager, package_name=pkg_name)
                )

        return SYS_Dependency(
            name=name,
            friendly_name=friendly_name or name,
            optional=optional,
            reason=reason,
            package_mappings=mappings,
        )

    @staticmethod
    def create_pip_dependency(
        name: str,
        friendly_name: Optional[str] = None,
        optional: bool = False,
        reason: str = "None specified.",
        semver: Optional[str] = None,
    ) -> PIP_Dependency:
        """
        Create a PIP dependency.

        Args:
            name: Name of the dependency
            friendly_name: User-friendly name (defaults to name if not provided)
            optional: Whether the dependency is optional
            reason: Reason for the dependency
            semver: Semantic version requirement

        Returns:
            PIP_Dependency: Configured PIP dependency
        """
        return PIP_Dependency(
            name=name,
            friendly_name=friendly_name or name,
            optional=optional,
            reason=reason,
            semver=semver,
        )


# Static methods for SYS_Dependency to make creation more convenient
@staticmethod
def for_apt(name: str, package: str, **kwargs) -> "SYS_Dependency":
    """Create a dependency for APT package manager."""
    return DependencyFactory.create_system_dependency(name=name, apt=package, **kwargs)


@staticmethod
def for_brew(name: str, package: str, **kwargs) -> "SYS_Dependency":
    """Create a dependency for Homebrew package manager."""
    return DependencyFactory.create_system_dependency(name=name, brew=package, **kwargs)


@staticmethod
def for_winget(name: str, package: str, **kwargs) -> "SYS_Dependency":
    """Create a dependency for WinGet package manager."""
    return DependencyFactory.create_system_dependency(
        name=name, winget=package, **kwargs
    )


@staticmethod
def for_chocolatey(name: str, package: str, **kwargs) -> "SYS_Dependency":
    """Create a dependency for Chocolatey package manager."""
    return DependencyFactory.create_system_dependency(
        name=name, chocolatey=package, **kwargs
    )


@staticmethod
def for_snap(name: str, package: str, **kwargs) -> "SYS_Dependency":
    """Create a dependency for Snap package manager."""
    return DependencyFactory.create_system_dependency(name=name, snap=package, **kwargs)


@staticmethod
def for_all_platforms(
    name: str,
    apt_pkg: Optional[str] = None,
    brew_pkg: Optional[str] = None,
    winget_pkg: Optional[str] = None,
    chocolatey_pkg: Optional[str] = None,
    snap_pkg: Optional[str] = None,
    **kwargs,
) -> "SYS_Dependency":
    """Create a dependency with mappings for all supported platforms."""
    return DependencyFactory.create_system_dependency(
        name=name,
        apt=apt_pkg,
        brew=brew_pkg,
        winget=winget_pkg,
        chocolatey=chocolatey_pkg,
        snap=snap_pkg,
        **kwargs,
    )


# Add static methods to SYS_Dependency class
SYS_Dependency.for_apt = for_apt
SYS_Dependency.for_brew = for_brew
SYS_Dependency.for_winget = for_winget
SYS_Dependency.for_chocolatey = for_chocolatey
SYS_Dependency.for_snap = for_snap
SYS_Dependency.for_all_platforms = for_all_platforms


def install_system_dependencies(
    dependencies: List[SYS_Dependency], only_missing: bool = True
) -> Dict[str, bool]:
    """
    Install system dependencies based on the detected operating system.
    Uses dependency resolution to determine installation order.

    Args:
        dependencies: List of SYS_Dependency objects
        only_missing: Only install dependencies that are not already satisfied

    Returns:
        Dict mapping dependency names to whether they were successfully installed
    """
    if not dependencies:
        return {}

    # Convert list to dictionary for dependency resolution
    deps_dict = {dep.name: dep for dep in dependencies}

    # Resolve dependencies to get proper installation order
    try:
        resolved_deps = resolve_dependencies(deps_dict)
        # Convert back to list but in resolved order
        dependencies = list(resolved_deps.values())
        logger.debug(
            f"Resolved dependencies in order: {[dep.name for dep in dependencies]}"
        )
    except DependencyResolutionError as e:
        logger.warning(f"Dependency resolution failed: {str(e)}. Using original order.")

    os_type = get_os_type()

    # Handle different OS types
    if os_type == OSType.DEBIAN or os_type == OSType.UBUNTU:
        return _install_apt_dependencies(dependencies, only_missing)
    elif os_type == OSType.MACOS:
        return _install_brew_dependencies(dependencies, only_missing)
    elif os_type == OSType.WINDOWS:
        return _install_winget_dependencies(dependencies, only_missing)
    else:
        logger.warning(
            f"Unsupported OS type for system dependency installation: {os_type}"
        )
        return {dep.name: False for dep in dependencies}


def _install_apt_dependencies(
    dependencies: List[SYS_Dependency], only_missing: bool = True
) -> Dict[str, bool]:
    """
    Install APT dependencies on Debian-based systems.

    Args:
        dependencies: List of SYS_Dependency objects
        only_missing: Only install dependencies that are not already satisfied

    Returns:
        Dict mapping dependency names to whether they were successfully installed
    """
    # Filter dependencies with apt packages
    apt_dependencies = []
    for dep in dependencies:
        apt_pkg = None
        # First try to find via package_mappings
        for mapping in dep.package_mappings:
            if mapping.manager == "apt":
                apt_pkg = mapping.package_name
                break

        # If not found and it has an apt_package attribute, use that
        if (
            apt_pkg is None
            and hasattr(dep, "apt_package")
            and dep.apt_package is not None
        ):
            apt_pkg = dep.apt_package

        if apt_pkg is not None:
            apt_dependencies.append((dep, apt_pkg))

    if not apt_dependencies:
        return {}

    # Check if we have sudo privileges
    try:
        subprocess.run(["sudo", "-n", "true"], capture_output=True, check=False)
        has_sudo = True
    except (FileNotFoundError, subprocess.SubprocessError):
        has_sudo = False

    # Determine which dependencies to install
    to_install = []
    deps_to_check = [dep for dep, _ in apt_dependencies]

    if only_missing:
        dependency_status = check_system_dependencies(deps_to_check)
        to_install = [
            (dep, pkg_name)
            for dep, pkg_name in apt_dependencies
            if not dependency_status.get(dep.name, False)
        ]
    else:
        to_install = apt_dependencies

    if not to_install:
        return {}

    # Install dependencies
    result = {}
    for dep, _ in apt_dependencies:
        result[dep.name] = False  # Initialize all as False

    if has_sudo:
        try:
            # Update package lists
            subprocess.run(["sudo", "apt-get", "update", "-qq"], check=True)

            # Install all packages in one command for efficiency
            pkg_names = [pkg_name for _, pkg_name in to_install]
            install_cmd = ["sudo", "apt-get", "install", "-y"] + pkg_names
            install_result = subprocess.run(
                install_cmd, capture_output=True, text=True, check=False
            )

            if install_result.returncode == 0:
                for dep, pkg_name in to_install:
                    result[dep.name] = True
                    logger.debug(f"Successfully installed APT package: {pkg_name}")
            else:
                # If batch install fails, try individually
                for dep, pkg_name in to_install:
                    pkg_result = subprocess.run(
                        ["sudo", "apt-get", "install", "-y", pkg_name],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    result[dep.name] = pkg_result.returncode == 0
                    if result[dep.name]:
                        logger.debug(f"Successfully installed APT package: {pkg_name}")
                    else:
                        logger.error(
                            f"Failed to install APT package {pkg_name}: {pkg_result.stderr}"
                        )
        except Exception as e:
            logger.error(f"Error installing APT dependencies: {str(e)}")
    else:
        logger.warning("Cannot install APT dependencies without sudo privileges")

    return result


def _install_brew_dependencies(
    dependencies: List[SYS_Dependency], only_missing: bool = True
) -> Dict[str, bool]:
    """
    Install Homebrew dependencies on macOS systems.

    Args:
        dependencies: List of SYS_Dependency objects
        only_missing: Only install dependencies that are not already satisfied

    Returns:
        Dict mapping dependency names to whether they were successfully installed
    """
    # Filter dependencies with brew packages
    brew_dependencies = []
    for dep in dependencies:
        brew_pkg = None
        # First try to find via package_mappings
        for mapping in dep.package_mappings:
            if mapping.manager == "brew":
                brew_pkg = mapping.package_name
                break

        # If not found and it's a BREW_Dependency, try the brew_package attribute
        if (
            brew_pkg is None
            and hasattr(dep, "brew_package")
            and dep.brew_package is not None
        ):
            brew_pkg = dep.brew_package

        if brew_pkg is not None:
            brew_dependencies.append((dep, brew_pkg))

    if not brew_dependencies:
        return {}

    # Determine which dependencies to install
    to_install = []
    deps_to_check = [dep for dep, _ in brew_dependencies]

    if only_missing:
        dependency_status = check_system_dependencies(deps_to_check)
        to_install = [
            (dep, pkg_name)
            for dep, pkg_name in brew_dependencies
            if not dependency_status.get(dep.name, False)
        ]
    else:
        to_install = brew_dependencies

    if not to_install:
        return {}

    # Install dependencies
    result = {}
    for dep, _ in brew_dependencies:
        result[dep.name] = False  # Initialize all as False

    try:
        # Try to install all packages in one command for efficiency
        pkg_names = [pkg_name for _, pkg_name in to_install]
        install_cmd = ["brew", "install"] + pkg_names
        install_result = subprocess.run(
            install_cmd, capture_output=True, text=True, check=False
        )

        if install_result.returncode == 0:
            for dep, pkg_name in to_install:
                result[dep.name] = True
                logger.debug(f"Successfully installed Homebrew package: {pkg_name}")
        else:
            # If batch install fails, try individually
            for dep, pkg_name in to_install:
                pkg_result = subprocess.run(
                    ["brew", "install", pkg_name],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                result[dep.name] = pkg_result.returncode == 0
                if result[dep.name]:
                    logger.debug(f"Successfully installed Homebrew package: {pkg_name}")
                else:
                    logger.error(
                        f"Failed to install Homebrew package {pkg_name}: {pkg_result.stderr}"
                    )
    except FileNotFoundError:
        logger.error("Homebrew (brew) not found; cannot install dependencies")
    except Exception as e:
        logger.error(f"Error installing Homebrew dependencies: {str(e)}")

    return result


def _install_winget_dependencies(
    dependencies: List[SYS_Dependency], only_missing: bool = True
) -> Dict[str, bool]:
    """
    Install WinGet dependencies on Windows systems.

    Args:
        dependencies: List of SYS_Dependency objects
        only_missing: Only install dependencies that are not already satisfied

    Returns:
        Dict mapping dependency names to whether they were successfully installed
    """
    # Filter dependencies with winget packages
    winget_dependencies = []
    for dep in dependencies:
        winget_pkg = None
        # First try to find via package_mappings
        for mapping in dep.package_mappings:
            if mapping.manager == "winget":
                winget_pkg = mapping.package_name
                break

        # If not found and it's a WINGET_Dependency, try the winget_package attribute
        if (
            winget_pkg is None
            and hasattr(dep, "winget_package")
            and dep.winget_package is not None
        ):
            winget_pkg = dep.winget_package

        if winget_pkg is not None:
            winget_dependencies.append((dep, winget_pkg))

    if not winget_dependencies:
        return {}

    # Determine which dependencies to install
    to_install = []
    deps_to_check = [dep for dep, _ in winget_dependencies]

    if only_missing:
        dependency_status = check_system_dependencies(deps_to_check)
        to_install = [
            (dep, pkg_name)
            for dep, pkg_name in winget_dependencies
            if not dependency_status.get(dep.name, False)
        ]
    else:
        to_install = winget_dependencies

    if not to_install:
        return {}

    # Install dependencies
    result = {}
    for dep, _ in winget_dependencies:
        result[dep.name] = False  # Initialize all as False

    try:
        # WinGet doesn't support installing multiple packages in one command efficiently
        # So we'll install them one by one
        for dep, pkg_name in to_install:
            # Note: --accept-source-agreements is needed to bypass prompts
            pkg_result = subprocess.run(
                [
                    "winget",
                    "install",
                    "--id",
                    pkg_name,
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            result[dep.name] = pkg_result.returncode == 0
            if result[dep.name]:
                logger.debug(f"Successfully installed WinGet package: {pkg_name}")
            else:
                logger.error(
                    f"Failed to install WinGet package {pkg_name}: {pkg_result.stderr}"
                )
    except FileNotFoundError:
        logger.error("WinGet not found; cannot install dependencies")
    except Exception as e:
        logger.error(f"Error installing WinGet dependencies: {str(e)}")

    return result


def check_pip_dependencies(dependencies: List[PIP_Dependency]) -> Dict[str, bool]:
    """
    Check if PIP dependencies are satisfied.

    Args:
        dependencies: List of PIP_Dependency objects

    Returns:
        Dict mapping dependency names to whether they are satisfied
    """
    result = {}
    for dep in dependencies:
        # Call the method directly on the class to avoid Pydantic __getattr__ issues
        result[dep.name] = PIP_Dependency.is_satisfied(dep)
        if not result[dep.name] and not dep.optional:
            logger.warning(f"Required PIP dependency '{dep.name}' is not installed")

    return result


def install_pip_dependencies(
    dependencies: List[PIP_Dependency], only_missing: bool = True
) -> Dict[str, bool]:
    """
    Install PIP dependencies based on resolved dependency order.

    Args:
        dependencies: List of PIP_Dependency objects
        only_missing: Only install dependencies that are not already satisfied

    Returns:
        Dict mapping dependency names to whether they were successfully installed
    """
    if not dependencies:
        return {}

    # Convert list to dictionary for dependency resolution
    deps_dict = {dep.name: dep for dep in dependencies}

    # Resolve dependencies to get proper installation order
    try:
        resolved_deps = resolve_dependencies(deps_dict)
        # Convert back to list but in resolved order
        dependencies = list(resolved_deps.values())
        logger.debug(
            f"Resolved PIP dependencies in order: {[dep.name for dep in dependencies]}"
        )
    except DependencyResolutionError as e:
        logger.warning(f"Dependency resolution failed: {str(e)}. Using original order.")

    # Determine which dependencies to install
    to_install = []
    if only_missing:
        dependency_status = check_pip_dependencies(dependencies)
        to_install = []
        for dep in dependencies:
            if not dependency_status.get(dep.name, False):
                # Include version requirement if specified
                if dep.semver:
                    to_install.append((dep.name, f"{dep.name}{dep.semver}"))
                else:
                    to_install.append((dep.name, dep.name))
    else:
        to_install = []
        for dep in dependencies:
            if dep.semver:
                to_install.append((dep.name, f"{dep.name}{dep.semver}"))
            else:
                to_install.append((dep.name, dep.name))

    if not to_install:
        return {}

    # Install dependencies
    result = {}

    # Get package specs for installation
    pkg_specs = [spec for _, spec in to_install]

    # Check if uv is available for faster installation
    use_uv = shutil.which("uv") is not None

    try:
        # Try batch installation first
        if use_uv:
            pip_cmd = ["uv", "pip", "install"] + pkg_specs
        else:
            pip_cmd = [sys.executable, "-m", "pip", "install"] + pkg_specs

        install_result = subprocess.run(
            pip_cmd, capture_output=True, text=True, check=False
        )

        if install_result.returncode == 0:
            # All packages installed successfully
            for name, _ in to_install:
                result[name] = True
                logger.debug(f"Successfully installed PIP package: {name}")
        else:
            # If batch install fails, try individually
            for name, spec in to_install:
                if use_uv:
                    individual_cmd = ["uv", "pip", "install", spec]
                else:
                    individual_cmd = [sys.executable, "-m", "pip", "install", spec]

                pkg_result = subprocess.run(
                    individual_cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                result[name] = pkg_result.returncode == 0
                if result[name]:
                    logger.debug(f"Successfully installed PIP package: {spec}")
                else:
                    logger.error(
                        f"Failed to install PIP package {spec}: {pkg_result.stderr}"
                    )
    except Exception as e:
        logger.error(f"Error installing PIP dependencies: {str(e)}")
        for name, _ in to_install:
            result[name] = False

    return result


# Define dependency resolution classes
class DependencyResolutionError(Exception):
    """Exception raised for errors in the dependency resolution process."""

    pass


class DependencyNode:
    """A node in the dependency graph representing a dependency."""

    def __init__(self, name: str, version: str = "0.0.0"):
        self.name = name
        self.version = version
        self.dependencies: Dict[str, str] = {}  # name -> version_constraint

    def add_dependency(self, name: str, version_constraint: Optional[str] = None):
        """Add a dependency to this node."""
        self.dependencies[name] = version_constraint

    def __repr__(self):
        return f"<DependencyNode {self.name}@{self.version}>"


import jwt


class DependencyProvider(AbstractExtensionProvider):
    """Provider for resolvelib that handles dependency resolution."""

    def __init__(self, dependency_map: Dict[str, List[DependencyNode]]):
        """
        Initialize with a mapping of dependency name to available versions.

        Args:
            dependency_map: Dict mapping dependency names to lists of DependencyNode objects
        """
        self.dependency_map = dependency_map

    def identify(self, requirement_or_candidate):
        """Get the name of the dependency."""
        if hasattr(requirement_or_candidate, "name"):
            return requirement_or_candidate.name
        # If it's just a string, return it as is
        return requirement_or_candidate

    def get_preference(
        self, identifier, resolutions, candidates, information, backtrack_causes
    ):
        """Get the preference for a dependency (higher versions preferred)."""
        return len(candidates)

    def find_matches(self, identifier, requirements, incompatibilities):
        """Find candidates matching the requirements."""
        if identifier not in self.dependency_map:
            return []

        candidates = []
        for candidate in self.dependency_map[identifier]:
            # Check if candidate is compatible with all requirements
            compatible = True
            for req in requirements:
                # Handle both DependencyRequirement objects and string/name-only requirements
                version_constraint = getattr(req, "version_constraint", None)
                if not self._check_version_compatibility(
                    candidate.version, version_constraint
                ):
                    compatible = False
                    break

            if compatible:
                candidates.append(candidate)

        # Sort by version (higher versions first)
        candidates.sort(key=lambda c: c.version, reverse=True)
        return candidates

    def is_satisfied_by(self, requirement, candidate):
        """Check if a candidate satisfies a requirement."""
        # Handle both DependencyRequirement objects and string/name-only requirements
        version_constraint = getattr(requirement, "version_constraint", None)
        return self._check_version_compatibility(candidate.version, version_constraint)

    def get_dependencies(self, candidate):
        """Get dependencies of a candidate."""
        return [
            DependencyRequirement(name, version_constraint)
            for name, version_constraint in candidate.dependencies.items()
        ]

    def _check_version_compatibility(
        self, version: str, version_constraint: Optional[str]
    ) -> bool:
        """
        Check if a version is compatible with a constraint.

        Args:
            version: Version to check
            version_constraint: Version constraint string (e.g., ">=1.0.0")

        Returns:
            bool: True if compatible
        """
        if not version_constraint:
            return True

        try:
            import semver

            # Normalize version to semver format if needed
            # Many packages use versions like "8.2" instead of "8.2.0"
            version_parts = version.split(".")
            if len(version_parts) == 2:
                # Convert "8.2" to "8.2.0"
                normalized_version = f"{version_parts[0]}.{version_parts[1]}.0"
            elif len(version_parts) == 1:
                # Convert "8" to "8.0.0"
                normalized_version = f"{version_parts[0]}.0.0"
            else:
                normalized_version = version

            # Use the newer semver API to avoid deprecation warning
            version_obj = semver.Version.parse(normalized_version)
            return version_obj.match(version_constraint)
        except (ImportError, ValueError, AttributeError):
            # If we can't check, assume compatible
            return True


class DependencyRequirement:
    """Represents a requirement for resolvelib."""

    def __init__(self, name: str, version_constraint: Optional[str] = None):
        self.name = name
        self.version_constraint = version_constraint

    def __repr__(self):
        if self.version_constraint:
            return f"<Requirement {self.name}{self.version_constraint}>"
        return f"<Requirement {self.name}>"


class BaseReporter:
    """Basic reporter implementation for resolvelib."""

    def starting(self):
        pass

    def starting_round(self, index):
        pass

    def ending_round(self, index, state):
        pass

    def ending(self, state):
        pass

    def adding_requirement(self, requirement, parent):
        pass

    def backtracking(self, causes):
        pass

    def pinning(self, candidate):
        pass


def resolve_dependencies(dependencies: Dict[str, Dependency]) -> Dict[str, Dependency]:
    """
    Resolve dependencies using resolvelib.

    Args:
        dependencies: Dict mapping dependency names to Dependency objects

    Returns:
        Dict[str, Dependency]: Resolved dependencies in installation order

    Raises:
        DependencyResolutionError: If resolution fails
    """
    if not HAS_RESOLVELIB:
        logger.warning(
            "resolvelib not installed, skipping complex dependency resolution."
        )
        return dependencies

    # Build dependency nodes
    dependency_map: Dict[str, List[DependencyNode]] = {}

    # Create nodes for all dependencies
    for name, dep in dependencies.items():
        if name not in dependency_map:
            dependency_map[name] = []

        # Create a node for this dependency
        node = DependencyNode(name)

        # Add its dependencies based on the dependency type
        if isinstance(dep, SYS_Dependency):
            # System dependencies might have other system dependencies
            # For now, we don't model these, but we could extend this
            pass
        elif isinstance(dep, PIP_Dependency):
            # For PIP dependencies, we can use their declared versions
            pass

        dependency_map[name].append(node)

    # Create provider and resolver
    provider = DependencyProvider(dependency_map)

    try:
        resolver = Resolver(provider, BaseReporter())

        # Create initial requirements
        requirements = [DependencyRequirement(name) for name in dependencies.keys()]

        # Resolve
        result = resolver.resolve(requirements)

        # Construct result in resolution order
        resolved_deps = {}
        for node in result.mapping.values():
            resolved_deps[node.name] = dependencies[node.name]

        return resolved_deps

    except Exception as e:
        # If we get an exception, log it and fall back to original dependencies
        logger.error(f"Dependency resolution failed: {str(e)}")
        raise DependencyResolutionError(f"Failed to resolve dependencies: {str(e)}")


def resolve_extension_dependencies(available_extensions: Dict[str, any]) -> List[str]:
    """
    DEPRECATED: Use ExtensionRegistry.resolve_extension_dependencies instead.

    Resolve loading order for extensions based on their dependencies using topological sort.

    Args:
        available_extensions: Dictionary mapping extension names to extension classes or objects

    Returns:
        List of extension names in loading order

    Raises:
        ValueError: If circular dependencies are detected
    """
    import warnings

    warnings.warn(
        "resolve_extension_dependencies is deprecated. Use ExtensionRegistry.resolve_extension_dependencies instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    # Build dependency graph
    dependency_graph = {}
    for ext_name, ext_class in available_extensions.items():
        deps = []
        if hasattr(ext_class, "dependencies") and ext_class.dependencies:
            # Handle both Dependencies object and list of dependencies
            if hasattr(ext_class.dependencies, "ext"):
                # Dependencies object with .ext property
                for dep in ext_class.dependencies.ext:
                    if not dep.optional:  # Only consider required dependencies
                        deps.append(dep.name)
            elif hasattr(ext_class.dependencies, "__iter__"):
                # Direct list/iterable of EXT_Dependency objects
                for dep in ext_class.dependencies:
                    if isinstance(dep, EXT_Dependency) and not dep.optional:
                        deps.append(dep.name)
        dependency_graph[ext_name] = deps

    # Topological sort using Kahn's algorithm
    in_degree = {ext: 0 for ext in dependency_graph}
    for ext_name, deps in dependency_graph.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[ext_name] += 1

    # Start with extensions that have no dependencies
    queue = [ext for ext, degree in in_degree.items() if degree == 0]
    result = []

    while queue:
        current = queue.pop(0)
        result.append(current)

        # For each extension that depends on the current extension
        for ext_name, deps in dependency_graph.items():
            if current in deps:
                in_degree[ext_name] -= 1
                if in_degree[ext_name] == 0:
                    queue.append(ext_name)

    # Check for circular dependencies
    if len(result) != len(dependency_graph):
        remaining = set(dependency_graph.keys()) - set(result)
        raise ValueError(f"Circular dependency detected among extensions: {remaining}")

    return result


class EXT_Dependency(Dependency):
    """
    Represents a dependency of an extension on another extension.
    """

    # Item 30: ability metadata + optional-missing callback. Both are
    # additive; existing call sites continue to work without supplying
    # either.
    disables_abilities: List[str] = Field(
        default_factory=list,
        description=(
            "Names of abilities this extension would expose if the "
            "(optional) dependency were satisfied. Surfaced by the "
            "startup banner so operators know what is silently disabled."
        ),
    )
    on_optional_missing: Optional[Any] = Field(
        default=None,
        exclude=True,
        description=(
            "Callable[[str], None] invoked when this optional "
            "dependency is missing. Defaults to a structured warning "
            "log via lib.Dependencies.print_optional_skip_banner."
        ),
    )

    def is_satisfied(self, loaded_extensions: Dict[str, str]) -> bool:
        """
        Check if this dependency is satisfied.

        Args:
            loaded_extensions: Dict mapping extension names to their versions

        Returns:
            bool: True if the dependency is satisfied
        """
        # Check if the extension is loaded
        if self.name not in loaded_extensions:
            if self.optional:
                # Item 30: invoke the on-missing callback so the operator
                # has a real signal that an optional dependency was
                # silently skipped. Errors in the callback never block
                # startup -- log and continue.
                callback = self.on_optional_missing or _default_optional_missing_callback
                try:
                    callback(self.name)
                except Exception as err:  # pragma: no cover - defensive
                    logger.warning(
                        f"on_optional_missing callback for {self.name} raised: {err}"
                    )
            return self.optional

        # If semver is specified, check version compatibility
        if self.semver:
            try:
                return semver.match(loaded_extensions[self.name], self.semver)
            except ValueError:
                # If semver matching fails, log a warning
                logger.warning(
                    f"Invalid semver requirement '{self.semver}' for dependency '{self.name}'"
                )
                return False

        # Extension is loaded but no semver specified
        return True


def _default_optional_missing_callback(dep_name: str) -> None:
    """Default ``on_optional_missing`` handler. Emits a structured warning.

    Extensions can override per-dependency by passing their own callable
    to ``EXT_Dependency(on_optional_missing=...)``. The banner helper
    ``print_optional_skip_banner`` consumes the same metadata to render
    a single startup-time summary.
    """
    logger.warning(
        f"Optional extension dependency missing: {dep_name}. "
        f"Features depending on it will be disabled."
    )


def print_optional_skip_banner(skipped: List[Tuple[str, str]]) -> str:
    """Render a startup banner naming every skipped optional dependency.

    Args:
        skipped: A list of ``(dep_name, disabled_abilities_csv)`` tuples.
            Typical input is built by walking the resolved extension set
            and collecting ``(EXT_Dependency.name, ",".join(d.disables_abilities))``
            for any optional dependency that was missing.

    Returns:
        The rendered banner string. Also printed to stdout so operators
        see it during startup; deployments that capture stdout to their
        log pipeline get a structured record automatically.
    """
    if not skipped:
        rendered = "OPTIONAL DEPS SKIPPED: (none)"
        logger.info(rendered)
        return rendered
    parts = []
    for dep_name, abilities in skipped:
        if abilities:
            parts.append(f"{dep_name} (disabled: {abilities})")
        else:
            parts.append(dep_name)
    rendered = "OPTIONAL DEPS SKIPPED: " + ", ".join(parts)
    logger.info(rendered)
    return rendered


class SysDependencies(List[SYS_Dependency]):
    """List of system dependencies with installation ability."""

    def install(self, only_missing: bool = True) -> Dict[str, bool]:
        """Install system dependencies."""
        return install_system_dependencies(self, only_missing=only_missing)

    def check(self) -> Dict[str, bool]:
        """Check if system dependencies are satisfied."""
        return check_system_dependencies(self)


class PipDependencies(List[PIP_Dependency]):
    """List of PIP dependencies with installation ability."""

    def install(self, only_missing: bool = True) -> Dict[str, bool]:
        """Install PIP dependencies."""
        return install_pip_dependencies(self, only_missing=only_missing)

    def check(self) -> Dict[str, bool]:
        """Check if PIP dependencies are satisfied."""
        return check_pip_dependencies(self)


class ExtDependencies(List[EXT_Dependency]):
    """List of extension dependencies with checking ability."""

    def install(self, only_missing: bool = True) -> Dict[str, bool]:
        """Extension dependencies cannot be installed automatically."""
        logger.warning(
            "Extension dependencies cannot be installed automatically. They must be loaded by the extension system."
        )
        return {dep.name: False for dep in self}

    def check(
        self, loaded_extensions: Optional[Dict[str, str]] = None
    ) -> Dict[str, bool]:
        """Check if extension dependencies are satisfied."""
        if loaded_extensions is None:
            loaded_extensions = {}

        results = {}
        for dep in self:
            results[dep.name] = dep.is_satisfied(loaded_extensions)
        return results

    @property
    def env(self) -> str:
        """
        Get extension dependencies as a CSV string for APP_EXTENSIONS environment variable.

        Returns:
            str: Comma-separated list of extension names (e.g., "email,auth_mfa,database")
        """
        return ",".join(dep.name for dep in self)

    def server(self):
        """
        Create an isolated test server with only these extension dependencies.
        Uses dependency-based extension loading by temporarily setting APP_EXTENSIONS.

        Returns:
            Context manager that yields a TestClient with the specified extensions loaded

        Example:
            with deps.ext.server() as client:
                response = client.get("/api/status")
                assert response.status_code == 200
        """
        # Import here to avoid circular imports and missing dependencies during tests
        try:
            from fastapi.testclient import TestClient

            from serverframework.app import instance
        except ImportError as e:
            logger.error(f"Failed to import required modules for test server: {e}")
            raise ImportError(
                "Test server requires fastapi.testclient.TestClient and your app's instance function. "
                f"Missing dependency: {e}"
            )

        # Return the context manager class
        return _ExtServerContext(self.env)


class _ExtServerContext:
    """Context manager for extension test servers."""

    def __init__(self, extensions_csv: str):
        self.extensions_csv = extensions_csv
        self.client = None

    def __enter__(self):
        try:
            from fastapi.testclient import TestClient

            # Use app.instance() for proper environment isolation instead of env patching
            from serverframework.app import instance

            # Create app with isolated extensions config - no environment pollution
            app = instance(extensions=self.extensions_csv)

            # Create test client
            self.client = TestClient(app)
            return self.client

        except Exception as e:
            logger.error(f"Failed to create test server: {e}")
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.client:
                self.client.__exit__(exc_type, exc_val, exc_tb)
        except Exception:
            pass  # Ignore cleanup errors


class Dependencies(List[Dependency]):
    """
    Unified dependencies list with type-specific properties and installation methods.

    This class extends List[Dependency] and provides convenient properties to access
    dependencies by type (.sys, .pip, .ext) along with installation methods.

    Usage:
        deps = Dependencies([
            SYS_Dependency(...),
            PIP_Dependency(...),
            EXT_Dependency(...)
        ])

        # Install all dependencies
        results = deps.install()

        # Install only system dependencies
        sys_results = deps.sys.install()

        # Check PIP dependencies
        pip_status = deps.pip.check()
    """

    def __init__(self, dependencies: List[Dependency]):
        """
        Initialize Dependencies with validation against requirements.txt.

        Args:
            dependencies: List of dependency objects

        Raises:
            ValueError: If there are conflicting version requirements with requirements.txt
        """
        super().__init__(dependencies)
        self._validate_against_requirements()

    def _validate_against_requirements(self) -> None:
        """
        Validate PIP dependencies against requirements.txt file.
        Issues warnings for duplicates and errors for conflicts.
        """
        try:
            requirements_deps = self._parse_requirements_txt()
            if not requirements_deps:
                return  # No requirements.txt found or it's empty

            pip_deps = self.pip
            if not pip_deps:
                return  # No PIP dependencies to validate

            for dep in pip_deps:
                if dep.name in requirements_deps:
                    req_version = requirements_deps[dep.name]

                    # Check if this is just a duplicate (same name, no specific version in dep)
                    if not dep.semver:
                        logger.warning(
                            f"PIP dependency '{dep.name}' is already specified in requirements.txt. "
                            f"Consider removing it from dependencies or specifying a version constraint."
                        )
                        continue

                    # Check for version conflicts
                    if req_version and dep.semver:
                        if self._has_version_conflict(
                            dep.name, dep.semver, req_version
                        ):
                            raise ValueError(
                                f"Version conflict for '{dep.name}': "
                                f"Dependencies specifies '{dep.semver}' but requirements.txt has '{req_version}'. "
                                f"These version constraints are incompatible."
                            )
                    elif req_version and not dep.semver:
                        logger.warning(
                            f"PIP dependency '{dep.name}' is in requirements.txt with version '{req_version}' "
                            f"but no version constraint specified in dependencies."
                        )
                    elif not req_version and dep.semver:
                        logger.warning(
                            f"PIP dependency '{dep.name}' has version constraint '{dep.semver}' in dependencies "
                            f"but no version specified in requirements.txt."
                        )

        except Exception as e:
            logger.debug(f"Could not validate against requirements.txt: {e}")

    def _parse_requirements_txt(self) -> Dict[str, Optional[str]]:
        """
        Parse requirements.txt file and extract package names and versions.

        Returns:
            Dict mapping package names to their version constraints (or None if no version)
        """
        import os
        import re

        # Look for requirements.txt in common locations
        possible_paths = [
            "requirements.txt",
            "../requirements.txt",
            "../../requirements.txt",
            os.path.join(os.getcwd(), "requirements.txt"),
            os.path.join(os.path.dirname(os.getcwd()), "requirements.txt"),
        ]

        # Add the specific path from the user's system
        user_requirements_path = (
            r"c:\Users\Jameson\Source\AGI\aginfrastructure\requirements.txt"
        )
        if os.path.exists(user_requirements_path):
            possible_paths.insert(0, user_requirements_path)

        requirements_file = None
        for path in possible_paths:
            if os.path.exists(path):
                requirements_file = path
                break

        if not requirements_file:
            logger.debug("No requirements.txt file found for validation")
            return {}

        try:
            with open(requirements_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.debug(f"Could not read requirements.txt: {e}")
            return {}

        requirements = {}

        # Parse each line
        for line in content.split("\n"):
            line = line.strip()

            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue

            # Handle lines with comments at the end
            if "#" in line:
                line = line.split("#")[0].strip()

            if not line:
                continue

            # Parse package name and version
            # Handle various formats: package, package==1.0.0, package>=1.0.0, package~=1.0.0, etc.
            match = re.match(
                r"^([a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]|[a-zA-Z0-9])([<>=!~\s].+)?$",
                line,
            )
            if match:
                package_name = stringcase.spinalcase(
                    match.group(1)
                )  # Normalize package name
                version_spec = match.group(2).strip() if match.group(2) else None
                requirements[package_name] = version_spec
            else:
                # Try simpler parsing for edge cases
                parts = re.split(r"[<>=!~\s]", line, maxsplit=1)
                if parts:
                    package_name = stringcase.spinalcase(parts[0].strip())
                    if package_name:
                        requirements[package_name] = (
                            line[len(parts[0]) :].strip() if len(parts) > 1 else None
                        )

        logger.debug(
            f"Parsed {len(requirements)} requirements from {requirements_file}"
        )
        return requirements

    def _has_version_conflict(
        self, package_name: str, dep_semver: str, req_version: str
    ) -> bool:
        """
        Check if there's a version conflict between dependency semver and requirements.txt version.

        Args:
            package_name: Name of the package
            dep_semver: Semver constraint from dependency (e.g., ">=2.0.0")
            req_version: Version constraint from requirements.txt (e.g., "==1.5.0")

        Returns:
            True if there's a conflict, False otherwise
        """
        try:
            import re

            # Normalize both version strings
            dep_semver = dep_semver.strip()
            req_version = req_version.strip()

            # Extract version numbers and operators
            dep_match = re.match(
                r"^([<>=!~]+)?\s*([0-9]+(?:\.[0-9]+)*(?:\.[0-9]+)?).*$", dep_semver
            )
            req_match = re.match(
                r"^([<>=!~]+)?\s*([0-9]+(?:\.[0-9]+)*(?:\.[0-9]+)?).*$", req_version
            )

            if not dep_match or not req_match:
                # Can't parse versions, assume no conflict
                logger.debug(
                    f"Could not parse version constraints for {package_name}: '{dep_semver}' vs '{req_version}'"
                )
                return False

            dep_op = dep_match.group(1) or "=="
            dep_ver = dep_match.group(2)
            req_op = req_match.group(1) or "=="
            req_ver = req_match.group(2)

            # Simple conflict detection for common cases
            if dep_op == "==" and req_op == "==" and dep_ver != req_ver:
                return True

            if (
                dep_op == ">="
                and req_op == "=="
                and self._version_compare(req_ver, dep_ver) < 0
            ):
                return True

            if (
                dep_op == "<="
                and req_op == "=="
                and self._version_compare(req_ver, dep_ver) > 0
            ):
                return True

            if (
                req_op == ">="
                and dep_op == "=="
                and self._version_compare(dep_ver, req_ver) < 0
            ):
                return True

            if (
                req_op == "<="
                and dep_op == "=="
                and self._version_compare(dep_ver, req_ver) > 0
            ):
                return True

            # For more complex cases, we'd need a full constraint solver
            # For now, assume no conflict if we can't determine
            return False

        except Exception as e:
            logger.debug(f"Error checking version conflict for {package_name}: {e}")
            return False

    def _version_compare(self, version1: str, version2: str) -> int:
        """
        Compare two version strings.

        Returns:
            -1 if version1 < version2
             0 if version1 == version2
             1 if version1 > version2
        """
        try:
            # Use semver if available, otherwise do simple comparison
            v1_parts = [int(x) for x in version1.split(".")]
            v2_parts = [int(x) for x in version2.split(".")]

            # Pad with zeros to make same length
            max_len = max(len(v1_parts), len(v2_parts))
            v1_parts.extend([0] * (max_len - len(v1_parts)))
            v2_parts.extend([0] * (max_len - len(v2_parts)))

            for i in range(max_len):
                if v1_parts[i] < v2_parts[i]:
                    return -1
                elif v1_parts[i] > v2_parts[i]:
                    return 1

            return 0

        except Exception:
            # Fallback to string comparison
            if version1 < version2:
                return -1
            elif version1 > version2:
                return 1
            else:
                return 0

    @property
    def sys(self) -> SysDependencies:
        """Get system dependencies with installation methods."""
        sys_deps = [dep for dep in self if isinstance(dep, SYS_Dependency)]
        return SysDependencies(sys_deps)

    @property
    def pip(self) -> PipDependencies:
        """Get PIP dependencies with installation methods."""
        pip_deps = [dep for dep in self if isinstance(dep, PIP_Dependency)]
        return PipDependencies(pip_deps)

    @property
    def ext(self) -> ExtDependencies:
        """Get extension dependencies with checking methods."""
        ext_deps = [dep for dep in self if isinstance(dep, EXT_Dependency)]
        return ExtDependencies(ext_deps)

    def install(self, only_missing: bool = True) -> Dict[str, bool]:
        """
        Install all dependencies in the correct order.

        Args:
            only_missing: Only install dependencies that are not already satisfied

        Returns:
            Dict mapping dependency names to installation success status
        """
        results = {}

        # Install system dependencies first (they may be needed by PIP packages)
        if self.sys:
            logger.debug(f"Installing {len(self.sys)} system dependencies")
            sys_results = self.sys.install(only_missing=only_missing)
            results.update(sys_results)

        # Install PIP dependencies next
        if self.pip:
            logger.debug(f"Installing {len(self.pip)} PIP dependencies")
            pip_results = self.pip.install(only_missing=only_missing)
            results.update(pip_results)

        # Extension dependencies are checked but not installed
        if self.ext:
            logger.debug(f"Checking {len(self.ext)} extension dependencies")
            ext_results = self.ext.check()
            results.update(ext_results)

        return results

    def check(
        self, loaded_extensions: Optional[Dict[str, str]] = None
    ) -> Dict[str, bool]:
        """
        Check if all dependencies are satisfied.

        Args:
            loaded_extensions: Dict of loaded extensions for EXT_Dependency checking

        Returns:
            Dict mapping dependency names to satisfaction status
        """
        results = {}

        # Check system dependencies
        if self.sys:
            sys_results = self.sys.check()
            results.update(sys_results)

        # Check PIP dependencies
        if self.pip:
            pip_results = self.pip.check()
            results.update(pip_results)

        # Check extension dependencies
        if self.ext:
            ext_results = self.ext.check(loaded_extensions)
            results.update(ext_results)

        return results

    def get_missing(
        self, loaded_extensions: Optional[Dict[str, str]] = None
    ) -> "Dependencies":
        """
        Get a new Dependencies object containing only unsatisfied dependencies.

        Args:
            loaded_extensions: Dict of loaded extensions for EXT_Dependency checking

        Returns:
            Dependencies object with only missing dependencies
        """
        status = self.check(loaded_extensions)
        missing_deps = [dep for dep in self if not status.get(dep.name, False)]
        return Dependencies(missing_deps)

    def get_by_type(self, dependency_type: Type[Dependency]) -> List[Dependency]:
        """
        Get dependencies of a specific type.

        Args:
            dependency_type: The dependency class to filter by

        Returns:
            List of dependencies of the specified type
        """
        return [dep for dep in self if isinstance(dep, dependency_type)]

    def summary(self) -> Dict[str, int]:
        """
        Get a summary count of dependencies by type.

        Returns:
            Dict with counts of each dependency type
        """
        return {
            "sys": len(self.sys),
            "pip": len(self.pip),
            "ext": len(self.ext),
            "total": len(self),
        }
