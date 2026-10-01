from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AbstractSourceProvider(ABC):
    """
    Abstract base class for all source-control providers used by the Source
    extension (currently GitHub, GitLab, BitBucket, and Forgejo).

    Concrete providers are lightweight, directly-instantiated clients that
    hold their own credentials/config (api key, api uri, working directory)
    and expose synchronous repository operations. EXT_Source's async
    abilities call into these methods directly (no await) so a provider's
    return values are plain strings rather than awaitables.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        extension_id: Optional[str] = None,
        working_directory: str = "",
        agent_name: str = "",
        conversation_name: str = "",
        activity_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.api_uri = api_uri
        self.extension_id = extension_id
        self.working_directory = working_directory or "."
        self.agent_name = agent_name
        self.conversation_name = conversation_name
        self.activity_id = activity_id
        self.settings: Dict[str, Any] = kwargs

        platform = self.get_platform_name()
        self.commands = {
            f"Clone {platform} Repository": self.clone_repo,
            f"Get {platform} Repository Code Contents": self.get_repo_code_contents,
            f"Get {platform} Repository Issues": self.get_repo_issues,
            f"Get {platform} Repository Issue": self.get_repo_issue,
            f"Create {platform} Repository Issue": self.create_repo_issue,
            f"Update {platform} Repository Issue": self.update_repo_issue,
            f"Get {platform} Repository Pull Requests": self.get_repo_pull_requests,
            f"Create {platform} Repository Pull Request": self.create_repo_pull_request,
            f"Get {platform} Repository Commits": self.get_repo_commits,
            f"Close {platform} Issue": self.close_issue,
            f"Get List of My {platform} Repositories": self.get_my_repos,
        }

    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the name of the source control platform this provider interacts with."""

    @abstractmethod
    def clone_repo(self, repo_url: str) -> str:
        """Clone (or pull latest for) a repository into the working directory."""

    @abstractmethod
    def get_repo_code_contents(self, repo_url: str) -> str:
        """Get the code contents of a repository as a markdown document."""

    @abstractmethod
    def get_repo_issues(self, repo_url: str) -> str:
        """Get the open issues for a repository."""

    @abstractmethod
    def get_repo_issue(self, repo_url: str, issue_number: int) -> str:
        """Get a single issue's details."""

    @abstractmethod
    def create_repo_issue(
        self, repo_url: str, title: str, body: str, assignee: Optional[str] = None
    ) -> str:
        """Create a new issue in a repository."""

    @abstractmethod
    def update_repo_issue(
        self,
        repo_url: str,
        issue_number: int,
        title: str,
        body: str,
        assignee: Optional[str] = None,
    ) -> str:
        """Update an existing issue in a repository."""

    @abstractmethod
    def get_repo_pull_requests(self, repo_url: str) -> str:
        """Get the open pull requests for a repository."""

    @abstractmethod
    def create_repo_pull_request(
        self, repo_url: str, title: str, body: str, head: str, base: str
    ) -> str:
        """Create a new pull request in a repository."""

    @abstractmethod
    def get_repo_commits(self, repo_url: str, days: int = 7) -> str:
        """Get recent commits for a repository."""

    @abstractmethod
    def close_issue(self, repo_url: str, issue_number: int) -> str:
        """Close an issue in a repository."""

    @abstractmethod
    def get_my_repos(self) -> str:
        """List repositories owned by the authenticated user."""

    @staticmethod
    def services() -> List[str]:
        """Return a list of services provided by this provider."""
        return ["source", "repository", "version_control"]

    def get_extension_info(self) -> Dict[str, Any]:
        """Get information about the source extension."""
        return {
            "name": "Source",
            "description": f"Source control extension for {self.get_platform_name()} repositories",
        }
