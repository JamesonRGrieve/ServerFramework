# SPDX-License-Identifier: AGPL-3.0-or-later
"""Birthday and anniversary reminders: when they fall, what they say, and
that each goes once, to the right account, through auth_notifications."""

import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

from zephyrex.extensions.genealogy.Reminders import (
    Occasion,
    due,
    message,
    next_occurrence,
    occasion,
    send_reminders,
)
from zephyrex.testing.factories import create_user

TODAY = date(2026, 6, 7)


def test_the_next_occurrence_is_this_year_or_next():
    assert next_occurrence(date(1990, 6, 14), TODAY) == date(2026, 6, 14)
    assert next_occurrence(date(1990, 6, 7), TODAY) == TODAY
    assert next_occurrence(date(1990, 1, 2), TODAY) == date(2027, 1, 2)


def test_a_leap_day_falls_on_the_28th_in_other_years():
    assert next_occurrence(date(1992, 2, 29), date(2026, 1, 1)) == date(2026, 2, 28)
    assert next_occurrence(date(1992, 2, 29), date(2028, 1, 1)) == date(2028, 2, 29)


def test_reminders_go_a_week_ahead_and_on_the_day():
    week = occasion("birthday", "p1", "Alex", date(1990, 6, 14), TODAY)
    day = occasion("birthday", "p2", "Sam", date(1991, 6, 7), TODAY)
    later = occasion("birthday", "p3", "Jo", date(1992, 6, 20), TODAY)
    assert [found.person_id for found in due([week, day, later])] == ["p1", "p2"]
    assert week.years == 36


def test_what_a_reminder_says():
    assert message(Occasion("birthday", "p", "Alex", date(2026, 6, 14), 7, 36)) == (
        "Alex’s birthday is in a week",
        "On Sunday 14 June. They turn 36.",
    )
    assert message(Occasion("anniversary", "p", "Sam", date(2026, 6, 7), 0, 1)) == (
        "Your anniversary with Sam is today",
        "On Sunday 7 June. 1 year together.",
    )


@pytest.fixture(scope="module")
def server():
    from conftest import CORE_COMPANION_EXTENSIONS

    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    prepare_test_registry()
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    names = ["genealogy", "auth_notifications", *CORE_COMPANION_EXTENSIONS]
    app = instance(
        db_prefix=f"test.genealogy_reminders.{worker}", extensions=",".join(names)
    )
    yield TestClient(app)


def _headers(user):
    return {"Authorization": f"Bearer {user.jwt}"}


def test_reminders_reach_the_tree_owner_once(server):
    owner = create_user(server)
    me = server.post(
        "/v1/person",
        json={"person": {"name": "Me", "linked_user_id": owner.id}},
        headers=_headers(owner),
    ).json()["person"]["id"]
    alex = server.post(
        "/v1/person",
        json={"person": {"name": "Alex", "birth_date": "1990-06-14T00:00:00"}},
        headers=_headers(owner),
    ).json()["person"]["id"]
    tie = server.post(
        "/v1/relationship",
        json={
            "relationship": {
                "person_id": me,
                "target_person_id": alex,
                "kind": "partnership",
                "valid_from": "2020-06-07T00:00:00",
            }
        },
        headers=_headers(owner),
    )
    assert tie.status_code == 201, tie.text

    registry = server.app.state.model_registry
    assert send_reminders(registry, TODAY) >= 2
    assert send_reminders(registry, TODAY) == 0

    inbox = server.get("/v1/notifications", headers=_headers(owner)).json()[
        "notifications"
    ]
    titles = sorted(note["title"] for note in inbox)
    assert "Alex’s birthday is in a week" in titles
    assert "Your anniversary with Alex is today" in titles
    deliveries = server.get("/v1/user-notifications", headers=_headers(owner)).json()[
        "user_notifications"
    ]
    assert len(deliveries) == len(inbox)
