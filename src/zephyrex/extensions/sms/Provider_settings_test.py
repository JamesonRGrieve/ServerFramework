# SPDX-License-Identifier: AGPL-3.0-or-later
"""Provider instance settings through the API, with Amazon SNS (which
declares a secret, aws_secret_key): the catalogue a client renders, a
secret stored encrypted and never returned, and the provider still reading
it."""

import uuid
from typing import Any, Dict

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.sms.EXT_SMS import EXT_SMS
from zephyrex.extensions.sms.PRV_Amazon import PRV_Amazon_SMS
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceModel,
    ProviderInstanceSettingModel,
)
from zephyrex.testing.factories import if_match_of


class TestProviderSettings(ExtensionServerMixin):
    extension_class = EXT_SMS

    @pytest.fixture
    def headers(self, admin_a) -> Dict[str, str]:
        return {"Authorization": f"Bearer {admin_a.jwt}"}

    @pytest.fixture
    def sns(self, server, headers) -> Dict[str, Any]:
        providers = server.get("/v1/provider", headers=headers).json()["providers"]
        return next(p for p in providers if p["name"] == PRV_Amazon_SMS.name)

    @pytest.fixture
    def instance_id(self, server, headers, sns) -> str:
        response = server.post(
            "/v1/provider/instance",
            json={
                "provider_instance": {
                    "name": f"sns-{uuid.uuid4().hex}",
                    "provider_id": sns["id"],
                    "api_key": "AKIAZEPHYREXTEST0000",
                }
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        return str(response.json()["provider_instance"]["id"])

    def _set(self, server, headers, instance_id: str, key: str, value: str):
        return server.post(
            "/v1/provider/instance/setting",
            json={
                "provider_instance_setting": {
                    "provider_instance_id": instance_id,
                    "key": key,
                    "value": value,
                }
            },
            headers=headers,
        )

    def test_the_catalogue_lists_what_the_provider_reads(self, server, headers, sns):
        response = server.get(f"/v1/provider/{sns['id']}/settings", headers=headers)
        assert response.status_code == 200, response.text
        settings = {s["key"]: s for s in response.json()["settings"]}
        assert set(settings) == {"api_key", "aws_secret_key", "aws_region", "sender_id"}
        assert settings["aws_secret_key"]["write_only"] is True
        assert settings["aws_secret_key"]["env"] == "AWS_SECRET_ACCESS_KEY"
        assert settings["aws_region"] == {
            "key": "aws_region",
            "description": "AWS region",
            "env": "AWS_REGION",
            "default": "us-east-1",
            "write_only": False,
            "field": None,
            "multiline": False,
        }
        assert settings["api_key"]["field"] == "api_key"

    def test_a_secret_is_never_returned(self, server, headers, instance_id):
        created = self._set(
            server, headers, instance_id, "aws_secret_key", "s3cr3t-value"
        )
        assert created.status_code == 201, created.text
        row = created.json()["provider_instance_setting"]
        assert row["write_only"] is True and row["value"] is None

        fetched = server.get(
            f"/v1/provider/instance/setting/{row['id']}", headers=headers
        )
        assert fetched.json()["provider_instance_setting"]["value"] is None
        assert "s3cr3t-value" not in fetched.text

    def test_graphql_never_returns_a_secret_either(self, server, headers, instance_id):
        self._set(server, headers, instance_id, "aws_secret_key", "s3cr3t-value")
        response = server.post(
            "/graphql",
            json={"query": "{ providerInstanceSettings { key value writeOnly } }"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert "s3cr3t-value" not in response.text
        rows = response.json()["data"]["providerInstanceSettings"]
        secrets = [r for r in rows if r["key"] == "aws_secret_key"]
        assert secrets and all(r["writeOnly"] and r["value"] is None for r in secrets)

    def test_a_plain_setting_is_shown(self, server, headers, instance_id):
        created = self._set(server, headers, instance_id, "aws_region", "eu-west-1")
        row = created.json()["provider_instance_setting"]
        assert row["write_only"] is False and row["value"] == "eu-west-1"

    def test_the_provider_reads_the_secret_decrypted(
        self, server, headers, instance_id
    ):
        self._set(server, headers, instance_id, "aws_secret_key", "s3cr3t-value")
        registry = server.app.state.model_registry
        base, root_id = registry.DB.manager.Base, env("ROOT_ID")
        stored = ProviderInstanceSettingModel.DB(base).list(
            requester_id=root_id,
            model_registry=registry,
            return_type="dto",
            override_dto=ProviderInstanceSettingModel,
            provider_instance_id=instance_id,
            key="aws_secret_key",
        )
        assert stored and stored[0].value != "s3cr3t-value"  # encrypted at rest
        instance = ProviderInstanceModel.DB(base).get(
            requester_id=root_id,
            model_registry=registry,
            id=instance_id,
            return_type="dto",
            override_dto=ProviderInstanceModel,
        )
        assert PRV_Amazon_SMS.setting(instance, "aws_secret_key") == "s3cr3t-value"

    def test_an_update_cannot_make_a_secret_readable(
        self, server, headers, instance_id
    ):
        created = self._set(server, headers, instance_id, "aws_secret_key", "first")
        row_id = created.json()["provider_instance_setting"]["id"]
        updated = server.put(
            f"/v1/provider/instance/setting/{row_id}",
            json={
                "provider_instance_setting": {"value": "second", "write_only": False}
            },
            headers={
                **headers,
                **if_match_of(created.json()["provider_instance_setting"]),
            },
        )
        assert updated.status_code == 200, updated.text
        row = updated.json()["provider_instance_setting"]
        assert row["write_only"] is True and row["value"] is None
