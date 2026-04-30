"""Extension manifest format and loader (Item 20).

A ``manifest.toml`` declares extension metadata, dependencies, entry point,
and version in a human-editable form independent of the Python class. This
gives the framework a stable contract for ``install_from_manifest`` --
fetching/extracting/validating an extension package without first executing
arbitrary code from the package itself.

Existing extensions without a ``manifest.toml`` continue to be discovered
through the legacy filesystem walk in ``ExtensionRegistry``. The manifest is
strictly additive opt-in.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import List, Optional, Union

from pydantic import BaseModel, Field, ValidationError, field_validator

# PEP 440 version regex (subset adequate for manifest validation -- the
# canonical reference grammar lives in packaging.version, but we avoid a
# new top-level dependency by validating a strict-enough common subset).
_PEP440_RE = re.compile(
    r"^([1-9][0-9]*!)?"
    r"(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*"
    r"((a|b|c|rc|alpha|beta|pre|preview)(0|[1-9][0-9]*)?)?"
    r"(\.post(0|[1-9][0-9]*))?"
    r"(\.dev(0|[1-9][0-9]*))?"
    r"(\+[a-zA-Z0-9]+(\.[a-zA-Z0-9]+)*)?$",
    re.IGNORECASE,
)

# Extension names must match the same identifier pattern used by
# ``ExtensionRegistry._SAFE_EXTENSION_NAME``: only letters, digits, and
# underscore, starting with a letter or underscore.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ExtensionDependencyDecl(BaseModel):
    """Declared dependency on another extension."""

    name: str
    version: Optional[str] = None  # PEP 440 specifier (e.g. ">=1.0,<2")
    optional: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                f"extension dependency name {v!r} must match "
                f"[A-Za-z_][A-Za-z0-9_]*"
            )
        return v


class ExtensionManifest(BaseModel):
    """Schema for ``manifest.toml`` files shipped alongside extensions."""

    name: str
    version: str  # PEP 440
    description: Optional[str] = None
    author: Optional[str] = None
    entry_module: str  # module name (no .py) inside the extension directory
    extension_dependencies: List[ExtensionDependencyDecl] = Field(default_factory=list)
    pip_dependencies: List[str] = Field(default_factory=list)  # PEP 508 strings
    system_dependencies: List[str] = Field(default_factory=list)
    minimum_framework_version: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                f"extension name {v!r} must match [A-Za-z_][A-Za-z0-9_]*"
            )
        return v

    @field_validator("version", "minimum_framework_version")
    @classmethod
    def _validate_version(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _PEP440_RE.match(v):
            raise ValueError(
                f"version {v!r} is not a valid PEP 440 version string"
            )
        return v

    @field_validator("entry_module")
    @classmethod
    def _validate_entry_module(cls, v: str) -> str:
        # Entry module is a Python identifier optionally with dots; we keep
        # it simple since manifest entries are bare file stems by contract.
        if not v or "/" in v or "\\" in v or v.endswith(".py"):
            raise ValueError(
                f"entry_module {v!r} must be a bare module name without "
                f"path separators or '.py' suffix"
            )
        return v


def load_manifest(path: Union[str, Path]) -> ExtensionManifest:
    """Load and validate a ``manifest.toml`` file.

    Args:
        path: Filesystem path to the manifest file (``manifest.toml``) or
            to the directory that contains it.

    Returns:
        Validated ``ExtensionManifest`` instance.

    Raises:
        FileNotFoundError: If no ``manifest.toml`` is present at ``path``.
        ValueError: If the TOML is malformed or the schema validation fails.
    """
    p = Path(path)
    if p.is_dir():
        p = p / "manifest.toml"
    if not p.exists():
        raise FileNotFoundError(f"manifest.toml not found: {p}")
    try:
        with p.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"malformed manifest TOML at {p}: {e}") from e
    try:
        return ExtensionManifest(**data)
    except ValidationError as e:
        raise ValueError(f"manifest schema validation failed at {p}: {e}") from e


def find_manifest(extension_dir: Union[str, Path]) -> Optional[Path]:
    """Return the path to ``manifest.toml`` under ``extension_dir`` or None."""
    p = Path(extension_dir) / "manifest.toml"
    return p if p.exists() else None
