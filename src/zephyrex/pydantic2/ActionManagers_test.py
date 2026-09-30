# SPDX-License-Identifier: AGPL-3.0-or-later
"""Managers with no model: found beside model-backed ones, mounted for their
custom routes only, and refused if they declare CRUD."""

import sys
import types
from typing import ClassVar, List, Optional

import pytest

from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.fastapi.router import require_custom_routes_only
from zephyrex.pydantic2.registry import ModelRegistry

_MODULE = "zephyrex_probe.BLL_ActionProbe"


class ActionOnlyManager(AbstractBLLManager, RouterMixin):
    prefix: ClassVar[Optional[str]] = "/v1/action-probe"
    routes_to_register: ClassVar[Optional[List]] = []


class CrudWithoutModelManager(AbstractBLLManager, RouterMixin):
    prefix: ClassVar[Optional[str]] = "/v1/crud-probe"


@pytest.fixture
def probe_module(monkeypatch: pytest.MonkeyPatch) -> str:
    """A BLL module defining two managers and importing a third."""
    module = types.ModuleType(_MODULE)
    for manager in (ActionOnlyManager, CrudWithoutModelManager):
        monkeypatch.setattr(manager, "__module__", _MODULE)
        setattr(module, manager.__name__, manager)
    # Defined elsewhere, so never counted as this module's.
    imported = type("ImportedManager", (AbstractBLLManager, RouterMixin), {})
    setattr(module, "ImportedManager", imported)
    monkeypatch.setitem(sys.modules, _MODULE, module)
    return _MODULE


def test_discovery_finds_the_managers_a_module_defines(probe_module):
    found = ModelRegistry._bll_router_managers([probe_module, "not.a.bll.module"])
    assert found == [ActionOnlyManager, CrudWithoutModelManager]


def test_an_action_manager_is_accepted():
    require_custom_routes_only(ActionOnlyManager)


def test_a_model_less_manager_declaring_crud_fails_the_build():
    with pytest.raises(TypeError, match="set routes_to_register = \\[\\]"):
        require_custom_routes_only(CrudWithoutModelManager)
