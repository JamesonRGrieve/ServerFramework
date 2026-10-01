import os
from typing import Any, Optional

from zephyrex.extensions.source.PRV_Source import AbstractSourceProvider

try:
    import git
except ImportError:
    git = None  # type: ignore[assignment]

try:
    import github as pygithub
    from github import Github
except ImportError:
    pygithub = None  # type: ignore[assignment]
    Github = None  # type: ignore[assignment,misc]

GITHUB_API_BASE_URL = "https://api.github.com"
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


class GitHubProvider(AbstractSourceProvider):
    """
    Source-control provider backed by the GitHub REST API (via PyGithub) and
    local git operations (via GitPython).

    A lightweight, directly-instantiated client: it holds the api key/uri and
    issues synchronous requests to GitHub. The optional ``gitpython`` and
    PyGithub dependencies degrade gracefully to an informative error string
    when unavailable rather than raising ImportError at import time.
    """

    def get_platform_name(self) -> str:
        return "GitHub"

    def _client(self) -> Any:
        if Github is None:
            raise RuntimeError(
                "PyGithub is not installed; GitHub API operations are unavailable."
            )
        return Github(
            base_url=self.api_uri or GITHUB_API_BASE_URL,
            login_or_token=self.api_key or None,
        )

    def _get_repo_from_url(self, repo_url: str) -> Any:
        if "github.com" not in repo_url:
            raise ValueError(f"Not a valid GitHub URL: {repo_url}")

        parts = (
            repo_url.replace("https://github.com/", "").replace(".git", "").split("/")
        )
        if len(parts) < 2:
            raise ValueError(f"Could not parse GitHub repository URL: {repo_url}")
        return self._client().get_repo(f"{parts[0]}/{parts[1]}")

    def _format_error(self, exc: Exception) -> str:
        if pygithub is not None and isinstance(exc, pygithub.GithubException):
            if exc.status == 401:
                return "Error: Authentication failed. Please check your GitHub API key."
        return f"Error: {str(exc)}"

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
        if "/tree/" in repo_url:
            repo_url, branch_path = repo_url.split("/tree/", 1)
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
            repo = self._get_repo_from_url(repo_url)
            issue_list = [f"#{i.number}: {i.title}" for i in repo.get_issues(state="open")]
            return f"Open Issues for GitHub Repository at {repo_url}:\n\n" + "\n".join(
                issue_list
            )
        except Exception as e:
            return self._format_error(e)

    def get_repo_issue(self, repo_url: str, issue_number: int) -> str:
        try:
            repo = self._get_repo_from_url(repo_url)
            issue = repo.get_issue(issue_number)
            return (
                f"Issue Details for GitHub Repository at {repo_url}\n\n"
                f"#{issue.number}: {issue.title}\n\n{issue.body}"
            )
        except Exception as e:
            return self._format_error(e)

    def create_repo_issue(
        self, repo_url: str, title: str, body: str, assignee: Optional[str] = None
    ) -> str:
        try:
            repo = self._get_repo_from_url(repo_url)
            issue = repo.create_issue(title=title, body=body)
            if assignee:
                issue.add_to_assignees(assignee)
            return (
                f"Created new issue in GitHub Repository at {repo_url}\n\n"
                f"#{issue.number}: {issue.title}\n\n{issue.body}"
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
            repo = self._get_repo_from_url(repo_url)
            issue = repo.get_issue(issue_number)
            issue.edit(title=title, body=body)
            if assignee:
                issue.add_to_assignees(assignee)
            return (
                f"Updated issue in GitHub Repository at {repo_url}\n\n"
                f"#{issue.number}: {issue.title}\n\n{issue.body}"
            )
        except Exception as e:
            return self._format_error(e)

    def get_repo_pull_requests(self, repo_url: str) -> str:
        try:
            repo = self._get_repo_from_url(repo_url)
            pr_list = [f"#{pr.number}: {pr.title}" for pr in repo.get_pulls(state="open")]
            return (
                f"Open Pull Requests for GitHub Repository at {repo_url}:\n\n"
                + "\n".join(pr_list)
            )
        except Exception as e:
            return self._format_error(e)

    def create_repo_pull_request(
        self, repo_url: str, title: str, body: str, head: str, base: str
    ) -> str:
        try:
            repo = self._get_repo_from_url(repo_url)
            pr = repo.create_pull(title=title, body=body, head=head, base=base)
            return (
                f"Created new pull request in GitHub Repository at {repo_url}\n\n"
                f"#{pr.number}: {pr.title}\n\n{pr.body}"
            )
        except Exception as e:
            return self._format_error(e)

    def get_repo_commits(self, repo_url: str, days: int = 7) -> str:
        try:
            repo = self._get_repo_from_url(repo_url)
            commits = list(repo.get_commits())[:30]
            commit_list = [
                f"{c.sha[:7]}: {c.commit.message.splitlines()[0]}" for c in commits
            ]
            return (
                f"Recent Commits for GitHub Repository at {repo_url}:\n\n"
                + "\n".join(commit_list)
            )
        except Exception as e:
            return self._format_error(e)

    def close_issue(self, repo_url: str, issue_number: int) -> str:
        try:
            repo = self._get_repo_from_url(repo_url)
            repo.get_issue(issue_number).edit(state="closed")
            return f"Closed issue in GitHub Repository: {repo_url}, Issue #{issue_number}"
        except Exception as e:
            return self._format_error(e)

    def get_my_repos(self) -> str:
        try:
            client = self._client()
            repo_list = [f"{r.full_name} - {r.html_url}" for r in client.get_user().get_repos()]
            return "### My GitHub Repositories\n\n" + "\n".join(repo_list)
        except Exception as e:
            return self._format_error(e)
