# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entitlements against a real server: the free plan, a plan bought by an
active subscription, seats shared and taken back, a seat holder unable to
give seats on, a lapsed subscription dropping its seats, and the 402
checks."""

import uuid
from datetime import UTC, datetime
from typing import Any, Dict

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.entitlements import Plans
from zephyrex.extensions.entitlements.BLL_Entitlements import (
    require_feature,
    require_within_limit,
)
from zephyrex.extensions.entitlements.EXT_Entitlements import EXT_Entitlements
from zephyrex.extensions.entitlements.Plans import Plan
from zephyrex.lib.Environment import env

BASIC = Plan(name="basic", rank=0, features=frozenset({"map"}), limits={"people": 10})
PLUS = Plan(
    name="plus",
    rank=1,
    features=frozenset({"map", "calendar_sync"}),
    limits={"people": None},
    seats=1,
)
PLUS_PRICE = "price_plus_monthly"


@pytest.fixture(scope="module", autouse=True)
def plans():
    saved = dict(Plans.PLANS)
    Plans.PLANS.clear()
    Plans.register_plan(BASIC)
    Plans.register_plan(PLUS)
    mp = pytest.MonkeyPatch()
    mp.setenv(PLUS.plan_ids_setting, PLUS_PRICE)
    yield
    mp.undo()
    Plans.PLANS.clear()
    Plans.PLANS.update(saved)


class TestEntitlements(ExtensionServerMixin):
    extension_class = EXT_Entitlements

    @staticmethod
    def _headers(user) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    @staticmethod
    def _subscribe(server, user_id: str, active: bool) -> str:
        from zephyrex.extensions.payment.BLL_Payment import PaymentSubscriptionManager

        created = PaymentSubscriptionManager(
            model_registry=server.app.state.model_registry, requester_id=env("ROOT_ID")
        ).create(
            user_id=user_id,
            provider="stripe",
            provider_instance_id=str(uuid.uuid4()),
            external_id=f"sub_{uuid.uuid4().hex}",
            plan_id=PLUS_PRICE,
            status="active" if active else "canceled",
            active=active,
            refreshed_at=datetime.now(UTC),
        )
        return str(created.id)

    @staticmethod
    def _lapse(server, subscription_id: str) -> None:
        from zephyrex.extensions.payment.BLL_Payment import PaymentSubscriptionManager

        PaymentSubscriptionManager(
            model_registry=server.app.state.model_registry, requester_id=env("ROOT_ID")
        ).update(id=subscription_id, status="canceled", active=False)

    def _plan(self, server, user) -> Dict[str, Any]:
        response = server.get("/v1/entitlement", headers=self._headers(user))
        assert response.status_code == 200, response.text
        body: Dict[str, Any] = response.json()
        return body

    def test_everyone_starts_on_the_free_plan(self, server, user_b):
        body = self._plan(server, user_b)
        assert (body["plan"], body["features"], body["limits"], body["seats"]) == (
            "basic",
            ["map"],
            {"people": 10},
            0,
        )

    def test_a_default_plan_is_everyones_without_paying(
        self, server, user_b, monkeypatch
    ):
        # A test phase can give everyone the top plan: ENTITLEMENT_DEFAULT_PLAN.
        monkeypatch.setenv("ENTITLEMENT_DEFAULT_PLAN", "plus")
        assert self._plan(server, user_b)["plan"] == "plus"
        # A name no plan has changes nothing.
        monkeypatch.setenv("ENTITLEMENT_DEFAULT_PLAN", "gold")
        assert self._plan(server, user_b)["plan"] == "basic"

    def test_a_subscription_buys_its_plan_and_shares_a_seat(
        self, server, admin_a, admin_b
    ):
        subscription = self._subscribe(server, admin_a.id, active=True)
        body = self._plan(server, admin_a)
        assert (body["plan"], body["seats"]) == ("plus", 1)

        given = server.post(
            "/v1/entitlement/seats",
            json={"user_id": admin_b.id},
            headers=self._headers(admin_a),
        )
        assert given.status_code == 200, given.text
        held = self._plan(server, admin_b)
        assert (held["plan"], held["via_seat_from"], held["seats"]) == (
            "plus",
            admin_a.id,
            0,
        )

        # The plan has one seat, and a seat holder cannot give seats on.
        again = server.post(
            "/v1/entitlement/seats",
            json={"user_id": str(uuid.uuid4())},
            headers=self._headers(admin_a),
        )
        assert again.status_code == 402, again.text
        onward = server.post(
            "/v1/entitlement/seats",
            json={"user_id": admin_a.id},
            headers=self._headers(admin_b),
        )
        assert onward.status_code == 402, onward.text

        # A lapsed subscription takes its seats with it.
        self._lapse(server, subscription)
        assert self._plan(server, admin_b)["plan"] == "basic"
        assert self._plan(server, admin_a)["plan"] == "basic"

    def test_a_seat_can_be_taken_back_only_by_its_giver(self, server, user_b):
        subscriber = self._new_user(server)
        self._subscribe(server, subscriber.id, active=True)
        given = server.post(
            "/v1/entitlement/seats",
            json={"user_id": user_b.id},
            headers=self._headers(subscriber),
        )
        assert given.status_code == 200, given.text
        seat = given.json()
        seat_id = seat["id"]
        version = {"If-Match": f'"{seat["updated_at"] or seat["created_at"]}"'}
        stranger = server.delete(
            f"/v1/entitlement/seats/{seat_id}",
            headers={**self._headers(user_b), **version},
        )
        assert stranger.status_code == 404
        unversioned = server.delete(
            f"/v1/entitlement/seats/{seat_id}", headers=self._headers(subscriber)
        )
        assert unversioned.status_code == 428
        taken = server.delete(
            f"/v1/entitlement/seats/{seat_id}",
            headers={**self._headers(subscriber), **version},
        )
        assert taken.status_code == 200, taken.text
        assert self._plan(server, user_b)["plan"] == "basic"

    def test_a_seat_is_for_someone_else(self, server):
        subscriber = self._new_user(server)
        self._subscribe(server, subscriber.id, active=True)
        response = server.post(
            "/v1/entitlement/seats",
            json={"user_id": subscriber.id},
            headers=self._headers(subscriber),
        )
        assert response.status_code == 422

    def test_the_checks_refuse_with_what_to_upgrade_to(self, server, user_b):
        registry = server.app.state.model_registry
        require_feature(user_b.id, registry, "map")
        with pytest.raises(HTTPException) as refused:
            require_feature(user_b.id, registry, "calendar_sync")
        assert refused.value.status_code == 402
        assert refused.value.detail == {
            "feature": "calendar_sync",
            "upgrade_to": ["plus"],
        }
        require_within_limit(user_b.id, registry, "people", 10)
        with pytest.raises(HTTPException) as over:
            require_within_limit(user_b.id, registry, "people", 11)
        assert over.value.detail == {
            "limit": "people",
            "allowed": 10,
            "upgrade_to": ["plus"],
        }

    @staticmethod
    def _new_user(server):
        from zephyrex.testing.factories import create_user, generate_test_email

        return create_user(
            server, email=generate_test_email("subscriber"), last_name="Subscriber"
        )
