# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gift ideas against a real server: each is its giver's alone; links must
be http(s); Amazon links, saved or searched, carry the operator's tag and
nothing else gets one."""

from typing import Any, Dict

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.gifts.BLL_Gifts import (
    checked_link,
    is_amazon,
    search_link,
    tagged,
)
from zephyrex.extensions.gifts.EXT_Gifts import EXT_Gifts
from zephyrex.testing.factories import create_user

TAG = "cule-20"


class TestLinks:
    def test_an_amazon_link_carries_the_tag_once(self):
        url = tagged("https://www.amazon.ca/dp/B00X?tag=someone-else-21&th=1", TAG)
        assert url == "https://www.amazon.ca/dp/B00X?th=1&tag=cule-20"

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.etsy.com/listing/1",
            "https://amazon.com.evil.example/dp/1",
            "https://notamazon.com/x",
        ],
    )
    def test_anything_else_is_left_alone(self, url):
        assert tagged(url, TAG) == url

    def test_without_a_tag_nothing_changes(self):
        assert (
            tagged("https://www.amazon.com/dp/1", None) == "https://www.amazon.com/dp/1"
        )

    def test_a_search_at_the_store(self):
        assert (
            search_link("peonies & roses", "www.amazon.ca", TAG)
            == "https://www.amazon.ca/s?k=peonies+%26+roses&tag=cule-20"
        )

    @pytest.mark.parametrize(
        "host, amazon",
        [
            ("smile.amazon.com", True),
            ("amazon.co.uk", True),
            ("amazon.com.evil.example", False),
        ],
    )
    def test_amazon_hosts(self, host, amazon):
        assert is_amazon(host) is amazon

    @pytest.mark.parametrize(
        "value",
        [
            "javascript:alert(1)",
            "data:text/html,x",
            "ftp://x.example",
            "www.amazon.com",
        ],
    )
    def test_only_web_links(self, value):
        with pytest.raises(ValueError):
            checked_link(value)


class TestGifts(ExtensionServerMixin):
    extension_class = EXT_Gifts

    @staticmethod
    def _headers(user: Any) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    @pytest.fixture(autouse=True)
    def associate(self, monkeypatch):
        monkeypatch.setenv("AMAZON_ASSOCIATE_TAG", TAG)
        monkeypatch.setenv("AMAZON_DOMAIN", "www.amazon.ca")

    def test_a_gift_is_the_givers_alone_and_its_amazon_link_tagged(self, server):
        giver, other = create_user(server), create_user(server)
        made = server.post(
            "/v1/gift",
            json={
                "gift": {
                    "for_name": "Alex",
                    "title": "A silk scarf",
                    "url": "https://www.amazon.ca/dp/B01",
                }
            },
            headers=self._headers(giver),
        )
        assert made.status_code == 201, made.text
        gift = made.json()["gift"]
        assert gift["url"] == "https://www.amazon.ca/dp/B01?tag=cule-20"
        assert gift["status"] == "idea"
        theirs = server.get("/v1/gift", headers=self._headers(other)).json()["gifts"]
        assert all(row["id"] != gift["id"] for row in theirs)
        assert (
            server.get(
                f"/v1/gift/{gift['id']}", headers=self._headers(other)
            ).status_code
            == 404
        )

    def test_a_link_that_is_not_the_web_is_refused(self, server):
        refused = server.post(
            "/v1/gift",
            json={
                "gift": {"for_name": "Alex", "title": "x", "url": "javascript:alert(1)"}
            },
            headers=self._headers(create_user(server)),
        )
        assert refused.status_code == 422

    def test_a_search_for_an_idea(self, server):
        found = server.get(
            "/v1/gifts/shop-link",
            params={"q": "peonies"},
            headers=self._headers(create_user(server)),
        )
        assert found.status_code == 200, found.text
        assert found.json() == {
            "url": "https://www.amazon.ca/s?k=peonies&tag=cule-20",
            "tagged": True,
        }

    def test_a_pasted_link(self, server):
        headers = self._headers(create_user(server))
        amazon = server.post(
            "/v1/gifts/link",
            json={"url": "https://www.amazon.ca/dp/B02"},
            headers=headers,
        ).json()
        assert amazon == {
            "url": "https://www.amazon.ca/dp/B02?tag=cule-20",
            "tagged": True,
        }
        other = server.post(
            "/v1/gifts/link",
            json={"url": "https://www.etsy.com/listing/1"},
            headers=headers,
        ).json()
        assert other == {"url": "https://www.etsy.com/listing/1", "tagged": False}

    def test_untagged_without_an_associate_tag(self, server, monkeypatch):
        monkeypatch.setenv("AMAZON_ASSOCIATE_TAG", "")
        found = server.get(
            "/v1/gifts/shop-link",
            params={"q": "peonies"},
            headers=self._headers(create_user(server)),
        )
        assert found.json() == {
            "url": "https://www.amazon.ca/s?k=peonies",
            "tagged": False,
        }
