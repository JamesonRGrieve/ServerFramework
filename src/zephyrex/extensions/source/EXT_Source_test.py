# SPDX-License-Identifier: AGPL-3.0-or-later
"""The source extension: repository names that cannot leave the
provider's host or API path, file and ref checks, each provider's
configuration, real reads of public repositories on GitHub, Codeberg
(Forgejo), GitLab and Bitbucket, refused tokens, and routing to the
named provider (network tests xfail when the host is unreachable)."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.source.EXT_Source import (
    MAX_FILE_BYTES,
    EXT_Source,
    checked_limit,
    checked_state,
    file_path,
    git_ref,
    parse_time,
    repo_segments,
    text_file,
)
from zephyrex.extensions.source.PRV_Bitbucket import PRV_Bitbucket_Source
from zephyrex.extensions.source.PRV_Forgejo import PRV_Forgejo_Source
from zephyrex.extensions.source.PRV_GitHub import PRV_GitHub_Source
from zephyrex.extensions.source.PRV_GitLab import PRV_GitLab_Source

GITHUB_REPO = "octocat/Hello-World"
CODEBERG_REPO = "forgejo/forgejo"
GITLAB_REPO = "gitlab-org/gitlab-runner"
BITBUCKET_REPO = "atlassian/atlassian-event"


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


def reachable(url: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(not _online(url), reason=f"{url} is unreachable")


class TestRepositoryNames:
    @pytest.mark.parametrize(
        "repo, parts",
        [
            ("octocat/Hello-World", ["octocat", "Hello-World"]),
            ("https://github.com/octocat/Hello-World", ["octocat", "Hello-World"]),
            ("https://github.com/octocat/Hello-World.git", ["octocat", "Hello-World"]),
            (
                "https://github.com/octocat/Hello-World/tree/main/docs",
                ["octocat", "Hello-World"],
            ),
            (
                "https://github.com/octocat/Hello-World/issues/3",
                ["octocat", "Hello-World"],
            ),
        ],
    )
    def test_owner_and_name(self, repo, parts):
        assert repo_segments(repo, "github.com", 2) == parts

    def test_gitlab_nested_groups(self):
        assert repo_segments(
            "https://gitlab.com/group/sub/project/-/tree/main", "gitlab.com", None
        ) == ["group", "sub", "project"]

    def test_a_url_on_another_host_is_refused(self):
        """The old clone_repo put the token into whatever URL it was given."""
        with pytest.raises(InvalidInputExternalError):
            repo_segments("https://evil.example/octocat/Hello-World", "github.com", 2)

    @pytest.mark.parametrize(
        "repo", ["octocat/..", "../user", "octocat", "a/b/c", "a/b%2Fc", "a b/c", "./x"]
    )
    def test_a_name_that_would_reach_another_path_is_refused(self, repo):
        with pytest.raises(InvalidInputExternalError):
            repo_segments(repo, "github.com", 2)


class TestChecks:
    def test_file_paths(self):
        assert file_path("/docs//guide.md/") == "docs/guide.md"
        assert file_path("") == ""
        for path in ("../etc/passwd", "docs/../../x", "a/./b"):
            with pytest.raises(InvalidInputExternalError):
                file_path(path)

    def test_refs(self):
        assert git_ref("feature/login-v2") == "feature/login-v2"
        for ref in ("main..evil", "a b", "", "x;rm"):
            with pytest.raises(InvalidInputExternalError):
                git_ref(ref)

    def test_limits_and_states(self):
        assert checked_limit(30) == 30
        assert checked_state("all") == "all"
        with pytest.raises(InvalidInputExternalError):
            checked_limit(0)
        with pytest.raises(InvalidInputExternalError):
            checked_state("merged")

    def test_timestamps(self):
        assert parse_time("2026-10-01T12:00:00Z") == datetime(
            2026, 10, 1, 12, tzinfo=UTC
        )
        assert parse_time("2026-10-01T14:00:00+02:00") == parse_time(
            "2026-10-01T12:00:00Z"
        )

    def test_text_files(self):
        assert text_file("p", "a.txt", "main", b"hi")["content"] == "hi"
        with pytest.raises(InvalidInputExternalError):
            text_file("p", "a.bin", "main", b"\xff\xfe\x00")
        with pytest.raises(InvalidInputExternalError):
            text_file("p", "big.txt", "main", b"", size=MAX_FILE_BYTES + 1)

    async def test_ability_arguments_are_checked_first(self):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Source.update_issue("github", GITHUB_REPO, 1)
        with pytest.raises(InvalidInputExternalError):
            await EXT_Source.update_issue("github", GITHUB_REPO, 1, state="merged")
        with pytest.raises(InvalidInputExternalError):
            await EXT_Source.read_file("github", GITHUB_REPO, "/")
        with pytest.raises(InvalidInputExternalError):
            await EXT_Source.create_issue("github", GITHUB_REPO, "  ")


class TestConfiguration:
    def test_providers(self):
        assert {p.name for p in EXT_Source.providers} == {
            "github",
            "gitlab",
            "forgejo",
            "bitbucket",
        }

    def test_github_enterprise_host(self, provider_instance):
        instance = provider_instance(
            PRV_GitHub_Source, settings={"api_url": "https://ghe.example.com/api/v3/"}
        )
        assert PRV_GitHub_Source.api_base(instance) == "https://ghe.example.com/api/v3"
        assert PRV_GitHub_Source.web_host(instance) == "ghe.example.com"
        assert PRV_GitHub_Source.web_host(provider_instance(PRV_GitHub_Source)) == (
            "github.com"
        )

    def test_forgejo_defaults_to_codeberg(self, provider_instance):
        instance = provider_instance(PRV_Forgejo_Source)
        assert PRV_Forgejo_Source.api_base(instance) == "https://codeberg.org/api/v1"
        assert PRV_Forgejo_Source.headers(instance) == {"Accept": "application/json"}

    def test_tokens_are_sent_in_each_platforms_header(self, provider_instance):
        assert (
            PRV_GitHub_Source.headers(
                provider_instance(PRV_GitHub_Source, api_key="t")
            )["Authorization"]
            == "Bearer t"
        )
        assert (
            PRV_Forgejo_Source.headers(
                provider_instance(PRV_Forgejo_Source, api_key="t")
            )["Authorization"]
            == "token t"
        )

    async def test_bitbucket_has_no_issues(self, provider_instance):
        with pytest.raises(PermanentExternalError):
            await PRV_Bitbucket_Source.list_issues(
                provider_instance(PRV_Bitbucket_Source), BITBUCKET_REPO, "open", 5
            )


@reachable("https://api.github.com")
class TestGitHub:
    async def test_repository_issues_and_file(self, provider_instance):
        instance = provider_instance(PRV_GitHub_Source)
        repo = await PRV_GitHub_Source.get_repository(instance, GITHUB_REPO)
        assert repo["full_name"] == GITHUB_REPO and repo["default_branch"] == "master"
        issues = await PRV_GitHub_Source.list_issues(instance, GITHUB_REPO, "open", 5)
        assert issues and all(issue["number"] for issue in issues)
        readme = await PRV_GitHub_Source.read_file(
            instance, GITHUB_REPO, "README", None
        )
        assert readme["content"].startswith("Hello World")

    async def test_a_bad_token_is_refused(self, provider_instance):
        instance = provider_instance(PRV_GitHub_Source, api_key="ghp_not_a_real_token")
        with pytest.raises(AuthExternalError):
            await PRV_GitHub_Source.get_repository(instance, GITHUB_REPO)


@reachable("https://codeberg.org")
class TestForgejo:
    async def test_repository_files_and_commits(self, provider_instance):
        instance = provider_instance(PRV_Forgejo_Source)
        repo = await PRV_Forgejo_Source.get_repository(instance, CODEBERG_REPO)
        assert repo["default_branch"] == "forgejo"
        files = await PRV_Forgejo_Source.list_files(instance, CODEBERG_REPO, "", None)
        assert {"name": "README.md", "type": "file"}.items() <= next(
            entry for entry in files if entry["name"] == "README.md"
        ).items()
        readme = await PRV_Forgejo_Source.read_file(
            instance, CODEBERG_REPO, "README.md", None
        )
        assert "Forgejo" in readme["content"]
        after = datetime.now(UTC) - timedelta(days=30)
        commits = await PRV_Forgejo_Source.list_commits(
            instance, CODEBERG_REPO, after, None, 5
        )
        assert commits and all(parse_time(c["date"]) >= after for c in commits)

    async def test_a_directory_is_not_read_as_a_file(self, provider_instance):
        with pytest.raises(InvalidInputExternalError):
            await PRV_Forgejo_Source.read_file(
                provider_instance(PRV_Forgejo_Source), CODEBERG_REPO, "docs", None
            )

    async def test_a_bad_token_is_refused(self, provider_instance):
        instance = provider_instance(PRV_Forgejo_Source, api_key="not-a-real-token")
        with pytest.raises(AuthExternalError):
            await PRV_Forgejo_Source.list_repositories(instance, 5)


@reachable("https://gitlab.com")
class TestGitLab:
    async def test_repository_file_and_merge_requests(self, provider_instance):
        instance = provider_instance(PRV_GitLab_Source)
        repo = await PRV_GitLab_Source.get_repository(instance, GITLAB_REPO)
        assert repo["full_name"] == GITLAB_REPO and not repo["private"]
        readme = await PRV_GitLab_Source.read_file(
            instance, GITLAB_REPO, "README.md", None
        )
        assert readme["ref"] == repo["default_branch"] and readme["content"]
        merged = await PRV_GitLab_Source.list_pull_requests(
            instance, GITLAB_REPO, "closed", 5
        )
        assert merged and all(request["state"] == "closed" for request in merged)

    async def test_a_bad_token_is_refused(self, provider_instance):
        instance = provider_instance(
            PRV_GitLab_Source, api_key="glpat-not-a-real-token"
        )
        with pytest.raises(AuthExternalError):
            await PRV_GitLab_Source.list_repositories(instance, 5)


@reachable("https://api.bitbucket.org")
class TestBitbucket:
    async def test_repository_files_and_commits(self, provider_instance):
        instance = provider_instance(PRV_Bitbucket_Source)
        repo = await PRV_Bitbucket_Source.get_repository(instance, BITBUCKET_REPO)
        assert repo["full_name"] == BITBUCKET_REPO
        files = await PRV_Bitbucket_Source.list_files(
            instance, BITBUCKET_REPO, "", None
        )
        assert "README.md" in {entry["name"] for entry in files}
        readme = await PRV_Bitbucket_Source.read_file(
            instance, BITBUCKET_REPO, "README.md", None
        )
        assert readme["content"] and readme["ref"] == repo["default_branch"]
        commits = await PRV_Bitbucket_Source.list_commits(
            instance, BITBUCKET_REPO, datetime(2000, 1, 1, tzinfo=UTC), None, 3
        )
        assert len(commits) == 3 and commits[0]["sha"]

    async def test_a_bad_token_is_refused(self, provider_instance):
        instance = provider_instance(PRV_Bitbucket_Source, api_key="not-a-real-token")
        with pytest.raises(AuthExternalError):
            await PRV_Bitbucket_Source.get_repository(instance, BITBUCKET_REPO)


@reachable("https://codeberg.org")
class TestRouting:
    async def test_the_named_provider_answers(
        self, provider_instance, rotation_over, monkeypatch
    ):
        """The rotation leads with GitHub; naming forgejo skips it."""
        monkeypatch.setattr(
            EXT_Source,
            "_root_rotation_cache",
            rotation_over(
                provider_instance(PRV_GitHub_Source),
                provider_instance(PRV_Forgejo_Source),
            ),
        )
        repo = await EXT_Source.get_repository("forgejo", CODEBERG_REPO)
        assert repo["provider"] == "forgejo"
