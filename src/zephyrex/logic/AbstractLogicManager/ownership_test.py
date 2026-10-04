# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Any, Dict

import pytest

from zephyrex.database.StaticPermissions import ROOT_ID, SYSTEM_ID
from zephyrex.logic.AbstractLogicManager.ownership import (
    OWNERSHIP_FIELDS,
    created_records,
    each_created,
    owned_by,
    server_only,
    server_side,
    without,
)

USER = "user-a"
OTHER = "user-b"
SERVER = (ROOT_ID, SYSTEM_ID)


def test_root_and_system_are_server_side_and_users_and_no_one_are_not():
    assert server_side(ROOT_ID) and server_side(SYSTEM_ID)
    for requester in (USER, "", None):
        assert not server_side(requester)


def test_a_user_owns_what_they_create_whatever_owner_they_name():
    prepare = owned_by(USER)
    for named in ({}, {"user_id": OTHER}, {"user_id": None}, {"user_id": ""}):
        assert prepare(dict(named))["user_id"] == USER


@pytest.mark.parametrize("server", SERVER)
def test_root_and_system_may_name_another_owner_or_own_it_themselves(server: str):
    prepare = owned_by(server)
    assert prepare({"user_id": OTHER})["user_id"] == OTHER
    for unnamed in ({}, {"user_id": None}, {"user_id": ""}):
        assert prepare(dict(unnamed))["user_id"] == server


@pytest.mark.parametrize("server", SERVER)
def test_root_and_system_may_leave_a_record_unowned_when_allowed(server: str):
    prepare = owned_by(server, server_may_leave_unowned=True)
    assert prepare({"user_id": OTHER})["user_id"] == OTHER
    assert prepare({"user_id": None})["user_id"] is None
    assert prepare({})["user_id"] == server


def test_leaving_unowned_is_never_a_users():
    prepare = owned_by(USER, server_may_leave_unowned=True)
    for named in ({}, {"user_id": OTHER}, {"user_id": None}):
        assert prepare(dict(named))["user_id"] == USER


def test_each_created_prepares_a_single_create():
    sent = {"name": "n", "user_id": OTHER}
    prepared = each_created(sent, owned_by(USER))
    assert prepared == {"name": "n", "user_id": USER}
    assert sent["user_id"] == OTHER, "the caller's kwargs are not changed"


def test_each_created_prepares_every_entity_of_a_batch():
    entities = [{"name": "a", "user_id": OTHER}, {"name": "b"}]
    sent: Dict[str, Any] = {"entities": entities, "flag": True}
    prepared = each_created(sent, owned_by(USER))
    assert prepared["flag"] is True
    assert [e["user_id"] for e in prepared["entities"]] == [USER, USER]
    assert entities[0]["user_id"] == OTHER and "user_id" not in entities[1]


def test_entities_that_are_not_a_list_are_a_single_create():
    prepared = each_created({"entities": "x"}, owned_by(USER))
    assert prepared == {"entities": "x", "user_id": USER}


def test_created_records_lists_one_record_or_a_batch():
    record = object()
    assert created_records(record) == [record]
    assert created_records([record, record]) == [record, record]


def test_without_drops_the_named_fields_in_place():
    fields = {"user_id": OTHER, "team_id": "t", "name": "n"}
    assert without(fields, OWNERSHIP_FIELDS) is fields
    assert fields == {"name": "n"}
    assert without({}, OWNERSHIP_FIELDS) == {}


def test_server_only_fields_are_dropped_from_a_users_write():
    fields = {"status": "done", "name": "n"}
    assert server_only(USER, fields, ("status",)) == {"name": "n"}


@pytest.mark.parametrize("server", SERVER)
def test_server_only_fields_are_kept_in_root_and_systems_writes(server: str):
    fields = {"status": "done", "name": "n"}
    assert server_only(server, dict(fields), ("status",)) == fields
