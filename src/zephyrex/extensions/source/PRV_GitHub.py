# SPDX-License-Identifier: AGPL-3.0-or-later
"""GitHub, or GitHub Enterprise Server through the ``api_url`` setting.
The instance's API key is a personal access token (else ``GITHUB_TOKEN``);
without one, public repositories are read anonymously at GitHub's lower
rate limit."""

from typing import Any, ClassVar, Dict, Tuple
from urllib.parse import urlparse

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.source.GitHubStyle import GitHubStyleProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"


class PRV_GitHub_Source(GitHubStyleProvider):
    name: ClassVar[str] = "github"
    friendly_name: ClassVar[str] = "GitHub"
    description: ClassVar[str] = "GitHub or GitHub Enterprise Server"
    _env: ClassVar[Dict[str, Any]] = {
        "GITHUB_TOKEN": "",
        "GITHUB_API_URL": DEFAULT_API_URL,
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Personal access token (empty: public repositories only)",
            env="GITHUB_TOKEN",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "api_url",
            "API root (GitHub Enterprise: https://<host>/api/v3)",
            env="GITHUB_API_URL",
            default=DEFAULT_API_URL,
        ),
    )

    @classmethod
    def api_base(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "api_url") or DEFAULT_API_URL).rstrip("/")

    @classmethod
    def web_host(cls, instance: ProviderInstanceModel) -> str:
        host = urlparse(cls.api_base(instance)).hostname or ""
        return "github.com" if host == "api.github.com" else host

    @classmethod
    def headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        token = cls.setting(instance, "api_key")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers
