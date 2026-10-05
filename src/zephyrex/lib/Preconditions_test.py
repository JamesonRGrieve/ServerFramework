# SPDX-License-Identifier: AGPL-3.0-or-later
"""The version/ETag rules and the request-scoped If-Match binding."""

import asyncio
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

import pytest
from pydantic import BaseModel

from zephyrex.lib import Environment
from zephyrex.lib.Preconditions import (
    IF_MATCH_REQUIRED_SETTING,
    REQUIRED_DETAIL,
    IfMatch,
    PreconditionFailed,
    PreconditionRequired,
    StaleVersionError,
    check_route_record,
    claim_expected_version,
    entity_etag,
    entity_version,
    expect_route_record,
    expect_route_version,
    expect_versions,
    expected_version,
    if_match_required,
    missing_is_refused,
    none_match_hits,
    route_target_id,
)

STAMP = datetime(2026, 10, 3, 12, 0, 0, 123000)
TABLE = "widgets"


class Row(BaseModel):
    id: str = "w1"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WidgetManager:
    """The two members the binding reads of a logic manager."""

    class DB:
        __tablename__ = TABLE

    def visible_current(self, id: str) -> Dict[str, Any]:
        return {"id": id, "updated_at": STAMP.isoformat()}


class ModelLessManager:
    @property
    def DB(self) -> Any:
        raise AttributeError("no model")


@pytest.fixture
def required(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(IF_MATCH_REQUIRED_SETTING, "true")
    monkeypatch.setattr(Environment.settings, IF_MATCH_REQUIRED_SETTING, "true")
    yield


@pytest.fixture
def lenient(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A deployment that opted out: IF_MATCH_REQUIRED=false."""
    monkeypatch.setenv(IF_MATCH_REQUIRED_SETTING, "false")
    monkeypatch.setattr(Environment.settings, IF_MATCH_REQUIRED_SETTING, "false")
    yield


class TestDefault:
    def test_the_setting_defaults_to_required(self):
        field = Environment.AppSettings.model_fields[IF_MATCH_REQUIRED_SETTING]
        assert field.default == "true"

    def test_an_unset_setting_is_required(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(IF_MATCH_REQUIRED_SETTING, raising=False)
        monkeypatch.setattr(Environment.settings, IF_MATCH_REQUIRED_SETTING, "")
        assert if_match_required()

    def test_false_opts_out(self, lenient):
        assert not if_match_required()


class TestVersion:
    def test_is_updated_at_as_the_body_serialises_it(self):
        row = Row(created_at=datetime(2026, 1, 1), updated_at=STAMP)
        assert entity_version(row) == "2026-10-03T12:00:00.123000"
        assert entity_version(row) == row.model_dump(mode="json")["updated_at"]
        assert entity_etag(row) == '"2026-10-03T12:00:00.123000"'

    def test_falls_back_to_created_at_never_updated(self):
        assert entity_version(Row(created_at=STAMP)) == STAMP.isoformat()

    def test_reads_a_serialised_row_as_is(self):
        row = {"updated_at": "2026-10-03T12:00:00.000001"}
        assert entity_etag(row) == '"2026-10-03T12:00:00.000001"'

    def test_a_row_without_timestamps_has_none(self):
        assert entity_version(Row()) is None
        assert entity_etag({"id": "x"}) is None


class TestIfMatch:
    def test_names_the_current_version_however_spelled(self):
        row = Row(updated_at=STAMP)
        version = STAMP.isoformat()
        for header in (f'"{version}"', f'W/"{version}"', version, f'"x", "{version}"'):
            assert IfMatch.parse(header).matches(row), header

    def test_a_stale_version_does_not_match(self):
        assert not IfMatch.parse('"2026-10-03T12:00:00"').matches(Row(updated_at=STAMP))

    def test_any_matches_an_existing_record_only(self):
        assert IfMatch.parse("*").matches(Row())
        assert not IfMatch.parse("*").matches(None)

    def test_a_record_without_a_version_never_matches_a_tag(self):
        assert not IfMatch.parse('""').matches(Row())

    def test_if_none_match_compares_weakly(self):
        assert none_match_hits('"v7"', 'W/"v7"')
        assert none_match_hits('W/"v6", W/"v7"', '"v7"')
        assert none_match_hits("*", '"v7"')
        assert not none_match_hits('"v6"', '"v7"')


class TestErrors:
    def test_412_carries_the_current_record_and_its_etag(self):
        current = {"id": "w1", "updated_at": STAMP.isoformat()}
        error = PreconditionFailed(current)
        assert error.status_code == 412
        assert error.body() == {"detail": error.message, "current": current}
        assert error.headers == {"ETag": f'"{STAMP.isoformat()}"'}

    def test_a_batch_412_lists_the_stale_ids(self):
        error = PreconditionFailed([{"id": "w2"}], stale_ids=["w2"])
        assert error.body()["stale_ids"] == ["w2"]
        assert error.headers is None

    def test_428(self):
        assert PreconditionRequired().body() == {"detail": REQUIRED_DETAIL}
        assert PreconditionRequired(missing_ids=["w1"]).body() == {
            "detail": REQUIRED_DETAIL,
            "missing_ids": ["w1"],
        }


class TestBinding:
    def test_nothing_is_expected_outside_a_request(self):
        assert claim_expected_version(TABLE, "w1") is None
        assert expected_version(TABLE, "w1") == (False, None)

    def test_a_named_record_is_claimed_once(self):
        with expect_versions(WidgetManager(), {"w1": '"v1"'}):
            assert expected_version(TABLE, "w1")[0]
            claimed = claim_expected_version(TABLE, "w1")
            assert claimed is not None and claimed.versions == frozenset({"v1"})
            # The request's own further writes are not held to it.
            assert claim_expected_version(TABLE, "w1") is None
        assert expected_version(TABLE, "w1") == (False, None)

    def test_other_records_and_tables_are_not_held(self):
        with expect_versions(WidgetManager(), {"w1": '"v1"'}):
            assert claim_expected_version(TABLE, "w2") is None
            assert claim_expected_version("gadgets", "w1") is None

    def test_a_model_less_manager_binds_nothing(self):
        with expect_versions(ModelLessManager(), {"w1": '"v1"'}):
            assert expected_version(TABLE, "w1") == (False, None)

    def test_missing_is_accepted_while_lenient(self, lenient):
        with expect_versions(WidgetManager(), {"w1": None}):
            assert not missing_is_refused()
            assert claim_expected_version(TABLE, "w1") is None

    def test_missing_is_428_when_required(self, required):
        with expect_versions(WidgetManager(), {"w1": None}):
            assert missing_is_refused()
            with pytest.raises(PreconditionRequired):
                claim_expected_version(TABLE, "w1")

    def test_a_stale_write_becomes_412_with_the_visible_record(self):
        with pytest.raises(PreconditionFailed) as refused:
            with expect_versions(WidgetManager(), {"w1": '"v1"'}):
                raise StaleVersionError(TABLE, "w1")
        assert refused.value.body()["current"]["id"] == "w1"

    async def test_work_that_outlives_the_request_is_not_held(self, required):
        """A task the request spawned copies its context, binding included;
        once the request's write ends, the task's own writes are the
        server's and are held to no client version (no 428, no 412)."""
        release = asyncio.Event()
        seen: List[Any] = []

        async def background() -> None:
            await release.wait()
            seen.append(expected_version(TABLE, "w1"))
            seen.append(missing_is_refused())
            seen.append(claim_expected_version(TABLE, "w1"))

        with expect_versions(WidgetManager(), {"w1": None}):
            task = asyncio.create_task(background())
        release.set()
        await task
        assert seen == [(False, None), False, None]


class TestRouteBinding:
    def test_the_target_is_the_id_or_the_one_named_id(self):
        assert route_target_id({"id": "a", "team_id": "b"}) == "a"
        assert route_target_id({"job_id": "j"}) == "j"
        assert route_target_id({"team_id": "t", "user_id": "u"}) is None
        assert route_target_id({"path": "x"}) is None

    def test_reads_bind_nothing(self):
        with expect_route_version(WidgetManager(), "GET", {"id": "w1"}, '"v1"'):
            assert expected_version(TABLE, "w1") == (False, None)

    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "POST"])
    def test_writes_bind_the_named_record(self, method):
        with expect_route_version(WidgetManager(), method, {"id": "w1"}, '"v1"'):
            assert expected_version(TABLE, "w1")[0]

    def test_a_save_is_required_an_action_is_not(self, required):
        with expect_route_version(WidgetManager(), "PATCH", {"id": "w1"}, None):
            assert missing_is_refused()
        with expect_route_version(WidgetManager(), "POST", {"id": "w1"}, None):
            assert not missing_is_refused()
            assert claim_expected_version(TABLE, "w1") is None

    def test_a_route_resolving_its_record_is_required(self, required):
        with expect_route_version(WidgetManager(), "PATCH", {}, None):
            with pytest.raises(PreconditionRequired):
                check_route_record(WidgetManager(), "w1")
            with expect_route_record(WidgetManager(), "w1"):
                assert missing_is_refused()

    async def test_work_that_outlives_the_route_is_not_held(self, required):
        """As for a bound write: a task spawned by a route that resolves its
        own record holds nothing once the route returns."""
        release = asyncio.Event()
        seen: List[Any] = []

        async def background() -> None:
            await release.wait()
            check_route_record(WidgetManager(), "w1")
            with expect_route_record(WidgetManager(), "w1"):
                seen.append(expected_version(TABLE, "w1"))

        with expect_route_version(WidgetManager(), "PATCH", {}, None):
            task = asyncio.create_task(background())
        release.set()
        await task
        assert seen == [(False, None)]
