# SPDX-License-Identifier: AGPL-3.0-or-later
"""The framework drives the extension lifecycle on real app builds.

``on_initialize`` runs once per extension per app build and a False return
fails the build naming the extension; ``on_start``/``on_stop`` run in the app's
lifespan. Registrations made by the lifecycle are process-global, so these
tests also prove they act only on the apps that loaded the registering
extension."""

from __future__ import annotations

import textwrap
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import CORE_COMPANION_EXTENSIONS

PROBE_EXTENSION = "lifecycle_probe"
REFUSING_EXTENSION = "lifecycle_refusal"


def _write_extension(root: Path, name: str, class_body: str) -> None:
    extension_dir = root / name
    extension_dir.mkdir(parents=True)
    (extension_dir / "__init__.py").write_text("", encoding="utf-8")
    source = textwrap.dedent("""\
        from typing import ClassVar

        from zephyrex.extensions.AbstractExtensionProvider import (
            AbstractStaticExtension,
        )


        class EXT_Probe(AbstractStaticExtension):
            name: ClassVar[str] = "{name}"
            calls: ClassVar[list[str]] = []
        """).format(name=name) + textwrap.indent(textwrap.dedent(class_body), "    ")
    (extension_dir / "EXT_Probe.py").write_text(source, encoding="utf-8")


@pytest.fixture
def build_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build real apps on a private SQLite directory, optionally with a
    consumer extensions root; restores the global extensions root after."""
    from zephyrex.app import instance
    from zephyrex.lib.Paths import set_extensions_root
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db"))

    def _build(extensions: str, extensions_path: Path | None = None) -> FastAPI:
        prepare_test_registry()
        app: FastAPI = instance(
            db_prefix=f"test.lifecycle.{uuid.uuid4().hex[:8]}",
            extensions=extensions,
            extensions_path=str(extensions_path) if extensions_path else None,
        )
        return app

    try:
        yield _build
    finally:
        set_extensions_root(None)


def _loaded_extension(app: FastAPI, name: str) -> Any:
    """The extension class ``name`` as loaded into ``app`` (the probe classes
    are written at test time, so their ``calls`` attribute is untyped)."""
    registry = app.state.model_registry.extension_registry
    return next(ext for ext in registry.extensions if ext.name == name)


class TestLifecycleIsDriven:
    def test_initialize_once_per_build_and_start_stop_in_lifespan(
        self, build_app, tmp_path: Path
    ):
        root = tmp_path / "extensions"
        _write_extension(
            root,
            PROBE_EXTENSION,
            """
            @classmethod
            def on_initialize(cls) -> bool:
                cls.calls.append("initialize")
                return True

            @classmethod
            def on_start(cls) -> None:
                cls.calls.append("start")

            @classmethod
            def on_stop(cls) -> None:
                cls.calls.append("stop")
            """,
        )

        app = build_app(PROBE_EXTENSION, root)
        probe = _loaded_extension(app, PROBE_EXTENSION)
        assert probe.calls == ["initialize"]

        with TestClient(app):
            assert probe.calls == ["initialize", "start"]
        assert probe.calls == ["initialize", "start", "stop"]

        build_app(PROBE_EXTENSION, root)
        assert probe.calls == ["initialize", "start", "stop", "initialize"]

    def test_initialize_returning_false_fails_the_build_naming_it(
        self, build_app, tmp_path: Path
    ):
        from zephyrex import StartupError

        root = tmp_path / "extensions"
        _write_extension(
            root,
            REFUSING_EXTENSION,
            """
            @classmethod
            def on_initialize(cls) -> bool:
                return False
            """,
        )

        with pytest.raises(StartupError, match=f"'{REFUSING_EXTENSION}'"):
            build_app(REFUSING_EXTENSION, root)


@pytest.fixture
def isolated_merge_handlers() -> Iterator[None]:
    """Start from an empty merge-handler table so only the app build under
    test can register the handlers; restore the table afterwards."""
    from zephyrex.extensions.auth_merge.BLL_Auth_Merge import _HANDLERS

    snapshot = dict(_HANDLERS)
    _HANDLERS.clear()
    try:
        yield
    finally:
        _HANDLERS.clear()
        _HANDLERS.update(snapshot)


class TestMergeParticipationIsRegisteredByTheBuild:
    """auth_api_keys, auth_notifications and auth_recovery_questions join the
    account merge from ``on_initialize`` alone; nothing here calls it."""

    MERGE_EXTENSIONS = (
        "auth_merge",
        "auth_api_keys",
        "auth_notifications",
        "auth_recovery_questions",
    )

    @staticmethod
    def _register_user(model_registry) -> str:
        from zephyrex.lib.Environment import env
        from zephyrex.logic.BLL_Auth import UserManager, UserModel

        email = f"merge_{uuid.uuid4().hex[:10]}@example.com"
        UserManager.register(
            {
                "email": email,
                "password": "Merge1234!",
                "first_name": "Merge",
                "last_name": "Subject",
            },
            model_registry,
        )
        users = UserModel.DB(model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"), model_registry=model_registry, email=email
        )
        assert len(users) == 1
        return str(users[0]["id"])

    def test_merge_covers_api_keys_notifications_and_recovery_questions(
        self, build_app, isolated_merge_handlers
    ):
        from zephyrex.extensions.auth_api_keys.BLL_Auth_APIKeys import APIKeyModel
        from zephyrex.extensions.auth_merge.BLL_Auth_Merge import UserMergeManager
        from zephyrex.extensions.auth_notifications.BLL_Auth_Notifications import (
            NotificationModel,
            UserNotificationModel,
        )
        from zephyrex.extensions.auth_recovery_questions.BLL_Recovery_Questions import (
            UserRecoveryQuestionModel,
        )
        from zephyrex.lib.Environment import env

        extensions = ",".join(self.MERGE_EXTENSIONS + CORE_COMPANION_EXTENSIONS)
        registry = build_app(extensions).state.model_registry
        base = registry.DB.manager.Base
        root_id = env("ROOT_ID")
        initiating_id = self._register_user(registry)
        target_id = self._register_user(registry)

        KeyDB = APIKeyModel.DB(base)
        key = KeyDB.create(
            requester_id=root_id,
            model_registry=registry,
            return_type="dto",
            override_dto=APIKeyModel,
            name="target key",
            key_hash=uuid.uuid4().hex,
            user_id=target_id,
            is_revoked=False,
        )
        notification = NotificationModel.DB(base).create(
            requester_id=root_id,
            model_registry=registry,
            return_type="dto",
            override_dto=NotificationModel,
            title="hello",
            content="body",
        )
        DeliveryDB = UserNotificationModel.DB(base)
        delivery = DeliveryDB.create(
            requester_id=root_id,
            model_registry=registry,
            return_type="dto",
            override_dto=UserNotificationModel,
            user_id=target_id,
            notification_id=notification.id,
        )
        QuestionDB = UserRecoveryQuestionModel.DB(base)
        QuestionDB.create(
            requester_id=root_id,
            model_registry=registry,
            user_id=target_id,
            question="first pet?",
            answer="hashed-answer",
        )

        result = UserMergeManager(
            requester_id=root_id, model_registry=registry
        ).merge_users(initiating_user_id=initiating_id, target_user_id=target_id)

        assert result["handler_errors"] == ""
        revoked = KeyDB.get(
            requester_id=root_id,
            model_registry=registry,
            id=key.id,
            return_type="dto",
            override_dto=APIKeyModel,
        )
        assert revoked.is_revoked is True
        moved = DeliveryDB.get(
            requester_id=root_id,
            model_registry=registry,
            id=delivery.id,
            return_type="dto",
            override_dto=UserNotificationModel,
        )
        assert moved.user_id == initiating_id
        target_questions = QuestionDB.list(
            requester_id=root_id,
            model_registry=registry,
            filters=[QuestionDB.user_id == target_id],
        )
        assert len(target_questions) == 1
        assert target_questions[0]["deleted_at"] is not None


class TestSdkGenerationIsPerApp:
    def test_app_without_meta_sdk_py_generates_nothing_after_one_that_did(
        self, build_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from zephyrex.extensions.meta_sdk_py.PythonSDKEmitter import (
            SDK_PY_OUTPUT_DIR_ENV,
        )

        with_sdk = tmp_path / "with_sdk"
        without_sdk = tmp_path / "without_sdk"
        with_sdk.mkdir()
        without_sdk.mkdir()

        monkeypatch.setenv(SDK_PY_OUTPUT_DIR_ENV, str(with_sdk))
        build_app("meta_sdk_py")
        assert list(with_sdk.glob("*SDK_generated.py"))

        monkeypatch.setenv(SDK_PY_OUTPUT_DIR_ENV, str(without_sdk))
        build_app("")
        assert list(without_sdk.iterdir()) == []


class TestRegistryStopOrder:
    def test_stop_runs_in_reverse_order_and_survives_a_raising_extension(self):
        from zephyrex.extensions.AbstractExtensionProvider import (
            AbstractStaticExtension,
            ExtensionRegistry,
        )

        stopped: list[str] = []

        class EXT_First(AbstractStaticExtension):
            name = "lifecycle_first"

            @classmethod
            def on_stop(cls) -> None:
                stopped.append(cls.name)

        class EXT_Second(AbstractStaticExtension):
            name = "lifecycle_second"

            @classmethod
            def on_stop(cls) -> None:
                stopped.append(cls.name)
                raise RuntimeError("second failed to stop")

        registry = ExtensionRegistry("")
        registry.register_extension(EXT_First)
        registry.register_extension(EXT_Second)

        registry.stop_extensions()

        assert stopped == ["lifecycle_second", "lifecycle_first"]
