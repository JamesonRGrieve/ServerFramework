import os
from typing import Any, Optional, Tuple

from zephyrex.extensions.source.PRV_Source import AbstractSourceProvider

try:
    import git
except ImportError:
    git = None  # type: ignore[assignment]

try:
    from atlassian import Bitbucket
except ImportError:
    Bitbucket = None  # type: ignore[assignment,misc]

BITBUCKET_DEFAULT_URL = "https://api.bitbucket.org"
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


class BitBucketProvider(AbstractSourceProvider):
    """
    Source-control provider backed by the BitBucket REST API (via
    atlassian-python-api) and local git operations (via GitPython).

    A lightweight, directly-instantiated client: it holds the api key/uri
    (used as the BitBucket app password) and issues synchronous requests to
    BitBucket. The username is supplied via the ``bitbucket_username``
    keyword. The optional ``gitpython`` and ``atlassian-python-api``
    dependencies degrade gracefully to an informative error string when
    unavailable rather than raising ImportError at import time.
    """

    def get_platform_name(self) -> str:
        return "BitBucket"

    @property
    def username(self) -> str:
        return self.settings.get("bitbucket_username", "")

    def _client(self) -> Any:
        if Bitbucket is None:
            raise RuntimeError(
                "atlassian-python-api is not installed; BitBucket API operations are unavailable."
            )
        if not self.username or not self.api_key:
            raise RuntimeError(
                "BitBucket client requires both a username and an API key/password."
            )
        return Bitbucket(
            url=self.api_uri or BITBUCKET_DEFAULT_URL,
            username=self.username,
            password=self.api_key,
        )

    def _parse_repo_url(self, repo_url: str) -> Tuple[str, str]:
        if "bitbucket.org" in repo_url:
            parts = repo_url.split("bitbucket.org/")[1].split("/")
            if len(parts) >= 2:
                return parts[0], parts[1].split(".git")[0]

        parts = repo_url.rstrip("/").split("/")
        if len(parts) >= 2:
            return parts[-2], parts[-1].split(".git")[0]

        raise ValueError(f"Could not parse BitBucket repository URL: {repo_url}")

    def clone_repo(self, repo_url: str) -> str:
        if git is None:
            return "Error: GitPython is not installed; repository cloning is unavailable."

        split_url = repo_url.split("//")
        auth_repo_url = (
            f"{split_url[0]}//{self.username}:{self.api_key}@{split_url[1]}"
            if self.username and self.api_key
            else "//".join(split_url)
        )

        repo_name = repo_url.rstrip("/").split("/")[-1]
        repo_dir = os.path.join(self.working_directory, repo_name)

        try:
            if os.path.exists(repo_dir):
                repo = git.Repo(repo_dir)
                repo.remotes.origin.pull()
                return f"Pulled latest changes for {repo_url} to {repo_dir}"
            git.Repo.clone_from(url=auth_repo_url, to_path=repo_dir)
            return f"Cloned {repo_url} to {repo_dir}"
        except Exception as e:
            return f"Error: {str(e)}"

    def get_repo_code_contents(self, repo_url: str) -> str:
        branch: Optional[str] = None
        if "/src/" in repo_url:
            repo_url, branch_path = repo_url.split("/src/", 1)
            branch = branch_path.split("/")[0]

        repo_name = repo_url.rstrip("/").split("/")[-1]

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
            workspace, repo_slug = self._parse_repo_url(repo_url)
            issues = self._client().get_issues(workspace, repo_slug, max_results=100)
            issue_list = [f"#{i.get('id')}: {i.get('title')}" for i in issues]
            return f"Open Issues for BitBucket Repository at {repo_url}:\n\n" + "\n".join(
                issue_list
            )
        except Exception as e:
            return f"Error: {str(e)}"

    def get_repo_issue(self, repo_url: str, issue_number: int) -> str:
        try:
            workspace, repo_slug = self._parse_repo_url(repo_url)
            issue = self._client().get_issue(workspace, repo_slug, issue_number)
            return (
                f"Issue Details for BitBucket Repository at {repo_url}\n\n"
                f"{issue.get('id')}: {issue.get('title')}\n\n"
                f"{issue.get('content', {}).get('raw', '')}"
            )
        except Exception as e:
            return f"Error: {str(e)}"

    def create_repo_issue(
        self, repo_url: str, title: str, body: str, assignee: Optional[str] = None
    ) -> str:
        try:
            workspace, repo_slug = self._parse_repo_url(repo_url)
            issue_data = {"title": title, "content": {"raw": body}}
            if assignee:
                issue_data["assignee"] = {"username": assignee}
            issue = self._client().create_issue(workspace, repo_slug, issue_data)
            return (
                f"Created new issue in BitBucket Repository at {repo_url}\n\n"
                f"{issue.get('id')}: {issue.get('title')}\n\n"
                f"{issue.get('content', {}).get('raw', '')}"
            )
        except Exception as e:
            return f"Error: {str(e)}"

    def update_repo_issue(
        self,
        repo_url: str,
        issue_number: int,
        title: str,
        body: str,
        assignee: Optional[str] = None,
    ) -> str:
        try:
            workspace, repo_slug = self._parse_repo_url(repo_url)
            issue_data = {"title": title, "content": {"raw": body}}
            if assignee:
                issue_data["assignee"] = {"username": assignee}
            issue = self._client().update_issue(
                workspace, repo_slug, issue_number, issue_data
            )
            return (
                f"Updated issue in BitBucket Repository at {repo_url}\n\n"
                f"{issue.get('id')}: {issue.get('title')}\n\n"
                f"{issue.get('content', {}).get('raw', '')}"
            )
        except Exception as e:
            return f"Error: {str(e)}"

    def get_repo_pull_requests(self, repo_url: str) -> str:
        try:
            workspace, repo_slug = self._parse_repo_url(repo_url)
            prs = self._client().get_pullrequests(workspace, repo_slug)
            pr_list = [f"#{pr.get('id')}: {pr.get('title')}" for pr in prs]
            return (
                f"Open Pull Requests for BitBucket Repository at {repo_url}:\n\n"
                + "\n".join(pr_list)
            )
        except Exception as e:
            return f"Error: {str(e)}"

    def create_repo_pull_request(
        self, repo_url: str, title: str, body: str, head: str, base: str
    ) -> str:
        try:
            workspace, repo_slug = self._parse_repo_url(repo_url)
            pr_data = {
                "title": title,
                "description": body,
                "source": {"branch": {"name": head}},
                "destination": {"branch": {"name": base}},
            }
            pr = self._client().create_pullrequest(workspace, repo_slug, pr_data)
            return (
                f"Created new pull request in BitBucket Repository at {repo_url}\n\n"
                f"#{pr.get('id')}: {pr.get('title')}\n\n{pr.get('description', '')}"
            )
        except Exception as e:
            return f"Error: {str(e)}"

    def get_repo_commits(self, repo_url: str, days: int = 7) -> str:
        try:
            workspace, repo_slug = self._parse_repo_url(repo_url)
            commits = self._client().get_commits(workspace, repo_slug, limit=30)
            commit_list = [
                f"{c.get('hash')[:7]}: {c.get('message')}" for c in commits
            ]
            return (
                f"Recent Commits for BitBucket Repository at {repo_url}:\n\n"
                + "\n".join(commit_list)
            )
        except Exception as e:
            return f"Error: {str(e)}"

    def close_issue(self, repo_url: str, issue_number: int) -> str:
        try:
            workspace, repo_slug = self._parse_repo_url(repo_url)
            self._client().update_issue(
                workspace, repo_slug, issue_number, {"state": "resolved"}
            )
            return f"Closed issue in BitBucket Repository: {repo_url}, Issue #{issue_number}"
        except Exception as e:
            return f"Error: {str(e)}"

    def get_my_repos(self) -> str:
        try:
            repos = self._client().get_repositories(self.username)
            repo_list = []
            for repo in repos:
                full_name = repo.get("full_name")
                html_url = repo.get("links", {}).get("html", {}).get("href", "")
                repo_list.append(f"{full_name} - {html_url}")
            return "### My BitBucket Repositories\n\n" + "\n".join(repo_list)
        except Exception as e:
            return f"Error: {str(e)}"
