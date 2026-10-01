# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bitbucket Cloud, through its REST API 2.0. The API key is an Atlassian
API token or a workspace/repository access token, sent as a Bearer token
(else ``BITBUCKET_TOKEN``); without one, public repositories are read
anonymously. App passwords were retired in July 2026.

Bitbucket Cloud removed its issue tracker in August 2026 (its issues
live in Jira now), so the issue operations are refused here.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Mapping, NoReturn, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.source.EXT_Source import (
    MAX_LIMIT,
    AbstractSourceProvider,
    parse_time,
    readable_size,
    repo_segments,
    text_file,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://api.bitbucket.org/2.0"
WEB_HOST = "bitbucket.org"
# Our states -> Bitbucket's pull request states.
_PULL_REQUEST_STATES = {
    "open": ["OPEN"],
    "closed": ["MERGED", "DECLINED", "SUPERSEDED"],
    "all": ["OPEN", "MERGED", "DECLINED", "SUPERSEDED"],
}


def display_name(user: Optional[Mapping[str, Any]]) -> str:
    user = user or {}
    return str(user.get("nickname") or user.get("display_name") or "")


class PRV_Bitbucket_Source(AbstractSourceProvider):
    name: ClassVar[str] = "bitbucket"
    friendly_name: ClassVar[str] = "Bitbucket"
    description: ClassVar[str] = "Bitbucket Cloud"
    _env: ClassVar[Dict[str, Any]] = {"BITBUCKET_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "API or access token (empty: public repositories only)",
            env="BITBUCKET_TOKEN",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    def web_host(cls, instance: ProviderInstanceModel) -> str:
        return WEB_HOST

    @classmethod
    def _repo_path(cls, instance: ProviderInstanceModel, repo: str) -> str:
        workspace, slug = repo_segments(repo, WEB_HOST, 2)
        return f"/repositories/{workspace}/{slug}"

    @classmethod
    async def _call(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        *,
        params: Optional[Any] = None,
        json: Optional[Dict[str, Any]] = None,
        raw: bool = False,
    ) -> Any:
        token = cls.setting(instance, "api_key")
        return await cls.http().request(
            method,
            f"{API_URL}{path}",
            params=params,
            json=json,
            raw=raw,
            headers={"Authorization": f"Bearer {token}"} if token else None,
        )

    @classmethod
    def _repository(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "full_name": found.get("full_name", ""),
            "url": found.get("links", {}).get("html", {}).get("href", ""),
            "description": found.get("description") or "",
            "default_branch": (found.get("mainbranch") or {}).get("name", ""),
            "private": bool(found.get("is_private")),
            "provider": cls.name,
        }

    @classmethod
    def _pull_request(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        state = str(found.get("state", ""))
        return {
            "number": found.get("id"),
            "title": found.get("title", ""),
            "body": found.get("description") or "",
            "state": "open" if state == "OPEN" else "closed",
            "merged": state == "MERGED",
            "url": found.get("links", {}).get("html", {}).get("href", ""),
            "head": found.get("source", {}).get("branch", {}).get("name", ""),
            "base": found.get("destination", {}).get("branch", {}).get("name", ""),
            "author": display_name(found.get("author")),
            "provider": cls.name,
        }

    @classmethod
    async def _ref(
        cls, instance: ProviderInstanceModel, repo: str, ref: Optional[str]
    ) -> str:
        """``ref``, else the repository's main branch."""
        if ref:
            return ref
        return str((await cls.get_repository(instance, repo))["default_branch"])

    @classmethod
    def _no_issues(cls) -> NoReturn:
        raise PermanentExternalError(
            "Bitbucket Cloud removed its issue tracker in August 2026; "
            "a Bitbucket project's issues are in Jira",
            provider=cls.name,
        )

    @classmethod
    async def list_repositories(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            "/repositories",
            params={"role": "member", "sort": "-updated_on", "pagelen": limit},
        )
        return [cls._repository(repo) for repo in found.get("values", [])][:limit]

    @classmethod
    async def get_repository(
        cls, instance: ProviderInstanceModel, repo: str
    ) -> Dict[str, Any]:
        return cls._repository(
            await cls._call(instance, "GET", cls._repo_path(instance, repo))
        )

    @classmethod
    async def list_issues(
        cls, instance: ProviderInstanceModel, repo: str, state: str, limit: int
    ) -> List[Dict[str, Any]]:
        cls._no_issues()

    @classmethod
    async def get_issue(
        cls, instance: ProviderInstanceModel, repo: str, number: int
    ) -> Dict[str, Any]:
        cls._no_issues()

    @classmethod
    async def create_issue(
        cls,
        instance: ProviderInstanceModel,
        repo: str,
        title: str,
        body: str,
        assignees: List[str],
    ) -> Dict[str, Any]:
        cls._no_issues()

    @classmethod
    async def update_issue(
        cls,
        instance: ProviderInstanceModel,
        repo: str,
        number: int,
        changes: Dict[str, Any],
    ) -> Dict[str, Any]:
        cls._no_issues()

    @classmethod
    async def list_pull_requests(
        cls, instance: ProviderInstanceModel, repo: str, state: str, limit: int
    ) -> List[Dict[str, Any]]:
        params = [("state", value) for value in _PULL_REQUEST_STATES[state]]
        params.append(("pagelen", str(min(limit, MAX_LIMIT))))
        found = await cls._call(
            instance,
            "GET",
            f"{cls._repo_path(instance, repo)}/pullrequests",
            params=params,
        )
        return [cls._pull_request(pull) for pull in found.get("values", [])][:limit]

    @classmethod
    async def create_pull_request(
        cls,
        instance: ProviderInstanceModel,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> Dict[str, Any]:
        return cls._pull_request(
            await cls._call(
                instance,
                "POST",
                f"{cls._repo_path(instance, repo)}/pullrequests",
                json={
                    "title": title,
                    "description": body,
                    "source": {"branch": {"name": head}},
                    "destination": {"branch": {"name": base}},
                },
            )
        )

    @classmethod
    async def list_commits(
        cls,
        instance: ProviderInstanceModel,
        repo: str,
        after: datetime,
        ref: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        branch = await cls._ref(instance, repo, ref)
        found = await cls._call(
            instance,
            "GET",
            f"{cls._repo_path(instance, repo)}/commits/{branch}",
            params={"pagelen": limit},
        )
        # Bitbucket has no date filter; its listing is newest first.
        commits = []
        for commit in found.get("values", []):
            date = str(commit.get("date", ""))
            if date and parse_time(date) < after:
                break
            commits.append(
                {
                    "sha": commit.get("hash", ""),
                    "message": commit.get("message", ""),
                    "author": commit.get("author", {}).get("raw", ""),
                    "date": date,
                    "url": commit.get("links", {}).get("html", {}).get("href", ""),
                    "provider": cls.name,
                }
            )
        return commits[:limit]

    @classmethod
    async def list_files(
        cls, instance: ProviderInstanceModel, repo: str, path: str, ref: Optional[str]
    ) -> List[Dict[str, Any]]:
        branch = await cls._ref(instance, repo, ref)
        directory = f"{path}/" if path else ""
        found = await cls._call(
            instance,
            "GET",
            f"{cls._repo_path(instance, repo)}/src/{branch}/{directory}",
            params={"pagelen": MAX_LIMIT},
        )
        if found.get("type") == "commit_file":
            raise InvalidInputExternalError(
                f"{path} is a file, not a directory", provider=cls.name
            )
        return [
            {
                "name": str(entry.get("path", "")).rsplit("/", 1)[-1],
                "path": entry.get("path", ""),
                "type": "dir" if entry.get("type") == "commit_directory" else "file",
                "size": entry.get("size"),
                "provider": cls.name,
            }
            for entry in found.get("values", [])
        ]

    @classmethod
    async def read_file(
        cls, instance: ProviderInstanceModel, repo: str, path: str, ref: Optional[str]
    ) -> Dict[str, Any]:
        branch = await cls._ref(instance, repo, ref)
        location = f"{cls._repo_path(instance, repo)}/src/{branch}/{path}"
        meta = await cls._call(instance, "GET", location, params={"format": "meta"})
        if meta.get("type") != "commit_file":
            raise InvalidInputExternalError(f"{path} is not a file", provider=cls.name)
        # The size is checked before the body is fetched.
        readable_size(path, int(meta.get("size") or 0))
        response = await cls._call(instance, "GET", location, raw=True)
        return text_file(cls.name, path, branch, response.content)
