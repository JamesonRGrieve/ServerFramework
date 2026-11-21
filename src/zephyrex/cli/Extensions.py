# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extension-contributed commands: every loaded extension (bundled or a
consumer's) may ship ``CLI_<Name>.py`` with ``register(group)``; its
commands mount as ``zephyrex <GROUP or extension> <command>``."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from zephyrex.cli.Context import CommandGroup

CLI_MODULE_GLOB = "CLI_*.py"


def cli_modules(extension: str, extensions_path: Optional[str]) -> List[Path]:
    """The extension's ``CLI_*.py`` files (tests excluded), in name order."""
    from zephyrex.lib.Paths import find_extension_dir

    directory = find_extension_dir(extension, extensions_path)
    return sorted(
        path
        for path in directory.glob(CLI_MODULE_GLOB)
        if not path.name.endswith("_test.py")
    )


def register_extension_groups(
    subparsers: "argparse._SubParsersAction[Any]",
    options: argparse.ArgumentParser,
    extensions: List[str],
    extensions_path: Optional[str],
    taken: List[str],
) -> List[str]:
    """Mount each extension's commands; returns the group names mounted. A
    module's group is its ``GROUP`` (``api-key``), else the extension's
    name. A group named like a core command (``taken``) is refused."""
    from zephyrex.extensions.ExtensionLoader import load_extension_module

    groups: Dict[str, CommandGroup] = {}
    for extension in extensions:
        for path in cli_modules(extension, extensions_path):
            module = load_extension_module(path.parent.parent, extension, path.stem)
            name = getattr(module, "GROUP", extension)
            if name in taken:
                raise ValueError(
                    f"Extension {extension!r} ships CLI group {name!r},"
                    " which is a core command"
                )
            if name not in groups:
                groups[name] = CommandGroup(
                    name,
                    subparsers,
                    options,
                    help=f"Commands of the {extension} extension.",
                )
            module.register(groups[name])
    return list(groups)
