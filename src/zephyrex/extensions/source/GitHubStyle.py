# SPDX-License-Identifier: AGPL-3.0-or-later
"""The REST API GitHub defined and Forgejo and Gitea follow: the same
paths and object shapes, differing in base URL, auth header and paging
parameter, which a platform supplies."""

from abc import abstractmethod
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Mapping, Optional

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    RateLimitExternalError,
)
from zephyrex.extensions.source.EXT_Source import (
    AbstractSourceProvider,
    decoded,
    parse_time,
    readable_size,
    repo_segments,
    text_file,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


def login(user: Optional[Mapping[str, Any]]) -> str:
    return str((user or {}).get("login", ""))


class GitHubStyleProvider(AbstractSourceProvider):
    # The query parameter for a page's size.
    page_size_parameter: ClassVar[str] = "per_page"

    @classmethod
    @abstractmethod
    def api_base(cls, instance: ProviderInstanceModel) -> str:
        """The API root, without a trailing slash."""

    @classmethod
    @abstractmethod
    def headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        """Accept and authorization headers."""

    @classmethod
    def commit_parameters(cls) -> Dict[str, Any]:
        """Extra query parameters for a commit listing."""
        return {}

    @classmethod
    def issue_list_parameters(cls) -> Dict[str, Any]:
        """Extra query parameters for an issue listing."""
        return {}

    @classmethod
    def _repo_path(cls, instance: ProviderInstanceModel, repo: str) -> str:
        owner, name = repo_segments(repo, cls.web_host(instance), 2)
        return f"/repos/{owner}/{name}"

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
        try:
            return await cls.http().request(
                method,
                f"{cls.api_base(instance)}{path}",
                params=params,
                json=json,
                headers=cls.headers(instance),
            )
        except AuthExternalError as exc:
            # GitHub answers an exhausted rate limit with a 403, which is
            # no verdict on the token.
            if "rate limit" in str(exc.upstream_payload or "").lower():
                raise RateLimitExternalError(
                    f"{cls.friendly_name} rate limit reached",
                    provider=cls.name,
                    upstream_status=exc.upstream_status,
                ) from exc
            raise

    @classmethod
    def _repository(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "full_name": found.get("full_name", ""),
            "url": found.get("html_url", ""),
            "description": found.get("description") or "",
            "default_branch": found.get("default_branch", ""),
            "private": bool(found.get("private")),
            "provider": cls.name,
        }

    @classmethod
    def _issue(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "number": found.get("number"),
            "title": found.get("title", ""),
            "body": found.get("body") or "",
            "state": found.get("state", ""),
            "url": found.get("html_url", ""),
            "author": login(found.get("user")),
            "assignees": [login(user) for user in found.get("assignees") or []],
            "provider": cls.name,
        }

    @classmethod
    def _pull_request(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "number": found.get("number"),
            "title": found.get("title", ""),
            "body": found.get("body") or "",
            "state": found.get("state", ""),
            "merged": bool(found.get("merged_at")),
            "url": found.get("html_url", ""),
            "head": (found.get("head") or {}).get("ref", ""),
            "base": (found.get("base") or {}).get("ref", ""),
            "author": login(found.get("user")),
            "provider": cls.name,
        }

    @classmethod
    async def list_repositories(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            "/user/repos",
            params={"sort": "updated", cls.page_size_parameter: limit},
        )
        return [cls._repository(repo) for repo in found or []][:limit]

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
        found = await cls._call(
            instance,
            "GET",
            f"{cls._repo_path(instance, repo)}/issues",
            params={
                "state": state,
                cls.page_size_parameter: limit,
                **cls.issue_list_parameters(),
            },
        )
        # GitHub lists pull requests among issues; they carry pull_request.
        return [
            cls._issue(issue) for issue in found or [] if not issue.get("pull_request")
        ]

    @classmethod
    async def get_issue(
        cls, instance: ProviderInstanceModel, repo: str, number: int
    ) -> Dict[str, Any]:
        found = await cls._call(
            instance, "GET", f"{cls._repo_path(instance, repo)}/issues/{int(number)}"
        )
        if found.get("pull_request"):
            raise InvalidInputExternalError(
                f"#{number} is a pull request, not an issue", provider=cls.name
            )
        return cls._issue(found)

    @classmethod
    async def create_issue(
        cls,
        instance: ProviderInstanceModel,
        repo: str,
        title: str,
        body: str,
        assignees: List[str],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"title": title, "body": body}
        if assignees:
            payload["assignees"] = assignees
        return cls._issue(
            await cls._call(
                instance,
                "POST",
                f"{cls._repo_path(instance, repo)}/issues",
                json=payload,
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
        return cls._issue(
            await cls._call(
                instance,
                "PATCH",
                f"{cls._repo_path(instance, repo)}/issues/{int(number)}",
                json=changes,
            )
        )

    @classmethod
    async def list_pull_requests(
        cls, instance: ProviderInstanceModel, repo: str, state: str, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            f"{cls._repo_path(instance, repo)}/pulls",
            params={"state": state, cls.page_size_parameter: limit},
        )
        return [cls._pull_request(pull) for pull in found or []][:limit]

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
                f"{cls._repo_path(instance, repo)}/pulls",
                json={"title": title, "body": body, "head": head, "base": base},
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
        params: Dict[str, Any] = {
            "since": after.isoformat(),
            cls.page_size_parameter: limit,
            **cls.commit_parameters(),
        }
        if ref:
            params["sha"] = ref
        found = await cls._call(
            instance,
            "GET",
            f"{cls._repo_path(instance, repo)}/commits",
            params=params,
        )
        commits = []
        for entry in found or []:
            detail = entry.get("commit") or {}
            author = detail.get("author") or {}
            date = author.get("date", "")
            # The since filter is applied here too: not every server honours it.
            if date and parse_time(date) < after:
                continue
            commits.append(
                {
                    "sha": entry.get("sha", ""),
                    "message": detail.get("message", ""),
                    "author": author.get("name", ""),
                    "date": date,
                    "url": entry.get("html_url", ""),
                    "provider": cls.name,
                }
            )
        return commits[:limit]

    @classmethod
    async def _contents(
        cls, instance: ProviderInstanceModel, repo: str, path: str, ref: Optional[str]
    ) -> Any:
        suffix = f"/contents/{path}" if path else "/contents"
        return await cls._call(
            instance,
            "GET",
            f"{cls._repo_path(instance, repo)}{suffix}",
            params={"ref": ref} if ref else None,
        )

    @classmethod
    async def list_files(
        cls, instance: ProviderInstanceModel, repo: str, path: str, ref: Optional[str]
    ) -> List[Dict[str, Any]]:
        found = await cls._contents(instance, repo, path, ref)
        if not isinstance(found, list):
            raise InvalidInputExternalError(
                f"{path} is a file, not a directory", provider=cls.name
            )
        return [
            {
                "name": entry.get("name", ""),
                "path": entry.get("path", ""),
                "type": "dir" if entry.get("type") == "dir" else "file",
                "size": entry.get("size"),
                "provider": cls.name,
            }
            for entry in found
        ]

    @classmethod
    async def read_file(
        cls, instance: ProviderInstanceModel, repo: str, path: str, ref: Optional[str]
    ) -> Dict[str, Any]:
        found = await cls._contents(instance, repo, path, ref)
        if isinstance(found, list) or found.get("type") != "file":
            raise InvalidInputExternalError(f"{path} is not a file", provider=cls.name)
        size = int(found.get("size") or 0)
        # Over the API's inline limit the content is withheld (encoding "none").
        if found.get("encoding") != "base64":
            readable_size(path, size)
            raise InvalidInputExternalError(
                f"{path}: the API withheld its content", provider=cls.name
            )
        return text_file(
            cls.name, path, ref or "", decoded(found.get("content", ""), path), size
        )
