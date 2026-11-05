# SPDX-License-Identifier: AGPL-3.0-or-later
"""Source code hosting: repositories, issues, pull requests, commits and
file contents on GitHub, GitLab, Forgejo (and Gitea) and Bitbucket,
through each platform's REST API.

A repository lives on one platform, so every ability names its
``provider`` and runs on that provider's instances only. A ``repo`` is
``owner/name`` (GitLab: ``group/subgroup/name``) or a web URL on the
instance's own host; a URL naming another host is refused, so the
instance's token is never sent anywhere but its own platform.

Every provider answers in the same shapes:

- repository: ``full_name, url, description, default_branch, private``
- issue: ``number, title, body, state, url, author, assignees``
- pull request: ``number, title, body, state, merged, url, head, base, author``
- commit: ``sha, message, author, date, url``
- directory entry: ``name, path, type ("file" or "dir"), size``
- file: ``path, ref, size, content`` (UTF-8 text up to ``MAX_FILE_BYTES``)

each with the ``provider`` that answered.
"""

import base64
import binascii
import re
from abc import abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Dict, List, Optional, Set
from urllib.parse import urlparse

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

SOURCE_REQUEST_TIMEOUT_SECONDS = 30.0
MAX_FILE_BYTES = 1024 * 1024
DEFAULT_LIMIT = 30
MAX_LIMIT = 100
MAX_SINCE_DAYS = 3650
STATES = ("open", "closed", "all")
_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_REF = re.compile(r"^[A-Za-z0-9_.\-/]{1,250}$")
# Where a web URL's path stops naming the repository.
_URL_TAILS = {"-", "tree", "blob", "src", "issues", "pulls", "pull", "commits"}


def _segment(value: str, what: str) -> str:
    if not _SEGMENT.match(value) or set(value) == {"."}:
        raise InvalidInputExternalError(f"{what} {value!r} is not a valid name")
    return value


def repo_segments(repo: str, host: str, depth: Optional[int]) -> List[str]:
    """The path segments naming ``repo``: ``owner/name``, or a web URL on
    ``host``. ``depth`` is the segment count (None: two or more, GitLab's
    nested groups). Every segment is a plain name: one cannot reach
    another API path (``..``) or another host. A URL may run on past the
    repository (``/tree/main/docs``); a bare name may not."""
    path = repo.strip()
    is_url = "://" in path
    if is_url:
        parsed = urlparse(path)
        if (parsed.hostname or "").lower() != host.lower():
            raise InvalidInputExternalError(
                f"{repo!r} is not on this provider's host ({host})"
            )
        path = parsed.path
    parts = [part for part in path.strip("/").split("/") if part]
    if is_url:
        for index, part in enumerate(parts):
            if part in _URL_TAILS and index >= 2:
                parts = parts[:index]
                break
        if depth is not None:
            parts = parts[:depth]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1][: -len(".git")]
    if len(parts) < 2 or (depth is not None and len(parts) != depth):
        raise InvalidInputExternalError(f"{repo!r} does not name a repository")
    return [_segment(part, "repository path part") for part in parts]


def file_path(path: str) -> str:
    """A repository file path, normalised; ``..`` is refused."""
    parts = [part for part in path.strip().strip("/").split("/") if part]
    return "/".join(_segment(part, "path part") for part in parts)


def git_ref(ref: str, what: str = "ref") -> str:
    if not _REF.match(ref) or ".." in ref:
        raise InvalidInputExternalError(f"{what} {ref!r} is not a valid branch or tag")
    return ref


def checked_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise InvalidInputExternalError(f"limit must be 1-{MAX_LIMIT}, not {limit}")
    return limit


def checked_state(state: str) -> str:
    if state not in STATES:
        raise InvalidInputExternalError(
            f"state must be one of {', '.join(STATES)}, not {state!r}"
        )
    return state


def since(days: int) -> datetime:
    if not 1 <= days <= MAX_SINCE_DAYS:
        raise InvalidInputExternalError(f"days must be 1-{MAX_SINCE_DAYS}")
    return datetime.now(UTC) - timedelta(days=days)


def parse_time(value: str) -> datetime:
    """An API timestamp (ISO 8601, ``Z`` or an offset)."""
    return datetime.fromisoformat(value)


def readable_size(path: str, size: int) -> int:
    if size > MAX_FILE_BYTES:
        raise InvalidInputExternalError(
            f"{path} is {size} bytes; files over {MAX_FILE_BYTES} are not read"
        )
    return size


def text_file(
    provider: str, path: str, ref: str, raw: bytes, size: Optional[int] = None
) -> Dict[str, Any]:
    """A file's answer: its UTF-8 text, refused when too large or binary."""
    size = readable_size(path, len(raw) if size is None else size)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidInputExternalError(f"{path} is not a text file") from exc
    return {
        "path": path,
        "ref": ref,
        "size": size,
        "content": content,
        "provider": provider,
    }


def decoded(content: str, path: str) -> bytes:
    """A base64 ``content`` field as the APIs send it (with line breaks)."""
    try:
        return base64.b64decode(content, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise InvalidInputExternalError(f"{path}: undecodable content") from exc


def first_line(message: str) -> str:
    return message.splitlines()[0] if message else ""


class AbstractSourceProvider(AbstractStaticProvider):
    """A code hosting platform."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "list_repositories",
        "get_repository",
        "list_issues",
        "get_issue",
        "create_issue",
        "update_issue",
        "list_pull_requests",
        "create_pull_request",
        "list_commits",
        "list_files",
        "read_file",
    }
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = SOURCE_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    @abstractmethod
    def web_host(cls, instance: ProviderInstanceModel) -> str:
        """The host a repository URL on this instance's platform names."""

    @classmethod
    @abstractmethod
    async def list_repositories(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        """The token owner's repositories, most recently updated first."""

    @classmethod
    @abstractmethod
    async def get_repository(
        cls, instance: ProviderInstanceModel, repo: str
    ) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def list_issues(
        cls, instance: ProviderInstanceModel, repo: str, state: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Issues only, never pull requests."""

    @classmethod
    @abstractmethod
    async def get_issue(
        cls, instance: ProviderInstanceModel, repo: str, number: int
    ) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def create_issue(
        cls,
        instance: ProviderInstanceModel,
        repo: str,
        title: str,
        body: str,
        assignees: List[str],
    ) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def update_issue(
        cls,
        instance: ProviderInstanceModel,
        repo: str,
        number: int,
        changes: Dict[str, Any],
    ) -> Dict[str, Any]:
        """``changes`` holds any of ``title``, ``body``, ``state`` (open or
        closed) and ``assignees``."""

    @classmethod
    @abstractmethod
    async def list_pull_requests(
        cls, instance: ProviderInstanceModel, repo: str, state: str, limit: int
    ) -> List[Dict[str, Any]]: ...

    @classmethod
    @abstractmethod
    async def create_pull_request(
        cls,
        instance: ProviderInstanceModel,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def list_commits(
        cls,
        instance: ProviderInstanceModel,
        repo: str,
        after: datetime,
        ref: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Commits since ``after``, newest first."""

    @classmethod
    @abstractmethod
    async def list_files(
        cls, instance: ProviderInstanceModel, repo: str, path: str, ref: Optional[str]
    ) -> List[Dict[str, Any]]:
        """The entries of the directory at ``path`` ("" for the root)."""

    @classmethod
    @abstractmethod
    async def read_file(
        cls, instance: ProviderInstanceModel, repo: str, path: str, ref: Optional[str]
    ) -> Dict[str, Any]: ...

    @classmethod
    def services(cls) -> List[str]:
        return ["source_control"]


class EXT_Source(AbstractStaticExtension):
    name: ClassVar[str] = "source"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Repositories, issues, pull requests, commits and files on GitHub, "
        "GitLab, Forgejo and Bitbucket"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = set(AbstractSourceProvider._abilities)

    @classmethod
    async def _on(cls, provider: str, method: str, *args: Any) -> Any:
        return await cls.rotate_provider_for(provider, method, *args)

    @classmethod
    @ability("list_repositories")
    async def list_repositories(
        cls, provider: str, limit: int = DEFAULT_LIMIT
    ) -> List[Dict[str, Any]]:
        """The repositories of the provider's account."""
        result: List[Dict[str, Any]] = await cls._on(
            provider, "list_repositories", checked_limit(limit)
        )
        return result

    @classmethod
    @ability("get_repository")
    async def get_repository(cls, provider: str, repo: str) -> Dict[str, Any]:
        """A repository's description and default branch."""
        result: Dict[str, Any] = await cls._on(provider, "get_repository", repo)
        return result

    @classmethod
    @ability("list_issues")
    async def list_issues(
        cls, provider: str, repo: str, state: str = "open", limit: int = DEFAULT_LIMIT
    ) -> List[Dict[str, Any]]:
        """A repository's issues (open, closed or all)."""
        result: List[Dict[str, Any]] = await cls._on(
            provider, "list_issues", repo, checked_state(state), checked_limit(limit)
        )
        return result

    @classmethod
    @ability("get_issue")
    async def get_issue(cls, provider: str, repo: str, number: int) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls._on(provider, "get_issue", repo, number)
        return result

    @classmethod
    @ability("create_issue")
    async def create_issue(
        cls,
        provider: str,
        repo: str,
        title: str,
        body: str = "",
        assignees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Open an issue."""
        if not title.strip():
            raise InvalidInputExternalError("an issue needs a title")
        result: Dict[str, Any] = await cls._on(
            provider, "create_issue", repo, title, body, list(assignees or [])
        )
        return result

    @classmethod
    @ability("update_issue")
    async def update_issue(
        cls,
        provider: str,
        repo: str,
        number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        assignees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Change an issue: any of its title, body, state (open or closed,
        to reopen or close it) and assignees."""
        if state is not None and state not in ("open", "closed"):
            raise InvalidInputExternalError("state must be open or closed")
        changes = {
            key: value
            for key, value in (
                ("title", title),
                ("body", body),
                ("state", state),
                ("assignees", assignees),
            )
            if value is not None
        }
        if not changes:
            raise InvalidInputExternalError("nothing to change")
        result: Dict[str, Any] = await cls._on(
            provider, "update_issue", repo, number, changes
        )
        return result

    @classmethod
    @ability("list_pull_requests")
    async def list_pull_requests(
        cls, provider: str, repo: str, state: str = "open", limit: int = DEFAULT_LIMIT
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = await cls._on(
            provider,
            "list_pull_requests",
            repo,
            checked_state(state),
            checked_limit(limit),
        )
        return result

    @classmethod
    @ability("create_pull_request")
    async def create_pull_request(
        cls, provider: str, repo: str, title: str, head: str, base: str, body: str = ""
    ) -> Dict[str, Any]:
        """Propose merging branch ``head`` into ``base``."""
        if not title.strip():
            raise InvalidInputExternalError("a pull request needs a title")
        result: Dict[str, Any] = await cls._on(
            provider,
            "create_pull_request",
            repo,
            title,
            body,
            git_ref(head, "head"),
            git_ref(base, "base"),
        )
        return result

    @classmethod
    @ability("list_commits")
    async def list_commits(
        cls,
        provider: str,
        repo: str,
        days: int = 7,
        ref: Optional[str] = None,
        limit: int = DEFAULT_LIMIT,
    ) -> List[Dict[str, Any]]:
        """Commits of the last ``days`` days on ``ref`` (default branch)."""
        result: List[Dict[str, Any]] = await cls._on(
            provider,
            "list_commits",
            repo,
            since(days),
            git_ref(ref) if ref else None,
            checked_limit(limit),
        )
        return result

    @classmethod
    @ability("list_files")
    async def list_files(
        cls, provider: str, repo: str, path: str = "", ref: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """The entries of a directory of the repository."""
        result: List[Dict[str, Any]] = await cls._on(
            provider, "list_files", repo, file_path(path), git_ref(ref) if ref else None
        )
        return result

    @classmethod
    @ability("read_file")
    async def read_file(
        cls, provider: str, repo: str, path: str, ref: Optional[str] = None
    ) -> Dict[str, Any]:
        """A text file's contents."""
        checked = file_path(path)
        if not checked:
            raise InvalidInputExternalError("name a file to read")
        result: Dict[str, Any] = await cls._on(
            provider, "read_file", repo, checked, git_ref(ref) if ref else None
        )
        return result
