# SPDX-License-Identifier: AGPL-3.0-or-later
"""The publication record over the API: the envelopes a client reads
(list, one, search); a record belongs to its account's owner (a root
account's posts are not shown to anyone else); and a record cannot be
written through the API."""

import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.social.BLL_Social import record_publication
from zephyrex.extensions.social.EXT_Social import EXT_Social
from zephyrex.extensions.social.PRV_Threads import PRV_Threads_Social
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceManager

PHOTO = "https://cdn.example.com/a/photo.jpg"
PUBLISHED = datetime(2026, 10, 2, 12, tzinfo=UTC)


class TestPublicationAPI(ExtensionServerMixin):
    extension_class = EXT_Social

    @pytest.fixture
    def headers(self, admin_a) -> Dict[str, str]:
        return {"Authorization": f"Bearer {admin_a.jwt}"}

    @pytest.fixture
    def threads(self, server, headers) -> Dict[str, Any]:
        providers = server.get("/v1/provider", headers=headers).json()["providers"]
        return next(p for p in providers if p["name"] == PRV_Threads_Social.name)

    @pytest.fixture
    def own_account(self, server, headers, threads) -> str:
        """A Threads account the test admin set up."""
        response = server.post(
            "/v1/provider/instance",
            json={
                "provider_instance": {
                    "name": f"threads-{uuid.uuid4().hex}",
                    "provider_id": threads["id"],
                }
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        return str(response.json()["provider_instance"]["id"])

    @pytest.fixture
    def root_account(self, server, threads) -> str:
        created = ProviderInstanceManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        ).create(
            name=f"threads-root-{uuid.uuid4().hex}",
            provider_id=threads["id"],
            scope="root",
        )
        return str(created.id)

    def _record(
        self,
        server,
        account: str,
        content: str,
        media: Optional[List[str]] = None,
        provider: str = "threads",
    ) -> str:
        return record_publication(
            server.app.state.model_registry,
            {
                "provider": provider,
                "provider_instance_id": account,
                "platform_post_id": uuid.uuid4().hex,
                "url": None,
            },
            content,
            media or [],
            PUBLISHED,
        )

    def _listed(self, server, headers) -> Dict[str, Dict[str, Any]]:
        response = server.get("/v1/social_publication", headers=headers)
        assert response.status_code == 200, response.text
        return {row["id"]: row for row in response.json()["social_publications"]}

    def test_list_one_and_search(self, server, headers, own_account):
        with_media = self._record(server, own_account, "Sunrise", [PHOTO])
        text_only = self._record(server, own_account, "Plain words", provider="x")

        rows = self._listed(server, headers)
        assert rows[with_media]["media_urls"] == [PHOTO]
        assert rows[text_only]["media_urls"] is None
        assert rows[with_media]["provider_instance_id"] == own_account

        one = server.get(f"/v1/social_publication/{with_media}", headers=headers)
        assert one.status_code == 200, one.text
        assert one.json()["social_publication"]["content"] == "Sunrise"

        found = server.post(
            "/v1/social_publication/search",
            json={"social_publication": {"provider": {"eq": "x"}}},
            headers=headers,
        )
        assert found.status_code == 200, found.text
        ids = {row["id"] for row in found.json()["social_publications"]}
        assert text_only in ids and with_media not in ids

    def test_a_root_accounts_posts_are_not_shown(self, server, headers, root_account):
        hidden = self._record(server, root_account, "Internal")
        assert hidden not in self._listed(server, headers)
        assert (
            server.get(f"/v1/social_publication/{hidden}", headers=headers).status_code
            == 404
        )

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_a_record_cannot_be_written(self, server, headers, own_account, method):
        target = f"/v1/social_publication/{self._record(server, own_account, 'Hi')}"
        body = {"social_publication": {"content": "forged"}}
        for path in ("/v1/social_publication", target):
            response = server.request(
                method, path, headers=headers, json=None if method == "DELETE" else body
            )
            assert response.status_code in (404, 405), (path, response.text)
