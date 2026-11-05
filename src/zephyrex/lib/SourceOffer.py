# SPDX-License-Identifier: AGPL-3.0-or-later
"""What GET /source answers: where the running source is offered (AGPL-3.0
section 13), and whether it is what was published, for the framework and
for each loaded extension.

A bundled extension is part of the framework's source. An extension loaded
from a consumer's extensions directory is its own source: its manifest's
``repository`` says where it is offered, and its directory is hashed and
git-checked on its own (see ``zephyrex.lib.Provenance``).
"""

import inspect
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from zephyrex.lib.Provenance import SourceState, source_state

LICENSE = "AGPL-3.0-or-later"
MANIFEST_FILE = "manifest.toml"


def _state(state: SourceState) -> Dict[str, Optional[str]]:
    return {
        "hash_status": state.hash_status,
        "git_status": state.git_status,
        "commit": state.commit,
        "digest": state.digest,
    }


def _declared_repository(directory: Path) -> Optional[str]:
    from zephyrex.extensions.Manifest import load_manifest

    manifest = directory / MANIFEST_FILE
    return load_manifest(manifest).repository if manifest.is_file() else None


def _extension_entry(extension: Any, package_root: Path, link: str) -> Dict[str, Any]:
    directory = Path(inspect.getfile(extension)).resolve().parent
    if package_root in directory.parents:
        return {"name": extension.name, "bundled": True, "source": link}
    return {
        "name": extension.name,
        "bundled": False,
        "source": _declared_repository(directory),
        **_state(source_state(directory)),
    }


def source_offer(
    package_root: Path, link: str, version: str, extensions: Iterable[Any]
) -> Dict[str, Any]:
    """The /source body for the framework at ``package_root``, offered at
    ``link``, with the loaded ``extensions`` (their classes)."""
    entries: List[Dict[str, Any]] = sorted(
        (_extension_entry(extension, package_root, link) for extension in extensions),
        key=lambda entry: entry["name"],
    )
    return {
        "source": link,
        "version": version,
        "license": LICENSE,
        **_state(source_state(package_root)),
        "extensions": entries,
    }
