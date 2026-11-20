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
from typing import List, Optional, Sequence, Union

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


# Module-level state for the configurable extensions roots. Empty means "use
# the bundled extensions/ directory". Callers that host extensions outside the
# package set this once at startup via ``set_extensions_root`` (or by passing
# ``extensions_path`` to ``ExtensionRegistry``). A product composed with
# another names several roots, its own first (a pinned checkout of the other
# product's extensions after it), as an ``os.pathsep``-joined string or a list.
# The first root (where new extensions are installed), as before several were
# allowed, and the roots searched after it.
_EXTENSIONS_ROOT_OVERRIDE: Optional[Path] = None
_FURTHER_EXTENSIONS_ROOTS: List[Path] = []

RootsSpec = Union[str, os.PathLike, Sequence[Union[str, os.PathLike]]]


class DuplicateExtensionError(RuntimeError):
    """An extension name found in more than one extensions root: which one
    would load is ambiguous, so the app refuses to boot."""


def _parse_roots(path: RootsSpec, check: bool) -> List[Path]:
    """The roots ``path`` names, in order, each once."""
    parts: List[Union[str, os.PathLike]]
    if isinstance(path, (str, os.PathLike)):
        parts = [part for part in os.fspath(path).split(os.pathsep) if part.strip()]
    else:
        parts = list(path)
    roots: List[Path] = []
    for part in parts:
        resolved = Path(part).expanduser().resolve()
        if check and not resolved.exists():
            raise FileNotFoundError(f"Extensions root does not exist: {resolved}")
        if check and not resolved.is_dir():
            raise NotADirectoryError(f"Extensions root is not a directory: {resolved}")
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _extend_namespace(roots: List[Path]) -> None:
    """Make every root importable as ``zephyrex.extensions.*``, so a plain
    package beside the extensions (a product's shared contract code, say)
    imports in a booted app as it does under a test's conftest."""
    import zephyrex.extensions as package

    current = [str(entry) for entry in package.__path__]
    for root in reversed(roots):
        if str(root) not in current:
            current.insert(0, str(root))
    package.__path__ = current


def set_extensions_root(path: Optional[RootsSpec]) -> None:
    """Set the extensions roots: one directory, several joined with
    ``os.pathsep``, or a list, searched in order before the bundled
    ``<src_dir>/extensions``. Pass ``None`` to clear them."""
    global _FURTHER_EXTENSIONS_ROOTS, _EXTENSIONS_ROOT_OVERRIDE
    if path is None:
        _EXTENSIONS_ROOT_OVERRIDE = None
        _FURTHER_EXTENSIONS_ROOTS = []
        return
    roots = _parse_roots(path, check=True)
    if not roots:
        raise ValueError("No extensions root given")
    _EXTENSIONS_ROOT_OVERRIDE = roots[0]
    _FURTHER_EXTENSIONS_ROOTS = roots[1:]
    _extend_namespace(roots)


def extension_roots(override: Optional[RootsSpec] = None) -> List[Path]:
    """The consumer's extensions roots in search order (the bundled directory
    is not among them). ``override`` takes precedence over the module-level
    roots."""
    if override is not None:
        return _parse_roots(override, check=False)
    if _EXTENSIONS_ROOT_OVERRIDE is None:
        return []
    return [_EXTENSIONS_ROOT_OVERRIDE, *_FURTHER_EXTENSIONS_ROOTS]


def extensions_dir(override: Optional[RootsSpec] = None) -> str:
    """Return the first extensions root as a string (where new extensions are
    installed), or the bundled directory when none is set.

    ``override`` takes precedence over any module-level roots, which in turn
    take precedence over the bundled location.
    """
    roots = extension_roots(override)
    return str(roots[0]) if roots else str(_SRC_DIR / "extensions")


def extensions_path(override: Optional[RootsSpec] = None) -> Path:
    """``extensions_dir`` as a ``pathlib.Path``."""
    return Path(extensions_dir(override))


def find_extension_dir(name: str, override: Optional[RootsSpec] = None) -> Path:
    """The directory of extension ``name``: the root holding it, else the
    bundled one; the first root's (where it would go) when neither does.

    Raises ``DuplicateExtensionError`` when more than one root holds it.
    """
    roots = extension_roots(override)
    found = [root / name for root in roots if (root / name).is_dir()]
    if len(found) > 1:
        raise DuplicateExtensionError(
            f"Extension {name!r} is in more than one extensions root: "
            + ", ".join(str(path) for path in found)
        )
    if found:
        return found[0]
    bundled = _SRC_DIR / "extensions" / name
    if bundled.is_dir():
        return bundled
    return (roots[0] if roots else _SRC_DIR / "extensions") / name


def extension_dir(name: str, override: Optional[RootsSpec] = None) -> str:
    """Return the directory for a specific extension by name."""
    return str(find_extension_dir(name, override))
