# SPDX-License-Identifier: AGPL-3.0-or-later
"""Webhook and email triggers, fired the way they are in production.

Webhook calls are real HTTP requests to the app, signed with the secret the
app handed out; mail is a real RFC 5322 message parsed and handed to the
email extension's inbound hook point. A turn that thinks does so on a real
provider instance calling a real local model server.

Before these, a trigger could listen only to conversation messages: there
was no webhook endpoint, no inbound mail path, and no secret or address for
either."""

import json
import time
import uuid
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    EMAIL_DOMAIN_SETTING,
    AgentManager,
    InvocationInstanceManager,
    InvocationTriggerManager,
    InvocationTriggerModel,
    ProviderInstanceAgentManager,
)
from zephyrex.extensions.ai_agents.EventSources import (
    MAX_WEBHOOK_BODY_BYTES,
    REPLAY_WINDOW_SECONDS,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    webhook_signature,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents
from zephyrex.extensions.ai_agents.PinnedInstances_test import (
    ModelServer,
    model_instance,
)
from zephyrex.extensions.email.InboundEmail import InboundEmail, receive_inbound_email
from zephyrex.lib.Environment import env
from zephyrex.lib.SecretEncryption import decrypt_secret

DOMAIN = "agents.example.test"
INSTRUCTIONS = "Triage what arrives."


def auth(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def signed(secret: str, body: bytes, timestamp: Optional[int] = None) -> Dict[str, str]:
    moment = str(int(time.time()) if timestamp is None else timestamp)
    return {
        TIMESTAMP_HEADER: moment,
        SIGNATURE_HEADER: webhook_signature(secret, moment, body),
        "Content-Type": "application/json",
    }


class EventTriggers(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    def _agent(self, owner: Any, model_registry: Any) -> Any:
        return AgentManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(name=f"Agent {uuid.uuid4()}")

    def _trigger(
        self, owner: Any, model_registry: Any, agent: Any, **fields: Any
    ) -> Any:
        return InvocationTriggerManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(
            agent_id=agent.id,
            invocation_type="event",
            invocation_payload=INSTRUCTIONS,
            **fields,
        )

    def _turns(self, owner: Any, model_registry: Any, agent: Any) -> List[Any]:
        return InvocationInstanceManager(
            requester_id=owner.id, model_registry=model_registry
        ).list(agent_id=agent.id)


class TestWebhookTriggers(EventTriggers):
    def _webhook(self, server: Any, owner: Any, model_registry: Any, agent: Any):
        trigger = self._trigger(owner, model_registry, agent, event_source="webhook")
        made = server.post(
            f"/v1/invocation-trigger/{trigger.id}/webhook-secret", headers=auth(owner)
        )
        assert made.status_code == 200, made.text
        return trigger, made.json()["secret"]

    def _call(self, server: Any, trigger_id: str, body: bytes, headers: Dict[str, str]):
        return server.post(
            f"/v1/invocation-trigger/{trigger_id}/webhook",
            content=body,
            headers=headers,
        )

    def test_the_secret_is_shown_once_and_kept_encrypted(
        self, server, admin_a, admin_b, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        trigger, secret = self._webhook(server, admin_a, model_registry, agent)
        shown = server.get(
            f"/v1/invocation-trigger/{trigger.id}", headers=auth(admin_a)
        )
        assert shown.status_code == 200, shown.text
        assert secret not in shown.text and "webhook_secret" not in shown.text
        [row] = InvocationTriggerModel.DB(model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            return_type="dto",
            override_dto=InvocationTriggerModel,
            id=trigger.id,
        )
        assert row.webhook_secret != secret
        assert decrypt_secret(row.webhook_secret) == secret
        stolen = server.post(
            f"/v1/invocation-trigger/{trigger.id}/webhook-secret", headers=auth(admin_b)
        )
        assert stolen.status_code == 404, stolen.text

    def test_a_secret_cannot_be_written_by_a_user(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        triggers = InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        trigger = triggers.create(
            agent_id=agent.id,
            invocation_type="event",
            event_source="webhook",
            webhook_secret="chosen",
        )
        triggers.update(trigger.id, webhook_secret="chosen")
        [row] = InvocationTriggerModel.DB(model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            return_type="dto",
            override_dto=InvocationTriggerModel,
            id=trigger.id,
        )
        assert row.webhook_secret is None

    def test_only_a_webhook_trigger_has_a_secret(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        timer = InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(agent_id=agent.id, invocation_type="timer", interval_seconds=60)
        with pytest.raises(HTTPException) as refused:
            InvocationTriggerManager(
                requester_id=admin_a.id, model_registry=model_registry
            ).rotate_webhook_secret(timer.id)
        assert refused.value.status_code == 422

    def test_a_signed_call_fires_the_owners_turn(
        self, server, admin_a, admin_b, model_registry, local_http_server
    ):
        models = ModelServer(local_http_server, ["hooked"])
        agent = self._agent(admin_a, model_registry)
        ProviderInstanceAgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(
            agent_id=agent.id,
            provider_instance_id=model_instance(
                model_registry, admin_a, models.base_url("hooked")
            ).id,
        )
        trigger, secret = self._webhook(server, admin_a, model_registry, agent)
        body = json.dumps({"event": "build.failed", "id": 7}).encode()
        # A token the call happens to carry is never used: the turn is the
        # agent's owner's.
        headers = {**signed(secret, body), **auth(admin_b)}
        fired = self._call(server, trigger.id, body, headers)
        assert fired.status_code == 200, fired.text
        assert fired.json()["status"] == "succeeded"
        [turn] = self._turns(admin_a, model_registry, agent)
        assert turn.id == fired.json()["invocation_instance_id"]
        assert turn.user_id == admin_a.id
        assert turn.invocation_trigger_id == trigger.id
        assert turn.payload == f"{INSTRUCTIONS}\n\n{body.decode()}"
        assert models.called() == ["hooked"]
        counted = InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).get(id=trigger.id)
        assert counted.fire_count == 1 and counted.last_fired_at is not None

    def test_unsigned_calls_are_401_and_fire_nothing(
        self, server, admin_a, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        trigger, secret = self._webhook(server, admin_a, model_registry, agent)
        timer = InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(agent_id=agent.id, invocation_type="timer", interval_seconds=60)
        body = b'{"event": "x"}'
        stale = int(time.time()) - REPLAY_WINDOW_SECONDS - 60
        for trigger_id, headers in (
            (trigger.id, {}),
            (trigger.id, signed("not the secret", body)),
            (trigger.id, signed(secret, b"other body")),
            (trigger.id, signed(secret, body, timestamp=stale)),
            (trigger.id, {**signed(secret, body), TIMESTAMP_HEADER: "soon"}),
            (str(uuid.uuid4()), signed(secret, body)),
            (timer.id, signed(secret, body)),
        ):
            refused = self._call(server, trigger_id, body, headers)
            assert refused.status_code == 401, (trigger_id, headers, refused.text)
        assert self._turns(admin_a, model_registry, agent) == []

    def test_a_replayed_call_is_401(self, server, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        trigger, secret = self._webhook(server, admin_a, model_registry, agent)
        body = b'{"event": "once"}'
        headers = signed(secret, body)
        assert self._call(server, trigger.id, body, headers).status_code == 200
        replayed = self._call(server, trigger.id, body, headers)
        assert replayed.status_code == 401, replayed.text
        assert len(self._turns(admin_a, model_registry, agent)) == 1

    def test_a_rotated_secret_retires_the_old_one(
        self, server, admin_a, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        trigger, old = self._webhook(server, admin_a, model_registry, agent)
        server.post(
            f"/v1/invocation-trigger/{trigger.id}/webhook-secret", headers=auth(admin_a)
        )
        body = b"{}"
        assert (
            self._call(server, trigger.id, body, signed(old, body)).status_code == 401
        )

    def test_an_oversized_body_is_413(self, server, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        trigger, secret = self._webhook(server, admin_a, model_registry, agent)
        body = json.dumps({"pad": "x" * MAX_WEBHOOK_BODY_BYTES}).encode()
        response = self._call(server, trigger.id, body, signed(secret, body))
        assert response.status_code == 413, response.text
        assert self._turns(admin_a, model_registry, agent) == []

    def test_a_disabled_trigger_is_409(self, server, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        trigger, secret = self._webhook(server, admin_a, model_registry, agent)
        InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).update(trigger.id, enabled=False)
        body = b"{}"
        response = self._call(server, trigger.id, body, signed(secret, body))
        assert response.status_code == 409, response.text
        assert self._turns(admin_a, model_registry, agent) == []

    def test_calls_are_rate_limited(self, server, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        trigger, _ = self._webhook(server, admin_a, model_registry, agent)
        statuses = [
            self._call(server, trigger.id, b"{}", {}).status_code for _ in range(61)
        ]
        assert statuses[:60] == [401] * 60
        assert statuses[60] == 429


class TestEmailTriggers(EventTriggers):
    @pytest.fixture
    def domain(self, set_env: Any) -> str:
        set_env(EMAIL_DOMAIN_SETTING, DOMAIN)
        return DOMAIN

    def _mail(
        self, to: str, sender: str = "ops@example.org", subject: str = "Disk full"
    ) -> InboundEmail:
        message = EmailMessage()
        message["From"] = f"Ops <{sender}>"
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = f"<{uuid.uuid4()}@example.org>"
        message.set_content("The disk on db-2 is at 98%.")
        return InboundEmail.parse(message.as_bytes())

    def test_an_email_trigger_needs_the_domain(self, admin_a, model_registry, set_env):
        set_env(EMAIL_DOMAIN_SETTING, "")
        agent = self._agent(admin_a, model_registry)
        with pytest.raises(HTTPException) as refused:
            self._trigger(admin_a, model_registry, agent, event_source="email")
        assert refused.value.status_code == 422

    def test_the_address_is_the_servers(self, admin_a, model_registry, domain):
        agent = self._agent(admin_a, model_registry)
        trigger = self._trigger(
            admin_a,
            model_registry,
            agent,
            event_source="email",
            email_address=f"ceo@{domain}",
        )
        assert trigger.email_address.endswith(f"@{domain}")
        assert trigger.email_address != f"ceo@{domain}"
        moved = InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).update(trigger.id, email_address=f"ceo@{domain}")
        assert moved.email_address == trigger.email_address

    @pytest.mark.parametrize(
        "event_filter",
        ["not json", "[]", '{"to": "x"}', '{"from": 3}', '{"subject": ""}'],
    )
    def test_a_bad_filter_is_422(self, event_filter, admin_a, model_registry, domain):
        agent = self._agent(admin_a, model_registry)
        with pytest.raises(HTTPException) as refused:
            self._trigger(
                admin_a,
                model_registry,
                agent,
                event_source="email",
                event_filter=event_filter,
            )
        assert refused.value.status_code == 422

    async def test_mail_to_the_address_fires_the_owners_turn(
        self, admin_a, model_registry, domain
    ):
        agent = self._agent(admin_a, model_registry)
        trigger = self._trigger(
            admin_a,
            model_registry,
            agent,
            event_source="email",
            event_filter=json.dumps({"from": "@example.org", "subject": "disk"}),
        )
        mail = self._mail(f"Agent <{trigger.email_address.upper()}>")
        assert await receive_inbound_email(model_registry, mail) == 1
        [turn] = self._turns(admin_a, model_registry, agent)
        assert turn.user_id == admin_a.id
        assert turn.invocation_trigger_id == trigger.id
        assert turn.payload.startswith(f"{INSTRUCTIONS}\n\nAn email arrived.")
        assert "Subject: Disk full" in turn.payload
        assert "db-2 is at 98%" in turn.payload
        counted = InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).get(id=trigger.id)
        assert counted.fire_count == 1

    async def test_mail_the_filter_refuses_fires_nothing(
        self, admin_a, model_registry, domain
    ):
        agent = self._agent(admin_a, model_registry)
        trigger = self._trigger(
            admin_a,
            model_registry,
            agent,
            event_source="email",
            event_filter=json.dumps({"from": "boss@example.org"}),
        )
        await receive_inbound_email(model_registry, self._mail(trigger.email_address))
        await receive_inbound_email(
            model_registry,
            self._mail(trigger.email_address, sender="boss@example.org.evil"),
        )
        assert self._turns(admin_a, model_registry, agent) == []

    async def test_mail_wakes_only_the_triggers_it_is_for(
        self, admin_a, admin_b, model_registry, domain
    ):
        mine = self._agent(admin_a, model_registry)
        theirs = self._agent(admin_b, model_registry)
        my_trigger = self._trigger(admin_a, model_registry, mine, event_source="email")
        self._trigger(admin_b, model_registry, theirs, event_source="email")
        off = self._trigger(admin_a, model_registry, mine, event_source="email")
        InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).update(off.id, enabled=False)
        await receive_inbound_email(
            model_registry,
            self._mail(f"{my_trigger.email_address}, {off.email_address}"),
        )
        assert [
            t.invocation_trigger_id for t in self._turns(admin_a, model_registry, mine)
        ] == [my_trigger.id]
        assert self._turns(admin_b, model_registry, theirs) == []
