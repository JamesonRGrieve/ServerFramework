"""Single canonical mechanism for migration ownership detection.

Item 24. The framework historically documented three different ways of
deciding which extension owns a given table -- file-path detection, the
``@extension_model`` decorator registry, and the ``info={"extension":
name}`` entry in ``__table_args__``. We collapse all three into one
documented resolution rule:

  1. **``__table_args__["info"]["extension"]``** -- authoritative for
     field injections into core tables via ``@extension_model``. The
     decorator sets this dict automatically; authors do not write it by
     hand.
  2. **File-path detection** -- authoritative for new extension-owned
     tables whose model class lives under
     ``src/extensions/<name>/BLL_*.py``.
  3. **Otherwise** -- the table is core-owned (return ``None``).

The decorator-set info dict must merge with any existing
``__table_args__`` rather than overwriting; that contract belongs to the
decorator, not this module. Here we only resolve.

This module is the canonical resolver. Other documented mechanisms in
``DB.Migrations.md`` should be removed in favor of pointing here.
``MigrationManager`` may continue to expose its own
``env_is_table_owned_by_extension`` static method as a thin delegate to
``env_is_table_owned_by_extension`` defined here, so existing tests keep
passing without coupling MigrationManager to this module's import path.
"""

from __future__ import annotations

import os
import re
from logging import getLogger
from typing import Any, Dict, Optional

logger = getLogger(__name__)

# Match ``extensions.<name>...`` or filesystem paths containing
# ``/extensions/<name>/``. Both forms appear in ``__module__`` because
# the in-tree namespace is dotted while out-of-tree extensions loaded
# via ExtensionLoader use the legacy ``extensions.<name>.<file>`` name
# (see ExtensionLoader.py).
_MODULE_PATTERN = re.compile(r"(?:^|\.)extensions\.([A-Za-z0-9_]+)\.")
_PATH_PATTERN = re.compile(r"[\\/]extensions[\\/]([A-Za-z0-9_]+)[\\/]")


def _table_info_owner(table: Any) -> Optional[str]:
    """Return owner from ``table.info['extension']``, if present.

    The ``@extension_model`` decorator stamps the info dict with the
    owning extension name. Reading it here is the first resolution step
    because it covers the field-injection case where the table itself is
    a core table -- the file-path fallback would incorrectly return
    ``None`` for those (since the table's class lives in core).
    """
    try:
        info = getattr(table, "info", None)
    except Exception:
        return None
    if not info:
        return None
    extension = info.get("extension") if isinstance(info, dict) else None
    if isinstance(extension, str) and extension:
        return extension
    return None


def _model_class_for_table(table: Any) -> Any:
    """Best-effort: locate the SQLAlchemy declarative class for ``table``.

    SQLAlchemy stamps ``Table.class_`` in some configurations; in others
    we have to walk through ``table.metadata`` or rely on the caller to
    pass a class directly. Returning ``None`` signals "couldn't locate"
    and lets the file-path resolver decide based on whatever attributes
    the input does carry.
    """
    cls_attr = getattr(table, "class_", None)
    if cls_attr is not None:
        return cls_attr
    # Some test fixtures pass the model class directly rather than a
    # SQLAlchemy Table; treat those as their own class.
    if hasattr(table, "__module__") and not hasattr(table, "metadata"):
        return table
    return None


def _file_path_owner(table: Any) -> Optional[str]:
    """Return owner inferred from the model class's source location.

    Looks at ``__module__`` (``extensions.<name>...``) first since that
    works for in-tree extensions; falls back to inspecting the source
    file path for the ``/extensions/<name>/`` segment which catches
    out-of-tree extensions loaded via ExtensionLoader.
    """
    cls = _model_class_for_table(table)
    if cls is None:
        return None

    module = getattr(cls, "__module__", "") or ""
    match = _MODULE_PATTERN.search(module)
    if match:
        return match.group(1)

    # Fall back to source-file inspection.
    try:
        import inspect

        source_file = inspect.getsourcefile(cls) or inspect.getfile(cls)
    except (TypeError, OSError):
        source_file = ""
    if source_file:
        # Normalize path separators so the regex works on both POSIX
        # and Windows.
        normalized = source_file.replace(os.sep, "/")
        path_match = _PATH_PATTERN.search(normalized)
        if path_match:
            return path_match.group(1)

    return None


def env_is_table_owned_by_extension(table: Any) -> Optional[str]:
    """Return the owning extension name, or ``None`` for core.

    Single canonical resolution rule (replaces the three previously
    documented mechanisms):

      1. Check ``table.__table_args__`` /
         ``table.info`` for ``{'extension': name}`` (covers
         ``@extension_model`` injection on core tables -- though the
         injection case typically returns ``None`` here per
         ``DB.Migrations.md`` because injections do not change ownership;
         see ``env_table_extenders`` for that surface).
      2. Otherwise inspect the model class's ``__module__`` /
         source-file path for ``extensions/<name>/...``.
      3. Otherwise return ``None`` (core-owned).
    """
    # Step 1: explicit info-dict ownership stamp.
    info_owner = _table_info_owner(table)
    if info_owner is not None:
        return info_owner

    # Step 2: file-path / module-path inspection.
    return _file_path_owner(table)


def list_table_ownership(metadata: Any) -> Dict[str, Optional[str]]:
    """Return a mapping ``{table_name: extension_name | None}`` over all
    tables in ``metadata``.

    ``metadata`` is duck-typed: any object exposing a ``tables`` attribute
    (``Dict[str, Table]``) works -- typically SQLAlchemy ``MetaData``.
    """
    out: Dict[str, Optional[str]] = {}
    tables = getattr(metadata, "tables", None) or {}
    for name, table in tables.items():
        out[name] = env_is_table_owned_by_extension(table)
    return out


def print_ownership(ownership: Dict[str, Optional[str]]) -> str:
    """Format an ownership mapping for CLI display.

    Returns the rendered string and prints it to stdout, so this can be
    used both as a CLI helper and a programmatic formatter.
    """
    import sys

    lines = ["Table ownership audit:", "-" * 40]
    width = max((len(n) for n in ownership.keys()), default=10)
    for name in sorted(ownership.keys()):
        owner = ownership[name] or "<core>"
        lines.append(f"  {name.ljust(width)}  {owner}")
    rendered = "\n".join(lines)
    sys.stdout.write(rendered + "\n")
    sys.stdout.flush()
    return rendered


__all__ = [
    "env_is_table_owned_by_extension",
    "list_table_ownership",
    "print_ownership",
]
