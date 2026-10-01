from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.source.EXT_Source import EXT_Source
from zephyrex.extensions.source.PRV_Forgejo import ForgejoProvider


class TestSourceExtension:
    """
    Test suite for the Source extension.

    Tests extension metadata/configuration, source control provider creation
    and integration (GitHub, GitLab, BitBucket), repository abilities (clone,
    code contents, issues, pull requests, commits), capability management,
    and extension lifecycle/config validation.
    """

    @pytest.fixture
    def extension(self):
        """Create an EXT_Source instance for testing."""
        return EXT_Source()

    @pytest.fixture
    def mock_source_provider(self):
        """Mock source control provider."""
        mock_provider = MagicMock()
        mock_provider.clone_repo.return_value = "Cloned https://github.com/user/repo to /tmp/repo"
        mock_provider.get_repo_code_contents.return_value = "# Repository Content\n\n..."
        mock_provider.get_repo_issues.return_value = "Open Issues:\n\n#1: Bug report"
        mock_provider.get_repo_issue.return_value = "#1: Bug report\n\nDetails here"
        mock_provider.create_repo_issue.return_value = "Created new issue\n\n#2: New issue"
        mock_provider.update_repo_issue.return_value = "Updated issue\n\n#1: Updated title"
        mock_provider.get_repo_pull_requests.return_value = "Open Pull Requests:\n\n#5: Feature"
        mock_provider.create_repo_pull_request.return_value = "Created new pull request\n\n#6: New PR"
        mock_provider.get_repo_commits.return_value = "Recent Commits:\n\nabc1234: Initial commit"
        mock_provider.close_issue.return_value = "Closed issue #1"
        mock_provider.get_my_repos.return_value = "### My Repositories\n\nuser/repo"
        mock_provider.commands = {
            "Clone GitHub Repository": mock_provider.clone_repo,
            "Get GitHub Repository Issues": mock_provider.get_repo_issues,
        }
        return mock_provider

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "source"
        assert extension.version == "1.0.0"
        assert "source code repository" in extension.description
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "sys_dependencies")

    def test_dependencies(self, extension):
        """Test that dependencies are properly structured."""
        ext_deps = extension.ext_dependencies
        assert len(ext_deps) == 1

        dep_names = {dep.name for dep in ext_deps}
        assert "labels" in dep_names
        for dep in ext_deps:
            if dep.name == "labels":
                assert dep.optional is True

        pip_deps = extension.pip_dependencies
        assert len(pip_deps) == 2

        pip_dep_names = {dep.name for dep in pip_deps}
        assert "gitpython" in pip_dep_names
        assert "requests" in pip_dep_names

        for dep in pip_deps:
            if dep.name == "gitpython":
                assert dep.optional is True
            elif dep.name == "requests":
                assert dep.optional is False

        sys_deps = extension.sys_dependencies
        assert len(sys_deps) == 1
        assert sys_deps[0].name == "git"

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "repository_access",
            "code_analysis",
            "version_control",
            "commit_management",
            "branch_operations",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension attributes are properly initialized."""
        assert hasattr(extension, "source_platform")
        assert hasattr(extension, "api_key")
        assert hasattr(extension, "api_uri")
        assert hasattr(extension, "provider")
        assert hasattr(extension, "commands")
        assert extension.provider is None

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.source_platform == "github"
        assert extension.api_key == ""
        assert extension.api_uri == ""

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0

    @patch("zephyrex.extensions.source.EXT_Source.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.source.EXT_Source.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure handling."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_github_success(self, extension, mock_source_provider):
        """Test successful GitHub provider creation."""
        extension.source_platform = "github"
        extension.api_key = "test-api-key"

        with patch(
            "zephyrex.extensions.source.PRV_GitHub.GitHubProvider",
            return_value=mock_source_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_source_provider

    def test_create_provider_gitlab_success(self, extension, mock_source_provider):
        """Test successful GitLab provider creation."""
        extension.source_platform = "gitlab"
        extension.api_key = "test-api-key"

        with patch(
            "zephyrex.extensions.source.PRV_GitLab.GitLabProvider",
            return_value=mock_source_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_source_provider

    def test_create_provider_bitbucket_success(self, extension, mock_source_provider):
        """Test successful BitBucket provider creation."""
        extension.source_platform = "bitbucket"
        extension.api_key = "test-api-key"

        with patch(
            "zephyrex.extensions.source.PRV_BitBucket.BitBucketProvider",
            return_value=mock_source_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_source_provider

    def test_create_provider_forgejo_success(self, extension, mock_source_provider):
        """Test successful Forgejo provider creation."""
        extension.source_platform = "forgejo"
        extension.api_key = "test-api-key"
        extension.api_uri = "https://git.example.com"

        with patch(
            "zephyrex.extensions.source.PRV_Forgejo.ForgejoProvider",
            return_value=mock_source_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_source_provider

    def test_create_provider_unsupported_platform(self, extension):
        """Test provider creation with an unsupported platform."""
        extension.source_platform = "unsupported"
        extension._create_provider()

        assert extension.provider is None

    def test_create_provider_import_failure(self, extension):
        """Test provider creation when the GitHub provider import/construction fails."""
        with patch(
            "zephyrex.extensions.source.PRV_GitHub.GitHubProvider",
            side_effect=ImportError("Mock import error"),
        ):
            extension._create_provider()

            assert extension.provider is None

    def test_github_provider_creation_parameters(self, extension, mock_source_provider):
        """Test that the GitHub provider is created with correct parameters."""
        extension.source_platform = "github"
        extension.api_key = "test-api-key"
        extension.api_uri = "https://api.github.com"

        with patch(
            "zephyrex.extensions.source.PRV_GitHub.GitHubProvider",
            return_value=mock_source_provider,
        ) as mock_github_class:
            extension._create_provider()

            mock_github_class.assert_called_once_with(
                api_key="test-api-key",
                api_uri="https://api.github.com",
                extension_id="source",
                working_directory="",
                agent_name="",
                conversation_name="",
            )

    def test_register_commands_with_provider(self, extension, mock_source_provider):
        """Test command registration with an available provider."""
        extension.provider = mock_source_provider
        extension._register_commands()

        assert extension.commands == mock_source_provider.commands

    def test_register_commands_without_provider(self, extension):
        """Test command registration without a provider."""
        extension.source_platform = "github"
        extension.provider = None
        extension._register_commands()

        assert len(extension.commands) > 0
        for command_name in extension.commands:
            assert "GITHUB" in command_name

    def test_capability_management(self, extension):
        """Test capability management methods."""
        extension.register_capability("test_capability")
        assert "test_capability" in extension.capabilities

        capabilities = extension.get_registered_capabilities()
        assert isinstance(capabilities, set)
        assert "test_capability" in capabilities

        capabilities = extension.get_capabilities()
        assert isinstance(capabilities, set)

    def test_capability_registration_is_instance_scoped(self):
        """Registering a capability on one instance must not leak to another."""
        first = EXT_Source()
        second = EXT_Source()

        first.register_capability("instance_only_capability")

        assert "instance_only_capability" in first.capabilities
        assert "instance_only_capability" not in second.capabilities

    @pytest.mark.asyncio
    async def test_clone_repo_with_provider(self, extension, mock_source_provider):
        """Test cloning a repository with a provider."""
        extension.provider = mock_source_provider

        result = await extension.clone_repo("https://github.com/user/repo")

        assert "Cloned" in result
        mock_source_provider.clone_repo.assert_called_once_with(
            "https://github.com/user/repo"
        )

    @pytest.mark.asyncio
    async def test_clone_repo_without_provider(self, extension):
        """Test cloning a repository without a provider."""
        extension.provider = None

        result = await extension.clone_repo("https://github.com/user/repo")

        assert "No source control provider available" in result

    @pytest.mark.asyncio
    async def test_clone_repo_error(self, extension, mock_source_provider):
        """Test cloning a repository when the provider raises."""
        mock_source_provider.clone_repo.side_effect = Exception("Provider error")
        extension.provider = mock_source_provider

        result = await extension.clone_repo("https://github.com/user/repo")

        assert "Error cloning repository" in result

    @pytest.mark.asyncio
    async def test_get_repo_code_contents_success(self, extension, mock_source_provider):
        """Test getting repository code contents."""
        extension.provider = mock_source_provider

        result = await extension.get_repo_code_contents("https://github.com/user/repo")

        assert "Repository Content" in result
        mock_source_provider.get_repo_code_contents.assert_called_once_with(
            "https://github.com/user/repo"
        )

    @pytest.mark.asyncio
    async def test_get_repo_issues_success(self, extension, mock_source_provider):
        """Test getting repository issues."""
        extension.provider = mock_source_provider

        result = await extension.get_repo_issues("https://github.com/user/repo")

        assert "Open Issues" in result
        mock_source_provider.get_repo_issues.assert_called_once_with(
            "https://github.com/user/repo"
        )

    @pytest.mark.asyncio
    async def test_get_repo_issue_success(self, extension, mock_source_provider):
        """Test getting a single repository issue."""
        extension.provider = mock_source_provider

        result = await extension.get_repo_issue("https://github.com/user/repo", 1)

        assert "Bug report" in result
        mock_source_provider.get_repo_issue.assert_called_once_with(
            "https://github.com/user/repo", 1
        )

    @pytest.mark.asyncio
    async def test_create_repo_issue_success(self, extension, mock_source_provider):
        """Test creating a repository issue."""
        extension.provider = mock_source_provider

        result = await extension.create_repo_issue(
            "https://github.com/user/repo", "New issue", "Body text", "assignee"
        )

        assert "Created new issue" in result
        mock_source_provider.create_repo_issue.assert_called_once_with(
            "https://github.com/user/repo", "New issue", "Body text", "assignee"
        )

    @pytest.mark.asyncio
    async def test_update_repo_issue_success(self, extension, mock_source_provider):
        """Test updating a repository issue."""
        extension.provider = mock_source_provider

        result = await extension.update_repo_issue(
            "https://github.com/user/repo", 1, "Updated title", "Updated body"
        )

        assert "Updated issue" in result
        mock_source_provider.update_repo_issue.assert_called_once_with(
            "https://github.com/user/repo", 1, "Updated title", "Updated body", None
        )

    @pytest.mark.asyncio
    async def test_get_repo_pull_requests_success(self, extension, mock_source_provider):
        """Test getting repository pull requests."""
        extension.provider = mock_source_provider

        result = await extension.get_repo_pull_requests("https://github.com/user/repo")

        assert "Open Pull Requests" in result
        mock_source_provider.get_repo_pull_requests.assert_called_once_with(
            "https://github.com/user/repo"
        )

    @pytest.mark.asyncio
    async def test_create_repo_pull_request_success(self, extension, mock_source_provider):
        """Test creating a repository pull request."""
        extension.provider = mock_source_provider

        result = await extension.create_repo_pull_request(
            "https://github.com/user/repo", "New PR", "Body text", "feature", "main"
        )

        assert "Created new pull request" in result
        mock_source_provider.create_repo_pull_request.assert_called_once_with(
            "https://github.com/user/repo", "New PR", "Body text", "feature", "main"
        )

    @pytest.mark.asyncio
    async def test_get_repo_commits_success(self, extension, mock_source_provider):
        """Test getting repository commits."""
        extension.provider = mock_source_provider

        result = await extension.get_repo_commits("https://github.com/user/repo", days=14)

        assert "Recent Commits" in result
        mock_source_provider.get_repo_commits.assert_called_once_with(
            "https://github.com/user/repo", 14
        )

    @pytest.mark.asyncio
    async def test_close_issue_success(self, extension, mock_source_provider):
        """Test closing a repository issue."""
        extension.provider = mock_source_provider

        result = await extension.close_issue("https://github.com/user/repo", 1)

        assert "Closed issue" in result
        mock_source_provider.close_issue.assert_called_once_with(
            "https://github.com/user/repo", 1
        )

    @pytest.mark.asyncio
    async def test_get_my_repos_success(self, extension, mock_source_provider):
        """Test listing the authenticated user's repositories."""
        extension.provider = mock_source_provider

        result = await extension.get_my_repos()

        assert "My Repositories" in result
        mock_source_provider.get_my_repos.assert_called_once()

    @pytest.mark.asyncio
    async def test_repository_operations_without_provider(self, extension):
        """Test repository operations without a provider."""
        extension.provider = None

        operations = [
            extension.clone_repo("https://github.com/user/repo"),
            extension.get_repo_code_contents("https://github.com/user/repo"),
            extension.get_repo_issues("https://github.com/user/repo"),
            extension.get_repo_issue("https://github.com/user/repo", 1),
            extension.create_repo_issue("https://github.com/user/repo", "t", "b"),
            extension.update_repo_issue("https://github.com/user/repo", 1, "t", "b"),
            extension.get_repo_pull_requests("https://github.com/user/repo"),
            extension.create_repo_pull_request(
                "https://github.com/user/repo", "t", "b", "head", "base"
            ),
            extension.get_repo_commits("https://github.com/user/repo"),
            extension.close_issue("https://github.com/user/repo", 1),
            extension.get_my_repos(),
        ]

        for operation in operations:
            result = await operation
            assert "No source control provider available" in result

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test the no-provider warning message."""
        extension.source_platform = "test"

        result = await extension._no_provider_warning()

        assert "No source control provider available for test" in result

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        assert extension.on_start() is True

        extension.provider = MagicMock()
        assert extension.on_stop() is True
        assert extension.provider is None

        extension.on_startup()
        extension.on_shutdown()

    def test_provider_cleanup_on_stop(self, extension, mock_source_provider):
        """Test that the provider is cleaned up when the extension stops."""
        extension.provider = mock_source_provider

        result = extension.on_stop()

        assert result is True
        assert extension.provider is None

    @patch("zephyrex.extensions.source.EXT_Source.logger")
    def test_extension_startup_shutdown_hooks(self, mock_logger, extension):
        """Test startup and shutdown hooks log the expected messages."""
        extension.on_startup()
        mock_logger.debug.assert_called_with("Source extension startup hook called")

        extension.on_shutdown()
        mock_logger.debug.assert_called_with("Source extension shutdown hook called")

    def test_validate_config_all_available(self):
        """Test configuration validation when all dependencies are available."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_Source(
                source_platform="github",
                api_key="test-api-key",
                api_uri="https://api.github.com",
            )
            issues = extension.validate_config()

            assert len(issues) == 0

    def test_validate_config_missing_requests(self):
        """Test configuration validation when requests is missing."""

        def mock_import(name, *args, **kwargs):
            if name == "requests":
                raise ImportError("No module named 'requests'")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            extension = EXT_Source(api_key="test-api-key")
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "requests" in issue_text

    def test_validate_config_unsupported_platform(self):
        """Test configuration validation with an unsupported platform."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_Source(source_platform="unsupported", api_key="test-api-key")
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "unsupported" in issue_text

    def test_validate_config_missing_api_key(self):
        """Test configuration validation with a missing API key."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_Source(source_platform="github")
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "github" in issue_text
            assert "api key" in issue_text

    def test_get_required_permissions(self, extension):
        """Test getting required permissions."""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 5
        assert "source:read" in permissions
        assert "source:clone" in permissions
        assert "source:issues" in permissions
        assert "source:pull_requests" in permissions
        assert "repository:access" in permissions

    def test_has_capability(self, extension):
        """Test capability checking."""
        for capability in extension.capabilities:
            assert extension.has_capability(capability) is True

        assert extension.has_capability("non_existent_capability") is False

    def test_platform_specific_initialization(self):
        """Test initialization with the supported platforms."""
        for platform in ["github", "gitlab", "bitbucket", "forgejo"]:
            extension = EXT_Source(source_platform=platform)
            assert extension.source_platform == platform

    def test_platform_is_lowercased(self):
        """Test that the source platform is normalized to lower case."""
        extension = EXT_Source(source_platform="GITHUB")
        assert extension.source_platform == "github"

    def test_full_initialization_flow(self, extension):
        """Test the complete initialization flow."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()

            assert result is True

    def test_custom_configuration(self):
        """Test extension with custom configuration via constructor."""
        extension = EXT_Source(
            source_platform="gitlab",
            api_key="custom-api-key",
            api_uri="https://gitlab.example.com",
        )

        assert extension.source_platform == "gitlab"
        assert extension.api_key == "custom-api-key"
        assert extension.api_uri == "https://gitlab.example.com"


def _mock_response(status_code=200, json_data=None, text=""):
    """Build a MagicMock standing in for a `requests.Response`."""
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    if json_data is not None:
        response.content = b"{}"
        response.json.return_value = json_data
    else:
        response.content = b""
    return response


class TestForgejoProvider:
    """
    Test suite for the Forgejo source-control provider.

    Forgejo is self-hosted (no public default host), authenticated by a
    token, and its REST API is exercised directly through `requests` rather
    than a platform SDK, so its abilities are tested here by mocking
    `requests.request` at the module boundary.
    """

    @pytest.fixture
    def provider(self):
        """Create a ForgejoProvider pointed at a fake self-hosted instance."""
        return ForgejoProvider(
            api_key="test-token",
            api_uri="https://git.example.com",
            working_directory="/tmp/forgejo-test",
        )

    def test_get_platform_name(self, provider):
        assert provider.get_platform_name() == "Forgejo"

    def test_commands_registered_for_platform(self, provider):
        assert "Clone Forgejo Repository" in provider.commands
        assert "Get Forgejo Repository Issues" in provider.commands
        assert "Get List of My Forgejo Repositories" in provider.commands

    def test_services(self, provider):
        assert provider.services() == ["source", "repository", "version_control"]

    def test_get_extension_info(self, provider):
        info = provider.get_extension_info()
        assert info["name"] == "Source"
        assert "Forgejo" in info["description"]

    def test_api_base_requires_api_uri(self):
        provider = ForgejoProvider(api_key="test-token")
        with pytest.raises(RuntimeError, match="api_uri"):
            provider._api_base()

    def test_headers_include_token(self, provider):
        headers = provider._headers()
        assert headers["Authorization"] == "token test-token"

    def test_headers_without_api_key(self):
        provider = ForgejoProvider(api_uri="https://git.example.com")
        headers = provider._headers()
        assert "Authorization" not in headers

    def test_parse_repo_url(self, provider):
        assert provider._parse_repo_url(
            "https://git.example.com/myorg/myrepo"
        ) == ("myorg", "myrepo")
        assert provider._parse_repo_url(
            "https://git.example.com/myorg/myrepo.git"
        ) == ("myorg", "myrepo")

    def test_parse_repo_url_invalid(self, provider):
        with pytest.raises(ValueError, match="Could not parse"):
            provider._parse_repo_url("https://git.example.com/onlyorg")

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_get_repo_issues_success(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(
            json_data=[{"number": 1, "title": "Bug report"}]
        )

        result = provider.get_repo_issues("https://git.example.com/myorg/myrepo")

        assert "Open Issues for Forgejo Repository" in result
        assert "#1: Bug report" in result
        called_url = mock_requests.request.call_args.args[1]
        assert called_url == "https://git.example.com/api/v1/repos/myorg/myrepo/issues"
        assert mock_requests.request.call_args.kwargs["timeout"] == 30

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_get_repo_issues_error_unauthorized(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(status_code=401)

        result = provider.get_repo_issues("https://git.example.com/myorg/myrepo")

        assert result.startswith("Error: Authentication failed")

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_get_repo_issues_error_server_failure(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(
            status_code=500, text="internal error"
        )

        result = provider.get_repo_issues("https://git.example.com/myorg/myrepo")

        assert result.startswith("Error:")
        assert "500" in result

    def test_get_repo_issues_missing_api_uri(self):
        provider = ForgejoProvider(api_key="test-token")

        result = provider.get_repo_issues("https://git.example.com/myorg/myrepo")

        assert result.startswith("Error:")
        assert "api_uri" in result

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_get_repo_issue_success(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(
            json_data={"number": 1, "title": "Bug report", "body": "Details here"}
        )

        result = provider.get_repo_issue("https://git.example.com/myorg/myrepo", 1)

        assert "#1: Bug report" in result
        assert "Details here" in result

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_get_repo_issue_error(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(status_code=404, text="not found")

        result = provider.get_repo_issue("https://git.example.com/myorg/myrepo", 999)

        assert result.startswith("Error:")

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_create_repo_issue_success_with_assignee(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(
            json_data={"number": 2, "title": "New issue", "body": "Body text"}
        )

        result = provider.create_repo_issue(
            "https://git.example.com/myorg/myrepo", "New issue", "Body text", "someuser"
        )

        assert "Created new issue" in result
        assert "#2: New issue" in result
        payload = mock_requests.request.call_args.kwargs["json"]
        assert payload["assignees"] == ["someuser"]

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_create_repo_issue_error(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(status_code=403, text="forbidden")

        result = provider.create_repo_issue(
            "https://git.example.com/myorg/myrepo", "New issue", "Body text"
        )

        assert result.startswith("Error:")

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_update_repo_issue_success(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(
            json_data={"number": 1, "title": "Updated title", "body": "Updated body"}
        )

        result = provider.update_repo_issue(
            "https://git.example.com/myorg/myrepo", 1, "Updated title", "Updated body"
        )

        assert "Updated issue" in result
        assert "Updated title" in result

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_update_repo_issue_error(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(status_code=401)

        result = provider.update_repo_issue(
            "https://git.example.com/myorg/myrepo", 1, "Updated title", "Updated body"
        )

        assert result.startswith("Error: Authentication failed")

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_get_repo_pull_requests_success(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(
            json_data=[{"number": 5, "title": "Feature"}]
        )

        result = provider.get_repo_pull_requests("https://git.example.com/myorg/myrepo")

        assert "Open Pull Requests for Forgejo Repository" in result
        assert "#5: Feature" in result

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_get_repo_pull_requests_error(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(status_code=500, text="boom")

        result = provider.get_repo_pull_requests("https://git.example.com/myorg/myrepo")

        assert result.startswith("Error:")

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_create_repo_pull_request_success(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(
            json_data={"number": 6, "title": "New PR", "body": "Body text"}
        )

        result = provider.create_repo_pull_request(
            "https://git.example.com/myorg/myrepo", "New PR", "Body text", "feature", "main"
        )

        assert "Created new pull request" in result
        assert "#6: New PR" in result
        payload = mock_requests.request.call_args.kwargs["json"]
        assert payload == {
            "title": "New PR",
            "body": "Body text",
            "head": "feature",
            "base": "main",
        }

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_create_repo_pull_request_error(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(status_code=422, text="invalid")

        result = provider.create_repo_pull_request(
            "https://git.example.com/myorg/myrepo", "New PR", "Body text", "feature", "main"
        )

        assert result.startswith("Error:")

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_get_repo_commits_success(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(
            json_data=[
                {"sha": "abc1234567", "commit": {"message": "Initial commit\n\nBody"}}
            ]
        )

        result = provider.get_repo_commits("https://git.example.com/myorg/myrepo", days=14)

        assert "Recent Commits for Forgejo Repository" in result
        assert "abc1234: Initial commit" in result

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_get_repo_commits_error(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(status_code=500, text="boom")

        result = provider.get_repo_commits("https://git.example.com/myorg/myrepo")

        assert result.startswith("Error:")

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_close_issue_success(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(json_data={"number": 1})

        result = provider.close_issue("https://git.example.com/myorg/myrepo", 1)

        assert "Closed issue in Forgejo Repository" in result
        payload = mock_requests.request.call_args.kwargs["json"]
        assert payload == {"state": "closed"}

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_close_issue_error(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(status_code=404, text="not found")

        result = provider.close_issue("https://git.example.com/myorg/myrepo", 1)

        assert result.startswith("Error:")

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_get_my_repos_success(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(
            json_data=[
                {"full_name": "myorg/myrepo", "html_url": "https://git.example.com/myorg/myrepo"}
            ]
        )

        result = provider.get_my_repos()

        assert "My Forgejo Repositories" in result
        assert "myorg/myrepo - https://git.example.com/myorg/myrepo" in result

    @patch("zephyrex.extensions.source.PRV_Forgejo.requests")
    def test_get_my_repos_error(self, mock_requests, provider):
        mock_requests.request.return_value = _mock_response(status_code=401)

        result = provider.get_my_repos()

        assert result.startswith("Error: Authentication failed")

    @patch("zephyrex.extensions.source.PRV_Forgejo.git", None)
    def test_clone_repo_git_not_installed(self, provider):
        result = provider.clone_repo("https://git.example.com/myorg/myrepo")

        assert result.startswith("Error: GitPython is not installed")

    @patch("zephyrex.extensions.source.PRV_Forgejo.os.path.exists", return_value=False)
    @patch("zephyrex.extensions.source.PRV_Forgejo.git")
    def test_clone_repo_success(self, mock_git, mock_exists, provider):
        result = provider.clone_repo("https://git.example.com/myorg/myrepo")

        assert result.startswith("Cloned")
        mock_git.Repo.clone_from.assert_called_once()
        called_kwargs = mock_git.Repo.clone_from.call_args.kwargs
        assert called_kwargs["url"] == "https://test-token@git.example.com/myorg/myrepo"

    @patch("zephyrex.extensions.source.PRV_Forgejo.os.path.exists", return_value=True)
    @patch("zephyrex.extensions.source.PRV_Forgejo.git")
    def test_clone_repo_pulls_existing(self, mock_git, mock_exists, provider):
        result = provider.clone_repo("https://git.example.com/myorg/myrepo")

        assert result.startswith("Pulled latest changes")
        mock_git.Repo.return_value.remotes.origin.pull.assert_called_once()

    @patch("zephyrex.extensions.source.PRV_Forgejo.git")
    def test_clone_repo_error(self, mock_git, provider):
        mock_git.Repo.clone_from.side_effect = Exception("clone failed")

        result = provider.clone_repo("https://git.example.com/myorg/myrepo")

        assert result.startswith("Error:")
        assert "clone failed" in result

    def test_get_repo_code_contents_clone_error(self, provider):
        with patch.object(provider, "clone_repo", return_value="Error: boom"):
            result = provider.get_repo_code_contents(
                "https://git.example.com/myorg/myrepo"
            )

        assert result.startswith("Error cloning repository")

    def test_get_repo_code_contents_success(self, provider, tmp_path):
        provider.working_directory = str(tmp_path)
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()
        (repo_dir / "main.py").write_text("print('hello')\n")
        (repo_dir / "notes.txt").write_text("not code\n")

        with patch.object(provider, "clone_repo", return_value="Cloned ok"):
            result = provider.get_repo_code_contents(
                "https://git.example.com/myorg/myrepo"
            )

        assert "# Repository Content" in result
        assert "main.py" in result
        assert "print('hello')" in result
        assert "notes.txt" not in result

    def test_get_repo_code_contents_branch_extraction(self, provider, tmp_path):
        provider.working_directory = str(tmp_path)
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()

        with patch.object(
            provider, "clone_repo", return_value="Cloned ok"
        ) as mock_clone, patch(
            "zephyrex.extensions.source.PRV_Forgejo.git"
        ) as mock_git:
            provider.get_repo_code_contents(
                "https://git.example.com/myorg/myrepo/src/branch/develop/README.md"
            )

            mock_clone.assert_called_once_with("https://git.example.com/myorg/myrepo")
            mock_git.Repo.return_value.git.checkout.assert_called_once_with("develop")


if __name__ == "__main__":
    pytest.main([__file__])
