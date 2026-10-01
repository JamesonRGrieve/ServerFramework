import os
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

from zephyrex.extensions.source.PRV_Source import AbstractSourceProvider

try:
    import git
except ImportError:
    git = None  # type: ignore[assignment]

FORGEJO_API_SUFFIX = "/api/v1"
REQUEST_TIMEOUT_SECONDS = 30
CODE_FILE_EXTENSIONS = (
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".md",
    ".yml",
    ".yaml",
    ".json",
)
LANGUAGE_BY_EXTENSION = {"py": "python", "yml": "yaml"}


class ForgejoProvider(AbstractSourceProvider):
    """
    Source-control provider backed by a self-hosted Forgejo instance's REST
    API (Gitea-compatible, ``/api/v1``) via ``requests``, and local git
    operations (via GitPython).

    A lightweight, directly-instantiated client: it holds the api key
    (used as a Forgejo personal access token) and the api uri (the base URL
    of the self-hosted instance, e.g. ``https://git.example.com``) and
    issues synchronous requests to that instance. Unlike GitHub/GitLab/
    BitBucket there is no public default host to fall back to since Forgejo
    is always self-hosted, so ``api_uri`` is required for any API call. The
    optional ``gitpython`` dependency degrades gracefully to an informative
    error string when unavailable rather than raising ImportError at import
    time; ``requests`` is a hard dependency of the Source extension so it is
    imported unconditionally.
    """

    def get_platform_name(self) -> str:
        return "Forgejo"

    def _api_base(self) -> str:
        if not self.api_uri:
            raise RuntimeError(
                "Forgejo requires an api_uri pointing at the self-hosted instance "
                "(e.g. https://git.example.com)."
            )
        return f"{self.api_uri.rstrip('/')}{FORGEJO_API_SUFFIX}"

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"token {self.api_key}"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._api_base()}{path}"
        response = requests.request(
            method, url, headers=self._headers(), timeout=REQUEST_TIMEOUT_SECONDS, **kwargs
        )
        if response.status_code == 401:
            raise RuntimeError(
                "Error: Authentication failed. Please check your Forgejo API key."
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Error: Forgejo API request failed ({response.status_code}): {response.text}"
            )
        if not response.content:
            return None
        return response.json()

    def _parse_repo_url(self, repo_url: str) -> Tuple[str, str]:
        # Forgejo instances have arbitrary self-hosted hostnames (unlike
        # github.com/bitbucket.org), so owner/repo are parsed from the URL
        # path rather than matched against a fixed host substring.
        path = urlparse(repo_url).path if "://" in repo_url else repo_url
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) < 2:
            raise ValueError(f"Could not parse Forgejo repository URL: {repo_url}")
        return parts[0], parts[1].replace(".git", "")

    def _format_error(self, exc: Exception) -> str:
        message = str(exc)
        return message if message.startswith("Error:") else f"Error: {message}"

    def clone_repo(self, repo_url: str) -> str:
        if git is None:
            return "Error: GitPython is not installed; repository cloning is unavailable."

        split_url = repo_url.split("//")
        auth_repo_url = (
            f"{split_url[0]}//{self.api_key}@{split_url[1]}"
            if self.api_key
            else "//".join(split_url)
        )

        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        repo_dir = os.path.join(self.working_directory, repo_name)

        try:
            if os.path.exists(repo_dir):
                repo = git.Repo(repo_dir)
                repo.remotes.origin.pull()
                return f"Pulled latest changes for {repo_url} to {repo_dir}"
            git.Repo.clone_from(url=auth_repo_url, to_path=repo_dir)
            return f"Cloned {repo_url} to {repo_dir}"
        except Exception as e:
            return self._format_error(e)

    def get_repo_code_contents(self, repo_url: str) -> str:
        branch: Optional[str] = None
        if "/src/branch/" in repo_url:
            repo_url, branch_path = repo_url.split("/src/branch/", 1)
            branch = branch_path.split("/")[0]

        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")

        clone_result = self.clone_repo(repo_url)
        if clone_result.startswith("Error"):
            return f"Error cloning repository: {clone_result}"

        repo_dir = os.path.join(self.working_directory, repo_name)
        if branch:
            try:
                git.Repo(repo_dir).git.checkout(branch)  # type: ignore[union-attr]
            except Exception as e:
                return f"Error checking out branch {branch}: {str(e)}"

        markdown_content = "# Repository Content\n\n"
        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = [d for d in dirs if d != ".git" and not d.startswith(".")]

            for file in files:
                if not file.endswith(CODE_FILE_EXTENSIONS):
                    continue

                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.working_directory)
                ext = file.rsplit(".", 1)[-1]
                lang = LANGUAGE_BY_EXTENSION.get(ext, ext)

                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    markdown_content += f"## {rel_path}\n\n```{lang}\n{content}\n```\n\n"
                except OSError:
                    markdown_content += f"## {rel_path}\n\n*Could not read file*\n\n"

        output_file = os.path.join(self.working_directory, f"{repo_name}.md")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        return markdown_content

    def get_repo_issues(self, repo_url: str) -> str:
        try:
            owner, repo = self._parse_repo_url(repo_url)
            issues = self._request(
                "GET",
                f"/repos/{owner}/{repo}/issues",
                params={"state": "open", "type": "issues"},
            )
            issue_list = [f"#{i.get('number')}: {i.get('title')}" for i in issues or []]
            return f"Open Issues for Forgejo Repository at {repo_url}:\n\n" + "\n".join(
                issue_list
            )
        except Exception as e:
            return self._format_error(e)

    def get_repo_issue(self, repo_url: str, issue_number: int) -> str:
        try:
            owner, repo = self._parse_repo_url(repo_url)
            issue = self._request("GET", f"/repos/{owner}/{repo}/issues/{issue_number}")
            return (
                f"Issue Details for Forgejo Repository at {repo_url}\n\n"
                f"#{issue.get('number')}: {issue.get('title')}\n\n{issue.get('body', '')}"
            )
        except Exception as e:
            return self._format_error(e)

    def create_repo_issue(
        self, repo_url: str, title: str, body: str, assignee: Optional[str] = None
    ) -> str:
        try:
            owner, repo = self._parse_repo_url(repo_url)
            payload: Dict[str, Any] = {"title": title, "body": body}
            if assignee:
                payload["assignees"] = [assignee]
            issue = self._request("POST", f"/repos/{owner}/{repo}/issues", json=payload)
            return (
                f"Created new issue in Forgejo Repository at {repo_url}\n\n"
                f"#{issue.get('number')}: {issue.get('title')}\n\n{issue.get('body', '')}"
            )
        except Exception as e:
            return self._format_error(e)

    def update_repo_issue(
        self,
        repo_url: str,
        issue_number: int,
        title: str,
        body: str,
        assignee: Optional[str] = None,
    ) -> str:
        try:
            owner, repo = self._parse_repo_url(repo_url)
            payload: Dict[str, Any] = {"title": title, "body": body}
            if assignee:
                payload["assignees"] = [assignee]
            issue = self._request(
                "PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", json=payload
            )
            return (
                f"Updated issue in Forgejo Repository at {repo_url}\n\n"
                f"#{issue.get('number')}: {issue.get('title')}\n\n{issue.get('body', '')}"
            )
        except Exception as e:
            return self._format_error(e)

    def get_repo_pull_requests(self, repo_url: str) -> str:
        try:
            owner, repo = self._parse_repo_url(repo_url)
            prs = self._request(
                "GET", f"/repos/{owner}/{repo}/pulls", params={"state": "open"}
            )
            pr_list = [f"#{pr.get('number')}: {pr.get('title')}" for pr in prs or []]
            return (
                f"Open Pull Requests for Forgejo Repository at {repo_url}:\n\n"
                + "\n".join(pr_list)
            )
        except Exception as e:
            return self._format_error(e)

    def create_repo_pull_request(
        self, repo_url: str, title: str, body: str, head: str, base: str
    ) -> str:
        try:
            owner, repo = self._parse_repo_url(repo_url)
            payload = {"title": title, "body": body, "head": head, "base": base}
            pr = self._request("POST", f"/repos/{owner}/{repo}/pulls", json=payload)
            return (
                f"Created new pull request in Forgejo Repository at {repo_url}\n\n"
                f"#{pr.get('number')}: {pr.get('title')}\n\n{pr.get('body', '')}"
            )
        except Exception as e:
            return self._format_error(e)

    def get_repo_commits(self, repo_url: str, days: int = 7) -> str:
        try:
            owner, repo = self._parse_repo_url(repo_url)
            commits = self._request(
                "GET", f"/repos/{owner}/{repo}/commits", params={"limit": 30}
            )
            commit_list = []
            for c in commits or []:
                message = c.get("commit", {}).get("message", "") or ""
                first_line = message.splitlines()[0] if message else ""
                commit_list.append(f"{c.get('sha', '')[:7]}: {first_line}")
            return (
                f"Recent Commits for Forgejo Repository at {repo_url}:\n\n"
                + "\n".join(commit_list)
            )
        except Exception as e:
            return self._format_error(e)

    def close_issue(self, repo_url: str, issue_number: int) -> str:
        try:
            owner, repo = self._parse_repo_url(repo_url)
            self._request(
                "PATCH",
                f"/repos/{owner}/{repo}/issues/{issue_number}",
                json={"state": "closed"},
            )
            return f"Closed issue in Forgejo Repository: {repo_url}, Issue #{issue_number}"
        except Exception as e:
            return self._format_error(e)

    def get_my_repos(self) -> str:
        try:
            repos = self._request("GET", "/user/repos", params={"limit": 50})
            repo_list = [
                f"{r.get('full_name')} - {r.get('html_url')}" for r in repos or []
            ]
            return "### My Forgejo Repositories\n\n" + "\n".join(repo_list)
        except Exception as e:
            return self._format_error(e)
