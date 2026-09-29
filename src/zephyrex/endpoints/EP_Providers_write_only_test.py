# SPDX-License-Identifier: AGPL-3.0-or-later
"""A provider instance's api_key is write-only: stored and used by
providers, never returned by REST or GraphQL."""

import uuid

import pytest

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceModel, ProviderManager

SECRET = "sk-live-write-only-check"


@pytest.fixture
def instance_id(server, admin_a, model_registry) -> str:
    provider = ProviderManager(
        model_registry=model_registry, requester_id=env("ROOT_ID")
    ).create(name=f"write_only_provider_{uuid.uuid4().hex}")
    assert not isinstance(provider, list)
    response = server.post(
        "/v1/provider/instance",
        json={
            "provider_instance": {
                "name": f"write_only_{uuid.uuid4().hex}",
                "provider_id": provider.id,
                "api_key": SECRET,
            }
        },
        headers={"Authorization": f"Bearer {admin_a.jwt}"},
    )
    assert response.status_code == 201, response.text
    assert SECRET not in response.text
    return str(response.json()["provider_instance"]["id"])


def test_rest_get_omits_the_key(server, admin_a, instance_id):
    response = server.get(
        f"/v1/provider/instance/{instance_id}",
        headers={"Authorization": f"Bearer {admin_a.jwt}"},
    )

    assert response.status_code == 200, response.text
    assert "api_key" not in response.json()["provider_instance"]
    assert SECRET not in response.text


def test_graphql_type_has_no_key_field(server, admin_a):
    response = server.post(
        "/graphql",
        json={"query": '{ __type(name: "ProviderInstanceType") { fields { name } } }'},
        headers={"Authorization": f"Bearer {admin_a.jwt}"},
    )

    fields = {f["name"] for f in response.json()["data"]["__type"]["fields"]}
    assert "apiKey" not in fields
    assert "name" in fields


def test_the_stored_key_reaches_providers(instance_id, model_registry):
    instance = ProviderInstanceModel.DB(model_registry.DB.manager.Base).get(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        return_type="dto",
        override_dto=ProviderInstanceModel,
        id=instance_id,
    )

    assert instance.api_key == SECRET
