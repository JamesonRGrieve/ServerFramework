# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entitlements against a real server: the free plan, a plan bought by an
active subscription, seats shared and taken back, a seat holder unable to
give seats on, a lapsed subscription dropping its seats, the 402 checks,
and products sold once: listed publicly, their plan granted by a succeeded
payment and withdrawn by a full refund, and purchases refused for unknown
products, plans already held and deployments without a payment account;
and units (seats) bought in a quantity for a subject the buyer may buy for,
charged at the unit price times the quantity and counted while their
payment stands, a partial refund withdrawing its share."""

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
    units_bought,
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
PLUS_FOREVER = Plans.Product(
    id="plus_forever",
    plan="plus",
    amount="19.00",
    currency="USD",
    description="Plus, for good",
)


def _owns_team(user_id: str, subject_id: str, model_registry: Any) -> bool:
    """A stand-in for an app's ownership check: team ``team-<user id>`` is theirs."""
    return subject_id == f"team-{user_id}"


TEAM_SEAT = Plans.Product(
    id="team_seat",
    plan=None,
    amount="2.00",
    currency="USD",
    description="A team seat",
    max_quantity=50,
    authorize_subject=_owns_team,
)


@pytest.fixture(scope="module", autouse=True)
def plans():
    saved = dict(Plans.PLANS)
    saved_products = dict(Plans.PRODUCTS)
    Plans.PLANS.clear()
    Plans.PRODUCTS.clear()
    Plans.register_plan(BASIC)
    Plans.register_plan(PLUS)
    Plans.register_product(PLUS_FOREVER)
    Plans.register_product(TEAM_SEAT)
    mp = pytest.MonkeyPatch()
    mp.setenv(PLUS.plan_ids_setting, PLUS_PRICE)
    yield
    mp.undo()
    Plans.PLANS.clear()
    Plans.PLANS.update(saved)
    Plans.PRODUCTS.clear()
    Plans.PRODUCTS.update(saved_products)


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

    @staticmethod
    def _paid(server, user_id: str, product_id: str, status: str = "succeeded") -> str:
        from zephyrex.extensions.payment.BLL_Payment import PaymentManager

        created = PaymentManager(
            model_registry=server.app.state.model_registry, requester_id=env("ROOT_ID")
        ).create(
            user_id=user_id,
            provider="square",
            provider_instance_id=str(uuid.uuid4()),
            external_id=f"pay_{uuid.uuid4().hex}",
            status=status,
            amount=PLUS_FOREVER.amount,
            currency=PLUS_FOREVER.currency,
            product_id=product_id,
            refreshed_at=datetime.now(UTC),
        )
        return str(created.id)

    def test_products_are_listed_publicly_with_their_price(self, server):
        response = server.get("/v1/entitlement/products")
        assert response.status_code == 200, response.text
        assert response.json()["products"] == [
            {
                "id": "plus_forever",
                "plan": "plus",
                "amount": "19.00",
                "currency": "USD",
                "description": "Plus, for good",
                "features": ["calendar_sync", "map"],
                "max_quantity": 1,
                "needs_subject": False,
            },
            {
                "id": "team_seat",
                "plan": None,
                "amount": "2.00",
                "currency": "USD",
                "description": "A team seat",
                "features": [],
                "max_quantity": 50,
                "needs_subject": True,
            },
        ]

    def test_a_succeeded_payment_for_a_product_buys_its_plan_until_fully_refunded(
        self, server
    ):
        from zephyrex.extensions.payment.BLL_Payment import PaymentManager

        buyer = self._new_user(server)
        pending = self._paid(server, buyer.id, PLUS_FOREVER.id, status="pending")
        assert self._plan(server, buyer)["plan"] == "basic"

        payments = PaymentManager(
            model_registry=server.app.state.model_registry, requester_id=env("ROOT_ID")
        )
        payments.update(id=pending, status="succeeded")
        assert self._plan(server, buyer)["plan"] == "plus"

        payments.update(id=pending, amount_refunded="5.00")
        assert self._plan(server, buyer)["plan"] == "plus"
        payments.update(id=pending, amount_refunded=PLUS_FOREVER.amount)
        assert self._plan(server, buyer)["plan"] == "basic"

    def test_a_payment_for_an_unregistered_product_buys_nothing(self, server):
        buyer = self._new_user(server)
        self._paid(server, buyer.id, "retired_product")
        assert self._plan(server, buyer)["plan"] == "basic"

    def test_a_purchase_of_an_unknown_product_is_refused(self, server, user_b):
        response = server.post(
            "/v1/entitlement/purchase",
            json={"product_id": "nothing"},
            headers=self._headers(user_b),
        )
        assert response.status_code == 404, response.text

    def test_a_purchase_of_a_plan_already_held_is_refused(self, server):
        buyer = self._new_user(server)
        self._paid(server, buyer.id, PLUS_FOREVER.id)
        response = server.post(
            "/v1/entitlement/purchase",
            json={"product_id": PLUS_FOREVER.id},
            headers=self._headers(buyer),
        )
        assert response.status_code == 409, response.text

    def test_a_purchase_needs_a_session(self, server):
        response = server.post(
            "/v1/entitlement/purchase", json={"product_id": PLUS_FOREVER.id}
        )
        assert response.status_code == 401, response.text

    def test_a_purchase_with_no_payment_account_fails_and_grants_nothing(self, server):
        buyer = self._new_user(server)
        response = server.post(
            "/v1/entitlement/purchase",
            json={
                "product_id": PLUS_FOREVER.id,
                "payment_method_id": "cnon:card-nonce-ok",
            },
            headers=self._headers(buyer),
        )
        assert response.status_code >= 400, response.text
        assert self._plan(server, buyer)["plan"] == "basic"

    @staticmethod
    def _charge_succeeds(server, monkeypatch) -> list:
        """Payments succeed at once, as a card charge does: the payment row is
        the one payment_create would record. Returns the charges asked for."""
        from zephyrex.extensions.payment.BLL_Payment import PaymentManager
        from zephyrex.extensions.payment.EXT_Payment import EXT_Payment

        charges: list = []

        async def charge(**kwargs: Any) -> Dict[str, Any]:
            charges.append(kwargs)
            created = PaymentManager(
                model_registry=server.app.state.model_registry,
                requester_id=env("ROOT_ID"),
            ).create(
                user_id=kwargs["requester_id"],
                provider="square",
                provider_instance_id=str(uuid.uuid4()),
                external_id=f"pay_{uuid.uuid4().hex}",
                status="succeeded",
                amount=kwargs["amount"],
                currency=kwargs["currency"],
                product_id=kwargs["product_id"],
                refreshed_at=datetime.now(UTC),
            )
            return {"id": created.id, "status": "succeeded"}

        monkeypatch.setattr(EXT_Payment, "payment_create", staticmethod(charge))
        return charges

    def _buy_seats(self, server, buyer, quantity: int, subject_id=None):
        return server.post(
            "/v1/entitlement/purchase",
            json={
                "product_id": TEAM_SEAT.id,
                "payment_method_id": "cnon:card-nonce-ok",
                "quantity": quantity,
                "subject_id": subject_id or f"team-{buyer.id}",
            },
            headers=self._headers(buyer),
        )

    def test_units_are_charged_per_unit_and_counted_for_their_subject(
        self, server, monkeypatch
    ):
        from zephyrex.extensions.payment.BLL_Payment import PaymentManager

        charges = self._charge_succeeds(server, monkeypatch)
        registry = server.app.state.model_registry
        buyer = self._new_user(server)
        team = f"team-{buyer.id}"

        bought = self._buy_seats(server, buyer, 3)
        assert bought.status_code == 200, bought.text
        assert charges[-1]["amount"] == "6.00"
        assert charges[-1]["description"] == "A team seat x 3"
        assert self._buy_seats(server, buyer, 2).status_code == 200
        assert units_bought(TEAM_SEAT.id, team, registry) == 5
        # Units buy no plan, and another subject has none.
        assert self._plan(server, buyer)["plan"] == "basic"
        assert units_bought(TEAM_SEAT.id, "team-someone-else", registry) == 0

        payments = PaymentManager(model_registry=registry, requester_id=env("ROOT_ID"))
        first = bought.json()["payment_id"]
        payments.update(id=first, amount_refunded="2.00")
        assert units_bought(TEAM_SEAT.id, team, registry) == 4
        payments.update(id=first, amount_refunded="6.00")
        assert units_bought(TEAM_SEAT.id, team, registry) == 2

    def test_units_need_a_subject_the_buyer_may_buy_for(self, server, monkeypatch):
        charges = self._charge_succeeds(server, monkeypatch)
        buyer = self._new_user(server)
        theirs = self._buy_seats(server, buyer, 1, subject_id="team-another-user")
        assert theirs.status_code == 404, theirs.text
        unnamed = server.post(
            "/v1/entitlement/purchase",
            json={"product_id": TEAM_SEAT.id, "quantity": 1},
            headers=self._headers(buyer),
        )
        assert unnamed.status_code == 422, unnamed.text
        too_many = self._buy_seats(server, buyer, TEAM_SEAT.max_quantity + 1)
        assert too_many.status_code == 422, too_many.text
        assert charges == []

    def test_a_plan_is_bought_once_and_for_no_subject(self, server, monkeypatch):
        charges = self._charge_succeeds(server, monkeypatch)
        buyer = self._new_user(server)
        for extra in ({"quantity": 2}, {"subject_id": f"team-{buyer.id}"}):
            response = server.post(
                "/v1/entitlement/purchase",
                json={"product_id": PLUS_FOREVER.id, **extra},
                headers=self._headers(buyer),
            )
            assert response.status_code == 422, response.text
        assert charges == []

    def test_a_pending_payment_pays_for_no_units_yet(self, server):
        from zephyrex.extensions.entitlements.BLL_Entitlements import (
            EntitlementPurchaseManager,
        )

        registry = server.app.state.model_registry
        buyer = self._new_user(server)
        pending = self._paid(server, buyer.id, TEAM_SEAT.id, status="pending")
        EntitlementPurchaseManager(
            model_registry=registry, requester_id=env("ROOT_ID")
        ).create(
            user_id=buyer.id,
            payment_id=pending,
            product_id=TEAM_SEAT.id,
            quantity=4,
            subject_id=f"team-{buyer.id}",
        )
        assert units_bought(TEAM_SEAT.id, f"team-{buyer.id}", registry) == 0
