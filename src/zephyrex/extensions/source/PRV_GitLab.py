# SPDX-License-Identifier: AGPL-3.0-or-later
"""GitLab (gitlab.com, or a self-managed server through ``base_url``),
through its REST API v4. The API key is a personal, group or project
access token (else ``GITLAB_TOKEN``); without one, public projects are
read anonymously. Merge requests answer as pull requests; a project may
sit in nested groups (``group/subgroup/project``)."""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote, urlparse

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.source.EXT_Source import (
    MAX_LIMIT,
    AbstractSourceProvider,
    decoded,
    repo_segments,
    text_file,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_BASE_URL = "https://gitlab.com"
API_PATH = "/api/v4"
TREE_PAGE_SIZE = 100
# Our states -> GitLab's (None: list all and filter here).
_ISSUE_STATES = {"open": "opened", "closed": "closed", "all": "all"}
_MERGE_REQUEST_STATES = {"open": "opened", "closed": None, "all": "all"}


def username(user: Optional[Mapping[str, Any]]) -> str:
    return str((user or {}).get("username", ""))


class PRV_GitLab_Source(AbstractSourceProvider):
    name: ClassVar[str] = "gitlab"
    friendly_name: ClassVar[str] = "GitLab"
    description: ClassVar[str] = "GitLab.com or a self-managed GitLab"
    _env: ClassVar[Dict[str, Any]] = {
        "GITLAB_TOKEN": "",
        "GITLAB_URL": DEFAULT_BASE_URL,
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Access token (empty: public projects only)",
            env="GITLAB_TOKEN",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "base_url",
            "Server address (https://gitlab.com, https://gitlab.example.com)",
            env="GITLAB_URL",
            default=DEFAULT_BASE_URL,
        ),
    )

    @classmethod
    def _base_url(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "base_url") or DEFAULT_BASE_URL).rstrip("/")

    @classmethod
    def web_host(cls, instance: ProviderInstanceModel) -> str:
        return urlparse(cls._base_url(instance)).hostname or ""

    @classmethod
    def _project(cls, instance: ProviderInstanceModel, repo: str) -> str:
        """The project's API path: its full path, URL-encoded as one id."""
        full_path = "/".join(repo_segments(repo, cls.web_host(instance), None))
        return f"/projects/{quote(full_path, safe='')}"

    @classmethod
    async def _call(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        token = cls.setting(instance, "api_key")
        return await cls.http().request(
            method,
            f"{cls._base_url(instance)}{API_PATH}{path}",
            params=params,
            json=json,
            headers={"PRIVATE-TOKEN": str(token)} if token else None,
        )

    @classmethod
    def _repository(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "full_name": found.get("path_with_namespace", ""),
            "url": found.get("web_url", ""),
            "description": found.get("description") or "",
            "default_branch": found.get("default_branch") or "",
            "private": found.get("visibility") != "public",
            "provider": cls.name,
        }

    @staticmethod
    def _state(state: str) -> str:
        return {"opened": "open", "merged": "closed"}.get(state, state)

    @classmethod
    def _issue(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "number": found.get("iid"),
            "title": found.get("title", ""),
            "body": found.get("description") or "",
            "state": cls._state(str(found.get("state", ""))),
            "url": found.get("web_url", ""),
            "author": username(found.get("author")),
            "assignees": [username(user) for user in found.get("assignees") or []],
            "provider": cls.name,
        }

    @classmethod
    def _pull_request(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "number": found.get("iid"),
            "title": found.get("title", ""),
            "body": found.get("description") or "",
            "state": cls._state(str(found.get("state", ""))),
            "merged": found.get("state") == "merged",
            "url": found.get("web_url", ""),
            "head": found.get("source_branch", ""),
            "base": found.get("target_branch", ""),
            "author": username(found.get("author")),
            "provider": cls.name,
        }

    @classmethod
    async def _user_ids(
        cls, instance: ProviderInstanceModel, usernames: List[str]
    ) -> List[int]:
        """GitLab assigns by user id; each username is looked up."""
        ids = []
        for name in usernames:
            found = await cls._call(
                instance, "GET", "/users", params={"username": name}
            )
            if not found:
                raise InvalidInputExternalError(
                    f"GitLab has no user {name!r}", provider=cls.name
                )
            ids.append(int(found[0]["id"]))
        return ids

    @classmethod
    async def list_repositories(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            "/projects",
            params={
                "membership": "true",
                "order_by": "last_activity_at",
                "per_page": limit,
            },
        )
        return [cls._repository(project) for project in found or []][:limit]

    @classmethod
    async def get_repository(
        cls, instance: ProviderInstanceModel, repo: str
    ) -> Dict[str, Any]:
        return cls._repository(
            await cls._call(instance, "GET", cls._project(instance, repo))
        )

    @classmethod
    async def list_issues(
        cls, instance: ProviderInstanceModel, repo: str, state: str, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            f"{cls._project(instance, repo)}/issues",
            params={"state": _ISSUE_STATES[state], "per_page": limit},
        )
        return [cls._issue(issue) for issue in found or []][:limit]

    @classmethod
    async def get_issue(
        cls, instance: ProviderInstanceModel, repo: str, number: int
    ) -> Dict[str, Any]:
        return cls._issue(
            await cls._call(
                instance, "GET", f"{cls._project(instance, repo)}/issues/{int(number)}"
            )
        )

    @classmethod
    async def create_issue(
        cls,
        instance: ProviderInstanceModel,
        repo: str,
        title: str,
        body: str,
        assignees: List[str],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"title": title, "description": body}
        if assignees:
            payload["assignee_ids"] = await cls._user_ids(instance, assignees)
        return cls._issue(
            await cls._call(
                instance, "POST", f"{cls._project(instance, repo)}/issues", json=payload
            )
        )

    @classmethod
    async def update_issue(
        cls,
        instance: ProviderInstanceModel,
        repo: str,
        number: int,
        changes: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if "title" in changes:
            payload["title"] = changes["title"]
        if "body" in changes:
            payload["description"] = changes["body"]
        if "state" in changes:
            payload["state_event"] = (
                "close" if changes["state"] == "closed" else "reopen"
            )
        if "assignees" in changes:
            payload["assignee_ids"] = await cls._user_ids(
                instance, changes["assignees"]
            )
        return cls._issue(
            await cls._call(
                instance,
                "PUT",
                f"{cls._project(instance, repo)}/issues/{int(number)}",
                json=payload,
            )
        )

    @classmethod
    async def list_pull_requests(
        cls, instance: ProviderInstanceModel, repo: str, state: str, limit: int
    ) -> List[Dict[str, Any]]:
        gitlab_state = _MERGE_REQUEST_STATES[state]
        found = await cls._call(
            instance,
            "GET",
            f"{cls._project(instance, repo)}/merge_requests",
            # Filtering here: fetch a full page so the filter leaves enough.
            params={
                "state": gitlab_state or "all",
                "per_page": MAX_LIMIT if gitlab_state is None else limit,
            },
        )
        requests = [cls._pull_request(request) for request in found or []]
        if gitlab_state is None:
            # Closed includes merged, as on GitHub; GitLab lists them apart.
            requests = [request for request in requests if request["state"] == "closed"]
        return requests[:limit]

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
                f"{cls._project(instance, repo)}/merge_requests",
                json={
                    "title": title,
                    "description": body,
                    "source_branch": head,
                    "target_branch": base,
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
        params: Dict[str, Any] = {"since": after.isoformat(), "per_page": limit}
        if ref:
            params["ref_name"] = ref
        found = await cls._call(
            instance,
            "GET",
            f"{cls._project(instance, repo)}/repository/commits",
            params=params,
        )
        return [
            {
                "sha": commit.get("id", ""),
                "message": commit.get("message", ""),
                "author": commit.get("author_name", ""),
                "date": commit.get("committed_date", ""),
                "url": commit.get("web_url", ""),
                "provider": cls.name,
            }
            for commit in found or []
        ][:limit]

    @classmethod
    async def list_files(
        cls, instance: ProviderInstanceModel, repo: str, path: str, ref: Optional[str]
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"per_page": TREE_PAGE_SIZE}
        if path:
            params["path"] = path
        if ref:
            params["ref"] = ref
        found = await cls._call(
            instance,
            "GET",
            f"{cls._project(instance, repo)}/repository/tree",
            params=params,
        )
        return [
            {
                "name": entry.get("name", ""),
                "path": entry.get("path", ""),
                "type": "dir" if entry.get("type") == "tree" else "file",
                "size": None,
                "provider": cls.name,
            }
            for entry in found or []
        ]

    @classmethod
    async def read_file(
        cls, instance: ProviderInstanceModel, repo: str, path: str, ref: Optional[str]
    ) -> Dict[str, Any]:
        if not ref:
            ref = (await cls.get_repository(instance, repo))["default_branch"]
        found = await cls._call(
            instance,
            "GET",
            f"{cls._project(instance, repo)}/repository/files/{quote(path, safe='')}",
            params={"ref": ref},
        )
        size = int(found.get("size") or 0)
        return text_file(
            cls.name, path, str(ref), decoded(found.get("content", ""), path), size
        )
