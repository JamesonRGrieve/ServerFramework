# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lib.Rooms``: what a ``room_of`` mapping may return, and that its hooks do
nothing in an app without the ``rooms`` extension."""

from __future__ import annotations

from types import SimpleNamespace

from zephyrex.lib.Rooms import _active, _rooms


def test_a_mapping_names_no_room_one_or_several():
    record = SimpleNamespace(id="t1", scene_id="s1", campaign_id="c1")
    assert _rooms(lambda r: None, record) == []
    assert _rooms(lambda r: ("scene", r.scene_id), record) == [("scene", "s1")]
    assert _rooms(
        lambda r: [("scene", r.scene_id), ("campaign", r.campaign_id)], record
    ) == [("scene", "s1"), ("campaign", "c1")]


def test_a_room_without_an_id_is_dropped():
    record = SimpleNamespace(id="t1", scene_id=None, campaign_id="c1")
    assert _rooms(
        lambda r: [("scene", r.scene_id), ("campaign", r.campaign_id)], record
    ) == [("campaign", "c1")]


def test_hooks_only_act_where_rooms_is_loaded():
    def manager(csv: str) -> SimpleNamespace:
        registry = SimpleNamespace(extension_registry=SimpleNamespace(csv=csv))
        return SimpleNamespace(model_registry=registry)

    assert _active(manager("metadata,rooms")) is True
    assert _active(manager("metadata,live_sync")) is False
    assert _active(manager("")) is False
