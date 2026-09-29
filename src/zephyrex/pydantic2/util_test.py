# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from zephyrex.pydantic2.util import manager_resource_name, wire_resource_name


def test_model_wire_name_matches_its_managers():
    assert wire_resource_name("APIKeyModel", "Model") == "api_key"
    assert wire_resource_name("MagicLinkModel", "Model") == "magic_link"


@pytest.mark.parametrize(
    "class_name, expected",
    [
        ("UserManager", "user"),
        ("MagicLinkManager", "magic_link"),
        ("DevicePairingManager", "device_pairing"),
        ("APIKeyManager", "api_key"),
        ("UserOAuthManager", "user_o_auth"),
        ("ProviderInstanceSettingManager", "provider_instance_setting"),
        ("S3BucketManager", "s3_bucket"),
        ("WidgetManagerV2", "widget_v2"),
    ],
)
def test_manager_resource_name(class_name, expected):
    assert manager_resource_name(type(class_name, (), {})) == expected


def test_only_the_manager_suffix_is_removed():
    assert manager_resource_name(type("ManagerAccountManager", (), {})) == (
        "manager_account"
    )
