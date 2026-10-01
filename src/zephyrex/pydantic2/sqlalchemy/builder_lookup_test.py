# SPDX-License-Identifier: AGPL-3.0-or-later
"""``get_entity_module_class`` finds a model among the loaded modules
without touching anything a module does not define.

A lazy module (transformers is one) answers unknown attributes from a
module-level ``__getattr__`` that imports a submodule; probing it with
``hasattr`` imported one needing torchvision, and every app startup in that
process failed once anything had imported transformers.
"""

import sys
import types

from pydantic import BaseModel

from zephyrex.pydantic2.sqlalchemy.builder import get_entity_module_class


class _Probed(Exception):
    pass


def _lazy_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)

    def __getattr__(attribute: str):  # noqa: N807 - the module protocol's name
        raise _Probed(f"{name}.{attribute} was probed")

    module.__getattr__ = __getattr__  # type: ignore[method-assign]
    return module


def test_a_lazy_modules_getattr_is_never_run(monkeypatch):
    monkeypatch.setitem(sys.modules, "zx_lazy_probe", _lazy_module("zx_lazy_probe"))
    assert get_entity_module_class("ZxNoSuchEntity") == (None, None)


def test_a_model_is_found_by_name_and_by_its_model_suffix(monkeypatch):
    module = types.ModuleType("zx_models_probe")

    class ZxWidgetModel(BaseModel):
        pass

    module.ZxWidgetModel = ZxWidgetModel
    monkeypatch.setitem(sys.modules, "zx_models_probe", module)
    monkeypatch.setitem(sys.modules, "zx_lazy_probe", _lazy_module("zx_lazy_probe"))

    assert get_entity_module_class("ZxWidget") == ("zx_models_probe", ZxWidgetModel)
    assert get_entity_module_class("ZxWidgetModel") == (
        "zx_models_probe",
        ZxWidgetModel,
    )
