import os
from typing import Any, Optional

from zephyrex.extensions.source.PRV_Source import AbstractSourceProvider

try:
    import git
except ImportError:
    git = None  # type: ignore[assignment]

try:
    import gitlab
except ImportError:
    gitlab = None  # type: ignore[assignment]

GITLAB_DEFAULT_URL = "https://gitlab.com"
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


class GitLabProvider(AbstractSourceProvider):
    """
    Source-control provider backed by the GitLab REST API (via python-gitlab)
    and local git operations (via GitPython).

    A lightweight, directly-instantiated client: it holds the api key/uri and
    issues synchronous requests to GitLab. GitLab merge requests are exposed
    through the same ``get_repo_pull_requests``/``create_repo_pull_request``
    contract the other source providers share. The optional ``gitpython`` and
    ``python-gitlab`` dependencies degrade gracefully to an informative error
    string when unavailable rather than raising ImportError at import time.
    """

    def get_platform_name(self) -> str:
        return "GitLab"

    def _client(self) -> Any:
        if gitlab is None:
            raise RuntimeError(
                "python-gitlab is not installed; GitLab API operations are unavailable."
            )
        client = gitlab.Gitlab(
            url=self.api_uri or GITLAB_DEFAULT_URL, private_token=self.api_key or None
        )
        if self.api_key:
            client.auth()
        return client

    def _get_project(self, repo_url: str) -> Any:
        base_url = self.api_uri or GITLAB_DEFAULT_URL
        if base_url in repo_url:
            project_path = repo_url.replace(f"{base_url}/", "")
        else:
            parts = repo_url.rstrip("/").split("/")
            project_path = f"{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else repo_url
        return self._client().projects.get(project_path)

    def _format_error(self, exc: Exception) -> str:
        if gitlab is not None and isinstance(exc, gitlab.exceptions.GitlabAuthenticationError):
            return "Error: Authentication failed. Please check your GitLab API key."
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
            return self._format_error(e)

    def get_repo_code_contents(self, repo_url: str) -> str:
        branch: Optional[str] = None
        if "/-/tree/" in repo_url:
            repo_url, branch_path = repo_url.split("/-/tree/", 1)
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
            project = self._get_project(repo_url)
            issue_list = [
                f"#{i.iid}: {i.title}" for i in project.issues.list(state="opened")
            ]
            return f"Open Issues for GitLab Repository at {repo_url}:\n\n" + "\n".join(
                issue_list
            )
        except Exception as e:
            return self._format_error(e)

    def get_repo_issue(self, repo_url: str, issue_number: int) -> str:
        try:
            project = self._get_project(repo_url)
            issue = project.issues.get(issue_number)
            return (
                f"Issue Details for GitLab Repository at {repo_url}\n\n"
                f"{issue.iid}: {issue.title}\n\n{issue.description}"
            )
        except Exception as e:
            return self._format_error(e)

    def create_repo_issue(
        self, repo_url: str, title: str, body: str, assignee: Optional[str] = None
    ) -> str:
        try:
            project = self._get_project(repo_url)
            issue_data = {"title": title, "description": body}
            if assignee:
                users = project.manager.gitlab.users.list(username=assignee)
                if users:
                    issue_data["assignee_ids"] = [users[0].id]
            issue = project.issues.create(issue_data)
            return (
                f"Created new issue in GitLab Repository at {repo_url}\n\n"
                f"{issue.iid}: {issue.title}\n\n{issue.description}"
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
            project = self._get_project(repo_url)
            issue = project.issues.get(issue_number)
            issue.title = title
            issue.description = body
            if assignee:
                users = project.manager.gitlab.users.list(username=assignee)
                if users:
                    issue.assignee_ids = [users[0].id]
            issue.save()
            return (
                f"Updated issue in GitLab Repository at {repo_url}\n\n"
                f"{issue.iid}: {issue.title}\n\n{issue.description}"
            )
        except Exception as e:
            return self._format_error(e)

    def get_repo_pull_requests(self, repo_url: str) -> str:
        try:
            project = self._get_project(repo_url)
            mr_list = [
                f"!{mr.iid}: {mr.title}"
                for mr in project.mergerequests.list(state="opened")
            ]
            return (
                f"Open Merge Requests for GitLab Repository at {repo_url}:\n\n"
                + "\n".join(mr_list)
            )
        except Exception as e:
            return self._format_error(e)

    def create_repo_pull_request(
        self, repo_url: str, title: str, body: str, head: str, base: str
    ) -> str:
        try:
            project = self._get_project(repo_url)
            mr = project.mergerequests.create(
                {
                    "source_branch": head,
                    "target_branch": base,
                    "title": title,
                    "description": body,
                }
            )
            return (
                f"Created new merge request in GitLab Repository at {repo_url}\n\n"
                f"!{mr.iid}: {mr.title}\n\n{mr.description}"
            )
        except Exception as e:
            return self._format_error(e)

    def get_repo_commits(self, repo_url: str, days: int = 7) -> str:
        try:
            project = self._get_project(repo_url)
            commits = project.commits.list(all=True)[:30]
            commit_list = [f"{c.id[:7]}: {c.title}" for c in commits]
            return (
                f"Recent Commits for GitLab Repository at {repo_url}:\n\n"
                + "\n".join(commit_list)
            )
        except Exception as e:
            return self._format_error(e)

    def close_issue(self, repo_url: str, issue_number: int) -> str:
        try:
            project = self._get_project(repo_url)
            issue = project.issues.get(issue_number)
            issue.state_event = "close"
            issue.save()
            return f"Closed issue in GitLab Repository: {repo_url}, Issue #{issue_number}"
        except Exception as e:
            return self._format_error(e)

    def get_my_repos(self) -> str:
        try:
            client = self._client()
            projects = client.projects.list(owned=True, all=True)
            repo_list = [f"{p.path_with_namespace} - {p.web_url}" for p in projects]
            return "### My GitLab Projects\n\n" + "\n".join(repo_list)
        except Exception as e:
            return self._format_error(e)
