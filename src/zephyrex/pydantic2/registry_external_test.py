# SPDX-License-Identifier: AGPL-3.0-or-later
"""A table-less model (an upstream's records) is bound explicitly with
``bind_external``: routed like any model, resolved by ``apply``, and kept
out of ``bound_models``, which every database layer reads, so no table is
ever made for it. The app-level proof (a lifted model on REST and GraphQL,
no table) is in ``federation/BLL_Federation_Typed_test.py``."""

from typing import ClassVar, List, Optional

import pytest
from pydantic import BaseModel

from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.pydantic2.fastapi import RouterMixin, RouteType
from zephyrex.pydantic2.registry import ModelRegistry, is_external_model


class UpstreamThingModel(BaseModel):
    is_external_model: ClassVar[bool] = True
    id: str
    label: Optional[str] = None


class UpstreamThingManager(AbstractBLLManager, RouterMixin):
    _model = UpstreamThingModel
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []


class LocalThingModel(BaseModel):
    id: str


class PlainThingManager(AbstractBLLManager):
    _model = UpstreamThingModel


def test_a_table_less_model_is_bound_apart_from_the_tabled_ones():
    registry = ModelRegistry()
    registry.bind_external(UpstreamThingModel, UpstreamThingManager)

    assert UpstreamThingModel not in registry.bound_models
    assert registry.external_managers() == [UpstreamThingManager]
    assert registry.apply(UpstreamThingModel) is UpstreamThingModel
    assert is_external_model(UpstreamThingModel)


def test_binding_the_same_model_twice_is_one_binding():
    registry = ModelRegistry()
    registry.bind_external(UpstreamThingModel, UpstreamThingManager)
    registry.bind_external(UpstreamThingModel, UpstreamThingManager)

    assert registry.external_managers() == [UpstreamThingManager]


def test_a_table_less_model_is_never_bound_as_a_table():
    with pytest.raises(ValueError, match="bind_external"):
        ModelRegistry().bind(UpstreamThingModel)


def test_a_tabled_model_is_never_bound_as_table_less():
    class LocalThingManager(AbstractBLLManager, RouterMixin):
        _model = LocalThingModel

    with pytest.raises(ValueError, match="is_external_model"):
        ModelRegistry().bind_external(LocalThingModel, LocalThingManager)


def test_its_manager_routes_it_and_serves_it():
    with pytest.raises(ValueError, match="RouterMixin"):
        ModelRegistry().bind_external(UpstreamThingModel, PlainThingManager)

    class OtherManager(AbstractBLLManager, RouterMixin):
        _model = LocalThingModel

    with pytest.raises(ValueError, match="does not serve"):
        ModelRegistry().bind_external(UpstreamThingModel, OtherManager)


def test_a_name_is_one_models_whichever_is_bound_first():
    clashing_external = type(
        "LocalThingModel",
        (BaseModel,),
        {
            "__annotations__": {"id": str, "is_external_model": ClassVar[bool]},
            "is_external_model": True,
        },
    )
    clashing_manager = type(
        "ClashingManager",
        (AbstractBLLManager, RouterMixin),
        {"_model": clashing_external, "routes_to_register": []},
    )

    local_first = ModelRegistry()
    local_first.bind(LocalThingModel)
    with pytest.raises(RuntimeError, match="Duplicate model name"):
        local_first.bind_external(clashing_external, clashing_manager)

    external_first = ModelRegistry()
    external_first.bind_external(clashing_external, clashing_manager)
    with pytest.raises(RuntimeError, match="Duplicate model"):
        external_first.bind(LocalThingModel)


def test_nothing_is_bound_once_the_registry_is_committed():
    registry = ModelRegistry()
    registry._locked = True  # what commit() leaves
    with pytest.raises(RuntimeError, match="committed"):
        registry.bind_external(UpstreamThingModel, UpstreamThingManager)


def test_clearing_the_registry_forgets_table_less_models():
    registry = ModelRegistry()
    registry.bind_external(UpstreamThingModel, UpstreamThingManager)
    registry.clear()

    assert registry.external_managers() == []
    with pytest.raises(TypeError):
        registry.apply(UpstreamThingModel)
