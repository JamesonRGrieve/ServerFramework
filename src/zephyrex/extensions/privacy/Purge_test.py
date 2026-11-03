# SPDX-License-Identifier: AGPL-3.0-or-later
"""What was deleted long enough ago is purged for good: every table's rows
deleted before the cutoff go, live rows and recent deletions stay, and a
deleted row that something still points at stays with it (so a foreign key's
ON DELETE CASCADE can never take a live row along)."""

from datetime import datetime, timedelta
from typing import Any, Iterator

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    insert,
    select,
)
from sqlalchemy.orm import Session

from zephyrex.extensions.privacy import Purge
from zephyrex.extensions.privacy.EXT_Privacy import EXT_Privacy

NOW = datetime(2026, 11, 2, 12, 0, 0)
LONG_AGO = NOW - timedelta(days=31)
LATELY = NOW - timedelta(days=29)
CUTOFF = NOW - timedelta(days=30)

metadata = MetaData()
users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("created_by_user_id", String, ForeignKey("users.id")),
    Column("deleted_at", DateTime),
)
folders = Table(
    "folders",
    metadata,
    Column("id", String, primary_key=True),
    Column("created_by_user_id", String, ForeignKey("users.id")),
    Column("deleted_at", DateTime),
)
scripts = Table(
    "scripts",
    metadata,
    Column("id", String, primary_key=True),
    Column("created_by_user_id", String, ForeignKey("users.id")),
    Column("folder_id", String, ForeignKey("folders.id", ondelete="CASCADE")),
    Column("deleted_at", DateTime),
)
# No deleted_at: never deleted softly, so never purged.
events = Table(
    "events",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String),
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection: Any, _record: Any) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    with Session(engine) as opened:
        yield opened
    engine.dispose()


def _rows(session: Session, table: Table) -> set:
    return set(session.execute(select(table.c.id)).scalars())


def _add(session: Session, table: Table, **values: Any) -> None:
    session.execute(insert(table).values(**values))
    session.commit()


def test_rows_deleted_before_the_cutoff_are_purged(session: Session) -> None:
    _add(session, users, id="owner")
    _add(session, scripts, id="gone", created_by_user_id="owner", deleted_at=LONG_AGO)
    _add(session, scripts, id="recent", created_by_user_id="owner", deleted_at=LATELY)
    _add(session, scripts, id="live", created_by_user_id="owner")

    purged = Purge.purge_deleted(metadata, session, CUTOFF)

    assert _rows(session, scripts) == {"recent", "live"}
    assert purged == {"scripts": 1}


def test_a_deleted_row_a_live_row_points_at_stays(session: Session) -> None:
    """The folder's foreign key cascades: purging it would take the live script."""
    _add(session, users, id="owner")
    _add(session, folders, id="old", created_by_user_id="owner", deleted_at=LONG_AGO)
    _add(session, scripts, id="filed", created_by_user_id="owner", folder_id="old")

    assert Purge.purge_deleted(metadata, session, CUTOFF) == {}
    assert _rows(session, folders) == {"old"}
    assert _rows(session, scripts) == {"filed"}


def test_a_deleted_account_goes_after_everything_deleted_with_it(
    session: Session,
) -> None:
    """Referrers first, however the tables are ordered, and an account that
    names itself as its own creator is not held back by that."""
    _add(session, users, id="gone", created_by_user_id="gone", deleted_at=LONG_AGO)
    _add(session, folders, id="f", created_by_user_id="gone", deleted_at=LONG_AGO)
    _add(
        session,
        scripts,
        id="s",
        created_by_user_id="gone",
        folder_id="f",
        deleted_at=LONG_AGO,
    )
    _add(session, users, id="stays")

    purged = Purge.purge_deleted(metadata, session, CUTOFF)

    assert purged == {"scripts": 1, "folders": 1, "users": 1}
    assert _rows(session, users) == {"stays"}
    assert _rows(session, folders) == set()


def test_an_account_a_live_record_still_names_stays(session: Session) -> None:
    _add(session, users, id="gone", deleted_at=LONG_AGO)
    _add(session, scripts, id="kept", created_by_user_id="gone")

    assert Purge.purge_deleted(metadata, session, CUTOFF) == {}
    assert _rows(session, users) == {"gone"}


def test_a_key_only_the_database_holds_keeps_that_row_alone() -> None:
    """A foreign key the schema has but the models do not declare (a migration's
    own) keeps the account it names, and no other: the rest of the table is
    purged around it."""
    schema = MetaData()
    Table(
        "users",
        schema,
        Column("id", String, primary_key=True),
        Column("deleted_at", DateTime),
    )
    Table(
        "payments",
        schema,
        Column("id", String, primary_key=True),
        Column("user_id", String, ForeignKey("users.id")),
    )
    models = MetaData()
    accounts = Table(
        "users",
        models,
        Column("id", String, primary_key=True),
        Column("deleted_at", DateTime),
    )
    Table(
        "payments",
        models,
        Column("id", String, primary_key=True),
        Column("user_id", String),
    )
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection: Any, _record: Any) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    schema.create_all(engine)
    with Session(engine) as session:
        _add(session, accounts, id="paid", deleted_at=LONG_AGO)
        _add(session, accounts, id="free", deleted_at=LONG_AGO)
        _add(session, models.tables["payments"], id="p", user_id="paid")

        assert Purge.purge_deleted(models, session, CUTOFF) == {"users": 1}
        assert _rows(session, accounts) == {"paid"}
    engine.dispose()


def test_a_table_without_soft_deletes_is_left_alone(session: Session) -> None:
    _add(session, events, id="e", name="signed in")

    Purge.purge_deleted(metadata, session, CUTOFF)

    assert _rows(session, events) == {"e"}


class TestTheSetting:
    """``PRIVACY_PURGE_DELETED_AFTER_DAYS`` turns the daily purge on; unset, a
    deletion stays soft for ever, as without the extension."""

    def test_unset_runs_no_purge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(Purge.PURGE_SETTING, raising=False)
        assert (
            EXT_Privacy.register_services(model_registry=None, requester_id="root")
            == []
        )

    def test_set_runs_a_daily_purge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(Purge.PURGE_SETTING, "30")
        services = EXT_Privacy.register_services(
            model_registry=object(), requester_id="root"
        )
        assert len(services) == 1
        assert services[0].retention == timedelta(days=30)
        assert services[0].interval_seconds == 24 * 60 * 60

    @pytest.mark.parametrize("value", ["0", "-1", "thirty"])
    def test_a_setting_that_is_not_a_positive_whole_number_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(Purge.PURGE_SETTING, value)
        with pytest.raises(ValueError, match=Purge.PURGE_SETTING):
            EXT_Privacy.register_services(model_registry=object(), requester_id="root")
