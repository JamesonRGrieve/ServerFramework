# SPDX-License-Identifier: AGPL-3.0-or-later
"""Forgejo or Gitea, at the instance's ``base_url`` (else
``FORGEJO_URL``, else Codeberg). The API key is an access token (else
``FORGEJO_TOKEN``); without one, public repositories are read
anonymously."""

from typing import Any, ClassVar, Dict, Tuple
from urllib.parse import urlparse

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.source.GitHubStyle import GitHubStyleProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_BASE_URL = "https://codeberg.org"
API_PATH = "/api/v1"


class PRV_Forgejo_Source(GitHubStyleProvider):
    name: ClassVar[str] = "forgejo"
    friendly_name: ClassVar[str] = "Forgejo"
    description: ClassVar[str] = "Forgejo or Gitea (Codeberg by default)"
    _env: ClassVar[Dict[str, Any]] = {
        "FORGEJO_TOKEN": "",
        "FORGEJO_URL": DEFAULT_BASE_URL,
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Access token (empty: public repositories only)",
            env="FORGEJO_TOKEN",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "base_url",
            "Server address (https://codeberg.org, https://git.example.com)",
            env="FORGEJO_URL",
            default=DEFAULT_BASE_URL,
        ),
    )
    page_size_parameter: ClassVar[str] = "limit"

    @classmethod
    def _base_url(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "base_url") or DEFAULT_BASE_URL).rstrip("/")

    @classmethod
    def api_base(cls, instance: ProviderInstanceModel) -> str:
        return f"{cls._base_url(instance)}{API_PATH}"

    @classmethod
    def web_host(cls, instance: ProviderInstanceModel) -> str:
        return urlparse(cls._base_url(instance)).hostname or ""

    @classmethod
    def headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        token = cls.setting(instance, "api_key")
        if token:
            headers["Authorization"] = f"token {token}"
        return headers

    @classmethod
    def commit_parameters(cls) -> Dict[str, Any]:
        # Skip the per-commit diff stats and signature checks: slow, unused.
        return {"stat": "false", "verification": "false", "files": "false"}

    @classmethod
    def issue_list_parameters(cls) -> Dict[str, Any]:
        # Issues only: Forgejo filters pull requests out on request.
        return {"type": "issues"}
