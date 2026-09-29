# SPDX-License-Identifier: AGPL-3.0-or-later
"""Models with write-only fields bypass the entity cache.

A cached entity is a dump of the DTO, and dumps omit write-only fields
(``Field(exclude=True)``); a cache hit would hand readers an entity without
the secret they need (e.g. a TOTP seed at verification time).
"""

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import TeamManager
from zephyrex.logic.BLL_Providers import ProviderInstanceManager


def test_model_with_write_only_fields_is_not_cached(model_registry):
    manager = ProviderInstanceManager(
        model_registry=model_registry, requester_id=env("ROOT_ID")
    )

    assert manager._caches_entities is False


def test_model_without_write_only_fields_is_cached(model_registry):
    manager = TeamManager(model_registry=model_registry, requester_id=env("ROOT_ID"))

    assert manager._caches_entities is True
