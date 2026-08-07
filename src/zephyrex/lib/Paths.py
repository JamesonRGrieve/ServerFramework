"""Filesystem path resolution for the framework.

Centralizes resolution of ``src_dir`` (the directory containing ``lib/``,
``logic/``, ``database/``, ``extensions/``, etc.) and the extensions root.

Historically each call site computed ``src_dir`` independently with
``os.path.dirname(os.path.dirname(os.path.abspath(__file__)))``. That works
when every module sits two levels deep under ``src/``, but it spreads the
assumption across ~10 files and leaves no seam for hosting an extensions
folder outside the package tree. Callers should now import from here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

# ``Paths.py`` lives at ``<src_dir>/lib/Paths.py``. Going up one level lands
# us at ``<src_dir>``. We resolve once at import time so callers don't pay
# the cost on every lookup.
_THIS_FILE = Path(__file__).resolve()
_SRC_DIR = _THIS_FILE.parent.parent


def src_dir() -> str:
    """Return the directory containing the framework's top-level packages."""
    return str(_SRC_DIR)


def src_path() -> Path:
    """Return ``src_dir`` as a ``pathlib.Path``."""
    return _SRC_DIR


# Module-level state for the configurable extensions root. ``None`` means
# "use the bundled extensions/ directory". Callers that need to host
# extensions outside the package set this once at startup via
# ``set_extensions_root`` (or by passing ``extensions_path`` to
# ``ExtensionRegistry``).
_EXTENSIONS_ROOT_OVERRIDE: Optional[Path] = None


def set_extensions_root(path: Optional[Union[str, os.PathLike]]) -> None:
    """Override the extensions root directory.

    Pass ``None`` to clear the override and fall back to the bundled
    ``<src_dir>/extensions`` directory.
    """
    global _EXTENSIONS_ROOT_OVERRIDE
    if path is None:
        _EXTENSIONS_ROOT_OVERRIDE = None
        return
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Extensions root does not exist: {resolved}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"Extensions root is not a directory: {resolved}")
    _EXTENSIONS_ROOT_OVERRIDE = resolved


def extensions_dir(override: Optional[Union[str, os.PathLike]] = None) -> str:
    """Return the active extensions directory as a string.

    ``override`` takes precedence over any module-level override, which in
    turn takes precedence over the bundled location. This three-tier lookup
    lets a single call (e.g. inside ``ExtensionRegistry``) accept a
    per-instance path while leaving global defaults intact.
    """
    if override is not None:
        return str(Path(override).expanduser().resolve())
    if _EXTENSIONS_ROOT_OVERRIDE is not None:
        return str(_EXTENSIONS_ROOT_OVERRIDE)
    return str(_SRC_DIR / "extensions")


def extensions_path(override: Optional[Union[str, os.PathLike]] = None) -> Path:
    """``extensions_dir`` as a ``pathlib.Path``."""
    return Path(extensions_dir(override))


def extension_dir(name: str, override: Optional[Union[str, os.PathLike]] = None) -> str:
    """Return the directory for a specific extension by name."""
    return str(extensions_path(override) / name)
