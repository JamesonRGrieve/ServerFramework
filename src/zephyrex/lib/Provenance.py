# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the running source came from, and whether it is what was published.

Every response carries three headers (always on):

- ``Source-Link``: where this deployment offers its source, ``APP_REPOSITORY``
  (AGPL-3.0 section 13); a deployment running modified code points it at its
  own source.
- ``Source-Hash-Status``: ``verified`` when the installed source matches the
  digest in the release manifest the build wrote (``_provenance.json``, inside
  the signed wheel); ``modified`` when it does not; ``unverified`` when there
  is no manifest to check against (a source checkout).
- ``Source-Git-Status``: the running source's git state: ``clean`` or
  ``dirty`` in a git checkout of it; ``none`` when it is not one (no
  ``.git``, as for an installed release); ``error`` when git cannot say
  (not installed, timed out, refused). An installed release is vouched for
  by ``Source-Hash-Status``; the commit it was built from is in its manifest
  and in ``GET /source``.

The digest covers what a wheel ships (Python modules and package data, not
tests), so a checkout and an install of the same commit hash alike.

This module uses only the standard library: the build hook in setup.py loads
it directly, before the package's dependencies exist.
"""

import functools
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

MANIFEST_NAME = "_provenance.json"
SOURCE_SUFFIXES = frozenset({".py", ".json", ".md", ".toml"})
TEST_MODULE_SUFFIX = "_test"
PYTEST_CONFIG = "conftest.py"
GIT_TIMEOUT_SECONDS = 5

VERIFIED, MODIFIED, UNVERIFIED = "verified", "modified", "unverified"
CLEAN, DIRTY, NONE, ERROR = "clean", "dirty", "none", "error"


def shipped_files(root: Path) -> Iterator[Path]:
    """The files under ``root`` a wheel ships, in a stable order."""
    for path in sorted(root.rglob("*")):
        if (
            path.is_file()
            and path.suffix in SOURCE_SUFFIXES
            and "__pycache__" not in path.parts
            and not path.stem.endswith(TEST_MODULE_SUFFIX)
            and path.name not in (PYTEST_CONFIG, MANIFEST_NAME)
        ):
            yield path


def source_digest(root: Path) -> str:
    """SHA-256 over each shipped file's relative path and content hash."""
    digest = hashlib.sha256()
    for path in shipped_files(root):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


class _GitFailed(Exception):
    """git is missing, timed out, or refused (e.g. an unsafe-ownership repo)."""


def _git_environment() -> Dict[str, str]:
    """The environment without git's own variables: under a git hook or alias
    GIT_DIR / GIT_INDEX_FILE point at another repository, and git would
    answer for it (or write into its index) instead of for ``directory``."""
    return {
        name: value for name, value in os.environ.items() if not name.startswith("GIT_")
    }


def _git(directory: Path, *arguments: str) -> str:
    """``git -C directory ...``'s output; raises _GitFailed when git cannot
    answer."""
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), *arguments],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _GitFailed(str(error)) from error
    if result.returncode != 0:
        raise _GitFailed(result.stderr.strip())
    return result.stdout


def _in_a_repository(directory: Path) -> bool:
    """Whether a ``.git`` sits at or above ``directory``; decided without git,
    so a missing git is an error only where there is a repository to ask."""
    return any((path / ".git").exists() for path in (directory, *directory.parents))


def git_status(directory: Path) -> str:
    """``clean``/``dirty`` for a git checkout of the source under
    ``directory``; ``none`` when it is not one; ``error`` when git cannot say.

    A repository that does not track these files is ``none`` too: an
    installed package in a gitignored virtualenv inside some other project's
    repository is not a checkout of that project."""
    if not _in_a_repository(directory):
        return NONE
    try:
        if not _git(directory, "ls-files", "--", ".").strip():
            return NONE
        changes = _git(directory, "status", "--porcelain", "--", ".")
    except _GitFailed:
        return ERROR
    return DIRTY if changes.strip() else CLEAN


def _checkout_commit(directory: Path, status: str) -> Optional[str]:
    """HEAD of the checkout of ``directory`` (``status`` from git_status), or
    None when it is not one."""
    if status not in (CLEAN, DIRTY):
        return None
    try:
        return _git(directory, "rev-parse", "HEAD").strip() or None
    except _GitFailed:
        return None


def build_manifest(root: Path, source_tree: Path) -> dict:
    """The manifest a build writes beside the shipped files under ``root``;
    ``source_tree`` is the checkout it was built from."""
    status = git_status(source_tree)
    return {
        "digest": source_digest(root),
        "commit": _checkout_commit(source_tree, status),
        "git_status": status,
    }


@dataclass(frozen=True)
class SourceState:
    hash_status: str
    git_status: str
    digest: str
    commit: Optional[str]


@functools.lru_cache(maxsize=None)
def source_state(root: Path) -> SourceState:
    """Hashed once per process: the source does not change under a running
    server, and every app built in the process shares it."""
    digest = source_digest(root)
    manifest_path = root / MANIFEST_NAME
    manifest: Optional[dict] = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
    if manifest is None:
        hash_status = UNVERIFIED
    else:
        hash_status = VERIFIED if manifest.get("digest") == digest else MODIFIED
    status = git_status(root)
    commit = _checkout_commit(root, status)
    if commit is None and manifest is not None:
        commit = manifest.get("commit")
    return SourceState(
        hash_status=hash_status, git_status=status, digest=digest, commit=commit
    )


def source_headers(state: SourceState, link: str) -> List[Tuple[bytes, bytes]]:
    return [
        (b"source-link", link.encode("latin-1")),
        (b"source-hash-status", state.hash_status.encode()),
        (b"source-git-status", state.git_status.encode()),
    ]


class SourceHeadersMiddleware:
    """ASGI middleware putting the source headers on every HTTP response.
    ``link`` is read per response, so a changed APP_REPOSITORY shows at once."""

    def __init__(self, app: Any, root: Path, link: Callable[[], str]) -> None:
        self.app = app
        self.root = root
        self.link = link

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = source_headers(source_state(self.root), self.link())

        async def with_source(message: Any) -> None:
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [*message.get("headers", []), *headers],
                }
            await send(message)

        await self.app(scope, receive, with_source)
