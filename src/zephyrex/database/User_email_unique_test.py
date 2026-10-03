# SPDX-License-Identifier: AGPL-3.0-or-later
"""One live account per email, held by the database.

The hole this closes: only application code kept emails unique, so a writer
that skipped the check (or two that raced it) made two live accounts with
one email; DB_Payment_test showed the database accepting them. The users
table now carries a partial unique index on the (normalized) email of live
rows, ``uq_users_email_live``, made by migration ``users_email_unique_live``,
which refuses to run over existing duplicates and never deletes a row."""

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator, List, Optional

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserManager, UserModel
from zephyrex.logic.BLL_Auth.user import USERS_EMAIL_UNIQUE_INDEX
from zephyrex.testing.factories import create_user

MIGRATION = (
    Path(__file__).parent / "migrations" / "versions" / "users_email_unique_live.py"
)


def _email() -> str:
    return f"unique_{uuid.uuid4().hex[:8]}@example.com"


def _make(model_registry: Any, email: str) -> Any:
    return UserModel.DB(model_registry.DB.manager.Base).create(
        requester_id=env("SYSTEM_ID"),
        model_registry=model_registry,
        return_type="dto",
        override_dto=UserModel,
        email=email,
    )


def _soft_delete(model_registry: Any, user_id: str) -> None:
    UserModel.DB(model_registry.DB.manager.Base).delete(
        requester_id=env("ROOT_ID"), model_registry=model_registry, id=user_id
    )


class TestTheDatabaseHoldsEmailsUnique:
    def test_the_index_is_on_the_users_table(self, server, model_registry):
        inspector = sa.inspect(model_registry.DB.manager.get_setup_engine())
        unique = {
            index["name"]: index
            for index in inspector.get_indexes("users")
            if index.get("unique")
        }
        assert unique[USERS_EMAIL_UNIQUE_INDEX]["column_names"] == ["email"]

    def test_a_second_live_user_with_the_email_is_refused(self, server, model_registry):
        email = _email()
        _make(model_registry, email)
        with pytest.raises(IntegrityError):
            _make(model_registry, email)

    def test_the_email_is_free_once_its_user_is_soft_deleted(
        self, server, model_registry
    ):
        email = _email()
        first = _make(model_registry, email)
        _soft_delete(model_registry, first.id)
        second = _make(model_registry, email)
        assert second.id != first.id
        with pytest.raises(IntegrityError):
            _make(model_registry, email)


class TestAnEmailChangeIsStoredNormalized:
    def test_a_profile_email_is_stored_as_login_matches_it(
        self, server, model_registry
    ):
        """The profile route stored the email as sent, so ``Bob@X`` could
        never sign in (login matches ``bob@x``) and slipped past the index."""
        user = create_user(server)
        spelled = f"  Changed_{uuid.uuid4().hex[:8]}@Example.COM "
        users = UserManager(requester_id=user.id, model_registry=model_registry)
        users.update(id=user.id, email=spelled)
        assert users.get(id=user.id).email == UserManager._normalize_identifier(spelled)

    def test_an_email_another_live_user_holds_is_409(self, server, model_registry):
        holder, user = create_user(server), create_user(server)
        users = UserManager(requester_id=user.id, model_registry=model_registry)
        with pytest.raises(HTTPException) as taken:
            users.update(id=user.id, email=holder.email.upper())
        assert taken.value.status_code == 409
        assert users.get(id=user.id).email == user.email


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("users_email_unique_live", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def legacy_users(tmp_path: Path) -> Iterator[sa.Engine]:
    """A real SQLite users table as it stood before the revision."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE users (id VARCHAR PRIMARY KEY, email VARCHAR,"
                " deleted_at DATETIME)"
            )
        )
    yield engine
    engine.dispose()


def _insert(engine: sa.Engine, *rows: tuple) -> None:
    with engine.begin() as conn:
        for user_id, email, deleted_at in rows:
            conn.execute(
                sa.text("INSERT INTO users VALUES (:id, :email, :deleted_at)"),
                {"id": user_id, "email": email, "deleted_at": deleted_at},
            )


def _upgrade(engine: sa.Engine) -> None:
    module = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _emails(engine: sa.Engine) -> List[tuple]:
    with engine.connect() as conn:
        return [
            tuple(row)
            for row in conn.execute(sa.text("SELECT id, email FROM users ORDER BY id"))
        ]


def _index(engine: sa.Engine) -> Optional[dict]:
    for index in sa.inspect(engine).get_indexes("users"):
        if index["name"] == "uq_users_email_live":
            return dict(index)
    return None


class TestTheMigration:
    def test_live_duplicates_stop_it_and_change_nothing(self, legacy_users):
        _insert(
            legacy_users,
            ("u1", "Dup@Example.com", None),
            ("u2", "dup@example.com ", None),
            ("u3", "other@example.com", None),
        )
        before = _emails(legacy_users)

        with pytest.raises(RuntimeError) as refused:
            _upgrade(legacy_users)

        message = str(refused.value)
        assert "uq_users_email_live" in message
        assert "u1, u2" in message and "u3" not in message
        assert "dup@example.com" not in message.lower()
        assert _emails(legacy_users) == before
        assert _index(legacy_users) is None

    def test_a_soft_deleted_duplicate_does_not_stop_it(self, legacy_users):
        _insert(
            legacy_users,
            ("u1", "Kept@Example.com", None),
            ("u2", "kept@example.com", datetime.now(timezone.utc).isoformat()),
        )
        _upgrade(legacy_users)

        assert _emails(legacy_users) == [
            ("u1", "kept@example.com"),
            ("u2", "kept@example.com"),
        ]
        index = _index(legacy_users)
        assert index is not None and index["unique"]
        _insert(legacy_users, ("u3", "fresh@example.com", None))
        with pytest.raises(IntegrityError):
            _insert(legacy_users, ("u4", "kept@example.com", None))
        _insert(legacy_users, ("u5", "kept@example.com", "2026-01-01"))
