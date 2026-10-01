from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency, SYS_Dependency
from zephyrex.lib.Logging import logger


class EXT_Source(AbstractStaticExtension):
    """
    Source code repository extension for AGInfrastructure.

    Provides a way to interact with various source control systems like
    GitHub, GitLab, BitBucket, and Forgejo, currently supporting repository
    cloning, issue and pull-request management, and commit history, with
    plans for additional platforms.

    Component loading (DB, BLL, EP) is handled automatically by the import
    system based on file naming conventions.
    """

    # Extension metadata
    name = "source"
    version = "1.0.0"
    description = "Extension for source code repository interactions"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="labels",
            friendly_name="Labels Extension",
            optional=True,
            reason="Optional labels for source code categorization",
        )
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="gitpython",
            friendly_name="GitPython Library",
            optional=True,
            semver=">=3.1.0",
            reason="Git repository operations",
        ),
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            semver=">=2.28.0",
            reason="HTTP requests for repository APIs",
        ),
    ]

    sys_dependencies = [
        SYS_Dependency(
            name="git",
            friendly_name="Git Version Control",
            reason="Git command-line tools for repository operations",
            apt="git",
            brew="git",
            choco="git",
        ),
    ]

    # Define what capabilities this extension provides
    capabilities = [
        "repository_access",
        "code_analysis",
        "version_control",
        "commit_management",
        "branch_operations",
    ]

    # Define database tables (none for this extension)
    db_tables: List[Any] = []

    def __init__(
        self,
        source_platform: str = "github",
        api_key: str = "",
        api_uri: str = "",
        **kwargs: Any,
    ):
        super().__init__(**kwargs)

        self.source_platform = source_platform.lower()
        self.api_key = api_key
        self.api_uri = api_uri
        self.provider = None
        self.commands: Dict[str, Any] = {}

        # Give each instance its own copy of the mutable class-level
        # capability list so register_capability() on one instance can never
        # leak into the class default (and therefore into sibling instances).
        self.capabilities = list(type(self).capabilities)

    def on_initialize(self) -> bool:
        """Initialize the Source extension with the appropriate provider."""
        logger.debug("Initializing Source Extension...")

        try:
            self._create_provider()
            self._register_commands()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("Source extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Source extension: {str(e)}")
            return False

    def _create_provider(self) -> None:
        """Create the appropriate source control provider based on source_platform."""
        try:
            provider_mapping = {
                "github": ("zephyrex.extensions.source.PRV_GitHub", "GitHubProvider"),
                "gitlab": ("zephyrex.extensions.source.PRV_GitLab", "GitLabProvider"),
                "bitbucket": (
                    "zephyrex.extensions.source.PRV_BitBucket",
                    "BitBucketProvider",
                ),
                "forgejo": (
                    "zephyrex.extensions.source.PRV_Forgejo",
                    "ForgejoProvider",
                ),
            }

            if self.source_platform not in provider_mapping:
                logger.error(
                    f"Unsupported source control platform: {self.source_platform}"
                )
                self.provider = None
                return

            module_name, class_name = provider_mapping[self.source_platform]

            try:
                module = __import__(module_name, fromlist=[class_name])
                provider_class = getattr(module, class_name)

                self.provider = provider_class(
                    api_key=self.api_key,
                    api_uri=self.api_uri,
                    extension_id=self.name,
                    working_directory=getattr(self, "working_directory", ""),
                    agent_name=getattr(self, "agent_name", ""),
                    conversation_name=getattr(self, "conversation_name", ""),
                    **getattr(self, "settings", {}),
                )
                logger.debug(
                    f"Source control provider for {self.source_platform} created successfully"
                )

            except ImportError as e:
                logger.warning(
                    f"Could not import source provider for {self.source_platform}: {e}"
                )
                self.provider = None
            except Exception as e:
                logger.error(f"Error creating source provider: {str(e)}")
                self.provider = None

        except Exception as e:
            logger.error(f"Error in provider creation: {str(e)}")
            self.provider = None

    def _register_commands(self) -> None:
        """Register commands based on available provider."""
        if self.provider and hasattr(self.provider, "commands"):
            self.commands = self.provider.commands
        else:
            # Provide placeholder commands that warn about missing provider
            platform_name = self.source_platform.upper()
            self.commands = {
                f"Clone {platform_name} Repository": self._no_provider_warning,
                f"Manage {platform_name} Repository": self._no_provider_warning,
            }

    async def _no_provider_warning(self, *args: Any, **kwargs: Any) -> str:
        """Return a warning message when no provider is configured."""
        return f"No source control provider available for {self.source_platform}. Please check your configuration."

    def register_capability(self, capability: str) -> None:
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @ability("clone_repo")
    async def clone_repo(self, repo_url: str) -> str:
        """Clone (or pull latest for) a repository into the working directory."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.clone_repo(repo_url)
        except Exception as e:
            logger.error(f"Error cloning repository: {e}")
            return f"Error cloning repository: {str(e)}"

    @ability("get_repo_code_contents")
    async def get_repo_code_contents(self, repo_url: str) -> str:
        """Get the code contents of a repository as a markdown document."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_repo_code_contents(repo_url)
        except Exception as e:
            logger.error(f"Error getting repository code contents: {e}")
            return f"Error getting repository code contents: {str(e)}"

    @ability("get_repo_issues")
    async def get_repo_issues(self, repo_url: str) -> str:
        """Get the open issues for a repository."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_repo_issues(repo_url)
        except Exception as e:
            logger.error(f"Error getting repository issues: {e}")
            return f"Error getting repository issues: {str(e)}"

    @ability("get_repo_issue")
    async def get_repo_issue(self, repo_url: str, issue_number: int) -> str:
        """Get a single issue's details."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_repo_issue(repo_url, issue_number)
        except Exception as e:
            logger.error(f"Error getting repository issue: {e}")
            return f"Error getting repository issue: {str(e)}"

    @ability("create_repo_issue")
    async def create_repo_issue(
        self, repo_url: str, title: str, body: str, assignee: Optional[str] = None
    ) -> str:
        """Create a new issue in a repository."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.create_repo_issue(repo_url, title, body, assignee)
        except Exception as e:
            logger.error(f"Error creating repository issue: {e}")
            return f"Error creating repository issue: {str(e)}"

    @ability("update_repo_issue")
    async def update_repo_issue(
        self,
        repo_url: str,
        issue_number: int,
        title: str,
        body: str,
        assignee: Optional[str] = None,
    ) -> str:
        """Update an existing issue in a repository."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.update_repo_issue(
                repo_url, issue_number, title, body, assignee
            )
        except Exception as e:
            logger.error(f"Error updating repository issue: {e}")
            return f"Error updating repository issue: {str(e)}"

    @ability("get_repo_pull_requests")
    async def get_repo_pull_requests(self, repo_url: str) -> str:
        """Get the open pull requests for a repository."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_repo_pull_requests(repo_url)
        except Exception as e:
            logger.error(f"Error getting repository pull requests: {e}")
            return f"Error getting repository pull requests: {str(e)}"

    @ability("create_repo_pull_request")
    async def create_repo_pull_request(
        self, repo_url: str, title: str, body: str, head: str, base: str
    ) -> str:
        """Create a new pull request in a repository."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.create_repo_pull_request(repo_url, title, body, head, base)
        except Exception as e:
            logger.error(f"Error creating repository pull request: {e}")
            return f"Error creating repository pull request: {str(e)}"

    @ability("get_repo_commits")
    async def get_repo_commits(self, repo_url: str, days: int = 7) -> str:
        """Get recent commits for a repository."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_repo_commits(repo_url, days)
        except Exception as e:
            logger.error(f"Error getting repository commits: {e}")
            return f"Error getting repository commits: {str(e)}"

    @ability("close_issue")
    async def close_issue(self, repo_url: str, issue_number: int) -> str:
        """Close an issue in a repository."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.close_issue(repo_url, issue_number)
        except Exception as e:
            logger.error(f"Error closing repository issue: {e}")
            return f"Error closing repository issue: {str(e)}"

    @ability("get_my_repos")
    async def get_my_repos(self) -> str:
        """List repositories owned by the authenticated user."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_my_repos()
        except Exception as e:
            logger.error(f"Error listing repositories: {e}")
            return f"Error listing repositories: {str(e)}"

    def on_start(self) -> bool:
        """Start the Source extension."""
        try:
            logger.debug("Source extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Source extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Source extension."""
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Source extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Source extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import requests  # noqa: F401
        except ImportError:
            issues.append(
                "Requests library not installed - API communications will not work"
            )

        # Platform-specific validation
        if not self.source_platform:
            issues.append("Source control platform not specified")
        elif self.source_platform not in ["github", "gitlab", "bitbucket", "forgejo"]:
            issues.append(f"Unsupported source control platform: {self.source_platform}")

        # Check required credentials
        if not self.api_key:
            issues.append(
                f"{self.source_platform.title()} integration requires an API key"
            )

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "source:read",
            "source:clone",
            "source:issues",
            "source:pull_requests",
            "repository:access",
        ]

    def on_startup(self) -> None:
        """Called during application startup."""
        logger.debug("Source extension startup hook called")

    def on_shutdown(self) -> None:
        """Called during application shutdown."""
        logger.debug("Source extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
