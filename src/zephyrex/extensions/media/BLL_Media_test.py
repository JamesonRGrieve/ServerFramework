# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared watch-lists against a real server: the keeper shares a list,
everyone on it adds titles and says what they want to see, strangers see
nothing, and tonight's pick is something everyone wants, the same all day.
The catalogue is a stand-in for TMDb here (its own tests call TMDb)."""

from datetime import date
from typing import Any, Dict, List

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.media.BLL_Media import TitleEntry, tonight
from zephyrex.extensions.media.EXT_Media import EXT_Media
from zephyrex.testing.factories import create_user

CATALOGUE: Dict[str, Dict[str, Any]] = {
    "tmdb:movie:603": {
        "id": "tmdb:movie:603",
        "title": "The Matrix",
        "kind": "movie",
        "year": 1999,
        "image": "https://image.tmdb.org/t/p/w185/m.jpg",
    },
    "tmdb:movie:13": {
        "id": "tmdb:movie:13",
        "title": "Forrest Gump",
        "kind": "movie",
        "year": 1994,
    },
    "tmdb:movie:120": {
        "id": "tmdb:movie:120",
        "title": "The Fellowship of the Ring",
        "kind": "movie",
        "year": 2001,
    },
}


def _title(title_id: str, wanted_by: List[str], watched: bool = False) -> TitleEntry:
    return TitleEntry(
        title_id=title_id,
        id=f"tmdb:movie:{title_id}",
        title=title_id,
        kind="movie",
        wanted_by=wanted_by,
        watched_on=date(2026, 1, 1) if watched else None,
    )


class TestTonight:
    def test_what_everyone_wants_is_picked(self):
        pick = tonight(
            [_title("a", ["x"]), _title("b", ["x", "y"])], 2, "list", date(2026, 11, 2)
        )
        assert pick.title is not None and pick.title.title_id == "b"
        assert pick.everyone and pick.wanted_by == 2 and pick.of == 2

    def test_else_what_most_want(self):
        pick = tonight(
            [_title("a", ["x"]), _title("b", ["x", "y"])], 3, "list", date(2026, 11, 2)
        )
        assert pick.title is not None and pick.title.title_id == "b"
        assert not pick.everyone

    def test_watched_and_unwanted_titles_are_never_picked(self):
        pick = tonight(
            [_title("a", ["x", "y"], watched=True), _title("b", [])],
            2,
            "list",
            date(2026, 11, 2),
        )
        assert pick.title is None and pick.of == 2

    def test_among_equals_the_pick_holds_all_day_and_changes_by_day(self):
        titles = [_title(name, ["x"]) for name in "abcdefgh"]
        today = {
            tonight(titles, 1, "list", date(2026, 11, 2)).title.title_id
            for _ in range(5)
        }
        assert len(today) == 1
        days = {
            tonight(titles, 1, "list", date(2026, 11, d)).title.title_id
            for d in range(1, 29)
        }
        assert len(days) > 1


class TestWatchLists(ExtensionServerMixin):
    extension_class = EXT_Media

    @staticmethod
    def _headers(user: Any) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    @pytest.fixture(autouse=True)
    def catalogue(self, monkeypatch):
        async def info(media: str) -> Dict[str, Any]:
            if media not in CATALOGUE:
                raise InvalidInputExternalError(f"{media!r} is not in the catalogue")
            return CATALOGUE[media]

        async def search(
            query: str, kind: Any = None, limit: int = 10
        ) -> List[Dict[str, Any]]:
            return [
                item
                for item in CATALOGUE.values()
                if query.lower() in str(item["title"]).lower()
            ][:limit]

        monkeypatch.setattr(EXT_Media, "get_media_info", info)
        monkeypatch.setattr(EXT_Media, "search_media", search)

    @pytest.fixture(scope="class")
    def people(self, server) -> Dict[str, Any]:
        keeper, alex, stranger = (create_user(server) for _ in range(3))
        made = server.post(
            "/v1/watch-lists",
            json={"name": "Movie night"},
            headers=self._headers(keeper),
        )
        assert made.status_code == 200, made.text
        list_id = made.json()["id"]
        shared = server.post(
            f"/v1/watch-lists/{list_id}/members",
            json={"user_id": alex.id},
            headers=self._headers(keeper),
        )
        assert shared.status_code == 200, shared.text
        return {
            "keeper": keeper,
            "alex": alex,
            "stranger": stranger,
            "list_id": list_id,
        }

    def test_searching_the_catalogue(self, server, people):
        found = server.get(
            "/v1/watch-lists/search",
            params={"q": "matrix"},
            headers=self._headers(people["alex"]),
        )
        assert found.status_code == 200, found.text
        assert [item["title"] for item in found.json()["results"]] == ["The Matrix"]

    def test_everyone_adds_and_wants_and_the_pick_is_what_all_want(
        self, server, people
    ):
        list_id, keeper, alex = people["list_id"], people["keeper"], people["alex"]
        matrix = server.post(
            f"/v1/watch-lists/{list_id}/titles",
            json={"media_id": "tmdb:movie:603"},
            headers=self._headers(keeper),
        )
        assert matrix.status_code == 200, matrix.text
        assert matrix.json()["title"] == "The Matrix"
        assert matrix.json()["wanted_by"] == [keeper.id]
        gump = server.post(
            f"/v1/watch-lists/{list_id}/titles",
            json={"media_id": "tmdb:movie:13"},
            headers=self._headers(alex),
        )
        assert gump.status_code == 200, gump.text
        want = server.post(
            f"/v1/watch-lists/titles/{matrix.json()['title_id']}/want",
            json={"want": True},
            headers=self._headers(alex),
        )
        assert sorted(want.json()["wanted_by"]) == sorted([keeper.id, alex.id])
        pick = server.get(
            f"/v1/watch-lists/{list_id}/pick", headers=self._headers(alex)
        ).json()
        assert pick["title"]["title"] == "The Matrix"
        assert pick["everyone"] is True and pick["of"] == 2

        watched = server.post(
            f"/v1/watch-lists/titles/{matrix.json()['title_id']}/watched",
            json={"watched": True},
            headers=self._headers(keeper),
        )
        assert watched.json()["watched_on"] is not None
        pick = server.get(
            f"/v1/watch-lists/{list_id}/pick", headers=self._headers(keeper)
        ).json()
        assert pick["title"]["title"] == "Forrest Gump" and pick["everyone"] is False

    def test_a_title_is_on_a_list_once(self, server, people):
        again = server.post(
            f"/v1/watch-lists/{people['list_id']}/titles",
            json={"media_id": "tmdb:movie:13"},
            headers=self._headers(people["keeper"]),
        )
        assert again.status_code in (200, 409)
        again = server.post(
            f"/v1/watch-lists/{people['list_id']}/titles",
            json={"media_id": "tmdb:movie:13"},
            headers=self._headers(people["keeper"]),
        )
        assert again.status_code == 409

    def test_a_title_the_catalogue_does_not_have_is_refused(self, server, people):
        response = server.post(
            f"/v1/watch-lists/{people['list_id']}/titles",
            json={"media_id": "tmdb:movie:999999"},
            headers=self._headers(people["keeper"]),
        )
        assert response.status_code == 422

    def test_strangers_see_nothing(self, server, people):
        stranger = self._headers(people["stranger"])
        assert (
            server.get(
                f"/v1/watch-lists/{people['list_id']}/pick", headers=stranger
            ).status_code
            == 404
        )
        assert (
            server.get("/v1/watch-lists/mine", headers=stranger).json()["lists"] == []
        )
        added = server.post(
            f"/v1/watch-lists/{people['list_id']}/titles",
            json={"media_id": "tmdb:movie:120"},
            headers=stranger,
        )
        assert added.status_code == 404

    def test_members_see_the_list_but_only_the_keeper_shares_it(self, server, people):
        alex = self._headers(people["alex"])
        mine = server.get("/v1/watch-lists/mine", headers=alex).json()["lists"]
        assert [entry["name"] for entry in mine] == ["Movie night"]
        assert mine[0]["keeper_id"] == people["keeper"].id
        refused = server.post(
            f"/v1/watch-lists/{people['list_id']}/members",
            json={"user_id": people["stranger"].id},
            headers=alex,
        )
        assert refused.status_code == 403

    def test_a_member_leaves(self, server, people):
        keeper, alex = people["keeper"], people["alex"]
        other = server.post(
            "/v1/watch-lists", json={"name": "Docs"}, headers=self._headers(keeper)
        ).json()
        server.post(
            f"/v1/watch-lists/{other['id']}/members",
            json={"user_id": alex.id},
            headers=self._headers(keeper),
        )
        left = server.delete(
            f"/v1/watch-lists/{other['id']}/members/{alex.id}",
            headers=self._headers(alex),
        )
        assert left.status_code == 200, left.text
        assert left.json()["member_ids"] == []


def test_a_tmdb_title_carries_its_poster():
    from zephyrex.extensions.media.PRV_TMDb import PRV_TMDb_Media

    item = PRV_TMDb_Media._item(
        "movie",
        {
            "id": 603,
            "title": "The Matrix",
            "poster_path": "/f89U3.jpg",
            "release_date": "1999-03-30",
        },
    )
    assert item["image"] == "https://image.tmdb.org/t/p/w185/f89U3.jpg"
    assert (
        PRV_TMDb_Media._item("movie", {"id": 1, "title": "No poster"})["image"] is None
    )
