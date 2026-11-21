# SPDX-License-Identifier: AGPL-3.0-or-later
"""A model's reference to another model becomes a foreign key, required or optional alike."""

from sqlalchemy.orm import declarative_base

from zephyrex.logic.AbstractLogicManager import ApplicationModel, ModelMeta
from zephyrex.pydantic2.registry import ModelRegistry
from zephyrex.pydantic2.sqlalchemy import create_sqlalchemy_model


class ShelfFkProbeModel(ApplicationModel, metaclass=ModelMeta):
    name: str


class BookFkProbeModel(
    ApplicationModel, ShelfFkProbeModel.Reference, metaclass=ModelMeta
):
    title: str


class NoteFkProbeModel(
    ApplicationModel, ShelfFkProbeModel.Reference.Optional, metaclass=ModelMeta
):
    text: str


def _column(model: type, name: str):
    built = create_sqlalchemy_model(
        model, ModelRegistry(), base_model=declarative_base()
    )
    return built.__table__.columns[name]


def test_a_required_reference_is_a_foreign_key_that_may_not_be_null() -> None:
    column = _column(BookFkProbeModel, "shelf_fk_probe_id")
    assert {fk.target_fullname for fk in column.foreign_keys} == {"shelf_fk_probes.id"}
    assert column.nullable is False


def test_an_optional_reference_is_a_foreign_key_that_may_be_null() -> None:
    column = _column(NoteFkProbeModel, "shelf_fk_probe_id")
    assert {fk.target_fullname for fk in column.foreign_keys} == {"shelf_fk_probes.id"}
    assert column.nullable is True
