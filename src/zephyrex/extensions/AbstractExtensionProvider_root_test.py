# SPDX-License-Identifier: AGPL-3.0-or-later
"""AbstractStaticExtension.root against a booted, seeded app."""

import pytest

from zephyrex.extensions.email.EXT_EMail import EXT_EMail
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import RotationManager, root_rotation_name
from zephyrex.pydantic2.registry import ModelRegistry


@pytest.fixture
def email_registry(isolated_extension_server):
    """A freshly built app with the email extension (which ships providers, so
    its root rotation is seeded); building it attaches its registry."""
    isolated_extension_server("email")
    EXT_EMail._root_rotation_cache = None
    registry = ModelRegistry.attached()
    assert registry is not None
    yield registry
    EXT_EMail._root_rotation_cache = None


@pytest.mark.parametrize(
    "extension_name, expected",
    [
        ("email", "Root_Email"),
        ("auth_mfa", "Root_Auth_Mfa"),
        ("database_memory", "Root_Database_Memory"),
    ],
)
def test_root_rotation_name(extension_name, expected):
    assert root_rotation_name(extension_name) == expected


def test_root_targets_the_seeded_root_rotation(email_registry):
    root = EXT_EMail.root

    assert root is not None
    seeded = RotationManager(
        model_registry=email_registry, requester_id=env("ROOT_ID")
    ).get(name=root_rotation_name(EXT_EMail.name))
    assert root.target_id == seeded.id
    assert root.model_registry is email_registry


def test_root_is_cached_per_registry(email_registry):
    first = EXT_EMail.root
    assert first is not None
    assert EXT_EMail.root is first


def test_root_is_rebuilt_when_a_different_registry_is_attached(email_registry):
    stale = RotationManager(model_registry=email_registry, requester_id=env("ROOT_ID"))
    stale.model_registry = object()
    EXT_EMail._root_rotation_cache = stale

    root = EXT_EMail.root

    assert root is not None and root is not stale
    assert root.model_registry is email_registry


def test_root_is_none_for_an_extension_with_no_root_rotation(email_registry):
    from zephyrex.extensions.metadata.EXT_Metadata import MetadataExtension

    assert MetadataExtension.root is None
