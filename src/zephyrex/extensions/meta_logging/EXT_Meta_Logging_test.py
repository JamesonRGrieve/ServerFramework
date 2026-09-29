# SPDX-License-Identifier: AGPL-3.0-or-later
"""The meta_logging extension contributes its two log tables, and nothing it
registers reaches an app that did not load it."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from zephyrex.extensions.meta_logging.BLL_Meta_Logging import (
    AuditLogManager,
    AuditLogModel,
    SystemLogManager,
    SystemLogModel,
)
from zephyrex.extensions.meta_logging.EXT_Meta_Logging import EXT_Meta_Logging


def test_extension_contributes_exactly_the_two_log_models():
    assert EXT_Meta_Logging.name == "meta_logging"
    assert EXT_Meta_Logging.models == {AuditLogModel, SystemLogModel}


def test_hooks_are_bound_to_the_log_managers_only():
    """The guards are registered on meta_logging's own manager classes, so
    they run only where those managers run."""
    from zephyrex.logic.BLL_Auth import UserManager

    module = AuditLogManager.__module__
    for manager in (AuditLogManager, SystemLogManager):
        hooks = manager._hook_registry.get_hooks("list")
        assert {h["func"].__module__ for h in hooks["before"]} == {module}
    user_hooks = UserManager._hook_registry.get_hooks("list")
    assert all(
        h["func"].__module__ != module
        for h in user_hooks["before"] + user_hooks["after"]
    )


def test_an_app_without_meta_logging_binds_no_log_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db"))
    prepare_test_registry()
    registry = instance(
        db_prefix=f"test.meta_logging_absent.{uuid.uuid4().hex[:8]}",
        extensions="",
    ).state.model_registry

    assert "meta_logging" not in registry.loaded_extension_names()
    assert not registry.is_model_bound(AuditLogModel)
    assert not registry.is_model_bound(SystemLogModel)
