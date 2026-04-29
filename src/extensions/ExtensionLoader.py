"""Helper for loading extension modules from arbitrary filesystem locations.

Item 61 (out-of-tree extension import support).

The historical pattern is ``importlib.import_module("extensions.<name>.<file>")``,
which only resolves when the extensions tree lives under a ``sys.path`` entry as
``extensions/``. As soon as a consumer points the framework at
``./my_extensions``, the discovery walk finds the right files but ``import_module``
cannot import them because the dotted name does not resolve.

``load_extension_module`` does the ``importlib.util.spec_from_file_location`` +
``module_from_spec`` + ``loader.exec_module`` dance once and registers the result
under BOTH:

  * ``serverframework_ext_<extension>_<file>`` -- a globally unique synthesized
    name suitable for use as the module's canonical key.
  * ``extensions.<extension>.<file>`` -- the legacy package-style name so that
    intra-extension imports such as
    ``from extensions.payment.BLL_Payment import PaymentManager`` keep
    resolving without modification.

If the same extension module has already been loaded (under either name) the
cached instance is returned -- callers can call this helper repeatedly without
re-executing module bodies.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Optional, Union


def _synthesized_name(extension_name: str, file_stem: str) -> str:
    """Return the framework-internal canonical module name."""
    return f"serverframework_ext_{extension_name}_{file_stem}"


def _legacy_name(extension_name: str, file_stem: str) -> str:
    """Return the package-style name used by intra-extension imports."""
    return f"extensions.{extension_name}.{file_stem}"


def load_extension_module(
    extensions_root: Union[str, Path],
    extension_name: str,
    file_stem: str,
) -> ModuleType:
    """Load an extension module via spec_from_file_location.

    The module is registered in ``sys.modules`` under both its synthesized
    canonical name and its legacy ``extensions.<name>.<file>`` name so existing
    intra-extension imports keep resolving regardless of where the extensions
    tree lives on disk.

    Args:
        extensions_root: Filesystem path that contains ``<extension_name>/``.
            Typically the value returned by ``lib.Paths.extensions_dir(...)``.
        extension_name: The extension's directory name (e.g. ``"payment"``).
        file_stem: The file stem without ``.py`` (e.g. ``"BLL_Payment"`` or
            ``"EXT_payment"``).

    Returns:
        The loaded module object.

    Raises:
        FileNotFoundError: If the resolved file path does not exist.
        ImportError: If the spec/loader cannot be constructed for the file.
    """
    root = Path(extensions_root)
    file_path = root / extension_name / f"{file_stem}.py"

    synth = _synthesized_name(extension_name, file_stem)
    legacy = _legacy_name(extension_name, file_stem)

    # Reuse already-loaded modules. Both names point at the same object on
    # success, so checking either is fine.
    if synth in sys.modules:
        sys.modules.setdefault(legacy, sys.modules[synth])
        return sys.modules[synth]
    if legacy in sys.modules:
        sys.modules.setdefault(synth, sys.modules[legacy])
        return sys.modules[legacy]

    if not file_path.exists():
        raise FileNotFoundError(
            f"Extension module not found: {file_path} "
            f"(extension={extension_name!r}, file_stem={file_stem!r})"
        )

    # Use the legacy name as the module's __name__ so that ``cls.__module__``
    # comparisons in extension code (which look like
    # ``obj.__module__ == "extensions.payment.BLL_Payment"``) keep working.
    spec = importlib.util.spec_from_file_location(legacy, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(
            f"Could not create import spec for {file_path} "
            f"(extension={extension_name!r}, file_stem={file_stem!r})"
        )

    module = importlib.util.module_from_spec(spec)
    # Register under BOTH names BEFORE exec_module so that any intra-extension
    # import inside the module body resolves against this same instance rather
    # than recursively triggering another spec_from_file_location pass.
    sys.modules[synth] = module
    sys.modules[legacy] = module

    try:
        spec.loader.exec_module(module)
    except BaseException:
        # On failure, drop the partial registration so the next attempt does
        # not see a half-initialized module.
        sys.modules.pop(synth, None)
        sys.modules.pop(legacy, None)
        raise

    return module


def is_extension_module_loaded(
    extension_name: str, file_stem: str
) -> bool:
    """Return True if the named extension module has already been loaded."""
    return (
        _synthesized_name(extension_name, file_stem) in sys.modules
        or _legacy_name(extension_name, file_stem) in sys.modules
    )


def get_loaded_extension_module(
    extension_name: str, file_stem: str
) -> Optional[ModuleType]:
    """Return the already-loaded module, or None."""
    return sys.modules.get(
        _synthesized_name(extension_name, file_stem)
    ) or sys.modules.get(_legacy_name(extension_name, file_stem))
