# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who writes a user's customer link, who owns a payment record, and the
login check on subscriptions: real app, real database, Stripe answered by
a local server."""

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Dict

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.payment.BLL_Payment import (
    LINK_FIELDS,
    PaymentManager,
    PaymentSubscriptionManager,
    link_customer,
    linked_customer,
    require_active_subscription,
)
from zephyrex.extensions.payment.EXT_Payment import EXT_Payment
from zephyrex.extensions.payment.EXT_Payment_test import (
    add_instance,
    json_answer,
    rotation,
)
from zephyrex.extensions.payment.PRV_Stripe_Payment import PRV_Stripe_Payment
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserManager, UserModel
from zephyrex.testing.factories import TEST_PASSWORD, current_if_match


def auth(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def saving_self(server: Any, user: Any) -> Dict[str, str]:
    """``user``'s headers for a save of their own account, naming the
    version they read, as a client does."""
    return {**auth(user), **current_if_match(server, "/v1/user", auth(user))}


def subscription(status: str) -> Dict[str, Any]:
    return {
        "id": "sub_1",
        "status": status,
        "customer": "cus_1",
        "current_period_end": 1_900_000_000,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_1"}}]},
    }


class TestPaymentRecords(ExtensionServerMixin):
    extension_class = EXT_Payment

    @pytest.fixture
    def registry(self, server) -> Any:
        return server.app.state.model_registry

    @pytest.fixture
    def payer(self, server):
        from conftest_factories import create_user

        return create_user(server, f"payer_{uuid.uuid4().hex[:8]}@example.com")

    def fields(self, **extra: Any) -> Dict[str, Any]:
        return {
            "provider": "stripe",
            "provider_instance_id": "inst",
            "external_id": f"pi_{uuid.uuid4().hex[:8]}",
            "status": "pending",
            "amount": "1.00",
            "currency": "USD",
            "refreshed_at": datetime.now(UTC),
            **extra,
        }

    # The customer link.

    def test_a_user_cannot_set_their_own_customer_link(self, server, payer):
        """A user who names a customer id names someone else's customer: its
        details, and its subscriptions. Only the server writes the link
        (this PUT used to be accepted)."""
        for field in ("external_payment_id", "payment_instance_id"):
            response = server.put(
                "/v1/user",
                json={"user": {field: "cus_someone_else"}},
                headers=saving_self(server, payer),
            )
            assert response.status_code == 403, response.text
        response = server.put(
            "/v1/user",
            json={"user": {"display_name": "Payer"}},
            headers=saving_self(server, payer),
        )
        assert response.status_code == 200, response.text

    @pytest.mark.parametrize("field", LINK_FIELDS)
    def test_a_registration_naming_a_customer_stores_nothing(
        self, server, registry, field
    ):
        """The customer link is the server's to write. Registration used to
        accept ``external_payment_id`` (core listed it as a registration
        field) and store it on the new row; this test used to assert only
        that the stored id linked nothing. Naming either link field now
        refuses the registration, and no account is made."""
        email = f"reg_{uuid.uuid4().hex[:8]}@example.com"
        response = server.post(
            "/v1/user",
            json={
                "user": {
                    "email": email,
                    "password": "Str0ng!Passw0rd#",
                    field: "cus_victim",
                }
            },
        )
        assert response.status_code == 422, response.text
        assert field in response.text
        users = UserModel.DB(registry.DB.manager.Base)
        assert not users.exists(
            requester_id=env("ROOT_ID"), model_registry=registry, email=email
        )
        assert not users.exists(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            filters=[getattr(users, field) == "cus_victim"],
        )

    def test_a_registration_without_a_customer_still_registers(self, server, registry):
        email = f"reg_{uuid.uuid4().hex[:8]}@example.com"
        response = server.post(
            "/v1/user",
            json={"user": {"email": email, "password": "Str0ng!Passw0rd#"}},
        )
        assert response.status_code == 201, response.text
        user = UserManager(model_registry=registry, requester_id=env("ROOT_ID")).get(
            id=response.json()["id"]
        )
        assert linked_customer(user) is None

    def test_the_server_links(self, server, registry, payer):
        link_customer(payer.id, "inst_1", "cus_1")
        user = UserManager(model_registry=registry, requester_id=env("ROOT_ID")).get(
            id=payer.id
        )
        assert linked_customer(user) == ("inst_1", "cus_1")

    # Ownership.

    def test_a_record_is_the_requesters(self, server, registry, payer, admin_b):
        payments = PaymentManager(model_registry=registry, requester_id=payer.id)
        made = payments.create(**self.fields(user_id=admin_b.id))
        assert made.user_id == payer.id
        batch = payments.create(
            entities=[self.fields(user_id=admin_b.id), self.fields(user_id=admin_b.id)]
        )
        assert {record.user_id for record in batch} == {payer.id}
        root = PaymentManager(model_registry=registry, requester_id=env("ROOT_ID"))
        assert root.create(**self.fields(user_id=payer.id)).user_id == payer.id

    def test_an_update_never_moves_the_owner(self, server, registry, payer, admin_b):
        payments = PaymentManager(model_registry=registry, requester_id=payer.id)
        made = payments.create(**self.fields())
        moved = payments.update(made.id, user_id=admin_b.id, status="succeeded")
        assert moved.user_id == payer.id and moved.status == "succeeded"

    def test_another_user_cannot_see_a_record(self, server, registry, payer, admin_b):
        made = PaymentManager(model_registry=registry, requester_id=payer.id).create(
            **self.fields()
        )
        with pytest.raises(HTTPException) as raised:
            PaymentManager(model_registry=registry, requester_id=admin_b.id).get(
                id=made.id
            )
        assert raised.value.status_code == 404

    def test_the_tables_are_read_only_over_rest(self, server, payer):
        body = {**self.fields(), "refreshed_at": datetime.now(UTC).isoformat()}
        for path, key in (
            ("/v1/payment", "payment"),
            ("/v1/payment_subscription", "payment_subscription"),
        ):
            response = server.post(path, json={key: body}, headers=auth(payer))
            assert response.status_code in (404, 405), (path, response.text)
        listed = server.get("/v1/payment", headers=auth(payer))
        assert listed.status_code == 200, listed.text

    # The login check.

    @pytest.fixture
    def stripe_subscription(self, server, registry, local_http_server, monkeypatch):
        """A subscription of ``user`` at a Stripe account answering
        ``status`` (or failing)."""

        def _make(user: Any, status: str) -> Any:
            if status == "unreachable":
                route = json_answer({}, 503)
            else:
                route = json_answer(subscription(status))
            upstream = local_http_server({"/v1/subscriptions/sub_1": route})
            instance = add_instance(
                registry,
                PRV_Stripe_Payment.name,
                "sk_test",
                {"api_base": upstream.base_url},
            )
            monkeypatch.setattr(
                EXT_Payment, "_root_rotation_cache", rotation(registry, instance)
            )
            subscriptions = PaymentSubscriptionManager(
                model_registry=registry, requester_id=env("ROOT_ID")
            )
            return subscriptions.create(
                user_id=user.id,
                provider="stripe",
                provider_instance_id=str(instance.id),
                external_id="sub_1",
                status="active",
                active=True,
                refreshed_at=datetime.now(UTC),
            )

        return _make

    def test_a_user_without_subscriptions_is_not_checked(self, server, registry, payer):
        require_active_subscription(payer.id, registry)

    def test_an_active_subscription_passes(
        self, server, registry, payer, stripe_subscription
    ):
        stripe_subscription(payer, "active")
        require_active_subscription(payer.id, registry)

    def test_a_lapsed_subscription_is_refused(
        self, server, registry, payer, stripe_subscription
    ):
        record = stripe_subscription(payer, "canceled")
        with pytest.raises(HTTPException) as raised:
            require_active_subscription(payer.id, registry)
        assert raised.value.status_code == 402
        mirrored = PaymentSubscriptionManager(
            model_registry=registry, requester_id=env("ROOT_ID")
        ).get(id=record.id)
        assert mirrored.status == "canceled" and not mirrored.active

    def password_login(self, server, user: Any) -> Any:
        credentials = base64.b64encode(f"{user.email}:{TEST_PASSWORD}".encode())
        return server.post(
            "/v1/user/authorize",
            headers={"Authorization": f"Basic {credentials.decode()}"},
        )

    def test_a_lapsed_subscription_refuses_password_login(
        self, server, payer, stripe_subscription
    ):
        """Nothing called the subscription check: ``UserManager.login`` is a
        static method no manager hook reaches, so a lapsed subscriber signed
        in. It now runs as payment's login check, before any session."""
        stripe_subscription(payer, "canceled")
        refused = self.password_login(server, payer)
        assert refused.status_code == 402, refused.text
        assert "token" not in refused.json()
        assert "set-cookie" not in refused.headers

    def test_an_active_subscription_signs_in(self, server, payer, stripe_subscription):
        stripe_subscription(payer, "active")
        signed_in = self.password_login(server, payer)
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["token"]

    def test_an_unreachable_provider_keeps_the_last_record(
        self, server, registry, payer, stripe_subscription
    ):
        stripe_subscription(payer, "unreachable")
        require_active_subscription(payer.id, registry)

    def test_root_and_the_switch_are_not_checked(
        self, server, registry, payer, stripe_subscription, set_env
    ):
        stripe_subscription(payer, "canceled")
        require_active_subscription(env("ROOT_ID"), registry)
        set_env("DISABLE_SUBSCRIPTION_VALIDATION", "true")
        require_active_subscription(payer.id, registry)

    async def test_status_reads_every_subscription(
        self, server, payer, stripe_subscription
    ):
        stripe_subscription(payer, "past_due")
        status = await EXT_Payment.subscription_status(payer.id)
        assert status["active"] is False
        assert json.dumps(status)
