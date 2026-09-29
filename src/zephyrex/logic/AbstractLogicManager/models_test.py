# SPDX-License-Identifier: AGPL-3.0-or-later
"""Serialization of ModelMeta models follows pydantic's own options.

ModelMeta used to install a serializer that re-added every model field
missing from the result, so ``exclude``, ``include``, ``exclude_unset``
and write-only fields (``Field(exclude=True)``) had no effect.
"""

from datetime import datetime, timezone
from typing import Optional

from pydantic import Field

from zephyrex.logic.AbstractLogicManager.models import ApplicationModel, ModelMeta


class _Record(ApplicationModel, metaclass=ModelMeta):
    name: str
    note: Optional[str] = None
    secret: Optional[str] = Field(None, exclude=True)


def _record() -> _Record:
    return _Record(
        id="r1",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_by_user_id="u1",
        name="n",
        secret="s",
    )


def test_write_only_field_is_not_serialized():
    record = _record()

    assert record.secret == "s"
    assert "secret" not in record.model_dump()
    assert "secret" not in record.model_dump(mode="json")


def test_exclude_unset_drops_unset_fields():
    dumped = _record().model_dump(exclude_unset=True)

    assert "note" not in dumped
    assert dumped["name"] == "n"


def test_include_and_exclude_are_honoured():
    record = _record()

    assert set(record.model_dump(include={"id", "name"})) == {"id", "name"}
    assert "name" not in record.model_dump(exclude={"name"})
